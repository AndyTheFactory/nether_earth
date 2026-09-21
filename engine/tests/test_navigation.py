"""Tests for the non-electronic and electronic navigation policies (issue #65, M5.6)."""

from __future__ import annotations

import pytest

from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    advance_all_robot_transitions,
    validate_robot_move,
)
from nether_earth.navigation import (
    CARDINAL_DIRECTIONS,
    ELECTRONIC_NAVIGATION,
    NON_ELECTRONIC_NAVIGATION,
    ElectronicNavigation,
    NavigationDecision,
    NavigationStatus,
    NonElectronicNavigation,
    cell_is_enterable,
    navigation_policy_for,
    next_navigation_step,
    plan_route,
)
from nether_earth.reservations import apply_robot_move_batch, destination_available
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType

BIPOD_TICKS = DEFAULT_RULES.robot_move_ticks_bipod_normal


def _world(
    width: int = 10,
    height: int = 10,
    terrain_cells: dict[tuple[int, int], TerrainType] | None = None,
    blockers: tuple[Blocker, ...] = (),
) -> WorldMap:
    return WorldMap(
        map_id="navigation-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width,
            height=height,
            cells=terrain_cells or {},
            default=TerrainType.NORMAL,
        ),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _wall(cells: tuple[tuple[int, int], ...], entity_id: str = "wall") -> Blocker:
    """A single static blocker occupying ``cells`` (height 4, ground-rooted)."""
    return Blocker(
        id=EntityId(entity_id),
        components=tuple(Component(x=x, y=y, height=4) for x, y in cells),
    )


def _robot(
    entity_id: str = "robot-a",
    owner: PlayerId = PLAYER_ONE,
    x: int = 2,
    y: int = 5,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    electronics: ModuleIdentity | None = None,
    movement: RobotMoveTransition | None = None,
) -> Robot:
    build = RobotBuild(
        chassis=chassis, weapons=(ModuleIdentity.CANNON,), electronics=electronics
    )
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        movement=movement,
    )


def _state(
    robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
    tick: int = 0,
    seed: int = 0,
) -> GameState:
    return create_game_state(
        tick,
        (PLAYER_ONE, PLAYER_TWO),
        seed=seed,
        robots=list(robots),
        commanders=list(commanders),
    )


# --- The shared obstacle fixture used for the policy contrast -----------------
#
# A three-cell vertical wall at x=5, y=4..6. A robot at (2, 5) heading due east
# to (8, 5) is on a pure-X approach, so the wall sits squarely across its
# approach axis with an obvious two-cell detour around either end.

_WALL_CELLS = ((5, 4), (5, 5), (5, 6))
_START = (2, 5)
_TARGET = (8, 5)


def _walled_world() -> WorldMap:
    return _world(blockers=(_wall(_WALL_CELLS),))


def _navigating_robot(electronics: ModuleIdentity | None) -> Robot:
    return _robot(x=_START[0], y=_START[1], electronics=electronics)


def _run_to_target(
    robot: Robot,
    world: WorldMap,
    target: tuple[int, int],
    max_ticks: int = 400,
) -> tuple[NavigationStatus, tuple[int, int], int]:
    """Drive ``robot`` toward ``target`` under its own policy until it settles.

    Executes every proposed step through the real
    :func:`~nether_earth.reservations.apply_robot_move_batch` path (never a
    shortcut that writes positions directly), advancing ticks so in-flight
    moves resolve. Returns the terminal status, the robot's final cell, and
    the tick it settled on, so a test can assert both *what* happened and
    *that it stopped happening*.
    """
    state = _state((robot,))
    tick = 0
    for tick in range(max_ticks):
        state, _events = advance_all_robot_transitions(state, tick)
        current = state.robot_for(robot.entity_id)
        assert current is not None
        decision = next_navigation_step(current, target[0], target[1], state, world)
        if decision.status is NavigationStatus.ARRIVED:
            return decision.status, (current.x, current.y), tick
        if decision.status in (NavigationStatus.BLOCKED, NavigationStatus.UNREACHABLE):
            return decision.status, (current.x, current.y), tick
        if decision.status is NavigationStatus.STEP:
            assert decision.request is not None
            batch = apply_robot_move_batch(
                (decision.request,), state, world, tick
            )
            assert batch.results[0].accepted
            state = batch.state
    raise AssertionError(f"robot did not settle within {max_ticks} ticks")


