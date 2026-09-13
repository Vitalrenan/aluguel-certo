"""
Collector orchestration and the lake write (plan sections 3.1, 7, 7.1, 10).

No network: the fake adapters below override `fetch()` and serve pages from a
dict. That is also how a P3 adapter should be tested against the definition of
done -- section 10 says the rate limit is verified against a local server, not
the live site.

The behaviours worth protecting here are the failure ones. A run that writes
zero rows must not report success; one dead source must not abort the other
fifteen; and a PII hit must reach the top and stop everything.
"""
import os
import shutil
import sys
import tempfile
import collections
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coleta.anuncios import adapters  # noqa: E402
from coleta.anuncios import coletar as collect  # noqa: E402
from coleta.anuncios.adapters.base import DiscoveredURL, ListingSource, Payload  # noqa: E402
from comum import lake  # noqa: E402
from comum.config import parse_config  # noqa: E402
from comum.privacy import PIIViolation  # noqa: E402
from comum.schema import PROPERTY_FIELDS  # noqa: E402

PAGE = ('<html><script type="application/ld+json">'
        '{"@type":"Product","offers":{"@type":"Offer","price":"2600.00"}}'
        '</script></html>')


def config(platform="fake", **params):
    params.pop("budget", None)      # o teto do config foi removido (2026-09-07)
    data = {
        "version": 2,
        "sources": [{"domain": "exemplo.com.br", "platform": platform}],
        "targets": [{"state": "sp", "city": "santos"}],
        "scraping_params": {},
        "execution": {"dry_run": params.pop("dry_run", True)},
    }
    data.update(params)
    return parse_config(data)


class _Base(ListingSource):
    """Serves synthetic pages; never touches the network."""
    pages = {}

    def discover(self, domain, transaction):
        for i, url in enumerate(self.pages):
            yield DiscoveredURL(url=url, transaction_type=transaction,
                                discovery_path=f"sitemap:{transaction}",
                                listing_id=str(i),
                                hints={"property_type": "apartamento"})

    def fetch(self, discovered):
        text = self.pages.get(discovered.url)
        return None if text is None else Payload(discovered=discovered, text=text)

    def parse(self, payload):
        return {"price": "R$ 2.600,00", "description_clean": "Otimo apartamento",
                "area_m2": "50 m2"}


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self._saved = dict(adapters._REGISTRY)

    def tearDown(self):
        adapters._REGISTRY.clear()
        adapters._REGISTRY.update(self._saved)

    def register(self, cls, platform):
        cls.platform = platform
        return adapters.register(cls)


