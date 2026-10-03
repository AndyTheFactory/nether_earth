"""Terrain grid data model and YAML parsing.

Per `_specs/technical-spec.md` §7.3 and `_specs/functional-spec.md` §7.2,
terrain is a property of a grid cell, not a solid occupant. This module
represents terrain purely as data: a per-cell :class:`TerrainType` lookup.

Movement legality/penalties (bipod/tracks/anti-grav capability rules) are
`movement.py`'s concern (per `_specs/technical-spec.md` §7.3's "exact
speed/tick values must be derived from verified ZX Spectrum behavior"). This
module intentionally stops at "what terrain is this cell", not "can this
robot enter it".

Piece heights
-------------
Each terrain cell may also carry the height of its map piece, as
``Ld7bc_map_piece_heights`` gives it (rough 2 or 3, mountain 6, ditch and
normal 0); :meth:`TerrainGrid.height_at` reads it and ``debris_height`` is the
height of the rough piece a nuclear blast leaves. The height is map
data, decoded with its provenance (``data/maps/decode_zx_terrain.py``), not a
function of the terrain class: rough pieces come in two heights. A cell with
no ``height`` is 0 high, so hand-written test maps keep flat terrain.
`collision.surface_height_at` combines it with structure heights.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


class TerrainType(Enum):
    """The terrain classes required by `_specs/functional-spec.md` §7.2.

    They correspond to the Spectrum map element types
    (`_specs/open-questions.md` §4): normal = types 0-1, rough = 2-7,
    mountain = 8-11, ditch = 12-14.

    Values are lowercase strings so the enum round-trips directly against the
    YAML map format without a separate string<->enum lookup table.
    """

    NORMAL = "normal"
    ROUGH = "rough"
    MOUNTAIN = "mountain"
    DITCH = "ditch"


class TerrainValidationError(ValueError):
    """Raised when a YAML terrain section is structurally or semantically invalid.

    Kept distinct from ``map.MapValidationError`` because terrain parsing is
    owned by this module; ``map.load_world_map`` propagates this error type
    unchanged rather than wrapping/renaming it, so callers that only care
    about "map loading failed" can catch ``ValueError`` while callers that
    care about the specific section can catch this type.
    """


@dataclass(frozen=True, slots=True)
class TerrainGrid:
    """Deterministic per-cell terrain lookup for a map of ``width`` x ``height``.

    ``cells`` holds only the explicitly-specified overrides (sparse); any
    in-bounds cell not present in ``cells`` resolves to ``default``. This
    keeps the common case (a mostly-``NORMAL`` battlefield with a handful of
    rough/ditch cells) cheap to represent and keeps two loads of the same
    YAML document trivially equal (``cells`` is an immutable mapping built
    from the same input regardless of any incidental YAML list ordering).
    """

    width: int
    height: int
    cells: Mapping[tuple[int, int], TerrainType]
    default: TerrainType = TerrainType.NORMAL
    heights: Mapping[tuple[int, int], int] = field(default_factory=dict)
    debris_height: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise ValueError("TerrainGrid width must be a positive integer")
        if self.height <= 0:
            raise ValueError("TerrainGrid height must be a positive integer")
        if self.debris_height < 0 or any(h < 0 for h in self.heights.values()):
            raise ValueError("TerrainGrid heights must be non-negative")
        # Zero heights are dropped so equal terrain compares equal.
        object.__setattr__(
            self, "heights", MappingProxyType({k: h for k, h in self.heights.items() if h})
        )
        # Defensively copy+freeze `cells` so a caller's mutable dict (or a
        # MappingProxyType wrapping one) cannot be mutated out from under an
        # already-constructed grid, which would otherwise let terrain
        # queries silently change over time and break the "loading the same
        # map produces canonical-equivalent world state" guarantee.
        object.__setattr__(self, "cells", MappingProxyType(dict(self.cells)))

    def terrain_at(self, x: int, y: int) -> TerrainType:
        """Return the terrain type at ``(x, y)``.

        Raises ``ValueError`` for coordinates outside ``[0, width)`` x
        ``[0, height)`` so callers cannot silently read terrain for a cell
        that is not part of the battlefield.
        """
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"({x}, {y}) is outside the {self.width}x{self.height} grid")
        return self.cells.get((x, y), self.default)

    def height_at(self, x: int, y: int) -> int:
        """Return the terrain piece height at ``(x, y)``; 0 off the map or where none is set."""
        return self.heights.get((x, y), 0)


def _terrain_type_from_raw(value: Any, *, context: str) -> TerrainType:
    if not isinstance(value, str):
        raise TerrainValidationError(f"{context}: terrain type must be a string")
    try:
        return TerrainType(value)
    except ValueError as exc:
        valid = ", ".join(member.value for member in TerrainType)
        raise TerrainValidationError(
            f"{context}: unknown terrain type {value!r} (expected one of: {valid})"
        ) from exc


def _height_from_raw(value: Any, *, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TerrainValidationError(f"{context}: must be a non-negative integer")
    return value


def parse_terrain_grid(raw: "dict[str, Any] | None", width: int, height: int) -> TerrainGrid:
    """Parse a YAML ``terrain`` section into a :class:`TerrainGrid`.

    Expected shape (all keys optional; ``raw`` itself may be ``None``/absent,
    yielding an all-``NORMAL`` grid)::

        terrain:
          default: normal
          debris_height: 3          # optional, default 0
          cells:
            - {x: 1, y: 2, type: rough, height: 2}   # height optional, default 0
            - {x: 3, y: 0, type: ditch}

    Validates: unknown ``default``/cell terrain type strings, negative or
    non-integer heights, cells outside
    ``[0, width)`` x ``[0, height)``, and duplicate ``(x, y)`` cell entries.
    Validation order is deterministic (input list order) so the same invalid
    document always raises the same error.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TerrainValidationError("terrain section must be a mapping")

    default_raw = raw.get("default", TerrainType.NORMAL.value)
    default = _terrain_type_from_raw(default_raw, context="terrain.default")

    debris_height = _height_from_raw(raw.get("debris_height", 0), context="terrain.debris_height")

    cells_raw = raw.get("cells", [])
    if not isinstance(cells_raw, list):
        raise TerrainValidationError("terrain.cells must be a list")

    cells: dict[tuple[int, int], TerrainType] = {}
    heights: dict[tuple[int, int], int] = {}
    for index, entry in enumerate(cells_raw):
        context = f"terrain.cells[{index}]"
        if not isinstance(entry, dict):
            raise TerrainValidationError(f"{context}: must be a mapping")

        x = entry.get("x")
        y = entry.get("y")
        if not isinstance(x, int) or isinstance(x, bool):
            raise TerrainValidationError(f"{context}: x must be an integer")
        if not isinstance(y, int) or isinstance(y, bool):
            raise TerrainValidationError(f"{context}: y must be an integer")
        if not (0 <= x < width and 0 <= y < height):
            raise TerrainValidationError(
                f"{context}: cell ({x}, {y}) is outside the {width}x{height} grid"
            )

        key = (x, y)
        if key in cells:
            raise TerrainValidationError(f"{context}: duplicate terrain cell {key}")

        cells[key] = _terrain_type_from_raw(entry.get("type"), context=context)
        heights[key] = _height_from_raw(entry.get("height", 0), context=f"{context}.height")

    return TerrainGrid(
        width=width,
        height=height,
        cells=cells,
        default=default,
        heights=heights,
        debris_height=debris_height,
    )
