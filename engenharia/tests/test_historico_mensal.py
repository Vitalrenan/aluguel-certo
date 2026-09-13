"""
The monthly history table. Fixtures are synthetic -- real pages carry live
phone numbers.

THE TEST THAT PROTECTS THE SERIES is `test_dois_meses_do_mesmo_imovel_sao_duas_linhas`.
A dedup keyed on `property_id` without the month returns a clean table, deletes
the temporal series, and raises nothing. Nothing else in the stage would catch
it: the row count merely looks smaller, which is what deduplication is supposed
to do.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from tratamento.anuncios import historico, particoes


def linha(pid, preco, data, cidade="Santos", **extra):
    """One synthetic listing observation."""
    base = {
        "property_id": pid,
        "price": preco,
        "extraction_date": data,
        "city": cidade,
        "state": "SP",
        "transaction_type": "venda",
        "source_domain": "exemplo.com.br",
        "source_platform": "sintetico",
        "area_m2": 80.0,
        "bedrooms": 2,
    }
    base.update(extra)
    return base


def grava_particao(raiz: Path, data: str, registros, cidade="santos",
                   dominio="exemplo.com.br", plataforma="sintetico"):
    """Write one day partition at the ano/mes/dia path."""
    ano, mes, dia = data.split("-")
    pasta = raiz / "sp" / cidade / ano / mes / dia / plataforma / dominio
    pasta.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(registros).to_parquet(pasta / "listings.parquet", index=False)


class TestParticoes(unittest.TestCase):
    """The path is {uf}/{cidade}/{ano}/{mes}/{dia}/{plataforma}/{dominio}/."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_acha_os_dias_do_mes(self):
        for dia in ("2026-08-27", "2026-08-28", "2026-08-29"):
            grava_particao(self.raiz, dia, [linha("a1", 500000.0, dia)])
        achadas = particoes.do_mes(self.raiz, "2026-08")
        self.assertEqual(len(achadas), 3)
        self.assertEqual([p.dia for p in achadas], ["27", "28", "29"])

    def test_nao_mistura_meses(self):
        grava_particao(self.raiz, "2026-08-29", [linha("a1", 500000.0, "2026-08-29")])
        grava_particao(self.raiz, "2026-09-02", [linha("a1", 510000.0, "2026-09-02")])
        self.assertEqual(len(particoes.do_mes(self.raiz, "2026-08")), 1)
        self.assertEqual(len(particoes.do_mes(self.raiz, "2026-09")), 1)
        self.assertEqual(particoes.meses(self.raiz), ["2026-08", "2026-09"])

    def test_arquivo_fora_do_formato_e_ignorado_sem_levantar(self):
        solto = self.raiz / "sp" / "santos" / "lixo.parquet"
        solto.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([linha("x", 1.0, "2026-08-01")]).to_parquet(solto, index=False)
        self.assertEqual(particoes.do_mes(self.raiz, "2026-08"), [])


class TestColapsoDentroDoMes(unittest.TestCase):
    """Days of one month are one listing, not several."""

    def test_tres_dias_viram_uma_linha(self):
        obs = [linha("a1", 500000.0, "2026-08-27"),
               linha("a1", 500000.0, "2026-08-28"),
               linha("a1", 490000.0, "2026-08-29")]
        d = historico.colapsa_mes(pd.DataFrame(obs), "2026-08")
        self.assertEqual(len(d), 1)
        self.assertEqual(int(d.n_dias_observado.iloc[0]), 3)

    def test_guarda_o_primeiro_e_o_ultimo_preco(self):
        obs = [linha("a1", 500000.0, "2026-08-27"),
               linha("a1", 490000.0, "2026-08-29")]
        d = historico.colapsa_mes(pd.DataFrame(obs), "2026-08")
        self.assertEqual(float(d.preco_primeiro.iloc[0]), 500000.0)
        self.assertEqual(float(d.preco_ultimo.iloc[0]), 490000.0)
        self.assertTrue(bool(d.preco_mudou_no_mes.iloc[0]))

    def test_preco_estavel_nao_marca_mudanca(self):
        obs = [linha("a1", 500000.0, "2026-08-27"),
               linha("a1", 500000.0, "2026-08-29")]
        d = historico.colapsa_mes(pd.DataFrame(obs), "2026-08")
        self.assertFalse(bool(d.preco_mudou_no_mes.iloc[0]))

    def test_a_linha_que_sobra_e_inteira_de_um_dia_so(self):
        """
        Atributo e preco vem da MESMA observacao. Pegar o ultimo valor
        coluna a coluna montaria um imovel que nunca existiu: area de um dia,
        preco de outro.
        """
        obs = [linha("a1", 500000.0, "2026-08-27", area_m2=80.0, bedrooms=2),
               linha("a1", 490000.0, "2026-08-29", area_m2=75.0, bedrooms=3)]
        d = historico.colapsa_mes(pd.DataFrame(obs), "2026-08")
        self.assertEqual(float(d.area_m2.iloc[0]), 75.0)
        self.assertEqual(int(d.bedrooms.iloc[0]), 3)
        self.assertEqual(float(d.price.iloc[0]), 490000.0)


