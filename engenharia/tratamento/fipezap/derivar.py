"""
FipeZAP: the long raw series into the monthly table the market layer consumes.

    python -m tratamento.fipezap.derivar             # relata, nao grava
    python -m tratamento.fipezap.derivar --write

    serie_mensal.parquet   uma linha por cidade e mes, os cinco campos em colunas

THE CUT THAT USED TO HAPPEN AT INGESTION HAPPENS HERE. The old collector kept
the last month of three fields and discarded eighteen years of series before
anything reached disk. The raw layer now holds all of it, and this module
decides what the market table needs -- reversibly.

THE YIELD RANGE IS A DECLARED ASSUMPTION, NOT A MEASUREMENT.

FipeZAP publishes ONE yield per city per month: the aggregate. It does not
publish the spread BETWEEN PROPERTIES, and that spread is what a range shown to
a user means -- two flats in the same city do not return the same rate. The
temporal spread of the FipeZAP series itself is no substitute; it is the spread
of a city aggregate and is far too narrow.

So the range applies the RELATIVE spread measured in our own Santos data
(MEDICOES §5, n=151 pairs) to the FipeZAP centre. That the spread measured in
Santos holds in other cities is an assumption, and every row carries it in the
`dispersao` column rather than leaving it implicit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

RAW_PADRAO = "dados/01_raw/fipezap"
OUT_PADRAO = "dados/02_processed/fipezap"

# Dispersao RELATIVA do yield entre imoveis, medida na nossa base em Santos
# (MEDICOES §5, n=151 pares): p10/mediana e p90/mediana.
DISPERSAO_RELATIVA = {"p10": 0.438 / 0.625, "p90": 0.882 / 0.625}

CAMPOS = ["venda_m2", "venda_var12", "locacao_m2", "locacao_var12",
          "yield_mensal"]


class SemEntrada(Exception):
    """Nothing to derive from. Zero rows is a failure, never a success."""


def le_bruto(raiz: str | Path) -> pd.DataFrame:
    """The most recent raw capture. Each one is the whole series."""
    arquivos = sorted(Path(raiz).glob("*/*/serie.parquet"))
    if not arquivos:
        return pd.DataFrame()
    d = pd.read_parquet(arquivos[-1])
    d.attrs["origem"] = str(arquivos[-1])
    return d


def serie_mensal(d: pd.DataFrame) -> pd.DataFrame:
    """
    Long into wide: one row per city and month.

    Fields end in different months, so the ragged edge becomes nulls rather
    than truncation. A city whose yield stops in July still carries its August
    price, and the null says which is which.
    """
    largo = (d.pivot_table(index=["cidade", "mes_referencia"],
                           columns="campo", values="valor", aggfunc="last")
             .reset_index())
    largo.columns.name = None
    for c in CAMPOS:
        if c not in largo:
            largo[c] = None

    # A faixa acompanha o centro, e diz de onde veio.
    largo["yield_p10"] = largo["yield_mensal"] * DISPERSAO_RELATIVA["p10"]
    largo["yield_p90"] = largo["yield_mensal"] * DISPERSAO_RELATIVA["p90"]
    largo["dispersao"] = (
        "relativa medida em Santos (MEDICOES §5, n=151), aplicada ao centro "
        "FipeZAP; a FipeZAP nao publica dispersao entre imoveis")
    largo["fonte"] = "Indice FipeZAP, serie historica"

    return largo.sort_values(["cidade", "mes_referencia"]).reset_index(drop=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=RAW_PADRAO)
    ap.add_argument("--out-dir", default=OUT_PADRAO)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    bruto = le_bruto(args.raw_dir)
    if not len(bruto):
        print(f"nenhuma captura crua em {args.raw_dir}", file=sys.stderr)
        return 1

    d = serie_mensal(bruto)
    if not len(d):
        raise SemEntrada("derivacao vazia a partir de entrada nao vazia")

    print("=" * 72)
    print(f"FipeZAP -- serie mensal   ({bruto.attrs.get('origem', '')})")
    print("=" * 72)
    print(f"  {len(d):,} linhas   {d.cidade.nunique()} cidades   "
          f"{d.mes_referencia.min()} a {d.mes_referencia.max()}")

    print("\n  preenchimento por campo:")
    for c in CAMPOS + ["yield_p10", "yield_p90"]:
        print(f"    {c:<16}{d[c].notna().mean():>7.1%}")

    nossas = ["Santos", "São Paulo", "Guarujá", "São Vicente", "Praia Grande"]
    print("\n  ultimo mes das cidades da nossa base:")
    for cidade in nossas:
        sub = d[d.cidade == cidade]
        if not len(sub):
            print(f"    {cidade:<14}AUSENTE da planilha")
            continue
        ult = sub.dropna(subset=["yield_mensal"]).tail(1)
        if not len(ult):
            print(f"    {cidade:<14}sem rentabilidade publicada")
            continue
        r = ult.iloc[0]
        print(f"    {cidade:<14}{r.mes_referencia}   "
              f"yield {r.yield_mensal:.3%}   "
              f"venda R$/m2 {r.venda_m2 if pd.notna(r.venda_m2) else float('nan'):,.0f}")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    destino = Path(args.out_dir)
    destino.mkdir(parents=True, exist_ok=True)
    alvo = destino / "serie_mensal.parquet"
    try:
        d.to_parquet(alvo, index=False)
    except Exception as exc:
        raise SemEntrada(f"falha ao gravar {alvo}: {exc}") from exc
    print(f"\n  gravado: {alvo}  ({len(d):,} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
