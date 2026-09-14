"""
The monthly history table -- raw day partitions into one row per listing per month.

    python -m tratamento.anuncios.historico                    # relata, nao grava
    python -m tratamento.anuncios.historico --mes 2026-08
    python -m tratamento.anuncios.historico --todos --write

WHAT THIS REPLACES, AND WHY IT IS A REWRITE RATHER THAN A PORT.

`construir_base.py` consolidated the lake with a rule it stated plainly: "em
ambos os niveis a linha mais recente vence. Um imovel recoletado em outubro
ATUALIZA o de setembro em vez de duplicar -- e por isso que a coleta mensal nao
infla a base."

That rule is correct for a snapshot and fatal for a series. Collapsing across
months means the same flat seen in August and October is one row, and the price
it carries is October's. August is gone. No error is raised, the table is clean,
and the temporal series the whole refactor exists to build cannot form.

So the rule is INVERTED at exactly one level:

    WITHIN a month   collapse. Three collection days are one listing in August,
                     not three. This half is unchanged, and it is what
                     `property_id` makes possible: measured 2026-09-12 across
                     the seven Santos domains with two collections, the id
                     intersection between 27/08 and 29/08 was 99,4% to 100%.

    ACROSS months    never collapse. August and October are two observations of
                     one flat, and that is the data.

WHAT THE COLLAPSE KEEPS. Folding three days into one row discards information if
done carelessly, so it does not just take the last row:

    <attributes>            last observation -- area and bedrooms do not change;
                            if they did, the listing was corrected and the
                            correction is the truth
    preco_primeiro          first observation of the month
    preco_ultimo            last observation, and the month's price for series
                            purposes
    n_dias_observado        distinct collection days
    preco_mudou_no_mes      derived, a reprice inside the month is signal

Without `n_dias_observado` a listing seen once weighs the same as one seen all
month in any median, and nothing in the table would say so.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from comum import cidades_alvo, schema
from tratamento.anuncios import particoes

RAW_PADRAO = "dados/01_raw/listings"
OUT_PADRAO = "dados/02_processed/listings"

# Columns the collapse computes. They are not attributes of the listing, they
# are attributes of the OBSERVATION, and naming them apart keeps that visible.
DERIVADAS = ("preco_primeiro", "preco_ultimo", "n_dias_observado",
             "preco_mudou_no_mes", "ano", "mes", "mes_referencia")


class HistoricoVazio(Exception):
    """No partition matched. Zero rows is a failure, never a success."""


# --------------------------------------------------------------------------
# o colapso de dias
# --------------------------------------------------------------------------

def colapsa_mes(quadro: pd.DataFrame, mes: str) -> pd.DataFrame:
    """
    One month of stacked day partitions into one row per `property_id`.

    `quadro` must carry `extraction_date`; it is the only ordering the collapse
    can trust. Sorting by it is not cosmetic -- "last observation wins" is
    undefined without it, and pandas would silently keep whatever order the
    concat happened to produce.
    """
    if not len(quadro):
        return quadro

    d = quadro.sort_values("extraction_date", kind="stable")
    grupos = d.groupby("property_id", sort=False)

    # The surviving row is the last one seen. `.last()` per column would mix
    # rows -- area from one day, price from another -- and produce a listing
    # that never existed.
    base = d.drop_duplicates(subset="property_id", keep="last").copy()
    base = base.set_index("property_id")

    precos = grupos["price"]
    base["preco_primeiro"] = precos.first()
    base["preco_ultimo"] = precos.last()
    base["n_dias_observado"] = grupos["extraction_date"].nunique()
    base["preco_mudou_no_mes"] = (grupos["price"].nunique(dropna=True) > 1)

    ano, num = mes.split("-")
    base["ano"] = ano
    base["mes"] = num
    base["mes_referencia"] = mes

    return base.reset_index()


def constroi_mes(raiz: str | Path, mes: str) -> tuple[pd.DataFrame, dict]:
    """
    Read every day partition of one month and collapse it.

    Returns the table and a per-source report. The report is not decoration:
    every collection reports fill rate per field, and the day count per source
    is what makes a missing day visible instead of merely absent.
    """
    encontradas = particoes.do_mes(raiz, mes)
    if not encontradas:
        return pd.DataFrame(), {}

    por_fonte: dict[str, list] = {}
    for p in encontradas:
        por_fonte.setdefault(p.fonte, []).append(p)

    partes, relato = [], {}
    for fonte, lista in sorted(por_fonte.items()):
        # Partições vazias saem ANTES do concat, e as colunas são alinhadas à
        # união. Sem isso o pandas infere o dtype do resultado a partir de
        # colunas todas-nulas e avisa que vai mudar de comportamento -- e o
        # dia em que mudar, um `int64` de uma fonte vira `object` por causa de
        # outra que nunca preencheu o campo.
        quadros = [q for q in (pd.read_parquet(p.caminho) for p in lista)
                   if len(q)]
        if not quadros:
            continue
        colunas = sorted(set().union(*(q.columns for q in quadros)))
        quadros = [q.reindex(columns=colunas) for q in quadros]
        empilhado = pd.concat(quadros, ignore_index=True)
        colapsado = colapsa_mes(empilhado, mes)
        relato[fonte] = {
            "dias": len({p.data for p in lista}),
            "linhas": len(empilhado),
            "unicas": len(colapsado),
        }
        partes.append(colapsado)

    colunas = sorted(set().union(*(p.columns for p in partes)))
    partes = [p.reindex(columns=colunas) for p in partes]
    todo = pd.concat(partes, ignore_index=True)

    # A LISTA DE CIDADES-ALVO VALE AQUI TAMBEM, e nao so na ingestao.
    #
    # O portao do normalizador bloqueia coleta NOVA. As particoes ja gravadas
    # continuam no lago com o que foi coletado antes da regra, e sem este
    # filtro elas seguiriam alimentando tabela, ABT e modelo -- a regra valeria
    # para o futuro e nao para o que se reprocessa, que e a metade que sobe
    # para producao.
    if "city" in todo.columns:
        antes = len(todo)
        todo = todo[todo.city.map(
            lambda c: cidades_alvo.esta_no_alvo(c) if pd.notna(c) else False)]
        if antes != len(todo):
            log_fora = antes - len(todo)
            print(f"  {log_fora} linha(s) de cidade fora do alvo, descartadas")

    # Um imovel anunciado por duas agencias no MESMO mes e uma linha. O
    # `property_id` e sha1(dominio|listing_id), entao a repeticao aqui e entre
    # fontes, e o colapso acima -- que roda por fonte -- nao a alcanca.
    todo = todo.sort_values("extraction_date", kind="stable")
    todo = todo.drop_duplicates(subset="property_id", keep="last")

    # Faixas plausiveis do contrato, aplicadas TAMBEM aqui. O normalizador as
    # aplica na coleta, mas isso so vale para o que for coletado dali em
    # diante; os parquets ja gravados carregam as sentinelas.
    todo = pd.DataFrame([schema.aplica_faixas(r)[0]
                         for r in todo.to_dict("records")])

    return todo.reset_index(drop=True), relato


def constroi(raiz: str | Path, meses: list[str]) -> tuple[pd.DataFrame, dict]:
    """
    Several months, stacked and NEVER collapsed between them.

    This function has no `drop_duplicates` on `property_id` alone, and that
    absence is the design. Adding one would look like tidying and would delete
    the series.
    """
    partes, relato = [], {}
    for mes in meses:
        d, r = constroi_mes(raiz, mes)
        if len(d):
            partes.append(d)
            relato[mes] = {"fontes": len(r), "linhas": len(d), "por_fonte": r}
    if not partes:
        return pd.DataFrame(), {}
    return pd.concat(partes, ignore_index=True), relato


# --------------------------------------------------------------------------
# escrita
# --------------------------------------------------------------------------

def grava(quadro: pd.DataFrame, destino: str | Path) -> list[Path]:
    """
    One file per city per month, hive-style.

        {uf}/{cidade}/ano={ano}/mes={mes}/historico.parquet

    Rebuilding one month replaces exactly one file and leaves every other month
    untouched. A single table per city would mean rewriting the whole history
    to correct one month, and a half-written rewrite loses the past.

    Write failures RAISE. A failed write that returns quietly looks exactly
    like a successful one from the caller's side.
    """
    if not len(quadro):
        raise HistoricoVazio(
            "recusando gravar 0 linhas -- zero e falha, nao sucesso.")

    destino = Path(destino)
    escritos = []
    chaves = ["state", "city", "ano", "mes"]
    faltando = [c for c in chaves if c not in quadro.columns]
    if faltando:
        raise HistoricoVazio(f"colunas de particao ausentes: {faltando}")

    # AGRUPA PELO CAMINHO, nao pelo valor cru. `SP`/`sp` e `Santos`/`santos`
    # sao tres grafias que dao o mesmo diretorio, e agrupar pelo cru gerava
    # tres grupos que se sobrescreviam ali: o ultimo a gravar vencia e os
    # outros sumiam. Custou 8.108 das 14.842 linhas em 2026-09-12, sem erro,
    # sem aviso, e com a tabela parecendo correta.
    q = quadro.copy()
    q["_uf"] = q["state"].map(lambda v: schema.slugify(str(v)))
    q["_cidade"] = q["city"].map(lambda v: schema.slugify(str(v)))

    for (uf, cidade, ano, mes), parte in q.groupby(
            ["_uf", "_cidade", "ano", "mes"], sort=True):
        pasta = destino / uf / cidade / f"ano={ano}" / f"mes={mes}"
        pasta.mkdir(parents=True, exist_ok=True)
        caminho = pasta / "historico.parquet"
        if caminho in escritos:
            # Nunca deve acontecer: a chave de grupo JA e o caminho. Se
            # acontecer, alguem mexeu na chave e a perda seria silenciosa.
            raise HistoricoVazio(
                f"dois grupos para o mesmo caminho {caminho} -- "
                "a chave de agrupamento deixou de ser o caminho")
        try:
            parte.drop(columns=["_uf", "_cidade"]).to_parquet(caminho, index=False)
        except Exception as exc:
            raise HistoricoVazio(f"falha ao gravar {caminho}: {exc}") from exc
        escritos.append(caminho)
    return escritos


# --------------------------------------------------------------------------
# linha de comando
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=RAW_PADRAO)
    ap.add_argument("--out-dir", default=OUT_PADRAO)
    ap.add_argument("--mes", default=None, help="AAAA-MM")
    ap.add_argument("--todos", action="store_true",
                    help="todos os meses presentes no lago")
    ap.add_argument("--write", action="store_true",
                    help="grava; sem isto o processo so relata")
    args = ap.parse_args(argv)

    if args.todos:
        alvos = particoes.meses(args.raw_dir)
    elif args.mes:
        alvos = [args.mes]
    else:
        alvos = particoes.meses(args.raw_dir)[-1:]

    if not alvos:
        print(f"nenhuma particao em {args.raw_dir}", file=sys.stderr)
        return 1

    quadro, relato = constroi(args.raw_dir, alvos)
    if not len(quadro):
        print(f"nenhuma linha para {', '.join(alvos)}", file=sys.stderr)
        return 1

    print("=" * 72)
    print(f"historico -- {len(alvos)} mes(es): {', '.join(alvos)}")
    print("=" * 72)
    for mes, r in sorted(relato.items()):
        print(f"\n  {mes}: {r['fontes']} fonte(s), {r['linhas']} linha(s)")
        print(f"    {'cidade/dominio':<40}{'dias':>5}{'linhas':>9}{'unicas':>8}")
        for fonte, f in sorted(r["por_fonte"].items()):
            rep = f["linhas"] - f["unicas"]
            marca = f"   ({rep} repetidos entre dias)" if rep else ""
            print(f"    {fonte:<40}{f['dias']:>5}{f['linhas']:>9}"
                  f"{f['unicas']:>8}{marca}")

    print(f"\n  TOTAL {len(quadro)} linha(s) x {quadro.shape[1]} coluna(s)")
    if "mes_referencia" in quadro:
        print("\n  linhas por mes de referencia:")
        for mes, n in quadro.mes_referencia.value_counts().sort_index().items():
            print(f"    {mes}  {n:>7}")
        repetidos = int(quadro.property_id.duplicated().sum())
        print(f"\n  imoveis com mais de uma observacao: {repetidos}")
        print("  (repetido aqui e a SERIE, nao duplicata -- ver o docstring)")

    # Taxa de preenchimento por campo. Saida padrao, nao resposta a pergunta.
    print("\n  preenchimento por campo:")
    for c in sorted(quadro.columns):
        taxa = quadro[c].notna().mean()
        if taxa < 1.0:
            print(f"    {c:<28}{taxa:>7.1%}")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    escritos = grava(quadro, args.out_dir)
    print(f"\n  gravados {len(escritos)} arquivo(s):")
    for c in escritos:
        print(f"    {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
