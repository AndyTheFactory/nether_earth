"""Tests for autonomous robot orders, target selection, and engagement intent (issue #64, M5.5)."""

from __future__ import annotations

import pytest

from nether_earth.capture import StructureOwnership
from nether_earth.commander import Commander, CommanderMode
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.navigation import NavigationStatus, next_navigation_step
from nether_earth.orders import (
    MAX_ORDER_DISTANCE_MILES,
    Advance,
    EngagementIntent,
    EngagementTargetKind,
    OrderStatus,
    Retreat,
    RobotEngagementIntentEvent,
    RobotOrderChangedEvent,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
    apply_order_evaluations,
    apply_set_robot_order,
    engagement_intent_for,
    evaluate_order,
    evaluate_orders,
    order_is_valid,
    select_capture_target,
    select_destroy_target,
)
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import CELLS_PER_MILE, DEFAULT_RULES, miles_to_cells
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

NEUTRAL_FACTORY = EntityId("factory-neutral")
ENEMY_FACTORY = EntityId("factory-enemy")
OWN_FACTORY = EntityId("factory-own")
ENEMY_WAR_BASE = EntityId("warbase-enemy")

NEUTRAL_FACTORY_CAPTURE_CELL = (6, 2)
ENEMY_FACTORY_CAPTURE_CELL = (8, 8)
OWN_FACTORY_CAPTURE_CELL = (1, 8)
ENEMY_WAR_BASE_CAPTURE_CELL = (9, 1)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _world(
    width: int = 20,
    height: int = 12,
    *,
    factories: bool = True,
    war_bases: bool = True,
    blockers: tuple[Blocker, ...] = (),
    terrain_cells: dict[tuple[int, int], TerrainType] | None = None,
) -> WorldMap:
    """A battlefield with one neutral, one enemy, and one friendly factory.

    Structures are deliberately placed far from the origin so a robot
    spawned near ``(0, 0)`` has a nonzero distance to every candidate and
    target-selection ties never arise by accident.
    """
    built_factories: tuple[Factory, ...] = ()
    built_war_bases: tuple[WarBase, ...] = ()
    points: tuple[InteractionPoint, ...] = ()

    if factories:
        built_factories = (
            Factory(
                id=NEUTRAL_FACTORY,
                components=(Component(x=6, y=1, height=3),),
                factory_type=FactoryType.CHASSIS,
                owner=None,
            ),
            Factory(
                id=ENEMY_FACTORY,
                components=(Component(x=8, y=9, height=3),),
                factory_type=FactoryType.CANNON,
                owner=PLAYER_TWO,
            ),
            Factory(
                id=OWN_FACTORY,
                components=(Component(x=1, y=9, height=3),),
                factory_type=FactoryType.PHASER,
                owner=PLAYER_ONE,
            ),
        )
        points += (
            InteractionPoint(
                id="neutral-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=NEUTRAL_FACTORY,
                footprint=Footprint(cells=frozenset({NEUTRAL_FACTORY_CAPTURE_CELL})),
            ),
            InteractionPoint(
                id="enemy-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=ENEMY_FACTORY,
                footprint=Footprint(cells=frozenset({ENEMY_FACTORY_CAPTURE_CELL})),
            ),
            InteractionPoint(
                id="own-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=OWN_FACTORY,
                footprint=Footprint(cells=frozenset({OWN_FACTORY_CAPTURE_CELL})),
            ),
        )

    if war_bases:
        built_war_bases = (
            WarBase(
                id=ENEMY_WAR_BASE,
                components=(Component(x=10, y=0, height=3),),
                owner=PLAYER_TWO,
            ),
        )
        points += (
            InteractionPoint(
                id="enemy-warbase-capture",
                kind=InteractionKind.WARBASE_CAPTURE,
                structure_id=ENEMY_WAR_BASE,
                footprint=Footprint(cells=frozenset({ENEMY_WAR_BASE_CAPTURE_CELL})),
            ),
        )

    return WorldMap(
        map_id="orders-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width, height=height, cells=terrain_cells or {}, default=TerrainType.NORMAL
        ),
        war_bases=built_war_bases,
        factories=built_factories,
        blockers=blockers,
        interaction_points=points,
        spawn_positions={},
    )


def _empty_world(width: int = 20, height: int = 12, **kwargs: object) -> WorldMap:
    return _world(width, height, factories=False, war_bases=False, **kwargs)  # type: ignore[arg-type]


def _robot(
    entity_id: str = "robot-a",
    owner: PlayerId = PLAYER_ONE,
    x: int = 2,
    y: int = 5,
    *,
    chassis: ModuleIdentity = ModuleIdentity.TRACKS,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    electronics: ModuleIdentity | None = None,
    order: object = None,
    movement: RobotMoveTransition | None = None,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons, electronics=electronics)
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
        order=order,  # type: ignore[arg-type]
    )


def _state(
    robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
    ownership: tuple[StructureOwnership, ...] = (),
    tick: int = 0,
) -> GameState:
    state = create_game_state(
        tick, (PLAYER_ONE, PLAYER_TWO), robots=list(robots), commanders=list(commanders)
    )
    if ownership:
        state = state.with_structure_ownership(ownership)
    return state


