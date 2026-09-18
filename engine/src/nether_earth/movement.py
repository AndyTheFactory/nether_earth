"""Shared robot movement executor and terrain legality (issue #60, M5.1).

This module is the single authoritative low-level robot movement path.
Every robot control source -- direct player control (M5.4) and autonomous
orders/navigation (M5.5/M5.7) alike -- must go through
:func:`validate_robot_move`/:func:`apply_robot_move` rather than deciding
movement legality itself, per `_specs/milestones/05-orders-navigation-capture.md`
("All robot movement uses one engine legality/execution path"). Nothing in
here knows *why* a robot wants to move; deciding that is the order/policy
layer's job.

What this module owns
----------------------
- chassis/terrain capability (:data:`CHASSIS_TERRAIN_PERMISSIONS`,
  :func:`chassis_can_enter`), locked by
  `_specs/functional-spec.md` / `_specs/open-questions.md` §4: bipod and
  tracks may enter ``NORMAL`` and ``ROUGH`` but never ``DITCH``; anti-grav
  may enter all three;
- the integer per-cell movement duration (:func:`move_duration_ticks`),
  read from centralized :class:`~nether_earth.rules.EngineRules` data --
  never a literal at a call site (see `rules.py`'s module docstring for
  the still-open `_specs/open-questions.md` §4 fidelity status of those
  defaults, owned by issue #61);
- the move-start / move-complete / rejection contract
  (:class:`RobotMoveRequest`, :class:`RobotMoveResult`,
  :class:`MovementRejectionReason`, the three events, and
  :func:`apply_robot_move` / :func:`advance_all_robot_transitions` /
  :func:`cancel_robot_move`).

What this module deliberately reuses rather than re-implements
---------------------------------------------------------------
- **occupancy (M2)**: destination occupancy is queried through
  `occupancy.py`'s :class:`~nether_earth.occupancy.OccupancyGrid`, built
  by :func:`folded_robot_occupancy` from ``WorldMap.occupancy()`` plus
  every live robot -- the same fold `robot_launch.py` already performs
  (that module now delegates to this one, so the fold exists once);
- **commander blocking (M3)**: queried through `collision.py`'s
  :func:`~nether_earth.collision.commander_blocks_cell`, the stable query
  that module's docstring explicitly reserved for M5 robot movement, using
  `collision.py`'s own ground-rooted
  :func:`~nether_earth.collision.robot_vertical_range` rather than
  re-deriving vertical-range overlap semantics here.

What this module deliberately does NOT own
-------------------------------------------
Pathfinding, order logic, and destination reservation/contention are later
M5 tasks (M5.3/M5.5/M5.7). They are not implemented here; instead this
module exposes the hooks they plug into:

- :data:`DestinationAvailabilityCheck` -- a duck-typed callable, defaulting
  to permissive, consulted as the *last* legality gate. M5.3's reservation
  manager binds its "is this cell reserved by someone else" query here (via
  ``functools.partial`` or a bound method), exactly as `collision.py`'s
  queries are bound into `commander_movement.py`'s checks today; a
  rejection surfaces as
  :attr:`MovementRejectionReason.DESTINATION_UNAVAILABLE`.
- :func:`apply_robot_move` is the single move-start point M5.3 hooks to
  take a reservation, and :func:`advance_robot_transition` /
  :func:`cancel_robot_move` are the single completion/cancellation points
  it hooks to release one. A rejected move never starts a transition, so
  there is never a reservation to leak.

Move shape: classic 4-directional, one cell per request (``dx``/``dy``
each in ``{-1, 0, 1}``, exactly one nonzero), mirroring
:class:`~nether_earth.commander_movement.CommanderMoveCommand`. Multi-cell
travel is the navigation layer issuing successive single-cell moves, not a
longer transition. Diagonals are rejected structurally (``ValueError`` in
``__post_init__``), not as a gameplay rejection.

Authoritative position stays discrete: a robot with an in-progress
:class:`~nether_earth.robot.RobotMoveTransition` is still authoritatively
at its origin cell until ``started_tick + duration_ticks``; only rendering
may interpolate. Terrain cost is charged for the cell being *entered*
(the destination), which is the only reading under which a single move's
duration is well defined without inventing a half-cell model.

Determinism: every function here is pure (state in, new state out, never
mutated in place), iterates only canonically ordered tuples
(``state.robots`` is sorted by ``entity_id.value``, ``state.commanders``
by ``player_id.value``), draws no randomness, and reads no wall-clock
time. A rejected move returns the caller's ``state`` object unchanged, so
a failed movement cannot partially mutate anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from nether_earth.collision import commander_blocks_cell, robot_vertical_range
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.occupancy import OccupancyGrid
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import CHASSIS_MODULES, ModuleIdentity
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState
from nether_earth.structures import Footprint
from nether_earth.terrain import TerrainType

__all__ = [
    "CHASSIS_TERRAIN_PERMISSIONS",
    "DestinationAvailabilityCheck",
    "MovementRejectionReason",
    "RobotMoveCancelledEvent",
    "RobotMoveCompletedEvent",
    "RobotMoveRequest",
    "RobotMoveResult",
    "RobotMoveStartedEvent",
    "advance_all_robot_transitions",
    "advance_robot_transition",
    "apply_robot_move",
    "cancel_robot_move",
    "chassis_can_enter",
    "folded_robot_occupancy",
    "move_duration_ticks",
    "robot_move_duration_ticks",
    "validate_robot_move",
]


# --------------------------------------------------------------------------
# Terrain capability (locked rules)
# --------------------------------------------------------------------------

#: The locked per-chassis terrain permissions
#: (`_specs/milestones/05-orders-navigation-capture.md` "Terrain
#: capability", `_specs/open-questions.md` §4): bipod and tracks traverse
#: ordinary and rough terrain but never a ditch/ravine; anti-grav traverses
#: every terrain type. This is rule *legality*, not tunable numeric
#: configuration, so it lives here (the one module that owns movement
#: legality) rather than in `rules.py`, which is deliberately a flat set of
#: tunable numeric scalars. Electronics never appears in this table:
#: `_specs/open-questions.md` §5 locks that electronics improves routing
#: intelligence only and never changes chassis terrain permissions.
CHASSIS_TERRAIN_PERMISSIONS: Mapping[ModuleIdentity, frozenset[TerrainType]] = MappingProxyType(
    {
        ModuleIdentity.BIPOD: frozenset({TerrainType.NORMAL, TerrainType.ROUGH}),
        ModuleIdentity.TRACKS: frozenset({TerrainType.NORMAL, TerrainType.ROUGH}),
        ModuleIdentity.ANTI_GRAV: frozenset(
            {TerrainType.NORMAL, TerrainType.ROUGH, TerrainType.DITCH}
        ),
    }
)


def _require_chassis(chassis: ModuleIdentity) -> None:
    """Raise ``ValueError`` unless ``chassis`` is a chassis module identity."""
    if chassis not in CHASSIS_MODULES:
        raise ValueError(
            f"{chassis.value!r} is not a chassis module: must be one of "
            f"{sorted(module.value for module in CHASSIS_MODULES)}"
        )


def chassis_can_enter(chassis: ModuleIdentity, terrain: TerrainType) -> bool:
    """Return whether ``chassis`` is physically able to enter ``terrain``.

    The single authoritative terrain-capability query (see
    :data:`CHASSIS_TERRAIN_PERMISSIONS`). Raises ``ValueError`` if
    ``chassis`` is not a chassis module identity -- an impossible build,
    not a movement rejection.
    """
    _require_chassis(chassis)
    return terrain in CHASSIS_TERRAIN_PERMISSIONS[chassis]


# --------------------------------------------------------------------------
# Movement duration (centralized configuration)
# --------------------------------------------------------------------------

_BASE_MOVE_TICKS: Mapping[ModuleIdentity, Callable[[EngineRules], int]] = MappingProxyType(
    {
        ModuleIdentity.BIPOD: lambda rules: rules.robot_move_ticks_bipod,
        ModuleIdentity.TRACKS: lambda rules: rules.robot_move_ticks_tracks,
        ModuleIdentity.ANTI_GRAV: lambda rules: rules.robot_move_ticks_anti_grav,
    }
)

_TERRAIN_MULTIPLIERS: Mapping[
    ModuleIdentity, Mapping[TerrainType, Callable[[EngineRules], int]]
] = MappingProxyType(
    {
        ModuleIdentity.BIPOD: MappingProxyType(
            {
                TerrainType.NORMAL: lambda rules: 1,
                TerrainType.ROUGH: lambda rules: rules.robot_rough_multiplier_bipod,
            }
        ),
        ModuleIdentity.TRACKS: MappingProxyType(
            {
                TerrainType.NORMAL: lambda rules: 1,
                TerrainType.ROUGH: lambda rules: rules.robot_rough_multiplier_tracks,
            }
        ),
        ModuleIdentity.ANTI_GRAV: MappingProxyType(
            {
                TerrainType.NORMAL: lambda rules: 1,
                TerrainType.ROUGH: lambda rules: rules.robot_rough_multiplier_anti_grav,
                TerrainType.DITCH: lambda rules: rules.robot_ditch_multiplier_anti_grav,
            }
        ),
    }
)


def move_duration_ticks(
    chassis: ModuleIdentity,
    terrain: TerrainType,
    rules: EngineRules = DEFAULT_RULES,
) -> int:
    """Return the integer tick duration of one ``chassis`` move into ``terrain``.

    The duration is ``chassis``' base ticks-per-cell times that chassis'
    multiplier for ``terrain`` (``1`` for ordinary terrain), with both
    halves read from ``rules`` -- see `rules.py` for the centralized
    fields and their documented, issue-#61-owned fidelity status. No
    movement call site may inline a tick literal instead of calling this.

    Raises ``ValueError`` if ``chassis`` cannot enter ``terrain`` at all
    (there is no meaningful duration for an impossible move; callers must
    gate on :func:`chassis_can_enter` / :func:`validate_robot_move`
    first).
    """
    if not chassis_can_enter(chassis, terrain):
        raise ValueError(
            f"{chassis.value!r} cannot enter {terrain.value!r} terrain: no movement duration"
        )
    base = _BASE_MOVE_TICKS[chassis](rules)
    multiplier = _TERRAIN_MULTIPLIERS[chassis][terrain](rules)
    return base * multiplier


def robot_move_duration_ticks(
    robot: Robot,
    terrain: TerrainType,
    rules: EngineRules = DEFAULT_RULES,
) -> int:
    """Return :func:`move_duration_ticks` for ``robot``'s own chassis.

    Convenience wrapper so callers holding a :class:`~nether_earth.robot.Robot`
    never reach into ``robot.build.chassis`` themselves to compute timing.
    """
    return move_duration_ticks(robot.build.chassis, terrain, rules)


# --------------------------------------------------------------------------
# Occupancy (M2 contract, shared fold)
# --------------------------------------------------------------------------


def folded_robot_occupancy(world: WorldMap, state: GameState) -> OccupancyGrid:
    """Return ``world``'s static occupancy grid with every live robot folded in.

    `occupancy.py`'s grid is not attached to ``GameState`` (see that
    module and `robot_launch.py`'s docstrings): ``WorldMap.occupancy()``
    indexes *static* structures only, so any system that needs "is this
    cell solid right now" folds current robots in via the existing
    :meth:`~nether_earth.occupancy.OccupancyGrid.with_added` dynamic-update
    API. This is that one shared fold -- `robot_launch.py` delegates here
    rather than keeping a second copy of it.

    Robots are folded in canonical ``state.robots`` order (sorted by
    ``entity_id.value``), so the fold never depends on incidental
    ordering. A robot with a move in progress occupies its *authoritative*
    (origin) cell only; its destination is claimed through M5.3's
    reservation contract, not through this grid.
    """
    grid = world.occupancy()
    for robot in state.robots:
        grid = grid.with_added(robot.entity_id, Footprint(cells=frozenset({(robot.x, robot.y)})))
    return grid


# --------------------------------------------------------------------------
# Reservation hook (M5.3 plugs in here)
# --------------------------------------------------------------------------

#: ``(state, robot, dest_x, dest_y) -> available``. See the module
#: docstring: M5.3's destination-reservation manager binds a query of this
#: shape so reservation logic lives in its own module while this module
#: stays the single legality gate. Returning ``False`` rejects the move
#: with :attr:`MovementRejectionReason.DESTINATION_UNAVAILABLE`.
DestinationAvailabilityCheck = Callable[[GameState, Robot, int, int], bool]


def _permissive_destination_check(
    state: GameState, robot: Robot, dest_x: int, dest_y: int
) -> bool:
    """Default :data:`DestinationAvailabilityCheck`: always available.

    Used until M5.3 lands, exactly as `commander_movement.py` defaults its
    own collision checks to permissive stubs until the real queries are
    bound in.
    """
    return True


# --------------------------------------------------------------------------
# Request / result / events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RobotMoveRequest:
    """A request to move one robot one cell in a cardinal direction.

    Deliberately *not* a :class:`~nether_earth.commands.Command`: a move
    request is the shared internal currency of this executor, issued both
    by a player command (M5.4) and by an autonomous order (M5.7). The
    command types that wrap it belong to those tasks' own modules, matching
    `commander_movement.py`'s separation of gameplay legality from the
    generic command contract.

    ``dx``/``dy`` are each restricted to ``{-1, 0, 1}`` with exactly one
    nonzero (see the module docstring); illegal shapes raise in
    ``__post_init__`` rather than becoming a rejection reason.
    """

    entity_id: EntityId
    dx: int
    dy: int

    def __post_init__(self) -> None:
        if self.dx not in (-1, 0, 1) or self.dy not in (-1, 0, 1):
            raise ValueError("dx and dy must each be in {-1, 0, 1}")
        if self.dx == 0 and self.dy == 0:
            raise ValueError("dx and dy cannot both be zero (not a move)")
        if self.dx != 0 and self.dy != 0:
            raise ValueError(
                "diagonal movement is not supported: exactly one of dx/dy must be nonzero"
            )


class MovementRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected robot move.

    A dedicated enum (rather than reusing
    :class:`nether_earth.commands.RejectionReason` or
    :class:`nether_earth.commander_movement.CommanderMovementRejectionReason`)
    because robot movement legality is its own gameplay concern layered on
    top of the generic command contract -- the same rationale
    `commander_movement.py` documents for its own reason enum. Higher-level
    control (M5.5/M5.7) branches on these codes to decide whether to retry,
    replan, or abandon an order, so they are part of this module's public
    contract.
    """

    NO_SUCH_ROBOT = "no_such_robot"
    MOVE_IN_PROGRESS = "move_in_progress"
    OUT_OF_BOUNDS = "out_of_bounds"
    TERRAIN_IMPASSABLE = "terrain_impassable"
    OCCUPIED = "occupied"
    COMMANDER_BLOCKED = "commander_blocked"
    DESTINATION_UNAVAILABLE = "destination_unavailable"


