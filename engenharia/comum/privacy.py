"""
PII scrubbing and the pre-write gate.

This module exists to make it structurally impossible to persist personal
data. It has three jobs:

  1. scrub_text()  -- redact personal data out of free text
  2. scrub_url()   -- drop contact URLs, strip phone-bearing query params
  3. assert_clean() -- THE GATE: scan an assembled frame and RAISE if anything
                       personal survived

Design bias (REFACTOR-PLAN §2.4): redact aggressively. A false positive costs
a few characters of description. A false negative is an LGPD exposure.

The gate is the one place in this codebase that must NOT swallow its error.
Everywhere else, silent failure loses data; here, silent failure creates a
compliance incident. See SDD D-13 for the pattern this deliberately breaks.

Patterns are grounded in what P0 actually found on Santos agency sites, not
in what seemed plausible -- notably phone numbers inside WhatsApp deep-link
query strings (api.whatsapp.com/send?phone=5513981959555).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

REDACTION = "[CONTATO]"


class PIIViolation(Exception):
    """Personal data reached the persistence boundary. Nothing was written."""


@dataclass(frozen=True)
class Hit:
    kind: str
    value: str
    field: str | None = None

    def __str__(self) -> str:
        where = f" in {self.field}" if self.field else ""
        return f"{self.kind}{where}: {self.value[:40]!r}"


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------
# Ordering matters: longer/more specific patterns run first so that a phone
# inside a whatsapp URL is consumed as a contact URL, not half-redacted.

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}", re.I)

# api.whatsapp.com/send?phone=55..., wa.me/55..., tel:, mailto:
CONTACT_URL = re.compile(
    r"(?:https?://)?(?:api\.whatsapp\.com/\S*|wa\.me/\S*|web\.whatsapp\.com/\S*)"
    r"|(?:tel|mailto|callto|sms|whatsapp):\S+",
    re.I,
)

# +55 (13) 98195-9555  |  (13) 3041-5555  |  13 98195 9555
PHONE_FORMATTED = re.compile(
    r"(?:\+?55[\s.\-]?)?\(?\d{2}\)?[\s.\-]?9?\d{4}[\s.\-]\d{4}"
)

# Bare runs: 5513981959555 (query strings), 13981959555.
# 11-13 digits: mobile with DDD is 11, with +55 is 13.
#
# NOT 10, deliberately. Zap listing ids are exactly 10 digits
# ('...-id-2809851822'), and a 10-digit floor would discard every legacy
# listing URL as if it were a landline. Bare unformatted landlines are rare
# in prose; formatted ones are caught by PHONE_FORMATTED.
PHONE_BARE = re.compile(r"(?<!\d)\d{11,13}(?!\d)")

# phone=55139..., telefone=..., celular=..., whatsapp=...
PHONE_PARAM = re.compile(
    r"(?:phone|fone|telefone|celular|whats?app|tel)\s*[=:]\s*\+?\d[\d\s.\-()]{7,}",
    re.I,
)

CPF = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
CNPJ = re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)")

# CRECI: 25497 | CRECI 44569 | CRECI-SP 12345-F | "creci": "329568-F"
#
# As aspas entram no separador por causa da forma JSON. Medido em
# lopes.com.br (2026-09-06): o payload do anuncio traz
# `"creci": "329568-F"` e a versao anterior desta regex NAO casava --
# o `"` nao estava na classe entre o rotulo e os digitos.
#
# Isto e a SEGUNDA camada. A primeira -- o allowlist do schema, que
# nunca deixa `agents` virar coluna -- ja barrava o caso. Mas um gate
# cego a uma forma real de PII e um gate pior, e o custo do conserto
# e um caractere.
CRECI = re.compile(
    r"CRECI[\s:.\-\"']*(?:[A-Z]{2}[\s:.\-\"']*)?\d+(?:\s*-\s*[A-Z])?", re.I
)

# Names only when contextually labelled -- unlabelled name detection is
# unreliable, and the allowlist is the real protection against name fields.
# The LABEL is case-insensitive ("Falar com", "falar com", "CORRETOR"), but
# the NAME must stay case-SENSITIVE -- capitalisation is what separates a
# personal name from ordinary prose. Hence the scoped (?i:...) rather than a
# module-level re.I, which would match "falar com apartamento novo".
LABELLED_NAME = re.compile(
    r"(?i:corretor|corretora|consultor|consultora|respons[aá]vel|anunciante|"
    r"atendente|propriet[aá]ri[oa]|falar\s+com|contat[oe]\s+com|procurar)"
    r"\s*[:\-]?\s*"
    r"([A-ZÀ-Ý][a-zà-ÿ']+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ']+)+)",
    re.U,
)

# Applied in order. Each is (kind, pattern).
DETECTORS: tuple[tuple[str, re.Pattern], ...] = (
    ("contact_url", CONTACT_URL),
    ("email", EMAIL),
    ("cpf", CPF),
    ("cnpj", CNPJ),
    ("creci", CRECI),
    ("phone_param", PHONE_PARAM),
    ("phone", PHONE_FORMATTED),
    ("phone_bare", PHONE_BARE),
    ("labelled_name", LABELLED_NAME),
)

# URLs get a narrower set. A bare digit run inside a URL path is almost
# always a listing id, whereas a phone in a URL is essentially always in a
# query parameter or a wa.me path -- both covered below. Running the full
# set over URLs would shred legitimate listing links.
URL_DETECTORS: tuple[tuple[str, re.Pattern], ...] = (
    ("contact_url", CONTACT_URL),
    ("email", EMAIL),
    ("cpf", CPF),
    ("cnpj", CNPJ),
    ("phone_param", PHONE_PARAM),
)

# Fields WE derive, which by construction cannot carry personal data.
# property_id is sha1(...)[:20] -- a hex string, and roughly one hex string in
# twenty contains a run of 11+ digits, which PHONE_BARE would read as a mobile
# number. That is the same false-positive class as the 10-digit Zap listing ids
# found during P1, and it would have failed the gate on one clean run in twenty.
# A hash cannot contain an email, a CPF or a wa.me link either, but those cost
# nothing to keep checking.
ID_DETECTORS: tuple[tuple[str, re.Pattern], ...] = (
    ("contact_url", CONTACT_URL),
    ("email", EMAIL),
    ("cpf", CPF),
    ("cnpj", CNPJ),
    ("phone_param", PHONE_PARAM),
)

# Known-safe shapes checked BEFORE phone detectors, so legitimate property
# data is not shredded. CEP is 5+3 digits and cannot match PHONE_FORMATTED
# (which needs 4+4), but the bare-digit detector could catch a CEP written
# without its hyphen, so we mask CEPs out and restore them afterwards.
CEP = re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)")
_CEP_TOKEN = "\x00CEP%d\x00"


def _mask_ceps(text: str) -> tuple[str, list[str]]:
    found: list[str] = []

    def repl(m):
        found.append(m.group(0))
        return _CEP_TOKEN % (len(found) - 1)

    return CEP.sub(repl, text), found


def _unmask_ceps(text: str, found: list[str]) -> str:
    for i, original in enumerate(found):
        text = text.replace(_CEP_TOKEN % i, original)
    return text


# --------------------------------------------------------------------------
# Scrubbing
# --------------------------------------------------------------------------

def scan_text(text, detectors=DETECTORS) -> list[Hit]:
    """Detect without modifying. Used by the gate."""
    if not isinstance(text, str) or not text:
        return []
    masked, _ = _mask_ceps(text)
    hits: list[Hit] = []
    for kind, pattern in detectors:
        for m in pattern.finditer(masked):
            value = m.group(1) if (kind == "labelled_name" and m.groups()) else m.group(0)
            hits.append(Hit(kind, value))
    return hits


def scrub_text(text):
    """
    Redact personal data out of free text.

    Returns (clean_text, hits). Redacted spans become a token rather than
    vanishing, so the redaction is auditable and the sentence stays readable.
    """
    if not isinstance(text, str) or not text:
        return text, []

    masked, ceps = _mask_ceps(text)
    hits: list[Hit] = []

    for kind, pattern in DETECTORS:
        def repl(m, _kind=kind):
            if _kind == "labelled_name" and m.groups():
                # keep the label ("Falar com"), redact only the name
                whole, name = m.group(0), m.group(1)
                hits.append(Hit(_kind, name))
                return whole.replace(name, REDACTION)
            hits.append(Hit(_kind, m.group(0)))
            return REDACTION

        masked = pattern.sub(repl, masked)

    clean = _unmask_ceps(masked, ceps)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean, hits


def is_contact_url(url) -> bool:
    """tel:, mailto:, wa.me, api.whatsapp.com -- never stored, never followed."""
    if not isinstance(url, str) or not url:
        return False
    return bool(CONTACT_URL.search(url.strip()))


def scrub_url(url):
    """
    Return a storable URL, or None if it must be discarded.

    P0 §5.1: WhatsApp deep links embed full mobile numbers in query strings,
    so a URL is not automatically safe just because it is a URL.
    """
    if not isinstance(url, str) or not url:
        return None
    url = url.strip()
    if is_contact_url(url):
        return None

    parts = urlsplit(url)
    if parts.query and (PHONE_PARAM.search(parts.query) or EMAIL.search(parts.query)
                        or PHONE_BARE.search(parts.query)):
        # Drop the query rather than the whole URL -- the path is usually the
        # listing itself and remains useful.
        url = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else parts.path

    if scan_text(url, URL_DETECTORS):
        return None
    return url


def scrub_record(record: dict, text_fields, url_fields) -> tuple[dict, list[Hit]]:
    """Apply the right scrubber to each field of one record."""
    out, all_hits = dict(record), []
    for field in url_fields:
        if field in out:
            out[field] = scrub_url(out[field])
    for field in text_fields:
        if field in out and isinstance(out[field], str):
            clean, hits = scrub_text(out[field])
            out[field] = clean
            all_hits.extend(Hit(h.kind, h.value, field) for h in hits)
    # amenities are free text too, just in a list
    if isinstance(out.get("amenities"), list):
        cleaned = []
        for item in out["amenities"]:
            c, hits = scrub_text(str(item))
            cleaned.append(c)
            all_hits.extend(Hit(h.kind, h.value, "amenities") for h in hits)
        out["amenities"] = cleaned
    return out, all_hits


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

def scan_records(records, url_fields=frozenset({"link"}),
                 id_fields=frozenset({"property_id"})) -> list[Hit]:
    """
    Scan every string value of every record. Detection only.

    URL-bearing and derived-id fields are scanned with narrower detector sets
    so that numeric listing ids and hash digests are not mistaken for phone
    numbers. Everything else gets the full set.
    """
    hits: list[Hit] = []
    for record in records:
        for field, value in record.items():
            if field in url_fields:
                detectors = URL_DETECTORS
            elif field in id_fields:
                detectors = ID_DETECTORS
            else:
                detectors = DETECTORS
            if isinstance(value, str):
                hits.extend(Hit(h.kind, h.value, field)
                            for h in scan_text(value, detectors))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        hits.extend(Hit(h.kind, h.value, field)
                                    for h in scan_text(item, detectors))
    return hits


def assert_clean(records, context: str = "", url_fields=frozenset({"link"}),
                 id_fields=frozenset({"property_id"})) -> None:
    """
    THE GATE. Call immediately before any write.

    Raises PIIViolation if anything personal survived. Callers must NOT catch
    this -- the run should exit non-zero with nothing written.
    """
    hits = scan_records(records, url_fields, id_fields)
    if hits:
        sample = "; ".join(str(h) for h in hits[:8])
        raise PIIViolation(
            f"PII GATE FAILED{' for ' + context if context else ''}: "
            f"{len(hits)} hit(s) across {len({h.field for h in hits})} field(s). "
            f"NOTHING WRITTEN. First hits -> {sample}"
        )


def assert_clean_dataframe(df, context: str = "", url_fields=frozenset({"link"}),
                           id_fields=frozenset({"property_id"})) -> None:
    """Convenience wrapper for the pandas path."""
    assert_clean(df.to_dict("records"), context or "dataframe", url_fields, id_fields)
