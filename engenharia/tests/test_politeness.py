"""
Tests for utils/politeness.py. No network: robots.txt bodies and HTTP
responses are stubbed so the suite runs offline and in CI.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comum import politeness  # noqa: E402
from comum.politeness import (  # noqa: E402
    PoliteSession, RateLimiter, SourceBlocked, USER_AGENT,
)


class _Resp:
    def __init__(self, status=200, text="", ctype="text/html"):
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}


def _session_with(robots_body, responses):
    """Build a PoliteSession whose HTTP layer is stubbed."""
    s = PoliteSession(min_interval=0.0)

    def fake_get(url, **kw):
        if url.endswith("/robots.txt"):
            return _Resp(200, robots_body, "text/plain")
        return responses.pop(0) if responses else _Resp(200, "ok")

    s.session.get = mock.Mock(side_effect=fake_get)
    return s


class TestRobotsCompliance(unittest.TestCase):

    ALLOW_ALL = "User-agent: *\nAllow: /\n"
    DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
    DISALLOW_PATH = "User-agent: *\nDisallow: /admin\n"

    def test_disallowed_root_returns_none_and_is_recorded(self):
        s = _session_with(self.DISALLOW_ALL, [])
        self.assertIsNone(s.get("https://exemplo.com.br/imovel/1.html"))
        self.assertEqual(1, len(s.skipped_by_robots))

    def test_allowed_site_is_fetched(self):
        s = _session_with(self.ALLOW_ALL, [_Resp(200, "<html>listing</html>")])
        r = s.get("https://exemplo.com.br/imovel/1.html")
        self.assertIsNotNone(r)
        self.assertIn("listing", r.text)

    def test_partial_disallow_respected(self):
        s = _session_with(self.DISALLOW_PATH, [_Resp(200, "ok")])
        self.assertIsNone(s.get("https://exemplo.com.br/admin/panel"))
        self.assertIsNotNone(s.get("https://exemplo.com.br/imovel/1.html"))

    def test_absent_robots_means_allowed(self):
        s = PoliteSession(min_interval=0.0)
        s.session.get = mock.Mock(side_effect=lambda url, **kw: (
            _Resp(404, "", "text/html") if url.endswith("robots.txt") else _Resp(200, "ok")
        ))
        self.assertIsNotNone(s.get("https://exemplo.com.br/imovel/1.html"))

    def test_robots_cannot_be_switched_off(self):
        with self.assertRaises(ValueError):
            PoliteSession(respect_robots=False)

    def test_user_agent_is_identifying(self):
        self.assertIn("AluguelCerto", USER_AGENT)
        self.assertIn("http", USER_AGENT)


class TestMissingResources(unittest.TestCase):
    """P0: sitemaps go stale -- 6/6 sampled listings returned 410."""

    ALLOW = "User-agent: *\nAllow: /\n"

    def test_404_and_410_are_normal(self):
        for code in (404, 410):
            with self.subTest(code=code):
                s = _session_with(self.ALLOW, [_Resp(code)])
                self.assertIsNone(s.get("https://exemplo.com.br/imovel/gone.html"))

    def test_410_does_not_count_as_failure(self):
        s = _session_with(self.ALLOW, [_Resp(410)] * 10)
        for _ in range(10):
            s.get("https://exemplo.com.br/imovel/gone.html")
        self.assertFalse(s.limiter.is_disabled("exemplo.com.br"))


class TestBackoff(unittest.TestCase):

    ALLOW = "User-agent: *\nAllow: /\n"

    def test_repeated_429_disables_host(self):
        s = _session_with(self.ALLOW, [_Resp(429)] * 6)
        with mock.patch.object(politeness.time, "sleep"):
            with self.assertRaises(SourceBlocked):
                for _ in range(6):
                    s.get("https://exemplo.com.br/imovel/1.html")
        self.assertTrue(s.limiter.is_disabled("exemplo.com.br"))

    def test_disabled_host_refuses_further_requests(self):
        s = _session_with(self.ALLOW, [])
        s.limiter.disable("exemplo.com.br")
        with self.assertRaises(SourceBlocked):
            s.get("https://exemplo.com.br/imovel/1.html")


class TestRateLimiter(unittest.TestCase):

    def test_enforces_minimum_interval(self):
        rl = RateLimiter(min_interval=0.25)
        start = time.monotonic()
        rl.acquire("a.com")
        rl.acquire("a.com")
        self.assertGreaterEqual(time.monotonic() - start, 0.2)

    def test_different_hosts_do_not_block_each_other(self):
        rl = RateLimiter(min_interval=0.25)
        start = time.monotonic()
        rl.acquire("a.com")
        rl.acquire("b.com")
        self.assertLess(time.monotonic() - start, 0.2)

    def test_failure_counter(self):
        rl = RateLimiter()
        self.assertEqual(1, rl.note_failure("a.com"))
        self.assertEqual(2, rl.note_failure("a.com"))
        rl.note_success("a.com")
        self.assertEqual(1, rl.note_failure("a.com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