@dataclass(frozen=True, slots=True)
class RobotMoveResult:
    """Outcome of validating a :class:`RobotMoveRequest`.

    Mirrors :class:`nether_earth.commander_movement.CommanderMoveResult`'s
    accept/reject invariant: exactly one of "accepted" or "a stable
    rejection reason" holds.
    """

    request: RobotMoveRequest
    accepted: bool
    reason: MovementRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError("an accepted RobotMoveResult must not carry a rejection reason")
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected RobotMoveResult must carry a rejection reason")

    @classmethod
    def accept(cls, request: RobotMoveRequest) -> RobotMoveResult:
        """Build an accepted result for ``request``."""
        return cls(request=request, accepted=True, reason=None)

    @classmethod
    def reject(
        cls, request: RobotMoveRequest, reason: MovementRejectionReason
    ) -> RobotMoveResult:
        """Build a rejected result for ``request`` with a stable ``reason``."""
        return cls(request=request, accepted=False, reason=reason)


@dataclass(frozen=True, slots=True)
class RobotMoveStartedEvent(Event):
    """A robot began a cell-to-cell move; authoritative position is still
    ``(from_x, from_y)`` until ``started_tick + duration_ticks``."""

    entity_id: EntityId
    owner: PlayerId
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    started_tick: int
    duration_ticks: int


