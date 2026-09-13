"""
A coleta e por CIDADE, MES e QUANTIDADE.

Regras que estes testes travam:

  1. o que ja veio no mes e pulado, sempre -- nao e mais opcional
  2. `--limit N` conta anuncios INEDITOS: pedir 1.000 com 2.000 no lake
     busca 1.000 novos e chega a 3.000
  3. o recorte e o MES: coleta de agosto nao bloqueia setembro
  4. o recorte e a CIDADE: Santos nao bloqueia Sao Paulo na mesma fonte

Nenhum teste toca a rede.
"""
from __future__ import annotations

import collections
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from coleta.anuncios import adapters
from coleta.anuncios import coletar as collect
from coleta.anuncios.adapters.base import DiscoveredURL, ListingSource, Payload
from comum import lake, schema
from comum.config import parse_config
from comum.normalizer import derive_property_id

PAGINA = ('<html><script type="application/ld+json">'
          '{"@type":"Product","offers":{"@type":"Offer","price":"2600.00"}}'
          '</script></html>')


def config(**extra):
    d = {
        "version": 2,
        "sources": [{"domain": "exemplo.com.br", "platform": "fake",
                     "transactions": ["venda"]}],
        "targets": [{"state": "sp", "city": "santos"},
                    {"state": "sp", "city": "sao paulo"}],
        "scraping_params": {},
        "execution": {"dry_run": False},
    }
    d.update(extra)
    return parse_config(d)


class Fonte(ListingSource):
    """Publica N anuncios com id estavel entre corridas."""
    platform = "fake"
    N = 10

    def discover(self, domain, transaction):
        for i in range(self.N):
            yield DiscoveredURL(url=f"https://exemplo.com.br/{i}.html",
                                transaction_type=transaction,
                                discovery_path="sitemap", listing_id=str(i),
                                hints={"property_type": "apartamento"})

    def fetch(self, discovered):
        return Payload(discovered=discovered, text=PAGINA)

    def parse(self, payload):
        return {"price": "R$ 2.600,00", "area_m2": "50 m2",
                "description_clean": "Otimo apartamento"}


def semeia(raiz: str, cidade: str, data: str, ids: range) -> None:
    """Grava uma particao com os `property_id` que a Fonte produziria."""
    w = lake.LakeWriter(raw_dir=raiz, extraction_date=data)
    regs = []
    for i in ids:
        r = {c: None for c in schema.PROPERTY_FIELDS}
        r.update(property_id=derive_property_id("exemplo.com.br", str(i), None),
                 source_domain="exemplo.com.br", source_platform="fake",
                 link=f"https://exemplo.com.br/{i}.html",
                 transaction_type="venda", extraction_date=data,
                 price=500_000.0, city=cidade.title(), state="sp",
                 neighborhood="centro", property_type="apartamento")
        regs.append(r)
    w.write(regs, "sp", cidade, "fake", "exemplo.com.br")


class Base(unittest.TestCase):
    def setUp(self):
        self._salvo = dict(adapters._REGISTRY)
        adapters._REGISTRY["fake"] = Fonte
        self.tmp = TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        adapters._REGISTRY.clear(); adapters._REGISTRY.update(self._salvo)
        self.tmp.cleanup()

    def roda(self, **kw):
        w = lake.LakeWriter(raw_dir=self.raiz, extraction_date=kw.pop("hoje", "2026-09-15"))
        return collect.Collector(config(), writer=w, **kw).run()


class TestPulaOQueJaVeio(Base):

    def test_sem_nada_no_lake_coleta_tudo(self):
        r = self.roda(cidade="santos")
        self.assertEqual(len(r[0].records), 10)

    def test_segunda_corrida_no_mesmo_mes_nao_repete(self):
        """Antes de 2026-09-07 isto devolvia os mesmos 10 de novo."""
        semeia(self.raiz, "santos", "2026-09-10", range(10))
        r = self.roda(cidade="santos")
        self.assertEqual(len(r[0].records), 0)
        self.assertEqual(r[0].error, "0 rows collected")

    def test_pular_e_o_padrao_nao_precisa_de_flag(self):
        semeia(self.raiz, "santos", "2026-09-10", range(10))
        r = self.roda(cidade="santos")     # nenhuma flag de incremental
        self.assertEqual(len(r[0].records), 0)


