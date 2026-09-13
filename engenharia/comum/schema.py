"""
Single source of truth for what a listing record may contain.

The persistence boundary is an ALLOWLIST: only fields named here survive.
Anything an adapter invents is dropped before it reaches a DataFrame.

Rationale (REFACTOR-PLAN §2.2): a denylist fails the first time a platform
emits a field nobody anticipated. P0 confirmed agency phone/email appear in
site chrome on every page, so leakage is the default outcome unless the
boundary is closed by construction.
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# The allowlist
# --------------------------------------------------------------------------

PROPERTY_FIELDS: frozenset[str] = frozenset({
    # identity / provenance
    "property_id",        # sha1(source_domain + platform listing id)
    "source_domain",      # legal-entity identifier, NOT personal data
    "source_platform",    # microsistec | universal | wordpress | ...
    "link",               # scrubbed listing URL

    # classification
    "transaction_type",   # venda | locacao
    "property_type",      # apartamento | casa | kitnet | sala | terreno | ...

    # geography
    #
    # The "no street addresses observed" note that stood here was true of
    # three platforms and false of the fourth. `universal` returns cep,
    # endereco and numero on 100% of listings (n=59, 2026-08-29); they were
    # being discarded at ingestion by a policy hold, not missing from source.
    # Hold lifted 2026-08-29: a CEP locates a property, not a person, and the
    # listing is a public advertisement for that property.
    #
    # `endereco` and `numero` stay unmapped. The CEP gives the block, which is
    # what prices; the door number adds nothing a model can use.
    "address",
    "neighborhood",
    "city",
    "state",
    "cep_prefix",         # first 5 digits -- coarse, safe to use as a category
    "cep",                # full 8 digits -- street segment. NOT a category:
                          # measured 32 distinct CEPs in 45 same-neighbourhood
                          # listings (1.4 per CEP). Useful only derived --
                          # geocoded, or as distance to a reference point.

    # money
    "price",
    "condo_fee",
    "iptu_tax",

    # physical
    "area_m2",
    "area_total_m2",
    "bedrooms",
    "bathrooms",
    "suites",
    "parking_spots",
    "floor_level",

    # content
    "description_clean",  # post-scrubber ONLY -- raw description never persists
    "amenities",          # list[str]; long, not wide (fixes SDD D-04)

    # lifecycle
    "extraction_date",
    "listing_first_seen",
    "listing_last_seen",
})

# Fields that carry free text and MUST pass the scrubber.
TEXT_FIELDS: frozenset[str] = frozenset({
    "description_clean", "address", "neighborhood", "city", "property_type",
})

# Fields holding URLs -- scrubbed differently (P0 §5.1: phones live in query
# strings, e.g. api.whatsapp.com/send?phone=5513...).
URL_FIELDS: frozenset[str] = frozenset({"link"})

# Fields we derive rather than extract. They cannot carry personal data, and
# scanning them with the full detector set produces false positives -- see the
# ID_DETECTORS note in privacy.py.
ID_FIELDS: frozenset[str] = frozenset({"property_id"})

REQUIRED_FIELDS: frozenset[str] = frozenset({
    "property_id", "source_domain", "source_platform", "link",
    "transaction_type", "extraction_date",
})

FIELD_TYPES: dict[str, str] = {
    "price": "float", "condo_fee": "float", "iptu_tax": "float",
    "area_m2": "int", "area_total_m2": "int", "bedrooms": "int",
    "bathrooms": "int", "suites": "int", "parking_spots": "int",
    "floor_level": "int",
    "amenities": "list",
}

TRANSACTION_TYPES = frozenset({"venda", "locacao"})


class SchemaViolation(Exception):
    """A record is missing required fields or carries an invalid value."""


# --------------------------------------------------------------------------
# Value coercion
# --------------------------------------------------------------------------

_RANGE_RE = re.compile(r"\d+\s*(?:-|a|até)\s*\d+")


def clean_currency(value):
    """'R$ 2.600,00' -> 2600.0 ; returns None rather than guessing."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    s = s.replace("R$", "").replace("\xa0", " ").strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None
    # pt-BR: '.' groups thousands, ',' is the decimal separator.
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def clean_int(value):
    """
    '50 m2' -> 50.

    Deliberately takes the FIRST integer group rather than concatenating
    every digit. The old implementation turned '50-140 m2' into 50140
    (SDD D-04 / §5.4); a range now yields its lower bound, and anything
    unparseable yields None -- never 0. 'Unknown' must not become 'zero',
    or the model trains on fabricated observations.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    m = re.search(r"\d+", str(value).replace(".", ""))
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def is_range(value) -> bool:
    """True when a scraped value looks like '50-140 m2' -- lower bound taken."""
    return bool(value) and bool(_RANGE_RE.search(str(value)))


def slugify(text) -> str:
    if not isinstance(text, str):
        return str(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cep_prefix(value):
    """'11075-350' -> '11075'. Coarse enough to join on, too coarse to locate."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits[:5] if len(digits) >= 5 else None