@dataclass(frozen=True, slots=True)
class RobotMoveCompletedEvent(Event):
    """A robot's in-progress move resolved; ``(x, y)`` is now authoritative."""

    entity_id: EntityId
    owner: PlayerId
    x: int
    y: int
    tick: int


@dataclass(frozen=True, slots=True)
class RobotMoveCancelledEvent(Event):
    """A robot's in-progress move was abandoned before resolving.

    The robot stays at its authoritative ``(x, y)`` (its origin cell) and
    the transition is cleared. Emitted by :func:`cancel_robot_move`, the
    single cancellation point M5.3 hooks to release the destination
    reservation.
    """

    entity_id: EntityId
    owner: PlayerId
    x: int
    y: int
    tick: int


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _in_bounds(world: WorldMap, x: int, y: int) -> bool:
    return 0 <= x < world.width and 0 <= y < world.height


def _commander_blocks(
    state: GameState, robot: Robot, x: int, y: int, rules: EngineRules
) -> bool:
    """Return whether any commander blocks ``robot`` from entering ``(x, y)``.

    Composes `collision.py`'s
    :func:`~nether_earth.collision.commander_blocks_cell` -- the query that
    module reserved for exactly this caller -- over ``state.commanders`` in
    canonical (player-id sorted) order, against the robot's own
    ground-rooted vertical range from
    :func:`~nether_earth.collision.robot_vertical_range`. No overlap math
    is re-derived here.

    ``rules`` is forwarded because a commander's blocking volume is
    ``[altitude, altitude + rules.commander_height)`` -- letting it default
    would silently evaluate a caller's non-default rule set against
    :data:`~nether_earth.rules.DEFAULT_RULES`. This mirrors
    :func:`~nether_earth.collision.commander_horizontal_move_allowed`,
    which forwards ``rules`` into the identical call.
    """
    vertical_range = robot_vertical_range(robot)
    return any(
        commander_blocks_cell(state, commander, x, y, vertical_range, rules=rules)
        for commander in state.commanders
    )


