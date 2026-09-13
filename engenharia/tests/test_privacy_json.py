"""
O detector de CRECI precisa enxergar a forma JSON.

Existe porque o inventario de lopes.com.br (2026-09-06) mostrou que o
payload do anuncio traz `"creci": "329568-F"` -- e a regex nao casava,
porque `"` nao estava na classe de separadores entre rotulo e digitos.

O allowlist do schema ja barrava esse caso (o campo `agents` nunca vira
coluna), entao isto e a segunda camada. Mas um gate cego a uma forma real
de PII nao pode ficar de pe so porque a primeira camada segurou.
"""
from __future__ import annotations

import unittest

from comum import privacy


class TestCreciEmJson(unittest.TestCase):

    def casa(self, s: str) -> bool:
        return bool(privacy.CRECI.search(s))

    def test_forma_json_com_aspas(self):
        """A forma exata medida em lopes.com.br."""
        self.assertTrue(self.casa('"creci": "329568-F"'))

    def test_forma_json_sem_espaco(self):
        self.assertTrue(self.casa('{"creci":"329568-F"}'))

    def test_forma_json_com_uf(self):
        self.assertTrue(self.casa('"creci": "SP 12345"'))

    def test_aspas_simples(self):
        self.assertTrue(self.casa("'creci': '44569'"))

    def test_formas_antigas_continuam_valendo(self):
        """A correcao nao pode quebrar o que ja era detectado."""
        for s in ("CRECI: 25497", "CRECI 44569", "CRECI-SP 12345-F",
                  "creci 12345", "Creci: 98765-J"):
            with self.subTest(s=s):
                self.assertTrue(self.casa(s))

    def test_nao_dispara_em_prosa_sem_numero(self):
        self.assertFalse(self.casa("o corretor tem CRECI ativo"))


class TestGateComPayloadReal(unittest.TestCase):
    """A forma do payload de lopes.com.br, reduzida e sintetica."""

    PAYLOAD = ('{"product":{"sku":"REO1","description":"Apartamento amplo",'
               '"agents":[{"name":"Fulana De Tal","creci":"329568-F"}]}}')

    def test_detector_ve_o_creci_no_payload(self):
        achados = [k for k, rx in privacy.DETECTORS if rx.search(self.PAYLOAD)]
        self.assertIn("creci", achados)

    def test_gate_recusa_se_o_creci_chegar_a_um_campo(self):
        """
        Se algum dia o campo escapar da projecao, o gate tem de levantar.
        Este e o cenario que a correcao protege.
        """
        registro = {"property_id": "abc", "link": "https://x.com/i/1",
                    "description_clean": 'contato "creci": "329568-F"'}
        with self.assertRaises(privacy.PIIViolation):
            privacy.assert_clean([registro], "teste")


if __name__ == "__main__":
    unittest.main()
