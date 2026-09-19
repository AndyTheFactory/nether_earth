"""Non-electronic and electronic robot navigation policies (issue #65, M5.6).

`_specs/open-questions.md` §5 (RESOLVED) locks the rules this module owns:

- non-electronic robots use deliberately limited/original-style *local*
  routing and may become blocked even when a longer valid route exists;
- electronic robots use proper deterministic pathfinding/replanning around
  obstacles;
- electronics improves routing intelligence only -- it never changes chassis
  terrain permissions;
- navigation strategies must be isolated behind an engine policy/interface.

This module is that interface (:class:`NavigationPolicy`) plus its two
locked implementations (:class:`NonElectronicNavigation`,
:class:`ElectronicNavigation`). It is the layer `movement.py`'s docstring
calls "the order/policy layer": it decides *why* a robot wants to move, and
`movement.py` remains the only thing that decides *whether* it may.

The non-electronic policy getting stuck is correct product behavior
----------------------------------------------------------------------
:class:`NonElectronicNavigation` is intentionally incapable of routing
around an obstacle that sits across its approach axis, even when a trivially
short detour exists. That is the locked gameplay difference electronics buys
a player; it is not a deficiency to be repaired, and it must not be replaced
with generic optimal pathfinding "for simplicity"
(`_specs/milestones/05-orders-navigation-capture.md`, issue #65's fidelity
note). Exact historical quirks of the original algorithm remain open
research detail -- the *qualitative* behavior locked above is what this
module implements, behind an interface that lets a future refinement swap
the local rule without touching any caller.

What this module deliberately reuses rather than re-implements
---------------------------------------------------------------
A navigation policy produces a *next step*, never a state mutation. Every
step it proposes is validated by, and must be executed through,
`movement.py`/`reservations.py`:

- **terrain legality**: `movement.py`'s
  :func:`~nether_earth.movement.chassis_can_enter` -- the single
  chassis/terrain table. Because both policies (and the route planner) ask
  exactly that function about the robot's *own* chassis, an electronic robot
  provably cannot be routed somewhere its chassis forbids: an electronic
  bipod still cannot enter a ditch.
- **occupancy**: `movement.py`'s
  :func:`~nether_earth.movement.folded_robot_occupancy` -- the same fold of
  static structures plus live robots the executor uses.
- **commander blocking**: `movement.py`'s
  :func:`~nether_earth.movement.commander_blocks_robot_cell`, which itself
  composes the M3 `collision.py` contract. No overlap math here.
- **reservations**: `reservations.py`'s
  :func:`~nether_earth.reservations.destination_available` (M5.3), so a cell
  another robot's in-flight move has claimed is a dynamic obstacle both
  policies see.
- **final legality**: a proposed step is only ever returned after
  :func:`~nether_earth.movement.validate_robot_move` accepts it, with the
  reservation gate bound. A policy therefore cannot smuggle a move past the
  executor, and the caller is expected to *execute* it via
  :func:`~nether_earth.reservations.apply_robot_move_batch` (never
  :func:`~nether_earth.movement.apply_robot_move` directly, which would
  resolve same-tick contention by submission order).

:func:`cell_is_enterable` is the one place those queries are composed into
"could this robot stand in this cell right now", and it is what the route
planner searches over -- so the planner and the executor can never disagree
about what is passable.

Stateless by design
--------------------
Neither policy stores a plan. :meth:`NavigationPolicy.next_step` recomputes
from the current :class:`~nether_earth.state.GameState` on every call, which
*is* the locked "replanning" requirement: an electronic robot whose route is
invalidated by a new blocker or reservation simply computes a different
route on the next tick, and a stale plan can never be followed because none
is ever retained. Policies are therefore field-less, frozen singletons
(:data:`NON_ELECTRONIC_NAVIGATION`, :data:`ELECTRONIC_NAVIGATION`).

Determinism
------------
No randomness is drawn here. `_specs/milestones/05-orders-navigation-capture.md`
permits "seeded RNG or stable ordering"; navigation uses stable ordering
throughout, which is strictly stronger (it needs no RNG stream, so it cannot
perturb `reservations.py`'s contention stream). Concretely: candidate
neighbours are always visited in the fixed canonical
:data:`CARDINAL_DIRECTIONS` order; the route planner breaks equal-cost ties
by insertion order in that canonical order; and no outcome depends on dict
or set iteration order (dicts are used only for membership and lookup, never
iterated). Contention *between* robots remains `reservations.py`'s seeded
concern, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from heapq import heappop, heappush
from typing import Protocol

from nether_earth.map import WorldMap
from nether_earth.movement import (
    RobotMoveRequest,
    chassis_can_enter,
    commander_blocks_robot_cell,
    folded_robot_occupancy,
    move_duration_ticks,
    validate_robot_move,
)
from nether_earth.occupancy import OccupancyGrid
from nether_earth.reservations import (
    ReservationTable,
    destination_available,
    reservations_from_state,
)
from nether_earth.robot import Robot
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "CARDINAL_DIRECTIONS",
    "ELECTRONIC_NAVIGATION",
    "NON_ELECTRONIC_NAVIGATION",
    "ElectronicNavigation",
    "NavigationDecision",
    "NavigationPolicy",
    "NavigationStatus",
    "NonElectronicNavigation",
    "cell_is_enterable",
    "navigation_policy_for",
    "next_navigation_step",
    "plan_route",
]


#: The four cardinal steps in one fixed canonical order, sorted by
#: ``(dx, dy)``: west, north, south, east. Every candidate-direction loop in
#: this module walks this tuple, so tie-breaking is a property of the module
#: rather than of any caller's or container's incidental ordering. Diagonals
#: are absent because `movement.py` rejects them structurally.
CARDINAL_DIRECTIONS: tuple[tuple[int, int], ...] = ((-1, 0), (0, -1), (0, 1), (1, 0))


# --------------------------------------------------------------------------
# Decision contract
# --------------------------------------------------------------------------


class NavigationStatus(str, Enum):
    """Stable, serializable outcome codes for one navigation query.

    A dedicated enum rather than a reuse of
    :class:`~nether_earth.movement.MovementRejectionReason`: that enum says
    why a *specific move* was illegal, while these say what the *robot's
    navigation* concluded. Autonomous orders (M5.7, issue #64) branch on
    these to decide whether to keep going, wait, or abandon an order, so
    they are part of this module's public contract.
    """

    #: The robot's authoritative cell already is the target.
    ARRIVED = "arrived"
    #: A legal next step is available; the decision carries its request.
    STEP = "step"
    #: The robot already has a move in flight; nothing to decide this tick.
    MOVE_IN_PROGRESS = "move_in_progress"
    #: No legal step is available *right now*. Under
    #: :class:`NonElectronicNavigation` this is the locked "stuck even though
    #: a longer route exists" outcome; under :class:`ElectronicNavigation` it
    #: means a route exists but its next cell is momentarily unavailable
    #: (typically another robot's reservation), so retrying later may work.
    BLOCKED = "blocked"
    #: :class:`ElectronicNavigation` proved no route to the target exists
    #: under this robot's own chassis permissions and the current obstacles.
    #: Never produced by :class:`NonElectronicNavigation`, which searches
    #: nothing and so can never prove unreachability.
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class NavigationDecision:
    """What a :class:`NavigationPolicy` concluded for one robot this tick.

    ``request`` is present exactly when ``status`` is
    :attr:`NavigationStatus.STEP`, and is then a
    :class:`~nether_earth.movement.RobotMoveRequest` that
    :func:`~nether_earth.movement.validate_robot_move` has *already*
    accepted against the state this decision was computed from. The caller
    executes it through
    :func:`~nether_earth.reservations.apply_robot_move_batch`, which
    re-validates it; between the two, a same-tick contender may take the
    cell, which is exactly the contention path §11 locks.

    ``route`` is the remaining planned cells, target last, excluding the
    robot's current cell. It is informational only -- policies are stateless
    and never consume a previously returned route (see the module
    docstring). :class:`ElectronicNavigation` fills it with its full plan;
    :class:`NonElectronicNavigation` has no plan, so it reports at most the
    single cell it is stepping into.
    """

    status: NavigationStatus
    request: RobotMoveRequest | None = None
    route: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.status is NavigationStatus.STEP and self.request is None:
            raise ValueError("a STEP NavigationDecision must carry a move request")
        if self.status is not NavigationStatus.STEP and self.request is not None:
            raise ValueError(
                f"a {self.status.value!r} NavigationDecision must not carry a move request"
            )


# --------------------------------------------------------------------------
# Shared traversability (composed from the M2/M3/M5.1/M5.3 contracts)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _TraversalView:
    """The per-query snapshot of dynamic obstacles, derived once.

    :func:`~nether_earth.movement.folded_robot_occupancy` and
    :func:`~nether_earth.reservations.reservations_from_state` both walk
    every robot, so re-deriving them per candidate cell would make the route
    planner quadratic in robot count for no behavioral gain: within one
    :meth:`NavigationPolicy.next_step` call the state is fixed, so one
    derivation is the same answer as many.
    """

    occupancy: OccupancyGrid
    reservations: ReservationTable


def _traversal_view(state: GameState, world: WorldMap) -> _TraversalView:
    return _TraversalView(
        occupancy=folded_robot_occupancy(world, state),
        reservations=reservations_from_state(state),
    )


def _in_bounds(world: WorldMap, x: int, y: int) -> bool:
    return 0 <= x < world.width and 0 <= y < world.height


def _enterable(
    robot: Robot,
    x: int,
    y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
    view: _TraversalView,
) -> bool:
    """Return whether ``robot`` could stand in ``(x, y)`` given ``view``.

    The checks, and their order, mirror
    :func:`~nether_earth.movement.validate_robot_move`'s cell-level gates
    (bounds, terrain, occupancy, commander blocking, reservation) by calling
    the very same functions, so the planner and the executor cannot drift
    apart. The request-level gates `movement.py` also applies -- does the
    robot exist, does it already have a move in flight, is the step a legal
    single cardinal step -- are not cell properties and stay solely
    `movement.py`'s.
    """
    if not _in_bounds(world, x, y):
        return False
    if not chassis_can_enter(robot.build.chassis, world.terrain.terrain_at(x, y)):
        return False
    if view.occupancy.is_occupied(x, y):
        return False
    if commander_blocks_robot_cell(state, robot, x, y, rules):
        return False
    return not view.reservations.is_reserved_by_other(robot.entity_id, x, y)


def cell_is_enterable(
    robot: Robot,
    x: int,
    y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> bool:
    """Return whether ``robot`` could currently stand in ``(x, y)``.

    The single composed traversability query both navigation policies and
    :func:`plan_route` search over (see the module docstring for the
    contracts it delegates to). Note that a robot's *own* authoritative cell
    reports ``False``, because the robot itself is folded into occupancy;
    callers never test the cell they are standing in.
    """
    return _enterable(robot, x, y, state, world, rules, _traversal_view(state, world))


def _step_request(robot: Robot, dx: int, dy: int) -> RobotMoveRequest:
    return RobotMoveRequest(entity_id=robot.entity_id, dx=dx, dy=dy)


def _step_is_legal(
    robot: Robot,
    dx: int,
    dy: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> RobotMoveRequest | None:
    """Return the request for stepping ``(dx, dy)`` if the executor accepts it.

    The last gate before any decision leaves this module: the candidate goes
    through :func:`~nether_earth.movement.validate_robot_move` itself, with
    `reservations.py`'s
    :func:`~nether_earth.reservations.destination_available` bound, so a
    policy can never propose a step the movement executor would reject.
    """
    request = _step_request(robot, dx, dy)
    result = validate_robot_move(request, state, world, rules, destination_available)
    return request if result.accepted else None


# --------------------------------------------------------------------------
# Deterministic route planning (electronic only)
# --------------------------------------------------------------------------


def plan_route(
    robot: Robot,
    target_x: int,
    target_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> tuple[tuple[int, int], ...] | None:
    """Return the cheapest legal route for ``robot`` to ``(target_x, target_y)``.

    Returns the route as cells excluding the robot's current cell and ending
    at the target -- ``()`` when the robot is already there -- or ``None``
    when no route exists under this robot's own chassis permissions and the
    current obstacles.

    Algorithm: uniform-cost search (Dijkstra) over the 4-connected grid,
    where a cell's edge cost is
    :func:`~nether_earth.movement.move_duration_ticks` for entering it. Plain
    breadth-first search would minimize *cells* traversed, but per-cell
    movement cost is not uniform -- rough terrain multiplies a bipod's cost
    severely -- so BFS would happily route an electronic robot through a
    rough corridor that takes far longer than a longer ordinary-terrain
    detour. Charging the executor's own duration function makes "proper
    pathfinding" mean what a player would expect (fastest arrival) and keeps
    routing cost and movement cost derived from the one centralized
    :class:`~nether_earth.rules.EngineRules` source. There is no A*
    heuristic: the grid is small, and Dijkstra keeps tie-breaking trivially
    auditable.

    Determinism: neighbours are expanded in the fixed
    :data:`CARDINAL_DIRECTIONS` order and the priority queue is keyed by
    ``(cost, insertion_counter, cell)``, so equal-cost routes always resolve
    to the same one; a cell's predecessor is only overwritten on a *strictly*
    cheaper path, never an equal one. No dict or set is ever iterated.

    Terrain safety: every expanded cell passes :func:`cell_is_enterable` for
    *this* robot, so a returned route is always traversable by its own
    chassis -- electronics can never route a bipod through a ditch.
    """
    start = (robot.x, robot.y)
    target = (target_x, target_y)
    if start == target:
        return ()

    view = _traversal_view(state, world)
    if not _enterable(robot, target_x, target_y, state, world, rules, view):
        return None

    best: dict[tuple[int, int], int] = {start: 0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    counter = 0
    frontier: list[tuple[int, int, tuple[int, int]]] = [(0, counter, start)]

    while frontier:
        cost, _order, cell = heappop(frontier)
        if cell == target:
            return _reconstruct(came_from, start, target)
        if cost > best[cell]:
            continue  # a cheaper path to this cell was already expanded
        for dx, dy in CARDINAL_DIRECTIONS:
            neighbour = (cell[0] + dx, cell[1] + dy)
            if not _enterable(robot, neighbour[0], neighbour[1], state, world, rules, view):
                continue
            step_cost = move_duration_ticks(
                robot.build.chassis, world.terrain.terrain_at(*neighbour), rules
            )
            neighbour_cost = cost + step_cost
            known = best.get(neighbour)
            if known is None or neighbour_cost < known:
                best[neighbour] = neighbour_cost
                came_from[neighbour] = cell
                counter += 1
                heappush(frontier, (neighbour_cost, counter, neighbour))

    return None


def _reconstruct(
    came_from: dict[tuple[int, int], tuple[int, int]],
    start: tuple[int, int],
    target: tuple[int, int],
) -> tuple[tuple[int, int], ...]:
    """Walk ``came_from`` back from ``target`` to ``start``, excluding ``start``."""
    route: list[tuple[int, int]] = []
    cell = target
    while cell != start:
        route.append(cell)
        cell = came_from[cell]
    route.reverse()
    return tuple(route)


# --------------------------------------------------------------------------
# The policy interface and its two locked implementations
# --------------------------------------------------------------------------


class NavigationPolicy(Protocol):
    """The one engine navigation interface, per `_specs/open-questions.md` §5.

    A policy is a pure decision function: given a robot and a target cell it
    returns a :class:`NavigationDecision`, never touching
    :class:`~nether_earth.state.GameState`. Autonomous orders (M5.7, issue
    #64) hold a policy chosen by :func:`navigation_policy_for` and call
    :meth:`next_step` once per robot per tick, collecting the resulting
    requests into one
    :func:`~nether_earth.reservations.apply_robot_move_batch` call.

    Implementations are stateless and must remain so (see the module
    docstring): keeping a plan across ticks would reintroduce exactly the
    stale-route problem replanning exists to avoid.
    """

    def next_step(
        self,
        robot: Robot,
        target_x: int,
        target_y: int,
        state: GameState,
        world: WorldMap,
        rules: EngineRules = DEFAULT_RULES,
    ) -> NavigationDecision:
        """Return this tick's decision for ``robot`` heading to the target cell."""
        ...


def _trivial_decision(
    robot: Robot, target_x: int, target_y: int
) -> NavigationDecision | None:
    """Return the decision common to both policies, or ``None`` to continue.

    Arrival and "a move is already in flight" are policy-independent facts,
    so neither implementation gets to answer them differently. The
    in-flight check comes first because a robot mid-move is authoritatively
    still at its origin cell (`movement.py`), so answering ``ARRIVED`` for a
    robot currently moving *away* would be wrong.
    """
    if robot.movement is not None:
        return NavigationDecision(status=NavigationStatus.MOVE_IN_PROGRESS)
    if (robot.x, robot.y) == (target_x, target_y):
        return NavigationDecision(status=NavigationStatus.ARRIVED)
    return None


@dataclass(frozen=True, slots=True)
class NonElectronicNavigation:
    """Deliberately limited, original-style local routing (locked behavior).

    The rule, in full: the robot considers **only steps that reduce its
    remaining distance to the target**, in exactly two candidate positions --

    1. the **primary axis**: a step along whichever axis has the larger
       remaining absolute delta (ties go to X, a fixed rule, not an
       incidental ordering);
    2. the **secondary axis**: a step along the other axis, considered only
       when its remaining delta is nonzero.

    The first candidate the movement executor accepts is taken; if neither is
    accepted the robot reports :attr:`NavigationStatus.BLOCKED` and stays put.

    That is the whole algorithm. It has no memory, no search, and no notion
    of a detour: it will never take a sideways step (one axis already
    aligned) and never a backwards step. So a single blocker directly on its
    approach axis, with the target straight beyond it, stalls it
    indefinitely even though stepping one cell aside and back would arrive
    in two extra ticks. **This is the locked product difference electronics
    buys** (`_specs/open-questions.md` §5, issue #65's fidelity note) -- it
    is not a bug, and it must not be "fixed" into competence. The secondary-
    axis fallback is the only obstacle handling it has, and it only helps
    while the robot is still off-axis from its target.

    Because there is no search, this policy can never return
    :attr:`NavigationStatus.UNREACHABLE`: it does not know whether a route
    exists, only whether its one or two greedy candidates are legal now.
    """

    def next_step(
        self,
        robot: Robot,
        target_x: int,
        target_y: int,
        state: GameState,
        world: WorldMap,
        rules: EngineRules = DEFAULT_RULES,
    ) -> NavigationDecision:
        """Return the greedy local decision described in the class docstring."""
        trivial = _trivial_decision(robot, target_x, target_y)
        if trivial is not None:
            return trivial

        delta_x = target_x - robot.x
        delta_y = target_y - robot.y
        step_x = (0 if delta_x == 0 else (1 if delta_x > 0 else -1), 0)
        step_y = (0, 0 if delta_y == 0 else (1 if delta_y > 0 else -1))

        if abs(delta_x) >= abs(delta_y):
            candidates = (step_x, step_y)
        else:
            candidates = (step_y, step_x)

        for dx, dy in candidates:
            if dx == 0 and dy == 0:
                continue  # that axis is already aligned: not a candidate
            request = _step_is_legal(robot, dx, dy, state, world, rules)
            if request is not None:
                return NavigationDecision(
                    status=NavigationStatus.STEP,
                    request=request,
                    route=((robot.x + dx, robot.y + dy),),
                )

        return NavigationDecision(status=NavigationStatus.BLOCKED)


@dataclass(frozen=True, slots=True)
class ElectronicNavigation:
    """Deterministic proper pathfinding and replanning (locked behavior).

    Plans a full route with :func:`plan_route` on every call -- that
    recomputation *is* the replanning, so a route invalidated by a new
    structure, robot, commander, or reservation is simply never followed
    (see the module docstring on statelessness). The first cell of the plan
    becomes the proposed step, after
    :func:`~nether_earth.movement.validate_robot_move` accepts it.

    Outcomes: :attr:`NavigationStatus.UNREACHABLE` when no route exists at
    all right now, and :attr:`NavigationStatus.BLOCKED` in the narrow case
    where a route exists but its first step is nonetheless refused by the
    executor -- a retry next tick is then the policy-appropriate response.

    Electronics buys *intelligence only*: every cell of every route is
    admitted by :func:`cell_is_enterable`, which asks
    :func:`~nether_earth.movement.chassis_can_enter` about the robot's own
    chassis. An electronic bipod is therefore routed around a ditch, never
    through it, and if the only route crosses a ditch it reports
    ``UNREACHABLE`` rather than acquiring a permission it does not have.
    """

    def next_step(
        self,
        robot: Robot,
        target_x: int,
        target_y: int,
        state: GameState,
        world: WorldMap,
        rules: EngineRules = DEFAULT_RULES,
    ) -> NavigationDecision:
        """Replan from scratch and return the first step of the new route."""
        trivial = _trivial_decision(robot, target_x, target_y)
        if trivial is not None:
            return trivial

        route = plan_route(robot, target_x, target_y, state, world, rules)
        if route is None:
            return NavigationDecision(status=NavigationStatus.UNREACHABLE)

        next_x, next_y = route[0]
        request = _step_is_legal(
            robot, next_x - robot.x, next_y - robot.y, state, world, rules
        )
        if request is None:
            return NavigationDecision(status=NavigationStatus.BLOCKED, route=route)
        return NavigationDecision(
            status=NavigationStatus.STEP, request=request, route=route
        )


#: The stateless singleton instances callers should use. Policies hold no
#: fields, so constructing one per robot per tick would allocate for nothing;
#: :func:`navigation_policy_for` hands out these two.
NON_ELECTRONIC_NAVIGATION: NonElectronicNavigation = NonElectronicNavigation()
ELECTRONIC_NAVIGATION: ElectronicNavigation = ElectronicNavigation()


def navigation_policy_for(robot: Robot) -> NavigationPolicy:
    """Return the navigation policy ``robot``'s build entitles it to.

    The single place electronics is turned into routing behavior: a build
    carrying an electronics module (`robot_build.py`'s optional
    ``RobotBuild.electronics``) navigates with
    :data:`ELECTRONIC_NAVIGATION`, everything else with
    :data:`NON_ELECTRONIC_NAVIGATION`. Callers must not branch on
    ``robot.build.electronics`` themselves, so the mapping stays in one
    place for M5.7 and beyond.
    """
    if robot.build.electronics is not None:
        return ELECTRONIC_NAVIGATION
    return NON_ELECTRONIC_NAVIGATION


def next_navigation_step(
    robot: Robot,
    target_x: int,
    target_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> NavigationDecision:
    """Return ``robot``'s decision under its own policy -- the usual entry point.

    Equivalent to ``navigation_policy_for(robot).next_step(...)``. Callers
    that need to navigate a heterogeneous fleet call this per robot and batch
    the resulting requests into one
    :func:`~nether_earth.reservations.apply_robot_move_batch`.
    """
    return navigation_policy_for(robot).next_step(
        robot, target_x, target_y, state, world, rules
    )
