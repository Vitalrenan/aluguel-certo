"""
A gravação funde com a partição existente em vez de sobrescrever.

Existe porque o caminho é `listings_{data}.parquet` — um arquivo por dia por
fonte — e até 2026-09-05 a segunda corrida do dia apagava a primeira.

O risco vira certeza junto com `--novos-apenas`: a corrida pula o que já está
no lake e grava só os inéditos, destruindo exatamente o que pulou. Uma coleta
de 988 apartamentos de São Paulo quase foi perdida assim.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from comum import lake, schema


def linha(pid, preco=500_000.0, **kw):
    d = {c: None for c in schema.PROPERTY_FIELDS}
    d.update(property_id=pid, source_domain="x.com.br", source_platform="p",
             link=f"https://x.com.br/{pid}", transaction_type="venda",
             extraction_date="2026-09-05", price=preco, city="Santos",
             state="sp", neighborhood="gonzaga", property_type="apartamento")
    d.update(kw)
    return d


class TestFusao(unittest.TestCase):

    def escreve(self, raiz, registros):
        w = lake.LakeWriter(raw_dir=str(raiz))
        return w.write(registros, "sp", "santos", "p", "x.com.br")

    def test_segunda_corrida_nao_apaga_a_primeira(self):
        with TemporaryDirectory() as t:
            self.escreve(t, [linha("a"), linha("b")])
            p = self.escreve(t, [linha("c")])
            d = pd.read_parquet(p)
            self.assertEqual(sorted(d.property_id), ["a", "b", "c"])

    def test_property_id_repetido_fica_com_a_linha_nova(self):
        """Preço muda entre coletas; a mais recente é a verdadeira."""
        with TemporaryDirectory() as t:
            self.escreve(t, [linha("a", preco=500_000.0)])
            p = self.escreve(t, [linha("a", preco=560_000.0)])
            d = pd.read_parquet(p)
            self.assertEqual(len(d), 1)
            self.assertEqual(d.price.iloc[0], 560_000.0)

    def test_particao_nova_grava_normalmente(self):
        with TemporaryDirectory() as t:
            p = self.escreve(t, [linha("a")])
            self.assertEqual(len(pd.read_parquet(p)), 1)

    def test_colunas_seguem_o_allowlist_apos_a_fusao(self):
        with TemporaryDirectory() as t:
            self.escreve(t, [linha("a")])
            p = self.escreve(t, [linha("b")])
            d = pd.read_parquet(p)
            self.assertEqual(sorted(d.columns), sorted(schema.PROPERTY_FIELDS))

    def test_tres_corridas_acumulam(self):
        with TemporaryDirectory() as t:
            self.escreve(t, [linha("a")])
            self.escreve(t, [linha("b")])
            p = self.escreve(t, [linha("c"), linha("d")])
            self.assertEqual(len(pd.read_parquet(p)), 4)


class TestRecusaSegura(unittest.TestCase):

    def test_particao_ilegivel_levanta_em_vez_de_sobrescrever(self):
        """
        Parquet corrompido não pode virar "partição vazia" e autorizar a
        sobrescrita do que ele contém. É a diferença entre perder o arquivo e
        saber que ele está quebrado.
        """
        with TemporaryDirectory() as t:
            w = lake.LakeWriter(raw_dir=t)
            p = Path(w.path_for("sp", "santos", "p", "x.com.br"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"isto nao e um parquet")
            with self.assertRaises(lake.WriteFailed):
                w.write([linha("a")], "sp", "santos", "p", "x.com.br")
            # e o arquivo quebrado continua la, para ser investigado
            self.assertTrue(p.exists())

    def test_zero_linhas_continua_recusado(self):
        with TemporaryDirectory() as t:
            w = lake.LakeWriter(raw_dir=t)
            with self.assertRaises(lake.WriteFailed):
                w.write([], "sp", "santos", "p", "x.com.br")


if __name__ == "__main__":
    unittest.main()
