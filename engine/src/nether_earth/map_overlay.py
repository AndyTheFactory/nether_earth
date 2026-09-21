"""Scenario overlay: applying scenario-specific ownership/spawns onto a map.

Per `_specs/technical-spec.md` §27 ("PvP-specific starting ownership should
preferably live in scenario data rather than mutating the canonical map
definition") and the milestone's "scenario overlay" workstream, this module
provides the mechanism for layering scenario-time ownership and named
spawn/reference positions over a base :class:`map.WorldMap` without mutating
the source map definition.

`_specs/open-questions.md` §2 (PvP treatment of the remaining war bases) is
now RESOLVED: Player 1 starts owning the extreme-left war base, Player 2
starts owning the extreme-right war base, and the two war bases between
them start neutral. :func:`default_pvp_overlay` encodes exactly that locked
decision as overlay data — it does not touch base-map geometry, and a
"neutral" war base is simply one this overlay's ``ownership`` mapping does
not mention (see :class:`WarBase`'s ``owner: PlayerId | None`` default).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind

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


#: Stable id for the overlay :func:`default_pvp_overlay` returns.
STANDARD_PVP_OVERLAY_ID = "standard-pvp"


def default_pvp_overlay(world_map: "WorldMap") -> ScenarioOverlay:
    """Return the locked v1 standard-PvP :class:`ScenarioOverlay` for ``world_map``.

    Encodes `_specs/open-questions.md` §2 (RESOLVED) /
    `_specs/milestones/02-map-world-model.md`'s "Locked v1 PvP scenario
    overlay": Player 1 (``ids.PLAYER_ONE``) owns the extreme-left war base,
    Player 2 (``ids.PLAYER_TWO``) owns the extreme-right war base, and every
    other war base is left out of ``ownership`` entirely — since a war
    base's ``owner`` already defaults to ``None`` (neutral), simply not
    mentioning a war base here *is* "starts neutral". This never mutates
    ``world_map``; apply the returned overlay with :func:`apply_overlay` as
    usual.

    Defining "extreme-left"/"extreme-right":

    - Each war base's horizontal extent is the min/max ``x`` across its own
      ``components`` (a war base can span multiple cells).
      "Extreme-left" is the war base with the smallest min-``x``;
      "extreme-right" is the war base with the largest max-``x``.
    - Ties (two war bases sharing the same extreme min-``x`` or max-``x``)
      are broken by war-base id, ascending for the left pick and descending
      for the right pick, so the result is deterministic for a given map
      even though it is an arbitrary tie-break rather than a gameplay rule.
      The real fixture/original map is expected to have exactly four
      war bases at unambiguous distinct x-positions, so this tie-break
      should not matter in practice.
    - This function does not require exactly four war bases: with more than
      four, every war base that is not the extreme-left/-right pick starts
      neutral (same "absent from ``ownership``" rule); with fewer than two,
      or if the extreme-left and extreme-right pick resolve to the same war
      base (e.g. only one war base on the map), it raises
      :class:`OverlayValidationError` since a standard PvP assignment needs
      two distinct starting war bases.
    """
    if len(world_map.war_bases) < 2:
        raise OverlayValidationError(
            "default_pvp_overlay requires at least two war bases on the map, "
            f"found {len(world_map.war_bases)}"
        )

    leftmost = min(
        world_map.war_bases,
        key=lambda war_base: (
            min(component.x for component in war_base.components),
            war_base.id.value,
        ),
    )
    rightmost = max(
        world_map.war_bases,
        key=lambda war_base: (
            max(component.x for component in war_base.components),
            war_base.id.value,
        ),
    )

    if leftmost.id == rightmost.id:
        raise OverlayValidationError(
            "default_pvp_overlay could not find two distinct extreme-left/"
            f"extreme-right war bases on the map (both resolved to {leftmost.id.value!r})"
        )

    return ScenarioOverlay(
        id=STANDARD_PVP_OVERLAY_ID,
        ownership={
            leftmost.id: PLAYER_ONE,
            rightmost.id: PLAYER_TWO,
        },
        spawn_positions={
            f"{PLAYER_ONE.value}_commander": _commander_spawn_cell(world_map, leftmost, mirrored=False),
            f"{PLAYER_TWO.value}_commander": _commander_spawn_cell(world_map, rightmost, mirrored=True),
        },
    )


#: Player 1's commander start relative to its war base's capture anchor cell.
#: Evidence (`_specs/open-questions.md` §17): the ZX Spectrum start routine
#: (`La600_start`) sets the ship to x=17, y=10, altitude 0 while war base 0's
#: anchor is (22, 9) -- an offset of (-5, +1), i.e. just outside the base on
#: the side facing away from the map interior.
_SPAWN_OFFSET_FROM_ANCHOR: tuple[int, int] = (-5, 1)


def _commander_spawn_cell(
    world_map: "WorldMap", war_base: "WarBase", *, mirrored: bool
) -> tuple[int, int]:
    """Return the commander start cell for ``war_base``'s owner.

    The cell is the war base's ``warbase_capture`` anchor plus
    :data:`_SPAWN_OFFSET_FROM_ANCHOR`; ``mirrored`` flips the x offset so
    Player 2 starts outside its extreme-right base just as Player 1 starts
    outside its extreme-left one. The original game is single-player, so the
    mirrored convention is provisional owner-review data
    (`_specs/open-questions.md` §17), kept in one place here so changing it
    is a data edit. The result is clamped into the map so an odd map cannot
    yield an out-of-bounds spawn (``apply_overlay`` would reject it).
    """
    capture_points = world_map.interaction_points_for(war_base.id, kind=InteractionKind.WARBASE_CAPTURE)
    if capture_points:
        anchor_x, anchor_y = min(capture_points[0].footprint.cells)
    else:
        anchor_x = min(component.x for component in war_base.components)
        anchor_y = max(component.y for component in war_base.components)
    dx, dy = _SPAWN_OFFSET_FROM_ANCHOR
    x = anchor_x - dx if mirrored else anchor_x + dx
    y = anchor_y + dy
    x = min(max(x, 0), world_map.width - 1)
    y = min(max(y, 0), world_map.height - 1)
    return (x, y)
