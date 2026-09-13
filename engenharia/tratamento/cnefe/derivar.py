"""
The two CNEFE lookups, from the raw layer.

    python -m tratamento.cnefe.derivar            # relata, nao grava
    python -m tratamento.cnefe.derivar --write

    logradouro_cep.parquet   municipio + rua canonica  -> CEP
    cep_coord.parquet        CEP                       -> lat/lon

WHY BOTH LIVE HERE. They read the same raw table and disagreeing about how a
street name is spelled would make them silently incompatible. One module, one
`chave`, imported from `comum.logradouro` by the consumers too.

`cep_coord` HAD NO BUILDER. Measured 2026-09-12: two files in the old
repository read `02_processed/referencia/cep_coord_cnefe2022.parquet` and no
file in the repository produced it. The table sat on disk with no versioned
recipe -- if it were lost, nobody could rebuild it. This module is that recipe.

THE COORDINATE OF A CEP IS THE MEDIAN, NOT THE MEAN. A CEP covers a stretch of
street, so its addresses form a cloud rather than a point, and one bad record
can sit in another municipality. The mean follows it there; the median does not
move.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from comum.logradouro import chave, utilizavel

RAW_PADRAO = "dados/01_raw/cnefe"
OUT_PADRAO = "dados/02_processed/cnefe"

MUNICIPIOS = {
    "3548500": "Santos", "3550308": "Sao Paulo", "3551009": "Sao Vicente",
    "3518701": "Guaruja", "3541000": "Praia Grande", "3513504": "Cubatao",
    "3506359": "Bertioga",
}


class SemEntrada(Exception):
    """Nothing to derive from. Zero rows is a failure, never a success."""


def le_bruto(raiz: str | Path) -> pd.DataFrame:
    """Every municipality, every census year, present in the raw layer."""
    arquivos = sorted(Path(raiz).glob("*/*/enderecos.parquet"))
    if not arquivos:
        return pd.DataFrame()
    partes = []
    for a in arquivos:
        d = pd.read_parquet(a)
        cod = a.parent.parent.name
        d["cod_municipio"] = d.get("cod_municipio", cod)
        d["municipio"] = MUNICIPIOS.get(cod, cod)
        d["ano_censo"] = a.parent.name
        partes.append(d)
    return pd.concat(partes, ignore_index=True)


def logradouro_cep(d: pd.DataFrame) -> pd.DataFrame:
    """
    municipio + canonical street -> the CEP, plus the ambiguity.

    A street has several CEPs, one per stretch. Without a house number there is
    no way to pick the stretch, so the MOST FREQUENT one is kept and `n_ceps`
    records how many there were. Dropping `n_ceps` would make an unambiguous
    street indistinguishable from one split across nine postcodes.
    """
    partes = []
    for col in ("nom_tipo_seglogr", "nom_titulo_seglogr", "nom_seglogr"):
        partes.append(d[col].fillna("").str.strip() if col in d else "")
    bruto = (partes[0] + " " + partes[1] + " " + partes[2])

    t = pd.DataFrame({
        "municipio": d["municipio"],
        "logradouro": bruto.str.replace(r"\s+", " ", regex=True).str.strip(),
        "cep": d["cep"],
    })
    t = t[t.cep.str.len() == 8]
    t["chave"] = t.logradouro.map(chave)
    t = t[t.chave.map(utilizavel)]

    g = (t.groupby(["municipio", "chave", "cep"]).size()
         .rename("n").reset_index()
         .sort_values("n", ascending=False))
    return (g.groupby(["municipio", "chave"])
            .agg(cep=("cep", "first"), n_enderecos=("n", "sum"),
                 n_ceps=("cep", "nunique"))
            .reset_index())


def cep_coord(d: pd.DataFrame) -> pd.DataFrame:
    """
    CEP -> median coordinate, with the spread that says how trustworthy it is.

    `n_enderecos` and the coordinate count are kept because a CEP built from
    two addresses and one built from four hundred are not the same evidence,
    and the point alone does not say which is which.
    """
    t = d[["municipio", "cep", "latitude", "longitude"]].copy()
    t = t[t.cep.str.len() == 8]
    t["latitude"] = pd.to_numeric(t.latitude, errors="coerce")
    t["longitude"] = pd.to_numeric(t.longitude, errors="coerce")
    t = t.dropna(subset=["latitude", "longitude"])

    return (t.groupby(["municipio", "cep"])
            .agg(lat=("latitude", "median"), lon=("longitude", "median"),
                 n_enderecos=("latitude", "size"))
            .reset_index())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=RAW_PADRAO)
    ap.add_argument("--out-dir", default=OUT_PADRAO)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    d = le_bruto(args.raw_dir)
    if not len(d):
        print(f"nenhuma tabela crua em {args.raw_dir}", file=sys.stderr)
        return 1
    print(f"entrada: {len(d):,} enderecos de {d.municipio.nunique()} municipio(s)")

    ruas = logradouro_cep(d)
    ceps = cep_coord(d)
    if not len(ruas) or not len(ceps):
        raise SemEntrada(
            f"derivacao vazia: {len(ruas)} logradouros, {len(ceps)} CEPs")

    print(f"\n{'=' * 70}\nlogradouro -> CEP\n{'=' * 70}")
    print(f"  {len(ruas):,} logradouros distintos")
    for m, sub in ruas.groupby("municipio"):
        print(f"    {m:<12}{len(sub):>8,} logradouros   "
              f"{(sub.n_ceps > 1).mean():>6.1%} com mais de um CEP   "
              f"mediana de {sub.n_ceps.median():.0f} CEP(s)")

    print(f"\n{'=' * 70}\nCEP -> coordenada\n{'=' * 70}")
    print(f"  {len(ceps):,} CEPs distintos")
    for m, sub in ceps.groupby("municipio"):
        print(f"    {m:<12}{len(sub):>8,} CEPs   "
              f"mediana de {sub.n_enderecos.median():.0f} endereco(s) por CEP")
        print(f"    {'':<12}lat {sub.lat.min():.4f} a {sub.lat.max():.4f}   "
              f"lon {sub.lon.min():.4f} a {sub.lon.max():.4f}")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    destino = Path(args.out_dir)
    destino.mkdir(parents=True, exist_ok=True)
    for nome, quadro in (("logradouro_cep", ruas), ("cep_coord", ceps)):
        alvo = destino / f"{nome}.parquet"
        try:
            quadro.to_parquet(alvo, index=False)
        except Exception as exc:
            raise SemEntrada(f"falha ao gravar {alvo}: {exc}") from exc
        print(f"  gravado: {alvo}  ({len(quadro):,} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
