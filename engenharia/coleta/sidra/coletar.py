"""
SIDRA ingestion -- the IBGE consumer price index into the raw layer.

    python -m coleta.sidra.coletar                  # baixa, valida, relata
    python -m coleta.sidra.coletar --write
    python -m coleta.sidra.coletar --desde 2020-01 --write

WHAT THIS IS. One request per run to the agency's own open API. No pagination,
no search, no personal data -- a national aggregate index.

THE QUESTION THAT USED TO BLOCK THIS SOURCE, AND HOW IT WAS SETTLED. The IPCA
is published by metropolitan region, and Santos is not a region of its own, so
"IPCA da cidade" had no answer for our main city. Decided 2026-09-12: use the
NATIONAL index, because it is a country indicator and treating it as one
removes the problem instead of working around it.

VERIFIED AT THE SOURCE, 2026-09-12, not assumed:

    tabela 1737         IPCA -- serie historica, mensal, desde 1979-12
    nivel territorial   N1 apenas -- Brasil. A propria tabela nao oferece
                        recorte municipal nem metropolitano, o que confirma
                        a decisao em vez de apenas ser compativel com ela
    variaveis           63    variacao mensal
                        2265  variacao acumulada em 12 meses
                        69    variacao acumulada no ano
                        2266  numero-indice (base dez/1993 = 100)

WHY `recorte` IS A COLUMN AND NOT A COMMENT. Every row carries `Brasil`. The
market table repeats this number on every neighbourhood row, and without the
column saying what it covers, somebody downstream reads a national index as
local inflation. The column is the only thing standing between those two.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

API = "https://apisidra.ibge.gov.br/values"
TABELA = "1737"
RAW_PADRAO = "dados/01_raw/sidra"

AGENTE = ("AluguelCerto/1.0 (+https://github.com/aluguelcerto; "
          "ingestao de indice publico)")

# Conferidas nos metadados da tabela em 2026-09-12.
VARIAVEIS = {
    "63": "ipca_mensal",
    "2265": "ipca_12m",
    "69": "ipca_ano",
    "2266": "ipca_indice",
}

RECORTE = "Brasil"


class ColetaInvalida(Exception):
    """The API did not return what we expect. Raises; never a crooked number."""


def busca(periodo: str = "all") -> list[dict]:
    """
    One request. `periodo` is SIDRA's own grammar: `all`, `last 12`, `202401-202612`.

    HTTP 200 with an HTML error page is the classic silent failure here, so the
    content type is checked before the body is trusted.
    """
    url = f"{API}/t/{TABELA}/n1/all/v/{','.join(VARIAVEIS)}/p/{periodo}"
    r = requests.get(url, headers={"User-Agent": AGENTE}, timeout=180)
    if r.status_code != 200:
        raise ColetaInvalida(f"{url} respondeu {r.status_code}")
    if "json" not in r.headers.get("Content-Type", ""):
        raise ColetaInvalida(
            f"{url} devolveu 200 mas Content-Type "
            f"{r.headers.get('Content-Type')!r} -- nao e JSON")
    try:
        bruto = r.json()
    except ValueError as exc:
        raise ColetaInvalida(f"{url}: corpo nao e JSON ({exc})") from exc

    # A primeira linha e o cabecalho descritivo, nao dado. Trata-la como dado
    # gravaria a string "Valor" na coluna de valor.
    if len(bruto) < 2:
        raise ColetaInvalida(
            f"{url} devolveu {len(bruto)} linha(s) -- sem dado. "
            "Zero e falha, nunca sucesso.")
    return bruto[1:]


def tabula(linhas: list[dict]) -> pd.DataFrame:
    """One row per month and variable, in long form."""
    registros = []
    for r in linhas:
        cod = r.get("D2C")
        periodo = r.get("D3C")
        valor = r.get("V")
        if cod not in VARIAVEIS or not periodo:
            continue
        # `...` e `-` sao os marcadores de ausencia do SIDRA. Viram nulo, e
        # nao zero: zero e um valor de inflacao, ausencia nao.
        try:
            v = float(valor)
        except (TypeError, ValueError):
            v = None
        registros.append({
            "recorte": RECORTE,
            "mes_referencia": f"{periodo[:4]}-{periodo[4:]}",
            "campo": VARIAVEIS[cod],
            "valor": v,
        })
    if not registros:
        raise ColetaInvalida(
            "nenhuma linha tabulavel -- as variaveis mudaram de codigo?")
    return pd.DataFrame(registros)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--periodo", default="all",
                    help="gramatica do SIDRA: all, 'last 12', 202001-202612")
    ap.add_argument("--raw-dir", default=RAW_PADRAO)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    d = tabula(busca(args.periodo))

    print("=" * 72)
    print(f"SIDRA tabela {TABELA} -- IPCA, recorte {RECORTE}")
    print("=" * 72)
    print(f"  {len(d):,} linhas   {d.campo.nunique()} campos   "
          f"{d.mes_referencia.min()} a {d.mes_referencia.max()}")

    print("\n  cobertura por campo:")
    for campo, sub in d.groupby("campo"):
        print(f"    {campo:<14}{len(sub):>7,} meses   "
              f"{sub.valor.notna().mean():>6.1%} preenchido   "
              f"ate {sub[sub.valor.notna()].mes_referencia.max()}")

    ultimos = (d[d.campo == "ipca_12m"].dropna(subset=["valor"])
               .sort_values("mes_referencia").tail(3))
    print("\n  IPCA acumulado em 12 meses, ultimos 3:")
    for _, r in ultimos.iterrows():
        print(f"    {r.mes_referencia}   {r.valor:>6.2f}%")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    agora = datetime.now(timezone.utc)
    pasta = (Path(args.raw_dir) / TABELA
             / agora.strftime("%Y") / agora.strftime("%m"))
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / "serie.parquet"
    try:
        d.to_parquet(alvo, index=False)
    except Exception as exc:
        raise ColetaInvalida(f"falha ao gravar {alvo}: {exc}") from exc
    print(f"\n  gravado: {alvo}  ({len(d):,} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
