"""
Universal Software adapter (plan section 16).

No network. Personal values here are SYNTHETIC, for the reason given at the top
of test_privacy.py -- real payloads carry live broker phones, emails and CRECI.

The two tests that matter most are the ones protecting against silent wrongness:
`locacao` must be sent as `aluguel` (the platform accepts `locacao` and returns
an empty set with HTTP 200), and `captadores` must never survive ingestion.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coleta.anuncios.adapters.base import AdapterError  # noqa: E402
from coleta.anuncios.adapters.universal import (  # noqa: E402
    API_FIELDS, FINALIDADE, NEVER_MAP, UniversalAdapter, _area, _count, _money,
)
from comum.config import Source  # noqa: E402
from comum.normalizer import Normalizer  # noqa: E402


def api_record(codigo=5, **over):
    rec = {
        "codigo": codigo, "url_amigavel": "apartamento-a-venda-ponta-da-praia-santos-sp",
        "tipo": "Apartamento", "bairro": "Ponta da Praia",
        "cidade": "Santos", "estado": "SP",
        "valor": "R$ 790.000,00", "valoranterior": "R$ 810.000,00",
        "valortratado": 790000,
        "areainterna": "78,20", "areaprincipaltratado": 7820,
        "numeroquartos": "2", "numerobanhos": "2",
        "numerosuites": "1", "numerovagas": "2",
        "codigofinalidade": 2, "nomecondominio": None,
        "titulo": "Apartamento a venda, Ponta da Praia - Santos/SP",
        "latitude": -23.98, "longitude": -46.30,
        # --- must never survive ---
        "captadores": [{"codigo": 1, "nome": "Joao Silva",
                        "email": "joao@exemplo.com.br",
                        "telefone": "13981959555", "creci": "25497",
                        "foto": "https://x/j.jpg", "principal": 1}],
        "fotos": [{"big_url": "https://x/1.jpg"}], "fotos360": [],
    }
    rec.update(over)
    return rec


class FakeSession:
    """Serves the POST API; records what was asked for."""

    def __init__(self, by_finalidade, per_page=100):
        self.by_finalidade = by_finalidade
        self.per_page = per_page
        self.posts = []
        self.primed = 0

    def prime(self, path="/"):
        self.primed += 1

    def post(self, url, data=None, **kwargs):
        self.posts.append(dict(data or {}))
        fin = (data or {}).get("finalidade")
        page = int((data or {}).get("numeropagina", 1))
        rows = self.by_finalidade.get(fin, [])
        start = (page - 1) * self.per_page
        return FakeResponse({"lista": rows[start:start + self.per_page],
                             "quantidade": len(rows), "favoritos": []})

    def get(self, url, **kwargs):
        return None

    def close(self):
        pass


class FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.status_code = 200
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def adapter(by_finalidade, per_page=100):
    source = Source(domain="consultareimoveis.com.br", platform="universal")
    return UniversalAdapter(FakeSession(by_finalidade, per_page), source)


class TestTheRentalKeyword(unittest.TestCase):
    """The failure that would have been silent and total."""

    def test_locacao_is_sent_as_aluguel(self):
        a = adapter({"aluguel": [api_record(9)]})
        found = list(a.discover(a.domain, "locacao"))
        self.assertEqual(len(found), 1)
        self.assertEqual(a.session.posts[0]["finalidade"], "aluguel")

    def test_venda_is_sent_as_venda(self):
        a = adapter({"venda": [api_record()]})
        list(a.discover(a.domain, "venda"))
        self.assertEqual(a.session.posts[0]["finalidade"], "venda")

    def test_the_mapping_is_explicit(self):
        self.assertEqual(FINALIDADE, {"venda": "venda", "locacao": "aluguel"})

    def test_sending_locacao_literally_would_find_nothing(self):
        """Documents why the mapping exists: the platform answers 200/empty."""
        a = adapter({"locacao": [api_record(9)]})   # keyed by the WRONG word
        self.assertEqual(list(a.discover(a.domain, "locacao")), [])

    def test_unknown_transaction_is_refused(self):
        a = adapter({"venda": [api_record()]})
        with self.assertRaises(AdapterError):
            list(a.discover(a.domain, "temporada"))


class TestPersonalDataIsDroppedAtIngestion(unittest.TestCase):
    def setUp(self):
        self.a = adapter({"venda": [api_record()]})
        self.projected = self.a._load("venda")[0]

    def test_captadores_never_enters_the_catalogue(self):
        self.assertNotIn("captadores", self.projected)

    def test_every_never_map_field_is_absent(self):
        for f in NEVER_MAP:
            self.assertNotIn(f, self.projected, f"{f} survived ingestion")

    def test_no_personal_string_survives(self):
        blob = json.dumps(self.projected)
        for leak in ("13981959555", "joao@exemplo.com.br", "Joao Silva", "25497"):
            self.assertNotIn(leak, blob)

    def test_never_map_and_api_fields_do_not_overlap(self):
        self.assertEqual(API_FIELDS & NEVER_MAP, frozenset())

    def test_coordinates_are_deferred(self):
        self.assertNotIn("latitude", self.projected)
        self.assertNotIn("longitude", self.projected)


class TestDiscovery(unittest.TestCase):
    def test_listing_url_is_slug_then_code(self):
        a = adapter({"venda": [api_record(5)]})
        d = next(iter(a.discover(a.domain, "venda")))
        self.assertEqual(
            d.url,
            "https://consultareimoveis.com.br/imovel/"
            "apartamento-a-venda-ponta-da-praia-santos-sp/5")

    def test_session_is_primed_once(self):
        a = adapter({"venda": [api_record()], "aluguel": [api_record(9)]})
        list(a.discover(a.domain, "venda"))
        list(a.discover(a.domain, "locacao"))
        self.assertEqual(a.session.primed, 1)

    def test_each_transaction_is_its_own_query(self):
        """finalidade is an input, so the catalogue caches per transaction."""
        a = adapter({"venda": [api_record()], "aluguel": [api_record(9)]})
        list(a.discover(a.domain, "venda"))
        list(a.discover(a.domain, "venda"))       # cached
        list(a.discover(a.domain, "locacao"))
        self.assertEqual([p["finalidade"] for p in a.session.posts],
                         ["venda", "aluguel"])

    def test_pagination(self):
        rows = [api_record(i) for i in range(250)]
        a = adapter({"venda": rows}, per_page=100)
        self.assertEqual(len(a._load("venda")), 250)
        self.assertEqual(len(a.session.posts), 3)

    def test_record_without_code_is_skipped(self):
        a = adapter({"venda": [api_record(codigo=None), api_record(7)]})
        self.assertEqual(len(list(a.discover(a.domain, "venda"))), 1)

    def test_non_json_response_is_an_adapter_error(self):
        """viveremsantosimoveis runs a build with no such endpoint."""
        a = adapter({"venda": [api_record()]})
        a.session.post = lambda url, data=None, **kw: FakeResponse(None)
        with self.assertRaises(AdapterError):
            a._load("venda")


class TestParsing(unittest.TestCase):
    def _parse(self, transaction="venda", **over):
        key = FINALIDADE[transaction]
        a = adapter({key: [api_record(**over)]})
        d = next(iter(a.discover(a.domain, transaction)))
        return a.parse(a.fetch(d))

    def test_no_listing_page_is_fetched(self):
        a = adapter({"venda": [api_record()]})
        d = next(iter(a.discover(a.domain, "venda")))
        before = len(a.session.posts)
        a.fetch(d)
        self.assertEqual(len(a.session.posts), before)

    def test_price_prefers_the_integer_field(self):
        self.assertEqual(self._parse()["price"], 790000.0)

    def test_price_falls_back_to_the_pt_br_string(self):
        self.assertEqual(self._parse(valortratado=None)["price"], 790000.0)

    def test_area_is_the_integer_divided_by_100(self):
        self.assertEqual(self._parse()["area_m2"], 78)

    def test_area_falls_back_to_the_pt_br_string(self):
        self.assertEqual(self._parse(areaprincipaltratado=0)["area_m2"], 78)

    def test_zero_area_is_absent_not_zero(self):
        rec = self._parse(areaprincipaltratado=0, areainterna="0,00")
        self.assertIsNone(rec["area_m2"])

    def test_counts_are_coerced(self):
        rec = self._parse()
        self.assertEqual((rec["bedrooms"], rec["bathrooms"],
                          rec["suites"], rec["parking_spots"]), (2, 2, 1, 2))

    def test_missing_count_is_none_not_zero(self):
        self.assertIsNone(self._parse(numeroquartos="")["bedrooms"])

    def test_geography(self):
        rec = self._parse()
        self.assertEqual((rec["city"], rec["state"], rec["neighborhood"]),
                         ("Santos", "SP", "Ponta da Praia"))


class TestEndToEnd(unittest.TestCase):
    def _collect(self, by_fin):
        a = adapter(by_fin)
        n = Normalizer(source_domain=a.domain, source_platform=a.platform,
                       extraction_date="2026-08-28")
        for t in ("venda", "locacao"):
            for d in a.discover(a.domain, t):
                n.normalize(a.parse(a.fetch(d)), d)
        return n

    def test_both_transactions_produce_clean_rows(self):
        n = self._collect({"venda": [api_record(1)], "aluguel": [api_record(2)]})
        rows = n.finalize()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["transaction_type"] for r in rows}, {"venda", "locacao"})

    def test_gate_accepts_a_realistic_harvest(self):
        n = self._collect({"venda": [api_record(i) for i in range(1, 31)],
                           "aluguel": [api_record(100 + i) for i in range(10)]})
        self.assertEqual(len(n.finalize()), 40)

    def test_nothing_personal_reaches_the_rows(self):
        rows = self._collect({"venda": [api_record()]}).finalize()
        blob = json.dumps(rows)
        for leak in ("13981959555", "joao@exemplo.com.br", "Joao Silva",
                     "25497", "-23.98"):
            self.assertNotIn(leak, blob)


class TestHelpers(unittest.TestCase):
    def test_money(self):
        self.assertEqual(_money(790000), 790000.0)
        self.assertEqual(_money("R$ 790.000,00"), 790000.0)
        self.assertEqual(_money("R$ 1.800,50"), 1800.5)
        self.assertIsNone(_money(0))
        self.assertIsNone(_money(None))

    def test_count(self):
        self.assertEqual(_count("2"), 2)
        self.assertEqual(_count(0), 0)
        self.assertIsNone(_count(""))
        self.assertIsNone(_count(None))

    def test_area(self):
        self.assertEqual(_area({"areaprincipaltratado": 7820}), 78)
        self.assertEqual(_area({"areaprincipaltratado": 0, "areainterna": "78,20"}), 78)
        self.assertIsNone(_area({"areaprincipaltratado": 0, "areainterna": ""}))


if __name__ == "__main__":
    unittest.main()