# --- Interface shape ----------------------------------------------------------


def test_the_two_policies_are_distinct_implementations_of_one_interface() -> None:
    assert isinstance(NON_ELECTRONIC_NAVIGATION, NonElectronicNavigation)
    assert isinstance(ELECTRONIC_NAVIGATION, ElectronicNavigation)
    assert type(NON_ELECTRONIC_NAVIGATION) is not type(ELECTRONIC_NAVIGATION)
    for policy in (NON_ELECTRONIC_NAVIGATION, ELECTRONIC_NAVIGATION):
        assert callable(policy.next_step)


def test_navigation_policy_is_selected_by_the_electronics_module() -> None:
    assert navigation_policy_for(_robot()) is NON_ELECTRONIC_NAVIGATION
    assert (
        navigation_policy_for(_robot(electronics=ModuleIdentity.ELECTRONICS))
        is ELECTRONIC_NAVIGATION
    )


def test_next_navigation_step_dispatches_to_the_robots_own_policy() -> None:
    world = _walled_world()
    for electronics in (None, ModuleIdentity.ELECTRONICS):
        robot = _navigating_robot(electronics)
        state = _state((robot,))
        assert next_navigation_step(
            robot, _TARGET[0], _TARGET[1], state, world
        ) == navigation_policy_for(robot).next_step(
            robot, _TARGET[0], _TARGET[1], state, world
        )


def test_policies_are_stateless_singletons() -> None:
    assert NonElectronicNavigation() == NON_ELECTRONIC_NAVIGATION
    assert ElectronicNavigation() == ELECTRONIC_NAVIGATION


def test_step_decision_must_carry_a_request() -> None:
    with pytest.raises(ValueError):
        NavigationDecision(status=NavigationStatus.STEP)


def test_non_step_decision_must_not_carry_a_request() -> None:
    with pytest.raises(ValueError):
        NavigationDecision(
            status=NavigationStatus.BLOCKED,
            request=RobotMoveRequest(entity_id=EntityId("robot-a"), dx=1, dy=0),
        )


def test_cardinal_directions_are_the_four_canonically_ordered_steps() -> None:
    assert CARDINAL_DIRECTIONS == ((-1, 0), (0, -1), (0, 1), (1, 0))
    assert CARDINAL_DIRECTIONS == tuple(sorted(CARDINAL_DIRECTIONS))


# --- Policy-independent decisions ---------------------------------------------


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_both_policies_report_arrival_at_the_target_cell(
    electronics: ModuleIdentity | None,
) -> None:
    robot = _robot(x=4, y=4, electronics=electronics)
    decision = next_navigation_step(robot, 4, 4, _state((robot,)), _world())

    assert decision.status is NavigationStatus.ARRIVED
    assert decision.request is None
    assert decision.route == ()


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_both_policies_defer_while_a_move_is_already_in_flight(
    electronics: ModuleIdentity | None,
) -> None:
    robot = _robot(
        x=4,
        y=4,
        electronics=electronics,
        movement=RobotMoveTransition(
            entity_id=EntityId("robot-a"),
            from_x=4,
            from_y=4,
            to_x=5,
            to_y=4,
            started_tick=0,
            duration_ticks=BIPOD_TICKS,
        ),
    )
    decision = next_navigation_step(robot, 8, 4, _state((robot,)), _world())

    assert decision.status is NavigationStatus.MOVE_IN_PROGRESS
    assert decision.request is None


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_a_robot_mid_move_over_its_target_is_not_reported_as_arrived(
    electronics: ModuleIdentity | None,
) -> None:
    """In-flight beats arrival: authoritative position is still the origin."""
    robot = _robot(
        x=4,
        y=4,
        electronics=electronics,
        movement=RobotMoveTransition(
            entity_id=EntityId("robot-a"),
            from_x=4,
            from_y=4,
            to_x=5,
            to_y=4,
            started_tick=0,
            duration_ticks=BIPOD_TICKS,
        ),
    )
    decision = next_navigation_step(robot, 4, 4, _state((robot,)), _world())

    assert decision.status is NavigationStatus.MOVE_IN_PROGRESS