def _wall(cells: tuple[tuple[int, int], ...], entity_id: str = "wall") -> Blocker:
    return Blocker(
        id=EntityId(entity_id),
        components=tuple(Component(x=x, y=y, height=4) for x, y in cells),
    )


# --------------------------------------------------------------------------
# Miles/cells conversion (locked: 1 mile = 2 cells, 0-50 miles = 0-100 cells)
# --------------------------------------------------------------------------


def test_locked_mile_conversion_is_two_cells_per_mile() -> None:
    assert CELLS_PER_MILE == 2
    assert miles_to_cells(0) == 0
    assert miles_to_cells(1) == 2
    assert miles_to_cells(MAX_ORDER_DISTANCE_MILES) == 100


def test_miles_to_cells_rejects_negative_distance() -> None:
    with pytest.raises(ValueError):
        miles_to_cells(-1)


def test_advance_goal_uses_the_shared_helper_for_the_full_locked_range() -> None:
    """Advance N binds a goal exactly ``miles_to_cells(N)`` cells east."""
    world = _empty_world(width=200)
    for miles in (0, 1, 7, MAX_ORDER_DISTANCE_MILES):
        robot = _robot(x=0, y=5, order=Advance(miles))
        evaluation = evaluate_order(robot, _state((robot,)), world)
        assert evaluation is not None
        if miles == 0:
            # Zero miles is instantly complete, not an impossible order.
            assert evaluation.status is OrderStatus.COMPLETED
            assert evaluation.order == StopAndDefend()
            continue
        assert isinstance(evaluation.order, Advance)
        assert evaluation.order.target_x == miles_to_cells(miles)


def test_retreat_goal_is_the_westward_mirror_of_advance() -> None:
    world = _empty_world(width=200)
    robot = _robot(x=150, y=5, order=Retreat(20))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert isinstance(evaluation.order, Retreat)
    assert evaluation.order.target_x == 150 - miles_to_cells(20)


# --------------------------------------------------------------------------
# Structural validation and fallback
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "order",
    [
        StopAndDefend(),
        Advance(0),
        Advance(MAX_ORDER_DISTANCE_MILES),
        Retreat(1),
        SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY),
        SearchDestroy(SearchDestroyTarget.ROBOT),
    ],
)
def test_every_locked_order_is_structurally_valid(order: object) -> None:
    assert order_is_valid(order)


@pytest.mark.parametrize(
    "order",
    [
        Advance(-1),
        Advance(MAX_ORDER_DISTANCE_MILES + 1),
        Retreat(-5),
        Retreat(101),
        Advance(True),  # bool must not sneak through as 1 mile
        Advance("10"),
        SearchCapture("neutral_factory"),
        SearchDestroy("robot"),
        "stop",
        None,
        object(),
    ],
)
def test_structurally_invalid_orders_are_rejected(order: object) -> None:
    assert not order_is_valid(order)


def test_invalid_order_falls_back_to_stop_and_defend() -> None:
    robot = _robot(order=Advance(MAX_ORDER_DISTANCE_MILES + 1))
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.order == StopAndDefend()
    assert evaluation.status is OrderStatus.FALLBACK
    assert evaluation.request is None
    assert evaluation.changed


def test_advance_from_the_eastern_edge_is_impossible_and_falls_back() -> None:
    world = _empty_world(width=10)
    # 2×2 bodies (CR002.3): the easternmost anchor column is width - 2.
    robot = _robot(x=8, y=5, order=Advance(10))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.FALLBACK
    assert evaluation.order == StopAndDefend()


def test_retreat_from_the_western_edge_is_impossible_and_falls_back() -> None:
    robot = _robot(x=0, y=5, order=Retreat(10))
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.FALLBACK


def test_advance_beyond_the_map_clamps_to_the_edge_rather_than_failing() -> None:
    world = _empty_world(width=10)
    robot = _robot(x=4, y=5, order=Advance(MAX_ORDER_DISTANCE_MILES))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert isinstance(evaluation.order, Advance)
    # The last anchor column a 2×2 body can stand on (CR002.3).
    assert evaluation.order.target_x == world.width - 2


def test_zero_mile_advance_completes_immediately() -> None:
    robot = _robot(x=4, y=5, order=Advance(0))
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.COMPLETED
    assert evaluation.order == StopAndDefend()


# --------------------------------------------------------------------------
# Advance/Retreat lifecycle
# --------------------------------------------------------------------------


def test_advance_binds_its_goal_once_and_never_rebinds() -> None:
    """A rebound goal would make the robot advance forever."""
    world = _empty_world(width=40)
    robot = _robot(x=0, y=5, order=Advance(3))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    bound = evaluation.order
    assert isinstance(bound, Advance)
    assert bound.target_x == 6

    # Same order, robot now two cells further east: the goal must not move.
    moved = _robot(x=2, y=5, order=bound)
    again = evaluate_order(moved, _state((moved,)), world)
    assert again is not None
    assert again.order == bound
    assert not again.changed


def test_advance_produces_an_eastward_move_request() -> None:
    world = _empty_world(width=40)
    robot = _robot(x=0, y=5, order=Advance(3))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.request is not None
    assert (evaluation.request.dx, evaluation.request.dy) == (1, 0)
    assert evaluation.request.entity_id == robot.entity_id


