"""
Adversarial test corpus for utils/privacy.py.

The strings here are SYNTHETIC but modelled on shapes P0 actually observed on
Santos agency sites (see P0-recon-santos.md §5). Real captured pages are
deliberately NOT committed as fixtures: they contain live phone numbers and
emails, and storing them would violate the policy this module enforces.

Run:  python -m unittest discover -s tests -v      (from pipeline/)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comum.privacy import (  # noqa: E402
    PIIViolation, REDACTION, assert_clean, is_contact_url,
    scan_text, scrub_record, scrub_text, scrub_url,
)


class TestPhoneDetection(unittest.TestCase):
    """Every Brazilian phone shape seen in the wild must be caught."""

    CASES = [
        "(13) 98195-9555",      # mobile, parenthesised DDD
        "(13) 3041-5555",       # landline
        "(13)3041-5555",        # no space
        "13 98195-9555",        # no parens
        "+55 13 98195-9555",    # international
        "+5513981959555",       # international, unseparated
        "13981959555",          # bare mobile, 11 digits
        "5513981959555",        # bare with country code, 13 digits
    ]

    def test_all_shapes_detected(self):
        for raw in self.CASES:
            with self.subTest(phone=raw):
                text = f"Apartamento otimo. Ligue {raw} para agendar."
                clean, hits = scrub_text(text)
                self.assertTrue(hits, f"no hit for {raw!r}")
                self.assertNotIn(raw, clean)
                self.assertIn(REDACTION, clean)

    def test_digits_do_not_survive(self):
        """A partially-redacted phone is still a phone."""
        clean, _ = scrub_text("Contato (13) 98195-9555 agora")
        self.assertNotIn("98195", clean)
        self.assertNotIn("9555", clean)


class TestContactUrls(unittest.TestCase):
    """P0 §5.1 -- phone numbers live inside WhatsApp deep links."""

    def test_whatsapp_deeplink_is_contact_url(self):
        url = "https://api.whatsapp.com/send?phone=5513981959555&text=Ola"
        self.assertTrue(is_contact_url(url))
        self.assertIsNone(scrub_url(url))

    def test_wa_me_short_link(self):
        self.assertIsNone(scrub_url("https://wa.me/5513981959555"))

    def test_tel_and_mailto(self):
        self.assertIsNone(scrub_url("tel:+5513981959555"))
        self.assertIsNone(scrub_url("mailto:contato@exemplo.com.br"))

    def test_phone_in_query_string_is_stripped(self):
        url = "https://exemplo.com.br/imovel/123?phone=5513981959555&ref=abc"
        out = scrub_url(url)
        self.assertIsNotNone(out)
        self.assertNotIn("5513981959555", out)
        self.assertIn("/imovel/123", out)

    def test_whatsapp_url_inside_free_text_is_redacted(self):
        text = "Fale conosco: https://api.whatsapp.com/send?phone=5513981959555"
        clean, hits = scrub_text(text)
        self.assertTrue(hits)
        self.assertNotIn("5513981959555", clean)
        self.assertNotIn("whatsapp", clean.lower())


class TestOtherIdentifiers(unittest.TestCase):

    def test_email(self):
        clean, hits = scrub_text("Envie para socomercial@outlook.com hoje")
        self.assertTrue(any(h.kind == "email" for h in hits))
        self.assertNotIn("@outlook.com", clean)

    def test_cpf(self):
        clean, hits = scrub_text("CPF 123.456.789-01 do proprietario")
        self.assertTrue(any(h.kind == "cpf" for h in hits))
        self.assertNotIn("123.456.789-01", clean)

    def test_cnpj(self):
        clean, hits = scrub_text("CNPJ 12.345.678/0001-90")
        self.assertTrue(any(h.kind == "cnpj" for h in hits))
        self.assertNotIn("0001-90", clean)

    def test_creci_variants(self):
        for raw in ("CRECI: 25497", "CRECI 44569", "CRECI-SP 12345-F"):
            with self.subTest(creci=raw):
                clean, hits = scrub_text(f"Anuncio {raw} valido")
                self.assertTrue(any(h.kind == "creci" for h in hits), raw)
                self.assertNotIn("25497", clean)

    def test_labelled_name_redacted_but_label_kept(self):
        clean, hits = scrub_text("Falar com Maria Silva Santos sobre o imovel")
        self.assertTrue(any(h.kind == "labelled_name" for h in hits))
        self.assertNotIn("Maria Silva Santos", clean)
        self.assertIn("Falar com", clean)

    def test_corretor_label(self):
        clean, hits = scrub_text("Corretor responsavel: Joao Pedro Alves")
        self.assertTrue(hits)
        self.assertNotIn("Joao Pedro Alves", clean)


class TestFalsePositives(unittest.TestCase):
    """
    Legitimate property data must survive. Over-redaction is cheap but not
    free -- if these fail, descriptions get shredded and the model loses
    signal.
    """

    def test_cep_survives(self):
        clean, hits = scrub_text("Imovel no CEP 11075-350, bairro Embare")
        self.assertIn("11075-350", clean)
        self.assertEqual([], hits)

    def test_price_survives(self):
        clean, hits = scrub_text("Valor R$ 2.600,00 por mes, condominio R$ 450,00")
        self.assertIn("2.600,00", clean)
        self.assertEqual([], hits)

    def test_large_price_survives(self):
        clean, hits = scrub_text("Apartamento por R$ 1.030.000,00 a vista")
        self.assertIn("1.030.000,00", clean)
        self.assertEqual([], hits)

    def test_property_features_survive(self):
        text = "Apartamento com 50 m2, 1 quarto, 1 banheiro, 1 suite, 2 vagas"
        clean, hits = scrub_text(text)
        self.assertEqual([], hits)
        self.assertEqual(text, clean)

    def test_zap_listing_url_survives(self):
        """Zap ids are 10 digits -- must not read as a landline."""
        url = ("https://www.zapimoveis.com.br/imovel/"
               "venda-apartamento-2-quartos-boqueirao-santos-sp-68m2-id-2809851822/")
        self.assertEqual(url, scrub_url(url))

    def test_microsistec_listing_url_survives(self):
        url = "https://scimoveissantos.com.br/5989-apartamento-em-santos-bairro-embare.html"
        self.assertEqual(url, scrub_url(url))

    def test_neighborhood_names_survive(self):
        for hood in ("Gonzaga, Santos", "Ponta da Praia", "Boqueirao", "Embare"):
            with self.subTest(hood=hood):
                clean, hits = scrub_text(hood)
                self.assertEqual([], hits)
                self.assertEqual(hood, clean)


class TestRealisticDescription(unittest.TestCase):
    """The case this module exists for: PII buried in an agency description."""

    DIRTY = (
        "Excelente apartamento no Embare com 50 m2, 1 quarto e 1 suite. "
        "Valor R$ 2.600,00 mensais mais condominio de R$ 450,00. "
        "CEP 11075-350. Agende sua visita! Falar com Carlos Eduardo pelo "
        "telefone (13) 98195-9555 ou (13) 3041-5555, email "
        "socomercialimoveis@outlook.com. CRECI: 25497. "
        "WhatsApp: https://api.whatsapp.com/send?phone=5513981959555"
    )

    def test_all_pii_removed(self):
        clean, hits = scrub_text(self.DIRTY)
        for leak in ("98195", "3041-5555", "@outlook.com", "25497",
                     "5513981959555", "Carlos Eduardo"):
            self.assertNotIn(leak, clean, f"leaked: {leak}")
        self.assertGreaterEqual(len(hits), 5)

    def test_property_data_preserved(self):
        clean, _ = scrub_text(self.DIRTY)
        for keep in ("Embare", "50 m2", "1 quarto", "2.600,00", "450,00", "11075-350"):
            self.assertIn(keep, clean, f"lost: {keep}")

    def test_output_passes_the_gate(self):
        clean, _ = scrub_text(self.DIRTY)
        assert_clean([{"description_clean": clean}])  # must not raise


class TestTheGate(unittest.TestCase):

    def test_gate_blocks_dirty_records(self):
        records = [{"description_clean": "Ligue (13) 98195-9555"}]
        with self.assertRaises(PIIViolation) as ctx:
            assert_clean(records, "unit-test")
        self.assertIn("NOTHING WRITTEN", str(ctx.exception))

    def test_gate_passes_clean_records(self):
        records = [{
            "property_id": "abc123",
            "source_domain": "scimoveissantos.com.br",
            "link": "https://scimoveissantos.com.br/5989-apartamento-em-santos-bairro-embare.html",
            "description_clean": "Apartamento com 50 m2 no Embare, 1 suite.",
            "price": 2600.0,
            "amenities": ["piscina", "elevador"],
        }]
        assert_clean(records)  # must not raise

    def test_gate_scans_list_fields(self):
        records = [{"amenities": ["piscina", "contato (13) 98195-9555"]}]
        with self.assertRaises(PIIViolation):
            assert_clean(records)

    def test_gate_scans_every_field_not_just_known_ones(self):
        records = [{"unexpected_field": "email me at leak@exemplo.com"}]
        with self.assertRaises(PIIViolation):
            assert_clean(records)


class TestScrubRecord(unittest.TestCase):

    def test_scrubs_text_and_url_fields(self):
        record = {
            "description_clean": "Otimo imovel. Ligue (13) 98195-9555.",
            "link": "https://wa.me/5513981959555",
            "amenities": ["piscina", "falar com Ana Maria"],
            "price": 2600.0,
        }
        clean, hits = scrub_record(
            record, text_fields={"description_clean"}, url_fields={"link"}
        )
        self.assertIsNone(clean["link"])
        self.assertNotIn("98195", clean["description_clean"])
        self.assertNotIn("Ana Maria", " ".join(clean["amenities"]))
        self.assertEqual(2600.0, clean["price"])
        self.assertTrue(hits)
        assert_clean([clean])


if __name__ == "__main__":
    unittest.main(verbosity=2)
