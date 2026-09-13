"""
Config v2 loader (REFACTOR-PLAN §6).

Replaces the v1 `scraper_config.yaml`, which declared a `sites` list and then
read only `sites[0]` (SDD D-17). The unit of work is now the cartesian product
of `sources` (a domain served by a platform) and `targets` (a geography).

Two flags -- `respect_robots` and `fail_on_pii` -- are parsed but may not be
turned off. They exist so a reader can see that compliance is on, not so an
operator can switch it off. Compliance should not be one YAML edit away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SUPPORTED_VERSION = 2

# Interval floor. 0.2 rps -> 5s between requests; nothing may go faster than
# this regardless of what the YAML asks for, because these are small
# businesses on shared hosting (§5).
MIN_INTERVAL_FLOOR = 1.0
DEFAULT_RATE_LIMIT_RPS = 0.2


class ConfigError(Exception):
    """The config cannot be honoured as written."""


@dataclass(frozen=True)
class Source:
    domain: str
    platform: str
    transactions: tuple[str, ...] = ("venda", "locacao")
    enabled: bool = True
    rate_limit_rps: float = DEFAULT_RATE_LIMIT_RPS
    feed_url: str | None = None
    notes: str | None = None
    # Marketplace nacional precisa da cidade na URL de descoberta; site de
    # imobiliaria local nao, porque o dominio JA e a cidade. Opcional por isso.
    # Adicionado 2026-08-30 para o adapter `olx` (D-026).
    city: str | None = None
    state: str | None = None
    # Cidades que esta fonte atende. Vazio = todas.
    #
    # Existe porque `collect.py` roda TODA fonte para TODO alvo: sem escopo,
    # adicionar Sao Paulo como alvo faria as 7 imobiliarias de Santos coletarem
    # os imoveis DELAS e gravarem na particao de Sao Paulo -- sem erro nenhum.
    cidades: tuple[str, ...] = ()

    def atende(self, cidade: str) -> bool:
        if not self.cidades:
            return True
        return str(cidade).strip().lower() in self.cidades

    @property
    def min_interval(self) -> float:
        """Seconds between requests to this host."""
        if self.rate_limit_rps <= 0:
            return MIN_INTERVAL_FLOOR
        return max(MIN_INTERVAL_FLOOR, 1.0 / self.rate_limit_rps)

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"


@dataclass(frozen=True)
class Target:
    state: str
    city: str


@dataclass(frozen=True)
class Config:
    sources: tuple[Source, ...]
    targets: tuple[Target, ...]
    headless: bool = False
    dry_run: bool = False
    respect_robots: bool = True
    fail_on_pii: bool = True
    raw_dir: str | None = None
    _raw: dict = field(default_factory=dict, repr=False)

    def enabled_sources(self, platform: str | None = None) -> tuple[Source, ...]:
        out = tuple(s for s in self.sources if s.enabled)
        if platform:
            out = tuple(s for s in out if s.platform == platform)
        return out

    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted({s.platform for s in self.sources if s.enabled}))


def _require_true(block: dict, key: str) -> bool:
    value = block.get(key, True)
    if value is not True:
        raise ConfigError(
            f"{key} cannot be disabled (got {value!r}). "
            "It is read for visibility, not as a switch -- see plan §6."
        )
    return True


def parse_config(data: dict) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")

    version = data.get("version")
    if version != SUPPORTED_VERSION:
        raise ConfigError(
            f"config version {version!r} unsupported; expected {SUPPORTED_VERSION}. "
            "v1 (`sites:`) is gone -- see plan §6."
        )

    raw_sources = data.get("sources") or []
    if not raw_sources:
        raise ConfigError("config declares no sources")

    sources, seen = [], set()
    for entry in raw_sources:
        domain = (entry.get("domain") or "").strip().lower()
        platform = (entry.get("platform") or "").strip().lower()
        if not domain:
            raise ConfigError(f"source without a domain: {entry!r}")
        if not platform:
            raise ConfigError(f"source {domain} has no platform")
        if domain in seen:
            raise ConfigError(f"duplicate source domain: {domain}")
        seen.add(domain)

        transactions = tuple(entry.get("transactions") or ("venda", "locacao"))
        unknown = [t for t in transactions if t not in ("venda", "locacao")]
        if unknown:
            raise ConfigError(f"{domain}: unknown transaction(s) {unknown}")

        sources.append(Source(
            domain=domain,
            platform=platform,
            transactions=transactions,
            enabled=bool(entry.get("enabled", True)),
            rate_limit_rps=float(
                DEFAULT_RATE_LIMIT_RPS if entry.get("rate_limit_rps") is None
                else entry["rate_limit_rps"]),
            feed_url=entry.get("feed_url") or None,
            notes=entry.get("notes") or None,
            cidades=tuple(str(c).strip().lower()
                          for c in (entry.get("cidades") or ())),
            city=(str(entry["city"]).strip().lower()
                  if entry.get("city") else None),
            state=(str(entry["state"]).strip().lower()
                   if entry.get("state") else None),
        ))

    raw_targets = data.get("targets") or []
    if not raw_targets:
        raise ConfigError("config declares no targets")
    targets = tuple(
        Target(state=str(t["state"]).strip().lower(), city=str(t["city"]).strip().lower())
        for t in raw_targets
    )

    params = data.get("scraping_params") or {}
    execution = data.get("execution") or {}

    return Config(
        sources=tuple(sources),
        targets=targets,
        headless=bool(params.get("headless", False)),
        dry_run=bool(execution.get("dry_run", False)),
        respect_robots=_require_true(params, "respect_robots"),
        fail_on_pii=_require_true(execution, "fail_on_pii"),
        raw_dir=(data.get("storage") or {}).get("raw_dir"),
        _raw=data,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")))