def test_retreat_produces_a_westward_move_request() -> None:
    world = _empty_world(width=40)
    robot = _robot(x=20, y=5, order=Retreat(3))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.request is not None
    assert (evaluation.request.dx, evaluation.request.dy) == (-1, 0)


def test_advance_completes_into_stop_and_defend_on_arrival() -> None:
    """The locked 'Advance N ... then Stop & Defend' transition."""
    world = _empty_world(width=40)
    robot = _robot(x=6, y=5, order=Advance(3, target_x=6))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.COMPLETED
    assert evaluation.order == StopAndDefend()
    assert evaluation.request is None


def test_retreat_completes_into_stop_and_defend_on_arrival() -> None:
    robot = _robot(x=4, y=5, order=Retreat(3, target_x=4))
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.COMPLETED
    assert evaluation.order == StopAndDefend()


def test_advance_completes_regardless_of_row_detours() -> None:
    """Advance is a column goal, so a detour north does not block completion."""
    robot = _robot(x=6, y=0, order=Advance(3, target_x=6))
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.COMPLETED


def test_advance_with_a_move_already_in_flight_submits_no_second_request() -> None:
    world = _empty_world(width=40)
    transition = RobotMoveTransition(
        entity_id=EntityId("robot-a"),
        from_x=0,
        from_y=5,
        to_x=1,
        to_y=5,
        started_tick=0,
        duration_ticks=DEFAULT_RULES.robot_move_ticks_tracks_normal,
    )
    robot = _robot(x=0, y=5, order=Advance(3, target_x=6), movement=transition)
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.request is None


def test_a_non_electronic_robot_blocked_by_a_wall_keeps_its_order() -> None:
    """The locked 'may get stuck' behavior is not an impossible order."""
    wall = _wall(((5, 4), (5, 5), (5, 6)))
    world = _empty_world(width=20, blockers=(wall,))
    robot = _robot(x=3, y=5, order=Advance(4, target_x=12))  # body (3..4, 4..5)
    state = _state((robot,))

    decision = next_navigation_step(robot, 12, 5, state, world)
    assert decision.status is NavigationStatus.BLOCKED

    evaluation = evaluate_order(robot, state, world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.order == robot.order
    assert evaluation.request is None


def test_an_electronic_robot_with_no_route_falls_back() -> None:
    """UNREACHABLE is a *proof* of impossibility, so the order is abandoned."""
    # Walls every side of the robot's 2×2 body (3..4, 4..5), CR002.3.
    box = _wall(
        ((2, 4), (2, 5), (5, 4), (5, 5), (3, 3), (4, 3), (3, 6), (4, 6)), entity_id="box"
    )
    world = _empty_world(width=20, blockers=(box,))
    robot = _robot(
        x=3, y=5, electronics=ModuleIdentity.ELECTRONICS, order=Advance(4, target_x=11)
    )
    state = _state((robot,))

    decision = next_navigation_step(robot, 11, 5, state, world)
    assert decision.status is NavigationStatus.UNREACHABLE

    evaluation = evaluate_order(robot, state, world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.FALLBACK
    assert evaluation.order == StopAndDefend()


# --------------------------------------------------------------------------
# Stop & Defend
# --------------------------------------------------------------------------


def test_stop_and_defend_never_moves() -> None:
    enemy = _robot("robot-enemy", PLAYER_TWO, x=3, y=5)
    robot = _robot(x=2, y=5, order=StopAndDefend())
    evaluation = evaluate_order(robot, _state((robot, enemy)), _empty_world())
    assert evaluation is not None
    assert evaluation.request is None
    assert evaluation.status is OrderStatus.ACTIVE
    assert not evaluation.changed


def test_stop_and_defend_engages_the_nearest_enemy_robot() -> None:
    near = _robot("robot-enemy-near", PLAYER_TWO, x=4, y=5)
    far = _robot("robot-enemy-far", PLAYER_TWO, x=9, y=5)
    robot = _robot(x=2, y=5, order=StopAndDefend())
    evaluation = evaluate_order(robot, _state((robot, near, far)), _empty_world())
    assert evaluation is not None
    assert evaluation.intent is not None
    assert evaluation.intent.target_id == near.entity_id
    assert evaluation.intent.target_kind is EngagementTargetKind.ROBOT
    assert evaluation.intent.distance_cells == 2


def test_stop_and_defend_never_engages_a_friendly_robot() -> None:
    friend = _robot("robot-friend", PLAYER_ONE, x=3, y=5)
    robot = _robot(x=2, y=5, order=StopAndDefend())
    evaluation = evaluate_order(robot, _state((robot, friend)), _empty_world())
    assert evaluation is not None
    assert evaluation.intent is None


def test_stop_and_defend_without_enemies_produces_no_intent() -> None:
    robot = _robot(x=2, y=5, order=StopAndDefend())
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.intent is None
    assert evaluation.status is OrderStatus.ACTIVE


def test_stop_and_defend_never_completes() -> None:
    enemy = _robot("robot-enemy", PLAYER_TWO, x=3, y=5)
    robot = _robot(x=2, y=5, order=StopAndDefend())
    state = _state((robot, enemy))
    for _ in range(5):
        evaluation = evaluate_order(robot, state, _empty_world())
        assert evaluation is not None
        assert evaluation.order == StopAndDefend()
        assert evaluation.status is OrderStatus.ACTIVE


def test_defensive_target_selection_breaks_distance_ties_by_entity_id() -> None:
    west = _robot("robot-enemy-b", PLAYER_TWO, x=1, y=5)
    east = _robot("robot-enemy-a", PLAYER_TWO, x=3, y=5)
    robot = _robot(x=2, y=5, order=StopAndDefend())
    evaluation = evaluate_order(robot, _state((robot, west, east)), _empty_world())
    assert evaluation is not None
    assert evaluation.intent is not None
    assert evaluation.intent.target_id == EntityId("robot-enemy-a")


# --------------------------------------------------------------------------
# Search & Capture target selection
# --------------------------------------------------------------------------


def test_search_capture_neutral_factory_selects_the_capture_footprint_cell() -> None:
    world = _world()
    robot = _robot(x=0, y=0)
    selected = select_capture_target(
        robot, SearchCaptureTarget.NEUTRAL_FACTORY, _state((robot,)), world
    )
    assert selected == (NEUTRAL_FACTORY, NEUTRAL_FACTORY_CAPTURE_CELL)


def test_search_capture_enemy_factory_ignores_neutral_and_own_factories() -> None:
    world = _world()
    robot = _robot(x=0, y=0)
    selected = select_capture_target(
        robot, SearchCaptureTarget.ENEMY_FACTORY, _state((robot,)), world
    )
    assert selected == (ENEMY_FACTORY, ENEMY_FACTORY_CAPTURE_CELL)


def test_search_capture_enemy_war_base_uses_war_base_capture_points() -> None:
    world = _world()
    robot = _robot(x=0, y=0)
    selected = select_capture_target(
        robot, SearchCaptureTarget.ENEMY_WAR_BASE, _state((robot,)), world
    )
    assert selected == (ENEMY_WAR_BASE, ENEMY_WAR_BASE_CAPTURE_CELL)


def test_search_capture_respects_runtime_ownership_overrides() -> None:
    """A factory captured earlier in the match is no longer a neutral target."""
    world = _world()
    robot = _robot(x=0, y=0)
    state = _state(
        (robot,), ownership=(StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_TWO),)
    )
    assert select_capture_target(robot, SearchCaptureTarget.NEUTRAL_FACTORY, state, world) is None
    # ... it has become an enemy factory instead, and is now the nearest one.
    selected = select_capture_target(robot, SearchCaptureTarget.ENEMY_FACTORY, state, world)
    assert selected == (NEUTRAL_FACTORY, NEUTRAL_FACTORY_CAPTURE_CELL)


def test_search_capture_never_targets_a_structure_the_robot_already_owns() -> None:
    world = _world()
    robot = _robot(x=0, y=8)  # closest to OWN_FACTORY's capture cell
    selected = select_capture_target(
        robot, SearchCaptureTarget.ENEMY_FACTORY, _state((robot,)), world
    )
    assert selected is not None
    assert selected[0] != OWN_FACTORY


def test_search_capture_selects_the_nearest_candidate() -> None:
    world = _world()
    # Two enemy factories: ENEMY_FACTORY at (8, 8) and the neutral one, flipped
    # to PLAYER_TWO, at (6, 2). A robot at (6, 4) is nearer the latter.
    robot = _robot(x=6, y=4)
    state = _state(
        (robot,), ownership=(StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_TWO),)
    )
    selected = select_capture_target(robot, SearchCaptureTarget.ENEMY_FACTORY, state, world)
    assert selected == (NEUTRAL_FACTORY, NEUTRAL_FACTORY_CAPTURE_CELL)


