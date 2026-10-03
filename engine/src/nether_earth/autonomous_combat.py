"""Consume order engagement intent for autonomous firing.

`orders.py` produces :class:`~nether_earth.orders.EngagementIntent`
every tick a robot's standing order (``StopAndDefend``/``SearchDestroy``) has
a valid hostile target and at least one structurally capable weapon -- but,
by that module's own explicit design, an intent is only ever a *statement*:
producing one fires nothing, spends nothing, and mutates nothing (see
`orders.py`'s module docstring, "What this module decides, and what it
deliberately does not"). This module is the combat-side consumer of that
boundary: it turns a qualifying intent into the exact same
:class:`~nether_earth.combat.FireRequest`/:func:`~nether_earth.combat.validate_fire`
call direct control uses, so autonomous fire and direct fire are provably
one code path, not two independently-maintained ones.

Why a new module rather than adding this to `orders.py` or `combat.py`
------------------------------------------------------------------------
Mirrors this codebase's established "one module per subsystem" convention
(`direct_control.py` is a separate module from `movement.py`, even though
both ultimately call into `movement.py`'s validation/execution functions):
`orders.py` owns *deciding* to engage (target selection, weapon
capability), `combat.py` owns *firing* (validation, projectiles, damage,
detonation), and this module owns the narrow re-validation-then-fire bridge
between the two -- consuming ``EngagementIntent`` without touching order
lifecycle, and calling into `combat.py`/`destruction.py` without
duplicating either.

Re-validation scope -- what changes between the intent and this tick
---------------------------------------------------------------------
`orders.py` computes ``EngagementIntent`` from the SAME entry ``state``
every other tick-start subsystem reads, but combat effects (projectile
hits, nuclear detonations) execute in this same
authoritative step and can destroy the very source robot or target an
intent named before this function ever runs. This function re-validates
only the combat-time facts that may have
changed since the intent was computed -- source robot still alive, target
still alive/valid, weapon still in range (including the electronics range
bonus), and channel availability (via the ordinary ``validate_fire``/
``apply_fire`` path, not re-implemented here) -- and never re-selects a
different target, never re-runs navigation, and never touches
``Order``/``OrderStatus`` lifecycle. A disqualified intent produces
deterministic no-fire: ``(state, ())`` unchanged, not an error and not a
fallback order change.

Weapon selection is deterministic and fixed-order
----------------------------------------------------
``EngagementIntent.weapons`` is already normalized into
`robot_build.py`'s :data:`~nether_earth.robot_build.CANONICAL_WEAPON_ORDER`
(cannon, missile, phaser, nuclear) by ``EngagementIntent``'s own
construction path (`orders.py`'s ``_capable_weapons``, which filters
``robot.build.weapons`` -- itself always canonically ordered by
:class:`~nether_earth.robot_build.RobotBuild`). This module therefore never
re-sorts; it simply walks ``intent.weapons`` in the order given and fires
the first weapon that is still eligible under this tick's re-validation,
so the outcome never depends on iteration order and is always the same for
the same inputs.

Nuclear is never part of that walk (`_specs/resolved-questions.md` "Autonomous use of the nuclear weapon"):
against a robot target only cannon/missile/phaser are considered,
so Stop & Defend and Search & Destroy (robots) never detonate. The only
autonomous detonation is a structure intent (factory/war base), which
`orders.py` emits solely on the tick a Search & Destroy carrier stands on
its target cell; this module additionally requires ``distance_cells == 0``
and a fitted nuclear module. It routes through `destruction.py`'s
:func:`~nether_earth.destruction.execute_nuclear_detonation` via the
identical ``FireRequest``/:func:`~nether_earth.combat.validate_fire`
acceptance boundary direct-control nuclear fire uses.

One fire path, verified by construction not inspection
------------------------------------------------------------
:func:`consume_engagement_intent` performs no combat legality check of its
own beyond source/target existence and range: weapon-fitted and channel-
occupancy legality are entirely `combat.py`'s :func:`~nether_earth.combat.validate_fire`'s
concern, reached through the exact same :class:`~nether_earth.combat.FireRequest`
shape and :func:`~nether_earth.combat.apply_fire`/:func:`~nether_earth.combat.validate_fire`
calls direct control uses. Neither this module nor `combat.py` branches on
"was this call autonomous or direct" anywhere -- see
``test_combat_autonomous.py``'s dedicated test proving this by construction
(an equivalent direct-control ``FireRequest`` and an autonomous-path
``FireRequest`` for the same effective robot/weapon/target produce the same
:class:`~nether_earth.combat.FireResult`/event shape).

Autonomous fire happens only on the robot's own update
------------------------------------------------------
`Lb154_robot_ai_update` decrements ``ROBOT_STRICT_CYCLES_TO_NEXT_UPDATE``
every game cycle and returns until it reaches 0. On the update it either
fires (``Lb6d6_weapon_fire``, then ``ROBOT_STRUCT_DESIRED_MOVE_DIRECTION``
is set to 0) or moves, and both paths end in ``Lb20d_move_robot``, which
calls ``Lb5f3_determine_speed_based_on_terrain`` to reload the counter
from ``Lb61d_robot_movement_speed_table`` using ``ROBOT_STRUCT_ALTITUDE``.
A firing update does not move (``Lb471`` returns at once for direction 0),
so the altitude is still that of the cell the robot stands on, and the
next update is one speed-table period for *that* cell later. After a move
the altitude is re-read for the new cell (``Lb495``), which is the
engine's move duration into that cell (`movement.move_duration_ticks`).

The engine already runs the move half of that cadence: a move takes the
table's ticks and the robot's next update is the tick the move completes.
This module adds the fire half without a new timer:

- :func:`autonomous_update_due` -- a robot is at an update when it has no
  move in flight and at least :func:`autonomous_update_period_ticks` have
  passed since its ``last_fire_tick``;
- :func:`consume_engagement_intent` fires only when the update is due;
- :func:`gate_order_requests` drops an order's move request when the robot
  is not at an update, or when it will fire on this update (a firing
  update does not move).

Stationary robots that have not fired have no tracked phase and are
treated as due every tick (documented in `_specs/resolved-questions.md` "Exact projectile mechanics").
Direct (combat-mode) fire is not an order and is not gated here; it keeps
`combat.py`'s once-per-cycle rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from nether_earth.combat import FireRequest, apply_fire, validate_fire, weapon_range_cells
from nether_earth.commander import CommanderMode
from nether_earth.destruction import effective_world, execute_nuclear_detonation
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId
from nether_earth.map import WorldMap
from nether_earth.movement import (
    RobotMoveRequest,
    RobotTurnStartedEvent,
    robot_move_duration_ticks,
    unit_move_terrain,
)
from nether_earth.orders import (
    EngagementIntent,
    EngagementTargetKind,
    OrderEvaluation,
    StopAndDefend,
)
from nether_earth.robot import Robot, RobotFacing, RobotTurnTransition
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "autonomous_update_due",
    "autonomous_update_period_ticks",
    "consume_engagement_intent",
    "consume_engagement_intents",
    "gate_order_requests",
    "settle_walk_outs",
]


def _weapon_eligible(
    weapon: ModuleIdentity, distance_cells: int, electronics_fitted: bool, rules: EngineRules
) -> bool:
    """Return whether ``weapon`` can still reach a target ``distance_cells`` away.

    Nuclear is always eligible (see the module docstring: it has no
    meaningful range gate). Every other weapon reuses `combat.py`'s own
    :func:`~nether_earth.combat.weapon_range_cells` lookup -- the exact same
    range table :func:`~nether_earth.combat.apply_fire` consults when
    creating a projectile -- plus ``rules.electronics_range_bonus_cells``
    when the firing robot's build carries
    :attr:`~nether_earth.robot_build.ModuleIdentity.ELECTRONICS`, mirroring
    :func:`~nether_earth.combat.apply_fire`'s own identical convention so
    the two range computations can never silently diverge.
    """
    if weapon is ModuleIdentity.NUCLEAR:
        return True
    max_range = weapon_range_cells(weapon, rules)
    if electronics_fitted:
        max_range += rules.electronics_range_bonus_cells
    return distance_cells <= max_range


def _target_still_valid(
    intent: EngagementIntent, state: GameState, world: WorldMap
) -> bool:
    """Return whether ``intent``'s named target is still alive/valid in ``state``.

    A robot target is valid when :meth:`~nether_earth.state.GameState.robot_for`
    still resolves it. A structure target (factory/war base) is valid when
    it still appears in `destruction.py`'s
    :func:`~nether_earth.destruction.effective_world` -- which already
    excludes destroyed structures -- for the matching kind. Existence is
    the only combat-time fact re-validated here for structures; ownership
    re-checking is `orders.py`'s target-selection concern and is not
    repeated.
    """
    if intent.target_kind is EngagementTargetKind.ROBOT:
        # A blinking robot is never shot at (`Lb68e_object_found`), though a
        # Search & Destroy hunt may still be heading for it (`Lb41d`).
        target = state.robot_for(intent.target_id)
        return target is not None and not target.destroyed

    live_world = effective_world(world, state)
    structures = (
        live_world.factories
        if intent.target_kind is EngagementTargetKind.FACTORY
        else live_world.war_bases
    )
    return any(structure.id == intent.target_id for structure in structures)



def _facing_toward(robot: Robot, intent: EngagementIntent) -> RobotFacing | None:
    """Return the cardinal facing that points ``robot`` at ``intent``'s target.

    The Spectrum's own scan (``Lb626_check_directions_with_enemy_robots``)
    is per-direction: it looks along the robot's lane and the lanes either
    side, so "the direction the enemy is in" is already cardinal there. An
    engagement intent here carries a target cell instead, which may be off
    both axes, so the cardinal is chosen by dominant axis with X winning a
    tie -- the same total, deterministic rule the old target-cell fire
    direction used, now applied to turning rather than to the bullet.

    Returns ``None`` when the target is on the robot's own cell, where no
    direction can be derived and turning would be meaningless.
    """
    raw_dx = intent.target_x - robot.x
    raw_dy = intent.target_y - robot.y
    if raw_dx == 0 and raw_dy == 0:
        return None
    if abs(raw_dx) >= abs(raw_dy):
        return RobotFacing.EAST if raw_dx > 0 else RobotFacing.WEST
    return RobotFacing.SOUTH if raw_dy > 0 else RobotFacing.NORTH


def consume_engagement_intent(
    intent: EngagementIntent,
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Re-validate and, if a weapon still qualifies, fire ``intent``.

    Returns ``(state, ())`` -- the caller's own ``state`` object, unchanged,
    with no events -- for every disqualifying outcome (source robot
    destroyed, target destroyed/invalid, no weapon in range, or the
    selected weapon's normal-weapon channel already occupied): a no-fire
    tick is never an error, mirroring `movement.py`'s "same illegal case
    always same reason" discipline extended to "no fire is always silent".

    In order:

    1. **Source re-check**: ``state.robot_for(intent.robot_id)`` must still
       resolve. A robot destroyed since this intent was computed (by an
       earlier combat effect this same tick) can no longer fire.
    1b. **Update gate**: :func:`autonomous_update_due` must hold;
       an autonomous robot fires only on its own update.
    2. **Target re-check**: see :func:`_target_still_valid`.
    3. **Weapon selection**: the first weapon in ``intent.weapons``
       (already canonically ordered -- see the module docstring) that is
       still eligible under :func:`_weapon_eligible`, skipping nuclear for
       robot targets; a structure target selects nuclear only at
       ``distance_cells == 0`` (see the module docstring). No weapon
       eligible means no fire this tick.
    4. **Fire**: a :class:`~nether_earth.combat.FireRequest` is built for
       the selected weapon and routed through the identical boundary
       direct control uses -- :func:`~nether_earth.combat.validate_fire`
       plus :func:`~nether_earth.destruction.execute_nuclear_detonation`
       for nuclear, :func:`~nether_earth.combat.apply_fire` for cannon/
       missile/phaser (which itself re-runs :func:`~nether_earth.combat.validate_fire`
       and naturally rejects with
       :attr:`~nether_earth.combat.FireRejectionReason.CHANNEL_OCCUPIED`
       when the robot's channel is already busy -- the correct "no fire
       this tick" outcome for a channel-blocked autonomous shot, not
       re-implemented here).

    This function never re-selects a different target, never re-runs
    navigation, and never touches ``Order``/``OrderStatus`` lifecycle --
    only `orders.py` decides what a robot intends to engage.
    """
    robot = state.robot_for(intent.robot_id)
    if robot is None:
        return state, ()

    if not autonomous_update_due(robot, world, tick, rules):
        return state, ()

    request = _select_fire_request(intent, robot, state, world, rules)
    if request is None:
        return state, ()

    # Turn to face the target before shooting (owner decision, 2026-09-23):
    # a bullet travels in the robot's own facing (`Lb6d6_weapon_fire`), so a
    # robot aimed the wrong way has nothing to shoot at. It spends this
    # update rotating 90 degrees instead of firing, exactly as `Lb471` does
    # for a move it is not facing; a 180-degree turn therefore costs two
    # updates before the shot goes out. Nuclear is exempt: it detonates on
    # the carrier's own cell, so facing is irrelevant to it.
    if request.weapon is not ModuleIdentity.NUCLEAR:
        desired = _facing_toward(robot, intent)
        if desired is not None and desired is not robot.facing:
            if robot.turning is not None:
                return state, ()  # already rotating; the turn resolves on its own
            turn = RobotTurnTransition(
                entity_id=robot.entity_id,
                from_facing=robot.facing,
                to_facing=robot.facing.rotate_toward(desired),
                started_tick=tick,
                duration_ticks=rules.robot_turn_ticks,
            )
            sequence = sequencer.next_sequence() if sequencer is not None else 0
            turned = robot.with_turning(turn)
            return state.with_robots(
                tuple(turned if r.entity_id == robot.entity_id else r for r in state.robots)
            ), (
                RobotTurnStartedEvent(
                    sequence=sequence,
                    entity_id=robot.entity_id,
                    owner=robot.owner,
                    from_facing=turn.from_facing,
                    to_facing=turn.to_facing,
                    started_tick=tick,
                    duration_ticks=turn.duration_ticks,
                ),
            )

    if request.weapon is ModuleIdentity.NUCLEAR:
        result = validate_fire(request, state)
        if not result.accepted:
            return state, ()
        return execute_nuclear_detonation(state, world, intent.robot_id, tick, rules, sequencer)

    new_state, _result, events = apply_fire(
        request, state, world, tick, rules, sequencer, autonomous=True
    )
    return new_state, events


