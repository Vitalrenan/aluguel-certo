"""
Which (city, target) pairs are served, and which model answers.

THE ENVIRONMENT VARIABLE IS GONE. The old service read
`ALUGUELCERTO_SEGMENTO` once at start-up and served that segment for its whole
life. A screen with a city selector cannot be served that way: the process
would have to be restarted to answer about another city.

THERE IS NO POOLED FALLBACK. A pair with no model gets an explicit refusal
carrying `motivo`, never an approximate number. Serving a pooled model and
calling it the city's estimate produces a figure wearing the same confidence as
a measured one, and nobody reading the screen can tell them apart.

AVAILABILITY IS PER PAIR, NOT PER CITY. São Paulo has sale volume and almost no
rent, so the city is available and the pair (São Paulo, rent) is not. Code that
treats a city as available in one piece will offer rent in São Paulo and get a
refusal after the user has filled the whole form.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


def slug(texto) -> str:
    """
    City name to path segment, the same way the trainer wrote it.

    `São Paulo` in the config and `sao-paulo` on disk are the same city; a
    literal comparison finds no model and the API would report a configured
    city as unavailable.
    """
    if not isinstance(texto, str):
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.strip().lower().replace(" ", "-")


@dataclass(frozen=True)
class Par:
    cidade: str
    alvo: str
    disponivel: bool
    motivo: str | None
    n_treino_medido: int | None


class SemModelo(Exception):
    """The pair is not served. Carries the reason the screen shows."""

    def __init__(self, cidade: str, alvo: str, motivo: str):
        self.cidade, self.alvo, self.motivo = cidade, alvo, motivo
        super().__init__(f"{cidade}/{alvo}: {motivo}")


@lru_cache(maxsize=4)
def catalogo(caminho: str) -> tuple[Par, ...]:
    """Every pair the config declares, available or not."""
    cfg = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    pares = []
    for c in cfg.get("cidades", []):
        for alvo, info in (c.get("alvos") or {}).items():
            pares.append(Par(
                cidade=c["nome"], alvo=alvo,
                disponivel=bool(info.get("disponivel")),
                motivo=info.get("motivo"),
                n_treino_medido=info.get("n_treino_medido")))
    return tuple(pares)


def procura(catalogo_: tuple[Par, ...], cidade: str, alvo: str) -> Par | None:
    alvo_k, cidade_k = alvo.strip().lower(), slug(cidade)
    for p in catalogo_:
        if slug(p.cidade) == cidade_k and p.alvo == alvo_k:
            return p
    return None


def pasta_do_modelo(raiz: str | Path, cidade: str, alvo: str) -> Path | None:
    """
    The most recent {ano}/{mes}/{cidade}/{alvo} directory, or None.

    Sorted by year and month descending, and the first COMPLETE one wins. A
    directory holding a booster and no card is skipped rather than loaded: the
    card is what says the scope, and an estimate that cannot say its scope is
    not servable.
    """
    raiz = Path(raiz)
    if not raiz.exists():
        return None
    candidatos = sorted(
        raiz.glob(f"*/*/{slug(cidade)}/{alvo.strip().lower()}"),
        key=lambda p: (p.parent.parent.parent.name, p.parent.parent.name),
        reverse=True)
    for c in candidatos:
        if (c / "model_card.json").exists() and (c / "modelo.txt").exists():
            return c
    return None


def resolve(raiz_modelos: str | Path, caminho_cidades: str,
            cidade: str, alvo: str) -> tuple[Path, dict]:
    """
    The model directory and its card, or `SemModelo` with the reason.

    The config is consulted BEFORE the disk. A pair the config marks
    unavailable must refuse with its own wording even if a stale directory
    happens to exist -- otherwise turning a city off would not turn it off.
    """
    par = procura(catalogo(str(caminho_cidades)), cidade, alvo)
    if par is None:
        raise SemModelo(cidade, alvo,
                        "cidade ou transacao fora do escopo atendido")
    if not par.disponivel:
        raise SemModelo(cidade, alvo,
                        par.motivo or "modelo estatistico ainda nao disponivel")

    pasta = pasta_do_modelo(raiz_modelos, par.cidade, alvo)
    if pasta is None:
        # Config diz disponivel e o disco nao tem. E erro de operacao, nao do
        # pedido: o texto diz isso em vez de culpar o usuario.
        raise SemModelo(cidade, alvo,
                        "modelo declarado disponivel mas ausente no disco -- "
                        "a publicacao do modelo falhou")

    cartao = json.loads((pasta / "model_card.json").read_text(encoding="utf-8"))
    return pasta, cartao
