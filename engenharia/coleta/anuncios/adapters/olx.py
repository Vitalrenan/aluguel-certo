"""
OLX — marketplace nacional. Inventário em MEDICOES §17, decisão em D-026.

VIA ELEITA: o **anúncio individual**, lendo o JSON embutido no HTML do servidor.
Não a busca, e não o JSON-LD.

Por que não a busca: a página de resultados traz título, preço e foto -- nada de
condomínio, IPTU, amenidades ou descrição. Os quatro campos que a skill
`novo-adapter` usa como canário de "você está lendo a lista".

Por que não o JSON-LD: ele **muda de `@type` entre venda e locação**. Anúncio de
venda emite `BuyAction`; o de locação, não. Um extrator ancorado em `BuyAction`
mede 100% na venda e ~0% na locação, e o resultado parece "campo com 30% de
preenchimento" em vez de bug. Foi exatamente o que aconteceu comigo: registrei
`description` e `postalCode` a 30% antes de perceber que a amostra misturava as
duas transações.

TAXA DE PREENCHIMENTO medida em 2026-08-30 (n=10 anúncios de venda + locação):

    real_estate_type    100%      condominio          100%
    size                100%      iptu                 80%
    rooms               100%      re_features         100%
    bathrooms           100%      re_complex_features  90%
    garage_spaces       100%      zipcode             100%
    listId              100%      neighbourhood       100%
    subject             100%      municipality        100%

`zipcode` a 100% é o ganho grande: a base atual tem CEP em 32,4%.

QUATRO ARMADILHAS MEDIDAS, todas com teste nomeado em `tests/test_olx.py`:

  1. IPTU EM DUAS CÓPIAS. O `dataLayer` traz `"iptu":null` enquanto o array
     `properties` traz `"R$ 375"` para o mesmo anúncio. Ler a cópia errada dá
     IPTU 100% nulo -- o caso que o projeto já viu 3 vezes em 4.
     Consequência: este adapter lê SÓ o array `properties`.

  2. `"R$ 0"` SIGNIFICA NÃO INFORMADO. Aparece em condomínio e IPTU. Coagir
     para 0.0 fabricaria observação em dois dos três campos de maior ganho do
     modelo.

  3. `bathrooms` VEM `"5 ou mais"`. String categórica onde se espera número.
     Vira 5 -- é um piso, não o valor -- e o teste fixa isso.

  4. VOCABULÁRIO. O OLX diz `Aluguel`; o schema diz `locacao`.

RATE LIMIT: o domínio devolveu 403 depois de ~25 requisições seguidas em
2026-08-30. Não é bloqueio de política (`robots.txt` permite os caminhos usados);
é limitação de taxa, e o `Fetcher` precisa recuar.

PII: 26 telefones e 12 CRECI em 10 páginas. O OLX expõe
`apigw.olx.com.br/v1/showphone`, que revela o telefone do vendedor -- este
adapter nunca o chama, e `seller` inteiro está em `NEVER_MAP`.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Mapeamento explícito. A exclusão é greppável e testada, não implícita.
# --------------------------------------------------------------------------

API_FIELDS = frozenset({
    "listId", "subject", "price", "zipcode", "neighbourhood", "municipality",
    "real_estate_type", "size", "rooms", "bathrooms", "garage_spaces",
    "condominio", "iptu", "re_features", "re_complex_features", "re_types",
})

NEVER_MAP = frozenset({
    # --- pessoa, nunca imóvel -------------------------------------------
    "seller", "sellerName", "user", "userId", "phone", "phoneNumber",
    "showphone", "email", "creci", "advertiser", "contact",
    # --- identidade de sessão, não do imóvel ----------------------------
    "clientId", "sessionId", "deviceId", "trackingId", "userToken",
    # --- a cópia ERRADA do IPTU (armadilha 1): o dataLayer traz null
    #     enquanto o array `properties` traz o valor.
    "dataLayer",
    # --- mídia: peso sem sinal de preço ---------------------------------
    "images", "thumbnail", "videos",
})

PLATAFORMA_TRANSACAO = {"venda": "venda", "aluguel": "locacao"}

# Caminhos de descoberta. A transação é do CAMINHO -- e é conferida contra o
# `real_estate_type` do anúncio em `parse()`, que é a fonte de maior precedência.
BUSCA = ("https://www.olx.com.br/imoveis/{termo}/estado-{uf}/"
         "baixada-santista-e-litoral-sul/{cidade}")

_URL_ANUNCIO = re.compile(r'(https://\w+\.olx\.com\.br/[^"\'\s]*?-(\d{10,}))(?=["\'\s])')

_STR = r'"((?:[^"\\]|\\.)*)"'
_PROP = re.compile(r'\{"name":' + _STR + r',"label":' + _STR
                   + r',"value":(' + _STR + r'|null)')


def _texto(bruto: str | None) -> str | None:
    """
    Desescapa o conteúdo de uma string JSON lida por regex.

    A página real manda UTF-8 direto (`"Boqueirão"`), mas nada obriga a isso --
    `"Boqueir\\u00e3o"` é JSON igualmente válido, e um extrator que devolve o
    escape cru grava lixo no bairro sem levantar nada.
    """
    if bruto is None:
        return None
    try:
        return json.loads('"' + bruto + '"')
    except ValueError:
        return bruto


def _campo(html: str, chave: str) -> str | None:
    """Escalar do JSON embutido. `null` literal vira None, nunca a string."""
    m = re.search('"' + re.escape(chave) + r'":(' + _STR + r'|null|\d+)', html)
    if not m:
        return None
    bruto = m.group(1)
    if bruto == "null":
        return None
    return _texto(m.group(2)) if m.group(2) is not None else bruto


def _propriedades(html: str) -> dict[str, str | None]:
    """
    O array `properties` -- a ÚNICA leitura de condomínio e IPTU.

    O `dataLayer` da mesma página traz `"iptu":null` para anúncios cujo
    `properties` traz `"R$ 375"`. Ver armadilha 1 no topo do módulo.
    """
    out: dict[str, str | None] = {}
    for m in _PROP.finditer(html):
        nome, valor = m.group(1), m.group(3)
        if nome in out:
            continue                       # primeira ocorrência vence
        out[nome] = None if valor == "null" else _texto(valor.strip('"'))
    return out


# --------------------------------------------------------------------------
# Conversão. Zero em dinheiro é ausência; zero em contagem é resposta.
# --------------------------------------------------------------------------

_DIGITOS = re.compile(r"[\d\.,]+")


def _dinheiro(valor) -> float | None:
    """
    `"R$ 1.234"` -> 1234.0 · `"R$ 0"` -> None · `None` -> None

    Zero NÃO é preço nem taxa: o OLX usa `R$ 0` para "não informado". Persistir
    0.0 fabricaria observação em condomínio e IPTU, dois dos três campos de
    maior ganho do modelo.
    """
    if valor is None or valor == "":
        return None
    m = _DIGITOS.search(str(valor))
    if not m:
        return None
    texto = m.group().replace(".", "").replace(",", ".")
    try:
        n = float(texto)
    except ValueError:
        return None
    return n if n > 0 else None


def _inteiro(valor) -> int | None:
    """
    `"3"` -> 3 · `"5 ou mais"` -> 5 · `"70m²"` -> 70 · `None` -> None

    `"5 ou mais"` é um BALDE, não um valor: 5 é o piso da faixa. Vira 5 porque
    é a leitura menos errada, e o teste fixa o comportamento para que a próxima
    pessoa saiba que é aproximação, não medida.
    """
    if valor is None or valor == "":
        return None
    m = re.search(r"\d+", str(valor).replace(".", ""))
    return int(m.group()) if m else None


def _area(valor) -> float | None:
    """`"70m²"` -> 70.0. Zero é ausência, como em dinheiro."""
    if valor is None:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(valor).replace(".", ""))
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    return n if n > 0 else None


def _cep(valor) -> str | None:
    d = re.sub(r"\D", "", str(valor or ""))
    return d if len(d) == 8 else None


def _lista(valor) -> list[str]:
    """`"Academia, Ar condicionado"` -> ["Academia", "Ar condicionado"]."""
    if not valor:
        return []
    return [p.strip() for p in str(valor).split(",") if p.strip()]


def _transacao_e_tipo(real_estate_type: str | None) -> tuple[str | None, str | None]:
    """
    `"Venda - apartamento padrão"` -> ("venda", "apartamento padrão")
    `"Aluguel - apartamento padrão"` -> ("locacao", "apartamento padrão")

    O OLX diz `Aluguel`; o schema diz `locacao`. Traduzir aqui, não a jusante.
    """
    if not real_estate_type:
        return None, None
    partes = [p.strip() for p in str(real_estate_type).split("-", 1)]
    transacao = PLATAFORMA_TRANSACAO.get(partes[0].strip().lower())
    tipo = partes[1] if len(partes) == 2 and partes[1] else None
    return transacao, tipo


@register
class OlxAdapter(ListingSource):
    """
    CRAWL+DOM: percorre a busca por transação, lê o JSON embutido do anúncio.

    Uma requisição por imóvel -- o anúncio traz tudo. O VivaReal exige duas
    (busca para atributo físico, anúncio para condomínio/IPTU).
    """

    platform = "olx"
    supports_feed = False
    jsonld_required = False

    def _urls_da_busca(self, transacao: str, paginas: int = 1) -> list[tuple[str, str]]:
        termo = {"venda": "venda", "locacao": "aluguel"}[transacao]
        cidade = (self.source.city or "santos").lower().replace(" ", "-")
        uf = (self.source.state or "sp").lower()
        achados: list[tuple[str, str]] = []
        vistos: set[str] = set()
        for p in range(1, paginas + 1):
            alvo = BUSCA.format(termo=termo, uf=uf, cidade=cidade)
            if p > 1:
                alvo = f"{alvo}?o={p}"
            resp = self.session.get(alvo)
            if resp is None:
                break
            for url, ident in _URL_ANUNCIO.findall(resp.text):
                if ident not in vistos:
                    vistos.add(ident)
                    achados.append((url, ident))
        return achados

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        if transaction not in PLATAFORMA_TRANSACAO.values():
            raise AdapterError(f"transação desconhecida: {transaction!r}")
        achados = self._urls_da_busca(transaction)
        if not achados:
            # Zero é falha, não sucesso. Uma busca com o termo errado devolve
            # 200 com zero resultados e produziria coleta limpa e vazia.
            raise AdapterError(
                f"olx: busca de {transaction} em {domain} não devolveu nenhum "
                f"anúncio. Verifique o termo da URL antes de aceitar o vazio.")
        log.info("%s: %d anúncio(s) de %s", domain, len(achados), transaction)
        for url, ident in achados:
            yield DiscoveredURL(
                url=url,
                transaction_type=transaction,
                discovery_path=f"busca/{transaction}",
                listing_id=ident,
                hints={"city": self.source.city, "state": self.source.state},
            )

    def parse(self, payload: Payload) -> dict:
        html = payload.text
        props = _propriedades(html)

        transacao, tipo = _transacao_e_tipo(props.get("real_estate_type"))
        # O anúncio tem precedência sobre o caminho de descoberta -- mas uma
        # divergência é sinal de que a busca devolveu o que não foi pedido,
        # e isso precisa aparecer, não ser absorvido em silêncio.
        do_caminho = payload.discovered.transaction_type
        if transacao and transacao != do_caminho:
            log.warning("%s: busca de %s devolveu anúncio de %s",
                        payload.url, do_caminho, transacao)

        registro = {
            "link": payload.url,
            "listing_id": _campo(html, "listId") or payload.discovered.listing_id,
            "transaction_type": transacao or do_caminho,
            "property_type": tipo,
            "price": _dinheiro(_campo(html, "price")),

            "city": _campo(html, "municipality") or payload.discovered.hints.get("city"),
            "state": payload.discovered.hints.get("state"),
            "neighborhood": _campo(html, "neighbourhood"),
            "cep": _cep(_campo(html, "zipcode")),

            # SÓ do array `properties`. Ver armadilha 1.
            "condo_fee": _dinheiro(props.get("condominio")),
            "iptu_tax": _dinheiro(props.get("iptu")),

            "area_m2": _area(props.get("size")),
            "bedrooms": _inteiro(props.get("rooms")),
            "bathrooms": _inteiro(props.get("bathrooms")),
            "parking_spots": _inteiro(props.get("garage_spaces")),

            "amenities": (_lista(props.get("re_features"))
                          + _lista(props.get("re_complex_features"))),
            "description_clean": self._descricao(html),
        }
        return {k: v for k, v in registro.items() if v not in (None, [], "")}

    @staticmethod
    def _descricao(html: str) -> str | None:
        """
        A descrição vive no JSON-LD, cujo `@type` muda entre venda e locação.
        Por isso NÃO se filtra por tipo: varre todo nó com `Object.description`
        e fica com o mais longo, que é o corpo do anúncio -- os curtos são a
        meta description da página.
        """
        melhor = None
        for bloco in re.findall(
                r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
            try:
                obj = json.loads(bloco)
            except (ValueError, TypeError):
                continue
            for no in (obj if isinstance(obj, list) else [obj]):
                if not isinstance(no, dict):
                    continue
                alvo = no.get("Object")
                texto = (alvo or {}).get("description") if isinstance(alvo, dict) else None
                texto = texto or no.get("description")
                if isinstance(texto, str) and (melhor is None or len(texto) > len(melhor)):
                    melhor = texto.strip()
        return melhor or None


__all__ = ["OlxAdapter", "API_FIELDS", "NEVER_MAP"]
