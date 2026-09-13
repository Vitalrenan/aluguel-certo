"""
The Normalizer -- the single chokepoint between adapters and the lake.

Adapters are allowed to return whatever they find (§3.2). Everything that
makes a record persistable happens here, in one order, once:

    provenance -> allowlist (§2.2) -> scrub (§2.4) -> property_id
                -> type coercion -> validate -> [gate §2.5] -> write

The gate is deliberately NOT part of `normalize()`. Per-record scrubbing is a
transformation; the gate is a whole-batch assertion that runs immediately
before the write, so that a record which arrived by some path other than this
one is still caught. `finalize()` is the only sanctioned way to get records
out.

Two failure modes are treated very differently, on purpose:

  - A single unparseable listing is DROPPED and counted. Sites are messy and
    one bad page must not lose a run.
  - PII reaching the boundary, or a run producing zero rows, RAISES. The
    first is a compliance incident; the second is the silent-success failure
    Cloudflare exposed, where the pipeline reported OK and wrote nothing.
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from . import privacy, schema

log = logging.getLogger(__name__)

# Fields whose absence is a property of the platform, not an extraction bug.
# §13.2 measured iptu_tax at 2/6 on Group A; condo_fee and floor_level are
# similarly listing-dependent, and P0 found no street addresses at all --
# listings are neighbourhood-granular, which is good for the model and better
# for privacy.
EXPECTED_LOW_FILL = frozenset({
    "iptu_tax", "condo_fee", "area_total_m2", "floor_level",
    "address", "cep_prefix", "suites", "amenities",
})


class EmptyHarvest(Exception):
    """A run produced no rows. Never a success -- see §10."""


@dataclass
class Stats:
    seen: int = 0
    kept: int = 0
    rejected: Counter = field(default_factory=Counter)
    dropped_fields: Counter = field(default_factory=Counter)
    fora_da_faixa: Counter = field(default_factory=Counter)
    pii_hits: Counter = field(default_factory=Counter)
    filled: Counter = field(default_factory=Counter)

    def fill_rates(self) -> dict[str, float]:
        if not self.kept:
            return {}
        return {
            f: self.filled.get(f, 0) / self.kept
            for f in sorted(schema.PROPERTY_FIELDS)
        }

    def report(self) -> str:
        lines = [
            f"seen={self.seen} kept={self.kept} "
            f"rejected={sum(self.rejected.values())}",
        ]
        if self.rejected:
            lines.append("  rejected: " + ", ".join(
                f"{k}={v}" for k, v in self.rejected.most_common()))
        if self.dropped_fields:
            top = ", ".join(f"{k}={v}" for k, v in self.dropped_fields.most_common(8))
            lines.append(f"  dropped by allowlist: {top}")
        if self.fora_da_faixa:
            top = ", ".join(f"{k}={v}" for k, v in self.fora_da_faixa.most_common())
            lines.append(f"  anulados fora da faixa do contrato: {top}")
        if self.pii_hits:
            lines.append("  redacted: " + ", ".join(
                f"{k}={v}" for k, v in self.pii_hits.most_common()))
        if self.kept:
            lines.append("  fill rates:")
            for fname, rate in self.fill_rates().items():
                flag = ""
                if rate < 0.5:
                    flag = "  (expected-low)" if fname in EXPECTED_LOW_FILL else "  <-- LOW"
                lines.append(f"    {fname:<22} {rate:6.1%}{flag}")
        return "\n".join(lines)


def normalise_url(url: str) -> str:
    """
    Canonical form used for the property_id fallback hash.

    Scheme, `www.`, query, fragment and trailing slash all vary between the
    sitemap, the category page and the canonical link for the same listing.
    None of them identify it.
    """
    parts = urlsplit(url.strip())
    host = (parts.netloc or "").lower().removeprefix("www.")
    path = (parts.path or "").rstrip("/").lower()
    return f"{host}{path}"


def derive_property_id(source_domain: str, listing_id, link) -> str | None:
    """
    sha1(domain + platform listing id), with a URL fallback (§4.1).

    Domain-scoped on purpose: the same apartment on five agency sites is five
    observations, not one. Deduplication happens downstream via
    property_cluster_id (§4.2), where the price spread between agencies is
    kept as a feature rather than averaged away.
    """
    if listing_id:
        seed = f"{source_domain}|{listing_id}"
    elif link:
        seed = f"url|{normalise_url(link)}"
    else:
        return None
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


class Normalizer:
    """Stateful across a run so it can report fill rates at the end (§10)."""

    def __init__(self, source_domain: str, source_platform: str,
                 target=None, extraction_date: str | None = None):
        self.source_domain = source_domain
        self.source_platform = source_platform
        self.target = target
        self.extraction_date = extraction_date or date.today().isoformat()
        self.stats = Stats()
        self.records: list[dict] = []

    # -- the pipeline ------------------------------------------------------

    def normalize(self, raw: dict, discovered) -> dict | None:
        """
        One raw adapter dict -> one persistable record, or None if unusable.

        Returning None is normal. Raising is not, except for PII.
        """
        self.stats.seen += 1

        merged = self._with_provenance(raw, discovered)
        kept, dropped = schema.filter_to_allowlist(merged)
        for key in dropped:
            self.stats.dropped_fields[key] += 1

        scrubbed, hits = privacy.scrub_record(
            kept, schema.TEXT_FIELDS, schema.URL_FIELDS)
        for hit in hits:
            self.stats.pii_hits[hit.kind] += 1

        if scrubbed.get("link") is None:
            # scrub_url discarded it: a contact URL, or a listing URL carrying
            # a personal identifier we cannot strip. No link means no re-fetch
            # and no dedup key, so the record is not worth keeping.
            self.stats.rejected["link_discarded_by_scrubber"] += 1
            return None

        scrubbed["property_id"] = derive_property_id(
            self.source_domain,
            getattr(discovered, "listing_id", None),
            scrubbed.get("link"),
        )

        record = schema.coerce_types(scrubbed)

        # Faixa plausivel do CONTRATO-DE-DADOS.md §5. Fora dela vira None e e
        # contado: `R$ 0,01` de condominio num terreno e sentinela do
        # anunciante, nao taxa. Ver `schema.aplica_faixas`.
        record, anulados = schema.aplica_faixas(record)
        for campo in anulados:
            self.stats.fora_da_faixa[campo] += 1

        try:
            schema.validate(record)
        except schema.SchemaViolation as exc:
            reason = str(exc).split(":")[0]
            self.stats.rejected[reason] += 1
            log.debug("rejected %s: %s", record.get("link"), exc)
            return None

        record = {f: record.get(f) for f in sorted(schema.PROPERTY_FIELDS)}

        self.stats.kept += 1
        for fname, value in record.items():
            if value not in (None, "", [], {}):
                self.stats.filled[fname] += 1
        self.records.append(record)
        return record

    def _with_provenance(self, raw: dict, discovered) -> dict:
        """
        Adapter output, under the facts the collector already knows.

        Ordering matters. Hints from the URL grammar go UNDERNEATH parsed
        values -- the page wins where the two disagree. Provenance goes ON
        TOP: an adapter does not get to invent its own source_domain, and
        `transaction_type` comes from the discovery path because §13.3
        measured that it exists nowhere else on these platforms.
        """
        merged = {}
        if discovered is not None:
            merged.update(getattr(discovered, "hints", None) or {})
        merged.update(raw or {})

        if discovered is not None:
            merged["link"] = discovered.url
            merged["transaction_type"] = discovered.transaction_type

        merged["source_domain"] = self.source_domain
        merged["source_platform"] = self.source_platform
        merged["extraction_date"] = self.extraction_date

        if self.target is not None:
            merged.setdefault("city", getattr(self.target, "city", None))
            merged.setdefault("state", getattr(self.target, "state", None))

        merged.setdefault("listing_first_seen", self.extraction_date)
        merged["listing_last_seen"] = self.extraction_date
        return merged

    # -- the way out -------------------------------------------------------

    def finalize(self, context: str = "", allow_empty: bool = False) -> list[dict]:
        """
        Run the gate and hand over the records. The ONLY sanctioned exit.

        Raises PIIViolation if anything personal survived, and EmptyHarvest if
        the run produced nothing. Callers must not catch PIIViolation.
        """
        privacy.assert_clean(
            self.records,
            context or f"{self.source_platform}:{self.source_domain}",
            url_fields=schema.URL_FIELDS,
            id_fields=schema.ID_FIELDS,
        )
        if not self.records and not allow_empty:
            raise EmptyHarvest(
                f"{self.source_platform}:{self.source_domain} produced 0 rows from "
                f"{self.stats.seen} listing(s). A zero-row run is a failure, not a "
                f"success -- {self.stats.report()}"
            )
        return list(self.records)
