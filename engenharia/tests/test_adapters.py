"""
Adapter contract and registry (plan sections 3.1, 3.2, 13.3).

The contract's job is to make one specific mistake impossible: yielding a
listing whose transaction type nobody established. Section 13.3 measured that
neither Microsistec URL grammar encodes venda/locacao and Group B's JSON-LD
Offer carries no businessFunction -- so if discovery does not attach it, the
fact is simply lost, and the rent-to-value ratio this pivot exists to measure
cannot be computed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coleta.anuncios import adapters  # noqa: E402
from coleta.anuncios.adapters.base import (  # noqa: E402
    AdapterError, DiscoveredURL, ListingSource, Payload,
)


class TestDiscoveredURL(unittest.TestCase):
    def test_transaction_is_mandatory_and_validated(self):
        with self.assertRaises(AdapterError):
            DiscoveredURL(url="https://a.com.br/1", transaction_type="unknown",
                          discovery_path="sitemap")

    def test_none_transaction_is_refused(self):
        with self.assertRaises(AdapterError):
            DiscoveredURL(url="https://a.com.br/1", transaction_type=None,
                          discovery_path="sitemap")

    def test_both_transactions_accepted(self):
        for t in ("venda", "locacao"):
            self.assertEqual(
                DiscoveredURL(url="https://a.com.br/1", transaction_type=t,
                              discovery_path="sitemap").transaction_type, t)

    def test_domain_strips_www(self):
        d = DiscoveredURL(url="https://www.Casabellaimoveis.com/5989.html",
                          transaction_type="venda", discovery_path="sitemap")
        self.assertEqual(d.domain, "casabellaimoveis.com")

    def test_hints_default_to_empty_not_shared(self):
        a = DiscoveredURL("https://a.com.br/1", "venda", "sitemap")
        b = DiscoveredURL("https://a.com.br/2", "venda", "sitemap")
        a.hints["x"] = 1
        self.assertEqual(b.hints, {})


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self._saved = dict(adapters._REGISTRY)

    def tearDown(self):
        adapters._REGISTRY.clear()
        adapters._REGISTRY.update(self._saved)

    def _make(self, name):
        @adapters.register
        class _A(ListingSource):
            platform = name
            def discover(self, domain, transaction):
                return iter(())
            def parse(self, payload):
                return {}
        return _A

    def test_register_and_resolve(self):
        cls = self._make("fake_platform")
        self.assertIs(adapters.resolve("fake_platform"), cls)
        self.assertTrue(adapters.is_registered("fake_platform"))

    def test_unknown_platform_raises_with_a_useful_message(self):
        with self.assertRaises(adapters.UnknownPlatform) as ctx:
            adapters.resolve("nao_existe")
        self.assertIn("nao_existe", str(ctx.exception))

    def test_platform_attribute_is_required(self):
        with self.assertRaises(AdapterError):
            @adapters.register
            class _NoPlatform(ListingSource):
                def discover(self, domain, transaction):
                    return iter(())
                def parse(self, payload):
                    return {}

    def test_double_registration_of_a_platform_is_refused(self):
        self._make("dup_platform")
        with self.assertRaises(AdapterError):
            self._make("dup_platform")

    def test_build_instantiates_with_session_and_source(self):
        self._make("build_me")

        class _Source:
            platform, domain = "build_me", "a.com.br"
        session = object()
        adapter = adapters.build(_Source(), session)
        self.assertIs(adapter.session, session)
        self.assertEqual(adapter.domain, "a.com.br")


class TestContract(unittest.TestCase):
    def test_abstract_methods_must_be_implemented(self):
        class _Incomplete(ListingSource):
            platform = "incomplete"
        with self.assertRaises(TypeError):
            _Incomplete(None, None)

    def test_default_fetch_returns_none_for_a_missing_listing(self):
        """404/410 are ordinary outcomes on these platforms, not errors."""
        class _Session:
            def get(self, url):
                return None

        class _A(ListingSource):
            platform = "x"
            def discover(self, domain, transaction):
                return iter(())
            def parse(self, payload):
                return {}

        class _Source:
            domain = "a.com.br"
        d = DiscoveredURL("https://a.com.br/gone", "venda", "sitemap")
        self.assertIsNone(_A(_Session(), _Source()).fetch(d))

    def test_payload_exposes_the_discovered_url(self):
        d = DiscoveredURL("https://a.com.br/1", "venda", "sitemap")
        self.assertEqual(Payload(discovered=d, text="<html>").url, "https://a.com.br/1")


if __name__ == "__main__":
    unittest.main()
