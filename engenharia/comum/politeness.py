"""
robots.txt compliance, per-host rate limiting, and backoff.

These sites are small businesses on shared hosting. Load a portal shrugs off
can degrade them. This module makes the polite path the only path.

REFACTOR-PLAN §5. Note that respecting robots.txt has been claimed in the
business plan since v0.1 and never implemented -- this is where that becomes
true.

P0 findings baked in:
  - 4 of 27 Santos sites disallow us at the root. They are SKIPPED, not
    worked around.
  - Sitemaps go stale: 6 of 6 sampled listings on one platform returned 410.
    404/410 are ordinary outcomes here, not errors.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

# Honest and identifying. We are not hiding; a site that does not want us
# can say so in robots.txt and we will comply.
USER_AGENT = (
    "AluguelCerto/0.1 (+https://aluguelcerto.com.br/bot; market research crawler)"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "pt-BR,pt;q=0.9",
}

MISSING_STATUSES = frozenset({404, 410})
BACKOFF_STATUSES = frozenset({429, 503, 502, 504})


class SourceBlocked(Exception):
    """The host asked us to stop. Drop the source for this run."""


# --------------------------------------------------------------------------

class RobotsCache:
    """One robots.txt fetch per host, cached for the process lifetime."""

    def __init__(self, session: requests.Session, timeout: int = 20):
        self._session = session
        self._timeout = timeout
        self._cache: dict[str, RobotFileParser] = {}
        self._lock = threading.Lock()

    def _parser_for(self, host_root: str) -> RobotFileParser:
        with self._lock:
            if host_root in self._cache:
                return self._cache[host_root]

        rp = RobotFileParser()
        url = f"{host_root}/robots.txt"
        try:
            r = self._session.get(url, timeout=self._timeout)
            ctype = r.headers.get("content-type", "").lower()
            if r.status_code == 200 and "html" not in ctype:
                rp.parse(r.text.splitlines())
            else:
                # Absent robots.txt means "no restrictions stated".
                rp.parse([])
        except Exception as exc:
            log.warning("robots.txt unreadable for %s (%s) -- assuming allowed",
                        host_root, type(exc).__name__)
            rp.parse([])

        with self._lock:
            self._cache[host_root] = rp
        return rp

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        return self._parser_for(root).can_fetch(USER_AGENT, url)

    def sitemaps(self, url: str) -> list[str]:
        parts = urlsplit(url)
        root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        return list(self._parser_for(root).site_maps() or [])

    def crawl_delay(self, url: str):
        parts = urlsplit(url)
        root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        try:
            return self._parser_for(root).crawl_delay(USER_AGENT)
        except Exception:
            return None


# --------------------------------------------------------------------------

@dataclass
class _HostState:
    last_request: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    failures: int = 0
    disabled: bool = False


class RateLimiter:
    """
    Minimum interval between requests, enforced per host.

    Concurrency across hosts is fine; concurrency WITHIN a host is not, which
    is why each host carries its own lock.
    """

    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._hosts: dict[str, _HostState] = {}
        self._lock = threading.Lock()

    def _state(self, host: str) -> _HostState:
        with self._lock:
            return self._hosts.setdefault(host, _HostState())

    def acquire(self, host: str, override_interval: float | None = None) -> None:
        st = self._state(host)
        interval = override_interval or self.min_interval
        st.lock.acquire()
        try:
            wait = interval - (time.monotonic() - st.last_request)
            if wait > 0:
                time.sleep(wait)
            st.last_request = time.monotonic()
        finally:
            st.lock.release()

    def note_failure(self, host: str) -> int:
        st = self._state(host)
        st.failures += 1
        return st.failures

    def note_success(self, host: str) -> None:
        self._state(host).failures = 0

    def disable(self, host: str) -> None:
        self._state(host).disabled = True

    def is_disabled(self, host: str) -> bool:
        return self._state(host).disabled


# --------------------------------------------------------------------------

class PoliteSession:
    """
    The only sanctioned way to make an outbound request.

    Enforces, in order: host not disabled -> robots.txt allows -> rate limit
    -> request -> backoff/disable on repeated failure.
    """

    def __init__(
        self,
        min_interval: float = 2.0,
        timeout: int = 25,
        max_failures: int = 4,
        respect_robots: bool = True,
    ):
        if not respect_robots:
            # Compliance must not be one config edit away from off.
            raise ValueError("respect_robots cannot be disabled")
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.timeout = timeout
        self.max_failures = max_failures
        self.robots = RobotsCache(self.session, timeout)
        self.limiter = RateLimiter(min_interval)
        self.skipped_by_robots: list[str] = []

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(url).netloc

    # -- public ------------------------------------------------------------

    def allowed(self, url: str) -> bool:
        if self.limiter.is_disabled(self._host(url)):
            return False
        return self.robots.can_fetch(url)

    def get(self, url: str, **kwargs):
        """
        Fetch politely.

        Returns a Response, or None when the resource is absent (404/410) or
        the host declined us. Never raises for ordinary web conditions --
        only SourceBlocked when a host has told us to stop.
        """
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        """
        Same contract as get(), for platforms whose search API is a POST.

        Some CRMs expose their catalogue only through a form-encoded POST
        (universal: `POST /retornar-imoveis-disponiveis`). The verb changes;
        robots, rate limiting and backoff do not.
        """
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs):
        """Every outbound request funnels through here."""
        host = self._host(url)

        if self.limiter.is_disabled(host):
            raise SourceBlocked(f"{host} disabled for this run")

        if not self.robots.can_fetch(url):
            log.info("robots.txt disallows %s -- skipping", url)
            self.skipped_by_robots.append(url)
            return None

        self.limiter.acquire(host, self.robots.crawl_delay(url))

        try:
            # Dispatch to session.get / session.post by name rather than
            # session.request(): those are the seams the P1 tests patch, and a
            # POST endpoint is not a reason to move them.
            verb = getattr(self.session, method.lower())
            resp = verb(url, timeout=self.timeout, **kwargs)
        except Exception as exc:
            n = self.limiter.note_failure(host)
            log.warning("request error %s (%s) [%d/%d]", url, type(exc).__name__,
                        n, self.max_failures)
            if n >= self.max_failures:
                self.limiter.disable(host)
                raise SourceBlocked(f"{host}: {n} consecutive failures") from exc
            return None

        if resp.status_code in MISSING_STATUSES:
            # Expected: sitemaps go stale (P0 -- 6/6 samples returned 410).
            self.limiter.note_success(host)
            return None

        if resp.status_code in BACKOFF_STATUSES:
            n = self.limiter.note_failure(host)
            delay = min(60, 2 ** n)
            log.warning("%s returned %d -- backing off %ds [%d/%d]",
                        host, resp.status_code, delay, n, self.max_failures)
            time.sleep(delay)
            if n >= self.max_failures:
                self.limiter.disable(host)
                raise SourceBlocked(f"{host} returned {resp.status_code} repeatedly")
            return None

        if resp.status_code >= 400:
            self.limiter.note_failure(host)
            return None

        self.limiter.note_success(host)
        return resp

    def close(self) -> None:
        self.session.close()
