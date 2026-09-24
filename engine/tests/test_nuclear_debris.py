"""A nuclear blast turns scenery boxes into rough debris; fences survive (CR002.18, #196).

Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`):

- `Lba02_look_for_robots_in_range_of_nuclear_bomb` walks the trimmed 9x9
  window around the carrier (row widths 5, 7, 9, 9, 9, 9, 9, 7, 5). For every
  window cell, `Lba44_robots_handled` skips cells with map bit 5 set (not the
  bottom-left corner of a 2x2 element) and element types outside 17..20
  ("do not destroy terrain" below 17; type 21 is "the fences that mark the end
  of the map"). Every other element is overwritten, through
  `Lbd91_add_element_to_map` at the same anchor, with debris of type 6 or 7.
- `Ld7bc_map_piece_heights`: types 6 and 7 are both height 3. Height 3 selects
  the rugged speed row (`Lb5f3_determine_speed_based_on_terrain`), and type
  < 8 blocks no chassis (`Lb513_get_robot_movement_possibilities`). So debris
  is exactly the map's native rough terrain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nether_earth.collision import commander_horizontal_move_allowed
from nether_earth.combat import FireCommand, Projectile, advance_projectiles
from nether_earth.commander import Commander, CommanderMode
from nether_earth.destruction import effective_world, execute_nuclear_detonation, scenery_world
from nether_earth.engine import step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    RobotMoveStartedEvent,
    validate_robot_move,
)
from nether_earth.robot import Robot, RobotFacing
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.snapshot import snapshot_to_json_string, to_snapshot
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component, StructureValidationError, parse_blockers
from nether_earth.terrain import TerrainGrid, TerrainType

MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

#: On the original map, box_low ``blocker-9`` covers (16..17, 13..14) and the
#: west fence covers x 12..13. A carrier at (16, 11) has the box anchor
#: (16, 14) and the fence anchors (12, 9), (12, 11), (12, 13) in its window.
CARRIER_CELL = (16, 11)
BOX_ID = EntityId("blocker-9")
BOX_CELLS = {(16, 13), (17, 13), (16, 14), (17, 14)}
FENCES_IN_WINDOW = {EntityId("blocker-5"), EntityId("blocker-6"), EntityId("blocker-7")}
MOVER_CELL = (14, 14)  # 2×2 body (14..15, 13..14): free ground between the fence and the box


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_world_map(MAP_PATH)


def _robot(
    entity_id: str,
    x: int,
    y: int,
    *,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    facing: RobotFacing = RobotFacing.EAST,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id), owner=PLAYER_ONE, x=x, y=y, build=build, stack=stack, height=height,
        facing=facing,
    )


def _carrier(x: int, y: int) -> Robot:
    return _robot("robot-nuke", x, y, weapons=(ModuleIdentity.NUCLEAR,))


def _state(*robots: Robot) -> GameState:
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=list(robots))


def _blast(world: WorldMap, x: int, y: int) -> GameState:
    carrier = _carrier(x, y)
    state, _events = execute_nuclear_detonation(_state(carrier), world, carrier.entity_id, tick=1)
    return state


def _anchor(blocker: Blocker) -> tuple[int, int]:
    return min(c.x for c in blocker.components), max(c.y for c in blocker.components)


# -- data --------------------------------------------------------------------------------


def test_only_the_boxes_are_destructible_on_the_original_map(world: WorldMap) -> None:
    # Types 17 (box_low) and 18 (box_high) are in 17..20; the type-21 fence is not.
    for blocker in world.blockers:
        assert blocker.destructible is (blocker.kind != "fence"), blocker.id
    assert sum(b.destructible for b in world.blockers) == 149


def test_destructible_is_an_optional_boolean() -> None:
    component = {"x": 1, "y": 1, "height": 7}
    plain, marked = parse_blockers(
        [
            {"id": "a", "components": [component]},
            {"id": "b", "components": [component | {"x": 2}], "destructible": True},
        ]
    )
    assert (plain.destructible, marked.destructible) == (False, True)
    with pytest.raises(StructureValidationError, match="destructible"):
        parse_blockers([{"id": "c", "components": [component], "destructible": "yes"}])


# -- the blast ---------------------------------------------------------------------------


def test_blast_next_to_a_box_turns_it_into_rough_debris_and_keeps_the_fences(world: WorldMap) -> None:
    state = _blast(world, *CARRIER_CELL)
    assert state.scenery_debris == (BOX_ID,)

    live = effective_world(world, state)
    assert BOX_ID not in {b.id for b in live.blockers}
    assert FENCES_IN_WINDOW <= {b.id for b in live.blockers}
    occupancy = live.occupancy()
    for x, y in BOX_CELLS:
        assert live.terrain.terrain_at(x, y) is TerrainType.ROUGH
        assert not occupancy.is_occupied(x, y)
    for fence_x in (12, 13):
        assert occupancy.is_occupied(fence_x, 11)
    # The base map is untouched.
    assert BOX_ID in {b.id for b in world.blockers}
    assert world.terrain.terrain_at(16, 13) is TerrainType.NORMAL


def _tiny_world(*blockers: Blocker) -> WorldMap:
    return WorldMap(
        map_id="debris",
        version=1,
        width=30,
        height=30,
        terrain=TerrainGrid(width=30, height=30, cells={}),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _box(blocker_id: str, x: int, y: int, *, destructible: bool = True) -> Blocker:
    """A 2x2 box stamped like `Lbd91_add_element_to_map`: anchor (x, y), cells x..x+1, y-1..y."""
    cells = ((x, y), (x + 1, y), (x, y - 1), (x + 1, y - 1))
    return Blocker(
        id=EntityId(blocker_id),
        components=tuple(Component(x=cx, y=cy, height=15) for cx, cy in cells),
        destructible=destructible,
    )


def test_only_the_anchor_cell_is_tested_against_the_window() -> None:
    # Carrier (10, 10); the dy = +4 row has width 5 (|dx| <= 2).
    cells_in = _box("cells-in", 8, 15)  # anchor dy = +5: outside, though (8, 14) is inside
    anchor_in = _box("anchor-in", 12, 14)  # anchor dy = +4, |dx| = 2: inside; (13, 14) is not
    far = _box("far", 20, 10)
    kept = _box("fence-like", 6, 11, destructible=False)  # anchor inside the window
    world = _tiny_world(cells_in, anchor_in, far, kept)

    state = _blast(world, 10, 10)

    assert state.scenery_debris == (EntityId("anchor-in"),)
    live = effective_world(world, state)
    assert [b.id.value for b in live.blockers] == ["cells-in", "far", "fence-like"]
    assert {(x, y) for (x, y), t in live.terrain.cells.items() if t is TerrainType.ROUGH} == {
        (12, 14), (13, 14), (12, 13), (13, 13)
    }


def test_debris_accumulates_across_blasts_in_canonical_order() -> None:
    world = _tiny_world(_box("b-2", 4, 10), _box("b-1", 20, 10))
    state = _blast(world, 20, 12)
    carrier = _carrier(4, 12)
    state, _ = execute_nuclear_detonation(
        state.with_robots((carrier,)), world, carrier.entity_id, tick=2
    )
    assert state.scenery_debris == (EntityId("b-1"), EntityId("b-2"))
    # A second blast over existing debris adds nothing (the blocker is gone).
    carrier = _carrier(4, 12)
    again, _ = execute_nuclear_detonation(
        state.with_robots((carrier,)), world, carrier.entity_id, tick=3
    )
    assert again.scenery_debris == state.scenery_debris


def test_effective_world_is_memoized_after_a_blast(world: WorldMap) -> None:
    state = _blast(world, *CARRIER_CELL)
    assert effective_world(world, state) is effective_world(world, state)
    assert scenery_world(world, state) is scenery_world(world, state)
    assert scenery_world(world, _state()) is world


# -- consequences ------------------------------------------------------------------------


def test_robots_cross_debris_at_rough_speed_through_engine_step(world: WorldMap) -> None:
    carrier = _carrier(*CARRIER_CELL)
    state = _state(carrier)
    fire = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=carrier.entity_id,
        weapon=ModuleIdentity.NUCLEAR,
    )
    move_east = RobotMoveRequest(entity_id=EntityId("robot-mover"), dx=1, dy=0)

    # Before the blast the box blocks the move.
    before = _state(_robot("robot-mover", *MOVER_CELL))
    rejected = validate_robot_move(move_east, before, world)
    assert rejected.reason is MovementRejectionReason.OCCUPIED

    state, _events = step(state, [fire], world=world)
    assert state.scenery_debris == (BOX_ID,)

    state = state.with_robots((_robot("robot-mover", *MOVER_CELL),))
    state, events = step(state, [], world=world, robot_moves=[move_east])
    started = [e for e in events if isinstance(e, RobotMoveStartedEvent)]
    assert [(e.to_x, e.to_y, e.duration_ticks) for e in started] == [
        (15, 14, DEFAULT_RULES.robot_move_ticks_bipod_rough)
    ]


def test_commander_flies_over_debris_at_rough_piece_height(world: WorldMap) -> None:
    # CR002.21: debris is a rough piece of type 6/7, 3 high (Ld7bc_map_piece_heights).
    state = _blast(world, *CARRIER_CELL)
    debris_world = scenery_world(world, state)
    assert world.terrain.debris_height == 3
    low = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=15, y=13, altitude=0)
    at_top = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=15, y=13, altitude=3)
    assert not commander_horizontal_move_allowed(_state(), at_top, 16, 13, world=world)
    assert not commander_horizontal_move_allowed(state, low, 16, 13, world=debris_world)
    assert commander_horizontal_move_allowed(state, at_top, 16, 13, world=debris_world)


def test_projectiles_fly_over_a_debris_box_high(world: WorldMap) -> None:
    # box_high (15) stops a bullet at altitude 10; its debris does not.
    box = next(b for b in world.blockers if b.kind == "box_high" and b.id == EntityId("blocker-11"))
    ax, ay = _anchor(box)
    state = _blast(world, ax, ay + 3)
    assert box.id in state.scenery_debris
    live = effective_world(world, state)
    firer = _robot("robot-gun", ax - 3, ay)
    projectile = Projectile(
        id=EntityId("projectile-1"),
        owner=PLAYER_ONE,
        source_robot_id=firer.entity_id,
        weapon=ModuleIdentity.CANNON,
        x=ax - 1,
        y=ay,
        z=DEFAULT_RULES.normal_projectile_altitude,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=20,
        created_tick=0,
    )
    flying = state.with_robots((firer.with_active_projectile(projectile.id),)).with_projectiles(
        (projectile,)
    )
    blocked, blocked_events = advance_projectiles(flying, world, tick=4)
    assert blocked.projectiles == () and blocked_events
    passed, passed_events = advance_projectiles(flying, live, tick=4)
    assert passed_events == ()
    assert [(p.x, p.y) for p in passed.projectiles] == [(ax + 1, ay)]


# -- determinism / protocol --------------------------------------------------------------


def test_blast_replay_is_deterministic_and_snapshotted(world: WorldMap) -> None:
    def run() -> str:
        carrier = _carrier(*CARRIER_CELL)
        state = _state(carrier)
        fire = FireCommand(
            player=PLAYER_ONE,
            sequence=0,
            entity_id=carrier.entity_id,
            weapon=ModuleIdentity.NUCLEAR,
        )
        state, _ = step(state, [fire], world=world)
        for _tick in range(5):
            state, _ = step(state, [], world=world)
        return snapshot_to_json_string(state)

    first = run()
    assert first == run()
    state = _blast(world, *CARRIER_CELL)
    assert to_snapshot(state)["scenery_debris"] == ["blocker-9"]


def test_scenery_debris_state_is_canonical() -> None:
    state = create_game_state(0, (PLAYER_ONE,), scenery_debris=[EntityId("b"), EntityId("a")])
    assert state.scenery_debris == (EntityId("a"), EntityId("b"))
    # Carried through unrelated transitions.
    assert state.with_tick(5).with_robots(()).scenery_debris == state.scenery_debris
    with pytest.raises(ValueError, match="debris"):
        state.with_scenery_debris((EntityId("a"), EntityId("a")))
