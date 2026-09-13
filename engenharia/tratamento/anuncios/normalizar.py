"""
Estágio de tratamento — `01_raw` → `02_processed`.

Lê o snapshot mais recente de cada domínio na camada crua, aplica o vocabulário
controlado de `utils/vocab.py` e grava a camada tratada.

**A camada crua nunca é alterada.** Ela registra o que a fonte disse; corrigir
ali apagaria a evidência e impediria reprocessar com regra nova. Toda execução
deste estágio é reproduzível a partir de `01_raw`.

Escopo desta versão: **vocabulário apenas.** Validação de faixa, correção de
separador decimal e os demais itens de `defeitos_de_extracao.md` são etapa
seguinte, e ficam explicitamente de fora para que o efeito do vocabulário seja
mensurável isolado.

Colunas acrescentadas, todas com sufixo que diz de onde vêm:

    property_type_raw    o valor original, para auditoria
    city_raw
    neighborhood_raw
    amenities_raw
    is_residencial       derivado do tipo canônico

As colunas originais passam a conter o valor normalizado. Quem quiser o cru
tem as `_raw` ao lado; quem quiser agrupar usa as normais e não precisa saber
que houve tratamento.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from comum import vocab

log = logging.getLogger("treat")

RAW_PADRAO = "dados/01_raw/listings"
OUT_PADRAO = "dados/02_processed/listings"


# A consolidacao mudou de dono E de regra. `consolida_mes` colapsava
# `property_id` entre meses; `historico.constroi_mes` colapsa so dentro do
# mes, que e o que faz a serie temporal existir.
from tratamento.anuncios.historico import constroi_mes


class Tratamento:
    """Aplica o vocabulário e conta o que mudou."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.stats: dict[str, dict] = {}

    def _conta(self, campo: str, antes: pd.Series, depois: pd.Series) -> None:
        self.stats[campo] = {
            "distintos_antes": int(antes.nunique()),
            "distintos_depois": int(depois.nunique()),
            "alterados": int((antes.fillna("") != depois.fillna("")).sum()),
            "nulos_novos": int(depois.isna().sum() - antes.isna().sum()),
        }

    def aplica(self) -> pd.DataFrame:
        d = self.df

        d["state_raw"] = d["state"]
        d["state"] = d["state"].map(vocab.normaliza_uf)
        self._conta("state", d["state_raw"], d["state"])

        d["city_raw"] = d["city"]
        d["city"] = d["city"].map(vocab.normaliza_municipio)
        self._conta("city", d["city_raw"], d["city"])

        # O mapa de bairro é construído a partir DESTE corpus, não de tabela
        # fixa: a lista de bairros muda por cidade e cresce com a base.
        mapa = vocab.mapa_canonico(d["neighborhood"].dropna())
        d["neighborhood_raw"] = d["neighborhood"]
        d["neighborhood"] = d["neighborhood"].map(
            lambda v: vocab.normaliza_bairro(v, mapa))
        self._conta("neighborhood", d["neighborhood_raw"], d["neighborhood"])

        d["property_type_raw"] = d["property_type"]
        d["property_type"] = d["property_type"].map(vocab.normaliza_tipo)
        self._conta("property_type", d["property_type_raw"], d["property_type"])
        d["is_residencial"] = d["property_type"].map(vocab.e_residencial)

        d["amenities_raw"] = d["amenities"]
        d["amenities"] = d["amenities"].map(vocab.normaliza_amenidades)
        rot_antes, rot_depois = Counter(), Counter()
        for v in d["amenities_raw"]:
            if v is not None:
                rot_antes.update(str(x).strip() for x in v)
        for v in d["amenities"]:
            rot_depois.update(v)
        self.stats["amenities"] = {
            "distintos_antes": len(rot_antes),
            "distintos_depois": len(rot_depois),
            "alterados": int(sum(
                1 for a, b in zip(d["amenities_raw"], d["amenities"])
                if sorted(str(x).strip() for x in (a if a is not None else [])) != sorted(b))),
            "nulos_novos": 0,
        }
        return d

    def relatorio(self) -> str:
        linhas = ["", "=" * 74, "RELATÓRIO DE TRATAMENTO — vocabulário", "=" * 74,
                  f"{'campo':<18}{'antes':>8}{'depois':>8}{'reducao':>10}{'linhas alt.':>13}"]
        for campo, s in self.stats.items():
            a, b = s["distintos_antes"], s["distintos_depois"]
            red = f"-{a-b}" if a >= b else f"+{b-a}"
            linhas.append(f"{campo:<18}{a:>8}{b:>8}{red:>10}{s['alterados']:>13}")
            if s["nulos_novos"] > 0:
                linhas.append(f"{'':<18}  {s['nulos_novos']} valores viraram nulo "
                              f"(não reconhecidos pelo vocabulário)")
        return "\n".join(linhas + ["=" * 74, ""])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trata a camada crua: vocabulário controlado")
    ap.add_argument("--raw-dir", default=RAW_PADRAO)
    ap.add_argument("--out-dir", default=OUT_PADRAO)
    ap.add_argument("--write", action="store_true", help="grava; sem isto é dry run")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    from tratamento.anuncios import particoes
    mes = getattr(args, 'mes', None) or (particoes.meses(args.raw_dir) or [None])[-1]
    if mes is None:
        print(f'nenhuma particao em {args.raw_dir}', file=sys.stderr)
        return 1
    df, _resumo = constroi_mes(args.raw_dir, mes)
    if not fontes:
        print(f"nenhum parquet em {args.raw_dir}", file=sys.stderr)
        return 1

    print(f"\n{len(fontes)} domínios, snapshot mais recente de cada:")
    quadros = []
    for dominio, (data, caminho) in sorted(fontes.items()):
        parte = pd.read_parquet(caminho)
        print(f"   {dominio:<28} {data}  {len(parte):>5} linhas")
        quadros.append(parte)
    df = pd.concat(quadros, ignore_index=True)

    t = Tratamento(df)
    tratado = t.aplica()
    print(t.relatorio())

    # Um tipo não reconhecido vira nulo. É informação, não falha: 0,15% das
    # linhas têm no campo `property_type` o título inteiro do anúncio.
    sem_tipo = int(tratado["property_type"].isna().sum())
    if sem_tipo:
        print(f"  {sem_tipo} linhas ({sem_tipo/len(tratado):.2%}) sem tipo canônico:")
        for v, n in tratado[tratado["property_type"].isna()]["property_type_raw"] \
                .value_counts().head(5).items():
            print(f"     {n:>3}  {str(v)[:64]}")

    if not args.write:
        print(f"\n[DRY RUN] {len(tratado)} linhas tratadas, nada gravado. Use --write.")
        return 0

    destino = Path(args.out_dir) / f"listings_{date.today().isoformat()}.parquet"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tratado.to_parquet(destino, index=False)
    print(f"\ngravado: {destino}  ({len(tratado)} linhas, {len(tratado.columns)} colunas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
