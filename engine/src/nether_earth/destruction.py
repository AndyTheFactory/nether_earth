"""Robot destruction service, shared by projectile damage and (later) nuclear effects (issue #76, M6.6).

Why a dedicated module
-----------------------
Destroying a robot is not just "remove it from ``state.robots``" -- it also
has to leave no stale reference anywhere else in ``GameState``: an
in-progress capture attempt naming the robot, and a commander currently
``DOCKED`` to it, both need explicit, correct cleanup in the same
authoritative step. `_specs/milestones/06-combat-damage-victory.md`'s
"Destruction service" is explicitly meant to be the *one* place this
cleanup logic lives, because it must be reachable identically from more
than one caller: `combat.py`'s per-hit :func:`~nether_earth.combat.apply_damage`
(this task) today, and a later task's nuclear-detonation area-destruction
effect (#78) tomorrow. Centralizing it here means those two callers can
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
  removing the robot from ``state.robots`` (step 4 below) is therefore
  already sufficient cleanup for both; a second explicit
  occupancy/reservation-clearing call here would be redundant.
- It does not remove the destroyed robot's in-flight
  :class:`~nether_earth.combat.Projectile` (if it had one via
  ``active_projectile_id``) from ``state.projectiles``. A normal-weapon
  projectile is a physical object already travelling independently of its
  firer per the locked rules; `combat.py`'s own
  :func:`~nether_earth.combat.advance_projectiles` (Task 4, M6.4) already
  handles a missing source robot safely when that projectile eventually
  terminates (its channel-release step becomes a no-op). Deleting the
  projectile here would silently despawn a still-in-flight shot the moment
  its firer dies, which the locked rules do not call for.

Structure destruction and nuclear detonation (issue #78, M6.8)
-------------------------------------------------------------------
This task extends the module with the structure-side half of destruction
(:func:`destroy_structure`) and the nuclear area-effect that is this
codebase's only caller of it (:func:`execute_nuclear_detonation`), mirroring
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
established API shape mid-milestone. It is instead recorded as a
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

Nuclear radius metric: a documented engine policy, NOT verified Spectrum fidelity
-------------------------------------------------------------------------------------
See :func:`execute_nuclear_detonation`'s own docstring for the full
reasoning. In short: this task adopts simple Manhattan distance
(``abs(dx) + abs(dy) <= rules.nuclear_radius_cells``), reusing this
codebase's one existing distance-metric convention (`orders.py`'s
``_manhattan``) rather than inventing new geometry for one weapon. This is a
deliberate, centralized, configured policy choice per
`_specs/milestones/06-combat-damage-victory.md`'s own fidelity note for this
exact case ("if the exact original radius metric/boundary inclusion is not
yet verified, isolate it in one named rule/policy and document the
configured choice instead of claiming unsupported fidelity") -- it is
explicitly *not* a claim that the original ZX Spectrum used this metric.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nether_earth.capture import CapturableStructureKind
from nether_earth.capture import effective_world as _capture_effective_world
from nether_earth.commander import CommanderMode
from nether_earth.docking import CommanderUndockedEvent
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState
from nether_earth.structures import Factory, WarBase, occupied_cells

__all__ = [
    "RobotDestroyedEvent",
    "StructureDestroyedEvent",
    "destroy_robot",
    "destroy_structure",
    "effective_world",
    "execute_nuclear_detonation",
]


@dataclass(frozen=True, slots=True)
class RobotDestroyedEvent(Event):
    """A robot was removed from play (destroyed).

    Mirrors :class:`~nether_earth.capture.StructureCapturedEvent`'s "this
    entity's fate changed" shape: the entity's identity, owner, and last
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
    """Remove ``entity_id`` from play, with full associated-state cleanup.

    Returns ``(state, ())`` -- the caller's own ``state`` object, unchanged,
    with no events -- when ``entity_id`` no longer names a live robot in
    ``state.robots``: already destroyed, or never existed. This is the
    idempotency guard: calling this function twice in a row on the same id
    returns the exact same ``state`` object (by identity, not merely by
    equality) the second time, mirroring
    :func:`~nether_earth.movement.cancel_robot_move`'s "cancelling twice is
    a safe no-op" precedent exactly.

    Otherwise, in this fixed order:

    1. **Capture progress cleanup**: any :class:`~nether_earth.capture.CaptureProgress`
       entry naming this robot (``progress.robot_id == entity_id``) is
       dropped -- a simple filter, not a call into `capture.py` itself,
       since this is a plain tuple-membership removal with no accrual
       logic of its own to reuse.
    2. **Docked-commander safety**: if a commander is currently ``DOCKED``
       to this robot (``commander.docked_robot_id == entity_id``), it is
       forced back to ``FREE`` at the robot's own last known position/
       height -- explicitly, via :meth:`~nether_earth.commander.Commander.with_docking`
       then :meth:`~nether_earth.commander.Commander.with_position`/
       :meth:`~nether_earth.commander.Commander.with_altitude`, rather than
       relying on `docking.py`'s :func:`~nether_earth.docking.follow_docked_robot`
       having already run this tick -- that ordering is not guaranteed from
       this function's own perspective, so setting position/altitude here
       explicitly keeps this function self-contained and correct regardless
       of call order. This reuses `docking.py`'s existing
       :class:`~nether_earth.docking.CommanderUndockedEvent` shape (rather
       than inventing a near-duplicate event type) -- ``from_altitude`` is
       the commander's altitude immediately before this forced transition,
       ``to_altitude`` is ``robot.height`` (the robot's last physical top
       surface, which the commander was resting on). The commander itself
       is never damaged or destroyed -- only relocated to a safe ``FREE``
       state.
    3. **Robot removal**: the robot is dropped from ``state.robots``. Per
       the module docstring, this alone is sufficient occupancy/reservation
       cleanup (both are derived projections over ``state.robots``, not
       separately stored) and the robot's in-flight projectile (if any) is
       deliberately left untouched (see the module docstring).
    4. **Event emission**: a :class:`RobotDestroyedEvent` is always emitted
       on this (non-no-op) path. When a docked-commander relocation also
       occurred, its :class:`~nether_earth.docking.CommanderUndockedEvent`
       is emitted first, then :class:`RobotDestroyedEvent` -- the commander
       safety consequence is presented as happening in response to the
       destruction it precedes in the returned tuple, matching this
       module's "the robot's fate is the event this function exists to
       report" framing, while still surfacing the relocation as its own
       first-class event rather than folding it into ``RobotDestroyedEvent``'s
       own fields.

    All state changes (capture-progress filter, commander update if any,
    robot removal) are applied in the fewest possible ``GameState``
    transitions.

    ``rules`` is accepted (and currently unused) for signature symmetry
    with `combat.py`'s :func:`~nether_earth.combat.apply_damage` (its own
    caller) and to keep this function's shape stable for a later rules-
    driven destruction refinement (e.g. `_specs/open-questions.md` §9's
    documented "blink" grace-period mechanic, deliberately out of this
    task's scope -- destruction here is immediate, not staged).
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
                .with_altitude(robot.height)
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


# --------------------------------------------------------------------------
# Structure destruction and nuclear detonation (issue #78, M6.8)
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
    subsystem should read structure existence through going forward -- in
    particular, a later integration task (M6.10) is expected to switch
    `engine.py`'s per-tick world resolution, and any other current direct
    caller of ``capture.effective_world`` for a subsystem that should also
    respect destruction (e.g.
    `resource_production.py`'s ``apply_daily_production``,
    `heli_pad.py`/`construction_session.py` entry, `orders.py`'s
    ``select_capture_target``/``select_destroy_target``), from
    ``capture.effective_world`` to this function. This task does not modify
    any of those callers itself -- it only builds and documents this one
    composable function; wiring it in is later tasks' scope.
    """
    ownership_applied = _capture_effective_world(base_world, state)
    if not state.structure_destruction:
        return ownership_applied

    destroyed = set(state.structure_destruction)
    remaining_war_bases = tuple(
        war_base for war_base in ownership_applied.war_bases if war_base.id not in destroyed
    )
    remaining_factories = tuple(
        factory for factory in ownership_applied.factories if factory.id not in destroyed
    )
    return replace(ownership_applied, war_bases=remaining_war_bases, factories=remaining_factories)


