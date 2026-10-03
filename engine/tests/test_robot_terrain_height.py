"""Robots stand on the terrain under them (CR002.25, #214).

Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`):

- `Lb495` (in `Lb471_move_robot_one_step_in_desired_direction`): right after
  `Lb4b9_robot_advance` moves the robot one cell, `Lb5d6_map_altitude_2x2`
  (highest map piece under the 2×2 body) is stored in
  ``ROBOT_STRUCT_ALTITUDE``; a directly controlled robot then sets the
  ship's altitude to ``ROBOT_STRUCT_HEIGHT + ROBOT_STRUCT_ALTITUDE``. The
  altitude changes only together with the robot's cell; a new robot starts
  at altitude 0 (`La6c8`/`Lc849`), on the flat war-base doorway.
- `Lb099_get_robot_or_decoration_altitude` (ship collision/landing,
  `Lb052_check_player_collision`): a robot's top is height + altitude.
- `La69a` docks when ``altitude - height - altitude == 0`` (`La720_land_on_robot`).
- `Lb513_get_robot_movement_possibilities`: the ship is an obstacle to the
  robot when it is lower than height + altitude.
- Bullets (owner decision 2026-09-22, not evidence-derived): the engine's
  §8 altitude gate compares the robot's top with the bullet altitude, so a
  robot whose top is below the bullet is flown over. With the Spectrum
  piece heights (CR003.3, ``Ld7b4_piece_heights``) the shortest robot is 13
  tall, above the bullet altitude 10, so every robot is hit on flat ground.
- `Lcee8_draw_robot_to_buffer` draws the robot at elevation
  ``ROBOT_STRUCT_ALTITUDE`` (frontend, `frontend/src/render/robot.test.ts`).

Engine: the altitude is `collision.unit_surface_height` at the robot's
authoritative anchor, which (like the Spectrum's cell) changes when a move
completes (`movement.py`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nether_earth.collision import robot_top, unit_surface_height
from nether_earth.combat import (
    Projectile,
    ProjectileTerminatedEvent,
    ProjectileTerminationReason,
    advance_projectiles,
    apply_damage,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import CommanderMoveCommand
from nether_earth.destruction import advance_destroyed_robots
from nether_earth.docking import CommanderDockedEvent, CommanderUndockedEvent
from nether_earth.engine import step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    validate_robot_move,
)
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_launch import _resolve_exit_cell
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState, create_game_state

MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

# 2×2 anchors on the original map (x..x+1, y-1..y) whose four cells share one height.
FLAT_ANCHOR = (30, 12)
ROUGH_2_ANCHOR = (32, 12)  # element types 2-5
ROUGH_3_ANCHOR = (54, 14)  # element types 6/7
MOUNTAIN_ANCHOR = (167, 9)  # element types 8-11

RAISED = [(ROUGH_2_ANCHOR, 2), (ROUGH_3_ANCHOR, 3), (MOUNTAIN_ANCHOR, 6)]
ROBOT_ID = EntityId("robot-1")


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_world_map(MAP_PATH)


def _robot(x: int, y: int, owner: PlayerId = PLAYER_ONE) -> Robot:
    # Tracks may stand on rough and mountains; height 13 (tracks 7 + cannon 6).
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=ROBOT_ID, owner=owner, x=x, y=y, build=build, stack=stack, height=height
    )


def _free(x: int, y: int, altitude: int) -> Commander:
    return Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)


def _state(commander: Commander, robot: Robot) -> GameState:
    return create_game_state(
        0, (PLAYER_ONE, PLAYER_TWO), commanders=[commander], robots=[robot]
    )


def _run(state: GameState, world: WorldMap, ticks: int) -> tuple[GameState, list[object]]:
    events: list[object] = []
    for _tick in range(ticks):
        state, tick_events = step(state, [], world=world)
        events.extend(tick_events)
    return state, events


# -- robot top ------------------------------------------------------------------------


@pytest.mark.parametrize(("anchor", "ground"), [(FLAT_ANCHOR, 0), *RAISED])
def test_robot_top_is_terrain_altitude_plus_stack_height(
    world: WorldMap, anchor: tuple[int, int], ground: int
) -> None:
    robot = _robot(*anchor)
    assert unit_surface_height(world, *anchor) == ground
    assert robot_top(world, robot) == ground + robot.height


def test_war_base_exits_are_flat_like_the_spectrums_launch_altitude(world: WorldMap) -> None:
    # `La6c8`/`Lc849` set ROBOT_STRUCT_ALTITUDE to 0 at launch; deriving the
    # altitude from the exit anchor agrees on every war base of the map.
    for war_base in world.war_bases:
        exit_cell = _resolve_exit_cell(world, war_base.id)
        assert exit_cell is not None
        assert unit_surface_height(world, *exit_cell) == 0


# -- docking (La69a, Lb099) -----------------------------------------------------------


@pytest.mark.parametrize(("anchor", "ground"), RAISED)
def test_commander_docks_on_a_raised_friendly_robot_at_its_top(
    world: WorldMap, anchor: tuple[int, int], ground: int
) -> None:
    robot = _robot(*anchor)
    top = ground + robot.height
    state = _state(_free(*anchor, top + 3), robot)

    state, events = _run(state, world, 6 * DEFAULT_RULES.commander_vertical_update_ticks)

    commander = state.commanders[0]
    assert commander.mode is CommanderMode.DOCKED
    assert commander.docked_robot_id == ROBOT_ID
    assert commander.altitude == top
    docked = [e for e in events if isinstance(e, CommanderDockedEvent)]
    assert [e.altitude for e in docked] == [top]


@pytest.mark.parametrize(("anchor", "ground"), RAISED)
def test_commander_rests_on_a_raised_enemy_robot_without_docking(
    world: WorldMap, anchor: tuple[int, int], ground: int
) -> None:
    robot = _robot(*anchor, owner=PLAYER_TWO)
    top = ground + robot.height
    state = _state(_free(*anchor, top + 3), robot)

    state, _events = _run(state, world, 8 * DEFAULT_RULES.commander_vertical_update_ticks)

    commander = state.commanders[0]
    assert commander.mode is CommanderMode.FREE
    assert commander.altitude == top


def _docked(robot: Robot, altitude: int, *, rising: bool = False) -> Commander:
    return Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=robot.x,
        y=robot.y,
        altitude=altitude,
        docked_robot_id=robot.entity_id,
        rising=rising,
    )


def test_docked_commander_rides_the_raised_top(world: WorldMap) -> None:
    robot = _robot(*MOUNTAIN_ANCHOR)
    # Docked at the ground-rooted height (pre-CR002.25): follow lifts it.
    state = _state(_docked(robot, robot.height), robot)
    state, _events = step(state, [], world=world)
    assert state.commanders[0].altitude == 6 + robot.height


def test_undock_lift_starts_from_the_raised_top(world: WorldMap) -> None:
    robot = _robot(*ROUGH_3_ANCHOR)
    top = 3 + robot.height
    state = _state(_docked(robot, top, rising=True), robot)

    state, events = step(state, [], world=world)
    undocked = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert [(e.from_altitude, e.to_altitude) for e in undocked] == [(top, top)]

    peak = top + DEFAULT_RULES.commander_exit_elevate_updates * DEFAULT_RULES.commander_ascent_step
    state, _events = _run(
        state,
        world,
        DEFAULT_RULES.commander_exit_elevate_updates * DEFAULT_RULES.commander_vertical_update_ticks,
    )
    assert state.commanders[0].altitude == peak


def test_docked_altitude_changes_when_the_move_onto_higher_rough_completes(
    world: WorldMap,
) -> None:
    # Rough-2 (56, 14) -> rough-3 (55, 14): the top follows the authoritative
    # anchor, which the engine moves when the step completes (the Spectrum
    # moves the cell and the altitude together, `Lb495`).
    origin, dest = (56, 14), (55, 14)
    assert unit_surface_height(world, *origin) == 2
    assert unit_surface_height(world, *dest) == 3
    duration = 6
    robot = _robot(*origin)
    moving = Robot(
        entity_id=robot.entity_id,
        owner=robot.owner,
        x=robot.x,
        y=robot.y,
        build=robot.build,
        stack=robot.stack,
        height=robot.height,
        movement=RobotMoveTransition(
            entity_id=robot.entity_id,
            from_x=origin[0],
            from_y=origin[1],
            to_x=dest[0],
            to_y=dest[1],
            started_tick=0,
            duration_ticks=duration,
        ),
    )
    state = _state(_docked(robot, 2 + robot.height), moving)

    altitudes = []
    for _tick in range(duration + 1):
        state, _events = step(state, [], world=world)
        altitudes.append(state.commanders[0].altitude)
        if state.robots[0].movement is None:
            break
    assert (state.robots[0].x, state.robots[0].y) == dest
    assert altitudes[:-1] == [2 + robot.height] * (len(altitudes) - 1)
    assert altitudes[-1] == 3 + robot.height


# -- ship vs robot collision (Lb052/Lb099, Lb513) -------------------------------------


def test_ship_cannot_fly_into_a_raised_robot_below_its_top(world: WorldMap) -> None:
    # Robot body on the mountain at columns 167..168; the ship at anchor 165
    # moves east to 166, whose body (166..167) overlaps it.
    robot = _robot(*MOUNTAIN_ANCHOR, owner=PLAYER_TWO)
    top = 6 + robot.height
    x, y = MOUNTAIN_ANCHOR
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    # Above the ground-rooted height (robot.height) but below the raised top.
    below = _state(_free(x - 2, y, robot.height + 2), robot)
    below, _events = step(below, [move], world=world)
    assert below.commanders[0].horizontal_transition is None

    level = _state(_free(x - 2, y, top), robot)
    level, _events = step(level, [move], world=world)
    assert level.commanders[0].horizontal_transition is not None


def test_robot_is_blocked_by_a_ship_below_its_raised_top(world: WorldMap) -> None:
    # Lb513: the ship is an obstacle while altitude < height + altitude.
    robot = _robot(*MOUNTAIN_ANCHOR)
    top = 6 + robot.height
    x, y = MOUNTAIN_ANCHOR
    request = RobotMoveRequest(entity_id=robot.entity_id, dx=1, dy=0)

    for altitude, blocked in [(robot.height, True), (top - 1, True), (top, False)]:
        ship = Commander(
            player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=x + 2, y=y, altitude=altitude
        )
        state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO), commanders=[ship], robots=[robot])
        result = validate_robot_move(request, state, world, DEFAULT_RULES)
        if blocked:
            assert result.reason is MovementRejectionReason.COMMANDER_BLOCKED
        else:
            assert result.accepted


# -- commander ejection ---------------------------------------------------------------


def test_commander_is_ejected_at_the_raised_top_when_its_robot_is_destroyed(
    world: WorldMap,
) -> None:
    robot = Robot(
        entity_id=ROBOT_ID,
        owner=PLAYER_ONE,
        x=MOUNTAIN_ANCHOR[0],
        y=MOUNTAIN_ANCHOR[1],
        build=_robot(0, 1).build,
        stack=_robot(0, 1).stack,
        height=_robot(0, 1).height,
        strength=1,
    )
    top = 6 + robot.height
    state = _state(_docked(robot, top), robot)

    state, events = apply_damage(state, world, ROBOT_ID, ModuleIdentity.CANNON, DEFAULT_RULES, 5)
    # Still docked while it blinks; ejected when it is removed (tick 24).
    assert state.commanders[0].mode is CommanderMode.DOCKED
    for tick in (8, 12, 16, 20, 24):
        state, events = advance_destroyed_robots(state, world, tick)

    assert state.robots == ()
    commander = state.commanders[0]
    assert commander.mode is CommanderMode.FREE
    assert commander.altitude == top
    undocked = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert [e.to_altitude for e in undocked] == [top]


# -- bullet altitude gate (owner decision 2026-09-22) ---------------------------------


def _east_bullet(x: int, y: int, z: int = DEFAULT_RULES.normal_projectile_altitude) -> Projectile:
    return Projectile(
        id=EntityId("projectile-1"),
        owner=PLAYER_ONE,
        source_robot_id=EntityId("robot-gun"),
        weapon=ModuleIdentity.CANNON,
        x=x,
        y=y,
        z=z,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=40,
        created_tick=0,
    )


def _advance_bullet_at(
    world: WorldMap, anchor: tuple[int, int], rules: EngineRules
) -> tuple[list[object], GameState]:
    robot = _robot(*anchor, owner=PLAYER_TWO)
    x, y = anchor
    # The bullet lands at anchor x - 1, whose 2×2 body overlaps the robot's.
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=[robot]).with_projectiles(
        (_east_bullet(x - 3, y, rules.normal_projectile_altitude),)
    )
    assert unit_surface_height(world, x - 1, y) < rules.normal_projectile_altitude
    state, events = advance_projectiles(state, world, tick=4, rules=rules)
    return list(events), state


def _assert_hit(events: list[object], state: GameState) -> None:
    assert state.projectiles == ()
    assert [(e.reason, e.hit_robot_id) for e in events if isinstance(e, ProjectileTerminatedEvent)] == [
        (ProjectileTerminationReason.ROBOT_HIT, ROBOT_ID)
    ]


@pytest.mark.parametrize("anchor", [FLAT_ANCHOR, MOUNTAIN_ANCHOR])
def test_bullet_hits_the_shortest_spectrum_robot_even_on_flat_ground(
    world: WorldMap, anchor: tuple[int, int]
) -> None:
    # CR003.3: tracks + cannon is 7 + 6 = 13, the Spectrum's shortest robot;
    # its top clears the bullet altitude (10) even at ground 0.
    robot = _robot(*anchor, owner=PLAYER_TWO)
    assert robot.height == 13
    assert robot_top(world, robot) >= DEFAULT_RULES.normal_projectile_altitude
    _assert_hit(*_advance_bullet_at(world, anchor, DEFAULT_RULES))


@pytest.mark.parametrize(("anchor", "hit"), [(MOUNTAIN_ANCHOR, True), (FLAT_ANCHOR, False)])
def test_bullet_gate_uses_the_robot_top(
    world: WorldMap, anchor: tuple[int, int], hit: bool
) -> None:
    # The §8 gate still compares the robot's top (ground + height), not its
    # height alone: with the bullet raised to 17, the height-13 robot is
    # flown over on flat ground (top 13) and hit on a mountain (top 19).
    rules = replace(DEFAULT_RULES, normal_projectile_altitude=17)
    x, y = anchor
    events, state = _advance_bullet_at(world, anchor, rules)

    if hit:
        _assert_hit(events, state)
    else:
        assert events == []
        assert [(p.x, p.y) for p in state.projectiles] == [(x - 1, y)]
