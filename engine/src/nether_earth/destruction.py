"""Robot destruction service, shared by projectile damage and nuclear effects.

Why a dedicated module
-----------------------
A robot killed in combat first blinks for
``rules.robot_destroyed_blink_cycles`` game cycles (:func:`destroy_robot`,
:func:`advance_destroyed_robots`; `docs/mechanics/combat.md` "Destroyed
robots"), then is removed. Removing a robot is not just "remove it from
``state.robots``" -- it also has to leave no stale reference anywhere else
in ``GameState``: an in-progress capture attempt naming the robot, and a
commander currently ``DOCKED`` to it, both need explicit, correct cleanup
in the same authoritative step (:func:`remove_robot`). The destruction service (`docs/mechanics/combat.md`) is meant to be the
*one* place this cleanup logic lives, because it must be reachable identically from more
than one caller: `combat.py`'s per-hit :func:`~nether_earth.combat.apply_damage`
and the nuclear-detonation area-destruction effect
(:func:`execute_nuclear_detonation`). Centralizing it here means those two callers can
never silently diverge on what "a robot is destroyed" actually cleans up --
the same architectural reasoning `capture.py`'s module docstring gives for
centralizing ownership-transfer logic in one place rather than letting
capture and (a hypothetical) other ownership-changing system each
reimplement it.

What this module deliberately does NOT do
--------------------------------------------
- It does not release world occupancy or destination reservations
  explicitly. Both are *derived* projections over ``state.robots``
  (`movement.py`'s :func:`~nether_earth.movement.folded_robot_occupancy`
  folds live robots into the static occupancy grid on every read;
  `reservations.py`'s :func:`~nether_earth.reservations.reservations_from_state`
  derives the reservation table the same way) rather than separately
  stored collections -- see `movement.py`'s own docstrings for why. Simply
  removing the robot from ``state.robots`` (:func:`remove_robot`) is therefore
  already sufficient cleanup for both; a second explicit
  occupancy/reservation-clearing call here would be redundant.
- It does not remove the destroyed robot's in-flight
  :class:`~nether_earth.combat.Projectile` (if it had one via
  ``active_projectile_id``) from ``state.projectiles``. A normal-weapon
  projectile is a physical object already travelling independently of its
  firer per the locked rules; `combat.py`'s own
  :func:`~nether_earth.combat.advance_projectiles` already
  handles a missing source robot safely when that projectile eventually
  terminates (its channel-release step becomes a no-op). Deleting the
  projectile here would silently despawn a still-in-flight shot the moment
  its firer dies, which the locked rules do not call for.

Structure destruction and nuclear detonation
--------------------------------------------
The structure-side half of destruction
(:func:`destroy_structure`) and the nuclear area-effect that is this
codebase's only caller of it (:func:`execute_nuclear_detonation`) mirror
``destroy_robot``'s own shape one level further: a war base/factory needs
the exact same "leave no stale reference anywhere else in ``GameState``"
discipline a destroyed robot does (a mid-capture attempt naming it, a
runtime ownership override naming it), so it gets its own dedicated
idempotent, event-emitting function in this same module rather than a
second ad hoc cleanup routine.

``GameState.structure_destruction`` -- a runtime marker, not a mutated ``WorldMap``
-------------------------------------------------------------------------------------
Exactly like `capture.py`'s :class:`~nether_earth.capture.StructureOwnership`
(see that module's own "Runtime ownership: ``GameState``, not a mutated
``WorldMap``" section), ``WorldMap.war_bases``/``WorldMap.factories`` are
plain, externally-held, never-mutated-in-place values -- so "this structure
has been destroyed" cannot live on ``WorldMap`` itself without changing that
established API shape. It is instead recorded as a
``GameState``-attached fact (``state.structure_destruction``, a canonical
sorted tuple of ids -- see `state.py`), and :func:`effective_world` below
layers it over ``capture.py``'s own ownership-layering
:func:`~nether_earth.capture.effective_world`, the same "layer an override
without mutating the source map" mechanism reused one level further.

Structure-only-via-nuclear is a structural, not a runtime, invariant
-------------------------------------------------------------------------
:func:`destroy_structure` is only ever reachable from
:func:`execute_nuclear_detonation` in this codebase: `combat.py`'s
:func:`~nether_earth.combat.apply_damage` (the only other destruction
caller) operates exclusively on ``Robot`` targets by construction (it reads
``state.robot_for(target_robot_id)`` and calls :func:`destroy_robot`, never
:func:`destroy_structure`), and `orders.py`'s
``_STRUCTURE_CAPABLE_WEAPONS = (ModuleIdentity.NUCLEAR,)`` already ensures no
engagement intent against a factory/war base is ever produced for a
cannon/missile/phaser-only robot. This function therefore does not add a
redundant runtime weapon-check of its own -- there is no reachable code path
that would need one, and a check that can never fire would be dead code.

Nuclear blast shapes: the Spectrum code
---------------------------------------
:func:`execute_nuclear_detonation` follows ``Lb99f_fire_nuclear_bomb``
(`_specs/resolved-questions.md` "Nuclear blast shape", `functional-spec.md` §17.3,
`technical-spec.md` §18): a trimmed 9x9 robot window around the carrier and
a per-kind building range test that destroys at most one building. All
shape parameters are :class:`~nether_earth.rules.EngineRules` fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TypeVar

from nether_earth.capture import CapturableStructureKind, capture_footprint
from nether_earth.capture import effective_world as _capture_effective_world
from nether_earth.collision import components_at, robot_top
from nether_earth.commander import CommanderMode
from nether_earth.docking import CommanderUndockedEvent
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.occupancy import unit_footprint_cells
from nether_earth.robot import Robot
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState
from nether_earth.structures import Blocker, Factory, WarBase
from nether_earth.terrain import TerrainType
from nether_earth.victory import VictoryEvent
from nether_earth.victory import evaluate_victory as _evaluate_victory

_S = TypeVar("_S", WarBase, Factory, Blocker)

__all__ = [
    "RobotDestroyedEvent",
    "StructureDestroyedEvent",
    "advance_destroyed_robots",
    "destroy_robot",
    "destroy_structure",
    "effective_world",
    "evaluate_victory_after_nuclear_detonation",
    "execute_nuclear_detonation",
    "is_blink_update_tick",
    "remove_robot",
    "scenery_world",
]


@dataclass(frozen=True, slots=True)
class RobotDestroyedEvent(Event):
    """A robot was destroyed.

    Emitted once, when the robot dies: at the killing hit (it then blinks
    and is removed later, :func:`destroy_robot`) or when a nuclear blast
    removes a live robot outright (:func:`remove_robot`). Mirrors
    :class:`~nether_earth.capture.StructureCapturedEvent`'s "this entity's
    fate changed" shape: the entity's identity, owner, and last
    authoritative position are carried directly on the event so consumers
    (replay, rendering) never need a separate state read to know where the
    destruction happened.
    """

    entity_id: EntityId
    owner: PlayerId
    x: int
    y: int
    tick: int


def destroy_robot(
    state: GameState,
    entity_id: EntityId,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Destroy ``entity_id`` in combat: it starts blinking, and is removed later.

    `Lb7d7_robot_destroyed` sets the robot's strength to -4 rather than
    removing it (owner decision 2026-10-03, `docs/mechanics/combat.md`
    "Destroyed robots"). Here its ``strength`` becomes 0 (what the HUD
    shows, `La81d`) and ``destroyed_cycles_remaining`` becomes
    ``rules.robot_destroyed_blink_cycles``; :func:`advance_destroyed_robots`
    counts it down and removes the robot. A move or turn already in progress
    runs on; :attr:`~nether_earth.robot.Robot.destroyed` keeps the robot
    from starting anything new. A docked commander stays docked until the
    removal. Emits one :class:`RobotDestroyedEvent`.

    Returns ``(state, ())`` -- the same object -- when ``entity_id`` names
    no robot or one already destroyed, so a second hit is a no-op.
    """
    robot = state.robot_for(entity_id)
    if robot is None or robot.destroyed:
        return state, ()
    dying = robot.with_strength(0).with_destroyed_cycles_remaining(
        rules.robot_destroyed_blink_cycles
    )
    new_state = state.with_robots(
        tuple(dying if other.entity_id == entity_id else other for other in state.robots)
    )
    event = RobotDestroyedEvent(
        sequence=sequencer.next_sequence() if sequencer is not None else 0,
        entity_id=entity_id,
        owner=robot.owner,
        x=robot.x,
        y=robot.y,
        tick=tick,
    )
    return new_state, (event,)


