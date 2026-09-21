"""M9.3 -- original-map geometry, interaction and ownership fidelity smoke tests (issue #115).

Real-map checks only (``data/maps/zx-spectrum-original.yaml`` with the
standard PvP overlay). Each assertion is tied to evidence recorded in
``docs/milestone-9/map-fidelity-pass.md`` and ``data/maps/zx-spectrum-original.md``;
anything the evidence does not settle (heli-pad roof placement, terrain,
scenery) is deliberately *not* asserted here -- see open-questions §18.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from nether_earth.collision import commander_horizontal_move_allowed
from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import commander_spawn_key, create_initial_state, default_pvp_scenario
from nether_earth.structures import Factory, WarBase
from nether_earth.terrain import TerrainType

ORIGINAL_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

#: Disassembly evidence: every war base is stamped from one template
#: (`Lbfb2_warbase`), every factory from another (`Lbfe2_factory`), and
#: the interpreter writes 2x2 tile blocks -> exactly two component heights.
WAR_BASE_COMPONENT_COUNT = 60
FACTORY_COMPONENT_COUNT = 20
COMPONENT_HEIGHTS = {7, 15}

#: Verified anchors (`Lbf46_warbases_factories_part1/2`), left to right.
WAR_BASE_ANCHORS = {
    "warbase-1": (22, 9),
    "warbase-2": (261, 8),
    "warbase-3": (369, 8),
    "warbase-4": (494, 8),
}


@pytest.fixture(scope="module")
def world() -> WorldMap:
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def _anchor(world: WorldMap, structure: WarBase | Factory) -> tuple[int, int]:
    kind = (
        InteractionKind.WARBASE_CAPTURE
        if isinstance(structure, WarBase)
        else InteractionKind.FACTORY_CAPTURE
    )
    points = world.interaction_points_for(structure.id, kind=kind)
    assert len(points) == 1, f"{structure.id.value} must declare exactly one {kind.value} point"
    cells = sorted(points[0].footprint.cells)
    assert len(cells) == 1, f"{structure.id.value} capture footprint must be a single cell"
    return cells[0]


def _relative_shape(structure: WarBase | Factory, anchor: tuple[int, int]) -> tuple[tuple[int, int, int], ...]:
    return tuple(sorted((c.x - anchor[0], c.y - anchor[1], c.height) for c in structure.components))


# -- geometry ------------------------------------------------------------------


def test_map_dimensions_match_the_original(world: WorldMap) -> None:
    # MAP_LENGTH equ 512 / MAP_WIDTH equ 16 in the disassembly.
    assert (world.width, world.height) == (512, 16)


def test_every_component_is_in_bounds_and_occupancy_has_no_overlaps(world: WorldMap) -> None:
    for structure in (*world.war_bases, *world.factories, *world.blockers):
        for component in structure.components:
            assert 0 <= component.x < world.width and 0 <= component.y < world.height
            assert component.height in COMPONENT_HEIGHTS
    # ``OccupancyGrid.from_structures`` raises on any two structures sharing a cell.
    assert world.occupancy().cells()


def test_war_base_anchors_and_shared_template(world: WorldMap) -> None:
    shapes = set()
    for base in world.war_bases:
        anchor = _anchor(world, base)
        assert anchor == WAR_BASE_ANCHORS[base.id.value]
        assert len(base.components) == WAR_BASE_COMPONENT_COUNT
        shapes.add(_relative_shape(base, anchor))
    assert len(shapes) == 1, "all four war bases must share one physical template"


def test_factories_share_one_template_and_cover_every_production_type(world: WorldMap) -> None:
    assert len(world.factories) == 24
    shapes = set()
    for factory in world.factories:
        assert len(factory.components) == FACTORY_COMPONENT_COUNT
        shapes.add(_relative_shape(factory, _anchor(world, factory)))
    assert len(shapes) == 1, "all 24 factories must share one physical template"
    types = {factory.factory_type.value for factory in world.factories}
    assert types == {"chassis", "electronics", "nuclear", "missile", "phaser", "cannon"}


# -- interaction points ----------------------------------------------------------


def test_each_war_base_declares_one_capture_one_heli_pad_one_exit_on_free_ground(world: WorldMap) -> None:
    occupancy = world.occupancy()
    for base in world.war_bases:
        by_kind = {
            kind: world.interaction_points_for(base.id, kind=kind)
            for kind in (InteractionKind.WARBASE_CAPTURE, InteractionKind.HELI_PAD, InteractionKind.EXIT)
        }
        assert all(len(points) == 1 for points in by_kind.values()), base.id.value
        for points in by_kind.values():
            for x, y in points[0].footprint.cells:
                assert 0 <= x < world.width and 0 <= y < world.height
                # A robot must be able to stand on the capture/exit cell and a
                # ground-level commander on the (placeholder) pad cell.
                assert not occupancy.is_occupied(x, y), f"{points[0].id.value} on solid geometry"
        # Evidence: the robot leaves construction at the anchor cell
        # (pad.y + 4 = anchor.y), i.e. exit == capture anchor.
        assert by_kind[InteractionKind.EXIT][0].footprint.cells == by_kind[InteractionKind.WARBASE_CAPTURE][0].footprint.cells


def test_each_factory_declares_exactly_one_capture_point_on_free_ground(world: WorldMap) -> None:
    occupancy = world.occupancy()
    for factory in world.factories:
        x, y = _anchor(world, factory)
        assert not occupancy.is_occupied(x, y)
        assert not world.interaction_points_for(factory.id, kind=InteractionKind.HELI_PAD)
        assert not world.interaction_points_for(factory.id, kind=InteractionKind.EXIT)


def test_every_interaction_point_references_a_real_structure(world: WorldMap) -> None:
    for point in world.interaction_points:
        assert world.structure_by_id(point.structure_id) is not None, point.id.value


# -- terrain (documented gap: only NORMAL is encoded) ---------------------------


def test_terrain_is_uniformly_normal_as_documented(world: WorldMap) -> None:
    # The evidence doc leaves rough/ditch placement undecoded; the map must
    # not silently gain terrain that the fidelity pass never verified.
    for x in range(world.width):
        for y in range(world.height):
            assert world.terrain.terrain_at(x, y) is TerrainType.NORMAL
    assert world.blockers == ()


# -- spawns, clearance and reachability ----------------------------------------


def _ground_reachable(world: WorldMap, start: tuple[int, int], goal: tuple[int, int]) -> bool:
    """BFS over cells a grounded commander may enter, using the engine's own collision rule."""
    state = create_initial_state(default_pvp_scenario(), world)
    probe = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=start[0], y=start[1], altitude=0)
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        if (x, y) == goal:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not (0 <= nx < world.width and 0 <= ny < world.height):
                continue
            if abs(nx - goal[0]) > 12 or abs(ny - goal[1]) > 16:
                continue  # local search window; the pad is a few cells from the spawn
            mover = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=x, y=y, altitude=0)
            if commander_horizontal_move_allowed(state, mover, nx, ny, world=world):
                seen.add((nx, ny))
                queue.append((nx, ny))
    del probe
    return False


