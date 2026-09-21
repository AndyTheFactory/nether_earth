"""Terrain piece heights for the commander, projectiles and damage (CR002.21, #203).

Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`):

- `Ld7bc_map_piece_heights` (23 entries, by element type):
  ``0 0 2 2 2 2 3 3 6 6 6 6 0 0 0 7 15 7 15 0 0 99 0``: rough types 2-5 are
  2 high, rough types 6/7 (and nuclear debris) 3, mountains 6, ditches 0.
- `Lb052_check_player_collision` takes the highest piece (`Lb08a`) under the
  ship's 2×2 area; a horizontal move is refused when ``altitude < height``
  and `Lafc3_gravity` only drops the ship while ``altitude - 1 >= height``,
  so the ship rests on top of terrain.
- `Lb724_bullet_update_internal` stops a bullet when `Lb5d6_map_altitude_2x2`
  ``>=`` its altitude (10): no terrain piece (max 6) is that high.
- `Lb495` stores `Lb5d6` at the robot as `ROBOT_STRUCT_ALTITUDE`, which the
  damage formula subtracts (`Lb7a7`): ``(60 - (height + altitude)) / 4``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from nether_earth.collision import (
    commander_horizontal_move_allowed,
    commander_vertical_move_allowed,
    surface_height_at,
    unit_surface_height,
)
from nether_earth.combat import Projectile, advance_projectiles, apply_damage, ground_height_at
from nether_earth.commander import Commander, CommanderMode
from nether_earth.engine import step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.state import GameState, create_game_state
from nether_earth.terrain import TerrainType, TerrainValidationError, parse_terrain_grid

MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

# 2×2 anchors on the original map (x..x+1, y-1..y) whose four cells share one height.
ROUGH_2_ANCHOR = (32, 12)  # element types 2-5
ROUGH_3_ANCHOR = (54, 14)  # element types 6/7
MOUNTAIN_ANCHOR = (167, 9)  # element types 8-11


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_world_map(MAP_PATH)


def _commander(x: int, y: int, altitude: int) -> Commander:
    return Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)


def _state(*commanders: Commander, robots: tuple[Robot, ...] = ()) -> GameState:
    return create_game_state(
        0, (PLAYER_ONE, PLAYER_TWO), commanders=list(commanders), robots=list(robots)
    )


def _robot(entity_id: str, x: int, y: int) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id), owner=PLAYER_TWO, x=x, y=y, build=build, stack=stack, height=height
    )


# -- data ------------------------------------------------------------------------------


def test_original_map_piece_heights_per_class(world: WorldMap) -> None:
    # Decoded counts (zx-spectrum-original.md, "Terrain"): rough types 2-5 are
    # 80 + 88 + 84 + 68 = 320 cells, types 6/7 12 + 12 = 24, mountains 436.
    per_class = Counter(
        (world.terrain.terrain_at(x, y), world.terrain.height_at(x, y))
        for x, y in world.terrain.cells
    )
    assert per_class == {
        (TerrainType.ROUGH, 2): 320,
        (TerrainType.ROUGH, 3): 24,
        (TerrainType.MOUNTAIN, 6): 436,
        (TerrainType.DITCH, 0): 204,
    }
    assert world.terrain.debris_height == 3
    assert world.version == 1


def test_terrain_height_is_optional_and_validated() -> None:
    grid = parse_terrain_grid({"cells": [{"x": 1, "y": 1, "type": "rough"}]}, 4, 4)
    assert grid.height_at(1, 1) == 0 and grid.debris_height == 0
    grid = parse_terrain_grid(
        {"debris_height": 3, "cells": [{"x": 1, "y": 1, "type": "rough", "height": 2}]}, 4, 4
    )
    assert grid.height_at(1, 1) == 2 and grid.height_at(2, 2) == 0 and grid.debris_height == 3
    for bad in ({"cells": [{"x": 1, "y": 1, "type": "rough", "height": -1}]}, {"debris_height": "3"}):
        with pytest.raises(TerrainValidationError):
            parse_terrain_grid(bad, 4, 4)


def test_one_surface_height_for_terrain_and_structures(world: WorldMap) -> None:
    assert surface_height_at(world, 22, 12) == 0  # open ground
    assert surface_height_at(world, 18, 3) == 15  # war-base tall block
    assert surface_height_at(world, *ROUGH_2_ANCHOR) == 2
    assert surface_height_at(world, *ROUGH_3_ANCHOR) == 3
    assert surface_height_at(world, *MOUNTAIN_ANCHOR) == 6
    assert surface_height_at(world, -1, 0) == 0 and surface_height_at(world, 0, 16) == 0
    # Lb5d6: the highest of the four body cells.
    x, y = MOUNTAIN_ANCHOR
    assert unit_surface_height(world, x - 2, y) == 0
    assert unit_surface_height(world, x - 1, y) == 6


def test_commanders_spawn_above_the_surface(world: WorldMap) -> None:
    scenario = default_pvp_scenario()
    overlaid = apply_overlay(world, default_pvp_overlay(world))
    state = create_initial_state(scenario, world=overlaid, seed=1)
    for commander in state.commanders:
        assert unit_surface_height(overlaid, commander.x, commander.y) <= commander.altitude


# -- commander ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchor", "height", "side"),
    # (54, 14) has a mountain to the west, so it is entered from the east (2-high rough).
    [(ROUGH_2_ANCHOR, 2, -1), (ROUGH_3_ANCHOR, 3, 1), (MOUNTAIN_ANCHOR, 6, -1)],
)
def test_commander_cannot_fly_into_terrain_below_its_height(
    world: WorldMap, anchor: tuple[int, int], height: int, side: int
) -> None:
    x, y = anchor
    start, dest = x + 2 * side, x + side
    assert unit_surface_height(world, start, y) < height == unit_surface_height(world, dest, y)
    below = _commander(start, y, height - 1)
    on_top = _commander(start, y, height)
    assert not commander_horizontal_move_allowed(_state(below), below, dest, y, world=world)
    assert commander_horizontal_move_allowed(_state(on_top), on_top, dest, y, world=world)


@pytest.mark.parametrize(
    ("anchor", "height"), [(ROUGH_2_ANCHOR, 2), (ROUGH_3_ANCHOR, 3), (MOUNTAIN_ANCHOR, 6)]
)
def test_commander_cannot_descend_below_terrain_height(
    world: WorldMap, anchor: tuple[int, int], height: int
) -> None:
    commander = _commander(*anchor, height)
    state = _state(commander)
    assert commander_vertical_move_allowed(state, commander, height, world=world)
    assert not commander_vertical_move_allowed(state, commander, height - 1, world=world)


def test_gravity_rests_the_commander_at_3_on_rough(world: WorldMap) -> None:
    state = _state(_commander(*ROUGH_3_ANCHOR, 9))
    for _tick in range(20 * DEFAULT_RULES.commander_vertical_update_ticks):
        state, _events = step(state, [], world=world)
    assert state.commanders[0].altitude == 3


def test_gravity_rests_the_commander_on_a_mountain(world: WorldMap) -> None:
    state = _state(_commander(*MOUNTAIN_ANCHOR, 12))
    for _tick in range(20 * DEFAULT_RULES.commander_vertical_update_ticks):
        state, _events = step(state, [], world=world)
    assert state.commanders[0].altitude == 6


# -- projectiles ----------------------------------------------------------------------


def _east_projectile(x: int, y: int) -> Projectile:
    return Projectile(
        id=EntityId("projectile-1"),
        owner=PLAYER_ONE,
        source_robot_id=EntityId("robot-gun"),
        weapon=ModuleIdentity.CANNON,
        x=x,
        y=y,
        z=DEFAULT_RULES.normal_projectile_altitude,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=40,
        created_tick=0,
    )


def test_bullets_fly_over_every_terrain_piece(world: WorldMap) -> None:
    # Lb724: stop when Lb5d6 >= 10; mountains (6) are the highest terrain piece.
    assert max(world.terrain.heights.values()) < DEFAULT_RULES.normal_projectile_altitude
    x, y = MOUNTAIN_ANCHOR
    state = _state().with_projectiles((_east_projectile(x - 3, y),))
    state, events = advance_projectiles(state, world, tick=4)
    assert events == ()
    assert [(p.x, p.y) for p in state.projectiles] == [(x - 1, y)]
    assert unit_surface_height(world, x - 1, y) == 6


def test_a_terrain_piece_as_high_as_the_bullet_stops_it(world: WorldMap) -> None:
    # The same >= rule as for structures: terrain goes through the one surface function.
    x, y = MOUNTAIN_ANCHOR
    heights = dict(world.terrain.heights)
    heights[(x - 1, y)] = DEFAULT_RULES.normal_projectile_altitude
    tall = replace(world, terrain=replace(world.terrain, heights=heights))
    state = _state().with_projectiles((_east_projectile(x - 3, y),))
    state, events = advance_projectiles(state, tall, tick=4)
    assert state.projectiles == () and events


# -- damage ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchor", "ground"), [((22, 12), 0), (ROUGH_2_ANCHOR, 2), (ROUGH_3_ANCHOR, 3), (MOUNTAIN_ANCHOR, 6)]
)
def test_robots_on_high_ground_take_less_damage(
    world: WorldMap, anchor: tuple[int, int], ground: int
) -> None:
    robot = _robot("robot-target", *anchor)
    assert ground_height_at(world, robot.x, robot.y) == ground
    state = _state(robots=(robot,))
    damaged, _events = apply_damage(state, world, robot.entity_id, ModuleIdentity.CANNON, DEFAULT_RULES, 1)
    expected = 2 * ((60 - (robot.height + ground)) // 4)
    assert robot.strength - damaged.robots[0].strength == expected
