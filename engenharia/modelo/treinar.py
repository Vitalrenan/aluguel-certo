"""
Final training, one model per city and target.

    python -m modelo.treinar                              # confere, nao treina
    python -m modelo.treinar --cidade Santos --alvo venda --write
    python -m modelo.treinar --todos --write

    modelos/{ano}/{mes}/{cidade}/{alvo}/
        modelo.txt        booster do ponto central
        modelo_q10.txt    limite inferior do intervalo
        modelo_q90.txt    limite superior
        colunas.json      a ordem, que e parte do modelo
        categorias.json   os niveis de cada categorica
        model_card.json   o cartao

WHAT CHANGED FROM `treina_final.py`. It keyed the artifact on a SEGMENT
(`venda_ate800k`, `locacao`) and the service picked one segment at start-up
through an environment variable. A screen with a city selector cannot be served
that way, so the key is now the pair (city, target) and the path carries year
and month.

THERE IS NO POOLED FALLBACK. A city outside `config/cidades.yaml` is not
trained and not served; it gets an explicit refusal. Serving a pooled model and
calling it the city's estimate produces a number wearing the same confidence as
a measured one, and nobody on the screen can tell them apart.

THE HOLDOUT IS TOUCHED ONCE, here, in `--write`. Measured during a
configuration search it stops existing for the whole project, which is why
there is no `--ablacao` in this script: ablation reads the ABT and never the
holdout, and it lives in `modelo.curva`.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from comum.logradouro import lugar
from comum.ml import formulario
from modelo import cartao as cartao_mod
from modelo.nucleo import grava as grava_artefato
from modelo.nucleo import mede, treina

ABT_PADRAO = "dados/03_refined/abt"
MODELOS_PADRAO = "modelos"
CIDADES_PADRAO = "config/cidades.yaml"
CENARIO = "sem_opcionais"


class TreinoInvalido(Exception):
    """Refuses to train or write something that cannot be trusted."""


def pares_disponiveis(caminho: str | Path) -> list[tuple[str, str]]:
    """
    The (city, target) pairs `cidades.yaml` marks available.

    Availability is per PAIR, not per city: São Paulo has sale volume and
    almost no rent, so the city is in scope and the pair (São Paulo, rent) is
    not.
    """
    cfg = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    pares = []
    for c in cfg.get("cidades", []):
        for alvo, info in (c.get("alvos") or {}).items():
            if info.get("disponivel"):
                pares.append((c["nome"], alvo))
    return pares


def _sha256(caminho: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with caminho.open("rb") as f:
            for bloco in iter(lambda: f.read(1 << 20), b""):
                h.update(bloco)
        return h.hexdigest()
    except OSError:
        return None


def le_abt(raiz: str | Path, alvo: str) -> tuple[pd.DataFrame, pd.DataFrame, Path]:
    """The most recent ABT and holdout of one target."""
    pasta = Path(raiz) / alvo
    abts = sorted(pasta.glob("abt_*.parquet"))
    if not abts:
        raise TreinoInvalido(f"nenhuma ABT em {pasta}")
    abt = abts[-1]
    holdout = pasta / abt.name.replace("abt_", "holdout_")
    if not holdout.exists():
        raise TreinoInvalido(f"ABT sem holdout irmao: {holdout}")
    return pd.read_parquet(abt), pd.read_parquet(holdout), abt


def recorta(d: pd.DataFrame, cidade: str) -> pd.DataFrame:
    """
    The city's rows, matched through the SAME normaliser the ABT was built with.

    `cidades.yaml` says `São Paulo` and the ABT says `Sao Paulo`, because the
    refining stage strips accents. Comparing literally returned zero rows and
    the trainer reported "treino 0" as if the city had no data -- a config file
    and a table disagreeing about spelling, reading as an empty city.
    """
    return d[d.city.map(lugar) == lugar(cidade)]


def treina_par(abt: pd.DataFrame, holdout: pd.DataFrame, cidade: str,
               alvo: str) -> tuple[object, dict, pd.DataFrame, pd.DataFrame]:
    tr = recorta(abt, cidade)
    ho = recorta(holdout, cidade)
    if len(tr) < 100:
        raise TreinoInvalido(
            f"{cidade}/{alvo}: {len(tr)} linhas de treino. "
            "Abaixo de 100 o modelo nao e servivel, e um modelo que nao deve "
            "ser servido nao deve ser gravado.")
    if not len(ho):
        raise TreinoInvalido(
            f"{cidade}/{alvo}: holdout vazio -- nao ha como medir, e um "
            "modelo sem medicao nao pode declarar desempenho.")

    colunas = list(formulario.COLUNAS)
    art = treina(tr, colunas)
    desempenho = mede(art, ho, CENARIO)
    return art, desempenho, tr, ho


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--abt", default=ABT_PADRAO)
    ap.add_argument("--destino", default=MODELOS_PADRAO)
    ap.add_argument("--cidades", default=CIDADES_PADRAO)
    ap.add_argument("--cidade", default=None)
    ap.add_argument("--alvo", default=None)
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="treina e mede no holdout UMA vez")
    args = ap.parse_args(argv)

    if args.cidade and args.alvo:
        pares = [(args.cidade, args.alvo)]
    elif args.todos or not (args.cidade or args.alvo):
        pares = pares_disponiveis(args.cidades)
    else:
        print("use --cidade com --alvo, ou --todos", file=sys.stderr)
        return 1

    if not pares:
        print("nenhum par disponivel em cidades.yaml", file=sys.stderr)
        return 1

    hoje = date.today()
    ano, mes = hoje.strftime("%Y"), hoje.strftime("%m")
    versao = f"{hoje.isoformat()}-a"

    print("=" * 72)
    print(f"TREINO FINAL -- {len(pares)} par(es) cidade x alvo")
    print("=" * 72)

    escritos, falhas = [], []
    for cidade, alvo in pares:
        print(f"\n  {cidade} / {alvo}")
        try:
            abt, holdout, abt_caminho = le_abt(args.abt, alvo)
        except TreinoInvalido as exc:
            print(f"    PULADO: {exc}")
            falhas.append((cidade, alvo, str(exc)))
            continue

        n_tr, n_ho = len(recorta(abt, cidade)), len(recorta(holdout, cidade))
        print(f"    treino {n_tr:>6}   holdout {n_ho:>5}   "
              f"ABT {abt_caminho.name}")

        if not args.write:
            continue

        try:
            art, desempenho, tr, ho = treina_par(abt, holdout, cidade, alvo)
        except TreinoInvalido as exc:
            print(f"    RECUSADO: {exc}")
            falhas.append((cidade, alvo, str(exc)))
            continue

        cobertura = {c: round(float(tr[c].notna().mean()), 4)
                     for c in tr.columns if tr[c].notna().mean() < 1.0}

        cart = cartao_mod.monta(
            cidade=cidade, alvo=alvo, ano=ano, mes=mes, versao=versao,
            escopo_treino="cidade", treino=tr, holdout=ho,
            abt_caminho=str(abt_caminho), abt_sha256=_sha256(abt_caminho),
            desempenho=desempenho, cobertura_por_campo=cobertura)
        # Valida ANTES de gravar qualquer byte do artefato: um diretorio com
        # booster e sem cartao seria carregado pelo servico sem reclamar.
        cartao_mod.valida(cart)

        pasta = (Path(args.destino) / ano / mes
                 / lugar(cidade).lower().replace(" ", "-") / alvo)
        grava_artefato(art, pasta, cart)
        cartao_mod.grava(cart, pasta)
        escritos.append(pasta)

        rmse = desempenho.get("rmse_reais") or desempenho.get("rmse")
        print(f"    gravado em {pasta}")
        if rmse:
            print(f"    holdout: {rmse}")

    print("\n" + "=" * 72)
    if not args.write:
        print("sem --write: nada foi treinado nem gravado.")
        return 0
    print(f"  {len(escritos)} modelo(s) gravado(s), {len(falhas)} recusado(s)")
    for cidade, alvo, motivo in falhas:
        print(f"    {cidade}/{alvo}: {motivo[:80]}")
    # Zero modelo gravado e falha, nunca sucesso.
    return 0 if escritos else 1


if __name__ == "__main__":
    raise SystemExit(main())