def test_search_capture_ignores_structures_with_no_declared_capture_points() -> None:
    """Not capturable on this map is a valid state, never an error."""
    world = _world()
    world = WorldMap(
        map_id=world.map_id,
        version=world.version,
        width=world.width,
        height=world.height,
        terrain=world.terrain,
        war_bases=world.war_bases,
        factories=world.factories,
        blockers=world.blockers,
        interaction_points=(),
        spawn_positions=world.spawn_positions,
    )
    robot = _robot(x=0, y=0)
    assert select_capture_target(robot, SearchCaptureTarget.NEUTRAL_FACTORY, _state((robot,)), world) is None


def test_search_capture_with_no_candidate_keeps_the_order_and_idles_defensively() -> None:
    """CR003.2: no target keeps the order; the robot holds with the defensive intent."""
    order = SearchCapture(SearchCaptureTarget.ENEMY_WAR_BASE)
    robot = _robot(order=order)
    enemy = _robot("robot-enemy", PLAYER_TWO, x=5, y=5)
    evaluation = evaluate_order(robot, _state((robot, enemy)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.order == order
    assert not evaluation.changed
    assert evaluation.request is None
    assert evaluation.intent is not None
    assert evaluation.intent.target_id == enemy.entity_id


def test_search_capture_moves_toward_its_target() -> None:
    world = _world()
    robot = _robot(x=0, y=2, order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.request is not None
    assert (evaluation.request.dx, evaluation.request.dy) == (1, 0)
    assert evaluation.order == SearchCapture(
        SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=NEUTRAL_FACTORY
    )
    assert evaluation.changed


def test_search_capture_holds_on_the_footprint_of_an_uncaptured_target() -> None:
    """CR003.2: the order stays ACTIVE and the robot holds so capture.py keeps counting."""
    world = _world()
    order = SearchCapture(SearchCaptureTarget.ENEMY_WAR_BASE, structure_id=ENEMY_WAR_BASE)
    robot = _robot(
        x=ENEMY_WAR_BASE_CAPTURE_CELL[0], y=ENEMY_WAR_BASE_CAPTURE_CELL[1], order=order
    )
    enemy = _robot("robot-enemy", PLAYER_TWO, x=12, y=5)
    evaluation = evaluate_order(robot, _state((robot, enemy)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.order == order
    assert evaluation.request is None
    assert evaluation.intent is not None
    assert evaluation.intent.target_id == enemy.entity_id


def test_search_capture_keeps_its_target_while_ownership_still_matches() -> None:
    """Lb289: a stored target that still matches is kept even if another is nearer."""
    world = _world()
    # Both factories are enemy-owned; the robot at (6, 4) is nearer (6, 2), but
    # it already targets ENEMY_FACTORY at (8, 8) and keeps heading there.
    order = SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, structure_id=ENEMY_FACTORY)
    robot = _robot(x=6, y=4, order=order)
    state = _state(
        (robot,), ownership=(StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_TWO),)
    )
    evaluation = evaluate_order(robot, state, world)
    assert evaluation is not None and evaluation.request is not None
    assert evaluation.order == order
    assert (evaluation.request.dx, evaluation.request.dy) in {(1, 0), (0, 1)}


def test_search_capture_retargets_when_its_target_changes_hands() -> None:
    """Once the stored target no longer matches, the next evaluation retargets."""
    world = _world()
    order = SearchCapture(SearchCaptureTarget.ENEMY_FACTORY)
    robot = _robot(x=6, y=4, order=order)
    state = _state(
        (robot,), ownership=(StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_TWO),)
    )
    first = evaluate_order(robot, state, world)
    assert first is not None and first.request is not None
    assert (first.request.dx, first.request.dy) == (0, -1)  # heading to (6, 2)
    assert first.order == SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, NEUTRAL_FACTORY)

    # The nearer factory changes hands to us: the next evaluation retargets
    # the remaining enemy factory at (8, 8), heading south instead.
    robot = robot.with_order(first.order)
    retaken = _state(
        (robot,), ownership=(StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_ONE),)
    )
    second = evaluate_order(robot, retaken, world)
    assert second is not None and second.request is not None
    assert (second.request.dx, second.request.dy) == (0, 1)
    assert second.status is OrderStatus.ACTIVE
    assert second.order == SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, ENEMY_FACTORY)