class TestCollector(RegistryFixture):
    def test_happy_path(self):
        class A(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE,
                     "https://exemplo.com.br/2.html": PAGE}
        self.register(A, "fake")

        c = collect.Collector(config())
        results = c.run()
        self.assertEqual(len(results), 1)
        # two pages x two transactions
        self.assertEqual(len(results[0].records), 4)
        self.assertTrue(results[0].ok)

    def test_transaction_labels_come_from_the_discovery_path(self):
        class A(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE}
        self.register(A, "fake")

        records = collect.Collector(config()).run()[0].records
        self.assertEqual({r["transaction_type"] for r in records}, {"venda", "locacao"})

    def test_unregistered_platform_is_skipped_not_failed(self):
        """Most configured sources are waiting on an adapter. That is normal."""
        result = collect.Collector(config(platform="nao_existe")).run()[0]
        self.assertIsNotNone(result.skipped)
        self.assertIsNone(result.error)

    def test_missing_listing_is_not_an_error(self):
        class A(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE}
            def fetch(self, discovered):
                return None      # every listing 410s, as Universal's do
        self.register(A, "fake")

        result = collect.Collector(config()).run()[0]
        self.assertEqual(result.records, [])
        self.assertEqual(result.error, "0 rows collected")

    def test_one_bad_page_does_not_lose_the_run(self):
        class A(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE,
                     "https://exemplo.com.br/2.html": PAGE}
            def parse(self, payload):
                if payload.url.endswith("2.html"):
                    raise ValueError("markup changed")
                return {"price": "1000"}
        self.register(A, "fake")

        result = collect.Collector(config()).run()[0]
        self.assertEqual(len(result.records), 2)
        self.assertTrue(any("parse_error" in k for k in result.stats.rejected))

    def test_limite_vale_POR_TRANSACAO(self):
        """
        `--limit 3` com duas transacoes declaradas devolve 3 de CADA, nao 3 no
        total.

        Antes era um contador so para todas: a venda consumia o orcamento
        inteiro e a locacao nunca rodava -- sem aviso. Custou uma corrida de
        duas horas em 2026-09-06, que trouxe 500 anuncios de venda e zero de
        locacao quando a fonte tinha 14.889 aluguéis em Sao Paulo.
        """
        class A(_Base):
            pages = {f"https://exemplo.com.br/{i}.html": PAGE for i in range(10)}
        self.register(A, "fake")

        result = collect.Collector(config(), limit=3).run()[0]
        por_transacao = collections.Counter(r["transaction_type"] for r in result.records)
        self.assertEqual(dict(por_transacao), {"venda": 3, "locacao": 3})

    def test_nenhuma_transacao_fica_de_fora(self):
        """A segunda transacao nao pode ser engolida pela primeira."""
        class A(_Base):
            pages = {f"https://exemplo.com.br/{i}.html": PAGE for i in range(10)}
        self.register(A, "fake")

        result = collect.Collector(config(), limit=2).run()[0]
        vistas = {r["transaction_type"] for r in result.records}
        self.assertEqual(vistas, {"venda", "locacao"})

    def test_sem_limite_nao_ha_teto(self):
        """
        Sem `--limit`, coleta tudo que a fonte oferecer.

        O `sources.yaml` tinha `max_listings_per_source: 5000`, um teto que
        ninguem escolheu e que nao aparecia em lugar nenhum da saida. Removido
        em 2026-09-07: limite e decisao do operador, nao default escondido.
        """
        class A(_Base):
            pages = {f"https://exemplo.com.br/{i}.html": PAGE for i in range(10)}
        self.register(A, "fake")

        result = collect.Collector(config()).run()[0]
        self.assertEqual(len(result.records), 20)      # 10 paginas x 2 transacoes

    def test_config_nao_aceita_mais_teto_escondido(self):
        """Mesmo que alguem reponha a chave no YAML, ela nao vira teto."""
        cfg = config()
        self.assertFalse(hasattr(cfg, "max_listings_per_source"))

    def test_adapter_that_cannot_label_a_transaction_fails_its_source_only(self):
        """DiscoveredURL refuses the URL; the source fails, the run goes on."""
        class Bad(_Base):
            def discover(self, domain, transaction):
                yield DiscoveredURL(url="https://exemplo.com.br/1.html",
                                    transaction_type=None, discovery_path="sitemap")
        self.register(Bad, "fake")

        result = collect.Collector(config()).run()[0]
        self.assertIn("AdapterError", result.error)

    def test_pii_from_an_adapter_reaches_the_top(self):
        class Leaky(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE}
            def parse(self, payload):
                raise PIIViolation("simulated gate failure")
        self.register(Leaky, "fake")

        with self.assertRaises(PIIViolation):
            collect.Collector(config()).run()

    def test_report_mentions_every_source(self):
        c = collect.Collector(config(platform="nao_existe"))
        c.run()
        self.assertIn("exemplo.com.br", c.report())


class TestExitCodes(RegistryFixture):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.dir, "sources.yaml")
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write(
                "version: 2\n"
                "sources:\n"
                "  - {domain: exemplo.com.br, platform: fake}\n"
                "targets:\n"
                "  - {state: sp, city: santos}\n"
                "execution:\n  dry_run: true\n"
            )

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_zero_rows_exits_non_zero(self):
        """The Cloudflare failure: reported success, wrote nothing."""
        self.assertEqual(collect.main(["--config", self.cfg_path]),
                         collect.EXIT_NOTHING_COLLECTED)

    def test_successful_run_exits_zero(self):
        class A(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE}
        self.register(A, "fake")
        self.assertEqual(collect.main(["--config", self.cfg_path]), collect.EXIT_OK)

    def test_pii_exits_two_and_is_reported(self):
        class Leaky(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE}
            def parse(self, payload):
                raise PIIViolation("simulated")
        self.register(Leaky, "fake")
        self.assertEqual(collect.main(["--config", self.cfg_path]), collect.EXIT_PII)


