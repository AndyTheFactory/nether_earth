"""2×2 robot and commander bodies (CR002.3 #170, CR002.4 #171).

A unit's ``(x, y)`` is the anchor of a 2×2 body covering ``x..x+1`` and
``y-1..y`` (`_specs/open-questions.md` §21; ``Lb5d6_map_altitude_2x2``,
``Lb513_get_robot_movement_possibilities``, ``Lb052_check_player_collision``
in `santiontanon/netherearth-disassembly`). These tests pin the convention
at every rule that reads it; projectile hits and the heli-pad have their
own files (`test_combat_projectile.py`, `test_heli_pad.py`).
"""

from __future__ import annotations

import pytest

from nether_earth.capture import advance_capture
from nether_earth.collision import (
    RobotFixture,
    commander_horizontal_move_allowed,
    commander_vertical_move_allowed,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.destruction import RobotDestroyedEvent, execute_nuclear_detonation
from nether_earth.docking import attempt_auto_dock
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    apply_robot_move,
    validate_robot_move,
)
from nether_earth.occupancy import (
    unit_footprint,
    unit_footprint_cells,
    unit_footprint_in_bounds,
    unit_footprints_overlap,
)
from nether_earth.reservations import (
    apply_robot_move_batch,
    destination_available,
)
from nether_earth.robot import Robot, RobotFacing
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component, Factory, FactoryType, Footprint
from nether_earth.terrain import TerrainGrid, TerrainType

WIDTH, HEIGHT = 20, 16


def _world(
    *,
    blockers: tuple[Blocker, ...] = (),
    factories: tuple[Factory, ...] = (),
    interaction_points: tuple[InteractionPoint, ...] = (),
    terrain: dict[tuple[int, int], TerrainType] | None = None,
) -> WorldMap:
    return WorldMap(
        map_id="unit-2x2-map",
        version=1,
        width=WIDTH,
        height=HEIGHT,
        terrain=TerrainGrid(width=WIDTH, height=HEIGHT, cells=terrain or {}, default=TerrainType.NORMAL),
        war_bases=(),
        factories=factories,
        blockers=blockers,
        interaction_points=interaction_points,
        spawn_positions={},
    )


def _box(x: int, y: int, height: int = 15, blocker_id: str = "box") -> Blocker:
    return Blocker(id=EntityId(blocker_id), components=(Component(x=x, y=y, height=height),))


def _robot(
    entity_id: str,
    x: int,
    y: int,
    owner: PlayerId = PLAYER_ONE,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    facing: RobotFacing = RobotFacing.EAST,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id), owner=owner, x=x, y=y, build=build, stack=stack, height=height,
        facing=facing,
    )


def _state(*robots: Robot, **kwargs: object) -> GameState:
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=list(robots), **kwargs)  # type: ignore[arg-type]


def _move(robot: Robot, dx: int, dy: int) -> RobotMoveRequest:
    return RobotMoveRequest(entity_id=robot.entity_id, dx=dx, dy=dy)


# --------------------------------------------------------------------------
# The convention itself
# --------------------------------------------------------------------------


def test_body_is_the_anchor_its_right_neighbour_and_the_row_above() -> None:
    assert unit_footprint_cells(5, 7) == ((5, 7), (6, 7), (5, 6), (6, 6))
    assert unit_footprint(5, 7).cells == frozenset({(5, 7), (6, 7), (5, 6), (6, 6)})


@pytest.mark.parametrize(("dx", "dy"), [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)])
def test_bodies_overlap_exactly_within_the_3x3_anchor_window(dx: int, dy: int) -> None:
    """``Lb052``/``Lb724`` scan the 3×3 window of anchors around a unit."""
    assert unit_footprints_overlap(5, 7, 5 + dx, 7 + dy)
    cells = set(unit_footprint_cells(5, 7))
    assert cells & set(unit_footprint_cells(5 + dx, 7 + dy))


@pytest.mark.parametrize(("dx", "dy"), [(2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, 1)])
def test_bodies_two_anchors_apart_do_not_overlap(dx: int, dy: int) -> None:
    assert not unit_footprints_overlap(5, 7, 5 + dx, 7 + dy)
    assert not set(unit_footprint_cells(5, 7)) & set(unit_footprint_cells(5 + dx, 7 + dy))


