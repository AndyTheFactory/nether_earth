"""Robots killed in combat and nuked buildings leave rough debris (CR005.3).

Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`):

- `Lb116_robot_destroyed`: when a robot's strength reaches 0 it ORs the four
  map cells of its 2×2 body (masking out the robot mark); only when all are 0
  (plain ground: no terrain piece, structure, scenery or earlier debris) does
  it `Lbd91_add_element_to_map` a random type 6/7 piece there.
- `Lba44_robots_handled`: robots in a nuclear blast are removed directly
  (``ROBOT_STRUCT_MAP_PTR + 1 = 0``), without passing through `Lb116`.
- `Lbbd8_destroy_factory` / `Lbbf9_destroy_warbase` ->
  `Lbc27_replace_building_by_debris`: every part of the building with a
  non-zero type becomes a random type 6/7 piece.

Types 6/7 are the map's rough pieces of height 3 (`Ld7bc_map_piece_heights`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nether_earth.capture import CapturableStructureKind
from nether_earth.combat import apply_damage
from nether_earth.destruction import (
    advance_destroyed_robots,
    destroy_structure,
    execute_nuclear_detonation,
    scenery_world,
)
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState, RobotLaunchCount, create_game_state
from nether_earth.terrain import TerrainType

MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

PLAIN = (22, 12)  # body (22..23, 11..12): open ground
ROUGH = (32, 12)  # body on rough terrain pieces


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_world_map(MAP_PATH)


def _robot(x: int, y: int, *, strength: int = 1, weapons=(ModuleIdentity.CANNON,)) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId("robot-p1-1"), owner=PLAYER_ONE, x=x, y=y, build=build,
        stack=stack, height=height,
    ).with_strength(strength)


def _state(*robots: Robot) -> GameState:
    return create_game_state(
        0, (PLAYER_ONE, PLAYER_TWO), robots=list(robots),
        robot_launches=[RobotLaunchCount(PLAYER_ONE, 1)],
    )


def _kill(world: WorldMap, state: GameState) -> GameState:
    """Kill the robot on tick 1 and run its blink to the removal on tick 20."""
    debris_before = state.robot_debris
    state, _ = apply_damage(
        state, scenery_world(world, state), EntityId("robot-p1-1"), ModuleIdentity.CANNON,
        DEFAULT_RULES, tick=1,
    )
    # `Lb116` places the debris at the removal, not at the hit.
    assert state.robot_debris == debris_before
    for tick in (4, 8, 12, 16, 20):
        assert state.robot_for(EntityId("robot-p1-1")) is not None
        state, _ = advance_destroyed_robots(state, world, tick)
    assert state.robot_for(EntityId("robot-p1-1")) is None
    return state


def _cells(anchor: tuple[int, int]) -> list[tuple[int, int]]:
    x, y = anchor
    return [(x, y), (x + 1, y), (x, y - 1), (x + 1, y - 1)]


def test_a_robot_killed_on_plain_ground_leaves_rough_debris(world: WorldMap) -> None:
    state = _kill(world, _state(_robot(*PLAIN)))

    assert state.robot_debris == (PLAIN,)
    physical = scenery_world(world, state)
    for x, y in _cells(PLAIN):
        assert physical.terrain.terrain_at(x, y) is TerrainType.ROUGH
        assert physical.terrain.height_at(x, y) == world.terrain.debris_height
    assert to_snapshot(state)["robot_debris"] == [{"x": PLAIN[0], "y": PLAIN[1]}]


def test_a_damaged_robot_leaves_nothing(world: WorldMap) -> None:
    state, _ = apply_damage(
        _state(_robot(*PLAIN, strength=99)), world, EntityId("robot-p1-1"),
        ModuleIdentity.CANNON, DEFAULT_RULES, tick=1,
    )

    assert state.robot_debris == ()
    assert "robot_debris" not in to_snapshot(state)


def test_a_robot_killed_on_terrain_or_debris_leaves_nothing(world: WorldMap) -> None:
    assert _kill(world, _state(_robot(*ROUGH))).robot_debris == ()

    once = _kill(world, _state(_robot(*PLAIN)))
    twice = _kill(world, once.with_robots((_robot(*PLAIN),)))
    assert twice.robot_debris == (PLAIN,)

    # One cell of the body on debris is enough.
    beside = _kill(world, once.with_robots((_robot(PLAIN[0] + 1, PLAIN[1]),)))
    assert beside.robot_debris == (PLAIN,)


def test_robots_killed_by_a_nuclear_blast_leave_nothing(world: WorldMap) -> None:
    carrier = _robot(*PLAIN, weapons=(ModuleIdentity.NUCLEAR,))
    state, _ = execute_nuclear_detonation(_state(carrier), world, carrier.entity_id, tick=1)

    assert state.robot_for(carrier.entity_id) is None
    assert state.robot_debris == ()


def test_a_nuked_building_becomes_rough_debris(world: WorldMap) -> None:
    factory = world.factories[0]
    state, _ = destroy_structure(
        _state(), factory.id, CapturableStructureKind.FACTORY, tick=1
    )

    physical = scenery_world(world, state)
    assert all(f.id != factory.id for f in physical.factories)
    for component in factory.components:
        assert physical.terrain.terrain_at(component.x, component.y) is TerrainType.ROUGH
        assert physical.terrain.height_at(component.x, component.y) == world.terrain.debris_height
    assert not physical.occupancy().blocks_unit(factory.components[0].x, factory.components[0].y)