def test_search_capture_skips_a_target_another_robot_with_the_same_order_holds() -> None:
    """Lb36c: targets are exclusive between same-owner robots with the same order."""
    world = _world()
    ownership = (StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_TWO),)
    holder = _robot(
        "robot-b",
        x=0,
        y=0,
        order=SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, structure_id=NEUTRAL_FACTORY),
    )
    robot = _robot(x=6, y=4, order=SearchCapture(SearchCaptureTarget.ENEMY_FACTORY))
    evaluation = evaluate_order(robot, _state((robot, holder), ownership=ownership), world)
    assert evaluation is not None
    assert evaluation.order == SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, ENEMY_FACTORY)

    # A different order type, or another player's robot, claims nothing.
    for other in (
        _robot("robot-b", x=0, y=0, order=SearchCapture(
            SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=NEUTRAL_FACTORY
        )),
        _robot("robot-b", PLAYER_TWO, x=0, y=0, order=SearchCapture(
            SearchCaptureTarget.ENEMY_FACTORY, structure_id=NEUTRAL_FACTORY
        )),
    ):
        evaluation = evaluate_order(robot, _state((robot, other), ownership=ownership), world)
        assert evaluation is not None
        assert evaluation.order == SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, NEUTRAL_FACTORY)


def test_search_capture_assignment_clears_a_pre_bound_target() -> None:
    robot = _robot()
    command = SetRobotOrderCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=robot.entity_id,
        order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=NEUTRAL_FACTORY),
    )
    new_state, event = apply_set_robot_order(command, _state((robot,)), tick=0)
    assert event is not None
    assert new_state.robots[0].order == SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)


