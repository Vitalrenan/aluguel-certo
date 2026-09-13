"""
Veploy adapter -- serlamimobiliaria.com.br.

The largest single remaining source in Santos: 1,455 listing URLs, all sampled
live. No API (checked); the data is on the page, but in the friendliest form a
page can offer -- JSON-LD with one `Offer` block per transaction:

    Offer: {price: 430000, businessFunction: ...goodrelations/v1#Sell}
    Offer: {price: 3000,   businessFunction: ...goodrelations/v1#LeaseOut}

**The URL slug looked like the cheap way to label the transaction and is not.**
Measured over all 1,455 URLs: 1,218 carry `-a-venda-`, 176 carry an aluguel
token, **61 carry neither**, and **91 carry both** ("...venda-por-r-43000000-ou-
aluguel-por-r-411200mes..."). Slug-derived labelling would have dropped 4% of
the catalogue and mislabelled another 6%, silently. `businessFunction` is
present, unambiguous, and one block per transaction -- so it wins, and the
slug is not consulted at all.

That has the same consequence as on `microsistec_a`: the transaction is
knowable only after the page is fetched, while `discover()` must label a URL
before yielding it. So `discover()` fetches, extracts, caches the small parsed
record, and yields one labelled URL per transaction the listing offers. A
property advertised both ways becomes two rows, per §14.2.

**The binding constraint is politeness.** `robots.txt` declares `crawl-delay: 5`
and `politeness.py` honours it, so a full pass is 1,455 x 5 s ~= 2 hours. That
is a scheduling fact, not a defect. Use `--limit` when developing.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from comum.jsonld import extract_blocks, find, find_raw, first_of_type

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

# /imovel/{CODE}/{slug}
LISTING_URL = re.compile(r"/imovel/([A-Z]{2}\d{3,6})/([a-z0-9-]+)$", re.I)

# goodrelations vocabulary on the Offer block.
BUSINESS_FUNCTION = {"sell": "venda", "leaseout": "locacao"}

# "Apartamento com 2 dormitórios, 51 m² - venda por R$ 430.000,00 ou aluguel
#  por R$ 4.112,00/mês - Centro - São Vicente/SP"
NAME_AREA = re.compile(r"(\d[\d.,]*)\s*m[²2]", re.I)
NAME_TAIL = re.compile(r"-\s*([^-]+?)\s*-\s*([^-/]+?)\s*/\s*([A-Z]{2})\s*$")

# <span><strong>Condomínio</strong> R$ 450,00</span>
LABELLED_MONEY = re.compile(
    r"<strong>\s*(condom[íi]nio|iptu)\s*</strong>\s*(R\$\s?[\d.]+(?:,\d{2})?)", re.I)

# <span class="...uppercase">Dormitórios</span><span class="font-bold">4</span>
STAT = re.compile(
    r'<span[^>]*uppercase[^>]*>\s*([^<]{3,20}?)\s*</span>\s*'
    r'<span[^>]*font-bold[^>]*>\s*([^<]{1,20}?)\s*</span>', re.I)

STAT_FIELD = {
    "dormitorios": "bedrooms", "dormitorio": "bedrooms", "quartos": "bedrooms",
    "banheiros": "bathrooms", "banheiro": "bathrooms",
    "suites": "suites", "suite": "suites",
    "vagas": "parking_spots", "vaga": "parking_spots",
}


def _slug(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def _money(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    s = str(value).replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s) or None
    except ValueError:
        return None


def _int(value):
    if value in (None, ""):
        return None
    m = re.search(r"\d+", str(value).replace(".", ""))
    return int(m.group(0)) if m else None


def offers_by_transaction(blocks) -> dict[str, float]:
    """
    {transaction: price} from every Offer that declares a businessFunction.

    A listing advertised for sale and rent emits two blocks; one advertised
    only for sale emits one. An Offer without a businessFunction is ignored --
    an unlabelled price is not usable, and guessing from magnitude is exactly
    the kind of inference this pipeline does not make.
    """
    out: dict[str, float] = {}
    for block in blocks:
        types = block.get("@type") or block.get("type") or []
        types = [types] if isinstance(types, str) else types
        if not any(str(t).lower() == "offer" for t in types):
            continue
        bf = str(block.get("businessFunction") or "").rsplit("#", 1)[-1].lower()
        transaction = BUSINESS_FUNCTION.get(bf)
        price = _money(block.get("price"))
        if transaction and price and transaction not in out:
            out[transaction] = price
    return out


@register
class VeployAdapter(ListingSource):
    platform = "veploy"
    supports_feed = False
    jsonld_required = True      # businessFunction exists nowhere else

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
                if LISTING_URL.search(url) and url not in seen:
                    seen.add(url)
                    urls.append(url)
            if urls:
                break
        if not urls:
            raise AdapterError(f"{self.domain}: no listing URLs in the sitemap")
        log.info("%s: %d listing URLs in the sitemap", self.domain, len(urls))
        self._urls = urls
        return urls

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        for url in self._listing_urls():
            record = self._records.get(url)
            if record is None:
                html = self.session.get_text(url)
                if html is None:
                    continue
                record = self._extract(html, url)
                self._records[url] = record

            prices = record.get("_prices") or {}
            if not prices:
                self.unlabelled += 1
                continue
            if transaction not in prices:
                continue

            m = LISTING_URL.search(url)
            yield DiscoveredURL(
                url=url,
                transaction_type=transaction,
                discovery_path="page:jsonld-businessFunction",
                listing_id=f"{m.group(1)}-{transaction}" if m else None,
                hints={},
            )

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        record = self._records.get(discovered.url)
        if record is None:
            return None
        return Payload(discovered=discovered, text=json.dumps(record),
                       content_type="application/json")

    # -- extraction --------------------------------------------------------

    def _extract(self, html: str, url: str) -> dict:
        blocks = extract_blocks(html)
        record: dict = {"link": url, "_prices": offers_by_transaction(blocks)}

        # O site emite MAIS DE UM nó de imóvel: um com @type
        # ["RealEstateListing","Apartment"] (que traz description e datePosted)
        # e outro só "Apartment" (que traz amenityFeature). Nenhum dos dois tem
        # tudo, então cada campo é buscado no primeiro nó que o possua, em vez
        # de eleger um nó e ler tudo dele.
        TIPOS = ("Apartment", "House", "Residence", "SingleFamilyResidence",
                 "Accommodation", "RealEstateListing")
        name = find(blocks, "name", types=TIPOS) or ""

        record["bedrooms"] = _int(find(blocks, "numberOfBedrooms", types=TIPOS))
        record["bathrooms"] = _int(find(blocks, "numberOfBathroomsTotal", types=TIPOS))
        record["parking_spots"] = _int(find(blocks, "numberOfParkingSpaces", types=TIPOS))

        floor_size = find_raw(blocks, "floorSize", types=TIPOS)
        if isinstance(floor_size, dict):
            record["area_total_m2"] = _int(floor_size.get("value"))
        else:
            record["area_total_m2"] = _int(find(blocks, "value", types=("QuantitativeValue",)))

        # The advertised (useful) area is in the title; floorSize is the larger
        # total. Measured: 45 vs 60, 150 vs 170, 51 vs -.
        area = NAME_AREA.search(name)
        if area:
            record["area_m2"] = _int(area.group(1))

        # "... - Centro - São Vicente/SP". Note the city and state are joined
        # by a slash, not a dash -- and the site's own PostalAddress puts the
        # NEIGHBOURHOOD in addressLocality and "Santos - SP" in addressRegion,
        # so the title is the more reliable source.
        tail = NAME_TAIL.search(name)
        if tail:
            record["neighborhood"], record["city"], record["state"] = (
                tail.group(1), tail.group(2), tail.group(3))
        else:
            record["neighborhood"] = find(blocks, "addressLocality",
                                          types=("PostalAddress",))
            region = find(blocks, "addressRegion", types=("PostalAddress",)) or ""
            parts = [p.strip() for p in str(region).split("-")]
            if len(parts) == 2:
                record["city"], record["state"] = parts

        if name:
            record["property_type"] = (name.split(" com ")[0].split(" à ")[0]
                                       .split(",")[0].strip() or None)

        amenities = find_raw(blocks, "amenityFeature", types=TIPOS)
        if isinstance(amenities, dict):
            amenities = [amenities]
        if isinstance(amenities, list):
            record["amenities"] = [a.get("name") for a in amenities
                                   if isinstance(a, dict) and a.get("name")]

        # A descrição e a data de publicação vivem num nó cujo @type é
        # ["RealEstateListing", "Apartment"]. Esse nó era descartado até
        # 2026-08-28 porque `realestatelisting` estava, por engano, na lista de
        # tipos proibidos de `jsonld.py` -- confundido com `RealEstateAgent`.
        desc = find(blocks, "description", types=("RealEstateListing",))
        if isinstance(desc, str) and desc.strip():
            record["description_clean"] = desc.strip()

        # `datePosted` é quando o ANÚNCIO foi publicado, não quando o vimos.
        # É melhor que a data da corrida para medir tempo de anúncio.
        posted = find(blocks, "datePosted", types=("RealEstateListing",))
        if isinstance(posted, str) and len(posted) >= 10:
            record["listing_first_seen"] = posted[:10]

        for label, value in LABELLED_MONEY.findall(html):
            key = "condo_fee" if _slug(label).startswith("condom") else "iptu_tax"
            record.setdefault(key, _money(value))

        for label, value in STAT.findall(html):
            field = STAT_FIELD.get(_slug(label))
            if field and record.get(field) is None:
                record[field] = _int(value)

        return record

    def parse(self, payload: Payload) -> dict:
        record = json.loads(payload.text)
        prices = record.get("_prices") or {}
        out = {k: v for k, v in record.items() if not k.startswith("_")}
        out["price"] = prices.get(payload.discovered.transaction_type)
        return out


__all__ = ["VeployAdapter", "offers_by_transaction", "BUSINESS_FUNCTION"]