def cep(value):
    """
    '11075-350' or '11075350' -> '11075350'. Returns None unless it is exactly
    8 digits: a short or long string is a malformed field, not a CEP, and
    guessing which digits are missing would invent a location.
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) == 8 else None


def normalise_transaction(value):
    """Map site wording onto the controlled vocabulary."""
    if not value:
        return None
    v = slugify(value)
    if any(t in v for t in ("aluguel", "alugar", "locacao", "locar", "rent")):
        return "locacao"
    if any(t in v for t in ("venda", "vender", "comprar", "sale")):
        return "venda"
    return None


# --------------------------------------------------------------------------
# The boundary
# --------------------------------------------------------------------------

def filter_to_allowlist(raw: dict) -> tuple[dict, list[str]]:
    """
    Drop every key not in PROPERTY_FIELDS.

    Returns (kept, dropped_keys). Adapters are allowed to be sloppy and return
    whatever they find; this is where that stops mattering.
    """
    kept, dropped = {}, []
    for k, v in raw.items():
        if k in PROPERTY_FIELDS:
            kept[k] = v
        else:
            dropped.append(k)
    return kept, sorted(dropped)


# --------------------------------------------------------------------------
# Faixas plausíveis — CONTRATO-DE-DADOS.md §5
# --------------------------------------------------------------------------
#
# O contrato declarava estas faixas desde o início e NADA no código as
# verificava. Consequência medida em 2026-08-31, na base de 5.349 linhas:
#
#     condo_fee   713 de 4.119 valores abaixo de R$ 50   (17,4%)
#     iptu_tax    318 de 4.554 valores abaixo de R$ 10   ( 7,0%)
#
# Não é erro de conversão nosso: é SENTINELA da fonte. Os valores baixos se
# concentram nos tipos que não têm condomínio -- prédio 100%, galpão 93%,
# terreno 91%, loja 79%, casa 72%, contra 7% em apartamento. O anunciante põe
# `1`, `0,01` ou `10` para deixar o campo em branco, e uma página do VivaReal
# publica literalmente `R$ 1/mês`.
#
# Para um terreno, `None` é a verdade e R$ 0,01 é ficção que o modelo aprende.
# `condo_fee` é a 4ª feature por ganho, então isso não era detalhe: 9,2% dos
# valores no recorte modelado estavam corrompidos.
#
# Fora da faixa vira None e é CONTADO -- o relatório de coleta mostra quanto
# foi anulado por campo. Anular em silêncio trocaria um defeito visível por um
# invisível.
FAIXAS = {
    "condo_fee": (50.0, 20_000.0),
    "iptu_tax": (10.0, 20_000.0),
}

# `price` tem faixa por transação: locação de R$ 1.200 é normal e venda de
# R$ 1.200 não existe. Uma faixa única rejeitaria metade da locação -- erro que
# eu cometi na primeira checagem, em 2026-08-31.
FAIXAS_PRECO = {
    "venda": (50_000.0, 100_000_000.0),
    "locacao": (300.0, 200_000.0),
}


# Physical ranges from CONTRATO-DE-DADOS.md §6. They are NOT wired into
# `aplica_faixas` on purpose: on collection, nulling an out-of-range area would
# throw away a row we cannot re-fetch, and area above 2.000 m2 is legitimate for
# a plot or a warehouse. They exist as a single source for the calculator, which
# rejects with 422 instead of nulling -- there the user is right there to fix
# the typo, and answering an estimate for `area_m2 = 8000` is worse than an
# error. See Arquitetura §6: the service uses THESE ranges, not a copy of them.
FAIXAS_FISICAS = {
    "area_m2": (15.0, 2_000.0),
    "bedrooms": (0.0, 12.0),
    "bathrooms": (0.0, 15.0),
    "suites": (0.0, 12.0),
    "parking_spots": (0.0, 12.0),
    "floor_level": (0.0, 60.0),
}


def aplica_faixas(record: dict) -> tuple[dict, list[str]]:
    """
    Anula o que está fora da faixa do contrato. Devolve (registro, anulados).

    Só toca em campo PREENCHIDO e fora da faixa: ausente continua ausente, e
    a lista devolvida existe para o relatório poder mostrar o que sumiu.
    """
    out = dict(record)
    anulados: list[str] = []

    for campo, (lo, hi) in FAIXAS.items():
        v = out.get(campo)
        if v is None or not isinstance(v, (int, float)):
            continue
        if not (lo <= float(v) <= hi):
            out[campo] = None
            anulados.append(campo)

    v = out.get("price")
    faixa = FAIXAS_PRECO.get(out.get("transaction_type"))
    if faixa and isinstance(v, (int, float)) and v is not None:
        lo, hi = faixa
        if not (lo <= float(v) <= hi):
            out["price"] = None
            anulados.append("price")

    return out, anulados


def coerce_types(record: dict) -> dict:
    out = dict(record)
    for field, kind in FIELD_TYPES.items():
        if field not in out:
            continue
        if kind == "float":
            out[field] = clean_currency(out[field])
        elif kind == "int":
            out[field] = clean_int(out[field])
        elif kind == "list":
            v = out[field]
            if v is None:
                out[field] = []
            elif isinstance(v, (list, tuple, set)):
                out[field] = [str(x) for x in v]
            else:
                out[field] = [str(v)]
    if "transaction_type" in out:
        out["transaction_type"] = normalise_transaction(out["transaction_type"])
    # Derived here rather than in the adapter so every platform that ever
    # supplies a CEP gets the same 5-digit prefix, from one implementation.
    if out.get("cep"):
        out["cep"] = cep(out["cep"])
        if out["cep"] and not out.get("cep_prefix"):
            out["cep_prefix"] = cep_prefix(out["cep"])
    return out


def validate(record: dict) -> None:
    """Raise SchemaViolation if the record cannot be persisted."""
    missing = [f for f in REQUIRED_FIELDS if not record.get(f)]
    if missing:
        raise SchemaViolation(f"missing required field(s): {missing}")
    tt = record.get("transaction_type")
    if tt not in TRANSACTION_TYPES:
        raise SchemaViolation(
            f"transaction_type must be one of {sorted(TRANSACTION_TYPES)}, got {tt!r}"
        )
