"""Canonical structure interaction metadata: heli-pads, exits, capture points.

Per `_specs/milestones/02-map-world-model.md`, the map/world contract must
expose canonical structure interaction metadata — war-base heli-pad, war-base
exit, factory capture location, and (if the resolved game rules require one)
war-base capture location — so later milestones (commander docking,
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


def parse_interaction_points(
    raw: "list[Any] | None", known_structure_ids: "set[str]"
) -> tuple[InteractionPoint, ...]:
    """Parse a YAML ``interaction_points`` list into a tuple of :class:`InteractionPoint`.

    Each entry has ``id``, ``kind``, ``structure_id`` (must be a member of
    ``known_structure_ids`` — the ids of structures that can host an
    interaction point, i.e. war bases and factories; blockers cannot), and
    ``footprint`` (single-cell shorthand or explicit multi-cell list, see
    ``structures.parse_footprint``). Validates duplicate ids, unknown kind
    strings, and dangling ``structure_id`` references. ``WARBASE_CAPTURE``
    entries are optional and may be absent entirely — see the module
    docstring.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise InteractionValidationError("interaction_points section must be a list")

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

        footprint = parse_footprint(entry.get("footprint"), context=context)

        points.append(
            InteractionPoint(
                id=point_id,
                kind=kind,
                structure_id=EntityId(structure_id_raw),
                footprint=footprint,
            )
        )

    return tuple(points)