def validate_robot_move(
    request: RobotMoveRequest,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
    destination_check: DestinationAvailabilityCheck = _permissive_destination_check,
) -> RobotMoveResult:
    """Validate ``request`` against every robot-movement legality rule.

    Pure function: reads its arguments and returns a
    :class:`RobotMoveResult`, never mutating anything. Checks run in this
    fixed order, so the same illegal move always reports the same reason:

    1. the robot exists in ``state`` (:attr:`~MovementRejectionReason.NO_SUCH_ROBOT`);
    2. it has no move already in flight -- one move at a time, a second
       request is rejected rather than queued or overriding the in-flight
       destination, matching `commander_movement.py`
       (:attr:`~MovementRejectionReason.MOVE_IN_PROGRESS`);
    3. the destination is on the battlefield
       (:attr:`~MovementRejectionReason.OUT_OF_BOUNDS`);
    4. the robot's chassis can enter the destination's terrain
       (:attr:`~MovementRejectionReason.TERRAIN_IMPASSABLE`);
    5. the destination has no ground-solid occupant -- structure or robot
       -- per the M2 occupancy contract
       (:attr:`~MovementRejectionReason.OCCUPIED`);
    6. no commander blocks the destination at the robot's vertical range,
       per the M3 collision contract
       (:attr:`~MovementRejectionReason.COMMANDER_BLOCKED`);
    7. ``destination_check`` (M5.3's reservation hook, permissive by
       default) allows the destination
       (:attr:`~MovementRejectionReason.DESTINATION_UNAVAILABLE`).

    ``rules`` is forwarded to the commander-blocking check, whose blocking
    volume depends on ``rules.commander_height``; a caller validating
    against a non-default rule set therefore gets that rule set applied
    here too. Movement *duration* is not computed by this function -- it
    is derived at move-start time by :func:`apply_robot_move`.
    """
    robot = state.robot_for(request.entity_id)
    if robot is None:
        return RobotMoveResult.reject(request, MovementRejectionReason.NO_SUCH_ROBOT)
    if robot.movement is not None:
        return RobotMoveResult.reject(request, MovementRejectionReason.MOVE_IN_PROGRESS)

    dest_x = robot.x + request.dx
    dest_y = robot.y + request.dy
    if not _in_bounds(world, dest_x, dest_y):
        return RobotMoveResult.reject(request, MovementRejectionReason.OUT_OF_BOUNDS)

    if not chassis_can_enter(robot.build.chassis, world.terrain.terrain_at(dest_x, dest_y)):
        return RobotMoveResult.reject(request, MovementRejectionReason.TERRAIN_IMPASSABLE)

    if folded_robot_occupancy(world, state).is_occupied(dest_x, dest_y):
        return RobotMoveResult.reject(request, MovementRejectionReason.OCCUPIED)

    if _commander_blocks(state, robot, dest_x, dest_y, rules):
        return RobotMoveResult.reject(request, MovementRejectionReason.COMMANDER_BLOCKED)

    if not destination_check(state, robot, dest_x, dest_y):
        return RobotMoveResult.reject(request, MovementRejectionReason.DESTINATION_UNAVAILABLE)

    return RobotMoveResult.accept(request)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def _replace_robot(state: GameState, updated: Robot) -> GameState:
    """Return ``state`` with ``updated`` replacing the robot of the same id."""
    return state.with_robots(
        tuple(
            updated if robot.entity_id == updated.entity_id else robot
            for robot in state.robots
        )
    )