def test_search_capture_produces_no_engagement_intent() -> None:
    world = _world()
    robot = _robot(x=0, y=2, order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.intent is None


# --------------------------------------------------------------------------
# Search & Destroy target selection and engagement intent
# --------------------------------------------------------------------------


def test_search_destroy_robot_selects_the_nearest_enemy_robot() -> None:
    near = _robot("robot-enemy-near", PLAYER_TWO, x=5, y=5)
    far = _robot("robot-enemy-far", PLAYER_TWO, x=12, y=5)
    robot = _robot(x=2, y=5)
    selected = select_destroy_target(
        robot, SearchDestroyTarget.ROBOT, _state((robot, near, far)), _empty_world()
    )
    assert selected == (near.entity_id, (5, 5))


def test_search_destroy_robot_never_selects_a_friendly_robot() -> None:
    friend = _robot("robot-friend", PLAYER_ONE, x=3, y=5)
    robot = _robot(x=2, y=5)
    assert (
        select_destroy_target(
            robot, SearchDestroyTarget.ROBOT, _state((robot, friend)), _empty_world()
        )
        is None
    )


def test_search_destroy_robot_breaks_distance_ties_by_entity_id() -> None:
    west = _robot("robot-enemy-b", PLAYER_TWO, x=1, y=5)
    east = _robot("robot-enemy-a", PLAYER_TWO, x=3, y=5)
    robot = _robot(x=2, y=5)
    selected = select_destroy_target(
        robot, SearchDestroyTarget.ROBOT, _state((robot, west, east)), _empty_world()
    )
    assert selected is not None
    assert selected[0] == EntityId("robot-enemy-a")


def test_search_destroy_structure_targets_neutral_and_enemy_but_not_own() -> None:
    world = _world()
    robot = _robot(x=0, y=9)  # nearest to OWN_FACTORY at (1, 9)
    selected = select_destroy_target(
        robot, SearchDestroyTarget.FACTORY, _state((robot,)), world
    )
    assert selected is not None
    assert selected[0] != OWN_FACTORY


def test_search_destroy_closes_on_its_target_and_produces_intent() -> None:
    enemy = _robot("robot-enemy", PLAYER_TWO, x=6, y=5)
    robot = _robot(x=2, y=5, order=SearchDestroy(SearchDestroyTarget.ROBOT))
    evaluation = evaluate_order(robot, _state((robot, enemy)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.request is not None
    assert (evaluation.request.dx, evaluation.request.dy) == (1, 0)
    assert evaluation.intent is not None
    assert evaluation.intent.target_id == enemy.entity_id
    assert evaluation.intent.distance_cells == 4


def test_search_destroy_with_no_target_falls_back_to_stop_and_defend() -> None:
    robot = _robot(order=SearchDestroy(SearchDestroyTarget.ROBOT))
    evaluation = evaluate_order(robot, _state((robot,)), _empty_world())
    assert evaluation is not None
    assert evaluation.status is OrderStatus.FALLBACK


def test_search_destroy_reselects_when_its_target_is_removed() -> None:
    order = SearchDestroy(SearchDestroyTarget.ROBOT)
    robot = _robot(x=2, y=5, order=order)
    near = _robot("robot-enemy-near", PLAYER_TWO, x=6, y=5)
    far = _robot("robot-enemy-far", PLAYER_TWO, x=2, y=10)

    first = evaluate_order(robot, _state((robot, near, far)), _empty_world())
    assert first is not None and first.intent is not None
    assert first.intent.target_id == near.entity_id

    second = evaluate_order(robot, _state((robot, far)), _empty_world())
    assert second is not None and second.intent is not None
    assert second.intent.target_id == far.entity_id
    assert second.order == order


def test_search_destroy_structure_requires_a_nuclear_module() -> None:
    """Issue #64: structures only produce valid intent with suitable capability."""
    world = _world()
    robot = _robot(x=0, y=0, order=SearchDestroy(SearchDestroyTarget.FACTORY))
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.FALLBACK
    assert evaluation.intent is None


def test_search_destroy_structure_navigates_to_the_capture_cell_without_intent() -> None:
    """OQ §19: no structure intent exists before the carrier reaches its target cell."""
    world = _world()
    robot = _robot(
        x=0,
        y=0,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        order=SearchDestroy(SearchDestroyTarget.WAR_BASE),
    )
    selected = select_destroy_target(robot, SearchDestroyTarget.WAR_BASE, _state((robot,)), world)
    assert selected == (ENEMY_WAR_BASE, ENEMY_WAR_BASE_CAPTURE_CELL)
    # The same target cell a Search & Capture navigates to.
    capture = select_capture_target(
        robot, SearchCaptureTarget.ENEMY_WAR_BASE, _state((robot,)), world
    )
    assert capture == selected

    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.request is not None
    assert evaluation.intent is None


def test_search_destroy_structure_one_cell_short_has_no_intent() -> None:
    world = _world()
    x, y = ENEMY_FACTORY_CAPTURE_CELL
    robot = _robot(
        x=x - 1,
        y=y,
        weapons=(ModuleIdentity.NUCLEAR,),
        order=SearchDestroy(SearchDestroyTarget.FACTORY),
    )
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.intent is None


@pytest.mark.parametrize(
    ("target", "kind", "structure_id", "cell"),
    [
        (
            SearchDestroyTarget.WAR_BASE,
            EngagementTargetKind.WAR_BASE,
            ENEMY_WAR_BASE,
            ENEMY_WAR_BASE_CAPTURE_CELL,
        ),
        (
            SearchDestroyTarget.FACTORY,
            EngagementTargetKind.FACTORY,
            ENEMY_FACTORY,
            ENEMY_FACTORY_CAPTURE_CELL,
        ),
    ],
)
def test_search_destroy_structure_completes_with_nuclear_intent_on_arrival(
    target: SearchDestroyTarget,
    kind: EngagementTargetKind,
    structure_id: EntityId,
    cell: tuple[int, int],
) -> None:
    world = _world()
    robot = _robot(
        x=cell[0],
        y=cell[1],
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        order=SearchDestroy(target),
    )
    evaluation = evaluate_order(robot, _state((robot,)), world)
    assert evaluation is not None
    assert evaluation.status is OrderStatus.COMPLETED
    assert evaluation.order == StopAndDefend()
    assert evaluation.request is None
    assert evaluation.intent is not None
    assert evaluation.intent.target_id == structure_id
    assert evaluation.intent.target_kind is kind
    assert evaluation.intent.distance_cells == 0
    # Only the nuke is capable against a structure; the cannon is filtered out.
    assert evaluation.intent.weapons == (ModuleIdentity.NUCLEAR,)


def test_search_destroy_structure_skips_structures_without_a_capture_cell() -> None:
    world = _world()
    world = WorldMap(
        map_id=world.map_id,
        version=world.version,
        width=world.width,
        height=world.height,
        terrain=world.terrain,
        war_bases=world.war_bases,
        factories=world.factories,
        blockers=world.blockers,
        interaction_points=tuple(
            point for point in world.interaction_points if point.structure_id != ENEMY_WAR_BASE
        ),
        spawn_positions={},
    )
    robot = _robot(x=0, y=0, weapons=(ModuleIdentity.NUCLEAR,))
    assert (
        select_destroy_target(robot, SearchDestroyTarget.WAR_BASE, _state((robot,)), world)
        is None
    )


def test_engagement_intent_lists_every_capable_weapon_in_canonical_order() -> None:
    enemy = _robot("robot-enemy", PLAYER_TWO, x=4, y=5)
    robot = _robot(
        x=2,
        y=5,
        weapons=(ModuleIdentity.PHASER, ModuleIdentity.CANNON),
        order=StopAndDefend(),
    )
    evaluation = evaluate_order(robot, _state((robot, enemy)), _empty_world())
    assert evaluation is not None
    assert evaluation.intent is not None
    assert evaluation.intent.weapons == (ModuleIdentity.CANNON, ModuleIdentity.PHASER)


def test_engagement_intent_for_returns_none_without_a_capable_weapon() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,))
    assert (
        engagement_intent_for(robot, EngagementTargetKind.FACTORY, ENEMY_FACTORY, 8, 9) is None
    )


def test_engagement_intent_rejects_an_empty_weapon_list() -> None:
    with pytest.raises(ValueError):
        EngagementIntent(
            robot_id=EntityId("robot-a"),
            player=PLAYER_ONE,
            target_kind=EngagementTargetKind.ROBOT,
            target_id=EntityId("robot-b"),
            target_x=1,
            target_y=1,
            distance_cells=1,
            weapons=(),
        )


def test_engagement_intent_reports_distance_rather_than_deciding_range() -> None:
    """Weapon range is an unresolved M6 concern; M5 must not invent one."""
    enemy = _robot("robot-enemy", PLAYER_TWO, x=19, y=11)
    robot = _robot(x=0, y=0, order=StopAndDefend())
    evaluation = evaluate_order(robot, _state((robot, enemy)), _empty_world())
    assert evaluation is not None
    assert evaluation.intent is not None
    # Produced even at maximum separation: nothing here thresholds on range.
    assert evaluation.intent.distance_cells == 30


# --------------------------------------------------------------------------
# Fleet evaluation
# --------------------------------------------------------------------------


def test_robots_without_an_order_are_skipped() -> None:
    robot = _robot(order=None)
    assert evaluate_order(robot, _state((robot,)), _empty_world()) is None
    assert evaluate_orders(_state((robot,)), _empty_world()) == ()


def test_evaluate_orders_walks_robots_in_canonical_entity_id_order() -> None:
    world = _empty_world(width=40)
    robots = tuple(
        _robot(f"robot-{name}", x=3 * index, y=index + 1, order=Advance(2))
        for index, name in enumerate(("c", "a", "b"))
    )
    evaluations = evaluate_orders(_state(robots), world)
    assert [evaluation.robot_id.value for evaluation in evaluations] == [
        "robot-a",
        "robot-b",
        "robot-c",
    ]


def test_evaluate_orders_is_independent_of_robot_submission_order() -> None:
    world = _empty_world(width=40)
    robots = tuple(
        _robot(f"robot-{name}", x=3 * index, y=index + 1, order=Advance(2))
        for index, name in enumerate(("c", "a", "b"))
    )
    forward = evaluate_orders(_state(robots), world)
    backward = evaluate_orders(_state(tuple(reversed(robots))), world)
    assert forward == backward


def test_a_directly_controlled_robot_is_not_steered_by_its_order() -> None:
    world = _empty_world(width=40)
    robot = _robot(x=0, y=5, order=Advance(3))
    commander = Commander(
        player_id=PLAYER_ONE,
        x=0,
        y=5,
        altitude=0,
        mode=CommanderMode.DOCKED,
        docked_robot_id=robot.entity_id,
    )
    assert evaluate_orders(_state((robot,), commanders=(commander,)), world) == ()
    # The order itself is retained, so it resumes after undocking.
    assert evaluate_orders(_state((robot,)), world) != ()


def test_evaluate_orders_never_mutates_the_state_it_reads() -> None:
    world = _empty_world(width=40)
    robot = _robot(x=0, y=5, order=Advance(3))
    state = _state((robot,))
    before = state
    evaluate_orders(state, world)
    assert state == before
    assert state.robots[0].order == Advance(3)


# --------------------------------------------------------------------------
# Applying evaluations
# --------------------------------------------------------------------------


def test_apply_order_evaluations_writes_bound_goals_back_to_the_robot() -> None:
    world = _empty_world(width=40)
    robot = _robot(x=0, y=5, order=Advance(3))
    state = _state((robot,))
    evaluations = evaluate_orders(state, world)
    new_state, events = apply_order_evaluations(evaluations, state, tick=1, sequencer=EventSequencer())
    assert new_state.robots[0].order == Advance(3, target_x=6)
    assert len(events) == 1
    change = events[0]
    assert isinstance(change, RobotOrderChangedEvent)
    assert change.previous == Advance(3)
    assert change.status is OrderStatus.ACTIVE
    assert change.tick == 1


def test_apply_order_evaluations_is_a_no_op_when_nothing_changed() -> None:
    robot = _robot(x=2, y=5, order=StopAndDefend())
    state = _state((robot,))
    evaluations = evaluate_orders(state, _empty_world())
    new_state, events = apply_order_evaluations(evaluations, state, tick=1)
    assert new_state is state
    assert events == ()


def test_apply_order_evaluations_emits_order_changes_before_intents() -> None:
    world = _empty_world(width=40)
    enemy = _robot("robot-enemy", PLAYER_TWO, x=9, y=5)
    advancing = _robot("robot-a", x=0, y=5, order=Advance(3))
    defending = _robot("robot-b", x=2, y=7, order=StopAndDefend())
    state = _state((advancing, defending, enemy))
    evaluations = evaluate_orders(state, world)
    _new_state, events = apply_order_evaluations(
        evaluations, state, tick=1, sequencer=EventSequencer()
    )
    kinds = [type(event) for event in events]
    assert kinds == [RobotOrderChangedEvent, RobotEngagementIntentEvent]
    assert [event.sequence for event in events] == [0, 1]


def test_apply_order_evaluations_is_replay_identical() -> None:
    world = _world()
    enemy = _robot("robot-enemy", PLAYER_TWO, x=9, y=5)
    robots = (
        _robot("robot-a", x=0, y=5, order=Advance(3)),
        _robot("robot-b", x=2, y=7, order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)),
        _robot("robot-c", x=4, y=3, order=SearchDestroy(SearchDestroyTarget.ROBOT)),
        enemy,
    )
    runs = []
    for ordering in (robots, tuple(reversed(robots))):
        state = _state(ordering)
        evaluations = evaluate_orders(state, world)
        runs.append(apply_order_evaluations(evaluations, state, tick=1, sequencer=EventSequencer()))
    assert runs[0] == runs[1]


