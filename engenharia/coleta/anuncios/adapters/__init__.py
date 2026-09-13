"""
Adapter registry (REFACTOR-PLAN §3.1).

Maps a configured `platform` string onto the class that knows how to read it.
Registration is by decorator so adding a platform means adding one module and
importing it here -- there is no dispatch table to forget to update.
"""
from __future__ import annotations

from .base import (
    AdapterError,
    DiscoveredURL,
    ListingSource,
    Payload,
    TRANSACTIONS,
)

_REGISTRY: dict[str, type[ListingSource]] = {}


class UnknownPlatform(AdapterError):
    """No adapter is registered for this platform."""


def register(cls: type[ListingSource]) -> type[ListingSource]:
    """Class decorator. `platform` must be set and unique."""
    platform = getattr(cls, "platform", "")
    if not platform:
        raise AdapterError(f"{cls.__name__} has no platform attribute")
    existing = _REGISTRY.get(platform)
    if existing is not None and existing is not cls:
        raise AdapterError(
            f"platform {platform!r} already registered to {existing.__name__}"
        )
    _REGISTRY[platform] = cls
    return cls


def resolve(platform: str) -> type[ListingSource]:
    try:
        return _REGISTRY[platform]
    except KeyError:
        raise UnknownPlatform(
            f"no adapter for platform {platform!r}; registered: {registered()}"
        ) from None


def build(source, session, target=None) -> ListingSource:
    """Instantiate the adapter a config Source asks for."""
    return resolve(source.platform)(session, source, target)


def registered() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def is_registered(platform: str) -> bool:
    return platform in _REGISTRY


# Adapters register themselves on import. A platform with no module here is
# reported by `collect.py` as an explicit SKIP with a reason, never as a
# silent no-op.
from . import lopes  # noqa: E402,F401
from . import microsistec_a  # noqa: E402,F401
from . import microsistec_b  # noqa: E402,F401
from . import universal  # noqa: E402,F401
from . import olx  # noqa: E402,F401
from . import veploy  # noqa: E402,F401
from . import vivareal  # noqa: E402,F401

__all__ = [
    "AdapterError", "DiscoveredURL", "ListingSource", "Payload", "TRANSACTIONS",
    "UnknownPlatform", "register", "resolve", "build", "registered",
    "is_registered",
]
