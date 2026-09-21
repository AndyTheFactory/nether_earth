"""Scenery blockers on the original map (CR002.1, issue #168).

Evidence (`santiontanon/netherearth-disassembly` @ 762e33e,
`netherearth-annotated.asm`; see `data/maps/zx-spectrum-original.md`,
"Blockers/scenery"):

- robots: `Lb5cd_robot_map_collision_internal` rejects any element type index
  >= the chassis limit 8/12/15 set in `Lb513_get_robot_movement_possibilities`,
  so types 17/18/21 stop every chassis;
- commander: `Lb052_check_player_collision` reports a collision when
  ``player_altitude < max piece height`` (`cp c` / carry); horizontal moves
  (`Laf4c_move_player_if_no_collision`) and gravity (`Lafc3_gravity`) are
  refused on collision, so the ship crosses a block only at altitude >= its
  height and comes to rest on top of it;
- projectiles: `Lb724_bullet_update_internal` destroys the bullet when
  ``Lb5d6_map_altitude_2x2 >= BULLET_STRUCT_ALTITUDE`` (10, set by
  `Lb6d6_weapon_fire`), so height-7 boxes are overflown and 15/99 stop it.

Robots, the commander and projectiles are 2×2 bodies anchored at their
``(x, y)`` (CR002.3/CR002.4, `_specs/open-questions.md` §21), so the
approach positions below are body anchors.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from nether_earth.collision import (
    commander_horizontal_move_allowed,
    commander_vertical_move_allowed,
)
from nether_earth.combat import (
    Projectile,
    ProjectileTerminationReason,
    advance_projectiles,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    validate_robot_move,
)
from nether_earth.navigation import plan_route
from nether_earth.occupancy import unit_footprint_cells
from nether_earth.robot import Robot
from nether_earth.robot_build import CHASSIS_MODULES, ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, StructureValidationError, parse_blockers
from nether_earth.terrain import TerrainType

MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

# `Ld7bc_map_piece_heights`: type 17 -> 7, 18 -> 15, 21 -> 99.
KIND_HEIGHT = {"box_low": 7, "box_high": 15, "fence": 99}


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_world_map(MAP_PATH)


def _free(world: WorldMap, x: int, y: int) -> bool:
    """Whether a 2×2 body anchored at ``(x, y)`` stands on free, flat ground."""
    occupancy = world.occupancy()
    return all(
        0 <= cx < world.width
        and 0 <= cy < world.height
        and not occupancy.is_occupied(cx, cy)
        and world.terrain.terrain_at(cx, cy) is TerrainType.NORMAL
        for cx, cy in unit_footprint_cells(x, y)
    )


def _box_origin(blocker: Blocker) -> tuple[int, int]:
    """Return the west column and top row of a 2x2 blocker."""
    return min(c.x for c in blocker.components), min(c.y for c in blocker.components)


def _approach(world: WorldMap, kind: str) -> tuple[Blocker, tuple[int, int], tuple[int, int]]:
    """Return a ``kind`` blocker, a free body anchor just west of it, and the next anchor east.

    The body at the first anchor touches the blocker's west side row for
    row; the body at the second overlaps the blocker's west column.
    """
    for blocker in world.blockers:
        if blocker.kind != kind:
            continue
        bx, by = _box_origin(blocker)
        start = (bx - 2, by + 1)
        if _free(world, *start):
            return blocker, start, (start[0] + 1, start[1])
    raise AssertionError(f"no {kind} blocker with free ground to its west")


def _robot(chassis: ModuleIdentity, x: int, y: int, *, electronics: bool = False) -> Robot:
    build = RobotBuild(
        chassis=chassis,
        weapons=(ModuleIdentity.CANNON,),
        electronics=ModuleIdentity.ELECTRONICS if electronics else None,
    )
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId("robot-1"), owner=PLAYER_ONE, x=x, y=y, build=build, stack=stack, height=height
    )


def _state(robots: tuple[Robot, ...] = (), projectiles: tuple[Projectile, ...] = ()) -> GameState:
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=list(robots), projectiles=list(projectiles))


# -- data --------------------------------------------------------------------------------


def test_original_map_encodes_the_660_decoded_scenery_cells_as_2x2_blockers(world: WorldMap) -> None:
    assert Counter(b.kind for b in world.blockers) == {"box_low": 81, "box_high": 68, "fence": 16}
    assert sum(len(b.components) for b in world.blockers) == 660
    for blocker in world.blockers:
        xs = sorted({c.x for c in blocker.components})
        ys = sorted({c.y for c in blocker.components})
        # `Lbd91_add_element_to_map` stamps every element as one 2x2 block.
        assert len(blocker.components) == 4 and xs[1] == xs[0] + 1 and ys[1] == ys[0] + 1
        assert {c.height for c in blocker.components} == {KIND_HEIGHT[blocker.kind or ""]}
        for c in blocker.components:
            assert world.terrain.terrain_at(c.x, c.y) is TerrainType.NORMAL  # no terrain under scenery


def test_fences_close_both_ends_of_the_map(world: WorldMap) -> None:
    # `Lba44_robots_handled`: type 21 is "the fences that mark the end of the map in each end".
    cells = {(c.x, c.y) for b in world.blockers if b.kind == "fence" for c in b.components}
    assert cells == {(x, y) for x in (12, 13, 503, 504) for y in range(16)}


# -- robots ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(KIND_HEIGHT))
@pytest.mark.parametrize("chassis", sorted(CHASSIS_MODULES, key=lambda m: m.value))
def test_no_chassis_can_step_into_a_blocker(world: WorldMap, chassis: ModuleIdentity, kind: str) -> None:
    _, (x, y), (bx, by) = _approach(world, kind)
    robot = _robot(chassis, x, y)
    result = validate_robot_move(
        RobotMoveRequest(entity_id=robot.entity_id, dx=bx - x, dy=by - y), _state((robot,)), world
    )
    assert not result.accepted
    assert result.reason is MovementRejectionReason.OCCUPIED


@pytest.mark.parametrize("chassis", sorted(CHASSIS_MODULES, key=lambda m: m.value))
def test_navigation_routes_around_a_blocker(world: WorldMap, chassis: ModuleIdentity) -> None:
    start, goal = next(
        ((bx - 2, by + 1), (bx + 2, by + 1))
        for b in world.blockers
        if b.kind == "box_high"
        for bx, by in (_box_origin(b),)
        if _free(world, bx - 2, by + 1) and _free(world, bx + 2, by + 1)
    )
    robot = _robot(chassis, *start, electronics=True)

    route = plan_route(robot, *goal, _state((robot,)), world)

    assert route is not None and route[-1] == goal
    blocked = {(c.x, c.y) for b in world.blockers for c in b.components}
    for anchor in route:
        assert not blocked.intersection(unit_footprint_cells(*anchor))
    assert len(route) > goal[0] - start[0]  # a detour, not the straight line through the box


# -- commander ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["box_low", "box_high"])
def test_commander_crosses_a_box_only_at_or_above_its_height(world: WorldMap, kind: str) -> None:
    _, (x, y), (bx, by) = _approach(world, kind)
    height = KIND_HEIGHT[kind]
    state = _state()

    def at(altitude: int) -> Commander:
        return Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)

    assert not commander_horizontal_move_allowed(state, at(height - 1), bx, by, world=world)
    assert commander_horizontal_move_allowed(state, at(height), bx, by, world=world)


@pytest.mark.parametrize("kind", ["box_low", "box_high"])
def test_commander_comes_to_rest_on_top_of_a_box(world: WorldMap, kind: str) -> None:
    _, _, (bx, by) = _approach(world, kind)
    height = KIND_HEIGHT[kind]
    state = _state()
    above = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=bx, y=by, altitude=height + 1)
    assert commander_vertical_move_allowed(state, above, height, world=world)
    on_top = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=bx, y=by, altitude=height)
    assert not commander_vertical_move_allowed(state, on_top, height - 1, world=world)


def test_commander_can_never_cross_a_fence(world: WorldMap) -> None:
    # Fences are 99 high; the ship tops out at MAX_PLAYER_ALTITUDE = 48.
    _, (x, y), (bx, by) = _approach(world, "fence")
    top = Commander(
        player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=x, y=y, altitude=DEFAULT_RULES.commander_max_altitude
    )
    assert not commander_horizontal_move_allowed(_state(), top, bx, by, world=world)


# -- projectiles -------------------------------------------------------------------------


def _projectile_towards(world: WorldMap, kind: str) -> tuple[GameState, tuple[int, int]]:
    _, (x, y), (bx, by) = _approach(world, kind)
    firer = _robot(ModuleIdentity.BIPOD, x, y)
    firer = Robot(
        entity_id=firer.entity_id,
        owner=firer.owner,
        x=x,
        y=y,
        build=firer.build,
        stack=firer.stack,
        height=firer.height,
        active_projectile_id=EntityId("projectile-1"),
    )
    projectile = Projectile(
        id=EntityId("projectile-1"),
        owner=PLAYER_ONE,
        source_robot_id=firer.entity_id,
        weapon=ModuleIdentity.CANNON,
        x=x,
        y=y,
        z=DEFAULT_RULES.normal_projectile_altitude,
        dx=bx - x,
        dy=by - y,
        travelled_cells=0,
        max_range_cells=20,
        created_tick=0,
    )
    return _state((firer,), (projectile,)), (bx, by)


def test_projectile_flies_over_a_low_box(world: WorldMap) -> None:
    state, (bx, by) = _projectile_towards(world, "box_low")
    assert KIND_HEIGHT["box_low"] < DEFAULT_RULES.normal_projectile_altitude
    new_state, events = advance_projectiles(state, world, tick=4)
    # One advance crosses both columns of the 2x2 box without terminating.
    assert events == ()
    assert [(p.x, p.y) for p in new_state.projectiles] == [(bx + 1, by)]


@pytest.mark.parametrize("kind", ["box_high", "fence"])
def test_projectile_is_stopped_by_a_high_box_or_fence(world: WorldMap, kind: str) -> None:
    state, _ = _projectile_towards(world, kind)
    new_state, events = advance_projectiles(state, world, tick=4)
    assert new_state.projectiles == ()
    assert [e.reason for e in events] == [ProjectileTerminationReason.STATIC_COLLISION]


# -- parsing -----------------------------------------------------------------------------


def test_blocker_kind_is_optional_opaque_data() -> None:
    component = {"x": 1, "y": 1, "height": 7}
    with_kind, without_kind = parse_blockers(
        [
            {"id": "a", "kind": "any_asset_label", "components": [component]},
            {"id": "b", "components": [{**component, "x": 2}]},
        ]
    )
    assert with_kind.kind == "any_asset_label"
    assert without_kind.kind is None


@pytest.mark.parametrize("bad", ["", "  ", 17, ["box"]])
def test_blocker_kind_must_be_a_non_empty_string(bad: object) -> None:
    with pytest.raises(StructureValidationError, match="kind"):
        parse_blockers([{"id": "a", "kind": bad, "components": [{"x": 1, "y": 1, "height": 7}]}])