class TestSerieEntreMeses(unittest.TestCase):
    """
    A regra invertida. O colapso vale DENTRO do mes e nunca entre meses.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_dois_meses_do_mesmo_imovel_sao_duas_linhas(self):
        grava_particao(self.raiz, "2026-08-29",
                       [linha("a1", 500000.0, "2026-08-29")])
        grava_particao(self.raiz, "2026-09-15",
                       [linha("a1", 520000.0, "2026-09-15")])

        d, _ = historico.constroi(self.raiz, ["2026-08", "2026-09"])

        self.assertEqual(len(d), 2, "o mesmo imovel em dois meses e a SERIE, "
                                    "nao duplicata")
        self.assertEqual(sorted(d.mes_referencia), ["2026-08", "2026-09"])
        precos = dict(zip(d.mes_referencia, d.price))
        self.assertEqual(float(precos["2026-08"]), 500000.0)
        self.assertEqual(float(precos["2026-09"]), 520000.0)

    def test_o_agosto_nao_e_sobrescrito_pelo_setembro(self):
        """
        O modo de falha do codigo anterior, isolado: ele mantinha a linha mais
        recente ENTRE meses, e agosto sumia sem erro.
        """
        grava_particao(self.raiz, "2026-08-29",
                       [linha("a1", 500000.0, "2026-08-29")])
        grava_particao(self.raiz, "2026-09-15",
                       [linha("a1", 520000.0, "2026-09-15")])
        d, _ = historico.constroi(self.raiz, ["2026-08", "2026-09"])
        agosto = d[d.mes_referencia == "2026-08"]
        self.assertEqual(len(agosto), 1)
        self.assertEqual(float(agosto.price.iloc[0]), 500000.0)


class TestDuplicataDentroDoMes(unittest.TestCase):
    """O mesmo imovel em duas fontes, no mesmo mes, e uma linha."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_duas_agencias_no_mesmo_mes_viram_uma_linha(self):
        grava_particao(self.raiz, "2026-08-27",
                       [linha("a1", 500000.0, "2026-08-27")],
                       dominio="agencia-a.com.br")
        grava_particao(self.raiz, "2026-08-28",
                       [linha("a1", 500000.0, "2026-08-28")],
                       dominio="agencia-b.com.br")
        d, _ = historico.constroi(self.raiz, ["2026-08"])
        self.assertEqual(len(d), 1)

    def test_imoveis_diferentes_nao_colapsam(self):
        grava_particao(self.raiz, "2026-08-27",
                       [linha("a1", 500000.0, "2026-08-27"),
                        linha("a2", 600000.0, "2026-08-27")])
        d, _ = historico.constroi(self.raiz, ["2026-08"])
        self.assertEqual(len(d), 2)


