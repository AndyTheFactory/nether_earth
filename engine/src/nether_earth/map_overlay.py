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
    from nether_earth.structures import Factory, WarBase


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
    does not reference a real war-base or factory id on ``world_map``.
    Blockers have no ``owner`` field, so an overlay referencing a blocker id
    is an unknown-reference error like any other unknown id. Also raises
    :class:`OverlayValidationError` if any ``overlay.spawn_positions`` cell
    falls outside ``[0, world_map.width)`` x ``[0, world_map.height)``, using
    the same in-bounds check ``terrain.py`` applies to its own cells.
    """
    ownable: tuple[WarBase | Factory, ...] = (*world_map.war_bases, *world_map.factories)
    known_structure_ids = {structure.id for structure in ownable}
    unknown = [
        entity_id for entity_id in overlay.ownership if entity_id not in known_structure_ids
    ]
    if unknown:
        unknown_values = ", ".join(repr(entity_id.value) for entity_id in unknown)
        raise OverlayValidationError(
            f"overlay {overlay.id!r} references unknown structure id(s): {unknown_values}"
        )

    out_of_bounds = [
        (name, cell)
        for name, cell in overlay.spawn_positions.items()
        if not (0 <= cell[0] < world_map.width and 0 <= cell[1] < world_map.height)
    ]
    if out_of_bounds:
        described = ", ".join(f"{name!r}={cell!r}" for name, cell in out_of_bounds)
        raise OverlayValidationError(
            f"overlay {overlay.id!r} spawn_positions outside the "
            f"{world_map.width}x{world_map.height} grid: {described}"
        )

    new_war_bases = tuple(
        replace(war_base, owner=overlay.ownership[war_base.id])
        if war_base.id in overlay.ownership
        else war_base
        for war_base in world_map.war_bases
    )
    new_factories = tuple(
        replace(factory, owner=overlay.ownership[factory.id])
        if factory.id in overlay.ownership
        else factory
        for factory in world_map.factories
    )

    merged_spawn_positions = {**world_map.spawn_positions, **overlay.spawn_positions}

    return replace(
        world_map,
        war_bases=new_war_bases,
        factories=new_factories,
        spawn_positions=merged_spawn_positions,
    )