def _manhattan(ax: int, ay: int, bx: int, by: int) -> int:
    """Return grid distance between two cells.

    Replicates `orders.py`'s own private ``_manhattan`` helper exactly
    (Manhattan distance, not Euclidean/Chebyshev), for consistency with this
    codebase's one existing distance-metric convention -- see
    :func:`execute_nuclear_detonation`'s docstring for why this task adopts
    that same metric for the nuclear-radius check rather than inventing a
    second one. Reimplemented here (not imported) because ``orders._manhattan``
    is a module-private name; importing a private name across modules would
    couple this module to `orders.py`'s internal layout for a one-line pure
    function.
    """
    return abs(ax - bx) + abs(ay - by)


def _eligible_structures_in_radius(
    world: WorldMap,
    epicenter_x: int,
    epicenter_y: int,
    rules: EngineRules,
) -> list[tuple[WarBase | Factory, CapturableStructureKind]]:
    """Return every war base/factory in ``world`` with any cell within the nuclear radius.

    A structure occupies multiple cells (`structures.py`'s
    :func:`~nether_earth.structures.occupied_cells`); it is eligible if the
    MINIMUM Manhattan distance from the epicenter to any of its occupied
    cells is ``<= rules.nuclear_radius_cells``. War bases and factories are
    combined into one canonical ``id.value``-sorted list -- simplest, and
    avoids inventing an unspecified "which kind goes first" rule when both
    could otherwise be sorted independently.
    """
    candidates: list[tuple[WarBase | Factory, CapturableStructureKind]] = [
        (war_base, CapturableStructureKind.WAR_BASE) for war_base in world.war_bases
    ]
    candidates.extend((factory, CapturableStructureKind.FACTORY) for factory in world.factories)

    eligible: list[tuple[WarBase | Factory, CapturableStructureKind]] = []
    for structure, kind in candidates:
        min_distance = min(
            _manhattan(epicenter_x, epicenter_y, cell_x, cell_y)
            for cell_x, cell_y in occupied_cells(structure)
        )
        if min_distance <= rules.nuclear_radius_cells:
            eligible.append((structure, kind))

    eligible.sort(key=lambda entry: entry[0].id.value)
    return eligible


