"""
VivaReal — marketplace nacional. Inventário em MEDICOES §17, decisão em D-026.

DUAS SUPERFÍCIES, AMBAS NECESSÁRIAS — e é o inverso do caso `universal`.

    busca (JSON-LD)     18 campos    área, andar, quartos, banheiros, rua
    anúncio (JSON-LD)   17 campos    preço, sku, descrição
    anúncio (DOM)       --           condomínio, IPTU

No `universal` a busca tinha 37 campos e o detalhe 214. Aqui **a busca tem mais
atributo físico que o anúncio**: `floorSize`, `floorLevel`, `numberOfBedrooms`,
`numberOfBathroomsTotal` e `streetAddress` existem na lista e SOMEM no detalhe,
que degrada para um `Product` genérico. Por isso os atributos da busca viajam
em `hints` e o anúncio só acrescenta dinheiro e texto.

Custo: 2 requisições por imóvel. O OLX faria em 1, mas está desligado por D-027.

SEMÂNTICA DE ENDEREÇO, medida em 30 anúncios (2026-08-30):

    addressLocality   1 valor distinto  'Santos'   -> é a CIDADE
    addressRegion     1 valor distinto  'SP'       -> é a UF
    addressCountry    1 valor distinto  'BR'
    streetAddress     varia                        -> é do imóvel

Mapear `addressLocality` para `neighborhood` gravaria "Santos" como bairro em
todos os anúncios -- o erro que o `veploy` cometeu em 2.029 linhas. **O bairro
só existe na URL do anúncio**, e é de lá que sai. O método que revelou isso é o
mesmo daquele caso: comparar o valor entre anúncios diferentes; constante
significa que não é atributo do imóvel.

LANÇAMENTOS FICAM DE FORA. A busca mistura `/imoveis-lancamentos/` no meio dos
`/imovel/`, e eles anunciam FAIXA em vez de valor -- `"Apartamento para comprar
com 96 - 351 m²"`. Um `floorSize` de faixa entraria como área única e nenhuma
exceção seria levantada. Filtro por `/imovel/` na URL.

PÁGINA MORTA. Com `requests` o anúncio inexistente devolve 404, que o `Fetcher`
já trata. Mas com outro cliente eu vi o MESMO anúncio devolver **200 com 293 KB
e JSON-LD válido**, servindo a página "Oops. Não conseguimos encontrar". Como o
comportamento não é estável, `fetch()` checa as duas coisas.

RATE LIMIT: 403 a partir da ~13ª requisição seguida em 2026-08-30. `rate_limit_rps`
conservador no `sources.yaml`.

PII: ~10 telefones e ~9 nomes por página (94 e 85 em 9 páginas). Nada disso é
lido: o registro é projetado no `parse()` e o gate de PII cobre o resto.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from urllib.parse import urlsplit

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

API_FIELDS = frozenset({
    # da BUSCA
    "floorSize", "floorLevel", "numberOfBedrooms", "numberOfBathroomsTotal",
    "streetAddress", "addressLocality", "addressRegion", "description", "url",
    # do ANÚNCIO
    "offers.price", "sku", "condoFee", "iptu",
})

NEVER_MAP = frozenset({
    # pessoa, nunca imóvel
    "advertiser", "account", "broker", "creci", "phone", "whatsapp", "email",
    "contactInfo", "agent", "RealEstateAgent",
    # sessão
    "userId", "deviceId", "trackingId", "anonymousId",
    # mídia e ruído
    "image", "images", "video", "medias", "recommendations",
})

TERMO = {"venda": "venda", "locacao": "aluguel"}
TIPOS_LD = ("Apartment", "House", "SingleFamilyResidence", "Residence")

BUSCA = "https://www.vivareal.com.br/{termo}/{uf}/{cidade}/"

# A paginacao da fonte CORTA. Medido em 2026-08-30: pagina 50 devolve 200 com
# 28 anuncios, pagina 60 devolve 404. Passar disso so gera 404 -- e por isso
# que 3.000 anuncios saem de FACETAS, nao de paginar fundo.
PAGINAS_MAX = 55

# Cada faceta tem paginacao propria e pouca sobreposicao: 5 facetas x 3 paginas
# deram 304 anuncios distintos contra 56 da busca simples (2026-08-30).
TIPOS_FACETA = ("apartamento_residencial", "casa_residencial",
                "cobertura_residencial", "kitnet_residencial")

# `/imovel/{tipo}-{n}-quartos-{bairro}-{cidade}-...-{transacao}-RS{preco}-id-{id}/`
_ID_URL = re.compile(r"-id-(\d+)")
_TIPO_URL = re.compile(r"/imovel/([a-z]+)-")
# `/imovel/imovel-comercial-5-quartos-...` faz o regex acima devolver "imovel",
# que e o segmento do CAMINHO e nao um tipo. Melhor None do que uma categoria
# inventada: o vocabulario a jusante sabe lidar com ausencia, nao com lixo.
# 2 linhas em 3.000 na coleta de 2026-08-31.
_TIPO_INVALIDO = frozenset({"imovel", "imoveis", "venda", "aluguel"})
_TRANSACAO_URL = re.compile(r"-(venda|aluguel)-RS")
_CONDO = re.compile(r'data-testid="condoFee"[^>]*>([^<]{1,40})<')
_IPTU = re.compile(r'data-testid="iptu"[^>]*>([^<]{1,40})<')

# MICRODATA do anúncio -- `itemProp` pareado com o texto legível do mesmo <li>.
#
# Achado em 2026-08-30 DEPOIS da primeira corrida a seco, que devolveu
# `parking_spots`, `suites` e `amenities` em 0%. Zero era erro meu: a URL do
# anúncio já dizia "com-garagem", então a informação existia. Estava aqui.
#
#     numberOfParkingSpaces -> "1 vaga"        numberOfSuites -> "1 suíte"
#     floorLevel            -> "22º andar"     floorSize      -> "68 m²"
#     PETS_ALLOWED          -> "Aceita animais"
#     PANORAMIC_VIEW        -> "Vista panorâmica"
#
# O `(?!itemProp=)` impede o casamento de atravessar para o próximo <li> e
# parear um rótulo com o texto do vizinho.
_MICRO = re.compile(
    r'itemProp="(\w+)"[^>]*>(?:(?!itemProp=).){0,600}?'
    r'class="amenities-item-text"[^>]*>([^<]{1,60})<', re.S)

# Numérico vem do `itemProp`; o resto da lista é amenidade.
_NUMERICOS = {
    "numberOfParkingSpaces": "parking_spots",
    "numberOfSuites": "suites",
    "numberOfRooms": "bedrooms",
    "numberOfBathroomsTotal": "bathrooms",
    "floorLevel": "floor_level",
}
# A pagina "Oops" ja foi servida com HTTP 200 -- ver docstring.
_MORTA = re.compile(r"(?i)n[ãa]o conseguimos encontrar a p[áa]gina|>\s*Oops\.\s*<")


def _slug(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _blocos(html: str) -> list[dict]:
    out: list[dict] = []
    for b in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                        html, re.S):
        try:
            o = json.loads(b)
        except (ValueError, TypeError):
            continue
        out += [n for n in (o if isinstance(o, list) else [o]) if isinstance(n, dict)]
    return out


def _numero(valor) -> float | None:
    """
    Zero é ausência em dinheiro e em área. Faixa (`"96 - 351"`) devolve None:
    lançamento anuncia intervalo, e um intervalo lido como valor único é dado
    inventado que não levanta nada.
    """
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).strip()
    if not texto or "-" in texto.replace("R$", "").strip().lstrip("-"):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", texto.replace(".", ""))
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return n if n > 0 else None


def _inteiro(valor) -> int | None:
    """
    Contagem. NÃO delega a `_numero`, e a diferença não é estilo.

    Em dinheiro e área, zero significa "não informado". Em contagem, zero é
    resposta: `0 suítes` é um fato sobre o imóvel, e virar `None` o transforma
    em ausência de dado. Delegar apagava essa distinção -- pego pelo teste
    `test_zero_suites_e_resposta_valida_nao_ausencia` em 2026-08-30.
    """
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).strip()
    if not texto or "-" in texto.replace("R$", "").strip().lstrip("-"):
        return None
    m = re.search(r"\d+", texto.replace(".", ""))
    return int(m.group()) if m else None


def _dinheiro(texto) -> float | None:
    """`"R$ 587/mês"` -> 587.0 · `"R$ 0"` -> None."""
    if texto is None:
        return None
    m = re.search(r"[\d\.]+", str(texto).replace("\xa0", " "))
    if not m:
        return None
    try:
        n = float(m.group().replace(".", ""))
    except ValueError:
        return None
    return n if n > 0 else None


def _bairro(url: str, cidade: str | None) -> str | None:
    """
    O bairro fica entre `-quartos-` e o slug da CIDADE.

    Ancorar na cidade em vez de contar segmentos: `ponta-da-praia` tem três
    palavras e `jose-menino` tem duas, então posição fixa erraria.
    """
    caminho = url.split("/imovel/")[-1]
    c = _slug(cidade)
    if not c or f"-{c}-" not in caminho:
        return None
    antes = caminho.split(f"-{c}-")[0]
    m = re.search(r"-quartos?-(.+)$", antes)
    if not m:
        return None
    return m.group(1).replace("-", " ").strip() or None


@register
class VivaRealAdapter(ListingSource):
    """CRAWL+DOM em duas superfícies: busca para atributo, anúncio para dinheiro."""

    platform = "vivareal"
    supports_feed = False
    jsonld_required = True

    #: Sufixo de tipo aplicado a cada faceta geografica. `None` = todos os
    #: tipos. Verificado em 2026-09-05: a faceta de tipo SOZINHA nao publica
    #: sub-faceta geografica (caparia em ~1.620 anuncios), mas a combinacao
    #: `{zona}/{bairro}/apartamento_residencial/` responde 200 e rende 30
    #: anuncios por pagina contra 19 da geografica pura.
    tipo_alvo: str | None = None

    def _pagina(self, base: str, vistos: set[str],
                achadas: list[str] | None = None) -> Iterator[dict]:
        """
        Percorre uma busca até esgotá-la. Gerador: o orçamento de `collect.py`
        corta no meio e as páginas seguintes nunca são pedidas.

        A paginação CORTA em ~55 (medido 2026-08-30: página 50 devolve 200 com
        28 anúncios, página 60 devolve 404). Por isso o teto de `PAGINAS_MAX`
        não é preguiça -- é o limite da fonte, e passar dele só gera 404.
        """
        for p in range(1, PAGINAS_MAX + 1):
            alvo = base if p == 1 else f"{base}?pagina={p}"
            resp = self.session.get(alvo)
            if resp is None:
                return                      # 404: a faceta acabou
            if p == 1 and achadas is not None:
                achadas.extend(self._facetas_no_html(resp.text, base))
            novos = 0
            for no in _blocos(resp.text):
                url = no.get("url") or ""
                # Lancamento fica de fora: anuncia FAIXA de area, nao valor.
                if no.get("@type") not in TIPOS_LD or "/imovel/" not in url:
                    continue
                # Deduplica pelo ID, NAO pela URL. A fonte emite a mesma
                # unidade sob URLs diferentes que variam so no preco embutido
                # -- `...-RS697540-id-2899535415/` e `...-RS697770-id-2899535415/`
                # sao o mesmo imovel. Deduplicar por URL deixou passar 5 pares
                # em 3.000 na coleta de 2026-08-31, e como `property_id` vem de
                # sha1(dominio + listing_id), eles viraram id duplicado na base.
                m = _ID_URL.search(url)
                chave = m.group(1) if m else url
                if chave in vistos:
                    continue
                vistos.add(chave)
                novos += 1
                yield no
            if novos == 0:
                return                      # pagina sem inedito; nao insistir

    @staticmethod
    def _facetas_no_html(html: str, raiz: str) -> list[str]:
        """
        As facetas que a PRÓPRIA PÁGINA linka, em vez de URL construída por nós.

        A gramática muda entre cidades e eu tinha embutido a de Santos.
        Medido em 2026-09-03:

            Santos      /venda/sp/santos/bairros/gonzaga/
            São Paulo   /venda/sp/sao-paulo/zona-sul/itaim-bibi/

        Construir `bairros/{x}/` para São Paulo devolve 404 -- a coleta ficaria
        presa no teto de paginação da busca simples, ~1.300 anúncios de 863 mil,
        sem erro nenhum. Ler os links resolve as duas cidades e qualquer outra.

        Lê do HTML JÁ BUSCADO, sem requisição extra. A versão anterior buscava a
        raiz de novo e recebia `None`: o `Fetcher` faz GET condicional, a
        segunda visita à mesma URL na mesma corrida devolve **304**, e 304 vira
        `None`. O resultado era zero facetas, sem erro nenhum -- exatamente o
        tipo de falha que a coleta inteira esconde.
        """
        caminho = urlsplit(raiz).path
        vistos, saida = set(), []
        for c in re.findall(rf'href="({re.escape(caminho)}[^"?#]{{3,80}}/)"', html):
            if c.rstrip("/") == caminho.rstrip("/") or c in vistos:
                continue
            vistos.add(c)
            saida.append(f"https://www.vivareal.com.br{c}")
        return saida

    def _facetas(self, transacao: str, descobertas: list[str]) -> Iterator[str]:
        """
        A busca simples esgota no teto de paginação (~55 páginas). Cada FACETA
        tem paginação própria e pouca sobreposição -- medido em 2026-08-30:
        5 facetas × 3 páginas deram 304 distintos contra 56 da busca simples.

        Ordem: busca geral, facetas que a página publica, e depois as que forem
        descobertas nas próprias facetas (uma página de zona linka seus bairros).
        Nada é lista fixa: a fonte se descreve sozinha e cidade nova não exige
        cadastro.
        """
        termo = TERMO[transacao]
        cidade = _slug(self.cidade or "santos")
        uf = _slug(self.uf or "sp")
        raiz = BUSCA.format(termo=termo, uf=uf, cidade=cidade)
        # A RAIZ SEM TIPO e sempre percorrida, mesmo com `tipo_alvo`: e ela que
        # publica os links das facetas geograficas. A faceta de tipo publica
        # ZERO sub-facetas (medido 2026-09-05), entao substituir a raiz por ela
        # prendia a coleta numa faceta so -- 1.024 anuncios em vez de milhares,
        # sem erro nenhum. Foi o que aconteceu na corrida de 2026-09-05.
        yield raiz
        if self.tipo_alvo:
            yield f"{raiz}{self.tipo_alvo}/"
        # `descobertas` e alimentada por `_pagina` enquanto percorre, entao a
        # lista cresce durante a iteracao: a pagina da cidade publica as zonas,
        # a da zona publica seus bairros.
        i = 0
        while i < len(descobertas):
            yield descobertas[i]
            i += 1

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        if transaction not in TERMO:
            raise AdapterError(f"transação desconhecida: {transaction!r}")

        vistos: set[str] = set()
        pendentes: list[str] = []
        vistas_facetas: set[str] = set()
        nenhum = True

        def alimenta():
            """
            Facetas de segundo nivel: a pagina de uma zona linka seus bairros.
            A lista cresce enquanto anda, entao Sao Paulo (zona -> bairro)
            desce um nivel a mais que Santos (bairro direto).
            """
            for base in self._facetas(transaction, pendentes):
                if base in vistas_facetas:
                    continue
                vistas_facetas.add(base)
                achadas: list[str] = []
                # A faceta geografica e SEMPRE percorrida, e e dela que saem as
                # sub-facetas -- a pagina da zona linka seus bairros.
                yield from self._pagina(base, vistos, achadas)
                # Com `tipo_alvo`, percorre TAMBEM a combinacao geografia+tipo.
                # A fonte aceita `{zona}/{bairro}/apartamento_residencial/` mas
                # nao a publica como link, entao esta e a unica construcao de
                # URL do adapter -- e foi verificada em 6 combinacoes antes de
                # entrar (30 anuncios por pagina contra 19 da geografica pura).
                if self.tipo_alvo and not base.rstrip("/").endswith(self.tipo_alvo):
                    yield from self._pagina(f"{base}{self.tipo_alvo}/", vistos)
                for filha in achadas:
                    if filha not in vistas_facetas and filha not in pendentes:
                        pendentes.append(filha)

        for no in alimenta():
            nenhum = False
            url = no["url"]
            endereco = no.get("address") or {}
            cidade = endereco.get("addressLocality")
            tipo = _TIPO_URL.search(url)
            tamanho = no.get("floorSize")
            yield DiscoveredURL(
                url=url,
                transaction_type=transaction,
                discovery_path=f"busca/{transaction}",
                listing_id=(_ID_URL.search(url).group(1)
                            if _ID_URL.search(url) else None),
                hints={
                    # `addressLocality` e a CIDADE e `addressRegion` a UF --
                    # medido constante em 30 anuncios. O bairro vem da URL.
                    "city": cidade,
                    "state": endereco.get("addressRegion"),
                    "address": endereco.get("streetAddress"),
                    "neighborhood": _bairro(url, cidade),
                    "property_type": (tipo.group(1)
                                      if tipo and tipo.group(1) not in _TIPO_INVALIDO
                                      else None),
                    "area_m2": _numero((tamanho or {}).get("value")
                                       if isinstance(tamanho, dict) else tamanho),
                    "floor_level": _inteiro(no.get("floorLevel")),
                    "bedrooms": _inteiro(no.get("numberOfBedrooms")),
                    "bathrooms": _inteiro(no.get("numberOfBathroomsTotal")),
                    "description_clean": (no.get("description") or None),
                },
            )

        if nenhum:
            # Zero e falha, nao sucesso. Um termo errado devolve 200 com zero
            # resultados e produziria coleta limpa, confiante e vazia. O guarda
            # fica DEPOIS do laco porque `discover` e gerador preguicoso: o
            # orcamento de `collect.py` pode corta-lo no meio, e so a ausencia
            # TOTAL e falha.
            raise AdapterError(
                f"vivareal: busca de {transaction} em {domain} não devolveu "
                f"nenhum anúncio. Verifique o termo da URL antes de aceitar o vazio.")
        log.info("%s: %d anúncio(s) de %s em %d faceta(s)",
                 domain, len(vistos), transaction, len(vistas_facetas))

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        """
        Anúncio ausente é resultado ordinário -- mas aqui ele tem duas formas.

        Com `requests` vem 404, que a camada de fetch já resolve. Com outro
        cliente o MESMO anúncio devolveu 200 com 293 KB e JSON-LD válido,
        servindo a página "Oops". Sem esta checagem, aquela página entraria na
        base como imóvel sem preço.
        """
        payload = super().fetch(discovered)
        if payload is None:
            return None
        if _MORTA.search(payload.text[:250_000]):
            log.info("%s: anúncio removido (página de erro com HTTP %d)",
                     discovered.url, payload.status)
            return None
        return payload

    @staticmethod
    def _microdados(html: str) -> tuple[dict, list[str]]:
        """
        Lê o bloco de características: números pelo `itemProp`, amenidades pelo
        texto. Devolve (numéricos, amenidades).

        Preferir o `itemProp` ao texto não é preciosismo: "1 vaga" e "2 vagas"
        têm plural diferente, "0 suítes" existe, e o `itemProp` não muda.
        """
        numericos: dict[str, int] = {}
        amenidades: list[str] = []
        for chave, texto in _MICRO.findall(html):
            alvo = _NUMERICOS.get(chave)
            if alvo:
                n = _inteiro(texto)
                if n is not None:
                    numericos[alvo] = n
            elif chave.isupper() and texto.strip():
                # `PETS_ALLOWED` -> "Aceita animais". Guarda o rótulo legível:
                # é ele que o vocabulário do projeto normaliza.
                amenidades.append(texto.strip())
        return numericos, amenidades

    def parse(self, payload: Payload) -> dict:
        html = payload.text
        blocos = _blocos(html)
        produto = next((n for n in blocos if n.get("@type") == "Product"), {})
        oferta = produto.get("offers") or {}
        numericos, amenidades = self._microdados(html)

        registro = dict(payload.discovered.hints)
        # O anúncio tem precedência sobre a busca: `numberOfParkingSpaces` e
        # `numberOfSuites` só existem aqui, e `floorLevel` é mais completo.
        registro.update({k: v for k, v in numericos.items() if v is not None})
        if amenidades:
            registro["amenities"] = amenidades
        registro.update({
            "link": payload.url,
            "listing_id": produto.get("sku") or payload.discovered.listing_id,
            "transaction_type": payload.discovered.transaction_type,
            "price": _numero(oferta.get("price")),
            # Do DOM, ancorados em `data-testid` -- nao em rotulo de texto, que
            # o carrossel de imoveis relacionados tambem traz.
            "condo_fee": _dinheiro(m.group(1)) if (m := _CONDO.search(html)) else None,
            "iptu_tax": _dinheiro(m.group(1)) if (m := _IPTU.search(html)) else None,
        })

        # A descricao do anuncio e mais longa que a da busca; a maior vence.
        longa = produto.get("description")
        curta = registro.get("description_clean")
        if isinstance(longa, str) and (not curta or len(longa) > len(curta)):
            registro["description_clean"] = longa.strip()

        return {k: v for k, v in registro.items() if v not in (None, "", [])}


__all__ = ["VivaRealAdapter", "API_FIELDS", "NEVER_MAP"]
