"""
The adapter contract (REFACTOR-PLAN §3.2, revised by §13.3).

One implementation per site PLATFORM, not per agency. Four Santos domains
share two platforms; the same vendors recur in other cities, which is the
whole bet behind this architecture.

Adapters are deliberately allowed to be sloppy. `parse()` may return any keys
it likes -- the Normalizer is the single chokepoint that enforces §2. One
place to audit, one place to test.

The one thing an adapter may NOT be sloppy about is `transaction_type`. §13.3
measured that neither Microsistec URL grammar encodes venda/locacao, and
Group B's JSON-LD `Offer` carries no `businessFunction` or `availability`.
For these platforms the discovery path is the only place the fact exists, so
`discover()` yields a DiscoveredURL that carries its own provenance rather
than a bare URL string. `transaction` stops being an input filter the adapter
may quietly ignore and becomes an output it must attach.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

TRANSACTIONS = ("venda", "locacao")


class AdapterError(Exception):
    """The adapter cannot proceed with this source. Not a per-listing failure."""


@dataclass(frozen=True)
class DiscoveredURL:
    """
    A listing URL plus the facts that were true of the path that found it.

    `transaction_type` is REQUIRED and may not be None: a listing whose
    transaction is unknown is worthless for the rent-to-value ratio this
    pivot exists to measure (§4.1), so it must be established at discovery
    time or the URL must not be yielded.

    `hints` carries anything the URL grammar gives up for free -- Group A
    yields property_id, property_type, city and neighborhood from the URL
    alone (§11.2), before a single byte of the page is fetched. Hints are
    merged UNDER parsed values: the page wins where the two disagree.
    """
    url: str
    transaction_type: str
    discovery_path: str
    listing_id: str | None = None
    hints: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.transaction_type not in TRANSACTIONS:
            raise AdapterError(
                f"discover() yielded transaction_type={self.transaction_type!r} "
                f"for {self.url}; must be one of {TRANSACTIONS}. See plan §13.3."
            )

    @property
    def domain(self) -> str:
        return urlsplit(self.url).netloc.lower().removeprefix("www.")


@dataclass(frozen=True)
class Payload:
    """What the fetch layer hands to `parse()`. Never persisted (§2.3)."""
    discovered: DiscoveredURL
    text: str
    status: int = 200
    content_type: str = "text/html"

    @property
    def url(self) -> str:
        return self.discovered.url


class ListingSource(ABC):
    """One implementation per site platform."""

    platform: str = ""
    supports_feed: bool = False

    #: Set True where JSON-LD is the only reliable source of a required field.
    #: Group B: price is in JSON-LD on 4/4 pages, in visible text on 3/6
    #: (§13.2). A DOM-first Group B adapter silently loses half its prices.
    jsonld_required: bool = False

    def __init__(self, session, source, target=None):
        self.session = session   # comum.fetch.Fetcher
        self.source = source     # comum.config.Source
        # O ALVO manda na cidade, nao a fonte. A particao do lake ja vem do
        # target (`writer.write(..., target.state, target.city, ...)`), entao um
        # adapter que montasse a URL pela cidade da FONTE gravaria imovel de uma
        # cidade na particao de outra -- sem erro nenhum.
        self.target = target

    @property
    def cidade(self) -> str | None:
        return (getattr(self.target, "city", None)
                or getattr(self.source, "city", None))

    @property
    def uf(self) -> str | None:
        return (getattr(self.target, "state", None)
                or getattr(self.source, "state", None))

    @property
    def domain(self) -> str:
        return self.source.domain

    @abstractmethod
    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        """
        Yield listing URLs labelled with the transaction they were found under.

        Implementations follow the §3.3 resolution order -- feed, then
        sitemap, then category crawl -- and must not yield a URL whose
        transaction they cannot establish.
        """

    @abstractmethod
    def parse(self, payload: Payload) -> dict:
        """
        Return a raw dict of whatever was found. The Normalizer filters it.

        Must not follow broker links, open contact modals, or read
        `RealEstateAgent` / `Person` JSON-LD blocks (§2.3).
        """

    # -- optional hooks ----------------------------------------------------

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        """
        Default fetch. Override only when a platform needs something special.

        Returns None for an absent listing -- 404/410 are ordinary outcomes
        here, not errors (§11.1 item 5).
        """
        resp = self.session.get(discovered.url)
        if resp is None:
            return None
        return Payload(
            discovered=discovered,
            text=resp.text,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} platform={self.platform} domain={self.domain}>"
