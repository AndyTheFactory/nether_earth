"""Commander docking/undocking on friendly robots (issue #40, M3.4).

Implements the physical commander/robot interaction described by
`_specs/functional-spec.md` §8.4 and the resolved decision in
`_specs/open-questions.md` §14:

- descending onto the top of a **friendly** robot automatically docks the
  commander (``FREE`` -> ``DOCKED(robot_id)``);
- while docked, the commander's effective position/altitude is derived from
  the robot it is docked to, and independent movement is disabled;
- rising away from a docked robot undocks the commander (``DOCKED`` ->
  ``FREE``) and starts it visibly ascending, in the same authoritative step;
- **enemy** robots remain physical collision surfaces only -- descent stops
  at the top of an enemy robot's stack (already implemented by
  :mod:`nether_earth.collision`, issue #39, which treats every
  :class:`~nether_earth.collision.RobotFixture` as a top surface identically
  regardless of ownership), but no docking, control transfer, or contact
  damage ever occurs there. This module's job for that half of the issue is
  almost entirely to *not* dock on an enemy fixture -- see
  :func:`attempt_auto_dock` -- and to prove it via tests, not to reimplement
  any collision/stopping geometry.

Why a dedicated module
-----------------------
Docking is a distinct gameplay concern from both the state shape
(``commander.py``, issue #37) and general collision geometry (``collision.py``,
issue #39): it is the *rule* that decides when a purely-geometric "resting on
a robot" collision outcome additionally causes a mode transition and control
hand-off. Keeping it separate means ``collision.py`` never needs to know
about ownership-conditional docking, and ``commander_movement.py`` (issue
#38) never needs to know about robots at all -- both stay exactly as
reusable as their own issues intended. This module is the composition point
that answers "is this specific top-surface contact a dock, or merely a
landing?" per `_specs/open-questions.md` §14.

No robot subsystem exists yet (M4/M5) -- this module, like ``collision.py``,
uses :class:`~nether_earth.collision.RobotFixture` as the test-only stand-in
for "a robot's position/height/owner", per
`_specs/milestones/03-commander-movement-docking.md`'s explicit allowance to
use M3 robot-height fixtures until M4 introduces full robot construction/
state. It is not this module's job to invent a richer robot model.

Purity/determinism
--------------------
Every public function here is pure: it takes immutable arguments
(``Commander``, ``RobotFixture``, ``GameState``, an integer ``tick``) and
returns new values, never mutating its arguments and never reading
wall-clock time or any other non-deterministic source. This matches the
convention already established by ``commander.py``/``commander_movement.py``/
``collision.py``.

Integration scope note (same as #38/#39): this module does NOT wire itself
into ``engine.step()``, ``commands.py``'s validation, or ``snapshot.py`` --
that is issue #42's job. Tests in this milestone call the functions below
directly, following the same "reference implementation, not yet threaded
into the authoritative tick loop" pattern #38 used for
``advance_commander_movement_tick``.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.collision import RobotFixture
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import VerticalMoveCheck, apply_automatic_elevation
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "CommanderDockedEvent",
    "CommanderUndockedEvent",
    "apply_undock",
    "attempt_auto_dock",
    "auto_dock_with_event",
    "docked_movement_allowed",
    "follow_docked_robot",
]


def _permissive_vertical_check(state: GameState, mover: Commander, dest_altitude: int) -> bool:
    """Default :data:`~nether_earth.commander_movement.VerticalMoveCheck`: always allow.

    A local copy (not imported) of ``commander_movement.py``'s private
    default of the same shape/behavior -- that module deliberately does not
    export its permissive defaults (they are an internal implementation
    detail of its own function signatures), so this module defines its own
    rather than reaching into another module's private names.
    """
    return True


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommanderDockedEvent(Event):
    """A ``FREE`` commander automatically docked onto a friendly robot.

    Emitted by :func:`attempt_auto_dock` exactly when a dock transition
    actually occurs (never on a no-op check), following the same
    "only emit when something changed" convention as #38's movement events.
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

    ``from_altitude``/``to_altitude`` record the single automatic-elevation
    ascent step (see :func:`apply_undock`) applied in the same authoritative
    step as the mode transition, so this event alone tells a consumer both
    "control was handed back to the commander" and "it started visibly
    rising away from the robot".
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
    ``commander.altitude == robot.height`` at a matching ``(x, y)``: the
    robot's ground-rooted range is ``[0, height)``, and a commander at
    ``altitude == height`` occupies ``[height, height + commander_height)``,
    which touches without overlapping -- exactly the "resting on top"
    condition `_specs/functional-spec.md` §8.4 ("automatic when descending
    onto the top of a friendly robot") and §8.3's height-aware collision
    model describe.

    2×2 bodies (CR002.4, `_specs/open-questions.md` §21): ``(x, y)`` is the
    anchor of both bodies, and docking needs the *same* anchor -- the bodies
    coincide exactly. The Spectrum's game loop docks only when the robot's
    map mark is on the ship's own anchor cell and ``altitude == robot
    height + robot altitude`` (``La69a``: ``bit 6`` on
    ``Lcca0_compute_player_map_ptr``, then ``La720_land_on_robot``). A
    commander resting on a robot whose body only partly overlaps its own is
    held up by it (`collision.py`) but does not dock.

    Only the *first* matching friendly fixture (in ``robots`` order) is
    docked to -- at most one robot can legally occupy a given cell (enforced
    upstream by the eventual robot subsystem/occupancy rules), so this is
    only ambiguous for a deliberately malformed test fixture list, in which
    case picking the first match keeps this function total and deterministic
    rather than raising.

    Returns ``commander`` unchanged (same object) when:

    - ``commander.mode`` is already ``DOCKED`` (docking only applies to a
      ``FREE`` commander -- a docked commander cannot re-dock without first
      undocking);
    - no robot fixture is at ``commander``'s ``(x, y)`` with
      ``height == commander.altitude``;
    - the matching fixture at that position is **enemy**-owned
      (``robot.owner != commander.player_id``) -- per
      `_specs/open-questions.md` §14, enemy robots are collision surfaces
      only; the commander may be resting there (that resting/stopping
      geometry is `collision.py`'s job, already exercised before this
      function is ever called), but no docking occurs. This is this
      function's primary "enemy robot contact" behavior and is covered
      explicitly by this issue's tests.

    This function does not itself decide *whether* the commander was
    allowed to descend to ``commander.altitude`` in the first place -- that
    legality (including "stop at any robot's top, friendly or enemy") is
    ``collision.py``'s :func:`~nether_earth.collision.commander_vertical_move_allowed`,
    which #42's integration is expected to call first. This function only
    asks "given where the commander now legally is, does that constitute a
    friendly dock?".
    """
    if commander.mode is not CommanderMode.FREE:
        return commander
    for robot in robots:
        if robot.x != commander.x or robot.y != commander.y:
            continue
        if robot.height != commander.altitude:
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
    to ``robot.height`` (resting exactly on top, the same touching condition
    :func:`attempt_auto_dock` used to dock in the first place); ``mode`` and
    ``docked_robot_id`` are left unchanged -- this function only repositions,
    it never itself docks or undocks.

    This is a pure per-call reposition, not a physics step: callers (this
    milestone's tests, and #42's future per-tick integration once a real
    robot subsystem can move a robot around) are expected to call it once per
    tick for a docked commander, passing whatever the robot's current
    position/height happens to be that tick. It does not validate that
    ``robot.id == commander.docked_robot_id`` -- callers are responsible for
    passing the correct fixture; this keeps the function a simple, total
    positional transform rather than duplicating lookup/validation that
    naturally lives at the call site (which already has to look the robot up
    by id to pass it in).
    """
    return commander.with_position(robot.x, robot.y).with_altitude(robot.height)


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
    vertical-physics functions -- that file is issue #38's, already merged
    and stable, and this issue is explicitly scoped to not modify it. #42
    (the later integration issue that wires all of #38-#41 into
    ``engine.step()``) is expected to call this guard *before* invoking
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
    state: GameState,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    vertical_check: VerticalMoveCheck = _permissive_vertical_check,
    sequencer: EventSequencer | None = None,
) -> tuple[Commander, CommanderUndockedEvent | None]:
    """Undock ``commander`` if it is ``DOCKED`` and currently holding rise intent.

    Mechanics (this module's own design decision -- the issue text leaves
    the exact cadence details open, "the locked automatic/ascent semantics"
    only fixes the +2 step size, not exactly when/how many steps fire):

    1. Trigger: ``commander.mode is DOCKED and commander.rising``. Rising
       intent is the existing persistent-intent mechanism #38 already
       threads through :class:`~nether_earth.commander_movement.CommanderSetVerticalIntentCommand`/
       :meth:`~nether_earth.commander.Commander.with_rising` (that module's
       docstring already documents that a docked commander's rise intent is
       *preserved*, "relevant to later undocking flows", even though it
       currently has no physics effect while docked -- this function is
       that later flow). Using the same intent flag (rather than a separate
       one-shot "undock" command) means a player holding rise while docked
       undocks and keeps ascending in one continuous input, matching how
       rise/descend already works for a free commander -- no new input
       vocabulary is introduced.
    2. Transition: immediately flip ``mode`` ``DOCKED`` -> ``FREE`` and clear
       ``docked_robot_id`` via :meth:`~nether_earth.commander.Commander.with_docking`
       -- control returns to the player in the same authoritative step that
       detects the rise intent, not on a later tick, so there is no
       observable "docked but already not following the robot" limbo state.
    3. Ascent: apply exactly *one*
       :func:`~nether_earth.commander_movement.apply_automatic_elevation`
       step (the +2-per-call primitive #38 built and explicitly reserved for
       "#40 undocking ... to reuse", per that function's own docstring) to
       the now-``FREE`` commander, so it visibly starts rising away from the
       robot's top surface in the same tick undocking occurs -- otherwise a
       commander could undock and then sit motionless at the robot's exact
       former position/altitude, which is indistinguishable from "still
       docked" to an observer and would let it immediately re-dock next
       tick via :func:`attempt_auto_dock` (since it would still be exactly
       resting on the robot's top). One ascent step per undock call keeps
       this a single deterministic transition rather than looping ascent
       steps internally (repeated undock calls -- i.e. repeated ticks with
       rise still held -- naturally continue the climb via this same
       function, or via normal :func:`~nether_earth.commander_movement.apply_vertical_physics`
       cadence once #42 wires vertical-cadence ticks for a now-``FREE``
       commander; this function does not gate on vertical-cadence ticks,
       matching :func:`apply_automatic_elevation`'s own no-cadence-gating
       docstring).
    4. ``vertical_check``, when supplied (e.g. bound to
       :func:`~nether_earth.collision.commander_vertical_move_allowed` via
       ``functools.partial`` per that module's #38/#42 integration
       contract), can still block the ascent step outright (e.g. something
       physically overlaps the space immediately above) -- in that case the
       mode transition to ``FREE`` still happens (control is handed back
       regardless), but altitude does not change and no
       :class:`CommanderVerticalUpdatedEvent`-shaped altitude change is
       folded into the emitted event; see the return-value note below.

    Returns ``(commander, None)`` unchanged (no transition at all) when
    ``commander`` is not ``DOCKED`` or is not currently holding rise intent.
    Otherwise returns ``(updated_commander, CommanderUndockedEvent)``: the
    event's ``from_altitude``/``to_altitude`` record whatever the ascent step
    actually achieved (equal to each other if the ascent step itself was
    blocked or already at the max-altitude bound -- the event still fires
    because the *mode* transition unconditionally occurred, which is the
    primary fact the event announces; a caller that also cares about the
    altitude-changed sub-fact can compare the two fields).
    """
    if commander.mode is not CommanderMode.DOCKED or not commander.rising:
        return commander, None

    docked_robot_id = commander.docked_robot_id
    assert docked_robot_id is not None  # guaranteed by the DOCKED invariant
    from_altitude = commander.altitude
    freed = commander.with_docking(CommanderMode.FREE, None)

    risen, _vertical_event = apply_automatic_elevation(
        freed, state, tick, rules, vertical_check, sequencer=None
    )

    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderUndockedEvent(
        sequence=sequence,
        player_id=risen.player_id,
        robot_id=docked_robot_id,
        x=risen.x,
        y=risen.y,
        from_altitude=from_altitude,
        to_altitude=risen.altitude,
        tick=tick,
    )
    return risen, event
