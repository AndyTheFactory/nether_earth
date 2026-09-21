"""Integration tests for direct-control robot movement (issue #63, M5.4).

Exercises ``engine.step`` end to end (not ``direct_control.py``'s pure
functions directly -- those are covered by ``test_direct_control.py``): a
``DirectRobotMoveCommand`` from a docked commander starts and completes a
move through the exact same shared batch (`~nether_earth.reservations.
apply_robot_move_batch`) autonomous robot moves use, an undocked (``FREE``)
commander's direct-move command is a deterministic no-op, and direct control
cannot bypass destination reservations or commander blocking -- it is
rejected by the very same checks a non-direct-control
``~nether_earth.movement.RobotMoveRequest`` would be rejected by.
"""

from __future__ import annotations

from nether_earth.commander import Commander, CommanderMode
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.engine import new_game, step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.movement import RobotMoveCompletedEvent, RobotMoveStartedEvent
from nether_earth.reservations import DestinationContentionResolvedEvent
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.state import GameState
from nether_earth.terrain import TerrainGrid, TerrainType

BIPOD_TICKS = DEFAULT_RULES.robot_move_ticks_bipod
ROBOT_ID = EntityId("robot-1")


def _world(
    width: int = 10,
    height: int = 10,
    terrain_cells: dict[tuple[int, int], TerrainType] | None = None,
) -> WorldMap:
    return WorldMap(
        map_id="direct-control-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width, height=height, cells=terrain_cells or {}, default=TerrainType.NORMAL
        ),
        war_bases=(),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="direct-control-fixture",
        map_id="direct-control-test-map",
        map_version=1,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id="direct-control-test-map", version=1, width=10, height=10)
    return scenario, map_data


def _base_state() -> GameState:
    scenario, map_data = _scenario_and_map()
    return new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=1)


def _robot(
    entity_id: str = "robot-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 5,
    y: int = 5,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    movement: RobotMoveTransition | None = None,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=(ModuleIdentity.CANNON,))
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


def _docked_commander(
    player_id: PlayerId = PLAYER_ONE, x: int = 5, y: int = 5, robot_id: EntityId = ROBOT_ID
) -> Commander:
    return Commander(
        player_id=player_id,
        mode=CommanderMode.DOCKED,
        x=x,
        y=y,
        altitude=4,
        docked_robot_id=robot_id,
    )


def _free_commander(player_id: PlayerId, x: int, y: int, altitude: int = 0) -> Commander:
    return Commander(player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)


def _direct_move(dx: int, dy: int, player: PlayerId = PLAYER_ONE, sequence: int = 0) -> DirectRobotMoveCommand:
    return DirectRobotMoveCommand(player=player, sequence=sequence, dx=dx, dy=dy)


# --------------------------------------------------------------------------
# Successful move: starts, then completes on the configured tick
# --------------------------------------------------------------------------


def test_direct_move_starts_through_the_shared_batch_and_completes() -> None:
    robot = _robot()
    commander = _docked_commander()
    state = _base_state().with_robots((robot,)).with_commanders((commander,))

    state, events = step(state, [_direct_move(1, 0)], world=_world())

    started = [e for e in events if isinstance(e, RobotMoveStartedEvent)]
    assert len(started) == 1
    assert started[0].entity_id == ROBOT_ID
    assert started[0].from_x == 5 and started[0].to_x == 6

    updated_robot = state.robot_for(ROBOT_ID)
    assert updated_robot is not None
    # Authoritative position does not change on start -- only on completion.
    assert (updated_robot.x, updated_robot.y) == (5, 5)
    assert updated_robot.movement is not None
    assert updated_robot.movement.duration_ticks == BIPOD_TICKS

    # Advance through the configured duration; no further commands issued.
    for _ in range(BIPOD_TICKS - 1):
        state, events = step(state, [], world=_world())
        assert not any(isinstance(e, RobotMoveCompletedEvent) for e in events)

    state, events = step(state, [], world=_world())
    completed = [e for e in events if isinstance(e, RobotMoveCompletedEvent)]
    assert len(completed) == 1
    assert completed[0].entity_id == ROBOT_ID

    final_robot = state.robot_for(ROBOT_ID)
    assert final_robot is not None
    assert (final_robot.x, final_robot.y) == (6, 5)
    assert final_robot.movement is None


