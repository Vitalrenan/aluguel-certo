"""
Canonical form of a street name.

THIS MODULE EXISTS SO THERE IS ONE OF IT. The lookup table is built from CNEFE
and joined against listing addresses; if the two sides normalise differently
they match almost nothing, the join returns a plausible small number, and
nothing raises. Both sides import `chave` from here.
"""
from __future__ import annotations

import re
import unicodedata

# Abreviacoes que o anuncio usa e o CNEFE nao. `Av. Paulista` e `Avenida
# Paulista` precisam colidir na mesma chave, senao a taxa de casamento despenca
# por motivo puramente ortografico.
TIPOS = {
    r"\bav\b": "avenida", r"\br\b": "rua", r"\bpc\b": "praca",
    r"\bpca\b": "praca", r"\bal\b": "alameda", r"\btv\b": "travessa",
    r"\brod\b": "rodovia", r"\bestr\b": "estrada", r"\blgo\b": "largo",
    r"\bvl\b": "vila", r"\bjd\b": "jardim", r"\bpq\b": "parque",
    r"\bmal\b": "marechal", r"\bpres\b": "presidente", r"\bprof\b": "professor",
    r"\bdr\b": "doutor", r"\bsen\b": "senador", r"\bdep\b": "deputado",
    r"\bcel\b": "coronel", r"\bcom\b": "comendador", r"\bsta\b": "santa",
    r"\bsto\b": "santo", r"\bs\b": "sao", r"\bcap\b": "capitao",
    r"\beng\b": "engenheiro", r"\bvisc\b": "visconde", r"\bbar\b": "barao",
}

# Ruido que so atrapalha a colisao.
PARADAS = {"de", "da", "do", "das", "dos", "e", "a", "o"}

# Abaixo disto a chave casa qualquer coisa. `rua` sozinha casaria a cidade
# inteira, e o casamento errado nao se distingue do certo depois do join.
MINIMO = 4


def chave(texto) -> str:
    """
    `Av. Paulista` and `AVENIDA PAULISTA` collapse to the same key.

    Literal comparison between the two sides matches zero.
    """
    if not isinstance(texto, str) or not texto.strip():
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for pad, cheio in TIPOS.items():
        t = re.sub(pad, cheio, t)
    palavras = [p for p in t.split() if p not in PARADAS]
    return " ".join(palavras)


def utilizavel(k: str) -> bool:
    """Whether a key is specific enough to join on."""
    return isinstance(k, str) and len(k) >= MINIMO


def lugar(texto) -> str:
    """
    Canonical form of a city or neighbourhood name, for GROUPING.

    `Guaruja` and `Guarujá` were two categories for one place, and `Santos`,
    `santos` and `SANTOS` were three. Measured on the ABT: normalising the
    neighbourhood alone is worth -2% error on sale and -8% on rent.

    Distinct from `chave`, which is for streets: this one keeps stopwords and
    word order intact, because `Jardim das Acacias` and `Jardim Acacias` are
    different neighbourhoods and collapsing them would merge two real places.
    """
    if not isinstance(texto, str) or not texto.strip():
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().title()
