"""
Availability per (city, target) pair, and the refusal that carries a reason.

THE TEST THAT MATTERS MOST is `test_par_indisponivel_recusa_com_motivo`. There
is no pooled fallback: a pair with no model must refuse rather than answer with
a number borrowed from another city. A borrowed number arrives looking exactly
like a measured one, and nothing downstream can tell them apart.

Fixtures are synthetic.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from api import resolucao

CIDADES_YAML = """
versao: 1
cidades:
  - slug: santos
    nome: Santos
    uf: SP
    alvos:
      venda:   {disponivel: true, n_treino_medido: 5819}
      locacao: {disponivel: true, n_treino_medido: 806}
  - slug: sao-paulo
    nome: São Paulo
    uf: SP
    alvos:
      venda:   {disponivel: true, n_treino_medido: 5638}
      locacao: {disponivel: false, n_treino_medido: 2,
                motivo: "modelo estatístico ainda não disponível"}
  - slug: guaruja
    nome: Guarujá
    uf: SP
    alvos:
      venda:   {disponivel: false, n_treino_medido: 370,
                motivo: "modelo estatístico ainda não disponível"}
"""


def monta_modelo(raiz: Path, ano: str, mes: str, cidade: str, alvo: str,
                 com_cartao: bool = True):
    pasta = raiz / ano / mes / cidade / alvo
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "modelo.txt").write_text("booster sintetico", encoding="utf-8")
    if com_cartao:
        (pasta / "model_card.json").write_text(json.dumps({
            "identificacao": {"cidade": cidade, "alvo": alvo,
                              "ano": ano, "mes": mes, "versao": f"{ano}-{mes}-a"},
            "escopo": {"escopo_treino": "cidade", "n_treino": 100},
        }), encoding="utf-8")
    return pasta


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cidades = self.raiz / "cidades.yaml"
        self.cidades.write_text(CIDADES_YAML, encoding="utf-8")
        self.modelos = self.raiz / "modelos"
        resolucao.catalogo.cache_clear()
        self.addCleanup(resolucao.catalogo.cache_clear)


class TestCatalogo(Base):

    def test_le_todos_os_pares_inclusive_os_indisponiveis(self):
        cat = resolucao.catalogo(str(self.cidades))
        self.assertEqual(len(cat), 5)
        self.assertEqual(sum(1 for p in cat if p.disponivel), 3)

    def test_disponibilidade_e_por_par_e_nao_por_cidade(self):
        """
        São Paulo tem venda e nao tem locacao. Tratar a cidade como disponivel
        inteira faz a tela oferecer aluguel e receber recusa depois de o
        usuario preencher o formulario todo.
        """
        cat = resolucao.catalogo(str(self.cidades))
        venda = resolucao.procura(cat, "São Paulo", "venda")
        locacao = resolucao.procura(cat, "São Paulo", "locacao")
        self.assertTrue(venda.disponivel)
        self.assertFalse(locacao.disponivel)

    def test_acento_e_caixa_nao_impedem_achar_a_cidade(self):
        cat = resolucao.catalogo(str(self.cidades))
        for grafia in ("São Paulo", "Sao Paulo", "sao paulo", "SAO PAULO"):
            self.assertIsNotNone(resolucao.procura(cat, grafia, "venda"),
                                 f"{grafia} devia achar a cidade")


class TestResolucao(Base):

    def test_par_disponivel_com_modelo_resolve(self):
        monta_modelo(self.modelos, "2026", "09", "santos", "venda")
        pasta, cartao = resolucao.resolve(
            self.modelos, str(self.cidades), "Santos", "venda")
        self.assertTrue(pasta.exists())
        self.assertEqual(cartao["escopo"]["escopo_treino"], "cidade")

    def test_par_indisponivel_recusa_com_motivo(self):
        """
        SEM MODELO AGRUPADO DE RESERVA. O par recusa com o texto que a tela
        mostra, em vez de devolver numero de outra cidade.
        """
        monta_modelo(self.modelos, "2026", "09", "santos", "venda")
        with self.assertRaises(resolucao.SemModelo) as ctx:
            resolucao.resolve(self.modelos, str(self.cidades),
                              "São Paulo", "locacao")
        self.assertIn("ainda não disponível", ctx.exception.motivo)

    def test_config_vence_o_disco(self):
        """
        Desligar uma cidade no config tem de desliga-la de fato, mesmo que um
        diretorio antigo continue no disco.
        """
        monta_modelo(self.modelos, "2026", "09", "guaruja", "venda")
        with self.assertRaises(resolucao.SemModelo):
            resolucao.resolve(self.modelos, str(self.cidades),
                              "Guarujá", "venda")

    def test_cidade_fora_do_catalogo_recusa(self):
        with self.assertRaises(resolucao.SemModelo) as ctx:
            resolucao.resolve(self.modelos, str(self.cidades),
                              "Curitiba", "venda")
        self.assertIn("fora do escopo", ctx.exception.motivo)

    def test_disponivel_no_config_e_ausente_no_disco_culpa_a_operacao(self):
        with self.assertRaises(resolucao.SemModelo) as ctx:
            resolucao.resolve(self.modelos, str(self.cidades),
                              "Santos", "venda")
        self.assertIn("publicacao do modelo falhou", ctx.exception.motivo)


class TestVersaoMaisRecente(Base):

    def test_pega_o_mes_mais_recente(self):
        monta_modelo(self.modelos, "2026", "07", "santos", "venda")
        monta_modelo(self.modelos, "2026", "09", "santos", "venda")
        monta_modelo(self.modelos, "2026", "08", "santos", "venda")
        pasta = resolucao.pasta_do_modelo(self.modelos, "Santos", "venda")
        self.assertEqual(pasta.parent.parent.name, "09")

    def test_diretorio_sem_cartao_e_pulado_e_nao_carregado(self):
        """
        Um diretorio com booster e sem cartao seria servido sem saber declarar
        o proprio escopo. Pular e voltar ao mes anterior e mais honesto que
        responder sem proveniencia.
        """
        monta_modelo(self.modelos, "2026", "08", "santos", "venda")
        monta_modelo(self.modelos, "2026", "09", "santos", "venda",
                     com_cartao=False)
        pasta = resolucao.pasta_do_modelo(self.modelos, "Santos", "venda")
        self.assertEqual(pasta.parent.parent.name, "08")


if __name__ == "__main__":
    unittest.main()