# --------------------------------------------------------------------------
# SetRobotOrderCommand
# --------------------------------------------------------------------------


def test_set_robot_order_assigns_the_order_and_emits_a_change_event() -> None:
    robot = _robot()
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=robot.entity_id, order=Advance(10)
    )
    new_state, event = apply_set_robot_order(command, state, tick=1, sequencer=EventSequencer())
    assert new_state.robots[0].order == Advance(10)
    assert event is not None
    assert event.previous is None
    assert event.status is OrderStatus.PENDING


def test_set_robot_order_ignores_a_robot_the_player_does_not_own() -> None:
    robot = _robot(owner=PLAYER_TWO)
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=robot.entity_id, order=Advance(10)
    )
    new_state, event = apply_set_robot_order(command, state, tick=1)
    assert new_state is state
    assert event is None


def test_set_robot_order_ignores_an_unknown_robot() -> None:
    state = _state()
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=EntityId("nobody"), order=Advance(10)
    )
    new_state, event = apply_set_robot_order(command, state, tick=1)
    assert new_state is state
    assert event is None


def test_set_robot_order_stores_an_invalid_order_as_stop_and_defend() -> None:
    robot = _robot()
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=robot.entity_id, order=Advance(999)
    )
    new_state, event = apply_set_robot_order(command, state, tick=1)
    assert new_state.robots[0].order == StopAndDefend()
    assert event is not None
    assert event.status is OrderStatus.FALLBACK


