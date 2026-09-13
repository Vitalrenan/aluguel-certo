"""
Group B adapter -- the /public/search JSON API (REFACTOR-PLAN §14).

Covers taguaimoveis.com.br and cferreiraimoveis.com.br. P0 grouped these with
Group A as one "Microsistec" platform; §13.1 split them on URL grammar, and
§14 found that the split runs deeper than markup: Group B exposes a paginated
JSON API that returns the entire catalogue, transaction flags included.

    GET https://{domain}/public/search?per_page=100&page={n}

What that buys, measured in §14.1: the whole of cferreira -- 1,093 listings --
in 11 requests, with ETag and Last-Modified for conditional re-fetch. The
plan budgeted 1,309 page fetches plus ~19 client-rendered search pages behind
a local Playwright. None of that is needed.

Three things about this adapter are unusual and deliberate.

**1. It never fetches a listing page.** The search response carries every
field we persist, so `fetch()` serves from the catalogue already in hand
rather than making 1,093 more requests. That is the politeness rule (§5) and
the never-fetch rule (§2.3) pulling in the same direction for once.

**2. It strips personal data at ingestion, not at parse.** §14.4: every record
carries `receiver1 = {creci, email, name, phones}` and a `contact_message`,
because the API bundles broker contact with property data in one response. We
cannot decline to receive it. We can decline to keep it for even one step
longer than the transport requires -- so `_project()` runs the moment a page
is decoded, and nothing downstream of it has ever seen the personal fields.

**3. A listing flagged for sale AND rent becomes two records.** §14.2 found
12.9% of cferreira listings carry both `sale_price` and `rent_price`. That is
a within-property rent-to-value ratio, which is the measurement this whole
pivot exists to obtain. `transaction_type` stays single-valued (§4.1), so the
property is emitted once per transaction, each row carrying only the price
that belongs to it.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from comum import schema

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

PER_PAGE = 100
MAX_PAGES = 100          # 10,000 listings; a Santos agency has ~1,000

# API fields we map. Everything else -- ~130 fields including receiver1,
# contact_message, familiar_income, bank_balance, ri and cib -- is dropped at
# ingestion and never reaches the rest of the process (§14.4).
#
# This is an allowlist for the same reason §2.2 is: the platform's data model
# already contains owner financial fields that these two agencies happen to
# leave null. Another agency on the same platform may not.
API_FIELDS = frozenset({
    "code", "details_url",
    "for_sale", "for_rent",
    "sale_price", "rent_price", "iptu_price", "condominium_price",
    "area_useful", "area_total",
    "dorms_count", "bathroom_count", "suit_count", "parking_lot_count",
    "floor",
    "type_text", "city_name", "neighborhood_name", "uf",
    "features",
    "obs",
})

# Named so the exclusion is greppable and testable, not merely implied by
# absence from API_FIELDS above.
NEVER_MAP = frozenset({
    "receiver1", "receiver2", "receiver1_id", "receiver2_id", "contact_message",
    "familiar_income", "bank_balance", "ri", "cib", "evaluation_price",
    # Deferred by §14.5 as a policy decision, not an extraction detail:
    # coordinates and street-level landmarks are a precision jump over the
    # neighbourhood granularity §11.2 assumed.
    "latitude", "longitude", "reference_point",
})


def _money(value):
    """
    API money -> float, or None.

    Two traps here. The API emits decimals as en-US strings ('1650.00'), so
    `schema.clean_currency` must NOT see them: it reads '.' as a pt-BR
    thousands separator and would return 165000.0. Native floats pass through
    it untouched, so the conversion happens here.

    And zero means "not informed", not "free". A property with R$ 0,00 of IPTU
    does not exist; the field is simply empty. Persisting 0 would be
    fabricating an observation -- the same error `clean_int` was written to
    avoid (SDD D-04).
    """
    if value in (None, "", "0", "0.00", 0):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out or None


def _count(value):
    """
    API count -> int, or None.

    Unlike money, zero is a real answer here: a flat with no parking space is
    a flat with no parking space. Only a missing field becomes None.
    """
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _amenities(features):
    """`features` is a list of dicts; only the name is a property attribute."""
    if not isinstance(features, list):
        return []
    return [f["name"] for f in features
            if isinstance(f, dict) and f.get("name")]


@register
class MicrosistecBAdapter(ListingSource):
    platform = "microsistec_b"
    supports_feed = False        # better than a feed: a paginated JSON API
    jsonld_required = False      # no HTML is parsed at all

    def __init__(self, session, source):
        super().__init__(session, source)
        self._catalogue: list[dict] | None = None

    # -- ingestion ---------------------------------------------------------

    @property
    def search_url(self) -> str:
        return f"{self.source.base_url}/public/search"

    def _project(self, record: dict) -> dict:
        """
        Keep the mapped API fields, drop everything else -- immediately.

        Called once per record as its page is decoded, so the personal fields
        exist only inside the response object, which is released as soon as
        this returns. See the §2.3 bundled-payload addendum in §14.4.
        """
        return {k: v for k, v in record.items() if k in API_FIELDS}

    def _load_catalogue(self) -> list[dict]:
        """
        Page through the API once and cache the projected result.

        Cached because `collect.py` calls `discover()` once per transaction,
        and re-fetching 1,093 listings to answer the same question twice would
        double the load on a small business for no new information.
        """
        if self._catalogue is not None:
            return self._catalogue

        records: list[dict] = []
        page, total = 1, None

        while page <= MAX_PAGES:
            url = f"{self.search_url}?per_page={PER_PAGE}&page={page}"
            resp = self.session.get(url)
            if resp is None:
                break

            try:
                body = resp.json()
            except ValueError as exc:
                raise AdapterError(f"{self.domain}: /public/search is not JSON") from exc

            results = body.get("results") or []
            if not results:
                break

            records.extend(self._project(r) for r in results)

            total = (body.get("pagination") or {}).get("totalItems")
            if total is not None and len(records) >= int(total):
                break
            page += 1

        if not records:
            raise AdapterError(f"{self.domain}: /public/search returned no listings")

        log.info("%s: %d listings in %d request(s)", self.domain, len(records), page)
        self._catalogue = records
        return records

    # -- the contract ------------------------------------------------------

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        """
        Yield the listings flagged for this transaction.

        A listing flagged for both appears in both passes, which is how §14.2's
        two-records resolution is reached without the collector needing to know
        anything about it.
        """
        flag = {"venda": "for_sale", "locacao": "for_rent"}.get(transaction)
        if flag is None:
            raise AdapterError(f"unsupported transaction {transaction!r}")

        for record in self._load_catalogue():
            if not record.get(flag):
                continue

            url = record.get("details_url")
            code = record.get("code")
            if not url or not code:
                continue

            yield DiscoveredURL(
                url=url,
                transaction_type=transaction,
                discovery_path=f"api:/public/search?{flag}",
                # Scoped by transaction on purpose: the sale row and the rent
                # row are two observations of one property and must not share
                # a property_id, or the P5 MERGE on (property_id,
                # extraction_date) would treat one as an update of the other.
                # They re-associate downstream via property_cluster_id (§4.2),
                # where every clustering attribute matches exactly.
                listing_id=f"{code}-{transaction}",
                hints={"property_type": record.get("type_text")},
            )

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        """
        Serve from the catalogue. No second request per listing.

        The record is already projected, so what travels in the Payload has
        never contained personal data.
        """
        code = str(discovered.listing_id).rsplit("-", 1)[0]
        for record in self._load_catalogue():
            if record.get("code") == code:
                return Payload(discovered=discovered,
                               text=json.dumps(record),
                               content_type="application/json")
        return None

    def parse(self, payload: Payload) -> dict:
        """
        Map API names onto schema names, choosing the price for this row.

        An explicit mapping, never a passthrough (§14.4). A passthrough would
        not leak -- the allowlist keeps 0 of 151 API fields, since API names
        are not schema names -- but it would silently produce empty records.
        """
        record = json.loads(payload.text)
        transaction = payload.discovered.transaction_type

        price = record.get("sale_price") if transaction == "venda" else record.get("rent_price")

        return {
            "link": record.get("details_url"),
            "property_type": record.get("type_text"),
            "city": record.get("city_name"),
            "state": record.get("uf"),
            "neighborhood": record.get("neighborhood_name"),

            "price": _money(price),
            "condo_fee": _money(record.get("condominium_price")),
            "iptu_tax": _money(record.get("iptu_price")),

            "area_m2": _count(record.get("area_useful")) or None,
            "area_total_m2": _count(record.get("area_total")) or None,
            "bedrooms": _count(record.get("dorms_count")),
            "bathrooms": _count(record.get("bathroom_count")),
            "suites": _count(record.get("suit_count")),
            "parking_spots": _count(record.get("parking_lot_count")),
            "floor_level": _count(record.get("floor")),

            "amenities": _amenities(record.get("features")),
            "description_clean": record.get("obs"),
        }


__all__ = ["MicrosistecBAdapter", "API_FIELDS", "NEVER_MAP"]