def autonomous_update_period_ticks(
    robot: Robot, world: WorldMap, rules: EngineRules = DEFAULT_RULES
) -> int:
    """Ticks from a firing update to the robot's next update.

    ``Lb5f3_determine_speed_based_on_terrain`` reloads the counter from the
    terrain the robot stands on after the update; a firing update does not
    move, so that is the highest piece under the robot's own 2×2 body
    (``Lb5d6_map_altitude_2x2``; see
    :func:`~nether_earth.movement.unit_move_terrain`). The value is the same
    per-(chassis, terrain) table the move duration uses
    (`_specs/resolved-questions.md` "Exact movement speeds and terrain penalties").
    """
    return robot_move_duration_ticks(robot, unit_move_terrain(world, robot.x, robot.y), rules)


def autonomous_update_due(
    robot: Robot, world: WorldMap, tick: int, rules: EngineRules = DEFAULT_RULES
) -> bool:
    """Whether ``tick`` is one of ``robot``'s own updates (see the module docstring).

    Not while a move is in flight: the update closing a move is the tick
    it completes, and `engine.py` resolves completions before orders run.
    After a shot, not before :func:`autonomous_update_period_ticks` ticks.
    """
    if robot.movement is not None:
        return False
    if robot.last_fire_tick is None:
        return True
    return tick >= robot.last_fire_tick + autonomous_update_period_ticks(robot, world, rules)


