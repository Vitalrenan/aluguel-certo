"""
The Normalizer -- the chokepoint between sloppy adapters and the lake.

Every test here is really one question: can an adapter, by being careless or
by being wrong, get something past this module that should not be persisted?

Strings are SYNTHETIC but modelled on shapes P0 observed (see the note at the
top of test_privacy.py -- real captured pages are not committed).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coleta.anuncios.adapters.base import DiscoveredURL  # noqa: E402
from comum.normalizer import (  # noqa: E402
    EmptyHarvest, Normalizer, derive_property_id, normalise_url,
)
from comum.privacy import PIIViolation  # noqa: E402
from comum.schema import PROPERTY_FIELDS  # noqa: E402


def discovered(url="https://scimoveissantos.com.br/5989-apartamento-em-santos-bairro-embare.html",
               transaction="locacao", listing_id="5989", **hints):
    return DiscoveredURL(
        url=url,
        transaction_type=transaction,
        discovery_path="sitemap",
        listing_id=listing_id,
        hints=hints or {"property_type": "apartamento", "neighborhood": "embare"},
    )


def norm(**kwargs):
    kwargs.setdefault("source_domain", "scimoveissantos.com.br")
    kwargs.setdefault("source_platform", "microsistec_a")
    kwargs.setdefault("extraction_date", "2026-08-25")
    return Normalizer(**kwargs)


class TestAllowlist(unittest.TestCase):
    def test_unknown_fields_are_dropped(self):
        n = norm()
        rec = n.normalize({"price": "R$ 2.600,00", "broker_name": "Joao Silva",
                           "broker_phone": "13981959555"}, discovered())
        self.assertNotIn("broker_name", rec)
        self.assertNotIn("broker_phone", rec)
        self.assertEqual(n.stats.dropped_fields["broker_name"], 1)

    def test_output_keys_are_exactly_the_allowlist(self):
        rec = norm().normalize({"price": "R$ 500.000,00"}, discovered(transaction="venda"))
        self.assertEqual(set(rec), set(PROPERTY_FIELDS))

    def test_a_field_invented_next_year_is_still_dropped(self):
        """The point of an allowlist: it fails closed on the unanticipated."""
        rec = norm().normalize({"price": "1000", "corretor_whatsapp_v2": "5513981959555"},
                               discovered())
        self.assertNotIn("corretor_whatsapp_v2", rec)


class TestProvenance(unittest.TestCase):
    def test_transaction_comes_from_discovery_not_the_page(self):
        """Plan section 13.3: the page does not know. The discovery path does."""
        rec = norm().normalize({"transaction_type": "venda"},
                               discovered(transaction="locacao"))
        self.assertEqual(rec["transaction_type"], "locacao")

    def test_adapter_cannot_forge_source_domain(self):
        rec = norm().normalize({"source_domain": "outracoisa.com.br"}, discovered())
        self.assertEqual(rec["source_domain"], "scimoveissantos.com.br")

    def test_hints_fill_gaps_but_the_page_wins(self):
        d = discovered(property_type="apartamento", neighborhood="embare")
        rec = norm().normalize({"neighborhood": "Gonzaga"}, d)
        self.assertEqual(rec["property_type"], "apartamento")   # from the URL
        self.assertEqual(rec["neighborhood"], "Gonzaga")        # page wins

    def test_target_supplies_city_and_state(self):
        class T:
            state, city = "sp", "santos"
        rec = norm(target=T()).normalize({}, discovered())
        self.assertEqual((rec["city"], rec["state"]), ("santos", "sp"))

    def test_last_seen_is_always_this_run(self):
        rec = norm().normalize({"listing_last_seen": "2020-01-01"}, discovered())
        self.assertEqual(rec["listing_last_seen"], "2026-08-25")


class TestScrubbing(unittest.TestCase):
    def test_description_is_redacted_and_counted(self):
        n = norm()
        rec = n.normalize(
            {"description_clean": "Otimo apartamento. Falar com Maria Souza "
                                  "no (13) 98195-9555 ou vendas@exemplo.com.br"},
            discovered())
        self.assertNotIn("98195", rec["description_clean"])
        self.assertNotIn("vendas@exemplo.com.br", rec["description_clean"])
        self.assertGreaterEqual(sum(n.stats.pii_hits.values()), 2)

    def test_property_signal_survives_redaction(self):
        rec = norm().normalize(
            {"description_clean": "Apartamento 50 m2, 1 quarto, CEP 11075-350. "
                                  "Contato: 13981959555"},
            discovered())
        self.assertIn("50 m2", rec["description_clean"])
        self.assertIn("11075-350", rec["description_clean"])
        self.assertNotIn("13981959555", rec["description_clean"])

    def test_amenities_are_scrubbed_too(self):
        rec = norm().normalize(
            {"amenities": ["piscina", "portaria 24h - fone 13981959555"]},
            discovered())
        self.assertNotIn("13981959555", " ".join(rec["amenities"]))

    def test_whatsapp_link_as_listing_url_is_rejected(self):
        n = norm()
        d = DiscoveredURL(
            url="https://api.whatsapp.com/send?phone=5513981959555&text=oi",
            transaction_type="locacao", discovery_path="dom")
        self.assertIsNone(n.normalize({"price": "2600"}, d))
        self.assertEqual(n.stats.rejected["link_discarded_by_scrubber"], 1)


class TestValidation(unittest.TestCase):
    def test_record_without_price_is_still_kept(self):
        """Price is not required: a listing 'sob consulta' is real data."""
        rec = norm().normalize({}, discovered())
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["price"])

    def test_ranges_take_the_lower_bound_not_concatenation(self):
        """SDD D-04: '50-140 m2' used to become 50140."""
        rec = norm().normalize({"area_m2": "50-140 m2"}, discovered())
        self.assertEqual(rec["area_m2"], 50)

    def test_currency_is_parsed_pt_br(self):
        rec = norm().normalize({"price": "R$ 2.600,00", "condo_fee": "R$ 450,50"},
                               discovered())
        self.assertEqual(rec["price"], 2600.0)
        self.assertEqual(rec["condo_fee"], 450.5)

    def test_unparseable_number_is_none_not_zero(self):
        rec = norm().normalize({"bedrooms": "consulte"}, discovered())
        self.assertIsNone(rec["bedrooms"])


class TestPropertyId(unittest.TestCase):
    def test_stable_for_same_listing(self):
        a = derive_property_id("a.com.br", "5989", "https://a.com.br/x")
        b = derive_property_id("a.com.br", "5989", "https://a.com.br/y")
        self.assertEqual(a, b)

    def test_same_listing_id_on_two_agencies_is_two_ids(self):
        """Deduplication is a downstream clustering job (section 4.2), not this."""
        self.assertNotEqual(derive_property_id("a.com.br", "5989", None),
                            derive_property_id("b.com.br", "5989", None))

    def test_url_fallback_when_no_listing_id(self):
        """P0 found a secondary Microsistec shape with no numeric id."""
        pid = derive_property_id("a.com.br", None,
                                 "https://www.a.com.br/p-apartamento-bairro_ilha_porchat-.html/")
        self.assertTrue(pid)
        same = derive_property_id("a.com.br", None,
                                  "http://a.com.br/p-apartamento-bairro_ilha_porchat-.html")
        self.assertEqual(pid, same)

    def test_normalise_url_strips_the_noise(self):
        self.assertEqual(
            normalise_url("https://WWW.A.com.br/Imovel/5989/?utm_source=x#foto"),
            "a.com.br/imovel/5989")


class TestTheGate(unittest.TestCase):
    def test_finalize_raises_when_pii_slipped_in(self):
        n = norm()
        n.normalize({}, discovered())
        # Simulate a record reaching the batch by some path other than
        # normalize() -- the gate exists precisely for that case.
        n.records.append({"property_id": "x", "link": "https://a.com.br/1",
                          "address": "Falar com Joao Silva 13981959555"})
        with self.assertRaises(PIIViolation):
            n.finalize()

    def test_zero_rows_is_a_failure_not_a_success(self):
        with self.assertRaises(EmptyHarvest):
            norm().finalize()

    def test_zero_rows_allowed_when_the_caller_judges_at_run_level(self):
        self.assertEqual(norm().finalize(allow_empty=True), [])

    def test_clean_batch_passes(self):
        n = norm()
        n.normalize({"price": "R$ 2.600,00", "description_clean": "Otimo apartamento"},
                    discovered())
        self.assertEqual(len(n.finalize()), 1)

    def test_listing_id_digits_in_url_are_not_read_as_phones(self):
        """The P1 false positive that would have discarded every Zap URL."""
        n = norm()
        n.normalize({}, discovered(
            url="https://www.zapimoveis.com.br/imovel/venda-apartamento-id-2809851822/",
            listing_id="2809851822", transaction="venda"))
        self.assertEqual(len(n.finalize()), 1)


class TestStats(unittest.TestCase):
    def test_fill_rates_are_reported(self):
        # Precos realistas de propósito: desde 2026-08-31 a faixa do contrato
        # (§5) anula venda abaixo de R$ 50.000, e a fixture antiga usava
        # R$ 1.000 -- valor que nao existe como venda.
        n = norm()
        n.normalize({"price": "450000", "iptu_tax": "50"},
                    discovered(transaction="venda"))
        n.normalize({"price": "620000"},
                    discovered(listing_id="6000", transaction="venda"))
        rates = n.stats.fill_rates()
        self.assertEqual(rates["price"], 1.0)
        self.assertEqual(rates["iptu_tax"], 0.5)

    def test_report_flags_low_fill_but_not_expected_low_fields(self):
        n = norm()
        n.normalize({"price": "1000"}, discovered())
        report = n.stats.report()
        self.assertIn("(expected-low)", report)   # iptu_tax, section 13.2
        self.assertIn("fill rates", report)


if __name__ == "__main__":
    unittest.main()
