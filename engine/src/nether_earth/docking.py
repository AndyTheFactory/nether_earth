"""Commander docking/undocking on friendly robots.

Implements the physical commander/robot interaction described by
`_specs/functional-spec.md` §8.4 and the resolved decision in
`_specs/resolved-questions.md` "Landing on an enemy robot":

- descending onto the top of a **friendly** robot automatically docks the
  commander (``FREE`` -> ``DOCKED(robot_id)``);
- while docked, the commander's effective position/altitude is derived from
  the robot it is docked to, and independent movement is disabled;
- rising away from a docked robot undocks the commander (``DOCKED`` ->
  ``FREE``) and starts the exit lift (see :func:`apply_undock`);
- **enemy** robots remain physical collision surfaces only -- descent stops
  at the top of an enemy robot's stack (implemented by
  :mod:`nether_earth.collision`, which treats every
  :class:`~nether_earth.collision.RobotFixture` as a top surface identically
  regardless of ownership), but no docking, control transfer, or contact
  damage ever occurs there. This module's job for that half is
  almost entirely to *not* dock on an enemy fixture -- see
  :func:`attempt_auto_dock` -- not to reimplement any collision/stopping
  geometry.

Why a dedicated module
-----------------------
Docking is a distinct gameplay concern from both the state shape
(``commander.py``) and general collision geometry (``collision.py``): it is
the *rule* that decides when a purely-geometric "resting on
a robot" collision outcome additionally causes a mode transition and control
hand-off. Keeping it separate means ``collision.py`` never needs to know
about ownership-conditional docking, and ``commander_movement.py`` never
needs to know about robots at all. This module is the composition point
that answers "is this specific top-surface contact a dock, or merely a
landing?" per `_specs/resolved-questions.md` "Landing on an enemy robot".

This module, like ``collision.py``, uses
:class:`~nether_earth.collision.RobotFixture` as its view of "a robot's
position/height/owner" (see
`docs/mechanics/commander.md`); it does not need a
richer robot model.

Purity/determinism
--------------------
Every public function here is pure: it takes immutable arguments
(``Commander``, ``RobotFixture``, an integer ``tick``) and
returns new values, never mutating its arguments and never reading
wall-clock time or any other non-deterministic source. This matches the
convention already established by ``commander.py``/``commander_movement.py``/
``collision.py``.

This module does not wire itself into ``engine.step()``; `engine.py` calls
the functions below.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.collision import RobotFixture
from nether_earth.commander import Commander, CommanderMode
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules

__all__ = [
    "CommanderDockedEvent",
    "CommanderUndockedEvent",
    "apply_undock",
    "attempt_auto_dock",
    "auto_dock_with_event",
    "docked_movement_allowed",
    "follow_docked_robot",
]


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommanderDockedEvent(Event):
    """A ``FREE`` commander automatically docked onto a friendly robot.

    Emitted by :func:`attempt_auto_dock` exactly when a dock transition
    actually occurs (never on a no-op check), following the same
    "only emit when something changed" convention as the commander movement
    events.
    """

    player_id: PlayerId
    robot_id: EntityId
    x: int
    y: int
    altitude: int
    tick: int


@dataclass(frozen=True, slots=True)
class CommanderUndockedEvent(Event):
    """A ``DOCKED`` commander undocked (via rising intent) back to ``FREE``.

    ``from_altitude``/``to_altitude`` are the commander's altitude before
    and after the transition. The exit lift runs on the
    following vertical updates (see :func:`apply_undock`), so for a
    rise-intent undock both are the robot-top altitude.
    """

    player_id: PlayerId
    robot_id: EntityId
    x: int
    y: int
    from_altitude: int
    to_altitude: int
    tick: int


# --------------------------------------------------------------------------
# Auto-docking (descent onto a friendly robot)
# --------------------------------------------------------------------------


def attempt_auto_dock(
    commander: Commander,
    robots: tuple[RobotFixture, ...],
    rules: EngineRules = DEFAULT_RULES,
) -> Commander:
    """Return ``commander``, docked if it is resting exactly on a friendly robot top.

    A ``FREE`` commander docks automatically the instant its vertical range
    ``[altitude, altitude + commander_height)`` *touches* (per
    ``collision.py``'s half-open touching-vs-overlap semantics -- resting,
    not overlapping) the top of a same-``(x, y)`` :class:`RobotFixture` whose
    ``owner`` matches ``commander.player_id``. Concretely this is
    ``commander.altitude == robot.top`` at a matching ``(x, y)``: the
    robot's ground-rooted range is ``[0, top)``, and a commander at
    ``altitude == top`` occupies ``[top, top + commander_height)``,
    which touches without overlapping -- exactly the "resting on top"
    condition `_specs/functional-spec.md` §8.4 ("automatic when descending
    onto the top of a friendly robot") and §8.3's height-aware collision
    model describe. ``top`` is the robot's stack height plus the terrain
    altitude under its body (:attr:`RobotFixture.top`), so a
    robot on rough or a mountain is docked 2, 3 or 6 higher.

    2×2 bodies (`_specs/resolved-questions.md` "2×2 robots, commander, projectiles and heli-pad"): ``(x, y)`` is the
    anchor of both bodies, and docking needs the *same* anchor -- the bodies
    coincide exactly. The Spectrum's game loop docks only when the robot's
    map mark is on the ship's own anchor cell and ``altitude == robot
    height + robot altitude`` (``La69a``: ``bit 6`` on
    ``Lcca0_compute_player_map_ptr``, then ``La720_land_on_robot``). A
    commander resting on a robot whose body only partly overlaps its own is
    held up by it (`collision.py`) but does not dock.

    Only the *first* matching friendly fixture (in ``robots`` order) is
    docked to -- at most one robot can legally occupy a given cell (enforced
    upstream by the occupancy rules), so this is
    only ambiguous for a deliberately malformed test fixture list, in which
    case picking the first match keeps this function total and deterministic
    rather than raising.

    Returns ``commander`` unchanged (same object) when:

    - ``commander.mode`` is already ``DOCKED`` (docking only applies to a
      ``FREE`` commander -- a docked commander cannot re-dock without first
      undocking);
    - ``commander.elevate_updates_remaining > 0`` (an exit lift is running;
      see :func:`apply_undock`);
    - no robot fixture is at ``commander``'s ``(x, y)`` with
      ``top == commander.altitude``;
    - the matching fixture at that position is **enemy**-owned
      (``robot.owner != commander.player_id``) -- per
      `_specs/resolved-questions.md` "Landing on an enemy robot", enemy robots are collision surfaces
      only; the commander may be resting there (that resting/stopping
      geometry is `collision.py`'s job, already exercised before this
      function is ever called), but no docking occurs. This is this
      function's primary "enemy robot contact" behavior.

    This function does not itself decide *whether* the commander was
    allowed to descend to ``commander.altitude`` in the first place -- that
    legality (including "stop at any robot's top, friendly or enemy") is
    ``collision.py``'s :func:`~nether_earth.collision.commander_vertical_move_allowed`,
    which the caller calls first. This function only
    asks "given where the commander now legally is, does that constitute a
    friendly dock?".
    """
    if commander.mode is not CommanderMode.FREE:
        return commander
    if commander.elevate_updates_remaining > 0:
        # Exit lift running (see apply_undock): no re-dock yet.
        return commander
    for robot in robots:
        if robot.x != commander.x or robot.y != commander.y:
            continue
        if robot.top != commander.altitude:
            continue
        if robot.owner != commander.player_id:
            # Enemy robot: physical collision surface only. No docking.
            return commander
        return commander.with_docking(CommanderMode.DOCKED, robot.id)
    return commander


def auto_dock_with_event(
    commander: Commander,
    robots: tuple[RobotFixture, ...],
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[Commander, CommanderDockedEvent | None]:
    """:func:`attempt_auto_dock`, plus a :class:`CommanderDockedEvent` on transition.

    Convenience wrapper so callers that want both the updated commander and
    an event (matching the ``(updated, event | None)`` shape used throughout
    ``commander_movement.py``) do not need to hand-roll the "did the mode
    actually change" check themselves.
    """
    updated = attempt_auto_dock(commander, robots, rules)
    if updated is commander or updated.mode is not CommanderMode.DOCKED:
        return updated, None
    docked_robot_id = updated.docked_robot_id
    assert docked_robot_id is not None  # guaranteed by the DOCKED invariant
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderDockedEvent(
        sequence=sequence,
        player_id=updated.player_id,
        robot_id=docked_robot_id,
        x=updated.x,
        y=updated.y,
        altitude=updated.altitude,
        tick=tick,
    )
    return updated, event


# --------------------------------------------------------------------------
# Following the docked robot
# --------------------------------------------------------------------------


def follow_docked_robot(
    commander: Commander,
    robot: RobotFixture,
    rules: EngineRules = DEFAULT_RULES,
) -> Commander:
    """Return ``commander`` repositioned to rest on top of ``robot``.

    While ``DOCKED``, the commander does not move independently -- its
    effective ``(x, y, altitude)`` is derived from the robot fixture/state it
    is docked to (`_specs/functional-spec.md` §8.4, "commander follows the
    robot"). This sets ``x``/``y`` to ``robot.x``/``robot.y`` and ``altitude``
    to ``robot.top`` (resting exactly on top, the same touching condition
    :func:`attempt_auto_dock` used to dock in the first place; the Spectrum's
    ``Lb495`` sets the ship's altitude to ``ROBOT_STRUCT_HEIGHT +
    ROBOT_STRUCT_ALTITUDE`` after each step of a directly controlled
    robot); ``mode`` and
    ``docked_robot_id`` are left unchanged -- this function only repositions,
    it never itself docks or undocks.

    This is a pure per-call reposition, not a physics step: callers are
    expected to call it once per
    tick for a docked commander, passing whatever the robot's current
    position/height happens to be that tick. It does not validate that
    ``robot.id == commander.docked_robot_id`` -- callers are responsible for
    passing the correct fixture; this keeps the function a simple, total
    positional transform rather than duplicating lookup/validation that
    naturally lives at the call site (which already has to look the robot up
    by id to pass it in).
    """
    return commander.with_position(robot.x, robot.y).with_altitude(robot.top)


# --------------------------------------------------------------------------
# Disabling independent movement while docked
# --------------------------------------------------------------------------


def docked_movement_allowed(commander: Commander) -> bool:
    """Return ``True`` iff ``commander`` may perform independent movement.

    ``False`` whenever ``commander.mode is CommanderMode.DOCKED`` --
    `_specs/functional-spec.md` §8.4 states plainly that "independent
    commander movement is disabled" while docked; the only action available
    to a docked commander is rising away (undocking, see
    :func:`apply_undock`).

    This module does not itself call ``commander_movement.py``'s move/
    vertical-physics functions. The integrating caller (``engine.step()``)
    calls this guard *before* invoking
    :func:`~nether_earth.commander_movement.apply_commander_move` or
    :func:`~nether_earth.commander_movement.apply_vertical_physics` for a
    given commander, short-circuiting them entirely (e.g. rejecting a
    ``CommanderMoveCommand`` from a docked player, and skipping the vertical-
    physics cadence step for a docked commander) rather than relying on
    those functions to reject the case internally. (Note:
    :func:`~nether_earth.commander_movement.apply_vertical_physics` already
    defensively no-ops for a non-``FREE`` commander as a second layer of
    protection -- see that function's docstring -- but the *primary*, intended
    gate for "docked commanders don't move independently" is this function,
    called up front by the integrating caller.)
    """
    return commander.mode is not CommanderMode.DOCKED


# --------------------------------------------------------------------------
# Undocking (rising away)
# --------------------------------------------------------------------------


def apply_undock(
    commander: Commander,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[Commander, CommanderUndockedEvent | None]:
    """Undock ``commander`` if it is ``DOCKED`` and currently holding rise intent.

    1. Trigger: ``commander.mode is DOCKED and commander.rising`` -- the
       persistent rise intent (:meth:`~nether_earth.commander.Commander.with_rising`)
       stands in for the Spectrum robot HUD's EXIT option, so holding rise
       undocks and no new input vocabulary is needed.
    2. Transition: ``mode`` flips ``DOCKED`` -> ``FREE`` and
       ``docked_robot_id`` is cleared in the same authoritative step.
    3. Lift (`_specs/resolved-questions.md` "Commander vertical limits and speed"): the commander gets
       ``rules.commander_exit_elevate_updates`` automatic-ascent updates,
       exactly like leaving the construction screen. Spectrum evidence: the
       robot HUD's EXIT option (``#a7fd``--``#a80f``, falling through to
       ``La812_exit_robot``) sets ``Lfd30_player_elevate_timer`` to 5, and
       ``Lafa2_player_ship_keyboard_control_altitude`` then ascends +2 per
       vertical update while the timer runs before gravity resumes. The
       ascent runs on the following vertical-cadence ticks
       (:func:`~nether_earth.commander_movement.apply_vertical_physics`), so
       this function does not move the commander; :func:`attempt_auto_dock`
       refuses to re-dock while the lift runs (the Spectrum's ``La69a``
       game loop runs the ship's altitude update before its dock test, so
       the ship is already above the robot top when that test runs).
       Up/down intent does not shorten the lift (owner decision; the
       Spectrum's ``Laf11`` shortening is not modelled).

    Returns ``(commander, None)`` unchanged when ``commander`` is not
    ``DOCKED`` or is not holding rise intent. Otherwise returns
    ``(updated_commander, CommanderUndockedEvent)``; the event's
    ``from_altitude`` and ``to_altitude`` are both the robot-top altitude.
    """
    if commander.mode is not CommanderMode.DOCKED or not commander.rising:
        return commander, None

    docked_robot_id = commander.docked_robot_id
    assert docked_robot_id is not None  # guaranteed by the DOCKED invariant
    freed = commander.with_docking(CommanderMode.FREE, None).with_elevate_updates(
        rules.commander_exit_elevate_updates
    )

    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderUndockedEvent(
        sequence=sequence,
        player_id=freed.player_id,
        robot_id=docked_robot_id,
        x=freed.x,
        y=freed.y,
        from_altitude=commander.altitude,
        to_altitude=freed.altitude,
        tick=tick,
    )
    return freed, event
