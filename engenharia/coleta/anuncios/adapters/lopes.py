"""
Lopes adapter -- a API `portal-product/v2/products-improved`.

Inventario de 2026-09-06 (MEDICOES). Tres coisas decidiram o desenho:

**1. A lista NAO basta.** A busca devolve 56 campos por imovel, o detalhe
devolve 66 -- mas a contagem engana. O que falta na busca sao exatamente os
campos que explicam preco: `suites`, `floor_level`, `area_total_m2`,
`iptu_tax` e `amenities` ficariam em 100% de nulo. E o mesmo caso do
`universal` (37 campos contra 214), e por isso o detalhe e obrigatorio.

**2. O detalhe responde a chamada direta.** Medido: HTTP 200, 13 KB de JSON,
sem cookie de sessao. Custo de 1 requisicao por anuncio, nao 2 -- ao
contrario do `universal`, cujo detalhe so responde depois da pagina visitada.

**3. A descoberta sai do sitemap, nao da busca.** A busca declara 34.560
anuncios de venda em SP mas pagina ate 20 paginas de 23 -- teto de 460 por
faceta. O sitemap publica 171.952 URLs de anuncio sem teto, e o slug carrega
tipo, transacao, cidade e bairro, entao da para filtrar antes de gastar
requisicao.

O preco vem do HTML servido tambem, dentro de `<script id="ng-state">` (o
TransferState do Angular) -- mas a API entrega o mesmo objeto sem os 600 KB
de pagina em volta.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from comum import schema

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

API = "https://apis.lopes.com.br/portal-product/v2/products-improved/{sku}"
SITEMAPS = ("https://www.lopes.com.br/sitemaps/sitemap-imoveis.xml",
            "https://www.lopes.com.br/sitemaps/sitemap-imoveis-2.xml",
            "https://www.lopes.com.br/sitemaps/sitemap-imoveis-3.xml",
            "https://www.lopes.com.br/sitemaps/sitemap-imoveis-4.xml")

#: /imovel/REO1128620/venda-apartamento-1-quarto-sao-paulo-vila-nova-conceicao
#:
#: O numero de digitos NAO e fixado. Os SKUs medidos tem 5 a 7, mas prender a
#: regex ao que a amostra mostrou e como um adapter deste projeto deixou de
#: reconhecer anuncio: a fonte muda a numeracao e a coleta cai para zero sem
#: erro nenhum. O que identifica o anuncio e o caminho `/imovel/`, nao o
#: comprimento do codigo.
URL_ANUNCIO = re.compile(r"/imovel/([A-Z]{2,4}\d{1,12})/([a-z0-9-]*)", re.I)

#: `attributes[].type` -> campo do schema. A API nomeia por tipo, nao por
#: posicao, entao a ordem da lista nao importa.
ATRIBUTOS = {
    "area_attr": "area_m2",
    "total_area_attr": "area_total_m2",
    "bedroom_attr": "bedrooms",
    "bathroom_attr": "bathrooms",
    "suite_attr": "suites",
    "parking_lots_attr": "parking_spots",
    "floor_attr": "floor_level",
}

#: O que e mapeado. Caminho de ponto dentro de `product`.
API_FIELDS = frozenset({
    "sku", "dealTypes", "divisionType", "description",
    "prices.sale", "prices.rent", "prices.condominium", "prices.property",
    "address.city", "address.state", "address.stateInitials",
    "address.neighborhood", "address.street",
    "attributes", "condominium.amenities", "features",
})

#: Existe para a exclusao ser greppavel e testavel, nao implicita na ausencia.
#:
#: `agents` traz nome, apelido, CRECI e foto do corretor no MESMO objeto do
#: imovel -- terceira plataforma deste projeto a fazer isso. Nao da para nao
#: receber; da para nao guardar por mais tempo que o transporte exige, e e o
#: que `_projeta` faz: descarta na ingestao, antes de qualquer cache.
#:
#: `map.lat/lng` sai por decisao, nao por ausencia: o schema nao tem campo de
#: coordenada (D-032 descartou geo em Santos). Se a decisao mudar, este e o
#: unico lugar a tocar -- a fonte entrega em 100% dos anuncios medidos.
NEVER_MAP = frozenset({
    "agents", "advertiser", "listingOwner", "formLead", "company",
    "map.lat", "map.lng",
    "media", "photo", "pois", "widgets", "similarProducts", "plans",
    "analyticsCategory", "showFinancingInfo", "improvedDescription",
    "prices.firstInstallment", "prices.fullMonthlyPrice",
    "prices.squareMeters", "prices.priceFrom", "prices.priceFromLegalText",
})

#: `dealTypes` da API -> vocabulario do projeto.
DEAL = {"sale": "venda", "rent": "locacao"}


def _dinheiro(valor):
    """
    Valor da API -> float, ou None.

    A API emite numero nativo (`5800000.0`), entao o parser pt-BR nao pode
    ve-lo: ele leria o ponto como separador de milhar. E zero significa NAO
    INFORMADO, nunca "de graca" -- um imovel com R$ 0,00 de IPTU nao existe,
    o campo esta vazio. Persistir 0 fabricaria observacao (SDD D-04).
    """
    if valor in (None, "", 0, 0.0):
        return None
    try:
        return float(valor) or None
    except (TypeError, ValueError):
        return None


def _numero(valor):
    """
    '97m²' -> 97.0 · '19º' -> 19.0 · '2' -> 2.0 · None se nao houver digito.

    Zero e resposta valida aqui, ao contrario de dinheiro: um apartamento sem
    vaga tem zero vagas. So a ausencia vira None.
    """
    if valor in (None, ""):
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(valor))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _inteiro(valor):
    n = _numero(valor)
    return None if n is None else int(n)


def _projeta(produto: dict) -> dict:
    """
    O registro pequeno, montado no instante em que o JSON e decodificado.

    Nada a jusante deve jamais ter visto `agents` nem `formLead`. Este e o
    ponto de descarte -- antes de qualquer cache, antes do Payload.
    """
    pr = produto.get("prices") or {}
    end = produto.get("address") or {}
    attrs = {a.get("type"): a.get("value") for a in (produto.get("attributes") or [])}
    amen = [a.get("name") for a in
            ((produto.get("condominium") or {}).get("amenities") or []) if a.get("name")]
    amen += [f.get("name") for f in (produto.get("features") or []) if f.get("name")]
    return {
        "sku": produto.get("sku"),
        "deals": [d for d in (produto.get("dealTypes") or []) if d in DEAL],
        "tipo": produto.get("divisionType"),
        "descricao": produto.get("description"),
        "venda": _dinheiro(pr.get("sale")),
        "aluguel": _dinheiro(pr.get("rent")),
        "condominio": _dinheiro(pr.get("condominium")),
        "iptu": _dinheiro(pr.get("property")),
        "cidade": end.get("city"),
        "uf": end.get("stateInitials"),
        "bairro": end.get("neighborhood"),
        "rua": end.get("street"),
        "atributos": attrs,
        "amenidades": amen,
    }


@register
class LopesAdapter(ListingSource):
    platform = "lopes"
    supports_feed = False
    jsonld_required = False        # o JSON-LD existe mas nao tem preco

    def __init__(self, session, source, target=None):
        super().__init__(session, source, target)
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # descoberta                                                          #
    # ------------------------------------------------------------------ #
    def _skus_do_sitemap(self, transacao: str) -> Iterator[tuple[str, dict]]:
        """
        Percorre os quatro sitemaps e devolve (sku, dicas) do que interessa.

        Filtra por CIDADE pelo slug, nunca por transacao.

        O slug abre com `venda-` ou `locacao-`, e usar isso para pular parece
        economizar uma chamada de API por anuncio. Mas o imovel anunciado para
        venda E locacao tem UM slug so -- o que a fonte escolheu -- e filtrar
        por ele apaga a metade que nao esta ali. Anuncio duplo nao e caso de
        borda: sao 244 imoveis da base atual, e sao exatamente eles que
        permitem medir a razao aluguel/valor no mesmo ativo.

        O token de URL parece a via barata e frequentemente nao e -- numa
        plataforma deste projeto, 61 de 1.455 URLs nao tinham token e 91
        tinham os dois. Cidade e seguro porque nao muda com a transacao.

        A transacao sai de `dealTypes` da API, depois da chamada.
        """
        cidade_alvo = schema.slugify(self.cidade) if self.cidade else None
        vistos: set[str] = set()

        for url_sitemap in SITEMAPS:
            resp = self.session.get(url_sitemap)
            if resp is None:
                continue
            for url in re.findall(r"<loc>([^<]+)</loc>", resp.text):
                m = URL_ANUNCIO.search(url)
                if not m:
                    continue
                sku, slug = m.group(1), (m.group(2) or "").lower()
                if sku in vistos:
                    continue
                if cidade_alvo and cidade_alvo not in slug:
                    continue
                vistos.add(sku)
                yield sku, {"link": url}

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        """
        Emite os anuncios desta transacao, com a transacao ja confirmada.

        A confirmacao vem de `dealTypes` da API, nao do slug: o slug e filtro
        barato, o campo e a verdade. Um anuncio marcado para venda E locacao
        aparece nas duas passagens -- e assim que o anuncio duplo vira duas
        linhas sem o coletor precisar saber disso.
        """
        vazio = True
        cidade_alvo = schema.slugify(self.cidade) if self.cidade else None
        for sku, dicas in self._skus_do_sitemap(transaction):
            produto = self._pega_api(sku)
            if produto is None:
                continue
            if transaction not in {DEAL[d] for d in produto["deals"]}:
                continue
            # A CIDADE da API vence o slug, pela mesma razao que `dealTypes`
            # vence: o slug e filtro barato, o campo e a verdade.
            #
            # Medido na primeira corrida real: um terreno em JUNDIAI entrou na
            # particao de Santos porque o bairro se chama "Cidade Santos
            # Dumont" e o filtro era `"santos" in slug`. Uma linha em 250 --
            # e nenhum erro. E a mesma armadilha do P0, em que um dominio da
            # lista de Santos apontava para uma imobiliaria do Rio.
            if self.cidade and schema.slugify(produto["cidade"] or "") != cidade_alvo:
                continue
            vazio = False
            yield DiscoveredURL(
                url=dicas["link"],
                transaction_type=transaction,
                discovery_path=f"sitemap+api:{sku}",
                # Escopado por transacao de proposito: a linha de venda e a de
                # locacao sao duas observacoes de um imovel e nao podem
                # compartilhar property_id.
                listing_id=f"{sku}-{transaction}",
                hints={
                    "property_type": produto["tipo"],
                    "city": produto["cidade"],
                    "state": produto["uf"],
                    "neighborhood": produto["bairro"],
                },
            )
        if vazio:
            raise AdapterError(
                f"{domain}: nenhum anuncio de {transaction!r}. "
                "Fonte vazia e falha, nunca sucesso (§10)."
            )

    # ------------------------------------------------------------------ #
    # busca                                                               #
    # ------------------------------------------------------------------ #
    def _pega_api(self, sku: str) -> dict | None:
        """Uma requisicao por anuncio. O registro ja sai projetado."""
        if sku in self._cache:
            return self._cache[sku]
        resp = self.session.get(API.format(sku=sku))
        if resp is None:
            return None
        try:
            corpo = json.loads(resp.text)
        except (ValueError, TypeError) as exc:
            raise AdapterError(f"{sku}: resposta da API nao e JSON") from exc
        produto = corpo.get("product")
        if not isinstance(produto, dict):
            return None
        pequeno = _projeta(produto)
        self._cache[sku] = pequeno
        return pequeno

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        """
        Serve do cache preenchido no discover. Sem segunda requisicao.

        O que viaja no Payload ja passou por `_projeta`, entao nunca conteve
        dado pessoal.
        """
        sku = (discovered.listing_id or "").rsplit("-", 1)[0]
        produto = self._cache.get(sku) or self._pega_api(sku)
        if produto is None:
            return None
        return Payload(discovered=discovered,
                       text=json.dumps(produto, ensure_ascii=False),
                       content_type="application/json")

    # ------------------------------------------------------------------ #
    # extracao                                                            #
    # ------------------------------------------------------------------ #
    def parse(self, payload: Payload) -> dict:
        """
        Nomes da API -> nomes do schema, escolhendo o preco desta linha.

        Mapeamento explicito, nunca passthrough (§1 do novo-adapter). Um
        passthrough nao vazaria -- o allowlist do schema nao guarda nome de
        campo da fonte -- mas produziria linhas vazias com o diagnostico tres
        camadas longe da causa.
        """
        p = json.loads(payload.text)
        transacao = payload.discovered.transaction_type
        preco = p["venda"] if transacao == "venda" else p["aluguel"]

        attrs = p.get("atributos") or {}
        out = {
            "listing_id": payload.discovered.listing_id,
            "transaction_type": transacao,
            "price": preco,
            "property_type": p.get("tipo"),
            "city": p.get("cidade"),
            "state": p.get("uf"),
            "neighborhood": p.get("bairro"),
            "address": p.get("rua"),
            "description_clean": p.get("descricao"),
            "condo_fee": p.get("condominio"),
            "iptu_tax": p.get("iptu"),
            "amenities": p.get("amenidades") or None,
        }
        for tipo, campo in ATRIBUTOS.items():
            bruto = attrs.get(tipo)
            if campo in ("area_m2", "area_total_m2"):
                out[campo] = _numero(bruto)
            else:
                out[campo] = _inteiro(bruto)
        return out
