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
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.commander import CommanderMode
from nether_earth.docking import CommanderUndockedEvent
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = ["RobotDestroyedEvent", "destroy_robot"]


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
