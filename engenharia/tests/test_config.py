"""
Config v2 (plan section 6).

The interesting tests here are the ones that assert a config CANNOT do
something: turn off robots.txt, turn off the PII gate, or declare a rate limit
faster than the floor. Compliance that a YAML edit can disable is not
compliance.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comum.config import (  # noqa: E402
    ConfigError, MIN_INTERVAL_FLOOR, load_config, parse_config,
)

MINIMAL = {
    "version": 2,
    "sources": [{"domain": "exemplo.com.br", "platform": "microsistec_a"}],
    "targets": [{"state": "SP", "city": "Santos"}],
}


def cfg(**overrides):
    import copy
    data = copy.deepcopy(MINIMAL)
    data.update(overrides)
    return data


class TestVersioning(unittest.TestCase):
    def test_v1_config_is_rejected(self):
        """The v1 shape read sites[0] and ignored the rest (SDD D-17)."""
        with self.assertRaises(ConfigError):
            parse_config({"scraping_params": {"sites": ["zap"]}})

    def test_wrong_version_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(version=1))


class TestComplianceFlags(unittest.TestCase):
    def test_respect_robots_cannot_be_false(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_config(cfg(scraping_params={"respect_robots": False}))
        self.assertIn("respect_robots", str(ctx.exception))

    def test_fail_on_pii_cannot_be_false(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(execution={"fail_on_pii": False}))

    def test_flags_default_to_on_when_absent(self):
        c = parse_config(cfg())
        self.assertTrue(c.respect_robots)
        self.assertTrue(c.fail_on_pii)

    def test_truthy_is_not_enough(self):
        """'yes'/1 must not pass for True -- the check is identity, not truth."""
        with self.assertRaises(ConfigError):
            parse_config(cfg(execution={"fail_on_pii": 1}))


class TestSources(unittest.TestCase):
    def test_duplicate_domain_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(sources=[
                {"domain": "a.com.br", "platform": "x"},
                {"domain": "a.com.br", "platform": "y"},
            ]))

    def test_platform_required(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(sources=[{"domain": "a.com.br"}]))

    def test_unknown_transaction_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(sources=[
                {"domain": "a.com.br", "platform": "x", "transactions": ["temporada"]},
            ]))

    def test_no_sources_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(sources=[]))

    def test_no_targets_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config(cfg(targets=[]))

    def test_domain_and_target_are_normalised(self):
        c = parse_config(cfg(sources=[{"domain": " Exemplo.COM.br ", "platform": " Vista "}]))
        self.assertEqual(c.sources[0].domain, "exemplo.com.br")
        self.assertEqual(c.sources[0].platform, "vista")
        self.assertEqual(c.targets[0].city, "santos")
        self.assertEqual(c.targets[0].state, "sp")


class TestRateLimit(unittest.TestCase):
    def test_rps_becomes_interval(self):
        c = parse_config(cfg(sources=[
            {"domain": "a.com.br", "platform": "x", "rate_limit_rps": 0.2}]))
        self.assertAlmostEqual(c.sources[0].min_interval, 5.0)

    def test_interval_has_a_floor(self):
        """A config asking for 50 rps against a shared host does not get it."""
        c = parse_config(cfg(sources=[
            {"domain": "a.com.br", "platform": "x", "rate_limit_rps": 50}]))
        self.assertEqual(c.sources[0].min_interval, MIN_INTERVAL_FLOOR)

    def test_zero_rps_does_not_divide_by_zero(self):
        c = parse_config(cfg(sources=[
            {"domain": "a.com.br", "platform": "x", "rate_limit_rps": 0}]))
        self.assertEqual(c.sources[0].min_interval, MIN_INTERVAL_FLOOR)


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.c = parse_config(cfg(sources=[
            {"domain": "a.com.br", "platform": "p1", "enabled": True},
            {"domain": "b.com.br", "platform": "p1", "enabled": False},
            {"domain": "c.com.br", "platform": "p2", "enabled": True},
        ]))

    def test_enabled_only(self):
        self.assertEqual({s.domain for s in self.c.enabled_sources()},
                         {"a.com.br", "c.com.br"})

    def test_filter_by_platform(self):
        self.assertEqual([s.domain for s in self.c.enabled_sources("p1")], ["a.com.br"])

    def test_platforms_excludes_disabled_only_platform(self):
        self.assertEqual(self.c.platforms(), ("p1", "p2"))


class TestRealFile(unittest.TestCase):
    """The committed sources.yaml must actually load."""

    def setUp(self):
        self.path = Path(__file__).resolve().parents[1] / "config" / "fontes.yaml"

    def test_loads(self):
        c = load_config(self.path)
        self.assertGreaterEqual(len(c.sources), 16)

    def test_robots_excluded_domains_are_absent(self):
        """P0 section 2.2: these four disallow us. They must not be listed."""
        c = load_config(self.path)
        domains = {s.domain for s in c.sources}
        for excluded in ("rglimoveis.com", "praiamarimoveis.com.br",
                         "grandeestiloimoveis.com.br", "novahimoveis.com.br"):
            self.assertNotIn(excluded, domains, f"{excluded} disallows us in robots.txt")

    def test_dead_domains_are_absent(self):
        c = load_config(self.path)
        domains = {s.domain for s in c.sources}
        for dead in ("primesantos.com.br", "chavesantos.com.br", "apolloon.com.br"):
            self.assertNotIn(dead, domains)


if __name__ == "__main__":
    unittest.main()
