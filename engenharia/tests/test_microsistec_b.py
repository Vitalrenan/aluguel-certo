"""
Group B adapter (plan section 14).

No network. The fake session below returns API pages built in-process, shaped
after what section 14 measured on the live endpoint -- including the parts we
must never keep, so the tests can prove they are dropped rather than assume it.

The personal values here are SYNTHETIC. Real captured payloads are not
committed, for the reason given at the top of test_privacy.py: they contain
live broker phones, emails and CRECI numbers, and storing them would violate
the policy this pipeline enforces.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coleta.anuncios.adapters.base import AdapterError  # noqa: E402
from coleta.anuncios.adapters.microsistec_b import (  # noqa: E402
    API_FIELDS, NEVER_MAP, MicrosistecBAdapter, _amenities, _count, _money,
)
from comum.config import Source  # noqa: E402
from comum.normalizer import Normalizer  # noqa: E402
from comum.privacy import PIIViolation  # noqa: E402


def api_record(code="AP15641", for_sale=True, for_rent=True, **over):
    """One result as the live API shapes it, personal fields included."""
    rec = {
        "id": 280017, "code": code, "hash": "9f2c1188aa3e4471bb90",
        "details_url": f"https://cferreiraimoveis.com.br/detalhes/imovel/"
                       f"apartamento/santos/gonzaga/codigo/{code.lower()}",
        "for_sale": for_sale, "for_rent": for_rent,
        "for_season": False, "for_lease": False,
        "sale_price": 460000, "rent_price": 3400,
        "iptu_price": "130.00", "condominium_price": "580.00",
        "area_useful": 76, "area_total": 0,
        "dorms_count": 2, "bathroom_count": 2, "suit_count": 1,
        "parking_lot_count": "1", "floor": "9",
        "type_text": "Apartamento", "subtype_text": "Padrao",
        "city_name": "Santos", "neighborhood_name": "Gonzaga", "uf": "SP",
        "features": [{"id": 1, "name": "Piscina", "pivot": {}},
                     {"id": 2, "name": "Portaria 24h", "pivot": {}}],
        "obs": "Apartamento reformado, 2 quartos.",
        # --- everything below must never survive ingestion ---
        "receiver1": {"name": "Joao Silva", "creci": "25497",
                      "email": "joao@exemplo.com.br",
                      "phones": [{"number": "13981959555"}]},
        "receiver2": None, "receiver1_id": "u0328", "receiver2_id": "u0329",
        "contact_message": "Ola! Gostaria de informacoes. Chame no 13981959555",
        "familiar_income": None, "bank_balance": None, "ri": None, "cib": None,
        "evaluation_price": "500000.00",
        "latitude": -23.9655, "longitude": -46.3311,
        "reference_point": "AZEVEDO SODRE",
        "photos": [{"big_url": "https://x/1.jpg"}],
    }
    rec.update(over)
    return rec


class FakeSession:
    """Serves paginated API responses; counts requests."""

    def __init__(self, records, per_page=100):
        self.records = records
        self.per_page = per_page
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        page = int(url.split("page=")[-1])
        start = (page - 1) * self.per_page
        chunk = self.records[start:start + self.per_page]
        return FakeResponse({
            "pagination": {"currentPage": page, "totalItems": len(self.records),
                           "perPage": str(self.per_page)},
            "results": chunk,
        })

    def close(self):
        pass


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


def adapter(records, per_page=100):
    source = Source(domain="cferreiraimoveis.com.br", platform="microsistec_b")
    return MicrosistecBAdapter(FakeSession(records, per_page), source)


class TestPersonalDataIsDroppedAtIngestion(unittest.TestCase):
    """Section 14.4: the API bundles PII we cannot decline to receive."""

    def setUp(self):
        self.a = adapter([api_record()])
        self.projected = self.a._load_catalogue()[0]

    def test_receiver_block_never_enters_the_catalogue(self):
        self.assertNotIn("receiver1", self.projected)
        self.assertNotIn("receiver2", self.projected)

    def test_contact_message_never_enters_the_catalogue(self):
        self.assertNotIn("contact_message", self.projected)

    def test_owner_financial_fields_never_enter_the_catalogue(self):
        for f in ("familiar_income", "bank_balance", "ri", "cib", "evaluation_price"):
            self.assertNotIn(f, self.projected)

    def test_every_never_map_field_is_absent(self):
        for f in NEVER_MAP:
            self.assertNotIn(f, self.projected, f"{f} survived ingestion")

    def test_no_personal_string_survives_anywhere_in_the_projection(self):
        blob = json.dumps(self.projected)
        for leak in ("13981959555", "joao@exemplo.com.br", "Joao Silva", "25497"):
            self.assertNotIn(leak, blob)

    def test_never_map_and_api_fields_do_not_overlap(self):
        self.assertEqual(API_FIELDS & NEVER_MAP, frozenset())

    def test_coordinates_are_deferred_not_persisted(self):
        """Section 14.5: a policy decision, deliberately not taken in P3."""
        self.assertNotIn("latitude", self.projected)
        self.assertNotIn("longitude", self.projected)


class TestDiscovery(unittest.TestCase):
    def test_dual_listing_is_discovered_under_both_transactions(self):
        a = adapter([api_record(for_sale=True, for_rent=True)])
        self.assertEqual(len(list(a.discover(a.domain, "venda"))), 1)
        self.assertEqual(len(list(a.discover(a.domain, "locacao"))), 1)

    def test_sale_only_listing_is_not_discovered_as_a_rental(self):
        a = adapter([api_record(for_sale=True, for_rent=False)])
        self.assertEqual(list(a.discover(a.domain, "locacao")), [])

    def test_transaction_label_is_attached(self):
        a = adapter([api_record()])
        d = next(iter(a.discover(a.domain, "locacao")))
        self.assertEqual(d.transaction_type, "locacao")
        self.assertIn("for_rent", d.discovery_path)

    def test_the_two_rows_get_different_property_ids(self):
        """Otherwise the P5 MERGE would treat one as an update of the other."""
        a = adapter([api_record()])
        venda = next(iter(a.discover(a.domain, "venda")))
        locacao = next(iter(a.discover(a.domain, "locacao")))
        self.assertNotEqual(venda.listing_id, locacao.listing_id)

    def test_catalogue_is_fetched_once_across_both_passes(self):
        a = adapter([api_record()])
        list(a.discover(a.domain, "venda"))
        list(a.discover(a.domain, "locacao"))
        self.assertEqual(len(a.session.urls), 1)

    def test_pagination_walks_every_page(self):
        records = [api_record(code=f"AP{i:05d}") for i in range(250)]
        a = adapter(records, per_page=100)
        a.session.per_page = 100
        self.assertEqual(len(a._load_catalogue()), 250)
        self.assertEqual(len(a.session.urls), 3)

    def test_listing_without_a_url_is_skipped_not_crashed(self):
        a = adapter([api_record(details_url=None), api_record(code="AP2")])
        self.assertEqual(len(list(a.discover(a.domain, "venda"))), 1)

    def test_empty_api_is_a_loud_failure(self):
        """A zero-row harvest is the silent-success mode (section 10)."""
        a = adapter([])
        with self.assertRaises(AdapterError):
            a._load_catalogue()

    def test_unknown_transaction_is_refused(self):
        a = adapter([api_record()])
        with self.assertRaises(AdapterError):
            list(a.discover(a.domain, "temporada"))


class TestParsing(unittest.TestCase):
    def _parse(self, transaction, **over):
        a = adapter([api_record(**over)])
        d = next(iter(a.discover(a.domain, transaction)))
        return a.parse(a.fetch(d))

    def test_no_listing_page_is_ever_fetched(self):
        a = adapter([api_record()])
        d = next(iter(a.discover(a.domain, "venda")))
        before = len(a.session.urls)
        a.fetch(d)
        self.assertEqual(len(a.session.urls), before)

    def test_sale_row_carries_the_sale_price(self):
        self.assertEqual(self._parse("venda")["price"], 460000.0)

    def test_rent_row_carries_the_rent_price(self):
        self.assertEqual(self._parse("locacao")["price"], 3400.0)

    def test_en_us_decimal_string_is_not_read_as_pt_br(self):
        """'130.00' is R$ 130,00 -- not R$ 13.000,00."""
        self.assertEqual(self._parse("venda")["iptu_tax"], 130.0)
        self.assertEqual(self._parse("venda")["condo_fee"], 580.0)

    def test_zero_money_means_not_informed_not_free(self):
        rec = self._parse("venda", iptu_price="0.00", condominium_price=0)
        self.assertIsNone(rec["iptu_tax"])
        self.assertIsNone(rec["condo_fee"])

    def test_zero_area_is_not_a_property(self):
        self.assertIsNone(self._parse("venda")["area_total_m2"])

    def test_zero_count_is_a_real_answer(self):
        """No parking space is a fact about the flat, not a missing field."""
        self.assertEqual(self._parse("venda", parking_lot_count="0")["parking_spots"], 0)

    def test_amenities_are_names_only(self):
        self.assertEqual(self._parse("venda")["amenities"], ["Piscina", "Portaria 24h"])

    def test_geography_is_mapped(self):
        rec = self._parse("venda")
        self.assertEqual((rec["city"], rec["state"], rec["neighborhood"]),
                         ("Santos", "SP", "Gonzaga"))

    def test_string_counts_are_coerced(self):
        rec = self._parse("venda")
        self.assertEqual(rec["floor_level"], 9)
        self.assertEqual(rec["parking_spots"], 1)


class TestEndToEndThroughTheNormalizer(unittest.TestCase):
    """The adapter is only correct if the chokepoint accepts what it emits."""

    def _collect(self, records):
        a = adapter(records)
        n = Normalizer(source_domain=a.domain, source_platform=a.platform,
                       extraction_date="2026-08-25")
        for transaction in ("venda", "locacao"):
            for d in a.discover(a.domain, transaction):
                n.normalize(a.parse(a.fetch(d)), d)
        return n

    def test_dual_listing_yields_two_clean_rows(self):
        n = self._collect([api_record()])
        rows = n.finalize()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["transaction_type"] for r in rows}, {"venda", "locacao"})
        self.assertEqual({r["price"] for r in rows}, {460000.0, 3400.0})

    def test_the_gate_accepts_a_real_shaped_harvest(self):
        n = self._collect([api_record(code=f"AP{i:05d}") for i in range(25)])
        self.assertEqual(len(n.finalize()), 50)

    def test_property_ids_are_unique_per_transaction(self):
        rows = self._collect([api_record()]).finalize()
        self.assertEqual(len({r["property_id"] for r in rows}), 2)

    def test_nothing_personal_reaches_the_rows(self):
        rows = self._collect([api_record()]).finalize()
        blob = json.dumps(rows)
        for leak in ("13981959555", "joao@exemplo.com.br", "Joao Silva",
                     "25497", "AZEVEDO SODRE", "-23.9655"):
            self.assertNotIn(leak, blob)

    def test_adversarial_description_is_redacted(self):
        """Section 10: descriptions containing phone/email/CPF/CRECI."""
        leaky = api_record(obs="Otimo apto. Falar com Maria Souza no (13) 98195-9555, "
                               "email vendas@exemplo.com.br, CRECI 44569, "
                               "CPF 123.456.789-00")
        n = self._collect([leaky])
        rows = n.finalize()
        text = rows[0]["description_clean"]
        for leak in ("98195", "vendas@exemplo.com.br", "44569", "123.456.789-00",
                     "Maria Souza"):
            self.assertNotIn(leak, text)
        self.assertIn("[CONTATO]", text)

    def test_a_leaky_amenity_does_not_pass_the_gate_unredacted(self):
        leaky = api_record(features=[{"name": "Portaria - fone 13981959555"}])
        rows = self._collect([leaky]).finalize()
        self.assertNotIn("13981959555", json.dumps(rows))


class TestHelpers(unittest.TestCase):
    def test_money(self):
        self.assertEqual(_money("1650.00"), 1650.0)
        self.assertEqual(_money(30000), 30000.0)
        self.assertIsNone(_money("0.00"))
        self.assertIsNone(_money(0))
        self.assertIsNone(_money(None))
        self.assertIsNone(_money("consulte"))

    def test_count(self):
        self.assertEqual(_count("9"), 9)
        self.assertEqual(_count(0), 0)
        self.assertIsNone(_count(None))
        self.assertIsNone(_count(""))
        self.assertIsNone(_count("varias"))

    def test_amenities(self):
        self.assertEqual(_amenities([{"name": "Piscina"}, {"id": 2}, "solto"]),
                         ["Piscina"])
        self.assertEqual(_amenities(None), [])


if __name__ == "__main__":
    unittest.main()
