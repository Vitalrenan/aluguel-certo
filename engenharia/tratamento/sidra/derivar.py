"""
SIDRA: the long raw IPCA series into the monthly table the market layer consumes.

    python -m tratamento.sidra.derivar              # relata, nao grava
    python -m tratamento.sidra.derivar --write

    serie_mensal.parquet   uma linha por mes, os quatro campos em colunas

`recorte` TRAVESSES EVERY STAGE, AND THAT IS THE POINT. The index is national.
The market table repeats it on every neighbourhood row of every city, so by the
time it reaches a screen it looks local. The column is what keeps the claim
honest all the way through, and it is carried rather than reconstructed: a
recorte inferred downstream would be a guess dressed as a fact.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

RAW_PADRAO = "dados/01_raw/sidra"
OUT_PADRAO = "dados/02_processed/sidra"

CAMPOS = ["ipca_mensal", "ipca_12m", "ipca_ano", "ipca_indice"]


class SemEntrada(Exception):
    """Nothing to derive from. Zero rows is a failure, never a success."""


def le_bruto(raiz: str | Path) -> pd.DataFrame:
    """The most recent raw capture of each table."""
    arquivos = sorted(Path(raiz).glob("*/*/*/serie.parquet"))
    if not arquivos:
        return pd.DataFrame()
    d = pd.read_parquet(arquivos[-1])
    d.attrs["origem"] = str(arquivos[-1])
    return d


def serie_mensal(d: pd.DataFrame) -> pd.DataFrame:
    """
    Long into wide: one row per month, `recorte` preserved.

    Months where a field is absent stay null. The index runs from 1979 and the
    twelve-month accumulation cannot exist for the first eleven of them; a zero
    there would be a number, and absence is not a number.
    """
    largo = (d.pivot_table(index=["recorte", "mes_referencia"],
                           columns="campo", values="valor", aggfunc="last")
             .reset_index())
    largo.columns.name = None
    for c in CAMPOS:
        if c not in largo:
            largo[c] = None
    largo["fonte"] = "IBGE/SIDRA tabela 1737 -- IPCA"
    return largo.sort_values("mes_referencia").reset_index(drop=True)


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
    print(f"SIDRA -- serie mensal   ({bruto.attrs.get('origem', '')})")
    print("=" * 72)
    print(f"  {len(d):,} meses   recorte {sorted(set(d.recorte))}   "
          f"{d.mes_referencia.min()} a {d.mes_referencia.max()}")
    print("\n  preenchimento por campo:")
    for c in CAMPOS:
        print(f"    {c:<14}{d[c].notna().mean():>7.1%}")

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
