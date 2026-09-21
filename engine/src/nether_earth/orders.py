"""Autonomous robot orders, target selection, and engagement intent (issue #64, M5.5).

`_specs/functional-spec.md` §16 and `_specs/technical-spec.md` §15 lock the
order catalog this module owns::

    StopAndDefend
    Advance(distance_miles)
    Retreat(distance_miles)
    SearchCapture(target_type)
    SearchDestroy(target_type)

plus the two cross-cutting rules that shape every function below:

- ``Advance``/``Retreat`` take 0-50 *miles*, converted through the single
  shared 2-cells-per-mile helper (`rules.py`'s
  :func:`~nether_earth.rules.miles_to_cells`) -- never a local ``* 2``;
- **impossible or invalid orders revert to Stop & Defend**, deterministically
  and without an error, because an order is player input and a player must
  never be able to wedge a robot by naming a goal the world cannot satisfy.

What this module decides, and what it deliberately does not
------------------------------------------------------------
This is the "why" layer above `navigation.py`'s "which way" and
`movement.py`'s "may I". One order evaluation produces at most:

1. a **movement goal** -- a target cell -- which is immediately handed to
   :func:`~nether_earth.navigation.next_navigation_step` and therefore to
   `movement.py`'s legality checks. This module never tests terrain,
   occupancy, commander blocking, or reservations itself, and never
   constructs a :class:`~nether_earth.movement.RobotMoveRequest` except by
   taking the one a navigation policy already validated. An order therefore
   *cannot* walk a robot somewhere direct control could not go;
2. an **engagement intent** (:class:`EngagementIntent`) -- a pure statement
   that a robot has a valid hostile target, which weapons it could bring to
   bear, and how far away the target is.

Milestone 5 explicitly stops there: no firing, no projectile, no damage, no
nuclear detonation (`_specs/milestones/05-orders-navigation-capture.md`,
"Out of scope"). Milestone 6 consumes :class:`EngagementIntent` and owns
every one of those. That is also why :class:`EngagementIntent` carries
``distance_cells`` rather than a boolean "in range": weapon ranges are an
M6 concern and are not yet locked, so deciding "close enough to shoot" here
would be inventing a rule `_specs/open-questions.md` has not resolved.
``weapons`` is likewise "weapons structurally capable against this target
kind", not "weapons that will fire".

Orders are per-robot state; evaluation is stateless
----------------------------------------------------
The order itself lives on :attr:`nether_earth.robot.Robot.order`, so
``state.robots``' single canonical ordering already orders order evaluation
and no parallel ``GameState`` collection can drift out of sync. Everything
*else* is recomputed from the current :class:`~nether_earth.state.GameState`
every tick -- in particular **target selection is never cached**. That is
what makes target disappearance free: a robot whose Search & Destroy target
was destroyed, or whose Search & Capture factory changed hands, simply
selects a different target (or falls back to Stop & Defend when none
remains) on the very next evaluation, with no stale-target bookkeeping that
could survive the thing it pointed at. It mirrors `navigation.py`'s
deliberately plan-free "replanning" for the same reason.

The one piece of order state that *is* retained is
:attr:`Advance.target_x`/:attr:`Retreat.target_x`: "move 20 miles east" is
relative to where the robot stood when the order was given, so the absolute
goal column is bound on first evaluation (the ``PENDING`` -> ``ACTIVE``
lifecycle transition) and stored back on the order. Recomputing it from the
robot's current position every tick would make the robot advance forever.

Order lifecycle
----------------
Every order has the same explicit three-phase lifecycle, reported by
:class:`OrderStatus` and applied by :func:`evaluate_order`:

- **PENDING** -- the order has just been assigned and any absolute goal it
  needs has not been bound yet. Only ``Advance``/``Retreat`` have such a
  goal; every other order is born ``ACTIVE``.
- **ACTIVE** -- the order is producing goals/intent each tick.
- **COMPLETED** -- the goal was reached. ``Advance``/``Retreat`` complete on
  arrival and are *replaced* by :class:`StopAndDefend` (the locked
  "then Stop & Defend" transition). ``SearchCapture`` completes on standing
  in the target's capture footprint, at which point `capture.py` -- not this
  module -- runs the actual capture, and the order is likewise replaced by
  :class:`StopAndDefend` so a captured structure does not keep the robot
  pinned to a goal it has already satisfied. A ``SearchDestroy`` against a
  factory/war base completes the same way on its target cell, and that
  completion evaluation carries the only structure engagement intent --
  the nuclear detonation `autonomous_combat.py` executes
  (`_specs/open-questions.md` §19).
- **FALLBACK** -- the order was invalid or became impossible and was
  replaced by :class:`StopAndDefend`.

:class:`StopAndDefend` itself never completes; it is the terminal order.

What counts as "impossible"
----------------------------
Only conditions that can never resolve on their own, because the locked
non-electronic navigation behavior is that a robot *may legitimately be
stuck* (`_specs/open-questions.md` §5) and a stuck robot must keep trying
rather than silently abandoning its order:

- a structurally invalid order (a distance outside 0-50 miles, a
  non-integer distance, an unrecognized order object);
- an ``Advance``/``Retreat`` whose goal column lies outside the map *and*
  whose robot already sits at that map edge, so zero progress is possible;
- a ``Search`` order for which the world currently offers **no** candidate
  target at all;
- a ``SearchDestroy`` against a structure by a robot carrying no nuclear
  module -- structures require a nuke (issue #64's scope note), so such a
  robot can never complete the order however far it walks;
- a goal :class:`~nether_earth.navigation.ElectronicNavigation` has
  *proved* unreachable (:attr:`~nether_earth.navigation.NavigationStatus.UNREACHABLE`).

:attr:`~nether_earth.navigation.NavigationStatus.BLOCKED` is explicitly NOT
impossible: it is the locked "may get stuck" outcome for a non-electronic
robot and a transient reservation conflict for an electronic one. Both keep
the order and retry next tick.

Determinism
------------
No randomness is drawn here at all -- contention between robots remains
`reservations.py`'s seeded concern, and this module must not perturb that
stream. Every choice is made by stable ordering instead:

- robots are evaluated in ``state.robots``' canonical ``entity_id.value``
  order;
- candidate targets are gathered in canonical id order and selected by
  ``(manhattan distance, entity_id.value)``, so an exact distance tie is
  broken by id and never by map declaration order;
- a target's goal cell is selected from its footprint by
  ``(manhattan distance, x, y)``, so a multi-cell footprint never depends on
  ``frozenset`` iteration order;
- no dict or set is ever iterated to produce an outcome.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum

from nether_earth.capture import capture_footprint, effective_owner
from nether_earth.commander import CommanderMode
from nether_earth.commands import Command
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.movement import RobotMoveRequest, validate_robot_move
from nether_earth.navigation import NavigationStatus, next_navigation_step
from nether_earth.reservations import destination_available
from nether_earth.robot import Robot
from nether_earth.robot_build import CANONICAL_WEAPON_ORDER, ModuleIdentity
from nether_earth.rules import DEFAULT_RULES, EngineRules, miles_to_cells
from nether_earth.state import GameState
from nether_earth.structures import Factory, WarBase

__all__ = [
    "MAX_ORDER_DISTANCE_MILES",
    "Advance",
    "EngagementIntent",
    "EngagementTargetKind",
    "Order",
    "OrderEvaluation",
    "OrderStatus",
    "Retreat",
    "RobotEngagementIntentEvent",
    "RobotOrderChangedEvent",
    "SearchCapture",
    "SearchCaptureTarget",
    "SearchDestroy",
    "SearchDestroyTarget",
    "SetRobotOrderCommand",
    "StopAndDefend",
    "apply_order_evaluations",
    "apply_set_robot_order",
    "engagement_intent_for",
    "evaluate_order",
    "evaluate_orders",
    "order_is_valid",
    "select_capture_target",
    "select_destroy_target",
    "walk_out_request",
]


#: The locked inclusive upper bound on an ``Advance``/``Retreat`` distance,
#: in miles (`_specs/functional-spec.md` §16: "Advance N -- move East 0-50
#: miles"). Combined with `rules.py`'s ``CELLS_PER_MILE`` this is the
#: locked "0-50 miles = 0-100 cells" range; the cell figure is deliberately
#: never written down separately, so the two can never disagree.
MAX_ORDER_DISTANCE_MILES = 50


# --------------------------------------------------------------------------
# Order catalog
# --------------------------------------------------------------------------


class SearchCaptureTarget(str, Enum):
    """Which structures a ``Search & Capture`` order hunts.

    The three locked choices from `_specs/functional-spec.md` §16 ("target
    neutral factories, enemy factories, or war bases"). ``str``-valued for
    the same reason every other engine enum is: the value is a stable
    serializable token for snapshots/replays, not a display string.

    Note that these describe the *ownership relation to the ordering
    player*, not a static map property: ``ENEMY_FACTORY`` means "a factory
    currently owned by somebody other than me", which is re-resolved against
    live ownership (`capture.py`'s :func:`~nether_earth.capture.effective_owner`)
    on every evaluation rather than frozen when the order was issued.
    """

    NEUTRAL_FACTORY = "neutral_factory"
    ENEMY_FACTORY = "enemy_factory"
    ENEMY_WAR_BASE = "enemy_war_base"


class SearchDestroyTarget(str, Enum):
    """Which entities a ``Search & Destroy`` order hunts.

    The three locked choices from `_specs/functional-spec.md` §16 ("target
    robots, factories, or war bases"). Only hostile entities are ever
    selected; see :func:`select_destroy_target`.
    """

    ROBOT = "robot"
    FACTORY = "factory"
    WAR_BASE = "war_base"


@dataclass(frozen=True, slots=True)
class StopAndDefend:
    """Hold position and engage valid enemies (`_specs/functional-spec.md` §16).

    The terminal order and the universal fallback: every other order
    eventually becomes this one, and an invalid/impossible order becomes it
    immediately. It has no fields, so two ``StopAndDefend`` values compare
    equal and a single shared instance would do -- but it is kept a
    dataclass rather than a sentinel so the :data:`Order` union is uniform
    and ``dataclasses.replace``/pattern matching work across all five
    members.

    It produces no movement goal ever (that is the "stop" half) and an
    :class:`EngagementIntent` against the nearest hostile *robot* whenever
    one exists (the "defend" half). Structures are excluded: a robot holding
    ground is defending against things that can move on it, and letting a
    passive defensive posture decide to nuke a war base would be inventing
    aggression the spec does not grant it -- that is what ``SearchDestroy``
    is for.
    """


@dataclass(frozen=True, slots=True)
class Advance:
    """Move East ``distance_miles`` miles, then Stop & Defend.

    ``distance_miles`` is the player-supplied 0-50 value and never changes.
    ``target_x`` is the *bound* absolute goal column, ``None`` until the
    first evaluation binds it (the ``PENDING`` -> ``ACTIVE`` transition
    described in the module docstring) -- it must be ``None`` at assignment
    time, because a caller pre-binding it would be choosing a goal the
    0-50-mile rule never validated.

    East is ``+x``. The goal is ``(target_x, robot.y)``: an advance is a
    column goal, not a point goal, so a robot detouring north around an
    obstacle still completes at the same column rather than being dragged
    back to its original row.
    """

    distance_miles: int
    target_x: int | None = None


@dataclass(frozen=True, slots=True)
class Retreat:
    """Move West ``distance_miles`` miles, then Stop & Defend.

    The exact mirror of :class:`Advance` (see its docstring for
    ``target_x``), with West as ``-x``. The westward sign is applied here,
    to the magnitude :func:`~nether_earth.rules.miles_to_cells` returns --
    that helper rejects negative input precisely so direction stays the
    caller's concern and a "negative distance" can never masquerade as a
    direction.
    """

    distance_miles: int
    target_x: int | None = None


@dataclass(frozen=True, slots=True)
class SearchCapture:
    """Seek out and stand on the capture footprint of a ``target`` structure.

    Completion is *arrival on the footprint*, not the capture itself:
    `capture.py` owns capture progress, duration, interruption, and
    ownership transfer, and it triggers purely on a qualifying robot's
    authoritative position. This order's whole job is to deliver the robot
    to a cell :func:`~nether_earth.capture.capture_footprint` reports -- the
    very same function `capture.py`'s :func:`~nether_earth.capture.advance_capture`
    reads -- and then to get out of the way by becoming
    :class:`StopAndDefend`, which holds the robot exactly where it stands
    and so satisfies the locked "qualifying occupation must be continuous"
    rule for free.
    """

    target: SearchCaptureTarget


@dataclass(frozen=True, slots=True)
class SearchDestroy:
    """Seek out a hostile ``target`` and produce engagement intent against it.

    Against robots, movement closes on the target and
    :class:`EngagementIntent` is produced every tick a valid target is
    selected and the robot carries a capable weapon; Milestone 6 decides
    whether the reported ``distance_cells`` is within the chosen weapon's
    range and resolves the firing. Such an order has no completion state: it
    re-selects a new target as targets are destroyed until no candidate
    remains at all (fallback to :class:`StopAndDefend`).

    Against a factory/war base (nuclear carriers only), the robot navigates
    to the structure's target cell -- the cell :func:`select_capture_target`
    would choose -- and produces no intent until it stands there. On that
    tick the order completes with a structure intent whose only effect is
    the nuclear detonation (`_specs/open-questions.md` §19).
    """

    target: SearchDestroyTarget


#: The five locked order types (`_specs/technical-spec.md` §15). A union
#: rather than a base class with subclasses: orders are pure immutable
#: values with no shared behavior, every consumer dispatches on the concrete
#: type, and a union makes an unhandled member a static type error rather
#: than a silently inherited no-op.
Order = StopAndDefend | Advance | Retreat | SearchCapture | SearchDestroy


def order_is_valid(order: object) -> bool:
    """Return whether ``order`` is a structurally valid order.

    Structural validity only -- whether the order is *achievable in the
    current world* is a separate, per-tick question answered by
    :func:`evaluate_order` (see the module docstring's "what counts as
    impossible"). An ``Advance`` to a column past the map edge is valid
    here and may still fall back later.

    An unrecognized object is invalid rather than an error: orders arrive
    from player input, and `_specs/functional-spec.md` §16's locked response
    to a bad order is a deterministic fallback, never an exception.
    """
    if isinstance(order, StopAndDefend):
        return True
    if isinstance(order, (Advance, Retreat)):
        # bool is a subclass of int and would silently pass as 0/1 miles.
        if isinstance(order.distance_miles, bool) or not isinstance(order.distance_miles, int):
            return False
        return 0 <= order.distance_miles <= MAX_ORDER_DISTANCE_MILES
    if isinstance(order, SearchCapture):
        return isinstance(order.target, SearchCaptureTarget)
    if isinstance(order, SearchDestroy):
        return isinstance(order.target, SearchDestroyTarget)
    return False


# --------------------------------------------------------------------------
# Order assignment command
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SetRobotOrderCommand(Command):
    """Assign ``order`` to one of the issuing player's own robots.

    Unlike `direct_control.py`'s
    :class:`~nether_earth.direct_control.DirectRobotMoveCommand` this *does*
    name an ``entity_id``: direct control drives exactly the one robot a
    commander is docked to, but orders are the mechanism by which a player
    directs robots they are *not* riding, so naming the robot is the whole
    point rather than a control-bypass vector. Ownership is still enforced
    (:func:`apply_set_robot_order` ignores a command naming a robot the
    issuing player does not own), so a player can only ever order their own
    units.

    No docking precondition is imposed here even though
    `_specs/functional-spec.md` §15 lists the orders menu among the
    post-docking options: that is a *UI availability* statement about where
    the menu lives, and `docking.py` already gates it at the presentation
    boundary. Re-deriving it as an engine rule would additionally make it
    impossible to keep a robot under orders after undocking, which §16
    plainly intends (an ordered robot acts autonomously while its commander
    flies elsewhere).

    A structurally invalid ``order`` is not rejected: per §16 it is stored
    as :class:`StopAndDefend`, so the fallback is visible in authoritative
    state rather than swallowed as a dropped command.
    """

    entity_id: EntityId
    order: Order


@dataclass(frozen=True, slots=True)
class RobotOrderChangedEvent(Event):
    """Emitted whenever a robot's authoritative order changes.

    Covers every transition the module docstring lists -- assignment,
    ``PENDING`` -> ``ACTIVE`` goal binding, completion, and fallback -- so a
    replay can reconstruct the order lifecycle without diffing snapshots.
    ``previous`` is ``None`` for a robot that had no order at all.

    ``status`` says *why* the order changed, which is the part a client
    cannot infer: ``Advance`` -> ``StopAndDefend`` looks identical whether
    the robot arrived (:attr:`OrderStatus.COMPLETED`) or the goal turned out
    to be impossible (:attr:`OrderStatus.FALLBACK`).
    """

    entity_id: EntityId
    previous: Order | None
    order: Order
    status: OrderStatus
    tick: int


def apply_set_robot_order(
    command: SetRobotOrderCommand,
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, RobotOrderChangedEvent | None]:
    """Apply ``command``, returning the new state and the change event, if any.

    Returns ``state`` unchanged with ``None`` when the named robot does not
    exist or is not owned by the issuing player -- a gameplay-level
    rejection, which by `engine.py`'s established convention produces no
    additional event beyond the generic ``CommandAccepted`` that already
    fired for the structurally valid command.

    A structurally invalid order is stored as :class:`StopAndDefend` with
    :attr:`OrderStatus.FALLBACK` (see :class:`SetRobotOrderCommand`).
    Assigning a robot the order it already holds is still an assignment and
    still emits an event: re-issuing ``Advance(10)`` to a robot mid-advance
    legitimately means "restart, from here", and silently dropping it would
    make the command's effect depend on hidden bound state.

    Any assignment ends a launch walk-out (``exit_steps_remaining``, see
    :func:`walk_out_request`): on the Spectrum a player gives orders from the
    robot's menu, and leaving it zeroes the robot's steps-to-keep-walking
    (``ld (ix + ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING), a`` with
    ``a = 0``), so the new order takes over at once.
    """
    robot = state.robot_for(command.entity_id)
    if robot is None or robot.owner != command.player:
        return state, None

    if order_is_valid(command.order):
        order = _unbound(command.order)
        status = OrderStatus.PENDING if isinstance(order, (Advance, Retreat)) else OrderStatus.ACTIVE
    else:
        order = StopAndDefend()
        status = OrderStatus.FALLBACK

    updated = replace(robot.with_order(order), exit_steps_remaining=0)
    new_state = state.with_robots(
        tuple(updated if other.entity_id == robot.entity_id else other for other in state.robots)
    )
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    return new_state, RobotOrderChangedEvent(
        sequence=sequence,
        entity_id=robot.entity_id,
        previous=robot.order,
        order=order,
        status=status,
        tick=tick,
    )


def _unbound(order: Order) -> Order:
    """Return ``order`` with any pre-bound absolute goal cleared.

    A newly assigned ``Advance``/``Retreat`` must always start ``PENDING``
    so its goal column is measured from where the robot stands *now*. A
    caller that constructed ``Advance(10, target_x=3)`` by hand (or replayed
    a serialized in-flight order into the command stream) would otherwise
    smuggle in a goal the 0-50-mile validation never saw.
    """
    if isinstance(order, (Advance, Retreat)):
        return replace(order, target_x=None)
    return order


# --------------------------------------------------------------------------
# Engagement intent
# --------------------------------------------------------------------------


class EngagementTargetKind(str, Enum):
    """What kind of entity an :class:`EngagementIntent` points at."""

    ROBOT = "robot"
    FACTORY = "factory"
    WAR_BASE = "war_base"


#: Weapons structurally capable against a mobile robot: all of them. Derived
#: from `robot_build.py`'s :data:`~nether_earth.robot_build.CANONICAL_WEAPON_ORDER`
#: rather than re-listed, so a future weapon added to the catalog is covered
#: here automatically instead of being silently omitted.
_ROBOT_CAPABLE_WEAPONS: tuple[ModuleIdentity, ...] = CANONICAL_WEAPON_ORDER

#: Weapons structurally capable against a factory or war base. Issue #64's
#: scope note locks this: "Factory/war-base destruction targets requiring a
#: nuke should only produce valid engagement intent when the robot has
#: suitable capability". Milestone 6 owns detonation itself.
_STRUCTURE_CAPABLE_WEAPONS: tuple[ModuleIdentity, ...] = (ModuleIdentity.NUCLEAR,)


@dataclass(frozen=True, slots=True)
class EngagementIntent:
    """One robot's deterministic intent to engage one hostile entity.

    The stable Milestone 6 contract this milestone is required to expose
    (`_specs/milestones/05-orders-navigation-capture.md`: "Combat-capable
    orders expose a stable engagement-intent contract for Milestone 6").
    It is a *statement*, not an action: producing one fires nothing, spends
    nothing, and mutates nothing.

    ``distance_cells`` is Manhattan distance from the robot's authoritative
    cell to ``target_x``/``target_y`` -- reported rather than thresholded,
    because weapon ranges are an M6 concern and are not locked yet (see the
    module docstring). ``weapons`` lists the robot's own fitted weapons that
    are structurally capable against ``target_kind``, in
    `robot_build.py`'s canonical order, and is never empty: an intent with
    no capable weapon is not produced at all.
    """

    robot_id: EntityId
    player: PlayerId
    target_kind: EngagementTargetKind
    target_id: EntityId
    target_x: int
    target_y: int
    distance_cells: int
    weapons: tuple[ModuleIdentity, ...]

    def __post_init__(self) -> None:
        if not self.weapons:
            raise ValueError("an EngagementIntent must name at least one capable weapon")
        if self.distance_cells < 0:
            raise ValueError("distance_cells must be non-negative")


@dataclass(frozen=True, slots=True)
class RobotEngagementIntentEvent(Event):
    """Carries one tick's :class:`EngagementIntent` into the event stream.

    Emitted every tick an intent exists, not only when it changes. Change
    detection would require storing the previous intent on the robot, and a
    *stale* intent is exactly the failure mode the module docstring's
    stateless target selection exists to prevent -- an intent is only ever
    true of the tick that computed it. Consumers wanting the values without
    the event stream call :func:`engagement_intent_for` directly, which is
    what Milestone 6 is expected to do.
    """

    intent: EngagementIntent
    tick: int


def _capable_weapons(
    robot: Robot, target_kind: EngagementTargetKind
) -> tuple[ModuleIdentity, ...]:
    """Return ``robot``'s fitted weapons capable against ``target_kind``.

    Canonical order is inherited from ``robot.build.weapons``, which
    `robot_build.py` already normalizes, so no re-sort is needed and no
    second weapon ordering exists to drift.
    """
    capable = (
        _ROBOT_CAPABLE_WEAPONS
        if target_kind is EngagementTargetKind.ROBOT
        else _STRUCTURE_CAPABLE_WEAPONS
    )
    return tuple(weapon for weapon in robot.build.weapons if weapon in capable)


def engagement_intent_for(
    robot: Robot,
    target_kind: EngagementTargetKind,
    target_id: EntityId,
    target_x: int,
    target_y: int,
) -> EngagementIntent | None:
    """Build ``robot``'s intent against one target, or ``None`` if incapable.

    The single construction point for :class:`EngagementIntent`: every order
    that engages goes through here, so "does this robot have a weapon that
    can hurt that" is answered in exactly one place for both
    :class:`StopAndDefend` and :class:`SearchDestroy`.
    """
    weapons = _capable_weapons(robot, target_kind)
    if not weapons:
        return None
    return EngagementIntent(
        robot_id=robot.entity_id,
        player=robot.owner,
        target_kind=target_kind,
        target_id=target_id,
        target_x=target_x,
        target_y=target_y,
        distance_cells=_manhattan(robot.x, robot.y, target_x, target_y),
        weapons=weapons,
    )


# --------------------------------------------------------------------------
# Deterministic target selection
# --------------------------------------------------------------------------


def _manhattan(ax: int, ay: int, bx: int, by: int) -> int:
    """Return grid distance between two cells.

    Manhattan rather than Euclidean because movement is 4-directional
    (`movement.py` rejects diagonals): the number of cardinal steps between
    two cells *is* this value, so it is both the honest distance metric and
    an integer, keeping gameplay state off floating point per `AGENTS.md`.
    """
    return abs(ax - bx) + abs(ay - by)


def _nearest_cell(
    robot: Robot, cells: Iterable[tuple[int, int]]
) -> tuple[int, int] | None:
    """Return the cell of ``cells`` nearest ``robot``, or ``None`` if empty.

    Ties break on ``(x, y)``, so the result never depends on the iteration
    order of the ``frozenset`` footprints this is usually called with.
    """
    ordered = sorted(cells)
    if not ordered:
        return None
    return min(ordered, key=lambda cell: (_manhattan(robot.x, robot.y, cell[0], cell[1]), cell))


@dataclass(frozen=True, slots=True)
class _StructureCandidate:
    """One structure a search order could pursue, with its resolved geometry."""

    structure_id: EntityId
    #: Where the robot must get to: the structure's capture footprint, for
    #: both capture and structure destroy (`_specs/open-questions.md` §19).
    goal_cells: frozenset[tuple[int, int]]


def _structures_of_kind(
    world: WorldMap, kind: EngagementTargetKind
) -> tuple[WarBase | Factory, ...]:
    """Return the world's factories or war bases in canonical id order."""
    structures: tuple[WarBase | Factory, ...]
    if kind is EngagementTargetKind.FACTORY:
        structures = world.factories
    elif kind is EngagementTargetKind.WAR_BASE:
        structures = world.war_bases
    else:  # pragma: no cover - guarded by callers' own enum dispatch
        raise ValueError(f"{kind!r} is not a structure kind")
    return tuple(sorted(structures, key=lambda structure: structure.id.value))


def select_capture_target(
    robot: Robot,
    target: SearchCaptureTarget,
    state: GameState,
    world: WorldMap,
) -> tuple[EntityId, tuple[int, int]] | None:
    """Return the ``(structure_id, goal_cell)`` a Search & Capture should pursue.

    Candidates are every structure of the requested kind whose *live*
    ownership (`capture.py`'s :func:`~nether_earth.capture.effective_owner`,
    so this tick's captures are already visible) matches the requested
    relation to ``robot.owner``, and that declares at least one capture
    interaction point on this map. A structure the robot already owns is
    never a candidate, which is what makes the order self-terminating rather
    than looping back onto its own prize.

    Selection is by ``(distance to nearest footprint cell, structure id)``
    and the returned goal cell is that nearest footprint cell -- the exact
    cells :func:`~nether_earth.capture.advance_capture` counts as qualifying
    occupation, read from the one shared
    :func:`~nether_earth.capture.capture_footprint` helper so the order can
    never walk a robot to a cell that does not start a capture.

    Returns ``None`` when no candidate exists, which the caller turns into
    the locked Stop & Defend fallback.
    """
    if target is SearchCaptureTarget.ENEMY_WAR_BASE:
        structure_kind = EngagementTargetKind.WAR_BASE
        interaction_kind = InteractionKind.WARBASE_CAPTURE
    else:
        structure_kind = EngagementTargetKind.FACTORY
        interaction_kind = InteractionKind.FACTORY_CAPTURE

    candidates: list[_StructureCandidate] = []
    for structure in _structures_of_kind(world, structure_kind):
        owner = effective_owner(world, state, structure)
        if target is SearchCaptureTarget.NEUTRAL_FACTORY:
            if owner is not None:
                continue
        elif owner is None or owner == robot.owner:
            # ENEMY_FACTORY / ENEMY_WAR_BASE: a neutral structure is not an
            # enemy one, and a robot never captures its own side's holding.
            continue
        footprint = capture_footprint(world, structure.id, interaction_kind)
        if not footprint:
            # Not capturable on this map (no declared capture points); a
            # valid map state, never an error -- see capture.py.
            continue
        candidates.append(
            _StructureCandidate(structure_id=structure.id, goal_cells=footprint)
        )

    return _closest_candidate(robot, candidates)


def select_destroy_target(
    robot: Robot,
    target: SearchDestroyTarget,
    state: GameState,
    world: WorldMap,
) -> tuple[EntityId, tuple[int, int]] | None:
    """Return the ``(target_id, goal_cell)`` a Search & Destroy should pursue.

    For :attr:`SearchDestroyTarget.ROBOT` the candidates are every robot
    with a different ``owner``, in canonical ``entity_id`` order, and the
    goal cell is the target robot's own authoritative cell -- the robot
    closes on it; `movement.py` refuses the final overlapping step, which is
    correct and needs no special case here.

    For the structure kinds the candidates are every factory/war base not
    owned by ``robot.owner`` (a *neutral* structure is a valid destruction
    target, unlike in :func:`select_capture_target`: nothing about blowing
    something up requires it to belong to an enemy first), and the goal cell
    is the nearest cell of the structure's capture footprint -- the same
    target cell a Search & Capture navigates to, where the Spectrum code
    detonates (`_specs/open-questions.md` §19). A structure with no declared
    capture point on this map has no target cell and is not a candidate.

    Selection is by ``(distance, id)`` exactly as in
    :func:`select_capture_target`. Returns ``None`` when nothing hostile
    exists.
    """
    if target is SearchDestroyTarget.ROBOT:
        hostile = [other for other in state.robots if other.owner != robot.owner]
        if not hostile:
            return None
        chosen = min(
            hostile,
            key=lambda other: (
                _manhattan(robot.x, robot.y, other.x, other.y),
                other.entity_id.value,
            ),
        )
        return chosen.entity_id, (chosen.x, chosen.y)

    structure_kind = (
        EngagementTargetKind.FACTORY
        if target is SearchDestroyTarget.FACTORY
        else EngagementTargetKind.WAR_BASE
    )
    interaction_kind = (
        InteractionKind.FACTORY_CAPTURE
        if target is SearchDestroyTarget.FACTORY
        else InteractionKind.WARBASE_CAPTURE
    )
    candidates = [
        _StructureCandidate(
            structure_id=structure.id,
            goal_cells=capture_footprint(world, structure.id, interaction_kind),
        )
        for structure in _structures_of_kind(world, structure_kind)
        if effective_owner(world, state, structure) != robot.owner
    ]
    return _closest_candidate(robot, candidates)


def _closest_candidate(
    robot: Robot, candidates: list[_StructureCandidate]
) -> tuple[EntityId, tuple[int, int]] | None:
    """Return the nearest candidate's ``(id, goal cell)``, ties broken by id."""
    best: tuple[int, str, EntityId, tuple[int, int]] | None = None
    for candidate in candidates:
        cell = _nearest_cell(robot, candidate.goal_cells)
        if cell is None:
            continue
        key = (
            _manhattan(robot.x, robot.y, cell[0], cell[1]),
            candidate.structure_id.value,
            candidate.structure_id,
            cell,
        )
        if best is None or key[:2] < best[:2]:
            best = key
    if best is None:
        return None
    return best[2], best[3]


# --------------------------------------------------------------------------
# Order evaluation
# --------------------------------------------------------------------------


class OrderStatus(str, Enum):
    """The lifecycle phase one order evaluation concluded.

    See the module docstring for the full lifecycle. These are part of the
    public contract: they appear on :class:`RobotOrderChangedEvent` and let
    a consumer distinguish "arrived" from "gave up", which the resulting
    :class:`StopAndDefend` alone cannot express.
    """

    #: Assigned but its absolute goal is not bound yet (``Advance``/``Retreat``
    #: only). Never observed as an *evaluation* outcome -- binding happens in
    #: the same evaluation -- only as an assignment outcome.
    PENDING = "pending"
    #: Producing goals/intent; the order is unchanged or was just bound.
    ACTIVE = "active"
    #: The goal was reached; the order became :class:`StopAndDefend`.
    COMPLETED = "completed"
    #: The order was invalid or impossible; it became :class:`StopAndDefend`.
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class OrderEvaluation:
    """Everything one robot's order concluded for one tick.

    ``order`` is the order the robot should now hold -- the same object when
    nothing changed, a goal-bound ``Advance``/``Retreat`` on the first
    evaluation, or :class:`StopAndDefend` on completion/fallback. Callers
    write it back to the robot; :attr:`changed` says whether that write is
    observable.

    ``request`` is a :class:`~nether_earth.movement.RobotMoveRequest` a
    navigation policy has *already validated* against the state this
    evaluation read, exactly as
    :class:`~nether_earth.navigation.NavigationDecision` documents. It must
    be executed through
    :func:`~nether_earth.reservations.apply_robot_move_batch` together with
    every other request of the tick, never applied directly -- applying them
    one at a time would let submission order decide contention, which
    `_specs/open-questions.md` §11 forbids.

    ``intent`` is this tick's engagement intent, if any. It is deliberately
    independent of ``request``: a robot may close on a target and intend to
    engage it in the same tick.
    """

    robot_id: EntityId
    order: Order
    previous: Order | None
    status: OrderStatus
    request: RobotMoveRequest | None = None
    intent: EngagementIntent | None = None

    @property
    def changed(self) -> bool:
        """Whether the robot's stored order must be updated."""
        return self.order != self.previous


def _fallback(robot: Robot) -> OrderEvaluation:
    """Return the locked Stop & Defend fallback evaluation for ``robot``.

    Produces no movement request and no intent *this* tick: the robot has
    just changed orders, and the fresh :class:`StopAndDefend` gets its
    defensive intent on the next evaluation. That keeps one evaluation to
    one order rather than chaining two, which would make the number of
    lifecycle transitions per tick unbounded.
    """
    return OrderEvaluation(
        robot_id=robot.entity_id,
        order=StopAndDefend(),
        previous=robot.order,
        status=OrderStatus.FALLBACK,
    )


def _completed(robot: Robot) -> OrderEvaluation:
    """Return the completion evaluation for ``robot`` (order -> Stop & Defend)."""
    return OrderEvaluation(
        robot_id=robot.entity_id,
        order=StopAndDefend(),
        previous=robot.order,
        status=OrderStatus.COMPLETED,
    )


def _navigate(
    robot: Robot,
    goal: tuple[int, int],
    order: Order,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
    intent: EngagementIntent | None = None,
) -> OrderEvaluation | None:
    """Ask the robot's own navigation policy for one step toward ``goal``.

    Returns ``None`` when the policy *proved* the goal unreachable
    (:attr:`~nether_earth.navigation.NavigationStatus.UNREACHABLE`), which
    the caller turns into the locked fallback; every other status keeps the
    order ``ACTIVE``:

    - ``STEP`` carries the validated request through;
    - ``BLOCKED`` is the locked "may get stuck" outcome and simply retries
      next tick (see the module docstring);
    - ``MOVE_IN_PROGRESS`` means a move is already in flight, so there is
      nothing to submit;
    - ``ARRIVED`` cannot reach here (callers test arrival themselves against
      their own completion rule, which is not always "standing on the goal
      cell"), but is handled as a no-op step for total coverage.
    """
    decision = next_navigation_step(robot, goal[0], goal[1], state, world, rules)
    if decision.status is NavigationStatus.UNREACHABLE:
        return None
    return OrderEvaluation(
        robot_id=robot.entity_id,
        order=order,
        previous=robot.order,
        status=OrderStatus.ACTIVE,
        request=decision.request if decision.status is NavigationStatus.STEP else None,
        intent=intent,
    )


def _defensive_intent(robot: Robot, state: GameState) -> EngagementIntent | None:
    """Return ``robot``'s Stop & Defend intent against the nearest hostile robot."""
    hostile = [other for other in state.robots if other.owner != robot.owner]
    if not hostile:
        return None
    chosen = min(
        hostile,
        key=lambda other: (
            _manhattan(robot.x, robot.y, other.x, other.y),
            other.entity_id.value,
        ),
    )
    return engagement_intent_for(
        robot, EngagementTargetKind.ROBOT, chosen.entity_id, chosen.x, chosen.y
    )


def _linear_goal(
    robot: Robot, order: Advance | Retreat, world: WorldMap
) -> tuple[Order, int] | None:
    """Bind (or re-read) an ``Advance``/``Retreat`` goal column.

    Returns the possibly-rebound order together with its absolute goal
    column, or ``None`` when the order is impossible.

    Binding clamps the goal to the map, because "advance 50 miles" from ten
    cells short of the eastern edge plainly means "advance to the edge", not
    "fail". It is impossible only when the clamp leaves nothing to do while
    a nonzero distance was asked for -- i.e. the robot already sits at the
    edge it was told to head for. A *zero*-mile order is not impossible; it
    is instantly complete, which the caller detects from the bound goal
    equalling the robot's own column.
    """
    sign = 1 if isinstance(order, Advance) else -1
    if order.target_x is None:
        raw = robot.x + sign * miles_to_cells(order.distance_miles)
        # The last on-map anchor column of a 2×2 body is width - 2 (CR002.3).
        target_x = max(0, min(world.width - 2, raw))
        if target_x != robot.x:
            return replace(order, target_x=target_x), target_x
        # Clamped to a standstill: impossible unless nothing was asked for.
        if order.distance_miles == 0:
            return replace(order, target_x=target_x), target_x
        return None
    return order, order.target_x


def walk_out_request(
    robot: Robot, state: GameState, world: WorldMap, rules: EngineRules = DEFAULT_RULES
) -> RobotMoveRequest | None:
    """The next step of ``robot``'s launch walk-out, if it has one and it is legal now.

    A launched robot holds Stop & Defend with ``exit_steps_remaining`` set
    (`robot_launch.py`). The Spectrum does the same (``La6c8``, right after
    ``Lc849_robot_construction_if_possible``: desired direction down,
    5 steps to keep walking, Stop & Defend, facing down already), and
    ``Lb154_robot_ai_update`` then keeps walking down one step per robot
    update while the step is possible (``Lb1e9``). "Down" is ``inc b``
    (``Lb4d5``), i.e. ``y + 1``: south, out of the doorway (§18/§21).

    Returns ``None`` when no steps are left or the step south is not legal
    right now (``validate_robot_move`` with reservations bound). The walk-out
    then ends: on the Spectrum a blocked desired direction drops to
    ``Lb1f5_move_in_a_new_direction``, which for Stop & Defend picks no
    direction (``Lb222``), so the robot stays and defends. `engine.py`
    settles the counter after the tick's move batch
    (:func:`~nether_earth.autonomous_combat.settle_walk_outs`).
    """
    if robot.exit_steps_remaining <= 0 or robot.movement is not None:
        return None
    request = RobotMoveRequest(entity_id=robot.entity_id, dx=0, dy=1)
    result = validate_robot_move(request, state, world, rules, destination_available)
    return request if result.accepted else None


def evaluate_order(
    robot: Robot,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> OrderEvaluation | None:
    """Evaluate ``robot``'s order for one tick.

    Returns ``None`` when the robot holds no order at all -- there is
    nothing autonomous to decide. (A freshly launched robot holds Stop &
    Defend and walks out of its war base first, CR002.3; see
    :func:`walk_out_request`.) Otherwise returns exactly one
    :class:`OrderEvaluation`; see that class and the module docstring for
    how the caller must apply it.

    Pure: ``state`` is never mutated, no randomness is drawn, and no
    wall-clock time is read.
    """
    order = robot.order
    if order is None:
        return None
    if not order_is_valid(order):
        return _fallback(robot)

    if isinstance(order, StopAndDefend):
        return OrderEvaluation(
            robot_id=robot.entity_id,
            order=order,
            previous=order,
            status=OrderStatus.ACTIVE,
            request=walk_out_request(robot, state, world, rules),
            intent=_defensive_intent(robot, state),
        )

    if isinstance(order, (Advance, Retreat)):
        bound = _linear_goal(robot, order, world)
        if bound is None:
            return _fallback(robot)
        order, target_x = bound
        if robot.x == target_x:
            return _completed(robot)
        evaluation = _navigate(robot, (target_x, robot.y), order, state, world, rules)
        return evaluation if evaluation is not None else _fallback(robot)

    if isinstance(order, SearchCapture):
        selected = select_capture_target(robot, order.target, state, world)
        if selected is None:
            return _fallback(robot)
        _structure_id, goal = selected
        if (robot.x, robot.y) == goal:
            # Standing on the capture footprint: capture.py takes it from
            # here, and Stop & Defend holds the robot in place so the
            # occupation stays continuous.
            return _completed(robot)
        evaluation = _navigate(robot, goal, order, state, world, rules)
        return evaluation if evaluation is not None else _fallback(robot)

    # SearchDestroy
    target_kind = _DESTROY_TARGET_KINDS[order.target]
    if not _capable_weapons(robot, target_kind):
        # No weapon that can hurt this target kind -- notably a robot with
        # no nuclear module ordered to destroy a structure. However far it
        # walks it can never complete, so this is impossible, not blocked.
        return _fallback(robot)
    selected = select_destroy_target(robot, order.target, state, world)
    if selected is None:
        return _fallback(robot)
    target_id, goal = selected
    if target_kind is EngagementTargetKind.ROBOT:
        intent = engagement_intent_for(robot, target_kind, target_id, goal[0], goal[1])
        evaluation = _navigate(robot, goal, order, state, world, rules, intent=intent)
        return evaluation if evaluation is not None else _fallback(robot)
    if (robot.x, robot.y) == goal:
        # Arrived on the structure's target cell: the order completes, and its
        # completion effect is the nuclear detonation `autonomous_combat.py`
        # executes from this intent (`_specs/open-questions.md` §19). No
        # structure intent exists on any earlier tick.
        return replace(
            _completed(robot),
            intent=engagement_intent_for(robot, target_kind, target_id, goal[0], goal[1]),
        )
    evaluation = _navigate(robot, goal, order, state, world, rules)
    return evaluation if evaluation is not None else _fallback(robot)


#: The locked Search & Destroy target -> engagement target kind mapping.
#: A module-level table rather than an inline chain so the two enums are
#: related in exactly one place.
_DESTROY_TARGET_KINDS: dict[SearchDestroyTarget, EngagementTargetKind] = {
    SearchDestroyTarget.ROBOT: EngagementTargetKind.ROBOT,
    SearchDestroyTarget.FACTORY: EngagementTargetKind.FACTORY,
    SearchDestroyTarget.WAR_BASE: EngagementTargetKind.WAR_BASE,
}


def _under_direct_control(robot: Robot, state: GameState) -> bool:
    """Whether a commander is currently docked to (i.e. driving) ``robot``.

    A directly driven robot must not also be steered by its standing order:
    both would submit a move request for the same robot in the same batch,
    and `reservations.py` would reject the second as ``MOVE_IN_PROGRESS`` --
    the player would "win" only by accident of canonical ordering. The
    docked link *is* the control relationship (see `direct_control.py`), so
    no extra per-robot flag is introduced here. The order is retained, not
    cleared: it resumes the tick after the commander undocks.
    """
    for commander in state.commanders:
        if commander.mode is CommanderMode.DOCKED and commander.docked_robot_id == robot.entity_id:
            return True
    return False


def evaluate_orders(
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> tuple[OrderEvaluation, ...]:
    """Evaluate every ordered robot's order for one tick, in canonical order.

    Walks ``state.robots``, which :meth:`~nether_earth.state.GameState.with_robots`
    already keeps sorted by ``entity_id.value``, so the returned tuple's
    order -- and therefore the caller's event emission order and the order
    requests enter the move batch in -- is canonical without a re-sort.
    Robots with no order, and robots a commander is currently docked to, are
    skipped entirely (see :func:`_under_direct_control`).

    Every evaluation reads the same entry ``state``: this is a pure
    read-only pass whose results the caller applies afterwards, so no
    robot's evaluation can observe another's outcome within the tick. That
    is what makes autonomous behavior independent of evaluation order and
    therefore replay-identical.
    """
    evaluations: list[OrderEvaluation] = []
    for robot in state.robots:
        if robot.order is None or _under_direct_control(robot, state):
            continue
        evaluation = evaluate_order(robot, state, world, rules)
        if evaluation is not None:
            evaluations.append(evaluation)
    return tuple(evaluations)


def apply_order_evaluations(
    evaluations: Iterable[OrderEvaluation],
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Write back ``evaluations``' order changes and emit their events.

    Returns the caller's own ``state`` object unchanged when no order
    changed, so a tick in which every robot simply continues provably
    mutates nothing.

    Emits one :class:`RobotOrderChangedEvent` per changed order followed by
    one :class:`RobotEngagementIntentEvent` per intent, both walking
    ``evaluations`` in the canonical order :func:`evaluate_orders` produced.
    Order-change events precede intent events so a consumer reading the tick
    linearly sees the order a robot now holds before any intent attributed
    to it.

    Movement requests are deliberately *not* applied here: they belong in
    the tick's single :func:`~nether_earth.reservations.apply_robot_move_batch`
    call alongside direct-control requests (see :class:`OrderEvaluation`).
    """
    ordered = tuple(evaluations)
    events: list[Event] = []

    changes = {
        evaluation.robot_id: evaluation for evaluation in ordered if evaluation.changed
    }
    if changes:
        state = state.with_robots(
            tuple(
                robot.with_order(changes[robot.entity_id].order)
                if robot.entity_id in changes
                else robot
                for robot in state.robots
            )
        )

    for evaluation in ordered:
        if not evaluation.changed:
            continue
        events.append(
            RobotOrderChangedEvent(
                sequence=sequencer.next_sequence() if sequencer is not None else 0,
                entity_id=evaluation.robot_id,
                previous=evaluation.previous,
                order=evaluation.order,
                status=evaluation.status,
                tick=tick,
            )
        )

    for evaluation in ordered:
        if evaluation.intent is None:
            continue
        events.append(
            RobotEngagementIntentEvent(
                sequence=sequencer.next_sequence() if sequencer is not None else 0,
                intent=evaluation.intent,
                tick=tick,
            )
        )

    return state, tuple(events)