def test_anchor_bounds_keep_the_whole_body_on_the_map() -> None:
    assert unit_footprint_in_bounds(0, 1, WIDTH, HEIGHT)
    assert unit_footprint_in_bounds(WIDTH - 2, HEIGHT - 1, WIDTH, HEIGHT)
    assert not unit_footprint_in_bounds(0, 0, WIDTH, HEIGHT)  # top row off the map
    assert not unit_footprint_in_bounds(WIDTH - 1, 5, WIDTH, HEIGHT)  # right column off the map
    assert not unit_footprint_in_bounds(-1, 5, WIDTH, HEIGHT)
    assert not unit_footprint_in_bounds(5, HEIGHT, WIDTH, HEIGHT)


# --------------------------------------------------------------------------
# Robot movement: structures, terrain, bounds, other robots
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dx", "dy", "box"),
    [
        (1, 0, (7, 4)),  # east: the new column, upper row
        (1, 0, (7, 5)),  # east: the new column, anchor row
        (-1, 0, (4, 4)),  # west: the new column, upper row
        (0, 1, (6, 6)),  # south: the new row, right column
        (0, -1, (5, 3)),  # north: the new row, anchor column
    ],
)
def test_a_structure_under_any_newly_entered_body_cell_blocks_the_move(
    dx: int, dy: int, box: tuple[int, int]
) -> None:
    """``Lb557``/``Lb56f``/``Lb58f``/``Lb5b1`` test both cells a step newly enters."""
    world = _world(blockers=(_box(*box),))
    robot = _robot("robot-a", 5, 5)
    result = validate_robot_move(_move(robot, dx, dy), _state(robot), world)
    assert result.reason is MovementRejectionReason.OCCUPIED


def test_a_structure_beside_the_body_does_not_block() -> None:
    world = _world(blockers=(_box(8, 5),))  # two columns right of the body's right edge after a step
    robot = _robot("robot-a", 5, 5)
    assert validate_robot_move(_move(robot, 1, 0), _state(robot), world).accepted


@pytest.mark.parametrize("cell", [(7, 5), (7, 4)])
def test_impassable_terrain_under_any_body_cell_blocks_the_move(cell: tuple[int, int]) -> None:
    world = _world(terrain={cell: TerrainType.DITCH})  # a bipod cannot enter a ditch
    robot = _robot("robot-a", 5, 5)
    result = validate_robot_move(_move(robot, 1, 0), _state(robot), world)
    assert result.reason is MovementRejectionReason.TERRAIN_IMPASSABLE


def test_the_move_duration_reads_the_slowest_terrain_under_the_body() -> None:
    world = _world(terrain={(7, 4): TerrainType.ROUGH})  # only the new upper-right cell
    robot = _robot("robot-a", 5, 5)
    state = _state(robot)
    moved, result, _event = apply_robot_move(_move(robot, 1, 0), state, world, tick=0)
    assert result.accepted
    started = moved.robot_for(robot.entity_id)
    assert started is not None and started.movement is not None
    assert started.movement.duration_ticks == DEFAULT_RULES.robot_move_ticks_bipod_rough


@pytest.mark.parametrize(
    ("x", "y", "dx", "dy"),
    [(5, 1, 0, -1), (WIDTH - 2, 5, 1, 0), (0, 5, -1, 0), (5, HEIGHT - 1, 0, 1)],
)
def test_a_body_leaving_the_map_is_out_of_bounds(x: int, y: int, dx: int, dy: int) -> None:
    robot = _robot("robot-a", x, y)
    result = validate_robot_move(_move(robot, dx, dy), _state(robot), _world())
    assert result.reason is MovementRejectionReason.OUT_OF_BOUNDS


def test_side_by_side_robots_can_pass_but_not_overlap() -> None:
    world = _world()
    mover = _robot("robot-a", 5, 5)
    # Anchors two rows apart: bodies touch along an edge, the east step is free.
    neighbour_below = _robot("robot-b", 6, 7)
    assert validate_robot_move(_move(mover, 1, 0), _state(mover, neighbour_below), world).accepted
    # Anchor one row and two columns away: the step east would overlap it.
    diagonal = _robot("robot-b", 7, 6)
    result = validate_robot_move(_move(mover, 1, 0), _state(mover, diagonal), world)
    assert result.reason is MovementRejectionReason.OCCUPIED


def test_a_robot_never_blocks_its_own_next_body() -> None:
    robot = _robot("robot-a", 5, 5)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        assert validate_robot_move(_move(robot, dx, dy), _state(robot), _world()).accepted


