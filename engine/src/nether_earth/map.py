"""Versioned YAML map loading.

M0 (`BootstrapMap`/`load_bootstrap_map`) validates only generic document
identity/dimension fields and is kept working unchanged — M1 tests depend on
it. M2 (`WorldMap`/`load_world_map`) is the real versioned world-model
loader: it composes ``terrain.py``, ``structures.py``, and
``interactions.py`` section parsers to build the authoritative battlefield
representation described by `_specs/technical-spec.md` §§7-8, 10 and
`_specs/functional-spec.md` §§7, 9.

Determinism note: `_specs/milestones/02-map-world-model.md` requires that
loading the same map twice produces canonical-equivalent world state.
``WorldMap`` therefore stores only tuples/immutable mappings (never a
plain ``list``/``set`` whose contents could depend on insertion history) so
``==`` comparison is meaningful and independent of any incidental YAML
ordering choices made during parsing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nether_earth.ids import EntityId
from nether_earth.interactions import InteractionKind, InteractionPoint, parse_interaction_points
from nether_earth.occupancy import OccupancyGrid
from nether_earth.structures import Structure, parse_structures
from nether_earth.terrain import TerrainGrid, parse_terrain_grid


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


def _parse_spawn_positions(raw: "dict[str, Any] | None") -> dict[str, tuple[int, int]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise MapValidationError("spawn_positions must be a mapping")

    spawn_positions: dict[str, tuple[int, int]] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise MapValidationError("spawn_positions keys must be non-empty strings")
        if not isinstance(entry, dict):
            raise MapValidationError(f"spawn_positions[{name!r}] must be a mapping")
        x = entry.get("x")
        y = entry.get("y")
        if not isinstance(x, int) or isinstance(x, bool):
            raise MapValidationError(f"spawn_positions[{name!r}].x must be an integer")
        if not isinstance(y, int) or isinstance(y, bool):
            raise MapValidationError(f"spawn_positions[{name!r}].y must be an integer")
        spawn_positions[name] = (x, y)

    return spawn_positions


@dataclass(frozen=True, slots=True)
class WorldMap:
    """The authoritative battlefield representation loaded from a versioned map YAML.

    Composes terrain, structures, and canonical structure interaction
    metadata behind query helpers so later milestones consume one shared
    world-model contract rather than re-deriving these lookups themselves.
    ``occupancy()`` is computed on demand (never cached as a field) to keep
    this a plain immutable value type — see the module docstring on why
    equality/determinism matters here.
    """

    map_id: str
    version: int
    width: int
    height: int
    terrain: TerrainGrid
    structures: tuple[Structure, ...]
    interaction_points: tuple[InteractionPoint, ...]
    spawn_positions: Mapping[str, tuple[int, int]]

    def structure_by_id(self, entity_id: EntityId) -> Structure | None:
        """Return the structure with ``entity_id``, or ``None`` if absent."""
        for structure in self.structures:
            if structure.id == entity_id:
                return structure
        return None

    def interaction_points_for(
        self, structure_id: EntityId, kind: InteractionKind | None = None
    ) -> tuple[InteractionPoint, ...]:
        """Return interaction points tied to ``structure_id``, optionally filtered by ``kind``.

        Results preserve the map's declared ``interaction_points`` order.
        """
        return tuple(
            point
            for point in self.interaction_points
            if point.structure_id == structure_id and (kind is None or point.kind is kind)
        )

    def occupancy(self) -> OccupancyGrid:
        """Compute the ground-solid occupancy grid for this map's structures.

        Always recomputed from ``structures`` rather than cached, so
        ``WorldMap`` remains a plain immutable value type.
        """
        return OccupancyGrid.from_structures(self.structures)


def load_world_map(path: str | Path) -> WorldMap:
    """Load a versioned M2 world map YAML document into a :class:`WorldMap`.

    Top-level shape::

        id: <str>
        version: <positive int>
        width: <positive int>
        height: <positive int>
        terrain: {default: normal, cells: [...]}      # optional
        structures: [...]                              # optional
        interaction_points: [...]                      # optional
        spawn_positions: {name: {x: .., y: ..}, ...}   # optional

    Section parsing is delegated to ``terrain.parse_terrain_grid``,
    ``structures.parse_structures``, and
    ``interactions.parse_interaction_points`` (in that order, since
    interaction points validate references against parsed structures);
    their validation errors propagate unchanged rather than being wrapped or
    swallowed.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MapValidationError("map document must be a mapping")

    map_id = raw.get("id")
    if not isinstance(map_id, str) or not map_id.strip():
        raise MapValidationError("id must be a non-empty string")

    version = _positive_int(raw.get("version"), "version")
    width = _positive_int(raw.get("width"), "width")
    height = _positive_int(raw.get("height"), "height")

    terrain = parse_terrain_grid(raw.get("terrain"), width, height)
    structures = parse_structures(raw.get("structures"))
    interaction_points = parse_interaction_points(raw.get("interaction_points"), structures)
    spawn_positions = _parse_spawn_positions(raw.get("spawn_positions"))

    return WorldMap(
        map_id=map_id,
        version=version,
        width=width,
        height=height,
        terrain=terrain,
        structures=structures,
        interaction_points=interaction_points,
        spawn_positions=spawn_positions,
    )