# --- Every proposed step is a step the movement executor accepts ---------------


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_a_proposed_step_is_always_accepted_by_the_movement_executor(
    electronics: ModuleIdentity | None,
) -> None:
    world = _walled_world()
    robot = _navigating_robot(electronics)
    state = _state((robot,))
    decision = next_navigation_step(robot, _TARGET[0], _TARGET[1], state, world)

    assert decision.status is NavigationStatus.STEP
    assert decision.request is not None
    assert validate_robot_move(
        decision.request, state, world, DEFAULT_RULES, destination_available
    ).accepted


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_proposed_steps_are_always_single_cardinal_moves(
    electronics: ModuleIdentity | None,
) -> None:
    world = _walled_world()
    robot = _navigating_robot(electronics)
    decision = next_navigation_step(
        robot, _TARGET[0], _TARGET[1], _state((robot,)), world
    )

    assert decision.request is not None
    assert (decision.request.dx, decision.request.dy) in CARDINAL_DIRECTIONS


# --- Non-electronic: deliberately limited local routing ------------------------


def test_non_electronic_steps_greedily_along_the_larger_axis_delta() -> None:
    robot = _robot(x=2, y=5)
    decision = NON_ELECTRONIC_NAVIGATION.next_step(
        robot, 8, 6, _state((robot,)), _world()
    )

    assert decision.request is not None
    assert (decision.request.dx, decision.request.dy) == (1, 0)


def test_non_electronic_breaks_an_equal_axis_delta_in_favor_of_x() -> None:
    robot = _robot(x=2, y=2)
    decision = NON_ELECTRONIC_NAVIGATION.next_step(
        robot, 5, 5, _state((robot,)), _world()
    )

    assert decision.request is not None
    assert (decision.request.dx, decision.request.dy) == (1, 0)


def test_non_electronic_falls_back_to_the_secondary_axis_when_primary_is_blocked() -> None:
    """Its only obstacle handling: usable solely while still off-axis."""
    # The robot's 2×2 body is (2..3, 4..5); the wall is just east of it.
    world = _world(blockers=(_wall(((4, 5),)),))
    robot = _robot(x=2, y=5)
    decision = NON_ELECTRONIC_NAVIGATION.next_step(
        robot, 8, 7, _state((robot,)), world
    )

    assert decision.request is not None
    assert (decision.request.dx, decision.request.dy) == (0, 1)


def test_non_electronic_gets_stuck_although_a_longer_valid_route_exists() -> None:
    """Locked product behavior (`_specs/open-questions.md` §5) -- not a bug."""
    world = _walled_world()
    robot = _navigating_robot(electronics=None)

    status, cell, _tick = _run_to_target(robot, world, _TARGET)

    assert status is NavigationStatus.BLOCKED
    # Nose against the wall: the 2×2 body (3..4, 4..5) ends one cell short of it.
    assert cell == (3, 5)
    # ...while a valid chassis-compatible route around the wall does exist.
    assert plan_route(_robot(x=3, y=5), _TARGET[0], _TARGET[1], _state(), world)


def test_non_electronic_never_reports_unreachable_because_it_searches_nothing() -> None:
    world = _world(blockers=(_wall(((4, 4), (4, 5), (4, 6), (4, 3), (4, 7))),))
    robot = _robot(x=2, y=5)
    decision = NON_ELECTRONIC_NAVIGATION.next_step(
        robot, 8, 5, _state((robot,)), world
    )

    assert decision.status is NavigationStatus.BLOCKED


