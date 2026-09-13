"""
Partition discovery over the raw lake.

    01_raw/listings/{uf}/{cidade}/{ano}/{mes}/{dia}/{plataforma}/{dominio}/listings.parquet

WHY THIS IS ITS OWN MODULE. The old code found partitions with
`re.search(r"/(\\d{4}-\\d{2}-\\d{2})/", path)` scattered across two scripts.
Against the `{ano}/{mes}/{dia}` hierarchy that pattern matches NOTHING, and the
failure is the expensive kind: zero partitions found is indistinguishable from
an empty month, so the stage builds a clean, empty table and exits zero.

Reading a whole month is now one glob instead of a scan-and-filter, which is
the point of the hierarchy.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Particao:
    """One day of one source, for one city."""
    caminho: Path
    uf: str
    cidade: str
    ano: str
    mes: str
    dia: str
    plataforma: str
    dominio: str

    @property
    def mes_referencia(self) -> str:
        return f"{self.ano}-{self.mes}"

    @property
    def data(self) -> str:
        return f"{self.ano}-{self.mes}-{self.dia}"

    @property
    def fonte(self) -> str:
        return f"{self.cidade}/{self.dominio}"


def _da_caminho(p: Path, raiz: Path) -> Particao | None:
    """
    Parse a path into its partition keys, or None if it does not fit.

    Returns None rather than raising: a stray file in the lake must not abort
    a collection run. Callers that need to know count the Nones.
    """
    try:
        partes = p.relative_to(raiz).parts
    except ValueError:
        return None
    # uf / cidade / ano / mes / dia / plataforma / dominio / arquivo
    if len(partes) != 8:
        return None
    uf, cidade, ano, mes, dia, plataforma, dominio, _arquivo = partes
    if not (len(ano) == 4 and ano.isdigit()
            and len(mes) == 2 and mes.isdigit()
            and len(dia) == 2 and dia.isdigit()):
        return None
    return Particao(caminho=p, uf=uf, cidade=cidade, ano=ano, mes=mes, dia=dia,
                    plataforma=plataforma, dominio=dominio)


def do_mes(raiz: str | Path, mes: str) -> list[Particao]:
    """
    Every day partition of one month, `mes` as AAAA-MM.

    Sorted by date, oldest first. The order matters downstream: the monthly
    collapse keeps the LAST observation of each listing, and "last" is only
    meaningful against a stable order.
    """
    raiz = Path(raiz)
    if not raiz.exists():
        return []
    ano, num = mes.split("-")
    achadas = [_da_caminho(p, raiz)
               for p in raiz.glob(f"*/*/{ano}/{num}/*/*/*/*.parquet")]
    return sorted((a for a in achadas if a is not None),
                  key=lambda a: (a.data, a.dominio))


def meses(raiz: str | Path) -> list[str]:
    """Every month present in the lake, oldest first."""
    raiz = Path(raiz)
    if not raiz.exists():
        return []
    vistos = {a.mes_referencia
              for p in raiz.glob("*/*/*/*/*/*/*/*.parquet")
              if (a := _da_caminho(p, raiz)) is not None}
    return sorted(vistos)