def _will_fire(
    intent: EngagementIntent,
    robot: Robot,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
    tick: int,
) -> bool:
    """Whether ``intent`` would fire on ``tick`` against the entry ``state``.

    A dry run through the same acceptance path
    :func:`consume_engagement_intent` uses (the returned state is
    discarded), so the move gate and the fire gate cannot disagree about
    the entry state.
    """
    request = _select_fire_request(intent, robot, state, world, rules)
    if request is None:
        return False
    if request.weapon is ModuleIdentity.NUCLEAR:
        return validate_fire(request, state).accepted
    _state, result, _events = apply_fire(request, state, world, tick, rules, autonomous=True)
    return result.accepted


def gate_order_requests(
    evaluations: Iterable[OrderEvaluation],
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
) -> tuple[RobotMoveRequest, ...]:
    """Return the order move requests that may start on ``tick``.

    A request is dropped when its robot is not at an update (it fired less
    than one period ago), or when the same evaluation's intent will fire on
    this update: `Lb154_robot_ai_update` sets the desired direction to 0
    after ``Lb6d6_weapon_fire``, so a firing update does not move.
    """
    requests: list[RobotMoveRequest] = []
    for evaluation in evaluations:
        if evaluation.request is None:
            continue
        robot = state.robot_for(evaluation.robot_id)
        if robot is None or not autonomous_update_due(robot, world, tick, rules):
            continue
        if evaluation.intent is not None and _will_fire(
            evaluation.intent, robot, state, world, rules, tick
        ):
            continue
        requests.append(evaluation.request)
    return tuple(requests)