def test_non_electronic_stalls_on_another_robots_reservation() -> None:
    """A dynamic blocker stalls it exactly as a static one does."""
    reserver = _robot(
        entity_id="robot-b",
        owner=PLAYER_TWO,
        x=4,
        y=3,
        movement=RobotMoveTransition(
            entity_id=EntityId("robot-b"),
            from_x=4,
            from_y=3,
            to_x=4,
            to_y=4,
            started_tick=0,
            duration_ticks=BIPOD_TICKS,
        ),
    )
    mover = _robot(x=2, y=5)
    state = _state((mover, reserver))
    decision = NON_ELECTRONIC_NAVIGATION.next_step(mover, 8, 5, state, _world())

    assert decision.status is NavigationStatus.BLOCKED


# --- Electronic: deterministic pathfinding and replanning ----------------------


def test_electronic_routes_around_the_obstacle_the_dumb_policy_stalls_on() -> None:
    world = _walled_world()
    robot = _navigating_robot(electronics=ModuleIdentity.ELECTRONICS)

    status, cell, _tick = _run_to_target(robot, world, _TARGET)

    assert status is NavigationStatus.ARRIVED
    assert cell == _TARGET


def test_obstacle_routing_contrast_same_fixture_divergent_outcomes() -> None:
    """The locked behavioral contrast, both policies, one identical fixture."""
    world = _walled_world()

    dumb_status, dumb_cell, _dumb_tick = _run_to_target(
        _navigating_robot(electronics=None), world, _TARGET
    )
    smart_status, smart_cell, _smart_tick = _run_to_target(
        _navigating_robot(electronics=ModuleIdentity.ELECTRONICS), world, _TARGET
    )

    assert dumb_status is NavigationStatus.BLOCKED
    assert dumb_cell != _TARGET
    assert smart_status is NavigationStatus.ARRIVED
    assert smart_cell == _TARGET


def test_electronic_route_avoids_every_blocked_cell() -> None:
    world = _walled_world()
    robot = _navigating_robot(electronics=ModuleIdentity.ELECTRONICS)
    route = plan_route(robot, _TARGET[0], _TARGET[1], _state((robot,)), world)

    assert route is not None
    assert route[-1] == _TARGET
    assert not set(route) & set(_WALL_CELLS)


def test_electronic_route_is_a_contiguous_cardinal_chain_from_the_robot() -> None:
    world = _walled_world()
    robot = _navigating_robot(electronics=ModuleIdentity.ELECTRONICS)
    route = plan_route(robot, _TARGET[0], _TARGET[1], _state((robot,)), world)

    assert route is not None
    previous = (robot.x, robot.y)
    for cell in route:
        assert (cell[0] - previous[0], cell[1] - previous[1]) in CARDINAL_DIRECTIONS
        previous = cell


def test_electronic_reports_unreachable_when_a_wall_fully_separates_the_target() -> None:
    world = _world(
        width=7,  # target anchor (5, 1): its 2×2 body is x 5..6
        height=3,
        blockers=(_wall(((3, 0), (3, 1), (3, 2))),),
    )
    robot = _robot(x=1, y=1, electronics=ModuleIdentity.ELECTRONICS)
    decision = ELECTRONIC_NAVIGATION.next_step(robot, 5, 1, _state((robot,)), world)

    assert decision.status is NavigationStatus.UNREACHABLE
    assert decision.request is None
    assert plan_route(robot, 5, 1, _state((robot,)), world) is None


def test_electronic_reports_unreachable_for_an_occupied_target_cell() -> None:
    world = _world(blockers=(_wall(((8, 5),)),))
    robot = _robot(x=2, y=5, electronics=ModuleIdentity.ELECTRONICS)

    assert ELECTRONIC_NAVIGATION.next_step(
        robot, 8, 5, _state((robot,)), world
    ).status is NavigationStatus.UNREACHABLE