# --------------------------------------------------------------------------
# Interaction-state gate: undocked commander cannot direct-move a robot
# --------------------------------------------------------------------------


def test_free_commander_direct_move_is_a_deterministic_no_op() -> None:
    robot = _robot()
    commander = _free_commander(PLAYER_ONE, x=5, y=5)
    state = _base_state().with_robots((robot,)).with_commanders((commander,))

    state, events = step(state, [_direct_move(1, 0)], world=_world())

    assert not any(isinstance(e, RobotMoveStartedEvent) for e in events)
    updated_robot = state.robot_for(ROBOT_ID)
    assert updated_robot is not None
    assert (updated_robot.x, updated_robot.y) == (5, 5)
    assert updated_robot.movement is None


def test_leaving_direct_control_via_undock_stops_further_direct_moves() -> None:
    """Undocking (the existing M3/M4 transition) is the "leave direct control" step."""
    robot = _robot()
    # Docked and holding rise intent: engine.step's Step 3 undocks in the
    # same tick this state is stepped, per docking.py's apply_undock. A
    # docked commander physically rests on the robot's top (docking.py's
    # touching condition), so its altitude is the robot's height.
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=5,
        y=5,
        altitude=robot.height,
        docked_robot_id=ROBOT_ID,
        rising=True,
    )
    state = _base_state().with_robots((robot,)).with_commanders((commander,))

    # First tick: undocking occurs. No direct-move command issued this tick.
    state, _events = step(state, [], world=_world())
    updated_commander = state.commander_for(PLAYER_ONE)
    assert updated_commander is not None
    assert updated_commander.mode is CommanderMode.FREE

    # Second tick: the now-FREE commander's direct-move command is rejected
    # at the gate -- zero side effects.
    state, events = step(state, [_direct_move(1, 0)], world=_world())
    assert not any(isinstance(e, RobotMoveStartedEvent) for e in events)
    updated_robot = state.robot_for(ROBOT_ID)
    assert updated_robot is not None
    assert (updated_robot.x, updated_robot.y) == (5, 5)


# --------------------------------------------------------------------------
# Direct control cannot bypass destination reservation
# --------------------------------------------------------------------------


def test_direct_move_cannot_claim_a_cell_already_reserved_by_another_robot() -> None:
    contested = (6, 5)
    reserving_transition = RobotMoveTransition(
        entity_id=EntityId("robot-2"),
        from_x=7,
        from_y=5,
        to_x=contested[0],
        to_y=contested[1],
        started_tick=0,
        duration_ticks=100,
    )
    reserving_robot = _robot(
        entity_id="robot-2", owner=PLAYER_TWO, x=7, y=5, movement=reserving_transition
    )
    controlled_robot = _robot()  # at (5, 5), one step west of the contested cell
    commander = _docked_commander()
    state = (
        _base_state()
        .with_robots((controlled_robot, reserving_robot))
        .with_commanders((commander,))
    )

    state, events = step(state, [_direct_move(1, 0)], world=_world())

    # No RobotMoveStartedEvent for the direct-controlled robot: the
    # reservation gate rejected it before the shared executor would ever
    # start a transition.
    assert not any(
        isinstance(e, RobotMoveStartedEvent) and e.entity_id == ROBOT_ID for e in events
    )
    updated_robot = state.robot_for(ROBOT_ID)
    assert updated_robot is not None
    assert (updated_robot.x, updated_robot.y) == (5, 5)
    assert updated_robot.movement is None

    # The reservation holder's own in-flight move is untouched.
    other_robot = state.robot_for(EntityId("robot-2"))
    assert other_robot is not None
    assert other_robot.movement == reserving_transition


