"""Compositional static structure data model and YAML parsing.

Per `_specs/open-questions.md` §15 (RESOLVED) and
`_specs/milestones/02-map-world-model.md` ("Locked structure-composition
model"), static geometry is not modeled as one generic rectangular
"building footprint". War bases and factories are distinct semantic world
entities, each composed from an explicit list of physical
:class:`Component` cells. Height is per-component, not one scalar for the
whole structure. Generic scenery/blockers (:class:`Blocker`) use the same
per-component shape for consistency, even though they carry no ownership or
production semantics.

This supersedes the earlier generic ``Structure``/``StructureKind`` model
(issue #19): that model used one uniform ``footprint``/``height`` pair per
structure, which the resolved open question explicitly rules out.

Interaction zones (heli-pad, exit, capture) remain independent semantic
metadata — see `interactions.py` — and are *not* derived from a structure's
physical composition. :class:`Footprint` and :func:`parse_footprint` are
kept unchanged here purely because `interactions.py` still uses them for
that independent interaction-zone geometry.

This module validates only that a single structure's own data is
well-formed (ids within its own kind, component shape, per-component
height, ownership, factory type). Cross-kind id-uniqueness and footprint
overlap *between* structures are left to `map.py` and `occupancy.py`
respectively.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from nether_earth.ids import EntityId, PlayerId


class FactoryType(Enum):
    """The six factory production categories (`_specs/functional-spec.md` §9.1)."""

    CHASSIS = "chassis"
    ELECTRONICS = "electronics"
    NUCLEAR = "nuclear"
    MISSILE = "missile"
    PHASER = "phaser"
    CANNON = "cannon"


class StructureValidationError(ValueError):
    """Raised when a YAML structure entry is structurally or semantically invalid."""


@dataclass(frozen=True, slots=True)
class Footprint:
    """The set of absolute grid cells an interaction point occupies.

    Cells are stored as absolute ``(x, y)`` grid coordinates, not offsets
    from an origin. This remains the shape `interactions.py` uses for
    interaction-zone geometry, which is intentionally independent of a
    structure's physical composition (see the module docstring).
    """

    cells: frozenset[tuple[int, int]]

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError("Footprint must contain at least one cell")


def parse_footprint(raw: Any, *, context: str) -> Footprint:
    """Parse a footprint from either single-cell shorthand or an explicit cell list.

    Accepts ``{x: 1, y: 2}`` (single-cell shorthand) or
    ``[{x: 1, y: 2}, {x: 2, y: 2}]`` (explicit multi-cell list) so callers
    never need two code paths depending on whether an interaction zone is
    one cell or many.
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
class Component:
    """A single physical map cell belonging to a structure's composition.

    ``height`` is per-component, not a uniform value for the whole
    structure — this is the crux of the resolved open-questions.md §15
    model. It must be a positive integer.
    """

    x: int
    y: int
    height: int

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("Component height must be a positive integer")


def _validate_components(components: tuple[Component, ...], *, context: str) -> None:
    if not components:
        raise ValueError(f"{context}: components must not be empty")
    seen: set[tuple[int, int]] = set()
    for component in components:
        key = (component.x, component.y)
        if key in seen:
            raise ValueError(f"{context}: duplicate component cell {key}")
        seen.add(key)


@dataclass(frozen=True, slots=True)
class WarBase:
    """A war base: composed from explicit physical components, owned by a player."""

    id: EntityId
    components: tuple[Component, ...]
    owner: PlayerId | None = None

    def __post_init__(self) -> None:
        _validate_components(self.components, context="WarBase")


@dataclass(frozen=True, slots=True)
class Factory:
    """A factory: composed from explicit physical components, with a production type."""

    id: EntityId
    components: tuple[Component, ...]
    factory_type: FactoryType
    owner: PlayerId | None = None

    def __post_init__(self) -> None:
        _validate_components(self.components, context="Factory")


@dataclass(frozen=True, slots=True)
class Blocker:
    """Generic unowned static scenery (cubes/boxes/other blockers).

    ``kind`` is an opaque data label (e.g. ``"box_low"``, ``"fence"`` on the
    original map, CR002.1 #168) that presentation layers map to an asset.
    The engine never branches on it: a blocker's gameplay effect comes only
    from its components' cells and heights, and ``destructible``.

    ``destructible`` (CR002.18, #196) marks scenery a nuclear blast turns
    into rough debris (`Lba44_robots_handled`: element types 17-20; the
    type-21 fence is not destructible). Map data, default ``False``.
    """

    id: EntityId
    components: tuple[Component, ...]
    kind: str | None = None
    destructible: bool = False

    def __post_init__(self) -> None:
        _validate_components(self.components, context="Blocker")
        if self.kind is not None and not self.kind.strip():
            raise ValueError("Blocker kind must be a non-empty string when given")


def occupied_cells(structure: "WarBase | Factory | Blocker") -> frozenset[tuple[int, int]]:
    """Return the ``(x, y)`` cells occupied by ``structure``'s components.

    For occupancy/validation code that only cares about footprint, not
    per-component height.
    """
    return frozenset((component.x, component.y) for component in structure.components)


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


