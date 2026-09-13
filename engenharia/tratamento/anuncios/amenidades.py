"""
Amenities out of free text, and the binary columns the model consumes.

    python -m tratamento.anuncios.amenidades                 # relata, nao grava
    python -m tratamento.anuncios.amenidades --write
    python -m tratamento.anuncios.amenidades --exemplos 20

Runs over the monthly history table, not over raw partitions: the collapse has
already decided which observation of each listing survives the month, and
extracting from text before that would do the work several times and then throw
most of it away.

WHAT IT ADDS, AND THE ONE THAT IS EASY TO GET WRONG.

    amen_*    the listing HAS it
    nega_*    the text states it does NOT

`nega_*` is a column family of its own on purpose. "sem elevador" is price
information, and folding it into the absence of `amen_elevador` erases it: a
listing whose source never mentioned elevators and one that says it has none
would become the same row.

    em_obra   the text describes a unit under construction

`sera construido` is deliberately NOT in the pattern: it matches "proximo ao
novo Shopping que sera construido", which describes the neighbourhood rather
than the unit on offer.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd

from comum.amenidades_texto import extrai
from tratamento.anuncios import historico

ENTRADA_PADRAO = "dados/02_processed/listings"
SAIDA_PADRAO = "dados/02_processed/listings_enriquecidos"

OBRA = re.compile(
    r"\bem\s+construcao\b|\bem\s+obras\b|\bna\s+planta\b|\blancamento\b"
    r"|\bentrega\s+(prevista|em|para)\b|\bprevisao\s+de\s+entrega\b"
    r"|\bsera\s+entregue\b")


def dobra(texto) -> str:
    """Accent- and case-folded, whitespace-collapsed."""
    if not isinstance(texto, str):
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).lower()


def coluna(rotulo: str) -> str:
    """'Varanda Gourmet' -> 'varanda_gourmet'."""
    s = dobra(rotulo)
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", s)).strip("_")


def binariza(d: pd.DataFrame, origem: str, prefixo: str) -> pd.DataFrame:
    rotulos = sorted({r for lista in d[origem] for r in lista})
    novas = {f"{prefixo}{coluna(r)}": d[origem].map(lambda v, _r=r: _r in v)
             for r in rotulos}
    return pd.DataFrame(novas, index=d.index)


def enriquece(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add the amenity columns derived from the description.

    Duplicates are kept by explicit decision: if the source listed `Piscina`
    and the text mentions it too, the final list holds both. Deduplicating
    would lose the fact that two independent places agreed.
    """
    d = df.copy()
    fonte, texto, negadas = [], [], []
    for atual, descricao in zip(d["amenities"], d["description_clean"]):
        base = list(atual) if atual is not None else []
        pos, neg = extrai(descricao)
        fonte.append(base)
        texto.append(pos)
        negadas.append(neg)
    d["amenities_fonte"] = fonte
    d["amenities_texto"] = texto
    d["amenities_negadas"] = negadas
    d["amenities"] = [f + t for f, t in zip(fonte, texto)]
    return d


def expande(d: pd.DataFrame) -> pd.DataFrame:
    """
    The enriched frame with the list columns replaced by boolean ones.

    EVERY COLUMN DERIVED FROM FREE TEXT IS PRODUCED HERE, because this is the
    last stage that still has the text: `description_clean` is dropped on the
    way out. `rx_vaga_tipo` and `rx_vista` used to be recomputed inside the
    trainer, which forced the training step to reassemble three layers and to
    require their dates to match. Deriving them once, where the text lives,
    removes that coupling.
    """
    from comum.ml import regras_texto

    amen = binariza(d, "amenities", "amen_")
    nega = binariza(d, "amenities_negadas", "nega_")
    base = d.drop(columns=["amenities", "amenities_fonte", "amenities_texto",
                           "amenities_negadas", "description_clean"])
    saida = pd.concat([base, amen, nega], axis=1)
    saida["em_obra"] = d["description_clean"].map(
        lambda t: bool(OBRA.search(dobra(t)))).values

    rx = regras_texto.aplica(d[["property_id", "description_clean"]],
                             col="description_clean")
    for col in ("rx_vaga_tipo", "rx_vista"):
        if col in rx:
            saida[col] = rx[col].values
    return saida


