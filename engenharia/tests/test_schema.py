"""Tests for utils/schema.py -- the allowlist boundary and value coercion."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comum.schema import (  # noqa: E402
    PROPERTY_FIELDS, SchemaViolation, cep_prefix, clean_currency, clean_int,
    coerce_types, filter_to_allowlist, is_range, normalise_transaction,
    slugify, validate,
)


class TestAllowlist(unittest.TestCase):

    def test_unknown_fields_dropped(self):
        raw = {
            "price": "R$ 2.600,00",
            "broker_name": "Carlos Eduardo",
            "broker_phone": "(13) 98195-9555",
            "corretor_email": "x@y.com",
            "neighborhood": "Embare",
        }
        kept, dropped = filter_to_allowlist(raw)
        self.assertEqual({"price", "neighborhood"}, set(kept))
        self.assertEqual(["broker_name", "broker_phone", "corretor_email"], dropped)

    def test_pii_fields_are_not_in_the_allowlist(self):
        """Regression guard: nobody adds a contact field to the schema."""
        forbidden = {
            "broker_name", "broker_phone", "phone", "email", "cpf", "cnpj",
            "creci", "contact", "corretor", "telefone", "whatsapp", "owner_name",
        }
        self.assertEqual(set(), forbidden & PROPERTY_FIELDS)

    def test_empty_input(self):
        kept, dropped = filter_to_allowlist({})
        self.assertEqual({}, kept)
        self.assertEqual([], dropped)


class TestCleanInt(unittest.TestCase):

    def test_simple(self):
        self.assertEqual(50, clean_int("50 m2"))
        self.assertEqual(120, clean_int("120m²"))
        self.assertEqual(2, clean_int("2 quartos"))

    def test_range_takes_lower_bound_not_concatenation(self):
        """
        SDD D-04: the old implementation concatenated digits, turning
        '50-140 m2' into 50140 -- a 50,000 m2 apartment.
        """
        self.assertEqual(50, clean_int("50-140 m²"))
        self.assertNotEqual(50140, clean_int("50-140 m²"))
        self.assertTrue(is_range("50-140 m²"))

    def test_unparseable_is_none_never_zero(self):
        """'Unknown' must not become 'zero' -- the model would treat it as an
        observation. This was the D-11 failure mode."""
        for bad in ("", None, "sem vaga", "nao informado", "--"):
            with self.subTest(value=bad):
                self.assertIsNone(clean_int(bad))

    def test_passthrough_numeric(self):
        self.assertEqual(3, clean_int(3))
        self.assertEqual(3, clean_int(3.7))
        self.assertIsNone(clean_int(True))


class TestCleanCurrency(unittest.TestCase):

    def test_ptbr_format(self):
        self.assertEqual(2600.0, clean_currency("R$ 2.600,00"))
        self.assertEqual(1030000.0, clean_currency("R$ 1.030.000,00"))
        self.assertEqual(450.0, clean_currency("R$ 450,00"))

    def test_no_decimals(self):
        self.assertEqual(585000.0, clean_currency("R$ 585.000"))

    def test_unparseable(self):
        for bad in ("", None, "sob consulta", "a combinar"):
            with self.subTest(value=bad):
                self.assertIsNone(clean_currency(bad))

    def test_passthrough_numeric(self):
        self.assertEqual(2600.0, clean_currency(2600))


class TestTransactionType(unittest.TestCase):

    def test_rental_wordings(self):
        for raw in ("Locação", "locacao", "Aluguel", "Para alugar", "ALUGUEL"):
            with self.subTest(raw=raw):
                self.assertEqual("locacao", normalise_transaction(raw))

    def test_sale_wordings(self):
        for raw in ("Venda", "À venda", "Comprar", "VENDA"):
            with self.subTest(raw=raw):
                self.assertEqual("venda", normalise_transaction(raw))

    def test_unknown(self):
        self.assertIsNone(normalise_transaction("permuta"))
        self.assertIsNone(normalise_transaction(None))


class TestHelpers(unittest.TestCase):

    def test_slugify_strips_accents(self):
        self.assertEqual("sao-vicente", slugify("São Vicente"))
        self.assertEqual("embare", slugify("Embaré"))

    def test_cep_prefix(self):
        self.assertEqual("11075", cep_prefix("11075-350"))
        self.assertEqual("11075", cep_prefix("11075350"))
        self.assertIsNone(cep_prefix("123"))
        self.assertIsNone(cep_prefix(None))


class TestValidation(unittest.TestCase):

    GOOD = {
        "property_id": "abc", "source_domain": "x.com.br",
        "source_platform": "microsistec", "link": "https://x.com.br/1.html",
        "transaction_type": "locacao", "extraction_date": "2026-08-23",
    }

    def test_valid_record_passes(self):
        validate(self.GOOD)

    def test_missing_required_field_raises(self):
        bad = dict(self.GOOD)
        del bad["transaction_type"]
        with self.assertRaises(SchemaViolation):
            validate(bad)

    def test_bad_transaction_type_raises(self):
        bad = dict(self.GOOD, transaction_type="permuta")
        with self.assertRaises(SchemaViolation):
            validate(bad)


class TestCoerceTypes(unittest.TestCase):

    def test_full_record(self):
        raw = {
            "price": "R$ 2.600,00", "condo_fee": "R$ 450,00",
            "area_m2": "50 m²", "bedrooms": "1 quarto", "suites": "1 Suíte",
            "transaction_type": "Locação", "amenities": None,
        }
        out = coerce_types(raw)
        self.assertEqual(2600.0, out["price"])
        self.assertEqual(450.0, out["condo_fee"])
        self.assertEqual(50, out["area_m2"])
        self.assertEqual(1, out["bedrooms"])
        self.assertEqual(1, out["suites"])
        self.assertEqual("locacao", out["transaction_type"])
        self.assertEqual([], out["amenities"])

    def test_amenities_normalised_to_list(self):
        self.assertEqual(["piscina"], coerce_types({"amenities": "piscina"})["amenities"])
        self.assertEqual(["a", "b"], coerce_types({"amenities": ("a", "b")})["amenities"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