# --------------------------------------------------------------------------
# Direct control cannot bypass commander blocking
# --------------------------------------------------------------------------


def test_direct_move_cannot_bypass_commander_blocking() -> None:
    robot = _robot()
    driver = _docked_commander()
    blocker = _free_commander(PLAYER_TWO, x=6, y=5, altitude=0)  # sits on the destination cell
    state = _base_state().with_robots((robot,)).with_commanders((driver, blocker))

    state, events = step(state, [_direct_move(1, 0)], world=_world())

    assert not any(isinstance(e, RobotMoveStartedEvent) for e in events)
    updated_robot = state.robot_for(ROBOT_ID)
    assert updated_robot is not None
    assert (updated_robot.x, updated_robot.y) == (5, 5)
    assert updated_robot.movement is None


# --------------------------------------------------------------------------
# Direct control cannot bypass terrain legality
# --------------------------------------------------------------------------


def test_direct_move_cannot_bypass_terrain_legality() -> None:
    robot = _robot(chassis=ModuleIdentity.BIPOD)
    commander = _docked_commander()
    world = _world(terrain_cells={(6, 5): TerrainType.DITCH})
    state = _base_state().with_robots((robot,)).with_commanders((commander,))

    state, events = step(state, [_direct_move(1, 0)], world=world)

    assert not any(isinstance(e, RobotMoveStartedEvent) for e in events)
    updated_robot = state.robot_for(ROBOT_ID)
    assert updated_robot is not None
    assert (updated_robot.x, updated_robot.y) == (5, 5)


# --------------------------------------------------------------------------
# Direct control participates in the same-tick contention path
# --------------------------------------------------------------------------


def test_direct_move_participates_in_same_tick_contention() -> None:
    contested = (6, 5)
    controlled_robot = _robot(entity_id="robot-1", owner=PLAYER_ONE, x=5, y=5)
    contender_robot = _robot(entity_id="robot-2", owner=PLAYER_TWO, x=7, y=5)
    commander = _docked_commander()
    state = (
        _base_state()
        .with_robots((controlled_robot, contender_robot))
        .with_commanders((commander,))
    )

    from nether_earth.movement import RobotMoveRequest

    state, events = step(
        state,
        [_direct_move(1, 0)],
        world=_world(),
        robot_moves=[RobotMoveRequest(entity_id=EntityId("robot-2"), dx=-1, dy=0)],
    )

    contentions = [e for e in events if isinstance(e, DestinationContentionResolvedEvent)]
    assert len(contentions) == 1
    assert contentions[0].x == contested[0] and contentions[0].y == contested[1]
    assert set(contentions[0].contenders) == {ROBOT_ID, EntityId("robot-2")}

    started = [e for e in events if isinstance(e, RobotMoveStartedEvent)]
    assert len(started) == 1
    assert started[0].entity_id == contentions[0].winner

    # Exactly one of the two robots is now mid-transition; the loser stays put.
    moving_entities = {
        entity_id
        for entity_id in (ROBOT_ID, EntityId("robot-2"))
        if state.robot_for(entity_id) is not None and state.robot_for(entity_id).movement is not None  # type: ignore[union-attr]
    }
    assert moving_entities == {contentions[0].winner}


# --------------------------------------------------------------------------
# Replay determinism
# --------------------------------------------------------------------------


def test_direct_control_is_deterministic_across_repeated_runs() -> None:
    def run() -> tuple[GameState, tuple]:
        robot = _robot()
        commander = _docked_commander()
        state = _base_state().with_robots((robot,)).with_commanders((commander,))
        state, events = step(state, [_direct_move(1, 0)], world=_world())
        return state, events

    state_a, events_a = run()
    state_b, events_b = run()

    assert state_a == state_b
    assert events_a == events_b
