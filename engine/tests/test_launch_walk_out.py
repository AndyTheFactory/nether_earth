"""Launched robots walk out of the war base (CR002.3 #170, owner decision 2026-09-21).

Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`):
right after `Lc849_robot_construction_if_possible`, `La6c8` sets the new
robot's desired direction to down, 5 steps to keep walking and Stop &
Defend; the construction screen already faces it down. `Lb154` ->
`Lb1e9_no_enemy_robots_in_sight` then walks it one step per robot update
while the step is possible, and a blocked step falls through to
`Lb1f5`/`Lb222`, which picks no direction for Stop & Defend. Leaving the
robot's menu zeroes its steps. See `_specs/resolved-questions.md` "2×2 robots, commander, projectiles and heli-pad".
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nether_earth.commander import Commander, CommanderMode
from nether_earth.construction_commands import LaunchRobotCommand
from nether_earth.construction_economy import ResourcePool
from nether_earth.construction_session import BuildInProgress, ConstructionSession
from nether_earth.engine import step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.movement import RobotMoveStartedEvent
from nether_earth.orders import Advance, SetRobotOrderCommand, StopAndDefend
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.state import GameState
from nether_earth.structures import Blocker, Component

MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
EXIT = (22, 9)  # warbase-1 exit anchor: the new body stands in the doorway (§18/§21)
BIPOD_FLAT = DEFAULT_RULES.robot_move_ticks_bipod_normal
ROBOT_ID = EntityId("robot-p1-1")


@pytest.fixture(scope="module")
def world() -> WorldMap:
    base = load_world_map(MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def _launched(world: WorldMap, *others: Robot) -> GameState:
    """Launch a bipod + cannon robot from warbase-1; returns the state after the launch tick."""
    state = create_initial_state(default_pvp_scenario(), world, seed=1)
    session = ConstructionSession(
        player_id=PLAYER_ONE,
        war_base_id=EntityId("warbase-1"),
        entry_tick=0,
        build=BuildInProgress(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,)),
        buffer=ResourcePool(general=20, category={}),
        entry_snapshot=ResourcePool(general=20, category={}),
    )
    state = state.with_construction_sessions((session,)).with_robots(others)
    state, _events = step(state, (LaunchRobotCommand(player=PLAYER_ONE, sequence=0),), world)
    robot = state.robot_for(ROBOT_ID)
    assert robot is not None and (robot.x, robot.y) == EXIT
    return state


def _robot(entity_id: str, x: int, y: int, owner=PLAYER_TWO) -> Robot:  # type: ignore[no-untyped-def]
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id), owner=owner, x=x, y=y, build=build, stack=stack, height=height
    )


def _run(state: GameState, world: WorldMap, ticks: int, commands=()):  # type: ignore[no-untyped-def]
    """Step ``ticks`` ticks (``commands`` on the first); return state and the robot's move starts."""
    starts: list[tuple[int, int, int]] = []
    for index in range(ticks):
        state, events = step(state, commands if index == 0 else (), world)
        starts += [
            (e.started_tick, e.to_x, e.to_y)
            for e in events
            if isinstance(e, RobotMoveStartedEvent) and e.entity_id == ROBOT_ID
        ]
    return state, starts


def _robot_in(state: GameState) -> Robot:
    robot = state.robot_for(ROBOT_ID)
    assert robot is not None
    return robot


def test_a_launched_robot_holds_stop_and_defend_with_five_exit_steps(world: WorldMap) -> None:
    robot = _robot_in(_launched(world))
    assert robot.order == StopAndDefend()
    assert robot.exit_steps_remaining == DEFAULT_RULES.robot_launch_exit_steps == 5


def test_it_walks_five_steps_south_one_per_update_then_stops(world: WorldMap) -> None:
    state = _launched(world)
    launch_tick = state.tick

    state, starts = _run(state, world, 6 * BIPOD_FLAT)

    # First update on the tick after launch, then one step per bipod-flat update.
    assert starts == [
        (launch_tick + 1 + i * BIPOD_FLAT, EXIT[0], EXIT[1] + 1 + i) for i in range(5)
    ]
    robot = _robot_in(state)
    assert (robot.x, robot.y) == (EXIT[0], EXIT[1] + 5)
    assert robot.exit_steps_remaining == 0
    assert robot.order == StopAndDefend()
    assert robot.movement is None