class TestFalhaNaoEhSilenciosa(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_gravar_zero_linhas_levanta(self):
        with self.assertRaises(historico.HistoricoVazio):
            historico.grava(pd.DataFrame(), self.raiz / "saida")

    def test_mes_sem_particao_devolve_vazio_e_nao_inventa(self):
        d, relato = historico.constroi(self.raiz, ["2026-08"])
        self.assertEqual(len(d), 0)
        self.assertEqual(relato, {})

    def test_particao_sem_coluna_de_cidade_levanta_na_escrita(self):
        d = pd.DataFrame([{"property_id": "a1", "ano": "2026", "mes": "08"}])
        with self.assertRaises(historico.HistoricoVazio):
            historico.grava(d, self.raiz / "saida")


class TestEscrita(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_um_arquivo_por_cidade_por_mes(self):
        grava_particao(self.raiz, "2026-08-29",
                       [linha("a1", 500000.0, "2026-08-29")])
        grava_particao(self.raiz, "2026-09-15",
                       [linha("a1", 520000.0, "2026-09-15")])
        d, _ = historico.constroi(self.raiz, ["2026-08", "2026-09"])

        saida = self.raiz / "saida"
        escritos = historico.grava(d, saida)
        self.assertEqual(len(escritos), 2)
        self.assertTrue((saida / "sp" / "santos" / "ano=2026" / "mes=08"
                         / "historico.parquet").exists())
        self.assertTrue((saida / "sp" / "santos" / "ano=2026" / "mes=09"
                         / "historico.parquet").exists())

    def test_regravar_um_mes_nao_toca_o_outro(self):
        grava_particao(self.raiz, "2026-08-29",
                       [linha("a1", 500000.0, "2026-08-29")])
        grava_particao(self.raiz, "2026-09-15",
                       [linha("a1", 520000.0, "2026-09-15")])
        saida = self.raiz / "saida"
        d, _ = historico.constroi(self.raiz, ["2026-08", "2026-09"])
        historico.grava(d, saida)

        agosto = saida / "sp" / "santos" / "ano=2026" / "mes=08" / "historico.parquet"
        antes = agosto.read_bytes()

        so_setembro, _ = historico.constroi(self.raiz, ["2026-09"])
        historico.grava(so_setembro, saida)

        self.assertEqual(agosto.read_bytes(), antes,
                         "reconstruir setembro nao pode reescrever agosto")


if __name__ == "__main__":
    unittest.main()


class TestColisaoDeCaminho(unittest.TestCase):
    """
    Grafias diferentes da mesma cidade dao o MESMO diretorio. Agrupar pelo
    valor cru gerava varios grupos apontando para um arquivo so, e o ultimo a
    gravar apagava os anteriores -- 8.108 de 14.842 linhas, sem erro nenhum.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_grafias_da_mesma_cidade_nao_se_sobrescrevem(self):
        d = pd.DataFrame([
            dict(linha("a1", 500000.0, "2026-08-27"), state="SP", city="Santos",
                 ano="2026", mes="08"),
            dict(linha("a2", 600000.0, "2026-08-27"), state="sp", city="santos",
                 ano="2026", mes="08"),
            dict(linha("a3", 700000.0, "2026-08-27"), state="sp", city="Santos",
                 ano="2026", mes="08"),
        ])
        saida = self.raiz / "saida"
        escritos = historico.grava(d, saida)

        self.assertEqual(len(escritos), 1, "as tres grafias sao um arquivo so")
        gravado = pd.read_parquet(escritos[0])
        self.assertEqual(len(gravado), 3,
                         "nenhuma das tres linhas pode ter sido sobrescrita")
        self.assertEqual(sorted(gravado.property_id), ["a1", "a2", "a3"])

    def test_acento_na_cidade_nao_cria_caminho_paralelo(self):
        d = pd.DataFrame([
            dict(linha("g1", 500000.0, "2026-08-27"), state="SP", city="Guarujá",
                 ano="2026", mes="08"),
            dict(linha("g2", 600000.0, "2026-08-27"), state="sp", city="Guaruja",
                 ano="2026", mes="08"),
        ])
        escritos = historico.grava(d, self.raiz / "saida")
        self.assertEqual(len(escritos), 1)
        self.assertEqual(len(pd.read_parquet(escritos[0])), 2)