def test_electronic_replans_around_a_newly_placed_blocker() -> None:
    """Replanning is re-derivation from current state, never a cached plan."""
    open_world = _world()
    robot = _robot(x=2, y=5, electronics=ModuleIdentity.ELECTRONICS)
    state = _state((robot,))

    straight = plan_route(robot, 8, 5, state, open_world)
    assert straight is not None
    assert (5, 5) in straight

    detoured = plan_route(robot, 8, 5, state, _walled_world())
    assert detoured is not None
    assert (5, 5) not in detoured
    assert len(detoured) > len(straight)


def test_electronic_replans_around_another_robots_reservation() -> None:
    world = _world()
    mover = _robot(x=2, y=5, electronics=ModuleIdentity.ELECTRONICS)
    reserver = _robot(
        entity_id="robot-b",
        owner=PLAYER_TWO,
        x=4,
        y=3,
        movement=RobotMoveTransition(
            entity_id=EntityId("robot-b"),
            from_x=4,
            from_y=3,
            to_x=4,
            to_y=4,
            started_tick=0,
            duration_ticks=BIPOD_TICKS,
        ),
    )
    state = _state((mover, reserver))
    decision = ELECTRONIC_NAVIGATION.next_step(mover, 8, 5, state, world)

    # The reserved body (4..5, 3..4) overlaps every body anchored at
    # (3..6, 4..5); the east step (3, 5) is one of them.
    assert decision.status is NavigationStatus.STEP
    assert (3, 5) not in decision.route
    assert decision.request is not None
    assert (decision.request.dx, decision.request.dy) != (1, 0)


def test_electronic_prefers_a_longer_ordinary_route_over_a_slower_rough_one() -> None:
    """Uniform-cost search charges the executor's own per-cell durations.

    Bipod: straight across eight rough cells = 8 * 32 + 24 = 280 ticks; the
    two-cell-longer ordinary detour = 11 * 24 = 264 ticks (§4 tick table).
    """
    rough = dict.fromkeys(((x, 5) for x in range(3, 11)), TerrainType.ROUGH)
    world = _world(width=16, terrain_cells=rough)
    robot = _robot(x=2, y=5, electronics=ModuleIdentity.ELECTRONICS)
    route = plan_route(robot, 11, 5, _state((robot,)), world)

    assert route is not None
    assert not set(route) & set(rough)
    assert len(route) == 11


def test_electronic_takes_the_rough_route_when_it_is_genuinely_cheapest() -> None:
    # A 2-row corridor: exactly one row of 2×2 bodies (anchor row 1).
    world = _world(
        width=5,
        height=2,
        terrain_cells={(2, 0): TerrainType.ROUGH},
    )
    robot = _robot(x=0, y=1, electronics=ModuleIdentity.ELECTRONICS)
    route = plan_route(robot, 3, 1, _state((robot,)), world)

    assert route == ((1, 1), (2, 1), (3, 1))


def test_plan_route_returns_empty_for_the_robots_own_cell() -> None:
    robot = _robot(x=4, y=4, electronics=ModuleIdentity.ELECTRONICS)

    assert plan_route(robot, 4, 4, _state((robot,)), _world()) == ()


# --- Electronics never grants a terrain permission -----------------------------


def test_electronics_never_routes_a_bipod_or_tracks_through_a_ditch() -> None:
    ditch = {(5, 4): TerrainType.DITCH, (5, 5): TerrainType.DITCH, (5, 6): TerrainType.DITCH}
    world = _world(terrain_cells=ditch)
    for chassis in (ModuleIdentity.BIPOD, ModuleIdentity.TRACKS):
        robot = _robot(
            x=2, y=5, chassis=chassis, electronics=ModuleIdentity.ELECTRONICS
        )
        route = plan_route(robot, _TARGET[0], _TARGET[1], _state((robot,)), world)

        assert route is not None
        assert not set(route) & set(ditch)