def apply_robot_move(
    request: RobotMoveRequest,
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    destination_check: DestinationAvailabilityCheck = _permissive_destination_check,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, RobotMoveResult, RobotMoveStartedEvent | None]:
    """Validate and, if legal, start ``request``'s move. The one move-start point.

    Returns ``(new_state, result, event)``. When rejected, ``new_state is
    state`` -- the caller's object, not a rebuilt copy -- and ``event is
    None``, so a failed move provably causes no partial state mutation.
    When accepted, the robot gains a
    :class:`~nether_earth.robot.RobotMoveTransition` whose
    ``duration_ticks`` comes from :func:`robot_move_duration_ticks` for the
    destination terrain; its authoritative ``x``/``y`` do NOT change yet
    (see :func:`advance_robot_transition` for completion).

    ``sequencer``, when supplied, assigns the started event's sequence
    number; omitting it yields sequence ``0``, which is only safe for
    isolated single-event tests -- callers orchestrating multiple events in
    one tick must supply a shared
    :class:`~nether_earth.events.EventSequencer`.
    """
    result = validate_robot_move(request, state, world, rules, destination_check)
    if not result.accepted:
        return state, result, None

    robot = state.robot_for(request.entity_id)
    assert robot is not None  # guaranteed by validate_robot_move's NO_SUCH_ROBOT check

    dest_x = robot.x + request.dx
    dest_y = robot.y + request.dy
    duration = robot_move_duration_ticks(robot, world.terrain.terrain_at(dest_x, dest_y), rules)
    transition = RobotMoveTransition(
        entity_id=robot.entity_id,
        from_x=robot.x,
        from_y=robot.y,
        to_x=dest_x,
        to_y=dest_y,
        started_tick=tick,
        duration_ticks=duration,
    )
    new_state = _replace_robot(state, robot.with_movement(transition))

    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = RobotMoveStartedEvent(
        sequence=sequence,
        entity_id=robot.entity_id,
        owner=robot.owner,
        from_x=transition.from_x,
        from_y=transition.from_y,
        to_x=transition.to_x,
        to_y=transition.to_y,
        started_tick=transition.started_tick,
        duration_ticks=transition.duration_ticks,
    )
    return new_state, result, event