def _with_box(world: WorldMap, x: int, y: int) -> WorldMap:
    box = Blocker(id=EntityId("test-box"), components=(Component(x=x, y=y, height=6),))
    return replace(world, blockers=(*world.blockers, box))


def test_a_blocked_step_ends_the_walk_out(world: WorldMap) -> None:
    # A box on the row the second step would newly enter (the body's rows become 10..11).
    world = _with_box(world, EXIT[0] + 1, EXIT[1] + 2)
    state = _launched(world)

    state, starts = _run(state, world, 6 * BIPOD_FLAT)

    assert [(x, y) for _tick, x, y in starts] == [(EXIT[0], EXIT[1] + 1)]
    robot = _robot_in(state)
    assert (robot.x, robot.y) == (EXIT[0], EXIT[1] + 1)
    assert robot.exit_steps_remaining == 0
    assert robot.order == StopAndDefend()


def test_a_robot_blocked_at_the_exit_never_walks(world: WorldMap) -> None:
    world = _with_box(world, EXIT[0], EXIT[1] + 1)
    state = _launched(world)

    state, starts = _run(state, world, 2 * BIPOD_FLAT)

    assert starts == []
    assert _robot_in(state).exit_steps_remaining == 0


def test_an_order_ends_the_walk_out_at_once(world: WorldMap) -> None:
    state = _launched(world)
    state, _starts = _run(state, world, 2)  # first step under way
    order = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=1, entity_id=ROBOT_ID, order=StopAndDefend()
    )

    state, starts = _run(state, world, 3 * BIPOD_FLAT, (order,))

    robot = _robot_in(state)
    assert robot.exit_steps_remaining == 0
    assert starts == []  # the step already under way completes, no further one starts
    assert (robot.x, robot.y) == (EXIT[0], EXIT[1] + 1)


def test_docking_on_the_robot_ends_the_walk_out(world: WorldMap) -> None:
    state = _launched(world)
    robot = _robot_in(state)
    rider = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=robot.x,
        y=robot.y,
        altitude=robot.height,
        docked_robot_id=ROBOT_ID,
    )
    others = tuple(c for c in state.commanders if c.player_id != PLAYER_ONE)
    state = state.with_commanders((rider, *others))

    state, starts = _run(state, world, 2 * BIPOD_FLAT)

    assert starts == []
    assert _robot_in(state).exit_steps_remaining == 0


def test_an_update_that_fires_ends_the_walk_out(world: WorldMap) -> None:
    # An enemy straight south in cannon range: the first update fires instead of walking.
    enemy = _robot("robot-p2-1", EXIT[0], EXIT[1] + 6)
    state = _launched(world, enemy)

    state, starts = _run(state, world, 2)

    robot = _robot_in(state)
    assert robot.last_fire_tick is not None
    assert starts == []
    assert robot.exit_steps_remaining == 0


def test_a_non_electronic_robot_given_advance_after_the_walk_out_leaves_the_base(
    world: WorldMap,
) -> None:
    """Regression for the 2×2 doorway: east/west steps from the exit anchor are walled."""
    state = _launched(world)
    state, _starts = _run(state, world, 6 * BIPOD_FLAT)
    advance = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=1, entity_id=ROBOT_ID, order=Advance(distance_miles=3)
    )

    state, starts = _run(state, world, 7 * BIPOD_FLAT, (advance,))

    robot = _robot_in(state)
    assert [(x, y) for _tick, x, y in starts][:1] == [(EXIT[0] + 1, EXIT[1] + 5)]
    assert robot.x == EXIT[0] + 6
    assert robot.order == StopAndDefend()


def test_robot_copies_keep_the_exit_steps() -> None:
    robot = replace(_robot("robot-x", 5, 5), exit_steps_remaining=3)
    assert robot.with_position(5, 6).exit_steps_remaining == 3
    assert robot.with_movement(None).exit_steps_remaining == 3
    assert robot.with_order(None).exit_steps_remaining == 3
    assert robot.with_strength(50).exit_steps_remaining == 3
    assert robot.with_active_projectile(None).exit_steps_remaining == 3
