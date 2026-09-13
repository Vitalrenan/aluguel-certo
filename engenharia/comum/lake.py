"""
Partitioned writes for the raw listings path.

    01_raw/listings/{state}/{city}/{year}/{month}/{day}/{platform}/{domain}/listings.parquet

Metadata still appears in the path so a human can browse the lake, but
consumers read it from COLUMNS. `city`, `state`, `source_domain`,
`source_platform` and `transaction_type` are all real fields now (§4.1), which
retires the path-parsing that SDD §5.2 flagged as the most brittle interface
in the subsystem.

This is a new module rather than a rewrite of `data_manager.py`. The legacy
DataManager still serves the Zap steps, which stay functional until the Zap
adapter is ported in P4 (§8); changing it underneath them would break a
working path to serve one that has no adapters yet.

Two behaviours differ from DataManager, deliberately:

  1. Write failures RAISE. DataManager prints the exception and returns, so a
     failed write looks exactly like a successful one from the caller's side
     (SDD D-13). A collection run that writes nothing must exit non-zero.
  2. The PII gate is re-run here, immediately before the write. The Normalizer
     already runs it in `finalize()`; running it again at the actual write is
     cheap, and it means no future caller can reach the lake by another route
     without passing the gate (§2.5).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import privacy, schema

log = logging.getLogger(__name__)

# Prefixo PRÓPRIO do ambiente refatorado, decidido em 2026-09-12.
#
# O lago antigo continua em `gs://dataacquisition/01_raw/`, intocado, e é o que
# alimenta o `pipeline/` de hoje. Escrever a partição nova ano/mês/dia por cima
# dele misturaria dois formatos no mesmo prefixo, e ninguém olhando o caminho
# saberia qual dos dois estava lendo.
#
# Um bucket separado seria melhor ainda, e não foi possível: a conta de serviço
# `aluguelcerto-dataeng` tem permissão de objeto, não de bucket -- verificado em
# 2026-09-12, 403 em `storage.buckets.get` e em `storage.buckets.list`.
DEFAULT_RAW_DIR = "gs://dataacquisition/refatoramento/01_raw/listings"


def _path_token(value: str) -> str:
    """
    Path-safe form that preserves the identifier as written.

    Deliberately not `slugify`: platform names carry meaning in their
    underscores -- `microsistec_a` and `microsistec_b` are two adapters over
    two URL grammars (§13.1), and slugifying both to `microsistec-a`/`-b`
    would quietly rename the partition. Domains, likewise, are the provenance
    key; `casabellaimoveis.com` must stay itself, dots included.
    """
    return re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower()).strip("-")


class WriteFailed(Exception):
    """The write did not happen. Never swallowed -- see §7 and SDD D-13."""


@dataclass
class LakeWriter:
    raw_dir: str = DEFAULT_RAW_DIR
    dry_run: bool = False
    extraction_date: str = ""
    storage_options: dict | None = None

    def __post_init__(self):
        self.raw_dir = (self.raw_dir or DEFAULT_RAW_DIR).rstrip("/")
        self.extraction_date = self.extraction_date or date.today().isoformat()

    @property
    def is_cloud(self) -> bool:
        return self.raw_dir.startswith("gs://")

    def partition(self, state: str, city: str, platform: str, domain: str) -> str:
        # Year / month / day as THREE segments, not one `2026-08-27` token.
        #
        # The monthly history table in the treatment layer reads a whole month
        # at a time. With a flat date token that means listing every partition
        # in the lake and filtering the names; with the hierarchy it is one
        # directory. The day still exists as its own level, so two collections
        # on different days never overwrite each other.
        ano, mes, dia = self.extraction_date.split("-")
        return "/".join([
            self.raw_dir,
            schema.slugify(state),
            schema.slugify(city),
            ano, mes, dia,
            _path_token(platform),
            _path_token(domain),
        ])

    def path_for(self, state: str, city: str, platform: str, domain: str) -> str:
        # The date lives in the path now, so it leaves the filename. Repeating
        # it in both places invites the two to disagree after a move.
        part = self.partition(state, city, platform, domain)
        return f"{part}/listings.parquet"

    def write(self, records, state: str, city: str, platform: str, domain: str) -> str | None:
        """
        Persist one source's harvest. Returns the path written, or None on dry run.

        Records must already have passed `Normalizer.finalize()`. The gate runs
        again here regardless: it is the last line before the data leaves the
        process, and that is where an assertion belongs.
        """
        if not records:
            raise WriteFailed(
                f"{platform}:{domain} -- refusing to write 0 rows. "
                "A zero-row write is the silent-success failure mode (§10)."
            )

        privacy.assert_clean(records, f"write:{platform}:{domain}",
                             url_fields=schema.URL_FIELDS,
                             id_fields=schema.ID_FIELDS)

        path = self.path_for(state, city, platform, domain)

        if self.dry_run:
            log.info("[DRY RUN] would write %d rows -> %s", len(records), path)
            return None

        df = pd.DataFrame.from_records(records)
        # Column order is the allowlist, sorted. Stable output beats whatever
        # order the last adapter happened to emit.
        df = df.reindex(columns=sorted(schema.PROPERTY_FIELDS))

        # FUNDE com o que ja existe na particao, em vez de sobrescrever.
        #
        # A particao e um dia por fonte, um arquivo dentro dela.
        # Ate 2026-09-05 uma segunda corrida no mesmo dia APAGAVA a primeira --
        # e o risco vira certeza quando combinada com `--novos-apenas`, que
        # pula o que ja esta no lake: a corrida coletaria so os ineditos e
        # gravaria so eles, destruindo o que pulou. Uma coleta de 988 linhas
        # quase foi perdida assim.
        #
        # `property_id` decide: repetido, a linha NOVA vence, porque preco e
        # data de anuncio mudam entre coletas e a mais recente e a verdadeira.
        existente = self._le_existente(path)
        if existente is not None and len(existente):
            antes = len(existente)
            df = (pd.concat([existente, df], ignore_index=True)
                    .drop_duplicates(subset="property_id", keep="last")
                    .reindex(columns=sorted(schema.PROPERTY_FIELDS)))
            log.info("particao ja tinha %d linha(s); apos a fusao: %d",
                     antes, len(df))

        try:
            if self.is_cloud:
                df.to_parquet(path, index=False,
                              storage_options=self.storage_options)
            else:
                from pathlib import Path
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(path, index=False)
        except Exception as exc:
            raise WriteFailed(f"write to {path} failed: {exc}") from exc

        log.info("wrote %d rows -> %s", len(df), path)
        return path

    def _le_existente(self, path: str):
        """
        A particao que ja esta no disco, ou None.

        Falha de leitura levanta: um parquet corrompido ou ilegivel nao pode
        virar "particao vazia" e autorizar a sobrescrita silenciosa do que ele
        contem.
        """
        if self.is_cloud:
            return None                      # fusao em GCS ainda nao suportada
        from pathlib import Path
        if not Path(path).exists():
            return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            raise WriteFailed(
                f"particao existente {path} nao pode ser lida ({type(exc).__name__}). "
                "Recusando gravar por cima -- isso apagaria dado sem conferir."
            ) from exc
