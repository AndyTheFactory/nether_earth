"""Non-electronic and electronic robot navigation policies.

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
(`_specs/milestones/05-orders-navigation-capture.md`). Exact historical quirks of the
original algorithm remain open
research detail -- the *qualitative* behavior locked above is what this
module implements, behind an interface that lets a future refinement swap
the local rule without touching any caller.

What this module deliberately reuses rather than re-implements
---------------------------------------------------------------
A navigation policy produces a *next step*, never a state mutation. Every
step it proposes is validated by, and must be executed through,
`movement.py`/`reservations.py`:

- **terrain legality**: `movement.py`'s
  :func:`~nether_earth.movement.unit_terrain_enterable` over
  :func:`~nether_earth.movement.chassis_can_enter` -- the single
  chassis/terrain table, applied to all four cells of the robot's 2×2 body.
  Routes are searched over body anchors. Because both policies (and the route planner) ask
  exactly that function about the robot's *own* chassis, an electronic robot
  provably cannot be routed somewhere its chassis forbids: an electronic
  bipod still cannot enter a ditch.
- **occupancy**: `movement.py`'s
  :func:`~nether_earth.movement.folded_robot_occupancy` -- the same fold of
  static structures plus live robots the executor uses.
- **commander blocking**: `movement.py`'s
  :func:`~nether_earth.movement.commander_blocks_robot_cell`, which itself
  composes the `collision.py` contract. No overlap math here.
- **reservations**: `reservations.py`'s
  :func:`~nether_earth.reservations.destination_available`, so a cell
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

The one exception is a Search & Destroy (robots) hunt:
re-planning a long route every tick for every hunter is costly, and a hunt
that gives up on a route blocked for one tick loses its target. The owner
decided (2026-09-27) that a hunter follows a cached route and re-plans every
:attr:`~nether_earth.rules.EngineRules.robot_hunt_replan_ticks` ticks, or
early when the route becomes invalid. The cache is authoritative robot state
(:attr:`~nether_earth.robot.Robot.hunt_route`), not policy state:
:func:`next_hunt_step` reads it from the robot and returns the value to
store back, so this module still holds no state of its own.

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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from heapq import heappop, heappush
from typing import Protocol

from nether_earth.ids import EntityId
from nether_earth.map import WorldMap
from nether_earth.movement import (
    RobotMoveRequest,
    commander_blocks_robot_cell,
    move_duration_ticks,
    static_occupancy,
    unit_move_terrain,
    unit_terrain_enterable,
    validate_robot_move,
)
from nether_earth.occupancy import (
    UNIT_FOOTPRINT_OFFSETS,
    unit_footprint_cells,
    unit_footprint_in_bounds,
)
from nether_earth.reservations import (
    destination_available,
    reservations_from_state,
)
from nether_earth.rng import MatchRandom, derive_seed
from nether_earth.robot import Robot, RobotHuntRoute
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "CARDINAL_DIRECTIONS",
    "ELECTRONIC_NAVIGATION",
    "NON_ELECTRONIC_NAVIGATION",
    "ElectronicNavigation",
    "HuntDecision",
    "NavigationDecision",
    "NavigationPolicy",
    "NavigationStatus",
    "NonElectronicNavigation",
    "body_alignment_anchors",
    "body_contact_anchors",
    "cell_is_enterable",
    "derive_wander_seed",
    "navigation_policy_for",
    "next_body_approach_step",
    "next_hunt_step",
    "next_navigation_step",
    "plan_route",
    "plan_route_to_any",
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
    navigation* concluded. Autonomous orders branch on
    these to decide whether to keep going, wait, or abandon an order, so
    they are part of this module's public contract.
    """

    #: The robot's authoritative cell already is the target.
    ARRIVED = "arrived"
    #: A legal next step is available; the decision carries its request.
    STEP = "step"
    #: The robot already has a move in flight; nothing to decide this tick.
    MOVE_IN_PROGRESS = "move_in_progress"
    #: The robot is mid-turn (owner decision, 2026-09-23): it is busy, not
    #: stuck, so a caller should wait rather than replan or abandon. Kept
    #: distinct from :attr:`BLOCKED` for exactly that reason.
    TURN_IN_PROGRESS = "turn_in_progress"
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
# Shared traversability (composed from the occupancy, collision, movement
# and reservation contracts)
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

    The planner reads the same facts through per-anchor indices
    (:func:`_anchor_index`), so each searched cell is a few lookups instead
    of four footprint walks per query: ``static_blocked`` marks anchors a
    structure blocks (memoized per world, see :func:`_static_blocked`),
    ``unit_blockers`` names every robot whose body or reserved destination
    overlaps an anchor, and ``commander_anchors`` holds every anchor some
    commander's body overlaps -- the only cells where
    :func:`~nether_earth.movement.commander_blocks_robot_cell` can say yes.
    """

    static_blocked: bytes
    unit_blockers: Mapping[int, frozenset[EntityId]]
    commander_anchors: frozenset[int]


#: One traversal view per (state, world), shared by every robot planning in
#: the same tick. The view is a pure derivation of both (see
#: :class:`_TraversalView`), but electronic navigation rebuilt it once per
#: robot per tick -- folding every robot into the occupancy grid and walking
#: the reservation table each time -- which profiled as the dominant cost of
#: a tick with more than one electronic robot. Same memo shape as
#: `capture.py`'s ``_EFFECTIVE_WORLD_MEMO``: keyed by object identity, with
#: both inputs kept alongside so a recycled ``id()`` cannot serve a stale
#: view, and cleared wholesale rather than evicted. ``GameState`` is
#: immutable, so a given state's view can never go out of date.
_TRAVERSAL_VIEW_MEMO: dict[tuple[int, int], tuple[GameState, WorldMap, _TraversalView]] = {}
_TRAVERSAL_VIEW_MEMO_MAX = 8


def _traversal_view(state: GameState, world: WorldMap) -> _TraversalView:
    key = (id(state), id(world))
    cached = _TRAVERSAL_VIEW_MEMO.get(key)
    if cached is not None and cached[0] is state and cached[1] is world:
        return cached[2]
    reservations = reservations_from_state(state)
    unit_blockers: dict[int, set[EntityId]] = {}
    body_cells = [
        (robot.entity_id, cell)
        for robot in state.robots
        for cell in unit_footprint_cells(robot.x, robot.y)
    ]
    body_cells.extend((holder, cell) for cell, holder in reservations.holders.items())
    for entity_id, cell in body_cells:
        for anchor in _anchors_covering(world, cell):
            unit_blockers.setdefault(anchor, set()).add(entity_id)
    commander_anchors = frozenset(
        _anchor_index(world, commander.x + dx, commander.y + dy)
        for commander in state.commanders
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if _in_bounds(world, commander.x + dx, commander.y + dy)
    )
    view = _TraversalView(
        static_blocked=_static_blocked(world),
        unit_blockers={anchor: frozenset(ids) for anchor, ids in unit_blockers.items()},
        commander_anchors=commander_anchors,
    )
    if len(_TRAVERSAL_VIEW_MEMO) >= _TRAVERSAL_VIEW_MEMO_MAX:
        _TRAVERSAL_VIEW_MEMO.clear()
    _TRAVERSAL_VIEW_MEMO[key] = (state, world, view)
    return view


def _anchors_covering(world: WorldMap, cell: tuple[int, int]) -> tuple[int, ...]:
    """Return the index of every on-map anchor whose 2×2 body covers ``cell``."""
    return tuple(
        _anchor_index(world, cell[0] - dx, cell[1] - dy)
        for dx, dy in UNIT_FOOTPRINT_OFFSETS
        if _in_bounds(world, cell[0] - dx, cell[1] - dy)
    )


#: Anchors a static structure blocks, per world -- the structure half of the
#: folded occupancy grid, which only changes with the world itself (capture
#: or destruction overlays, each a memoized world object).
_STATIC_BLOCKED_MEMO: dict[int, tuple[WorldMap, bytes]] = {}
_STATIC_BLOCKED_MEMO_MAX = 8


def _static_blocked(world: WorldMap) -> bytes:
    cached = _STATIC_BLOCKED_MEMO.get(id(world))
    if cached is not None and cached[0] is world:
        return cached[1]
    blocked = bytearray((world.height + 2) * world.width)
    for cell in static_occupancy(world).cells():
        for anchor in _anchors_covering(world, cell):
            blocked[anchor] = 1
    result = bytes(blocked)
    if len(_STATIC_BLOCKED_MEMO) >= _STATIC_BLOCKED_MEMO_MAX:
        _STATIC_BLOCKED_MEMO.clear()
    _STATIC_BLOCKED_MEMO[id(world)] = (world, result)
    return result


@dataclass(frozen=True, slots=True)
class _StaticTerrain:
    """One chassis's terrain facts for every body anchor of a map, flat-indexed.

    Index ``(y + 1) * width + x`` (:func:`_anchor_index`); the padding row
    above and below, and the last column (a 2×2 body anchored there would
    leave the map), are never enterable, so a planner can step ``±1`` /
    ``±width`` from any enterable index without a bounds check.
    ``enterable[i]`` is :func:`_in_bounds` plus
    :func:`~nether_earth.movement.unit_terrain_enterable`; ``step_cost[i]``
    is :func:`~nether_earth.movement.move_duration_ticks` for moving onto
    that anchor (0 where it is not enterable).
    """

    enterable: bytes
    step_cost: tuple[int, ...]


#: Terrain, map bounds and rules never change inside a search, and only
#: change between searches when debris lands, so these are derived once per
#: (terrain, bounds, chassis, rules) instead of once per searched cell --
#: profiling a real match put ~45% of every route plan in re-deriving them.
#: Keyed by object identity with both objects kept alive, like
#: :data:`_TRAVERSAL_VIEW_MEMO`; the terrain grid is shared by every
#: ownership-overlay world, so capture never invalidates it.
_STATIC_TERRAIN_MEMO: dict[
    tuple[int, int, int, ModuleIdentity, int], tuple[object, EngineRules, _StaticTerrain]
] = {}
_STATIC_TERRAIN_MEMO_MAX = 32


def _anchor_index(world: WorldMap, x: int, y: int) -> int:
    return (y + 1) * world.width + x


def _static_terrain(robot: Robot, world: WorldMap, rules: EngineRules) -> _StaticTerrain:
    chassis = robot.build.chassis
    key = (id(world.terrain), world.width, world.height, chassis, id(rules))
    cached = _STATIC_TERRAIN_MEMO.get(key)
    if cached is not None and cached[0] is world.terrain and cached[1] is rules:
        return cached[2]
    size = (world.height + 2) * world.width
    enterable = bytearray(size)
    step_cost = [0] * size
    for y in range(world.height):
        for x in range(world.width):
            if _in_bounds(world, x, y) and unit_terrain_enterable(chassis, world, x, y):
                index = _anchor_index(world, x, y)
                enterable[index] = 1
                step_cost[index] = move_duration_ticks(
                    chassis, unit_move_terrain(world, x, y), rules
                )
    result = _StaticTerrain(enterable=bytes(enterable), step_cost=tuple(step_cost))
    if len(_STATIC_TERRAIN_MEMO) >= _STATIC_TERRAIN_MEMO_MAX:
        _STATIC_TERRAIN_MEMO.clear()
    _STATIC_TERRAIN_MEMO[key] = (world.terrain, rules, result)
    return result


def _in_bounds(world: WorldMap, x: int, y: int) -> bool:
    return unit_footprint_in_bounds(x, y, world.width, world.height)


def _enterable(
    robot: Robot,
    x: int,
    y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
    view: _TraversalView,
) -> bool:
    """Return whether ``robot``'s 2×2 body could stand anchored at ``(x, y)`` given ``view``.

    Whole-body checks (`_specs/open-questions.md` §21): the body is
    on the map, its four cells are terrain the chassis may enter, and no
    structure, other robot, commander, or other robot's reserved destination
    body overlaps it. The robot itself never blocks its own next body.

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
    if not _static_terrain(robot, world, rules).enterable[_anchor_index(world, x, y)]:
        return False
    return _dynamic_enterable(robot, x, y, state, world, rules, view)