class TestWriteOverrides(RegistryFixture):
    """
    --write and --raw-dir are the only ways a real write happens.

    The config ships with dry_run: true, so persistence is an explicit act at
    the command line, never a side effect of running the collector.
    """

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "lake")
        self.cfg_path = os.path.join(self.dir, "sources.yaml")
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write(
                "version: 2\n"
                "sources:\n"
                "  - {domain: exemplo.com.br, platform: fake}\n"
                "targets:\n"
                "  - {state: sp, city: santos}\n"
                "execution:\n  dry_run: true\n"
            )

        class A(_Base):
            pages = {"https://exemplo.com.br/1.html": PAGE}
        self.register(A, "fake")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, *extra):
        return collect.main(["--config", self.cfg_path, "--raw-dir", self.out, *extra])

    def test_config_dry_run_writes_nothing_by_default(self):
        self.assertEqual(self._run(), collect.EXIT_OK)
        self.assertFalse(os.path.exists(self.out))

    def test_write_flag_overrides_config_dry_run(self):
        self.assertEqual(self._run("--write"), collect.EXIT_OK)
        self.assertTrue(os.path.exists(self.out))

    def test_dry_run_beats_write_whatever_the_order(self):
        """The safe flag is never overridable by the unsafe one."""
        self.assertEqual(self._run("--write", "--dry-run"), collect.EXIT_OK)
        self.assertFalse(os.path.exists(self.out))

    def test_raw_dir_override_places_the_partition(self):
        self._run("--write")
        found = [os.path.join(root, f)
                 for root, _, files in os.walk(self.out) for f in files]
        self.assertEqual(len(found), 1)
        self.assertIn(os.path.join("sp", "santos"), found[0])
        self.assertIn("fake", found[0])


class TestLakeWriter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def record(self, **over):
        rec = {f: None for f in PROPERTY_FIELDS}
        rec.update({"property_id": "abc123", "source_domain": "exemplo.com.br",
                    "source_platform": "fake", "link": "https://exemplo.com.br/1.html",
                    "transaction_type": "locacao", "extraction_date": "2026-08-25",
                    "price": 2600.0, "amenities": ["piscina"]})
        rec.update(over)
        return rec

    def test_path_follows_the_new_contract(self):
        """.../{state}/{city}/{year}/{month}/{day}/{platform}/{domain}/"""
        w = lake.LakeWriter(raw_dir="gs://bucket/01_raw/listings",
                            extraction_date="2026-08-25")
        self.assertEqual(
            w.path_for("SP", "São Vicente", "microsistec_a", "casabellaimoveis.com"),
            "gs://bucket/01_raw/listings/sp/sao-vicente/2026/08/25/"
            "microsistec_a/casabellaimoveis.com/listings.parquet")

    def test_o_dia_e_segmento_proprio_nao_sufixo(self):
        """
        Duas coletas do mesmo mes caem em diretorios irmaos, e o mes as
        contem. E o que deixa o estagio de tratamento ler agosto inteiro
        abrindo um diretorio, em vez de varrer o lago filtrando nomes.
        """
        a = lake.LakeWriter(raw_dir="/lago", extraction_date="2026-08-27")
        b = lake.LakeWriter(raw_dir="/lago", extraction_date="2026-08-29")
        pa = a.partition("SP", "Santos", "universal", "exemplo.com.br")
        pb = b.partition("SP", "Santos", "universal", "exemplo.com.br")
        self.assertNotEqual(pa, pb)
        self.assertTrue(pa.startswith("/lago/sp/santos/2026/08/"))
        self.assertTrue(pb.startswith("/lago/sp/santos/2026/08/"))

    def test_dry_run_writes_nothing(self):
        w = lake.LakeWriter(raw_dir=self.dir, dry_run=True)
        self.assertIsNone(w.write([self.record()], "sp", "santos", "fake", "exemplo.com.br"))
        self.assertEqual(os.listdir(self.dir), [])

    def test_zero_rows_refused(self):
        w = lake.LakeWriter(raw_dir=self.dir)
        with self.assertRaises(lake.WriteFailed):
            w.write([], "sp", "santos", "fake", "exemplo.com.br")

    def test_gate_runs_again_at_the_write(self):
        w = lake.LakeWriter(raw_dir=self.dir)
        leaky = self.record(description_clean="Falar com Joao Silva 13981959555")
        with self.assertRaises(PIIViolation):
            w.write([leaky], "sp", "santos", "fake", "exemplo.com.br")

    def test_real_write_produces_the_allowlist_columns(self):
        import pandas as pd
        w = lake.LakeWriter(raw_dir=self.dir, extraction_date="2026-08-25")
        path = w.write([self.record()], "sp", "santos", "fake", "exemplo.com.br")
        df = pd.read_parquet(path)
        self.assertEqual(list(df.columns), sorted(PROPERTY_FIELDS))
        self.assertEqual(df.iloc[0]["price"], 2600.0)

    def test_write_failure_raises_rather_than_printing(self):
        """SDD D-13: DataManager printed the exception and returned."""
        w = lake.LakeWriter(raw_dir="/nonexistent\x00path")
        with self.assertRaises(lake.WriteFailed):
            w.write([self.record()], "sp", "santos", "fake", "exemplo.com.br")


if __name__ == "__main__":
    unittest.main()