def relatorio(d: pd.DataFrame) -> str:
    n = len(d)
    tem_desc = d["description_clean"].map(
        lambda v: isinstance(v, str) and v.strip() != "")
    n_fonte = d["amenities_fonte"].map(len)
    n_texto = d["amenities_texto"].map(len)
    n_neg = d["amenities_negadas"].map(len)
    tem = d["amenities"].map(len)

    linhas = ["", "=" * 72, "AMENIDADES A PARTIR DO TEXTO", "=" * 72,
              f"linhas                                  {n:>7}",
              f"com descricao                           {int(tem_desc.sum()):>7}  {tem_desc.mean():>6.1%}",
              "",
              f"amenidades vindas da fonte              {int(n_fonte.sum()):>7}",
              f"amenidades vindas do texto              {int(n_texto.sum()):>7}",
              f"amenidades negadas no texto             {int(n_neg.sum()):>7}",
              "",
              f"linhas que ganharam alguma amenidade    {int((n_texto > 0).sum()):>7}  {(n_texto > 0).mean():>6.1%}",
              f"  destas, estavam VAZIAS antes          {int(((n_texto > 0) & (n_fonte == 0)).sum()):>7}",
              f"linhas com alguma negacao               {int((n_neg > 0).sum()):>7}  {(n_neg > 0).mean():>6.1%}",
              "",
              f"cobertura de amenities  antes           {(n_fonte > 0).mean():>6.1%}",
              f"cobertura de amenities  depois          {(tem > 0).mean():>6.1%}"]

    c_texto, c_neg = Counter(), Counter()
    for v in d["amenities_texto"]:
        c_texto.update(v)
    for v in d["amenities_negadas"]:
        c_neg.update(v)

    linhas += ["", "-" * 72, "15 amenidades mais extraidas do texto", "-" * 72]
    for k, v in c_texto.most_common(15):
        linhas.append(f"   {k:<34}{v:>6}")
    linhas += ["", "-" * 72, "amenidades NEGADAS no texto", "-" * 72]
    for k, v in c_neg.most_common(15):
        linhas.append(f"   {k:<34}{v:>6}")
    return "\n".join(linhas + ["=" * 72, ""])


def le_historico(raiz: str | Path) -> pd.DataFrame:
    """Every month of every city of the history table."""
    arquivos = sorted(Path(raiz).glob("*/*/ano=*/mes=*/historico.parquet"))
    if not arquivos:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(a) for a in arquivos], ignore_index=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entrada", default=ENTRADA_PADRAO)
    ap.add_argument("--saida", default=SAIDA_PADRAO)
    ap.add_argument("--exemplos", type=int, default=0)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    d = le_historico(args.entrada)
    if not len(d):
        print(f"nenhuma tabela historica em {args.entrada}", file=sys.stderr)
        return 1

    enriquecido = enriquece(d)
    print(relatorio(enriquecido))

    if args.exemplos:
        print("-" * 72)
        print(f"{args.exemplos} extracoes reais")
        print("-" * 72)
        amostra = enriquecido[enriquecido.amenities_texto.map(len) > 0]
        for _, r in amostra.head(args.exemplos).iterrows():
            print(f"\n  + {r.amenities_texto}")
            if r.amenities_negadas:
                print(f"  - {r.amenities_negadas}")

    saida = expande(enriquecido)
    amen = [c for c in saida.columns if c.startswith("amen_")]
    nega = [c for c in saida.columns if c.startswith("nega_")]
    print(f"  colunas amen_*                          {len(amen):>7}")
    print(f"  colunas nega_*                          {len(nega):>7}")
    print(f"  em_obra = True                          {int(saida.em_obra.sum()):>7}"
          f"  {saida.em_obra.mean():>6.1%}")
    print(f"\n  saida: {len(saida)} linhas x {saida.shape[1]} colunas")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    # Grava com a MESMA particao da tabela historica, pelo mesmo escritor.
    # Um segundo escritor aqui divergiria do primeiro no dia em que a regra de
    # caminho mudasse, e a divergencia so apareceria como arquivo orfao.
    destino = Path(args.saida)
    escritos = historico.grava(saida, destino)
    print(f"\n  gravados {len(escritos)} arquivo(s) em {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