def _dynamic_enterable(
    robot: Robot,
    x: int,
    y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
    view: _TraversalView,
) -> bool:
    """The state-dependent half of :func:`_enterable`: structures, robots, commanders, reservations.

    Reads :class:`_TraversalView`'s indices, which answer exactly what
    ``view.occupancy.blocks_unit(x, y, ignore=robot.entity_id)`` and
    ``view.reservations.is_reserved_by_other`` over the body would: the
    robot's own body and reservation never block it.
    """
    index = _anchor_index(world, x, y)
    if view.static_blocked[index]:
        return False
    blockers = view.unit_blockers.get(index)
    if blockers is not None and (len(blockers) > 1 or robot.entity_id not in blockers):
        return False
    return index not in view.commander_anchors or not commander_blocks_robot_cell(
        state, robot, x, y, rules, world=world
    )


def cell_is_enterable(
    robot: Robot,
    x: int,
    y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> bool:
    """Return whether ``robot`` could currently stand with its body anchored at ``(x, y)``.

    The single composed traversability query both navigation policies and
    :func:`plan_route` search over (see the module docstring for the
    contracts it delegates to). ``(x, y)`` is a 2×2 body anchor;
    the robot's own current body never blocks it.
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
    current obstacles. The single-goal case of :func:`plan_route_to_any`,
    which documents the algorithm.
    """
    return plan_route_to_any(robot, ((target_x, target_y),), state, world, rules)


def plan_route_to_any(
    robot: Robot,
    goals: tuple[tuple[int, int], ...],
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> tuple[tuple[int, int], ...] | None:
    """Return the cheapest legal route for ``robot`` to *any* anchor of ``goals``.

    Returns the route as cells excluding the robot's current cell and ending
    at the reached goal -- ``()`` when the robot already stands on a goal --
    or ``None`` when no goal is reachable under this robot's own chassis
    permissions and the current obstacles. A goal ``robot`` cannot currently
    stand on is simply not a goal; only when *none* is enterable (and the
    robot is not already on one) is the answer ``None`` without a search.

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
    auditable. With several goals the search stops at the first one popped,
    i.e. the cheapest; equal-cost goals resolve by the same deterministic
    queue order as equal-cost routes.

    Determinism: neighbours are expanded in the fixed
    :data:`CARDINAL_DIRECTIONS` order and the priority queue is keyed by
    ``(cost, insertion_counter, cell)``, so equal-cost routes always resolve
    to the same one; a cell's predecessor is only overwritten on a *strictly*
    cheaper path, never an equal one. ``goals`` is only used for membership,
    so its order never affects the result.

    Terrain safety: every expanded cell passes :func:`cell_is_enterable` for
    *this* robot, so a returned route is always traversable by its own
    chassis -- electronics can never route a bipod through a ditch.
    """
    start = (robot.x, robot.y)
    if start in goals:
        return ()

    view = _traversal_view(state, world)
    static = _static_terrain(robot, world, rules)
    static_enterable = static.enterable
    step_cost = static.step_cost
    width = world.width

    # The search runs over flat anchor indices (see `_StaticTerrain`) with
    # the static terrain facts precomputed; only the state-dependent half of
    # `_enterable` is evaluated here, once per index (0 unknown, 1 enterable,
    # 2 blocked). Same predicate, same costs, same expansion order and
    # tie-breaks as the cell-keyed search this replaces, so routes are
    # bit-for-bit identical -- it is only cheaper per cell.
    known: bytearray = bytearray(len(static_enterable))

    def enterable(index: int) -> bool:
        status = known[index]
        if status == 0:
            status = (
                1
                if static_enterable[index]
                and _dynamic_enterable(
                    robot, index % width, index // width - 1, state, world, rules, view
                )
                else 2
            )
            known[index] = status
        return status == 1

    targets = frozenset(
        _anchor_index(world, x, y)
        for x, y in goals
        if _in_bounds(world, x, y) and enterable(_anchor_index(world, x, y))
    )
    if not targets:
        return None

    start_index = _anchor_index(world, *start)
    offsets = tuple(dx + dy * width for dx, dy in CARDINAL_DIRECTIONS)
    best: dict[int, int] = {start_index: 0}
    came_from: dict[int, int] = {}
    counter = 0
    frontier: list[tuple[int, int, int]] = [(0, counter, start_index)]

    while frontier:
        cost, _order, index = heappop(frontier)
        if index in targets:
            route: list[tuple[int, int]] = []
            while index != start_index:
                route.append((index % width, index // width - 1))
                index = came_from[index]
            route.reverse()
            return tuple(route)
        if cost > best[index]:
            continue  # a cheaper path to this cell was already expanded
        for offset in offsets:
            neighbour = index + offset
            if not enterable(neighbour):
                continue
            neighbour_cost = cost + step_cost[neighbour]
            previous = best.get(neighbour)
            if previous is None or neighbour_cost < previous:
                best[neighbour] = neighbour_cost
                came_from[neighbour] = index
                counter += 1
                heappush(frontier, (neighbour_cost, counter, neighbour))

    return None


#: The five cell offsets that count as "in contact" for :func:`body_contact_anchors`:
#: the cell itself and its four cardinal neighbours. Corner-only (diagonal)
#: contact is deliberately absent -- see that function.
_CONTACT_NEIGHBOURS: tuple[tuple[int, int], ...] = ((0, 0), *CARDINAL_DIRECTIONS)


def body_contact_anchors(target_x: int, target_y: int) -> tuple[tuple[int, int], ...]:
    """Return every anchor from which a 2x2 body can engage the target body.

    ``(target_x, target_y)`` is the target unit's 2x2 body anchor.
    An anchor qualifies when some cell of the body anchored there is equal or
    *cardinally* adjacent to some cell of the target's body, which is the
    "touches or overlaps" rule restricted to edge contact.

    Corner-only contact (anchors two cells away on *both* axes) is excluded
    on purpose. A robot fires along its cardinal facing and a bullet hits
    when the two bodies overlap (``unit_footprints_overlap``, i.e. anchors
    within one cell on each axis), so from a diagonal corner anchor the
    bullet lane misses the target body by one cell on the off axis: the
    hunter would arrive, stop and fire for ever without ever hitting.
    Excluding those four anchors makes every goal of a Search & Destroy
    approach a position the hunter can actually shoot from.

    Derived from :data:`~nether_earth.occupancy.UNIT_FOOTPRINT_OFFSETS` and
    :data:`CARDINAL_DIRECTIONS` rather than a hard-coded box, and returned
    sorted so callers never depend on set order. Overlapping anchors are
    included for completeness; they are never enterable while the target
    stands there, so a route always ends beside it.
    """
    anchors: set[tuple[int, int]] = set()
    for cell_x, cell_y in unit_footprint_cells(target_x, target_y):
        for off_x, off_y in UNIT_FOOTPRINT_OFFSETS:
            for near_x, near_y in _CONTACT_NEIGHBOURS:
                anchors.add((cell_x + near_x - off_x, cell_y + near_y - off_y))
    return tuple(sorted(anchors))


def body_alignment_anchors(target_x: int, target_y: int) -> tuple[tuple[int, int], ...]:
    """Return the four anchors from which a 2x2 body is *lane-aligned* with the target.

    Both cells of the hunter's leading edge face both cells of the target's:
    the two bodies share their column pair or their row pair, touching along
    a full edge rather than at a corner or with a one-cell stagger. Since a
    2x2 body spans two cells, those anchors are exactly two cells away on one
    axis and level on the other.

    This is where a Search & Destroy hunter wants to end up (owner request):
    a robot fires along its cardinal facing and the shot only connects while
    the bodies overlap on the off axis, so a staggered stop makes hits
    depend on the stagger rather than on the aim. A subset of
    :func:`body_contact_anchors`, which stays the fallback for a target whose
    aligned anchors are all blocked or unreachable.
    """
    return tuple(
        sorted(
            (
                (target_x, target_y - 2),
                (target_x, target_y + 2),
                (target_x - 2, target_y),
                (target_x + 2, target_y),
            )
        )
    )


# --------------------------------------------------------------------------
# The policy interface and its two locked implementations
# --------------------------------------------------------------------------


class NavigationPolicy(Protocol):
    """The one engine navigation interface, per `_specs/open-questions.md` §5.

    A policy is a pure decision function: given a robot and a target cell it
    returns a :class:`NavigationDecision`, never touching
    :class:`~nether_earth.state.GameState`. Autonomous orders hold a policy
    chosen by :func:`navigation_policy_for` and call
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

    def next_step_to_body(
        self,
        robot: Robot,
        target_x: int,
        target_y: int,
        state: GameState,
        world: WorldMap,
        rules: EngineRules = DEFAULT_RULES,
    ) -> NavigationDecision:
        """Return this tick's decision for ``robot`` closing on the unit anchored there.

        The target cell is another unit's 2×2 body anchor, which is occupied
        by definition, so an implementation must not treat that occupancy as
        proof the target is unreachable.
        """
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
    if robot.turning is not None:
        return NavigationDecision(status=NavigationStatus.TURN_IN_PROGRESS)
    if (robot.x, robot.y) == (target_x, target_y):
        return NavigationDecision(status=NavigationStatus.ARRIVED)
    return None


@dataclass(frozen=True, slots=True)
class NonElectronicNavigation:
    """Deliberately limited, original-style local routing (locked behavior).

    The rule, in full:

    1. the **primary step**: along whichever axis has the larger remaining
       absolute delta (ties go to X, a fixed rule, not an incidental
       ordering);
    2. **momentum**: the direction the robot is already walking
       (``robot.facing``), when that is not the primary step. A detour under
       way continues rather than unravelling the moment the greedy step
       looks legal again;
    3. the **two perpendicular steps**, in an order drawn from this robot's
       own seeded stream. One of them is the secondary axis, the step that
       also closes the other delta; the draw decides whether the robot
       detours that way or the other way;
    4. **anything else still legal**, the reverse of the primary included,
       as a last resort.

    The first candidate the movement executor accepts is taken. Only a robot
    for which not one cardinal step is legal reports
    :attr:`NavigationStatus.BLOCKED`.

    Steps 2 and 3 are the Spectrum's own behavior, not an improvement on it
    (`_specs/open-questions.md` §5/§22.6). ``Lb222_choose_direction_to_move``
    intersects the directions that point at the target with the directions
    the robot may actually move in (``Lb513_get_robot_movement_possibilities``)
    and picks one at random; when that intersection is empty it falls through
    to ``Lb33e_pick_direction_at_random``, which picks at random among every
    possible direction. The original robot is erratic, never immobile.

    **Coherence.** A direction redrawn every tick would jitter on the spot
    and never clear an obstacle, so the Spectrum commits to its choice for
    ``rand & 3 + 3`` = 3-6 game cycles
    (``ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING``, ``Lb1f5``). Two
    things stand in for that counter here, and this policy still stores
    nothing on the robot and threads no RNG through ``GameState``:

    - the perpendicular draw is fixed for a whole tick window
      (``tick // rules.dumb_wander_commit_ticks``), so a robot picks one
      side of an obstacle and keeps picking it;
    - momentum (candidate 2) carries an started detour forward.

    Momentum is what actually breaks loops. Without it the robot steps
    aside, the primary step is legal for one cell, the greedy pull drags it
    back behind the obstacle, and it paces the same two cells for ever.
    Measured on a staggered-wall fixture, without momentum the robot fails
    to arrive on most seeds; with it, it arrives on every seed in roughly
    half the ticks. Extra randomness does not fix this and makes it worse:
    drawing uniformly over all four directions, or spending whole windows
    roaming at random, both lowered the arrival rate in the same test --
    a robot that moves at 24 ticks per step cannot afford a random walk.

    What electronics still buys is unchanged and substantial: a shortest
    legal route (:class:`ElectronicNavigation`) versus a greedy step with a
    random detour that may walk into a pocket, retreat from it and try again.
    This policy still has no search, no memory and no notion of a plan, and
    so can never return :attr:`NavigationStatus.UNREACHABLE`: it does not
    know whether a route exists, only which steps are legal now.
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

        primary = _primary_step(target_x - robot.x, target_y - robot.y)
        for dx, dy in _candidate_order(primary, robot, state, rules):
            request = _step_is_legal(robot, dx, dy, state, world, rules)
            if request is not None:
                return _step_decision(robot, request, dx, dy)

        return NavigationDecision(status=NavigationStatus.BLOCKED)

    def next_step_to_body(
        self,
        robot: Robot,
        target_x: int,
        target_y: int,
        state: GameState,
        world: WorldMap,
        rules: EngineRules = DEFAULT_RULES,
    ) -> NavigationDecision:
        """Greedy-step toward the nearest anchor lane-aligned with the target.

        Alignment is arrival: once ``robot`` stands on one of the target's
        :func:`body_alignment_anchors` the two bodies face each other along a
        full edge and the caller (a Search & Destroy hunt) takes over with
        fire. Until then the greedy goal is the nearest of those anchors
        rather than the target's own anchor -- stepping at the occupied body
        would be refused every tick, and the detour fallback would then walk
        the hunter off its target instead of alongside it.
        """
        if robot.movement is not None:
            return NavigationDecision(status=NavigationStatus.MOVE_IN_PROGRESS)
        if robot.turning is not None:
            return NavigationDecision(status=NavigationStatus.TURN_IN_PROGRESS)
        anchors = body_alignment_anchors(target_x, target_y)
        if (robot.x, robot.y) in anchors:
            return NavigationDecision(status=NavigationStatus.ARRIVED)
        goal = min(
            anchors, key=lambda cell: (_distance(robot, cell), cell)
        )
        return self.next_step(robot, goal[0], goal[1], state, world, rules)


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
        return _first_step_decision(robot, route, state, world, rules)

    def next_step_to_body(
        self,
        robot: Robot,
        target_x: int,
        target_y: int,
        state: GameState,
        world: WorldMap,
        rules: EngineRules = DEFAULT_RULES,
    ) -> NavigationDecision:
        """Replan to an anchor lane-aligned with the target unit's body.

        The goal set is :func:`body_alignment_anchors` of the target's
        anchor: the four positions where the two bodies face each other
        along a full edge, which is where a hunter's cardinal shot connects
        (owner request). Those are never the target's own (occupied) anchor,
        so an occupied body can never make the target ``UNREACHABLE``.

        When no aligned anchor is reachable -- a target backed into a corner,
        or every lane blocked -- the goal set falls back to the wider
        :func:`body_contact_anchors`, so the hunt still closes as far as it
        can instead of abandoning the order. Standing on a goal of whichever
        set applies is ``ARRIVED``; the caller decides what arrival means.
        """
        if robot.movement is not None:
            return NavigationDecision(status=NavigationStatus.MOVE_IN_PROGRESS)
        if robot.turning is not None:
            return NavigationDecision(status=NavigationStatus.TURN_IN_PROGRESS)
        route = plan_route_to_any(
            robot, body_alignment_anchors(target_x, target_y), state, world, rules
        )
        if route is None:
            route = plan_route_to_any(
                robot, body_contact_anchors(target_x, target_y), state, world, rules
            )
        if route == ():
            return NavigationDecision(status=NavigationStatus.ARRIVED)
        return _first_step_decision(robot, route, state, world, rules)


def _step_decision(
    robot: Robot, request: RobotMoveRequest, dx: int, dy: int
) -> NavigationDecision:
    """Return the one-cell ``STEP`` decision for an already-validated request."""
    return NavigationDecision(
        status=NavigationStatus.STEP,
        request=request,
        route=((robot.x + dx, robot.y + dy),),
    )


def derive_wander_seed(match_seed: int, tick: int, entity_id: EntityId, commit_ticks: int) -> int:
    """Return the seed of ``entity_id``'s wander stream for the tick's window.

    The window is ``tick // commit_ticks``, so the draw is constant for
    ``commit_ticks`` ticks and a detour holds its direction long enough to
    clear an obstacle (see :class:`NonElectronicNavigation`). Mixing the
    robot's id in gives every robot its own stream, so two robots blocked by
    the same wall in the same window do not fall into lockstep.
    """
    return derive_seed(match_seed, tick // commit_ticks, entity_id.value)


def _distance(robot: Robot, cell: tuple[int, int]) -> int:
    """Return the Manhattan distance from ``robot``'s anchor to ``cell``."""
    return abs(cell[0] - robot.x) + abs(cell[1] - robot.y)


def _primary_step(delta_x: int, delta_y: int) -> tuple[int, int]:
    """Return the one cardinal step down the larger remaining delta (ties to X)."""
    if abs(delta_x) >= abs(delta_y):
        return (1 if delta_x > 0 else -1, 0) if delta_x else (0, 1 if delta_y > 0 else -1)
    return (0, 1 if delta_y > 0 else -1)


def _candidate_order(
    primary: tuple[int, int],
    robot: Robot,
    state: GameState,
    rules: EngineRules,
) -> tuple[tuple[int, int], ...]:
    """Return this robot's candidate steps for the tick, best first.

    The primary step; then, when the robot is already walking some other
    way, that direction again; then the two steps perpendicular to the
    primary in this window's drawn order; then the reverse of the primary.

    Carrying on in the robot's current facing is the *momentum* rule, and it
    is what keeps a detour from unravelling. Without it the robot steps
    aside to clear an obstacle, the primary step becomes legal for one cell,
    the greedy pull drags it straight back into the obstacle's shadow, and
    it paces that two-cell loop for the rest of the match -- the reported
    "robots get stuck in loops". The Spectrum avoids the same loop with a
    counter (``ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING``, ``Lb1f5``:
    keep walking this way for 3-6 game cycles before reconsidering);
    ``robot.facing`` already records the direction a robot is walking, so
    this policy reads its momentum off that rather than storing a second
    counter on ``Robot``. It costs the robot nothing either way: a step in
    the direction it already faces needs no turn.

    See :class:`NonElectronicNavigation` for why the perpendicular pair is
    drawn once per window rather than once per tick, and why its order is
    drawn at all rather than always preferring the one that also closes the
    other axis.
    """
    perpendicular = (
        [(0, -1), (0, 1)] if primary[1] == 0 else [(-1, 0), (1, 0)]
    )
    rng = MatchRandom(
        seed=derive_wander_seed(
            state.seed, state.tick, robot.entity_id, rules.dumb_wander_commit_ticks
        )
    )
    rng.shuffle(perpendicular)
    candidates = [primary]
    if robot.facing.step != primary:
        candidates.append(robot.facing.step)
    candidates += [step for step in perpendicular if step not in candidates]
    candidates += [step for step in CARDINAL_DIRECTIONS if step not in candidates]
    return tuple(candidates)


def _first_step_decision(
    robot: Robot,
    route: tuple[tuple[int, int], ...] | None,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> NavigationDecision:
    """Turn a planned (non-empty) route, or ``None``, into this tick's decision."""
    if route is None:
        return NavigationDecision(status=NavigationStatus.UNREACHABLE)
    next_x, next_y = route[0]
    request = _step_is_legal(robot, next_x - robot.x, next_y - robot.y, state, world, rules)
    if request is None:
        return NavigationDecision(status=NavigationStatus.BLOCKED, route=route)
    return NavigationDecision(status=NavigationStatus.STEP, request=request, route=route)


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
    place.
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


def next_body_approach_step(
    robot: Robot,
    target_x: int,
    target_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> NavigationDecision:
    """Return ``robot``'s decision for closing on the unit anchored at the target.

    Equivalent to ``navigation_policy_for(robot).next_step_to_body(...)``:
    the entry point for pursuing another robot, whose anchor cell is
    occupied. Search & Destroy (robots) uses :func:`next_hunt_step`
    instead, which never reports ``UNREACHABLE``.
    """
    return navigation_policy_for(robot).next_step_to_body(
        robot, target_x, target_y, state, world, rules
    )


# --------------------------------------------------------------------------
# Search & Destroy (robots) hunts: cached route, periodic re-plan
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HuntDecision:
    """One hunt tick's navigation decision plus the hunter's cache to store back.

    ``decision`` is never :attr:`NavigationStatus.UNREACHABLE`. ``hunt_route``
    is the value the caller writes to
    :attr:`~nether_earth.robot.Robot.hunt_route`: the robot's own cache when
    nothing was re-planned, a new one after a re-plan, and always ``None``
    for a robot without electronics (it has no route to cache).
    """

    decision: NavigationDecision
    hunt_route: RobotHuntRoute | None


def _plan_body_approach(
    robot: Robot,
    target_x: int,
    target_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> tuple[tuple[int, int], ...] | None:
    """Plan to the target's aligned anchors, else its contact anchors.

    The goal logic of :meth:`ElectronicNavigation.next_step_to_body`: aligned
    anchors first, the wider contact set only when none is reachable.
    """
    route = plan_route_to_any(
        robot, body_alignment_anchors(target_x, target_y), state, world, rules
    )
    if route is None:
        route = plan_route_to_any(
            robot, body_contact_anchors(target_x, target_y), state, world, rules
        )
    return route


def _cached_next_cell(
    route: RobotHuntRoute, position: tuple[int, int]
) -> tuple[int, int] | None:
    """Return the cell after ``position`` on ``route``, or ``None`` when there is none.

    ``None`` covers both a route the robot has run to the end of and a
    position that is not on the route at all; either way the cache no longer
    tells the robot where to go, and it re-plans.
    """
    cells = route.cells()
    if position == (route.origin_x, route.origin_y):
        return cells[0] if cells else None
    try:
        index = cells.index(position)
    except ValueError:
        return None
    return cells[index + 1] if index + 1 < len(cells) else None


def _greedy_hunt_step(
    robot: Robot,
    target_x: int,
    target_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> NavigationDecision:
    """Step toward the target when no route exists (owner decision, 2026-09-27).

    A robot touching the target already has what a route would give it, so
    it holds (``ARRIVED``). Otherwise the goal is the nearest of the target's
    :func:`body_alignment_anchors`, as for :class:`NonElectronicNavigation`,
    and the step is the single :func:`_primary_step` down the larger
    remaining delta. There is no detour: if that step is illegal the robot
    waits this update (``BLOCKED``).
    """
    if (robot.x, robot.y) in body_contact_anchors(target_x, target_y):
        return NavigationDecision(status=NavigationStatus.ARRIVED)
    goal = min(
        body_alignment_anchors(target_x, target_y),
        key=lambda cell: (_distance(robot, cell), cell),
    )
    dx, dy = _primary_step(goal[0] - robot.x, goal[1] - robot.y)
    request = _step_is_legal(robot, dx, dy, state, world, rules)
    if request is None:
        return NavigationDecision(status=NavigationStatus.BLOCKED)
    return _step_decision(robot, request, dx, dy)


def next_hunt_step(
    robot: Robot,
    target_id: EntityId,
    target_x: int,
    target_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules = DEFAULT_RULES,
) -> HuntDecision:
    """Return ``robot``'s Search & Destroy (robots) step toward the target robot.

    Owner decision (2026-09-27): a hunt never gives up because a route
    is blocked right now. A robot without electronics uses its own greedy
    :meth:`NonElectronicNavigation.next_step_to_body`, which never reports
    ``UNREACHABLE``, and caches nothing. An electronics robot:

    1. waits while a move or turn is in flight, and has arrived when it
       stands on one of the target's aligned anchors (neither plans);
    2. follows its cached route (:class:`~nether_earth.robot.RobotHuntRoute`)
       and re-plans only when the cache is missing, was planned for another
       target, is ``robot_hunt_replan_ticks`` old, has run out, or its next
       cell is no longer enterable (occupied, reserved, or terrain the
       chassis cannot enter). A next cell that is enterable but whose step
       the executor still refuses is only ``BLOCKED``: the robot waits, and
       does not re-plan every tick;
    3. plans with the body goals (aligned anchors, else contact anchors).
       A route of ``()`` means the robot is on a goal and has arrived. No
       route at all is cached as such, and until the next periodic re-plan
       the robot steps greedily toward the target (:func:`_greedy_hunt_step`).

    Stateless like the policies: the cache comes in on ``robot`` and goes
    back out in the result, and only ``state.tick`` measures its age.
    """
    if robot.build.electronics is None:
        return HuntDecision(
            NON_ELECTRONIC_NAVIGATION.next_step_to_body(
                robot, target_x, target_y, state, world, rules
            ),
            None,
        )
    cache = robot.hunt_route
    if robot.movement is not None:
        return HuntDecision(NavigationDecision(status=NavigationStatus.MOVE_IN_PROGRESS), cache)
    if robot.turning is not None:
        return HuntDecision(NavigationDecision(status=NavigationStatus.TURN_IN_PROGRESS), cache)
    position = (robot.x, robot.y)
    if position in body_alignment_anchors(target_x, target_y):
        return HuntDecision(NavigationDecision(status=NavigationStatus.ARRIVED), cache)

    next_cell: tuple[int, int] | None = None
    fresh = (
        cache is not None
        and cache.target_id == target_id
        and 0 <= state.tick - cache.planned_tick < rules.robot_hunt_replan_ticks
    )
    if fresh and cache is not None:
        if cache.steps is None:
            return HuntDecision(
                _greedy_hunt_step(robot, target_x, target_y, state, world, rules), cache
            )
        if cache.steps == "" and position == (cache.origin_x, cache.origin_y) and (
            position in body_contact_anchors(target_x, target_y)
        ):
            # Planned as "already on a contact goal" and still touching it.
            return HuntDecision(NavigationDecision(status=NavigationStatus.ARRIVED), cache)
        next_cell = _cached_next_cell(cache, position)
        if next_cell is not None and not cell_is_enterable(
            robot, next_cell[0], next_cell[1], state, world, rules
        ):
            next_cell = None

    if next_cell is None:
        route = _plan_body_approach(robot, target_x, target_y, state, world, rules)
        cache = RobotHuntRoute.from_cells(target_id, state.tick, position, route)
        if route is None:
            return HuntDecision(
                _greedy_hunt_step(robot, target_x, target_y, state, world, rules), cache
            )
        if not route:
            return HuntDecision(NavigationDecision(status=NavigationStatus.ARRIVED), cache)
        next_cell = route[0]

    request = _step_is_legal(
        robot, next_cell[0] - robot.x, next_cell[1] - robot.y, state, world, rules
    )
    if request is None:
        return HuntDecision(NavigationDecision(status=NavigationStatus.BLOCKED), cache)
    return HuntDecision(
        NavigationDecision(
            status=NavigationStatus.STEP, request=request, route=(next_cell,)
        ),
        cache,
    )