@pytest.mark.parametrize("player", [PLAYER_ONE, PLAYER_TWO])
def test_commander_spawn_is_free_ground_next_to_its_own_war_base(world: WorldMap, player: PlayerId) -> None:
    spawn = world.spawn_positions[commander_spawn_key(player)]
    assert 0 <= spawn[0] < world.width and 0 <= spawn[1] < world.height
    assert not world.occupancy().is_occupied(*spawn)
    own_base = next(base for base in world.war_bases if base.owner == player)
    pad = world.interaction_points_for(own_base.id, kind=InteractionKind.HELI_PAD)[0]
    pad_cell = min(pad.footprint.cells)
    assert abs(pad_cell[0] - spawn[0]) + abs(pad_cell[1] - spawn[1]) <= 8
    assert spawn != pad_cell, "spawning on the pad would open construction at tick 0"
    assert _ground_reachable(world, spawn, pad_cell), "commander cannot reach its heli-pad on the ground"


def test_low_commander_is_blocked_by_a_war_base_but_clears_it_when_high_enough(world: WorldMap) -> None:
    state = create_initial_state(default_pvp_scenario(), world)
    base = next(b for b in world.war_bases if b.id.value == "warbase-1")
    tall = next(c for c in base.components if c.height == 15)
    low = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=tall.x - 1 if tall.x > 0 else tall.x + 1, y=tall.y, altitude=0)
    assert not commander_horizontal_move_allowed(state, low, tall.x, tall.y, world=world)
    high = Commander(
        player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=low.x, y=low.y, altitude=tall.height
    )
    assert commander_horizontal_move_allowed(state, high, tall.x, tall.y, world=world)
    assert DEFAULT_RULES.commander_max_altitude >= max(c.height for c in base.components)


# -- ownership presentation hooks --------------------------------------------


def test_ownership_hooks_use_map_structure_ids_only(world: WorldMap) -> None:
    state = create_initial_state(default_pvp_scenario(), world)
    known = {s.id for s in (*world.war_bases, *world.factories)}
    for record in state.structure_ownership:
        assert record.structure_id in known
    assert {r.structure_id.value: r.owner for r in state.structure_ownership} == {
        "warbase-1": PLAYER_ONE,
        "warbase-4": PLAYER_TWO,
    }
