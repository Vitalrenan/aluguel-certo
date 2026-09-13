"""
Microsistec Group A adapter -- scimoveissantos.com.br, casabellaimoveis.com.

The Group A/B split (§13.1) was made on URL grammar; it holds on extraction
too. Group B turned out to have a JSON API (§14). Group A does not, and its
`/public/search` returns 404 on both domains -- tested, not assumed.

    /{id}-{type}-em-{city}-bairro-{neighborhood}.html

**The transaction problem §13.3 identified is real here and unsolved by the
usual paths.** Measured over 400 sitemap URLs: zero carry venda/locação. The
JSON-LD `Offer` has `price` and `priceCurrency` but no `businessFunction` and
no `availability`. The words "venda" and "locação" both appear in the page
text -- in the site chrome and in the related-listings rail -- so a plain text
match is ambiguous and would be wrong roughly as often as it was right.

The fact lives in exactly one place, the main price block:

    <div id="info-valores"> ... <ul class="valores">
      <li class="first-item"><span>Locação</span><b>R$ 4.500,00</b></li>
      <li><span>IPTU</span><b>R$ 175,00</b></li>
      <li><span>Condomínio</span><b>R$ 800,00</b></li>

Anchoring on `id="info-valores"` is what separates this listing's transaction
from the "R$ 380.000,00 - Venda" of the related-listings rail further down the
same page. The anchor is the adapter; everything else is bookkeeping.

**Consequence for the contract.** The transaction is knowable only after the
page is fetched, but `discover()` must label a URL before yielding it. So
`discover()` fetches, extracts, caches the small parsed record, and yields a
labelled URL; `fetch()` then serves from that cache. Total requests are
unchanged -- one per listing -- and the cached objects are the extracted dicts,
not the 130 KB pages.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from comum.jsonld import extract_blocks, find

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

# 38-apartamento-em-santos-bairro-embare.html
# A secondary shape has no numeric id (P0 §4.1): p-apartamento-bairro_x-.html
LISTING_URL = re.compile(
    r"/(\d+)-([a-z0-9-]+)-em-([a-z0-9-]+)-bairro-([a-z0-9_-]+)\.html$", re.I)
LISTING_URL_NOID = re.compile(r"/([a-z0-9-]*-bairro[_-][a-z0-9_-]+-?)\.html$", re.I)

# The main price block, and only it.
INFO_VALORES = re.compile(
    r'id="info-valores".*?(?=<footer|most-view|related|</aside>)', re.S | re.I)
# Label/value pairs. The site uses three shapes for the same idea, so the
# separator is permissive: adjacent (price block), separated by <br> (summary
# strip), or split across table cells (composition table).
#
#   <li class="first-item"><span>Locação</span><b>R$ 4.500,00</b></li>
#   <li><i class="fa fa-bed"></i><br><span>Dorm.</span><br><b>2</b></li>
#   <td><span><i class="fa fa-bed"></i> Dormitório(s)</span></td>
#     <td class="text-right"><b>2</b></td>
VALOR_ROW = re.compile(
    r"<span[^>]*>(?:\s*<i[^>]*>\s*</i>)?\s*([^<]{2,30}?)\s*</span>"
    r"(?:\s|<br\s*/?>|</td>\s*<td[^>]*>)*"
    r"<b[^>]*>\s*([^<]{1,30}?)\s*(?:<|$)", re.I)

# Everything below this point on the page is other listings, not this one.
BEFORE_RELATED = re.compile(r"(most-view|related|Imóveis semelhantes|mais-vistos)", re.I)

# Amenities live in two adjacent <section> blocks. Anchoring on the section
# class -- not on the heading text -- is what stops one bleeding into the
# other, and stops the related-listings rail below from contributing.
#
#   <section class="tabs-caracteristicas grid-12">   -> Características Gerais
#   <section class="tabs-rooms grid-12">             -> Cômodos
#
# Items are <li><i class="fa fa-check-circle"></i>TEXTO</li>.
SECAO_CARACTERISTICAS = re.compile(
    r'<section[^>]*class="[^"]*tabs-caracteristicas[^"]*"[^>]*>(.*?)</section>', re.I | re.S)
SECAO_COMODOS = re.compile(
    r'<section[^>]*class="[^"]*tabs-rooms[^"]*"[^>]*>(.*?)</section>', re.I | re.S)
ITEM_LISTA = re.compile(r"<li[^>]*>(?:\s*<i[^>]*>\s*</i>)?\s*([^<]{2,50}?)\s*</li>", re.I)

TRANSACTION_LABEL = {
    "locacao": "locacao", "aluguel": "locacao", "locação": "locacao",
    "venda": "venda",
}

# Detail table: <span>Dormitório</span> ... <b>3</b> style rows elsewhere in
# the page. Reused from the same VALOR_ROW shape.
# Keys are the NORMALISED labels (see _label): accents stripped, "(s)"
# and trailing dots removed, "sendo " dropped. Observed on live pages:
# "Dorm.", "Suítes", "Vagas", "Banheiros", "Área (útil)", "Dormitório(s)",
# "sendo Suíte(s)".
DETAIL_FIELD = {
    "dorm": "bedrooms", "dormitorio": "bedrooms", "dormitorios": "bedrooms",
    "quarto": "bedrooms", "quartos": "bedrooms",
    "banheiro": "bathrooms", "banheiros": "bathrooms",
    "suite": "suites", "suites": "suites",
    "vaga": "parking_spots", "vagas": "parking_spots",
    "area util": "area_m2", "area": "area_m2",
    "area total": "area_total_m2",
    "andar": "floor_level",
}
MONEY_FIELD = {"iptu": "iptu_tax", "condominio": "condo_fee"}


def _slug(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def _label(text: str) -> str:
    """
    Normalise a label to its dictionary key.

    'Área (útil)' -> 'area util'; 'sendo Suíte(s)' -> 'suite'; 'Dorm.' ->
    'dorm'. Done here rather than by enumerating every spelling in
    DETAIL_FIELD, because the site varies the same label across three widgets.
    """
    t = _slug(text)
    t = re.sub(r"^sendo\s+", "", t)
    t = t.replace("(s)", "").replace("(", " ").replace(")", " ")
    t = t.replace(".", " ").replace(":", " ")
    return re.sub(r"\s+", " ", t).strip()


def _money(text):
    if text in (None, ""):
        return None
    if isinstance(text, (int, float)):
        return float(text) or None
    s = str(text).replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s) or None
    except ValueError:
        return None


def _int(value):
    if value in (None, ""):
        return None
    m = re.search(r"\d+", str(value).replace(".", ""))
    return int(m.group(0)) if m else None


def _titleise(slug: str) -> str | None:
    return slug.replace("_", " ").replace("-", " ").strip().title() or None


@register
class MicrosistecAAdapter(ListingSource):
    platform = "microsistec_a"
    supports_feed = False
    jsonld_required = False

    def __init__(self, session, source):
        super().__init__(session, source)
        self._urls: list[str] | None = None
        self._records: dict[str, dict] = {}
        self.unlabelled = 0

    # -- discovery ---------------------------------------------------------

    def _listing_urls(self) -> list[str]:
        if self._urls is not None:
            return self._urls
        urls, seen = [], set()
        for candidate in self.session.sitemap_candidates():
            for url in self.session.iter_sitemap(candidate):
                if url in seen:
                    continue
                if LISTING_URL.search(url) or LISTING_URL_NOID.search(url):
                    seen.add(url)
                    urls.append(url)
            if urls:
                break
        if not urls:
            raise AdapterError(f"{self.domain}: no listing URLs in the sitemap")
        log.info("%s: %d listing URLs in the sitemap", self.domain, len(urls))
        self._urls = urls
        return urls

    @staticmethod
    def url_hints(url: str) -> dict:
        """
        The URL grammar gives type, city and neighbourhood for free (§11.2).

        It does NOT give the transaction -- measured 0 of 400 (§13.3).
        """
        m = LISTING_URL.search(url)
        if not m:
            return {}
        return {
            "property_type": _titleise(m.group(2)),
            "city": _titleise(m.group(3)),
            "neighborhood": _titleise(m.group(4)),
        }

    @staticmethod
    def listing_id(url: str) -> str | None:
        m = LISTING_URL.search(url)
        if m:
            return m.group(1)
        m = LISTING_URL_NOID.search(url)
        return m.group(1)[:60] if m else None

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        """
        Fetch, read the transaction out of the price block, then yield.

        Eager by necessity -- see the module docstring. The page is fetched
        once; `fetch()` serves the cached extraction.
        """
        for url in self._listing_urls():
            record = self._records.get(url)
            if record is None:
                html = self.session.get_text(url)
                if html is None:
                    continue
                record = self._extract(html, url)
                self._records[url] = record

            found = record.get("_transaction")
            if found is None:
                self.unlabelled += 1
                continue
            if found != transaction:
                continue

            code = self.listing_id(url)
            yield DiscoveredURL(
                url=url,
                transaction_type=found,
                discovery_path="page:info-valores",
                listing_id=f"{code}-{found}" if code else None,
                hints=self.url_hints(url),
            )

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        record = self._records.get(discovered.url)
        if record is None:
            return None
        return Payload(discovered=discovered, text=json.dumps(record),
                       content_type="application/json")

    # -- extraction --------------------------------------------------------

    def _extract(self, html: str, url: str) -> dict:
        record: dict = {"link": url}

        block = INFO_VALORES.search(html)
        rows = VALOR_ROW.findall(block.group(0)) if block else []

        for label, value in rows:
            key = _label(label)
            if key in TRANSACTION_LABEL and record.get("_transaction") is None:
                # The first-item row: the transaction and the headline price.
                record["_transaction"] = TRANSACTION_LABEL[key]
                record["price"] = _money(value)
            elif key in MONEY_FIELD:
                record.setdefault(MONEY_FIELD[key], _money(value))
            elif key in DETAIL_FIELD:
                record.setdefault(DETAIL_FIELD[key], _int(value))

        # JSON-LD carries the price too, and agrees -- used only as a fallback
        # so a layout change in the price block does not silently lose it.
        if record.get("price") is None:
            blocks = extract_blocks(html)
            record["price"] = _money(find(blocks, "offers", "price"))

        # Detail rows outside the price block (Dormitório, Área, Vaga...),
        # truncated before the related-listings rail so this listing does not
        # inherit its neighbours' numbers.
        cut = BEFORE_RELATED.search(html)
        body = html[:cut.start()] if cut else html
        for label, value in VALOR_ROW.findall(body):
            field = DETAIL_FIELD.get(_label(label))
            if field and record.get(field) is None:
                record[field] = _int(value)

        record["amenities"] = self._amenities(html)

        # The description lives in the JSON-LD product node. When the agency
        # left it blank the site emits the string "False", not an empty field.
        blocks = extract_blocks(html)
        desc = find(blocks, "description", types=("IndividualProduct", "Product"))
        if isinstance(desc, str) and desc.strip().lower() not in ("", "false", "none"):
            record["description_clean"] = desc.strip()

        return record

    @staticmethod
    def _amenities(html: str) -> list[str]:
        """
        União de "Características Gerais" e "Cômodos", sem duplicar.

        Medido em 12 páginas: Características presente em 6/6, Cômodos em 4/12.
        O volume vai de 1 item (uma loja: "Monofásico") a 14 (casa de condomínio).
        """
        itens: list[str] = []
        # A página traz DUAS seções `tabs-caracteristicas`: a primeira é
        # "Detalhes do imóvel", uma <table> sem <li>, e a segunda é
        # "Características Gerais". Percorrer todas e colher apenas <li>
        # resolve sem depender do texto do cabeçalho, que varia com acento.
        for secao in (SECAO_CARACTERISTICAS, SECAO_COMODOS):
            for m in secao.finditer(html):
                for item in ITEM_LISTA.findall(m.group(1)):
                    item = re.sub(r"\s+", " ", item).strip()
                    if item and item not in itens:
                        itens.append(item)
        return itens

    def parse(self, payload: Payload) -> dict:
        record = json.loads(payload.text)
        # `_transaction` is discovery bookkeeping, not a schema field. The
        # allowlist would drop it anyway; dropping it here keeps the adapter
        # honest about what it emits.
        return {k: v for k, v in record.items() if not k.startswith("_")}


__all__ = ["MicrosistecAAdapter"]
