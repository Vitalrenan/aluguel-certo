"""
The only HTTP client (REFACTOR-PLAN §7, §12.1).

Everything an adapter fetches goes through here, which is what makes §5
enforceable rather than aspirational: robots.txt, per-host rate limiting and
backoff live in `politeness.py`, and this module is the only thing that calls
them. An adapter that wants to make its own request has to go out of its way,
and code review will see it.

No hosted scraper sits in this path, ever (§12.1). No browser either -- P0
measured that the 16 usable Santos sites are plain server-rendered HTML.
`selenium` remains only for the legacy Zap adapter.

Deviation from §7 worth naming: the plan specified `httpx`. This is built on
`requests` instead, because `politeness.py` is already written and tested
against `requests` (62 passing tests), and the two features §7 actually wanted
from httpx -- connection reuse and conditional GET -- are both available on a
`requests.Session`. Swapping the client later means changing one module.

Conditional GET (§5) is cheaper for a small business on shared hosting than it
is for us, which is the point. The validator cache is injected rather than
owned, so a later phase can persist it across runs without touching this file.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from .politeness import PoliteSession, SourceBlocked  # noqa: F401  (re-export)

log = logging.getLogger(__name__)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# A sitemapindex can nest. Deep nesting means a generated/faceted sitemap
# rather than a listing set -- r3imoveis declares 50,000 URLs (P0 §6), which
# is implausible for one Santos agency.
MAX_SITEMAP_DEPTH = 3


@dataclass
class FetchStats:
    requests: int = 0
    ok: int = 0
    missing: int = 0          # 404/410 -- ordinary here (§11.1 item 5)
    not_modified: int = 0     # 304 -- conditional GET paid off
    declined: int = 0         # robots.txt said no
    failed: int = 0

    def report(self) -> str:
        return (f"requests={self.requests} ok={self.ok} missing={self.missing} "
                f"not_modified={self.not_modified} robots_declined={self.declined} "
                f"failed={self.failed}")


@dataclass
class Fetcher:
    """
    A polite HTTP client bound to one configured source.

    The rate limit comes from the source's own `rate_limit_rps`, floored by
    `config.MIN_INTERVAL_FLOOR`. Concurrency within a host is 1 by
    construction (each host carries its own lock in `politeness.RateLimiter`).
    """

    source: object
    session: PoliteSession = None
    validators: dict = field(default_factory=dict)   # url -> {etag, last_modified}
    stats: FetchStats = field(default_factory=FetchStats)

    def __post_init__(self):
        if self.session is None:
            self.session = PoliteSession(min_interval=self.source.min_interval)

    # -- core --------------------------------------------------------------

    def get(self, url: str, conditional: bool = True):
        """
        Fetch one URL politely.

        Returns a Response, or None when the resource is absent, unchanged
        since last time, or declined by robots.txt. Raises SourceBlocked only
        when the host has told us to stop, which drops the source for the run.
        """
        self.stats.requests += 1

        headers = {}
        if conditional:
            cached = self.validators.get(url)
            if cached:
                if cached.get("etag"):
                    headers["If-None-Match"] = cached["etag"]
                if cached.get("last_modified"):
                    headers["If-Modified-Since"] = cached["last_modified"]

        before = len(self.session.skipped_by_robots)
        resp = self.session.get(url, headers=headers or None)

        if resp is None:
            if len(self.session.skipped_by_robots) > before:
                self.stats.declined += 1
            else:
                self.stats.missing += 1
            return None

        if resp.status_code == 304:
            self.stats.not_modified += 1
            return None

        etag = resp.headers.get("ETag")
        last_modified = resp.headers.get("Last-Modified")
        if etag or last_modified:
            self.validators[url] = {"etag": etag, "last_modified": last_modified}

        self.stats.ok += 1
        return resp

    def post(self, url: str, data=None, **kwargs):
        """
        POST through the politeness layer.

        No conditional-GET bookkeeping: a POST search query is not a cacheable
        resource, and these endpoints send `no-store` anyway.
        """
        self.stats.requests += 1
        before = len(self.session.skipped_by_robots)
        resp = self.session.post(url, data=data, **kwargs)
        if resp is None:
            if len(self.session.skipped_by_robots) > before:
                self.stats.declined += 1
            else:
                self.stats.missing += 1
            return None
        self.stats.ok += 1
        return resp

    def prime(self, path: str = "/"):
        """
        Warm the session on a normal page before calling an API.

        Some PHP back ends bind their search endpoint to a session cookie and
        answer an unprimed POST with an error page. One ordinary GET, of a page
        we are allowed to fetch, is the cheapest way to look like a browser
        without pretending to be one.

        Returns the response so a caller can read the URL it actually landed
        on -- some of these hosts redirect the bare domain to `www.`, and a
        redirect turns a POST into a GET.
        """
        return self.get(self.source.base_url.rstrip("/") + path, conditional=False)

    def get_text(self, url: str, conditional: bool = True) -> str | None:
        resp = self.get(url, conditional=conditional)
        if resp is None:
            return None
        # Servers on shared hosting frequently mislabel charset; the sites in
        # scope are pt-BR and the accented text matters for neighbourhood
        # names, so let requests sniff rather than trusting a bad header.
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text

    def allowed(self, url: str) -> bool:
        return self.session.allowed(url)

    # -- discovery helpers (§3.3 tier 2) -----------------------------------

    def sitemap_candidates(self) -> list[str]:
        """
        Sitemaps robots.txt declares, plus the conventional location.

        robots.txt first: a site that bothers to declare its sitemap usually
        maintains it. `/sitemap.xml` is the fallback guess.
        """
        base = self.source.base_url
        declared = self.session.robots.sitemaps(base)
        candidates = list(declared)
        default = urljoin(base + "/", "sitemap.xml")
        if default not in candidates:
            candidates.append(default)
        return candidates

    def iter_sitemap(self, url: str, depth: int = 0) -> Iterator[str]:
        """
        Yield every <loc> a sitemap contains, following sitemapindex nesting.

        A sitemap that 404s or fails to parse yields nothing rather than
        raising -- Universal Software's sitemap is stale enough that 0 of 6
        sampled listings were live (P0 §4.2), so a dead sitemap is a known
        platform characteristic, not an error.
        """
        if depth > MAX_SITEMAP_DEPTH:
            log.warning("sitemap nesting deeper than %d at %s -- stopping",
                        MAX_SITEMAP_DEPTH, url)
            return

        text = self.get_text(url)
        if not text:
            return

        try:
            root = ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as exc:
            log.warning("sitemap %s is not parseable XML (%s)", url, exc)
            return

        if root.tag.endswith("sitemapindex"):
            for loc in root.iter(f"{_SITEMAP_NS}loc"):
                if loc.text:
                    yield from self.iter_sitemap(loc.text.strip(), depth + 1)
            return

        for entry in root.iter(f"{_SITEMAP_NS}url"):
            loc = entry.find(f"{_SITEMAP_NS}loc")
            if loc is not None and loc.text:
                yield loc.text.strip()

    def iter_all_sitemap_urls(self) -> Iterator[str]:
        """Every URL from every sitemap this source declares, de-duplicated."""
        seen: set[str] = set()
        for candidate in self.sitemap_candidates():
            for url in self.iter_sitemap(candidate):
                if url not in seen:
                    seen.add(url)
                    yield url

    # -- lifecycle ---------------------------------------------------------

    @property
    def host(self) -> str:
        return urlsplit(self.source.base_url).netloc

    def close(self) -> None:
        self.session.close()