def test_electronics_cannot_reach_a_target_walled_off_by_a_ditch() -> None:
    world = _world(
        width=7,  # target anchor (5, 1): its 2×2 body is x 5..6
        height=3,
        terrain_cells=dict.fromkeys(((3, 0), (3, 1), (3, 2)), TerrainType.DITCH),
    )
    bipod = _robot(x=1, y=1, electronics=ModuleIdentity.ELECTRONICS)
    decision = ELECTRONIC_NAVIGATION.next_step(bipod, 5, 1, _state((bipod,)), world)

    assert decision.status is NavigationStatus.UNREACHABLE

    # ...whereas the same electronics on an anti-grav chassis crosses it,
    # because the *chassis* permits it -- not the electronics.
    anti_grav = _robot(
        x=1, y=1, chassis=ModuleIdentity.ANTI_GRAV, electronics=ModuleIdentity.ELECTRONICS
    )
    route = plan_route(anti_grav, 5, 1, _state((anti_grav,)), world)
    assert route is not None
    assert (3, 1) in route


def test_electronics_never_routes_a_bipod_over_a_mountain_but_tracks_may() -> None:
    """§4: bipod is blocked on mountain; tracks enter it (at 28 ticks/cell)."""
    world = _world(
        width=7,  # target anchor (5, 1): its 2×2 body is x 5..6
        height=3,
        terrain_cells=dict.fromkeys(((3, 0), (3, 1), (3, 2)), TerrainType.MOUNTAIN),
    )
    bipod = _robot(x=1, y=1, electronics=ModuleIdentity.ELECTRONICS)
    state = _state((bipod,))

    assert not cell_is_enterable(bipod, 3, 1, state, world)
    assert (
        ELECTRONIC_NAVIGATION.next_step(bipod, 5, 1, state, world).status
        is NavigationStatus.UNREACHABLE
    )
    assert next_navigation_step(bipod, 3, 1, state, world).status is not NavigationStatus.STEP

    for chassis in (ModuleIdentity.TRACKS, ModuleIdentity.ANTI_GRAV):
        robot = _robot(x=1, y=1, chassis=chassis, electronics=ModuleIdentity.ELECTRONICS)
        robot_state = _state((robot,))
        assert cell_is_enterable(robot, 3, 1, robot_state, world)
        route = plan_route(robot, 5, 1, robot_state, world)
        assert route is not None
        assert (3, 1) in route


def test_a_step_into_a_ditch_stays_rejected_by_the_executor_for_both_policies() -> None:
    world = _world(terrain_cells={(3, 5): TerrainType.DITCH})
    for electronics in (None, ModuleIdentity.ELECTRONICS):
        robot = _robot(x=2, y=5, electronics=electronics)
        state = _state((robot,))
        request = RobotMoveRequest(entity_id=robot.entity_id, dx=1, dy=0)

        assert (
            validate_robot_move(request, state, world).reason
            is MovementRejectionReason.TERRAIN_IMPASSABLE
        )
        decision = next_navigation_step(robot, 3, 5, state, world)
        assert decision.status is not NavigationStatus.STEP


# --- Shared traversability query ------------------------------------------------