def test_set_robot_order_clears_a_pre_bound_goal() -> None:
    """A caller must not smuggle in a goal the 0-50-mile rule never validated."""
    robot = _robot(x=5, y=5)
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=robot.entity_id,
        order=Advance(3, target_x=99),
    )
    new_state, _event = apply_set_robot_order(command, state, tick=1)
    assert new_state.robots[0].order == Advance(3)


def test_reissuing_the_same_order_restarts_it_from_the_current_position() -> None:
    world = _empty_world(width=40)
    robot = _robot(x=10, y=5, order=Advance(3, target_x=6))
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=robot.entity_id, order=Advance(3)
    )
    state, event = apply_set_robot_order(command, state, tick=1)
    assert event is not None
    evaluations = evaluate_orders(state, world)
    assert isinstance(evaluations[0].order, Advance)
    assert evaluations[0].order.target_x == 16


# --------------------------------------------------------------------------
# Robot.with_order
# --------------------------------------------------------------------------


def test_with_order_preserves_every_other_robot_field() -> None:
    robot = _robot(x=3, y=4, order=StopAndDefend())
    updated = robot.with_order(Advance(2))
    assert updated.order == Advance(2)
    assert (updated.entity_id, updated.owner, updated.x, updated.y) == (
        robot.entity_id,
        robot.owner,
        robot.x,
        robot.y,
    )
    assert updated.build == robot.build
    assert updated.stack == robot.stack
    assert updated.height == robot.height


def test_with_order_can_clear_an_order() -> None:
    robot = _robot(order=Advance(2))
    assert robot.with_order(None).order is None