# --------------------------------------------------------------------------
# Reservations and same-tick contention over 2×2 destinations
# --------------------------------------------------------------------------


def test_an_in_flight_destination_body_blocks_overlapping_claims() -> None:
    world = _world()
    first = _robot("robot-a", 5, 5)
    state = _state(first, _robot("robot-b", 9, 5))
    moved, result, _event = apply_robot_move(_move(first, 1, 0), state, world, tick=0)
    assert result.accepted
    other = moved.robot_for(EntityId("robot-b"))
    assert other is not None
    # robot-a reserved the body anchored at (6, 5): cells 6..7 x 4..5.
    assert not destination_available(moved, other, 7, 5)  # shares column 7
    assert not destination_available(moved, other, 7, 6)  # shares cell (7, 5)
    assert destination_available(moved, other, 8, 5)  # edge to edge
    assert destination_available(moved, other, 7, 7)  # edge to edge


def test_overlapping_same_tick_claims_to_different_anchors_contend() -> None:
    """Two robots stepping toward each other claim bodies that overlap by one column."""
    world = _world()
    west = _robot("robot-a", 4, 5)
    east = _robot("robot-b", 7, 5)
    state = _state(west, east)
    result = apply_robot_move_batch((_move(west, 1, 0), _move(east, -1, 0)), state, world, tick=1)

    contentions = result.contentions
    assert len(contentions) == 1
    assert set(contentions[0].contenders) == {west.entity_id, east.entity_id}
    moving = [r for r in result.state.robots if r.movement is not None]
    assert [r.entity_id for r in moving] == [contentions[0].winner]
    loser = next(r for r in result.results if not r.accepted)
    assert loser.reason is MovementRejectionReason.DESTINATION_UNAVAILABLE


def test_contention_is_replay_deterministic_for_the_same_seed() -> None:
    world = _world()
    west = _robot("robot-a", 4, 5)
    east = _robot("robot-b", 7, 5)
    requests = (_move(west, 1, 0), _move(east, -1, 0))
    winners = {
        next(
            e.winner
            for e in apply_robot_move_batch(requests, _state(west, east), world, tick=1).contentions
        )
        for _ in range(3)
    }
    assert len(winners) == 1


def test_non_overlapping_same_tick_claims_both_start() -> None:
    world = _world()
    west = _robot("robot-a", 3, 5)
    east = _robot("robot-b", 8, 5)
    state = _state(west, east)
    result = apply_robot_move_batch((_move(west, 1, 0), _move(east, -1, 0)), state, world, tick=1)
    assert all(r.accepted for r in result.results)
    assert result.contentions == ()


# --------------------------------------------------------------------------
# Capture: the anchor must stand on the capture cell
# --------------------------------------------------------------------------

FACTORY_ID = EntityId("factory-1")
CAPTURE_CELL = (10, 10)


def _capture_world() -> WorldMap:
    factory = Factory(
        id=FACTORY_ID,
        components=(Component(x=12, y=8, height=3),),
        factory_type=FactoryType.CHASSIS,
        owner=None,
    )
    point = InteractionPoint(
        id="factory-1-capture",
        kind=InteractionKind.FACTORY_CAPTURE,
        structure_id=FACTORY_ID,
        footprint=Footprint(cells=frozenset({CAPTURE_CELL})),
    )
    return _world(factories=(factory,), interaction_points=(point,))


def test_a_robot_anchored_on_the_capture_cell_captures() -> None:
    # Qualifying occupation is what this test pins, so it asserts the
    # countdown starts rather than the ownership flip: since the owner
    # decision of 2026-09-23 even a neutral factory takes the full
    # ``capture_duration_ticks`` (see test_capture.py for the completion).
    state = _state(_robot("robot-a", *CAPTURE_CELL))
    new_state, _events = advance_capture(state, _capture_world(), tick=1)
    progress = new_state.capture_progress_for(FACTORY_ID)
    assert progress is not None
    assert progress.capturing_player == PLAYER_ONE
    assert progress.robot_id == EntityId("robot-a")


@pytest.mark.parametrize("anchor", [(9, 10), (10, 11), (9, 11)])
def test_a_body_covering_the_capture_cell_with_its_anchor_elsewhere_does_not(
    anchor: tuple[int, int],
) -> None:
    """``Ladb7_building_loop`` tests the robot map mark (the anchor) on the building's cell."""
    assert CAPTURE_CELL in unit_footprint_cells(*anchor)
    state = _state(_robot("robot-a", *anchor))
    new_state, events = advance_capture(state, _capture_world(), tick=1)
    assert new_state.structure_ownership_for(FACTORY_ID) is None
    assert new_state.capture_progress_for(FACTORY_ID) is None
    assert events == ()


