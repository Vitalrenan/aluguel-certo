"""
One-time reorganisation of the raw lake into the year/month/day layout.

    python migrar_particoes.py --origem ... --destino ...          # relata
    python migrar_particoes.py --origem ... --destino ... --write  # copia

    de:   {uf}/{cidade}/{aaaa-mm-dd}/{plataforma}/{dominio}/listings_{data}.parquet
    para: {uf}/{cidade}/{aaaa}/{mm}/{dd}/{plataforma}/{dominio}/listings.parquet

COPIA, nunca move. O lago antigo alimenta o `pipeline/` de hoje, que continua
rodando durante a migracao inteira; mover deixaria os dois quebrados ao mesmo
tempo, e o unico ganho seria disco.

O CONTEUDO NAO E TOCADO. Cada parquet e copiado byte a byte, e a verificacao
compara contagem de linhas e de `property_id` distintos na origem e no destino.
Reescrever o quadro com pandas aqui mudaria tipos silenciosamente -- um
`int64` vira `float64` ao passar por um nulo -- e a camada crua e justamente a
que registra o que a fonte disse.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

DATA_ANTIGA = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def planeja(origem: Path, destino: Path) -> list[tuple[Path, Path]]:
    """
    (de, para) for every partition that fits the old layout.

    A file that does not fit is skipped and counted, never guessed at: a path
    this function cannot read is a path it must not invent a date for.
    """
    plano = []
    for p in sorted(origem.rglob("*.parquet")):
        partes = p.relative_to(origem).parts
        # uf / cidade / data / plataforma / dominio / arquivo
        if len(partes) != 6:
            continue
        uf, cidade, data, plataforma, dominio, _ = partes
        m = DATA_ANTIGA.match(data)
        if not m:
            continue
        ano, mes, dia = m.groups()
        alvo = (destino / uf / cidade / ano / mes / dia
                / plataforma / dominio / "listings.parquet")
        plano.append((p, alvo))
    return plano


def confere(de: Path, para: Path) -> tuple[int, int, bool]:
    """Rows and distinct ids on both sides, and whether they agree."""
    a = pd.read_parquet(de, columns=["property_id"])
    b = pd.read_parquet(para, columns=["property_id"])
    igual = (len(a) == len(b)
             and a.property_id.nunique() == b.property_id.nunique())
    return len(a), a.property_id.nunique(), igual


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origem", required=True)
    ap.add_argument("--destino", required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    origem, destino = Path(args.origem), Path(args.destino)
    if not origem.exists():
        print(f"origem nao existe: {origem}", file=sys.stderr)
        return 1

    plano = planeja(origem, destino)
    todos = list(origem.rglob("*.parquet"))
    fora = len(todos) - len(plano)

    print("=" * 72)
    print(f"migracao de particao -- {len(plano)} arquivo(s) no formato antigo")
    if fora:
        print(f"  {fora} arquivo(s) FORA do formato, ignorados e nao adivinhados")
    print("=" * 72)

    for de, para in plano:
        print(f"  {de.relative_to(origem).as_posix()}")
        print(f"    -> {para.relative_to(destino).as_posix()}")

    if not args.write:
        print("\nsem --write: nada foi copiado.")
        return 0

    linhas_total = 0
    divergentes = []
    for de, para in plano:
        para.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(de, para)
        n, ids, igual = confere(de, para)
        linhas_total += n
        if not igual:
            divergentes.append(para)

    print(f"\n  copiados {len(plano)} arquivo(s), {linhas_total} linha(s)")
    if divergentes:
        # Falha de copia LEVANTA. Um destino que nao confere com a origem e
        # dado corrompido, e devolver zero aqui o deixaria passar por bom.
        raise RuntimeError(
            f"{len(divergentes)} arquivo(s) nao conferem com a origem: "
            f"{[str(d) for d in divergentes[:3]]}")
    print("  verificacao: linhas e property_id distintos batem em todos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