class TestQuantidadeSaoNovas(Base):
    """`--limit N` conta INEDITOS, nao o total do mes."""

    def test_pedir_4_com_6_no_lake_traz_4_novos(self):
        semeia(self.raiz, "santos", "2026-09-10", range(6))
        r = self.roda(cidade="santos", limit=4)
        self.assertEqual(len(r[0].records), 4)
        novos = {x["property_id"] for x in r[0].records}
        antigos = {derive_property_id("exemplo.com.br", str(i), None) for i in range(6)}
        self.assertEqual(novos & antigos, set(), "recoletou o que ja tinha")

    def test_o_total_do_mes_soma(self):
        """6 no lake + 4 pedidos = 10 no mes, que e todo o catalogo."""
        semeia(self.raiz, "santos", "2026-09-10", range(6))
        self.roda(cidade="santos", limit=4)
        d = pd.concat([pd.read_parquet(p) for p in Path(self.raiz).rglob("*.parquet")])
        self.assertEqual(d.property_id.nunique(), 10)

    def test_pedir_mais_do_que_existe_traz_o_que_existe(self):
        semeia(self.raiz, "santos", "2026-09-10", range(6))
        r = self.roda(cidade="santos", limit=999)
        self.assertEqual(len(r[0].records), 4)


class TestRecorteDoMes(Base):

    def test_coleta_do_mes_anterior_nao_bloqueia(self):
        """
        Agosto nao trava setembro: preco e disponibilidade mudam, e a serie
        mensal e o que falta para medir valorizacao.
        """
        semeia(self.raiz, "santos", "2026-08-20", range(10))
        r = self.roda(cidade="santos")
        self.assertEqual(len(r[0].records), 10)

    def test_mes_explicito_muda_o_recorte(self):
        semeia(self.raiz, "santos", "2026-08-20", range(10))
        r = self.roda(cidade="santos", mes="2026-08")
        self.assertEqual(len(r[0].records), 0)

    def test_dia_diferente_do_mesmo_mes_conta(self):
        semeia(self.raiz, "santos", "2026-09-01", range(4))
        semeia(self.raiz, "santos", "2026-09-20", range(4, 7))
        r = self.roda(cidade="santos")
        self.assertEqual(len(r[0].records), 3)     # 10 - 7 ja vistos


class TestRecorteDaCidade(Base):

    def test_santos_nao_bloqueia_sao_paulo(self):
        """
        Portal nacional grava nas duas cidades. Sem o filtro, a coleta de
        Santos faria a de Sao Paulo achar que ja tinha tudo.
        """
        semeia(self.raiz, "santos", "2026-09-10", range(10))
        r = self.roda(cidade="sao paulo")
        self.assertEqual(len(r[0].records), 10)

    def test_sem_filtro_de_cidade_roda_os_dois_alvos(self):
        rs = self.roda()
        self.assertEqual(len(rs), 2)
        self.assertEqual([len(x.records) for x in rs], [10, 10])


class TestParametrosDaCorrida(Base):
    """Cidade, mes e quantidade — os tres da nova interface."""

    def test_o_mes_padrao_e_o_atual(self):
        c = collect.Collector(config())
        self.assertRegex(c.mes, r"^\d{4}-\d{2}$")
        from datetime import date
        self.assertEqual(c.mes, date.today().strftime("%Y-%m"))

    def test_os_tres_parametros_existem_na_linha_de_comando(self):
        import argparse, io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                collect.main(["--help"])
            except SystemExit:
                pass
        ajuda = buf.getvalue()
        for flag in ("--city", "--mes", "--limit"):
            self.assertIn(flag, ajuda)


if __name__ == "__main__":
    unittest.main()