def _select_fire_request(
    intent: EngagementIntent,
    robot: Robot,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> FireRequest | None:
    """Steps 2-3 of :func:`consume_engagement_intent`: re-check the target, pick a weapon."""
    if not _target_still_valid(intent, state, world):
        return None

    electronics_fitted = robot.build.electronics is ModuleIdentity.ELECTRONICS
    selected_weapon: ModuleIdentity | None = None
    if intent.target_kind is not EngagementTargetKind.ROBOT:
        # §19: a structure is engaged only by detonating on its target cell.
        if intent.distance_cells == 0 and ModuleIdentity.NUCLEAR in intent.weapons:
            selected_weapon = ModuleIdentity.NUCLEAR
    else:
        for weapon in intent.weapons:
            if weapon is ModuleIdentity.NUCLEAR:
                continue  # §19: never chosen autonomously against a robot
            if _weapon_eligible(weapon, intent.distance_cells, electronics_fitted, rules):
                selected_weapon = weapon
                break
    if selected_weapon is None:
        return None

    return FireRequest(
        robot_id=intent.robot_id,
        player=robot.owner,
        weapon=selected_weapon,
    )


def consume_engagement_intents(
    evaluations: Iterable[OrderEvaluation],
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Consume every non-``None`` intent in ``evaluations``, threading ``state`` through.

    Walks ``evaluations`` in the order given (the caller -- eventually
    `engine.py` -- is responsible for passing `orders.py`'s
    :func:`~nether_earth.orders.evaluate_orders` own canonical
    ``state.robots`` order; this function does not re-sort), skips any
    evaluation whose ``intent`` is ``None``, and calls
    :func:`consume_engagement_intent` once per remaining intent, feeding
    each call's returned state into the next and accumulating every emitted
    event in that same order.
    """
    events: list[Event] = []
    for evaluation in evaluations:
        if evaluation.intent is None:
            continue
        state, new_events = consume_engagement_intent(
            evaluation.intent, state, world, tick, rules, sequencer
        )
        events.extend(new_events)
    return state, tuple(events)


def settle_walk_outs(
    entry_state: GameState,
    state: GameState,
    evaluations: Iterable[OrderEvaluation],
    started: Iterable[EntityId],
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
) -> GameState:
    """Update every walking-out robot's ``exit_steps_remaining`` after the tick's move batch.

    ``entry_state`` is the state the tick's orders were evaluated and gated
    against; ``state`` is the state after the move batch; ``started`` names
    the robots whose move started this tick. For each robot still walking
    out, following ``Lb1e9_no_enemy_robots_in_sight``:

    - a commander docked on it ends the walk-out: the Spectrum does not
      update a robot the ship has landed on, and leaving it zeroes its
      steps (see :func:`~nether_earth.orders.apply_set_robot_order`);
    - its walk-out step started: one step fewer;
    - otherwise, if this tick was one of its own updates
      (:func:`autonomous_update_due`), the
      walk-out ends -- the step south was blocked, lost a same-tick
      contention, or the update fired instead (``Lb154``'s enemy-in-sight
      branch overwrites the steps before any move);
    - between updates nothing changes.
    """
    # A Stop & Defend evaluation only ever carries a walk-out request.
    walking = {
        evaluation.robot_id
        for evaluation in evaluations
        if evaluation.request is not None and isinstance(evaluation.order, StopAndDefend)
    }
    docked = {
        commander.docked_robot_id
        for commander in state.commanders
        if commander.mode is CommanderMode.DOCKED
    }
    started_ids = set(started)
    updated: list[Robot] = []
    changed = False
    for robot in state.robots:
        steps = robot.exit_steps_remaining
        if steps > 0:
            entry = entry_state.robot_for(robot.entity_id)
            if robot.entity_id in docked:
                steps = 0
            elif robot.entity_id in started_ids and robot.entity_id in walking:
                steps -= 1
            elif entry is not None and entry.movement is None and autonomous_update_due(
                entry, world, tick, rules
            ):
                steps = 0
        if steps != robot.exit_steps_remaining:
            robot = replace(robot, exit_steps_remaining=steps)
            changed = True
        updated.append(robot)
    return state.with_robots(tuple(updated)) if changed else state
