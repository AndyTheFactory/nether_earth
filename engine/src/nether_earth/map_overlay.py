"""Scenario overlay: applying scenario-specific ownership/spawns onto a map.

Per `_specs/technical-spec.md` §27 ("PvP-specific starting ownership should
preferably live in scenario data rather than mutating the canonical map
definition") and the milestone's "scenario overlay" workstream, this module
provides the mechanism for layering scenario-time ownership and named
spawn/reference positions over a base :class:`map.WorldMap` without mutating
the source map definition.

This module does not resolve `_specs/open-questions.md` §2 (what happens to
the war bases the PvP scenario does not assign to a player) — it only
provides the generic mechanism. No PvP-specific default assignment lives
here.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from nether_earth.ids import EntityId, PlayerId

if TYPE_CHECKING:
    from nether_earth.map import WorldMap


class OverlayValidationError(ValueError):
    """Raised when a :class:`ScenarioOverlay` references map data that does not exist."""


@dataclass(frozen=True, slots=True)
class ScenarioOverlay:
    """Scenario-time overrides layered over a base map without mutating it.

    ``ownership`` maps a structure id to the player that owns it under this
    scenario. ``spawn_positions`` maps a named spawn/reference position (e.g.
    ``"p1_commander"``) to an override grid cell; entries here take
    precedence over the base map's own ``spawn_positions`` when the overlay
    is applied (see :func:`apply_overlay`).
    """

    id: str
    ownership: Mapping[EntityId, PlayerId]
    spawn_positions: Mapping[str, tuple[int, int]]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("ScenarioOverlay id must be a non-empty string")


def apply_overlay(world_map: "WorldMap", overlay: ScenarioOverlay) -> "WorldMap":
    """Return a new :class:`map.WorldMap` with ``overlay`` applied.

    Structures named in ``overlay.ownership`` get their ``owner`` field
    replaced; ``overlay.spawn_positions`` entries are merged into the map's
    ``spawn_positions`` with the overlay winning on key collisions. Neither
    ``world_map`` nor its ``structures`` tuple is mutated — this always
    returns a new value.

    Raises :class:`OverlayValidationError` if any ``overlay.ownership`` key
    does not reference a real structure id on ``world_map``.
    """
    known_structure_ids = {structure.id for structure in world_map.structures}
    unknown = [
        entity_id for entity_id in overlay.ownership if entity_id not in known_structure_ids
    ]
    if unknown:
        unknown_values = ", ".join(repr(entity_id.value) for entity_id in unknown)
        raise OverlayValidationError(
            f"overlay {overlay.id!r} references unknown structure id(s): {unknown_values}"
        )

    new_structures = tuple(
        replace(structure, owner=overlay.ownership[structure.id])
        if structure.id in overlay.ownership
        else structure
        for structure in world_map.structures
    )

    merged_spawn_positions = {**world_map.spawn_positions, **overlay.spawn_positions}

    return replace(
        world_map,
        structures=new_structures,
        spawn_positions=merged_spawn_positions,
    )