def advance_robot_transition(
    robot: Robot,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[Robot, RobotMoveCompletedEvent | None]:
    """Complete ``robot``'s in-progress move if it is due by ``tick``.

    Returns ``(robot, None)`` unchanged when there is no transition or it
    has not yet elapsed; otherwise a new :class:`~nether_earth.robot.Robot`
    at the transition's destination with the transition cleared, plus the
    completion event. This is the one move-completion point (M5.3 releases
    the destination reservation here).
    """
    transition = robot.movement
    if transition is None or not transition.is_complete(tick):
        return robot, None
    updated = robot.with_position(transition.to_x, transition.to_y)
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = RobotMoveCompletedEvent(
        sequence=sequence,
        entity_id=updated.entity_id,
        owner=updated.owner,
        x=updated.x,
        y=updated.y,
        tick=tick,
    )
    return updated, event


def advance_all_robot_transitions(
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[RobotMoveCompletedEvent, ...]]:
    """Apply :func:`advance_robot_transition` to every robot in ``state``.

    Robots are processed in ``state.robots``' canonical order (sorted by
    ``entity_id.value``, per `state.py`), so both the resulting state and
    the event order are independent of any incidental construction/launch
    ordering.
    """
    events: list[RobotMoveCompletedEvent] = []
    updated_robots: list[Robot] = []
    for robot in state.robots:
        updated, event = advance_robot_transition(robot, tick, sequencer)
        updated_robots.append(updated)
        if event is not None:
            events.append(event)
    return state.with_robots(tuple(updated_robots)), tuple(events)


def cancel_robot_move(
    state: GameState,
    entity_id: EntityId,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, RobotMoveCancelledEvent | None]:
    """Abandon ``entity_id``'s in-progress move, leaving it at its origin cell.

    Returns ``(state, None)`` unchanged when there is no such robot or it
    has no move in flight, so cancelling twice is a safe no-op (no
    double-release for M5.3's reservation lifecycle to guard against).
    This is the one cancellation point; higher-level control (order
    changes, replanning) must route through it rather than clearing
    ``Robot.movement`` itself.
    """
    robot = state.robot_for(entity_id)
    if robot is None or robot.movement is None:
        return state, None
    updated = robot.with_movement(None)
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = RobotMoveCancelledEvent(
        sequence=sequence,
        entity_id=updated.entity_id,
        owner=updated.owner,
        x=updated.x,
        y=updated.y,
        tick=tick,
    )
    return _replace_robot(state, updated), event