def remove_robot(
    state: GameState,
    entity_id: EntityId,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
    *,
    world: WorldMap | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Remove ``entity_id`` from play, with full associated-state cleanup.

    The end of a blink (:func:`advance_destroyed_robots`, `Lb116`) and a
    nuclear blast (`Lba44`, which clears the robot's slot directly) both
    end here.

    Returns ``(state, ())`` -- the caller's own ``state`` object, unchanged,
    with no events -- when ``entity_id`` no longer names a robot in
    ``state.robots``: already removed, or never existed. This is the
    idempotency guard: calling this function twice in a row on the same id
    returns the exact same ``state`` object (by identity, not merely by
    equality) the second time, mirroring
    :func:`~nether_earth.movement.cancel_robot_move`'s "cancelling twice is
    a safe no-op" precedent exactly.

    Otherwise, in this fixed order:

    1. **Capture progress cleanup**: any :class:`~nether_earth.capture.CaptureProgress`
       entry naming this robot (``progress.robot_id == entity_id``) is
       dropped.
    2. **Docked-commander safety**: if a commander is currently ``DOCKED``
       to this robot, it is forced back to ``FREE`` at the robot's own last
       known position, resting on its last top: :func:`~nether_earth.collision.robot_top`
       in ``world`` (terrain altitude plus stack height), or ``robot.height``
       when no ``world`` is given (the world-less unit-test path, where
       robots stand at altitude 0). Position and altitude are set here
       explicitly rather than relying on `docking.py`'s
       :func:`~nether_earth.docking.follow_docked_robot` having already run
       this tick. Emits `docking.py`'s
       :class:`~nether_earth.docking.CommanderUndockedEvent`. The Spectrum
       drops the player out of the robot's menu here, when the slot empties
       (`Lace2`, `La812_exit_robot`). The commander is never harmed.
    3. **Robot removal**: the robot is dropped from ``state.robots``.
       Occupancy and reservations are derived from ``state.robots``, so
       they vanish with it; the robot's in-flight projectile (if any) is
       deliberately left untouched (see the module docstring).
    4. **Event emission**: a :class:`RobotDestroyedEvent`, after the
       undock event, unless the robot was already destroyed -- a blinking
       robot reported its destruction when it was hit.

    ``rules`` is accepted (and currently unused) for signature symmetry
    with :func:`destroy_robot`.
    """
    robot = state.robot_for(entity_id)
    if robot is None:
        return state, ()

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()
    events: list[Event] = []

    remaining_capture_progress = tuple(
        progress for progress in state.capture_progress if progress.robot_id != entity_id
    )

    updated_commanders = list(state.commanders)
    for index, commander in enumerate(updated_commanders):
        if commander.mode is CommanderMode.DOCKED and commander.docked_robot_id == entity_id:
            from_altitude = commander.altitude
            freed = (
                commander.with_docking(CommanderMode.FREE, None)
                .with_position(robot.x, robot.y)
                .with_altitude(robot_top(world, robot) if world is not None else robot.height)
            )
            updated_commanders[index] = freed
            events.append(
                CommanderUndockedEvent(
                    sequence=resolved_sequencer.next_sequence(),
                    player_id=freed.player_id,
                    robot_id=entity_id,
                    x=freed.x,
                    y=freed.y,
                    from_altitude=from_altitude,
                    to_altitude=freed.altitude,
                    tick=tick,
                )
            )
            break  # at most one commander can be docked to a given robot

    remaining_robots = tuple(r for r in state.robots if r.entity_id != entity_id)

    new_state = (
        state.with_capture_progress(remaining_capture_progress)
        .with_commanders(tuple(updated_commanders))
        .with_robots(remaining_robots)
    )

    if not robot.destroyed:
        events.append(
            RobotDestroyedEvent(
                sequence=resolved_sequencer.next_sequence(),
                entity_id=entity_id,
                owner=robot.owner,
                x=robot.x,
                y=robot.y,
                tick=tick,
            )
        )

    return new_state, tuple(events)


def is_blink_update_tick(tick: int, rules: EngineRules = DEFAULT_RULES) -> bool:
    """Whether ``tick`` starts a game cycle, when blinking robots update.

    Positive multiples of ``rules.robot_fire_cycle_ticks`` -- the same
    cadence-tick convention as projectile advances (tick 0 never qualifies).
    """
    return tick > 0 and tick % rules.robot_fire_cycle_ticks == 0


def advance_destroyed_robots(
    state: GameState,
    world: WorldMap | None,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Run one game cycle of every blinking robot (`Lb0fa_robot_update`).

    Only on :func:`is_blink_update_tick` ticks, at the start of the step:
    the Spectrum updates robots before bullets in each cycle. In id order,
    a destroyed robot with cycles left loses one -- which toggles
    :attr:`~nether_earth.robot.Robot.present` -- and one with none left is
    removed (`Lb116_robot_destroyed`): if its 2×2 is plain, empty ground in
    the physical world it leaves rough debris (:func:`robot_debris_anchor`),
    then :func:`remove_robot`. With ``world`` ``None`` no debris is placed.
    Returns ``state`` itself when nothing changed.
    """
    if not is_blink_update_tick(tick, rules) or not any(
        robot.destroyed for robot in state.robots
    ):
        return state, ()

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()
    events: list[Event] = []
    for robot in state.robots:
        remaining = robot.destroyed_cycles_remaining
        if remaining is None:
            continue
        if remaining > 0:
            counted = robot.with_destroyed_cycles_remaining(remaining - 1)
            state = state.with_robots(
                tuple(counted if r.entity_id == robot.entity_id else r for r in state.robots)
            )
            continue
        # The world it stood in: debris is decided against it, and a docked
        # commander is set down on the robot's top in it.
        physical_world = scenery_world(world, state) if world is not None else None
        if physical_world is not None:
            debris = robot_debris_anchor(physical_world, robot)
            if debris is not None:
                state = state.with_robot_debris((*state.robot_debris, debris))
        state, removal_events = remove_robot(
            state, robot.entity_id, tick, rules, resolved_sequencer, world=physical_world
        )
        events.extend(removal_events)
    return state, tuple(events)


# --------------------------------------------------------------------------
# Structure destruction and nuclear detonation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructureDestroyedEvent(Event):
    """A war base or factory was permanently removed from play.

    Mirrors :class:`~nether_earth.capture.StructureCapturedEvent`'s shape:
    the structure's identity and kind are carried directly on the event so
    consumers (replay, rendering, victory evaluation) never need a separate
    state read to know what was destroyed. Unlike
    :class:`RobotDestroyedEvent`, there is no ``owner`` field -- a
    destroyed structure's last owner is not part of the locked "a structure
    was destroyed" fact this event reports, and a consumer that wants it can
    already read it from the effective world/ownership just before this
    event's tick.
    """

    structure_id: EntityId
    structure_kind: CapturableStructureKind
    tick: int


def destroy_structure(
    state: GameState,
    structure_id: EntityId,
    structure_kind: CapturableStructureKind,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, Event | None]:
    """Permanently remove ``structure_id`` from play, with full associated-state cleanup.

    Returns ``(state, None)`` -- the caller's own ``state`` object,
    unchanged, with no event -- when ``structure_id`` is already recorded in
    ``state.structure_destruction``: this is the idempotency guard, mirroring
    :func:`destroy_robot`'s own "destroying twice is a safe no-op" exactly
    (same object identity, not merely equality, on the second call).

    Otherwise, in this fixed order:

    1. **Capture progress cleanup**: any
       :class:`~nether_earth.capture.CaptureProgress` entry naming this
       structure (``progress.structure_id == structure_id``) is dropped -- a
       destroyed structure can no longer be mid-capture.
    2. **Ownership override cleanup**: any
       :class:`~nether_earth.capture.StructureOwnership` entry naming this
       structure (``record.structure_id == structure_id``) is dropped -- a
       destroyed structure has no meaningful "current owner" override
       anymore. This does not touch ``WorldMap.war_bases``/``factories``'
       static starting owner (that value is never mutated); it only clears
       the runtime override, since :func:`effective_world` below is what
       actually excludes the structure from being seen at all going
       forward.
    3. **Destruction marker**: ``structure_id`` is added to
       ``state.structure_destruction``.
    4. **Event emission**: a single :class:`StructureDestroyedEvent` is
       emitted.

    All three state changes (capture-progress filter, ownership-override
    filter, destruction-marker addition) are applied in the fewest possible
    ``GameState`` transitions.

    ``rules`` is accepted (and currently unused) for signature symmetry with
    :func:`destroy_robot`/`combat.py`'s
    :func:`~nether_earth.combat.apply_damage`, and to keep this function's
    shape stable for any later rules-driven refinement.
    """
    if state.structure_destroyed(structure_id):
        return state, None

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()

    remaining_capture_progress = tuple(
        progress for progress in state.capture_progress if progress.structure_id != structure_id
    )
    remaining_structure_ownership = tuple(
        record for record in state.structure_ownership if record.structure_id != structure_id
    )
    updated_destruction = (*state.structure_destruction, structure_id)

    new_state = (
        state.with_capture_progress(remaining_capture_progress)
        .with_structure_ownership(remaining_structure_ownership)
        .with_structure_destruction(updated_destruction)
    )

    event = StructureDestroyedEvent(
        sequence=resolved_sequencer.next_sequence(),
        structure_id=structure_id,
        structure_kind=structure_kind,
        tick=tick,
    )

    return new_state, event


def effective_world(base_world: WorldMap, state: GameState) -> WorldMap:
    """Return ``base_world`` with ownership overrides AND destruction both layered on top.

    Composes on top of `capture.py`'s own
    :func:`~nether_earth.capture.effective_world` (which layers
    ``state.structure_ownership`` overrides -- see that function's
    docstring), then additionally filters OUT any war base/factory whose
    ``id`` is in ``state.structure_destruction``, via
    ``dataclasses.replace(world, war_bases=..., factories=...)`` -- the same
    mechanism `map_overlay.py`'s
    :func:`~nether_earth.map_overlay.apply_overlay` already uses to build a
    filtered ``WorldMap`` without mutating the original.

    Returns ``capture.effective_world(base_world, state)``'s own result
    unchanged when ``state.structure_destruction`` is empty (true for every
    match before its first nuclear detonation completes) -- no need to
    construct a second new ``WorldMap`` when there is nothing to filter,
    mirroring ``capture.effective_world``'s identical "no overrides, return
    unchanged" optimization.

    This is the single composed entry point every destruction-aware
    subsystem reads structure existence through; `engine.py`'s per-tick
    world resolution uses it rather than ``capture.effective_world``.
    """
    ownership_applied = _capture_effective_world(base_world, state)
    if not (state.structure_destruction or state.scenery_debris or state.robot_debris):
        return ownership_applied

    key = (
        id(ownership_applied),
        state.structure_destruction,
        state.scenery_debris,
        state.robot_debris,
    )
    cached = _EFFECTIVE_WORLD_MEMO.get(key)
    if cached is not None and cached[0] is ownership_applied:
        return cached[1]

    result = _apply_debris(ownership_applied, state)
    if len(_EFFECTIVE_WORLD_MEMO) >= _MEMO_MAX_ENTRIES:
        _EFFECTIVE_WORLD_MEMO.clear()
    _EFFECTIVE_WORLD_MEMO[key] = (ownership_applied, result)
    return result


def scenery_world(base_world: WorldMap, state: GameState) -> WorldMap:
    """Return the physical world: ``base_world`` with every kind of debris applied.

    For the physical checks `engine.py` runs against the base map -- robot
    move validation and commander collision. Nuclear debris, the
    rubble of a nuked building and the debris a robot killed in combat left
    are all rough, 3 high and no longer blocking; ownership
    overrides are not applied. Returns ``base_world`` itself when there is no
    debris; memoized like :func:`effective_world`.
    """
    if not (state.scenery_debris or state.structure_destruction or state.robot_debris):
        return base_world
    key = (id(base_world), state.structure_destruction, state.scenery_debris, state.robot_debris)
    cached = _SCENERY_WORLD_MEMO.get(key)
    if cached is not None and cached[0] is base_world:
        return cached[1]
    result = _apply_debris(base_world, state)
    if len(_SCENERY_WORLD_MEMO) >= _MEMO_MAX_ENTRIES:
        _SCENERY_WORLD_MEMO.clear()
    _SCENERY_WORLD_MEMO[key] = (base_world, result)
    return result


def _apply_debris(world: WorldMap, state: GameState) -> WorldMap:
    """Return ``world`` with ``state``'s debris applied.

    Each debris blocker (``state.scenery_debris``) and each
    destroyed building (``state.structure_destruction``) leaves the world,
    and every cell it covered becomes rough debris: `Lbc27_replace_building_by_debris`
    stamps a random type 6/7 piece over every part of a nuked building, as
    `Lba44_robots_handled` does over a box. Each ``state.robot_debris``
    anchor turns its 2×2 into debris the same way (`Lb116_robot_destroyed`).
    Debris cells are :attr:`~nether_earth.terrain.TerrainType.ROUGH`
    -- the class of the native rough pieces of types 6/7 (type < 8, so no
    chassis is blocked) -- with the map's ``terrain.debris_height`` (3, the
    ``Ld7bc_map_piece_heights`` entry of types 6/7). Ids no longer
    naming a blocker or building are ignored.
    """
    gone = set(state.scenery_debris) | set(state.structure_destruction)
    cells = dict(world.terrain.cells)
    heights = dict(world.terrain.heights)

    def rubble(x: int, y: int) -> None:
        cells[(x, y)] = TerrainType.ROUGH
        heights[(x, y)] = world.terrain.debris_height

    def keep(structures: tuple[_S, ...]) -> tuple[_S, ...]:
        remaining = []
        for structure in structures:
            if structure.id in gone:
                for component in structure.components:
                    rubble(component.x, component.y)
            else:
                remaining.append(structure)
        return tuple(remaining)

    blockers = keep(world.blockers)
    war_bases = keep(world.war_bases)
    factories = keep(world.factories)
    for x, y in state.robot_debris:
        for cell in unit_footprint_cells(x, y):
            rubble(*cell)
    terrain = replace(world.terrain, cells=cells, heights=heights)
    return replace(
        world, blockers=blockers, war_bases=war_bases, factories=factories, terrain=terrain
    )


def robot_debris_anchor(world: WorldMap, robot: Robot) -> tuple[int, int] | None:
    """Return where ``robot``, removed after its blink, leaves debris, or ``None``.

    `Lb116_robot_destroyed` adds a random type 6/7 piece over the robot's
    2×2 only when all four map cells are empty (element type 0): plain
    ground with no terrain piece, structure, scenery or earlier debris.
    ``world`` is the physical world (:func:`scenery_world`). A robot killed
    by a nuclear blast leaves none (`Lba44` removes it directly).
    """
    for x, y in unit_footprint_cells(robot.x, robot.y):
        if not (0 <= x < world.width and 0 <= y < world.height):
            return None
        if world.terrain.terrain_at(x, y) is not TerrainType.NORMAL:
            return None
        if components_at(world, x, y):
            return None
    return robot.x, robot.y


#: Memo for :func:`effective_world`, same discipline as
#: ``capture._EFFECTIVE_WORLD_MEMO``: the function is pure, entries hold the
#: ownership-applied world so its ``id`` cannot be recycled while cached, and
#: returning one stable object per state keeps downstream ``id(world)`` memos
#: (``capture_footprint``) hitting after a detonation.
_DebrisKey = tuple[
    int, tuple[EntityId, ...], tuple[EntityId, ...], tuple[tuple[int, int], ...]
]
_EFFECTIVE_WORLD_MEMO: dict[_DebrisKey, tuple[WorldMap, WorldMap]] = {}
_SCENERY_WORLD_MEMO: dict[_DebrisKey, tuple[WorldMap, WorldMap]] = {}
_MEMO_MAX_ENTRIES = 256


def _in_robot_window(carrier: Robot, robot: Robot, rules: EngineRules) -> bool:
    """Return whether ``robot`` stands inside the carrier-centred nuclear robot window.

    The window (`Lba02_look_for_robots_in_range_of_nuclear_bomb`) is
    ``len(rules.nuclear_robot_window_row_widths)`` rows tall, centred on the
    carrier's row, each row centred on the carrier's column. Map-edge
    clipping needs no code: every robot is already inside the map.

    The window tests robot *anchors* (the Spectrum scans the map marks,
    which sit on each robot's anchor cell), so a 2×2 robot counts only
    when its anchor is inside (`_specs/resolved-questions.md` "2×2 robots, commander, projectiles and heli-pad").
    """
    return _cell_in_window(carrier, robot.x, robot.y, rules)


def _cell_in_window(carrier: Robot, x: int, y: int, rules: EngineRules) -> bool:
    """Return whether cell ``(x, y)`` is inside the carrier-centred nuclear window."""
    widths = rules.nuclear_robot_window_row_widths
    row = y - carrier.y + len(widths) // 2
    if not 0 <= row < len(widths):
        return False
    return abs(x - carrier.x) <= widths[row] // 2


def _blocker_anchor(blocker: Blocker) -> tuple[int, int]:
    """Return the cell `Lba44_robots_handled` tests for ``blocker``: its bottom-left corner.

    `Lbd91_add_element_to_map` stamps a 2x2 element at ``x..x+1, y-1..y``
    and clears map bit 5 only on its ``(x, y)`` corner; the blast loop skips
    every cell with bit 5 set. That corner is the lowest x and highest y of
    the element's cells.
    """
    return (
        min(component.x for component in blocker.components),
        max(component.y for component in blocker.components),
    )


def _blockers_in_blast(world: WorldMap, carrier: Robot, rules: EngineRules) -> tuple[EntityId, ...]:
    """Return the destructible blockers whose anchor cell lies in the nuclear window.

    `Lba44_robots_handled` walks the same trimmed window
    as the robot scan and replaces each element of type 17-20 anchored there
    with rough debris; the type-21 fence survives. The map marks those
    elements ``destructible``. ``world`` must already exclude earlier debris.
    """
    return tuple(
        blocker.id
        for blocker in world.blockers
        if blocker.destructible and _cell_in_window(carrier, *_blocker_anchor(blocker), rules)
    )


def _building_anchor(
    world: WorldMap, structure: WarBase | Factory, kind: CapturableStructureKind
) -> tuple[int, int] | None:
    """Return the Spectrum building-struct coordinate of ``structure``, or ``None``.

    The Spectrum stores one ``(x, y)`` per building (``BUILDING_STRUCT_X``/
    ``BUILDING_STRUCT_Y``), which is also the cell its capture loop checks;
    the engine models that cell as the structure's capture interaction point
    (`data/maps/zx-spectrum-original.md`, "anchor coordinates"). A structure
    that declares none has no anchor, so no blast can reach it -- the same
    "valid, unmatched map state" treatment `capture.py` gives it.
    """
    interaction_kind = (
        InteractionKind.WARBASE_CAPTURE
        if kind is CapturableStructureKind.WAR_BASE
        else InteractionKind.FACTORY_CAPTURE
    )
    cells = capture_footprint(world, structure.id, interaction_kind)
    return min(cells) if cells else None


def _building_in_blast_range(
    carrier: Robot,
    anchor: tuple[int, int],
    kind: CapturableStructureKind,
    rules: EngineRules,
) -> bool:
    """Apply `Lb99f_fire_nuclear_bomb`'s per-kind building range test.

    ``dx = |carrier.x - anchor.x|``; ``dy = |carrier.y + 1 - anchor.y|``
    with a war base adding 4 more to ``carrier.y``. In range when
    ``dx < axis``, ``dy < axis`` and ``dx + dy < sum`` -- all strict.
    """
    carrier_y = carrier.y + rules.nuclear_building_dy_offset
    if kind is CapturableStructureKind.WAR_BASE:
        carrier_y += rules.nuclear_war_base_extra_dy_offset
        axis_limit = rules.nuclear_war_base_axis_limit
        sum_limit = rules.nuclear_war_base_sum_limit
    else:
        axis_limit = rules.nuclear_factory_axis_limit
        sum_limit = rules.nuclear_factory_sum_limit
    dx = abs(carrier.x - anchor[0])
    dy = abs(carrier_y - anchor[1])
    return dx < axis_limit and dy < axis_limit and dx + dy < sum_limit


def _first_building_in_blast(
    world: WorldMap, carrier: Robot, rules: EngineRules
) -> tuple[WarBase | Factory, CapturableStructureKind] | None:
    """Return the one building a detonation by ``carrier`` destroys, or ``None``.

    War bases are scanned before factories, each in the map's declared
    order -- the original map declares them in the Spectrum's building-index
    order (`Lbf46`/`Lbf6e` tables) -- and the first one in range wins
    ("A nuclear bomb will only destroy at most one building"). ``world``
    must already exclude destroyed structures. Ownership is not checked.
    """
    candidates: list[tuple[WarBase | Factory, CapturableStructureKind]] = [
        (war_base, CapturableStructureKind.WAR_BASE) for war_base in world.war_bases
    ]
    candidates.extend((factory, CapturableStructureKind.FACTORY) for factory in world.factories)
    for structure, kind in candidates:
        anchor = _building_anchor(world, structure, kind)
        if anchor is not None and _building_in_blast_range(carrier, anchor, kind, rules):
            return structure, kind
    return None


def execute_nuclear_detonation(
    state: GameState,
    world: WorldMap,
    carrier_id: EntityId,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Execute a nuclear detonation carried by ``carrier_id``.

    Returns ``(state, ())`` -- the caller's own ``state`` object, unchanged,
    with no events -- when ``carrier_id`` no longer names a live robot in
    ``state.robots`` (defensive, mirroring :func:`remove_robot`).

    Blast shapes follow the Spectrum code (`_specs/resolved-questions.md` "Nuclear blast shape"),
    all measured from the carrier's position BEFORE any destruction:

    - **Robots:** every other robot, of either side, inside the trimmed
      window (:func:`_in_robot_window`), in canonical ``entity_id`` order.
      `Lba33` tests the map mark, so a blinking robot goes only on a cycle
      it is shown (:attr:`~nether_earth.robot.Robot.present`); one hidden
      this cycle survives the blast and finishes its blink.
    - **Buildings:** at most one -- the first war base, else factory, in
      range of the effective world (:func:`_first_building_in_blast`), so
      already-destroyed structures are skipped.
    - **Carrier:** always removed, even while blinking (a docked player
      can still fire from a blinking robot, `Lac99`).
    - **Scenery:** every ``destructible`` blocker anchored in the same window
      becomes rough debris (``state.scenery_debris``; `Lba44_robots_handled`).
      Fences are not destructible.

    Robots are removed at once with :func:`remove_robot` -- no blink and no
    debris (`Lba44` clears the slot itself, skipping `Lb116`).

    Destruction order: carrier, then robots, then the building. ``state`` is
    threaded through each :func:`remove_robot`/:func:`destroy_structure`
    call, all sharing ONE resolved ``EventSequencer`` so event ``sequence``
    numbers preserve that order.
    """
    carrier = state.robot_for(carrier_id)
    if carrier is None:
        return state, ()

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()

    doomed_robots = sorted(
        (
            robot
            for robot in state.robots
            if robot.entity_id != carrier_id
            and robot.present
            and _in_robot_window(carrier, robot, rules)
        ),
        key=lambda robot: robot.entity_id.value,
    )
    live_world = effective_world(world, state)
    doomed_building = _first_building_in_blast(live_world, carrier, rules)
    debris = _blockers_in_blast(live_world, carrier, rules)

    events: list[Event] = []

    current_state, carrier_events = remove_robot(
        state, carrier_id, tick, rules, resolved_sequencer, world=live_world
    )
    events.extend(carrier_events)

    for robot in doomed_robots:
        current_state, robot_events = remove_robot(
            current_state, robot.entity_id, tick, rules, resolved_sequencer, world=live_world
        )
        events.extend(robot_events)

    if doomed_building is not None:
        structure, kind = doomed_building
        current_state, structure_event = destroy_structure(
            current_state, structure.id, kind, tick, rules, resolved_sequencer
        )
        if structure_event is not None:
            events.append(structure_event)

    if debris:
        current_state = current_state.with_scenery_debris((*current_state.scenery_debris, *debris))

    return current_state, tuple(events)


def evaluate_victory_after_nuclear_detonation(
    detonation_events: Iterable[Event],
    world: WorldMap,
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> VictoryEvent | None:
    """Trigger `victory.py`'s one authoritative victory check after a nuclear detonation.

    Mirrors `engine.py`'s Step 2d pattern for capture (see that
    module's ``step``, where a ``StructureCapturedEvent`` naming a war
    base gates a single
    :func:`~nether_earth.victory.evaluate_victory` call) -- just for the
    nuclear-destruction case instead of the capture case. This function does
    not define a second victory rule: it only decides *whether* a war base's
    existence just changed (by scanning ``detonation_events``, the events a
    prior call to :func:`execute_nuclear_detonation` returned) and, if so,
    calls `victory.py`'s single authoritative :func:`~nether_earth.victory.evaluate_victory`
    -- never redefining or duplicating its ownership-counting logic.

    Returns ``None`` immediately, with no call to
    :func:`~nether_earth.victory.evaluate_victory`, when no
    :class:`StructureDestroyedEvent` in ``detonation_events`` names a
    ``CapturableStructureKind.WAR_BASE`` -- no war-base existence changed, so
    there is nothing new for the victory check to see.

    Otherwise, :func:`~nether_earth.victory.evaluate_victory` is called
    EXACTLY ONCE -- this function's body contains exactly one ``if`` gate and
    one call site, so it produces at most one :class:`~nether_earth.victory.VictoryEvent`, never
    one per destroyed war base. It is called against
    ``effective_world(world, state)`` (this module's OWN :func:`effective_world`,
    which layers both capture ownership and destruction on top of ``world``)
    -- the critical correctness point: :func:`~nether_earth.victory.evaluate_victory`
    reads ``world.war_bases`` and counts each entry's ``owner``, and a
    destroyed war base must be excluded from that count entirely (not merely
    "owned by nobody"), which :func:`effective_world` already guarantees by
    filtering destroyed structures out of the returned ``WorldMap.war_bases``
    tuple. This is why `victory.py` itself has no nuclear-specific code: a
    destroyed war base is simply absent from the
    list ``evaluate_victory`` iterates, so nobody's owned-count includes it --
    exactly the correct effect of "this player now effectively owns one fewer
    war base."
    """
    any_war_base_destroyed = any(
        isinstance(event, StructureDestroyedEvent)
        and event.structure_kind is CapturableStructureKind.WAR_BASE
        for event in detonation_events
    )
    if not any_war_base_destroyed:
        return None

    return _evaluate_victory(effective_world(world, state), state, tick, sequencer)