def execute_nuclear_detonation(
    state: GameState,
    world: WorldMap,
    carrier_id: EntityId,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Execute a nuclear detonation carried by ``carrier_id``: area destruction.

    Returns ``(state, ())`` -- the caller's own ``state`` object, unchanged,
    with no events -- when ``carrier_id`` no longer names a live robot in
    ``state.robots``. This function should only ever be invoked after
    `combat.py`'s :func:`~nether_earth.combat.validate_fire` has confirmed
    the carrier exists, but it guards defensively anyway, mirroring this
    codebase's consistent idempotency/defensive-no-op convention (e.g.
    :func:`destroy_robot`, :func:`destroy_structure`).

    Radius metric -- a documented engine policy, NOT verified Spectrum fidelity
    -------------------------------------------------------------------------------
    Eligibility (for both robots and structures) is simple Manhattan
    distance from the carrier's epicenter cell:
    ``abs(dx) + abs(dy) <= rules.nuclear_radius_cells``. Disassembly evidence
    gathered for a *different* calculation elsewhere in this milestone's
    research (a "maximum distance in each axis 7, maximum sum of distances
    10" hybrid shape) suggests the original nuclear-radius geometry might
    not be simple Manhattan distance -- but no formal fidelity research task
    in this milestone specifically resolved the nuclear-radius shape itself.
    Per `_specs/milestones/06-combat-damage-victory.md`'s own fidelity note
    for this exact case, this function isolates the uncertain metric behind
    one named policy (this docstring plus :func:`_manhattan`) and documents
    the configured choice explicitly, rather than claiming unsupported
    fidelity: **this is a deliberate engine policy decision, chosen for
    consistency with this engine's one existing distance-metric convention
    (`orders.py`'s ``_manhattan``, reused by every other distance-gated
    system: engagement intent, target selection), not an independently
    verified reproduction of the original ZX Spectrum's nuclear blast
    geometry.**

    Sequence, all against the epicenter recorded from ``carrier``'s
    ``(x, y)`` BEFORE any destruction happens:

    1. Every robot in ``state.robots`` (excluding the carrier itself) within
       the radius, sorted by ``entity_id.value``.
    2. Every war base/factory in
       ``effective_world(world, state)`` (so already-destroyed structures
       and current runtime ownership are correctly reflected before this
       detonation's own effects apply) with any occupied cell within the
       radius, sorted by ``id.value`` (see
       :func:`_eligible_structures_in_radius`).
    3. Destruction order -- this task's own documented deterministic
       decision: the carrier is destroyed first, unconditionally, via
       :func:`destroy_robot`; then every eligible robot in canonical id
       order via :func:`destroy_robot`; then every eligible structure in
       canonical id order via :func:`destroy_structure`. ``state`` is
       threaded sequentially through each call (each returns a new state fed
       into the next), and every emitted event is accumulated, in that same
       order, into the returned tuple.

    ``sequencer`` is resolved to a single shared ``EventSequencer`` ONCE, at
    the top of this function (``sequencer if sequencer is not None else
    EventSequencer()``), and that one resolved instance -- never the
    original, possibly-``None`` ``sequencer`` parameter -- is passed to
    every downstream :func:`destroy_robot`/:func:`destroy_structure` call.
    This is required for the documented carrier -> robots -> structures
    order to actually be recoverable from event ``sequence`` numbers (which
    is what `events.py`'s ``order_events`` sorts by): passing the original
    ``sequencer`` straight through would mean a caller who defaults it to
    ``None`` gets a *fresh* ``EventSequencer()`` constructed independently
    inside each sub-call, so every emitted event would carry ``sequence ==
    0`` instead of a monotonically increasing sequence -- silently losing
    the ordering this function otherwise carefully constructs. Mirrors
    `engine.py`'s own ``step`` convention of constructing one shared
    ``EventSequencer()`` for a whole authoritative step.

    Commanders are never in ``state.robots``/``world.war_bases``/
    ``world.factories`` -- they are structurally excluded already, so this
    function contains no commander-related special-casing at all.
    """
    carrier = state.robot_for(carrier_id)
    if carrier is None:
        return state, ()

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()

    epicenter_x, epicenter_y = carrier.x, carrier.y

    eligible_robots = sorted(
        (
            robot
            for robot in state.robots
            if robot.entity_id != carrier_id
            and _manhattan(epicenter_x, epicenter_y, robot.x, robot.y) <= rules.nuclear_radius_cells
        ),
        key=lambda robot: robot.entity_id.value,
    )
    eligible_structures = _eligible_structures_in_radius(
        effective_world(world, state), epicenter_x, epicenter_y, rules
    )

    events: list[Event] = []

    current_state, carrier_events = destroy_robot(
        state, carrier_id, tick, rules, resolved_sequencer
    )
    events.extend(carrier_events)

    for robot in eligible_robots:
        current_state, robot_events = destroy_robot(
            current_state, robot.entity_id, tick, rules, resolved_sequencer
        )
        events.extend(robot_events)

    for structure, kind in eligible_structures:
        current_state, structure_event = destroy_structure(
            current_state, structure.id, kind, tick, rules, resolved_sequencer
        )
        if structure_event is not None:
            events.append(structure_event)

    return current_state, tuple(events)
