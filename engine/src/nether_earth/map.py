"""Versioned YAML map loading skeleton.

M0 validates only generic document structure. Gameplay-specific map semantics are introduced by M2.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class MapValidationError(ValueError):
    """Raised when bootstrap map data is structurally invalid."""


@dataclass(frozen=True, slots=True)
class BootstrapMap:
    map_id: str
    version: int
    width: int
    height: int


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MapValidationError(f"{field} must be a positive integer")
    return value


def load_bootstrap_map(path: str | Path) -> BootstrapMap:
    """Load only the M0 bootstrap fields from a YAML map document."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MapValidationError("map document must be a mapping")

    map_id = raw.get("id")
    if not isinstance(map_id, str) or not map_id.strip():
        raise MapValidationError("id must be a non-empty string")

    return BootstrapMap(
        map_id=map_id,
        version=_positive_int(raw.get("version"), "version"),
        width=_positive_int(raw.get("width"), "width"),
        height=_positive_int(raw.get("height"), "height"),
    )
