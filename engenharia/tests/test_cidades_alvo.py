"""
A lista fechada de cidades-alvo, e o portão que a aplica.

O TESTE QUE IMPORTA é `test_cidade_fora_do_alvo_nao_entra`. A regra vale na
INGESTÃO, antes de gravar: filtrar depois já teria custado requisição no site da
fonte e linha no lago, e uma regra aplicada tarde demais é indistinguível de uma
regra não aplicada.

Fixtures sintéticas.
"""
from __future__ import annotations

import unittest

from comum import cidades_alvo
from comum.cidades_alvo import CIDADES, Estado, alvo, esta_no_alvo
from comum.normalizer import Normalizer


class TestALista(unittest.TestCase):

    def test_sao_quinze(self):
        self.assertEqual(len(CIDADES), 15)

    def test_nome_e_slug_sao_unicos(self):
        self.assertEqual(len({c.nome for c in CIDADES}), 15)
        self.assertEqual(len({c.slug for c in CIDADES}), 15)

    def test_codigo_ibge_tem_sete_digitos_e_e_unico(self):
        """
        O código é a chave do CNEFE, e um errado baixa o município errado sem
        erro nenhum: a tabela sai cheia, com CEPs de outra cidade.
        """
        self.assertEqual(len({c.cod_ibge for c in CIDADES}), 15)
        for c in CIDADES:
            self.assertRegex(c.cod_ibge, r"^\d{7}$", c.nome)

    def test_coordenada_cai_no_brasil(self):
        """Latitude positiva ou longitude positiva põe a cidade em outro continente."""
        for c in CIDADES:
            self.assertTrue(-34 < c.lat < 6, f"{c.nome}: lat {c.lat}")
            self.assertTrue(-74 < c.lon < -34, f"{c.nome}: lon {c.lon}")


class TestOPredicado(unittest.TestCase):

    def test_acento_e_caixa_nao_importam(self):
        """
        A fonte escreve `São Paulo`, o caminho do lago escreve `sao-paulo`, e o
        usuário digita qualquer coisa. Comparar literalmente descartaria anúncio
        de cidade que está na lista.
        """
        for grafia in ("São Paulo", "Sao Paulo", "SÃO PAULO", "sao-paulo",
                       "  são paulo  "):
            self.assertTrue(esta_no_alvo(grafia), grafia)

    def test_cidade_fora_da_lista_e_recusada(self):
        for fora in ("Curitiba", "Águas de Lindóia", "Pardinho", ""):
            self.assertFalse(esta_no_alvo(fora), fora)

    def test_alvo_devolve_a_cidade_ou_none(self):
        self.assertEqual(alvo("guaruja").nome, "Guarujá")
        self.assertIsNone(alvo("Curitiba"))


class TestOPortaoNaIngestao(unittest.TestCase):
    """A regra é código, não recomendação."""

    def _registro(self, cidade: str) -> dict:
        return {
            "link": "https://exemplo.com.br/imovel/1",
            "price": 500000.0, "transaction_type": "venda",
            "property_type": "apartamento", "city": cidade,
            "neighborhood": "Centro", "state": "SP", "area_m2": 80.0,
        }

    class _Achado:
        """O mínimo que o normalizador lê de um achado do adapter."""
        listing_id = "1"
        url = "https://exemplo.com.br/imovel/1"
        transaction_type = "venda"
        property_type = None
        city = None
        neighborhood = None

    def test_cidade_no_alvo_entra(self):
        n = Normalizer("exemplo.com.br", "sintetico")
        self.assertIsNotNone(n.normalize(self._registro("Santos"), self._Achado()))
        self.assertEqual(n.stats.kept, 1)

    def test_cidade_fora_do_alvo_nao_entra(self):
        """
        E é CONTADA, com motivo próprio. Descarte silencioso faria a queda de
        volume parecer queda da fonte.
        """
        n = Normalizer("exemplo.com.br", "sintetico")
        self.assertIsNone(n.normalize(self._registro("Curitiba"), self._Achado()))
        self.assertEqual(n.stats.kept, 0)
        self.assertEqual(n.stats.rejected["cidade_fora_do_alvo"], 1)

    def test_cidade_vizinha_na_lista_passa(self):
        """
        Plataforma regional devolve imóvel de cidade vizinha, e isso não é erro
        enquanto a vizinha estiver na lista: o Guarujá entrou pela coleta
        apontada para Santos. O filtro é por cidade-alvo, não por cidade pedida.
        """
        n = Normalizer("exemplo.com.br", "sintetico")
        self.assertIsNotNone(n.normalize(self._registro("Guarujá"), self._Achado()))


class TestResumo(unittest.TestCase):

    def test_os_estados_somam_o_total(self):
        r = cidades_alvo.resumo()
        soma = r["coletando"] + r["inventariando"] + r["a_inventariar"]
        self.assertEqual(soma, r["total"])

    def test_codigos_ibge_do_cnefe_sao_os_da_lista(self):
        self.assertEqual(set(cidades_alvo.codigos_ibge()),
                         {c.cod_ibge for c in CIDADES})


if __name__ == "__main__":
    unittest.main()