def test_cell_is_enterable_honors_terrain_occupancy_commanders_and_reservations() -> None:
    """Every gate is tested on the whole 2×2 body anchored at the cell (CR002.3)."""
    reserver = _robot(
        entity_id="robot-b",
        owner=PLAYER_TWO,
        x=7,
        y=3,
        movement=RobotMoveTransition(
            entity_id=EntityId("robot-b"),
            from_x=7,
            from_y=3,
            to_x=7,
            to_y=4,
            started_tick=0,
            duration_ticks=BIPOD_TICKS,
        ),
    )
    mover = _robot(x=2, y=8)
    world = _world(
        terrain_cells={(7, 7): TerrainType.DITCH},
        blockers=(_wall(((1, 1),)),),
    )
    commander = Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=4, y=5, altitude=0)
    state = _state((mover, reserver), commanders=(commander,))

    assert cell_is_enterable(mover, 2, 5, state, world)  # plain open body
    assert not cell_is_enterable(mover, 6, 8, state, world)  # ditch under the body: chassis
    assert not cell_is_enterable(mover, 0, 2, state, world)  # blocker under the body
    assert not cell_is_enterable(mover, 8, 2, state, world)  # robot body: occupancy
    assert not cell_is_enterable(mover, 5, 6, state, world)  # commander body: blocking
    assert not cell_is_enterable(mover, 7, 5, state, world)  # reserved destination body
    assert not cell_is_enterable(mover, -1, 5, state, world)  # out of bounds
    assert not cell_is_enterable(mover, 9, 5, state, world)  # body column 10 is off the map
    assert not cell_is_enterable(mover, 2, 0, state, world)  # body row -1 is off the map
    # The robot never blocks itself: its own and overlapping bodies are free.
    assert cell_is_enterable(mover, 2, 8, state, world)
    assert cell_is_enterable(mover, 3, 8, state, world)


def test_electronic_route_avoids_a_commander_blocked_cell() -> None:
    world = _world()
    robot = _robot(x=2, y=5, electronics=ModuleIdentity.ELECTRONICS)
    commander = Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=5, y=5, altitude=0)
    state = _state((robot,), commanders=(commander,))
    route = plan_route(robot, 8, 5, state, world)

    assert route is not None
    assert (5, 5) not in route


# --- Determinism / replay safety -------------------------------------------------


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_navigation_decisions_are_identical_across_repeated_runs(
    electronics: ModuleIdentity | None,
) -> None:
    world = _walled_world()
    runs = [
        _run_to_target(_navigating_robot(electronics), world, _TARGET) for _ in range(5)
    ]

    assert len(set(runs)) == 1


@pytest.mark.parametrize("electronics", [None, ModuleIdentity.ELECTRONICS])
def test_a_navigation_decision_does_not_depend_on_robot_insertion_order(
    electronics: ModuleIdentity | None,
) -> None:
    world = _walled_world()
    mover = _robot(x=2, y=5, electronics=electronics)
    others = (
        _robot(entity_id="robot-b", owner=PLAYER_TWO, x=7, y=1),
        _robot(entity_id="robot-c", x=1, y=8),
    )
    forward = _state((mover, *others))
    reversed_order = _state((others[1], others[0], mover))

    assert next_navigation_step(
        mover, _TARGET[0], _TARGET[1], forward, world
    ) == next_navigation_step(mover, _TARGET[0], _TARGET[1], reversed_order, world)


def test_electronic_route_is_stable_across_repeated_planning() -> None:
    world = _walled_world()
    robot = _navigating_robot(electronics=ModuleIdentity.ELECTRONICS)
    state = _state((robot,))
    routes = {plan_route(robot, _TARGET[0], _TARGET[1], state, world) for _ in range(10)}

    assert len(routes) == 1


def test_symmetric_detour_tie_is_broken_canonically_not_incidentally() -> None:
    """Both wall ends are equidistant; the fixed direction order must decide."""
    world = _walled_world()
    robot = _navigating_robot(electronics=ModuleIdentity.ELECTRONICS)
    route = plan_route(robot, _TARGET[0], _TARGET[1], _state((robot,)), world)

    assert route is not None
    # CARDINAL_DIRECTIONS visits north (0, -1) before south (0, 1), so an
    # otherwise equal-cost detour resolves to the northern end of the wall.
    assert (5, 3) in route
    assert (5, 7) not in route


def test_navigation_is_deterministic_under_a_different_match_seed() -> None:
    """Navigation draws no randomness, so the match seed cannot perturb it."""
    world = _walled_world()
    robot = _navigating_robot(electronics=ModuleIdentity.ELECTRONICS)

    assert next_navigation_step(
        robot, _TARGET[0], _TARGET[1], _state((robot,), seed=0), world
    ) == next_navigation_step(
        robot, _TARGET[0], _TARGET[1], _state((robot,), seed=987654321), world
    )
