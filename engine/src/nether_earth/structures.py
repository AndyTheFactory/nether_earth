"""Static structure data model and YAML parsing.

Per `_specs/technical-spec.md` §10 and `_specs/functional-spec.md` §9,
factories and war bases are physical, owned, height-bearing structures.
`_specs/open-questions.md` §15 ("static-object footprints") is explicitly
open: it is unresolved whether every structure occupies exactly one cell or
can span multiple cells. :class:`Footprint` is the one canonical multi-cell
representation shared by this module and the later interaction-metadata
(`interactions.py`) and occupancy (`occupancy.py`) modules specifically so
that question can be answered later (per-map, even) without every consumer
inventing its own "what cells does this occupy" shape.

This module validates only that a single structure's own data is
well-formed (ids, kind, footprint shape, height, ownership, factory type
consistency). Footprint overlap *between* structures is deliberately left to
`occupancy.py` (issue #23's job) — see that module's docstring.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from nether_earth.ids import EntityId, PlayerId


class StructureKind(Enum):
    """Kinds of static, ground-solid structures the map can represent."""

    FACTORY = "factory"
    WARBASE = "warbase"
    BLOCKER = "blocker"


class FactoryType(Enum):
    """The six factory production categories (`_specs/functional-spec.md` §9.1)."""

    CHASSIS = "chassis"
    ELECTRONICS = "electronics"
    NUCLEAR = "nuclear"
    MISSILE = "missile"
    PHASER = "phaser"
    CANNON = "cannon"


class StructureValidationError(ValueError):
    """Raised when a YAML ``structures`` entry is structurally or semantically invalid."""


@dataclass(frozen=True, slots=True)
class Footprint:
    """The set of absolute grid cells a structure or interaction point occupies.

    Cells are stored as absolute ``(x, y)`` grid coordinates, not offsets
    from an origin — this is the single representation later milestones and
    modules (`interactions.py`, `occupancy.py`) build on, so there is only
    ever one "what cells does this occupy" shape in the codebase.
    """

    cells: frozenset[tuple[int, int]]

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError("Footprint must contain at least one cell")


def parse_footprint(raw: Any, *, context: str) -> Footprint:
    """Parse a footprint from either single-cell shorthand or an explicit cell list.

    Accepts ``{x: 1, y: 2}`` (single-cell shorthand) or
    ``[{x: 1, y: 2}, {x: 2, y: 2}]`` (explicit multi-cell list) so callers
    never need two code paths depending on whether a structure is one cell
    or many.
    """
    entries: list[Any]
    if isinstance(raw, dict):
        entries = [raw]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise StructureValidationError(
            f"{context}: footprint must be a single {{x, y}} mapping or a list of them"
        )

    if not entries:
        raise StructureValidationError(f"{context}: footprint must not be empty")

    cells: set[tuple[int, int]] = set()
    for cell_index, cell_entry in enumerate(entries):
        cell_context = f"{context}.footprint[{cell_index}]"
        if not isinstance(cell_entry, dict):
            raise StructureValidationError(f"{cell_context}: must be a mapping")
        x = cell_entry.get("x")
        y = cell_entry.get("y")
        if not isinstance(x, int) or isinstance(x, bool):
            raise StructureValidationError(f"{cell_context}: x must be an integer")
        if not isinstance(y, int) or isinstance(y, bool):
            raise StructureValidationError(f"{cell_context}: y must be an integer")
        cells.add((x, y))

    return Footprint(cells=frozenset(cells))


@dataclass(frozen=True, slots=True)
class Structure:
    """A single static, ground-solid, height-bearing structure on the map.

    ``factory_type`` must be set if and only if ``kind`` is
    ``StructureKind.FACTORY`` — a factory always has a production type
    (`_specs/functional-spec.md` §9.1) and non-factory structures never do.
    """

    id: EntityId
    kind: StructureKind
    footprint: Footprint
    height: int
    owner: PlayerId | None = None
    factory_type: FactoryType | None = None

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("Structure height must be a positive integer")
        if self.kind is StructureKind.FACTORY and self.factory_type is None:
            raise ValueError("factory structures must specify factory_type")
        if self.kind is not StructureKind.FACTORY and self.factory_type is not None:
            raise ValueError("only factory structures may specify factory_type")


def _structure_kind_from_raw(value: Any, *, context: str) -> StructureKind:
    if not isinstance(value, str):
        raise StructureValidationError(f"{context}: kind must be a string")
    try:
        return StructureKind(value)
    except ValueError as exc:
        valid = ", ".join(member.value for member in StructureKind)
        raise StructureValidationError(
            f"{context}: unknown structure kind {value!r} (expected one of: {valid})"
        ) from exc


def _factory_type_from_raw(value: Any, *, context: str) -> FactoryType:
    if not isinstance(value, str):
        raise StructureValidationError(f"{context}: factory_type must be a string")
    try:
        return FactoryType(value)
    except ValueError as exc:
        valid = ", ".join(member.value for member in FactoryType)
        raise StructureValidationError(
            f"{context}: unknown factory_type {value!r} (expected one of: {valid})"
        ) from exc


def parse_structures(raw: "list[Any] | None") -> tuple[Structure, ...]:
    """Parse a YAML ``structures`` list into a tuple of :class:`Structure`.

    Each entry has ``id``, ``kind``, ``footprint`` (single-cell shorthand or
    explicit multi-cell list, see :func:`parse_footprint`), ``height``,
    optional ``owner``, and optional ``factory_type`` (required exactly when
    ``kind`` is ``factory``). Validates duplicate structure ids, unknown
    kind/factory_type strings, and malformed footprints. Does not check
    footprint overlap between structures — see the module docstring.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise StructureValidationError("structures section must be a list")

    seen_ids: set[str] = set()
    structures: list[Structure] = []
    for index, entry in enumerate(raw):
        context = f"structures[{index}]"
        if not isinstance(entry, dict):
            raise StructureValidationError(f"{context}: must be a mapping")

        raw_id = entry.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise StructureValidationError(f"{context}: id must be a non-empty string")
        if raw_id in seen_ids:
            raise StructureValidationError(f"{context}: duplicate structure id {raw_id!r}")
        seen_ids.add(raw_id)

        kind = _structure_kind_from_raw(entry.get("kind"), context=context)
        footprint = parse_footprint(entry.get("footprint"), context=context)

        height = entry.get("height")
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            raise StructureValidationError(f"{context}: height must be a positive integer")

        owner_raw = entry.get("owner")
        owner: PlayerId | None = None
        if owner_raw is not None:
            if not isinstance(owner_raw, str) or not owner_raw.strip():
                raise StructureValidationError(f"{context}: owner must be a non-empty string")
            owner = PlayerId(owner_raw)

        factory_type_raw = entry.get("factory_type")
        factory_type: FactoryType | None = None
        if kind is StructureKind.FACTORY:
            if factory_type_raw is None:
                raise StructureValidationError(f"{context}: factory structures require factory_type")
            factory_type = _factory_type_from_raw(factory_type_raw, context=context)
        elif factory_type_raw is not None:
            raise StructureValidationError(
                f"{context}: factory_type is only valid for kind=factory"
            )

        structures.append(
            Structure(
                id=EntityId(raw_id),
                kind=kind,
                footprint=footprint,
                height=height,
                owner=owner,
                factory_type=factory_type,
            )
        )

    return tuple(structures)