def _parse_components(raw: Any, *, context: str) -> tuple[Component, ...]:
    if not isinstance(raw, list):
        raise StructureValidationError(f"{context}: components must be a list")
    if not raw:
        raise StructureValidationError(f"{context}: components must not be empty")

    seen: set[tuple[int, int]] = set()
    components: list[Component] = []
    for index, entry in enumerate(raw):
        component_context = f"{context}.components[{index}]"
        if not isinstance(entry, dict):
            raise StructureValidationError(f"{component_context}: must be a mapping")

        x = entry.get("x")
        y = entry.get("y")
        height = entry.get("height")
        if not isinstance(x, int) or isinstance(x, bool):
            raise StructureValidationError(f"{component_context}: x must be an integer")
        if not isinstance(y, int) or isinstance(y, bool):
            raise StructureValidationError(f"{component_context}: y must be an integer")
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            raise StructureValidationError(
                f"{component_context}: height must be a positive integer"
            )

        key = (x, y)
        if key in seen:
            raise StructureValidationError(f"{component_context}: duplicate component cell {key}")
        seen.add(key)

        components.append(Component(x=x, y=y, height=height))

    return tuple(components)


def _parse_owner(raw: Any, *, context: str) -> PlayerId | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise StructureValidationError(f"{context}: owner must be a non-empty string")
    return PlayerId(raw)


def _parse_id(raw: Any, *, context: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise StructureValidationError(f"{context}: id must be a non-empty string")
    return raw


def parse_war_bases(raw: "list[Any] | None") -> tuple[WarBase, ...]:
    """Parse a YAML ``war_bases`` list into a tuple of :class:`WarBase`.

    Each entry has ``id``, ``components`` (explicit list of
    ``{x, y, height}``), and optional ``owner``. Validates duplicate ids
    (within war bases), malformed/empty ``components``, non-integer
    ``x``/``y``/``height``, and duplicate ``(x, y)`` within one structure's
    components.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise StructureValidationError("war_bases section must be a list")

    seen_ids: set[str] = set()
    war_bases: list[WarBase] = []
    for index, entry in enumerate(raw):
        context = f"war_bases[{index}]"
        if not isinstance(entry, dict):
            raise StructureValidationError(f"{context}: must be a mapping")

        raw_id = _parse_id(entry.get("id"), context=context)
        if raw_id in seen_ids:
            raise StructureValidationError(f"{context}: duplicate war base id {raw_id!r}")
        seen_ids.add(raw_id)

        components = _parse_components(entry.get("components"), context=context)
        owner = _parse_owner(entry.get("owner"), context=context)

        war_bases.append(WarBase(id=EntityId(raw_id), components=components, owner=owner))

    return tuple(war_bases)


def parse_factories(raw: "list[Any] | None") -> tuple[Factory, ...]:
    """Parse a YAML ``factories`` list into a tuple of :class:`Factory`.

    Each entry has ``id``, ``components``, required ``factory_type``, and
    optional ``owner``. Validates the same component shape as
    :func:`parse_war_bases`, plus that ``factory_type`` is present and a
    known value.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise StructureValidationError("factories section must be a list")

    seen_ids: set[str] = set()
    factories: list[Factory] = []
    for index, entry in enumerate(raw):
        context = f"factories[{index}]"
        if not isinstance(entry, dict):
            raise StructureValidationError(f"{context}: must be a mapping")

        raw_id = _parse_id(entry.get("id"), context=context)
        if raw_id in seen_ids:
            raise StructureValidationError(f"{context}: duplicate factory id {raw_id!r}")
        seen_ids.add(raw_id)

        components = _parse_components(entry.get("components"), context=context)

        factory_type_raw = entry.get("factory_type")
        if factory_type_raw is None:
            raise StructureValidationError(f"{context}: factories require factory_type")
        factory_type = _factory_type_from_raw(factory_type_raw, context=context)

        owner = _parse_owner(entry.get("owner"), context=context)

        factories.append(
            Factory(
                id=EntityId(raw_id),
                components=components,
                factory_type=factory_type,
                owner=owner,
            )
        )

    return tuple(factories)


def parse_blockers(raw: "list[Any] | None") -> tuple[Blocker, ...]:
    """Parse a YAML ``blockers`` list into a tuple of :class:`Blocker`.

    Each entry has ``id``, ``components``, an optional ``kind`` (a
    non-empty string label) and an optional boolean ``destructible`` (see
    :class:`Blocker`). Blockers are unowned
    generic scenery and never carry ``owner``/``factory_type``. Validates the
    same component shape as :func:`parse_war_bases`.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise StructureValidationError("blockers section must be a list")

    seen_ids: set[str] = set()
    blockers: list[Blocker] = []
    for index, entry in enumerate(raw):
        context = f"blockers[{index}]"
        if not isinstance(entry, dict):
            raise StructureValidationError(f"{context}: must be a mapping")

        raw_id = _parse_id(entry.get("id"), context=context)
        if raw_id in seen_ids:
            raise StructureValidationError(f"{context}: duplicate blocker id {raw_id!r}")
        seen_ids.add(raw_id)

        components = _parse_components(entry.get("components"), context=context)

        kind = entry.get("kind")
        if kind is not None and (not isinstance(kind, str) or not kind.strip()):
            raise StructureValidationError(f"{context}: kind must be a non-empty string")

        destructible = entry.get("destructible", False)
        if not isinstance(destructible, bool):
            raise StructureValidationError(f"{context}: destructible must be a boolean")

        blockers.append(
            Blocker(
                id=EntityId(raw_id), components=components, kind=kind, destructible=destructible
            )
        )

    return tuple(blockers)