# --------------------------------------------------------------------------
# Nuclear window: robots count by anchor
# --------------------------------------------------------------------------


def test_the_nuclear_window_tests_robot_anchors() -> None:
    carrier = _robot("robot-nuke", 10, 8, weapons=(ModuleIdentity.NUCLEAR,))
    widths = DEFAULT_RULES.nuclear_robot_window_row_widths
    top_row = carrier.y - len(widths) // 2
    half = widths[0] // 2
    inside = _robot("robot-in", carrier.x + half, top_row, owner=PLAYER_TWO)
    # Anchored one row above the window: its lower body row is inside, its anchor is not.
    outside = _robot("robot-out", carrier.x - half, top_row - 1, owner=PLAYER_TWO)
    assert top_row - 1 >= 1
    state = _state(carrier, inside, outside)

    new_state, events = execute_nuclear_detonation(state, _world(), carrier.entity_id, tick=1)

    destroyed = {e.entity_id for e in events if isinstance(e, RobotDestroyedEvent)}
    assert inside.entity_id in destroyed
    assert outside.entity_id not in destroyed
    assert new_state.robot_for(outside.entity_id) is not None


# --------------------------------------------------------------------------
# Commander: 2×2 collision and docking
# --------------------------------------------------------------------------


def _commander(x: int, y: int, altitude: int, player: PlayerId = PLAYER_ONE) -> Commander:
    return Commander(player_id=player, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)


@pytest.mark.parametrize("box", [(6, 5), (7, 5), (6, 4), (7, 4)])
def test_a_high_structure_under_any_cell_of_the_commander_body_blocks_it(box: tuple[int, int]) -> None:
    """``Lb052_check_player_collision`` reads the map pieces of the ship's 2×2 area."""
    world = _world(blockers=(_box(*box, height=10),))
    commander = _commander(5, 5, altitude=4)
    assert not commander_horizontal_move_allowed(_state(), commander, 6, 5, world=world)
    above = _commander(5, 5, altitude=10)
    assert commander_horizontal_move_allowed(_state(), above, 6, 5, world=world)


def test_the_commander_body_cannot_leave_the_map() -> None:
    world = _world()
    assert not commander_horizontal_move_allowed(_state(), _commander(5, 1, 20), 5, 0, world=world)
    assert not commander_horizontal_move_allowed(
        _state(), _commander(WIDTH - 2, 5, 20), WIDTH - 1, 5, world=world
    )


def test_a_robot_overlapping_the_commander_body_blocks_and_holds_it_up() -> None:
    robot = RobotFixture(id=EntityId("robot-b"), owner=PLAYER_TWO, x=7, y=4, height=6)
    world = _world()
    low = _commander(5, 5, altitude=2)
    assert not commander_horizontal_move_allowed(_state(), low, 6, 5, world=world, robots=(robot,))
    # Resting on the robot's top with an overlapping (not coincident) body: no descent.
    resting = _commander(6, 5, altitude=6)
    assert not commander_vertical_move_allowed(_state(), resting, 5, world=world, robots=(robot,))


def test_commanders_with_overlapping_bodies_collide() -> None:
    world = _world()
    other = _commander(7, 6, altitude=0, player=PLAYER_TWO)
    mover = _commander(5, 5, altitude=0)
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO), commanders=(mover, other))
    assert not commander_horizontal_move_allowed(state, mover, 6, 5, world=world)
    far = _commander(8, 5, altitude=0, player=PLAYER_TWO)
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO), commanders=(mover, far))
    assert commander_horizontal_move_allowed(state, mover, 6, 5, world=world)


def test_docking_needs_the_commander_anchored_on_the_robot_anchor() -> None:
    """``La69a``: the robot's map mark must be on the ship's own anchor cell."""
    robot = RobotFixture(id=EntityId("robot-a"), owner=PLAYER_ONE, x=6, y=5, height=6)
    docked = attempt_auto_dock(_commander(6, 5, altitude=6), (robot,))
    assert docked.mode is CommanderMode.DOCKED and docked.docked_robot_id == robot.id
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        held = attempt_auto_dock(_commander(6 + dx, 5 + dy, altitude=6), (robot,))
        assert held.mode is CommanderMode.FREE
