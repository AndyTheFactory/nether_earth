"""Consume M5 engagement intent for autonomous firing (issue #77, M6.7).

`orders.py` (M5.5, issue #64) produces :class:`~nether_earth.orders.EngagementIntent`
every tick a robot's standing order (``StopAndDefend``/``SearchDestroy``) has
a valid hostile target and at least one structurally capable weapon -- but,
by that module's own explicit design, an intent is only ever a *statement*:
producing one fires nothing, spends nothing, and mutates nothing (see
`orders.py`'s module docstring, "What this module decides, and what it
deliberately does not"). This module is Milestone 6's consumer of that
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

Re-validation scope -- what changes between M5's intent and this tick
------------------------------------------------------------------------
`orders.py` computes ``EngagementIntent`` from the SAME entry ``state``
every other tick-start subsystem reads, but Milestone 6's own combat
effects (projectile hits, nuclear detonations) execute in this same
authoritative step and can destroy the very source robot or target an
intent named before this function ever runs. Per the task's locked scope,
this function re-validates only the combat-time facts that may have
changed since M5 computed the intent -- source robot still alive, target
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

Nuclear is never part of that walk (`_specs/open-questions.md` §19,
CR001.1): against a robot target only cannon/missile/phaser are considered,
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
"""

from __future__ import annotations

from collections.abc import Iterable

from nether_earth.combat import FireRequest, apply_fire, validate_fire, weapon_range_cells
from nether_earth.destruction import effective_world, execute_nuclear_detonation
from nether_earth.events import Event, EventSequencer
from nether_earth.map import WorldMap
from nether_earth.orders import EngagementIntent, EngagementTargetKind, OrderEvaluation
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "consume_engagement_intent",
    "consume_engagement_intents",
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
    excludes destroyed structures -- for the matching kind. Per the task's
    locked scope, existence is the only combat-time fact re-validated here
    for structures; ownership re-checking is M5's target-selection concern
    and is not repeated.
    """
    if intent.target_kind is EngagementTargetKind.ROBOT:
        return state.robot_for(intent.target_id) is not None

    live_world = effective_world(world, state)
    structures = (
        live_world.factories
        if intent.target_kind is EngagementTargetKind.FACTORY
        else live_world.war_bases
    )
    return any(structure.id == intent.target_id for structure in structures)


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
       resolve. A robot destroyed since M5 computed this intent (by an
       earlier combat effect this same tick) can no longer fire.
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

    if not _target_still_valid(intent, state, world):
        return state, ()

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
        return state, ()

    request = FireRequest(
        robot_id=intent.robot_id,
        player=robot.owner,
        weapon=selected_weapon,
        target_x=intent.target_x,
        target_y=intent.target_y,
    )

    if selected_weapon is ModuleIdentity.NUCLEAR:
        result = validate_fire(request, state)
        if not result.accepted:
            return state, ()
        return execute_nuclear_detonation(state, world, intent.robot_id, tick, rules, sequencer)

    new_state, _result, events = apply_fire(
        request, state, world, tick, rules, sequencer, autonomous=True
    )
    return new_state, events


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
