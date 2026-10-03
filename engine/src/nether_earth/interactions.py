"""Canonical structure interaction metadata: heli-pads, exits, capture points.

Per `_specs/milestones/02-map-world-model.md`, the map/world contract must
expose canonical structure interaction metadata — war-base heli-pad, war-base
exit, factory capture location, and (if the resolved game rules require one)
war-base capture location — so other systems (commander docking,
construction, capture, frontend) consume one shared representation instead of
inventing layer-specific coordinates.

`_specs/open-questions.md` §6 ("war-base capture mechanics") is open: it is
unresolved whether war bases can be captured at all, and if so how. This
module therefore treats ``WARBASE_CAPTURE`` interaction points as optional —
a map may declare zero, one, or more of them per war base — rather than
requiring one, so it does not silently answer that question.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from nether_earth.ids import EntityId
from nether_earth.structures import Footprint, parse_footprint


class InteractionKind(Enum):
    """Canonical kinds of structure interaction locations."""

    HELI_PAD = "heli_pad"
    EXIT = "exit"
    FACTORY_CAPTURE = "factory_capture"
    WARBASE_CAPTURE = "warbase_capture"


class InteractionValidationError(ValueError):
    """Raised when a YAML ``interaction_points`` entry is invalid."""


@dataclass(frozen=True, slots=True)
class InteractionPoint:
    """A single named interaction location/footprint tied to one structure."""

    id: str
    kind: InteractionKind
    structure_id: EntityId
    footprint: Footprint


def _interaction_kind_from_raw(value: Any, *, context: str) -> InteractionKind:
    if not isinstance(value, str):
        raise InteractionValidationError(f"{context}: kind must be a string")
    try:
        return InteractionKind(value)
    except ValueError as exc:
        valid = ", ".join(member.value for member in InteractionKind)
        raise InteractionValidationError(
            f"{context}: unknown interaction kind {value!r} (expected one of: {valid})"
        ) from exc


# Which structure kind each interaction kind is semantically allowed to
# attach to, per `_specs/functional-spec.md` §8.6/§9.3/§9.4: heli-pads and
# exits are war-base concepts, factory capture applies to factories, and
# war-base capture (optional, `_specs/open-questions.md` §6) applies only to
# war bases.
_WAR_BASE_ONLY_KINDS = frozenset(
    {InteractionKind.HELI_PAD, InteractionKind.EXIT, InteractionKind.WARBASE_CAPTURE}
)
_FACTORY_ONLY_KINDS = frozenset({InteractionKind.FACTORY_CAPTURE})


def parse_interaction_points(
    raw: "list[Any] | None",
    war_base_ids: "set[str]",
    factory_ids: "set[str]",
    *,
    width: int,
    height: int,
) -> tuple[InteractionPoint, ...]:
    """Parse a YAML ``interaction_points`` list into a tuple of :class:`InteractionPoint`.

    Each entry has ``id``, ``kind``, ``structure_id``, and ``footprint``
    (single-cell shorthand or explicit multi-cell list, see
    ``structures.parse_footprint``). ``structure_id`` must reference a known
    war base or factory (blockers cannot host an interaction point) — passed
    as two separate id sets, ``war_base_ids`` and ``factory_ids``, rather
    than one combined set, so this function can also enforce that each
    ``kind`` attaches only to the structure kind it is semantically valid
    for: ``HELI_PAD``/``EXIT``/``WARBASE_CAPTURE`` require a war base id,
    ``FACTORY_CAPTURE`` requires a factory id (`_specs/functional-spec.md`
    §8.6/§9.3/§9.4). ``width``/``height`` bound-check every footprint cell
    against the map dimensions, mirroring ``terrain.parse_terrain_grid``.

    Validates duplicate ids, unknown kind strings, dangling ``structure_id``
    references, kind-vs-structure-type mismatches, and out-of-bounds
    footprint cells. ``WARBASE_CAPTURE`` entries are optional and may be
    absent entirely — see the module docstring.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise InteractionValidationError("interaction_points section must be a list")

    known_structure_ids = war_base_ids | factory_ids

    seen_ids: set[str] = set()
    points: list[InteractionPoint] = []
    for index, entry in enumerate(raw):
        context = f"interaction_points[{index}]"
        if not isinstance(entry, dict):
            raise InteractionValidationError(f"{context}: must be a mapping")

        point_id = entry.get("id")
        if not isinstance(point_id, str) or not point_id.strip():
            raise InteractionValidationError(f"{context}: id must be a non-empty string")
        if point_id in seen_ids:
            raise InteractionValidationError(f"{context}: duplicate interaction point id {point_id!r}")
        seen_ids.add(point_id)

        kind = _interaction_kind_from_raw(entry.get("kind"), context=context)

        structure_id_raw = entry.get("structure_id")
        if not isinstance(structure_id_raw, str) or not structure_id_raw.strip():
            raise InteractionValidationError(f"{context}: structure_id must be a non-empty string")
        if structure_id_raw not in known_structure_ids:
            raise InteractionValidationError(
                f"{context}: structure_id {structure_id_raw!r} does not reference a known structure"
            )

        if kind in _WAR_BASE_ONLY_KINDS and structure_id_raw not in war_base_ids:
            raise InteractionValidationError(
                f"{context}: kind {kind.value!r} requires structure_id {structure_id_raw!r} "
                "to be a war base"
            )
        if kind in _FACTORY_ONLY_KINDS and structure_id_raw not in factory_ids:
            raise InteractionValidationError(
                f"{context}: kind {kind.value!r} requires structure_id {structure_id_raw!r} "
                "to be a factory"
            )

        footprint = parse_footprint(entry.get("footprint"), context=context)
        for x, y in footprint.cells:
            if not (0 <= x < width and 0 <= y < height):
                raise InteractionValidationError(
                    f"{context}: footprint cell ({x}, {y}) is outside the {width}x{height} grid"
                )

        points.append(
            InteractionPoint(
                id=point_id,
                kind=kind,
                structure_id=EntityId(structure_id_raw),
                footprint=footprint,
            )
        )

    return tuple(points)
