"""
The ABT and its holdout -- ready to train, in the refined layer.

    python -m refino.abt                    # relata, nao grava
    python -m refino.abt --write
    python -m refino.abt --mes 2026-08 --write

WHAT MOVED AND WHAT DID NOT. The construction rules are unchanged and stay
unchanged on purpose: they were each measured, and re-deciding them during a
move would make the move impossible to verify. What changed is where the table
lives and what it is keyed on.

    03_refined/abt/{alvo}/abt_{aaaa-mm}.parquet
    03_refined/abt/{alvo}/holdout_{aaaa-mm}.parquet

THE RULES, EACH ONE COSTING A REAL ERROR:

  1. Price outside the plausible band leaves. Sale 50k-100M, rent 300-200k.
  2. R$/m2 outside band leaves, per segment. This catches area outliers
     (`Terreno 360.000 m2`) without guessing an area ceiling. A row with null
     area is NOT removed: LightGBM handles NaN and a sixth of the base has no
     area.
  3. `city` and `neighborhood` normalised. `Guaruja`/`Guarujá` were two
     categories for one place. Measured: normalising the neighbourhood alone is
     worth -2% error on sale and -8% on rent.
  4. Split by GROUP, never by row. The same flat is listed twice often enough
     that random splitting puts copies on both sides and the error comes out
     optimistic. Every copy lands on the same side.
  5. `source_domain` / `source_platform` stay for audit but are NOT predictors:
     when estimating a new property that information does not exist.
  6. Target is log(price). A sale median near 800k and a rent median near 5,5k
     only share a model in log scale.

THE HOLDOUT IS TOUCHED ONCE, in the final training. Measured during a
configuration search, it stops existing for the whole project.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from comum.logradouro import lugar

ENTRADA_PADRAO = "dados/02_processed/listings_enriquecidos"
SAIDA_PADRAO = "dados/03_refined/abt"

FAIXA_PRECO = {"venda": (50_000, 100_000_000), "locacao": (300, 200_000)}
FAIXA_M2 = {"venda": (500, 60_000), "locacao": (5, 500)}

NUMERICAS = ["area_m2", "area_total_m2", "bedrooms", "bathrooms", "suites",
             "parking_spots", "floor_level", "condo_fee", "iptu_tax"]
CATEGORICAS = ["transaction_type", "property_type", "city", "neighborhood",
               "cep_prefix"]

SUPORTE_MIN = 30
FRACAO_HOLDOUT = 0.10
SEMENTE = 42

# Colunas de observacao que a tabela historica acrescentou. Ficam na ABT para
# auditoria e para virar preditor no dia em que a serie tiver meses suficientes
# -- hoje nao tem, e usa-las seria treinar num campo que quase nao varia.
OBSERVACAO = ["mes_referencia", "ano", "mes", "n_dias_observado",
              "preco_primeiro", "preco_ultimo", "preco_mudou_no_mes"]


class ABTVazia(Exception):
    """Zero rows is a failure, never a success."""


def normaliza(s) -> str:
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


def assinatura(d: pd.DataFrame) -> pd.Series:
    """
    Grouping key: the same flat listed twice falls in the same group.

    Built from what a duplicate listing shares rather than from `property_id`,
    which differs between agencies advertising the same unit.
    """
    return (d.transaction_type.map(normaliza) + "|" + d.city.map(normaliza) + "|"
            + d.neighborhood.map(normaliza) + "|" + d.price.astype(str) + "|"
            + d.area_m2.astype(str) + "|" + d.bedrooms.astype(str))


def filtra(d: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    log = [f"entrada                                {len(d):>6}"]
    n0 = len(d)

    d = d[d.price.notna()]
    log.append(f"  - preco nulo                         {n0 - len(d):>6}")

    n = len(d)
    dentro = pd.Series(False, index=d.index)
    for t, (lo, hi) in FAIXA_PRECO.items():
        dentro |= (d.transaction_type == t) & d.price.between(lo, hi)
    d = d[dentro]
    log.append(f"  - preco fora de faixa                {n - len(d):>6}")

    n = len(d)
    m2 = d.price / d.area_m2
    fora = pd.Series(False, index=d.index)
    for t, (lo, hi) in FAIXA_M2.items():
        fora |= (d.transaction_type == t) & m2.notna() & ~m2.between(lo, hi)
    d = d[~fora]
    log.append(f"  - R$/m2 implausivel (outlier area)   {n - len(d):>6}")

    log.append(f"ABT + holdout                          {len(d):>6}")
    return d, log


def le_entrada(raiz: str | Path, mes: str | None = None) -> pd.DataFrame:
    """
    The treated table, optionally one month of it.

    Without `--mes` every month is read and stacked. That is deliberate: the
    model wants all the evidence, and the monthly key stays on the row so a
    later cut by month remains possible.
    """
    padrao = (f"*/*/ano={mes.split(chr(45))[0]}/mes={mes.split(chr(45))[1]}/*.parquet"
              if mes else "*/*/ano=*/mes=*/*.parquet")
    arquivos = sorted(Path(raiz).glob(padrao))
    if not arquivos:
        return pd.DataFrame()
    partes = [pd.read_parquet(a) for a in arquivos]
    colunas = sorted(set().union(*(p.columns for p in partes)))
    partes = [p.reindex(columns=colunas) for p in partes]
    return pd.concat(partes, ignore_index=True)


def constroi(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (abt, holdout, relatorio)."""
    original = d
    d, log = filtra(d)
    if not len(d):
        raise ABTVazia("filtro removeu todas as linhas")

    d = d.copy()
    # Mesma funcao que a tabela de mercado usa. Duas normalizacoes diferentes
    # dariam bairros que existem numa tabela e nao na outra, e a juncao entre
    # elas perderia linhas sem erro.
    d["city"] = d.city.map(lugar)
    d["neighborhood"] = d.neighborhood.map(lugar)

    binarias = [c for c in d.columns if c.startswith(("amen_", "nega_"))]
    com_suporte = [c for c in binarias if d[c].sum() >= SUPORTE_MIN]
    preditores = NUMERICAS + CATEGORICAS + ["em_obra"] + com_suporte

    d["log_price"] = np.log(d.price)

    # `cep` inteiro fica na ABT mas FORA dos preditores: poucos anuncios por
    # CEP fazem dele ruido como categoria. Ele existe aqui para derivar coisa
    # util depois -- distancia, geocodificacao. O prefixo e que entra.
    from comum.ml import formulario as _f
    manter = (["property_id", "link", "source_domain", "source_platform",
               "state", "extraction_date", "price", "log_price", "cep"]
              + OBSERVACAO + preditores + list(_f.COLUNAS))
    vistos, ordenado = set(), []
    for c in manter:
        if c in d.columns and c not in vistos:
            vistos.add(c); ordenado.append(c)
    d = d[ordenado]

    # AS COLUNAS DO FORMULARIO ENTRAM AQUI, e nao no treino.
    #
    # `rx_vaga_tipo` e `rx_vista` saem do texto livre por regra; `fam_*` sao o
    # OU booleano dos membros de cada familia de amenidade, e os membros raros
    # so existem ANTES da poda por suporte -- que descarta 246 das 438 colunas.
    # Derivar depois da poda daria familias menores que as medidas, sem erro.
    #
    # Uma ABT "pronta para treinar" que obrigasse o treino a remontar tres
    # camadas nao estaria pronta; e a remontagem no treino foi de onde veio a
    # exigencia de datas de base casadas.
    from comum.ml import formulario, regras_texto

    if "description_clean" in original.columns:
        rx = regras_texto.aplica(
            original.loc[d.index, ["property_id", "description_clean"]],
            col="description_clean")
        for col in ("rx_vaga_tipo", "rx_vista"):
            if col in rx:
                d[col] = rx[col].values
    membros = sorted({m for ms in formulario.FAMILIAS.values() for m in ms})
    faltam = [m for m in membros if m not in original.columns]
    if faltam:
        raise ABTVazia(
            f"entrada sem os membros de familia {faltam[:6]} -- a familia "
            "sairia menor que a medida. Rode o estagio de amenidades antes.")
    fam = original.loc[d.index, membros]
    for nome, ms in formulario.FAMILIAS.items():
        d[nome] = np.logical_or.reduce(
            [fam[m].fillna(False).astype(bool).values for m in ms])
    d["property_type"] = d.property_type.map(formulario.normaliza_tipo)
    for c in formulario.BOOLEANAS:
        if c in d:
            d[c] = d[c].fillna(False).astype(bool)

    sig = assinatura(original.loc[d.index])
    grupos = sig.unique()
    rng = np.random.default_rng(SEMENTE)
    escolhidos = set(rng.choice(
        grupos, size=int(round(len(grupos) * FRACAO_HOLDOUT)), replace=False))
    e_holdout = sig.isin(escolhidos).values

    abt = d[~e_holdout].reset_index(drop=True)
    holdout = d[e_holdout].reset_index(drop=True)

    relatorio = {"log": log, "preditores": preditores, "binarias": binarias,
                 "com_suporte": com_suporte, "grupos": len(grupos)}
    return abt, holdout, relatorio


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entrada", default=ENTRADA_PADRAO)
    ap.add_argument("--saida", default=SAIDA_PADRAO)
    ap.add_argument("--mes", default=None, help="AAAA-MM; padrao e todos")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    d = le_entrada(args.entrada, args.mes)
    if not len(d):
        print(f"nenhuma tabela tratada em {args.entrada}", file=sys.stderr)
        return 1

    abt, holdout, r = constroi(d)

    print("=" * 72)
    print("CONSTRUCAO DA ABT")
    print("=" * 72)
    print("\n".join(r["log"]))
    print(f"\npreditores                             {len(r['preditores']):>6}")
    print(f"  numericas                            {len(NUMERICAS):>6}")
    print(f"  categoricas                          {len(CATEGORICAS):>6}")
    print(f"  binarias com suporte >= {SUPORTE_MIN}          "
          f"{len(r['com_suporte']):>6}   (de {len(r['binarias'])}; "
          f"{len(r['binarias']) - len(r['com_suporte'])} descartadas)")
    print(f"\ngrupos de imovel                       {r['grupos']:>6}")
    print(f"\nABT       {len(abt):>6} linhas x {abt.shape[1]:>3} colunas")
    print(f"holdout   {len(holdout):>6} linhas x {holdout.shape[1]:>3} colunas")

    print("\npares cidade x transacao com n >= 300 (candidatos a modelo proprio):")
    pares = abt.groupby(["city", "transaction_type"]).size().sort_values(ascending=False)
    for (cidade, alvo), n in pares[pares >= 300].items():
        print(f"   {cidade:<16}{alvo:<10}{n:>7}")
    print(f"   ... e {(pares < 300).sum()} par(es) abaixo de 300, "
          f"somando {pares[pares < 300].sum()} linha(s)")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    mes = args.mes or (abt.mes_referencia.max() if "mes_referencia" in abt
                       else "sem-mes")
    escritos = []
    for alvo, parte_abt in abt.groupby("transaction_type"):
        parte_ho = holdout[holdout.transaction_type == alvo]
        pasta = Path(args.saida) / str(alvo)
        pasta.mkdir(parents=True, exist_ok=True)
        for nome, quadro in (("abt", parte_abt), ("holdout", parte_ho)):
            alvo_arq = pasta / f"{nome}_{mes}.parquet"
            try:
                quadro.to_parquet(alvo_arq, index=False)
            except Exception as exc:
                raise ABTVazia(f"falha ao gravar {alvo_arq}: {exc}") from exc
            escritos.append((alvo_arq, len(quadro)))

    print(f"\n  gravados {len(escritos)} arquivo(s):")
    for caminho, n in escritos:
        print(f"    {caminho}  ({n} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
