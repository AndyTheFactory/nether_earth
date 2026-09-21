"""Tests for height-aware commander collision (issue #39, M3.3)."""

from __future__ import annotations

import pytest

from nether_earth.collision import (
    RobotFixture,
    VerticalRange,
    commander_blocks_cell,
    commander_horizontal_move_allowed,
    commander_vertical_move_allowed,
    commander_vertical_range,
    component_vertical_range,
    robot_vertical_range,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import create_game_state
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType


def _empty_world(width: int = 20, height: int = 20, **structures: object) -> WorldMap:
    return WorldMap(
        map_id="test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(width=width, height=height, cells={}, default=TerrainType.NORMAL),
        war_bases=structures.get("war_bases", ()),  # type: ignore[arg-type]
        factories=structures.get("factories", ()),  # type: ignore[arg-type]
        blockers=structures.get("blockers", ()),  # type: ignore[arg-type]
        interaction_points=(),
        spawn_positions={},
    )


def _blocker(entity_id: str, x: int, y: int, height: int) -> Blocker:
    return Blocker(id=EntityId(entity_id), components=(Component(x=x, y=y, height=height),))


def _commander(
    player_id: object = PLAYER_ONE,
    x: int = 5,
    y: int = 5,
    altitude: int = 0,
) -> Commander:
    return Commander(
        player_id=player_id,  # type: ignore[arg-type]
        mode=CommanderMode.FREE,
        x=x,
        y=y,
        altitude=altitude,
    )


# --- VerticalRange semantics -------------------------------------------------


def test_vertical_range_rejects_non_positive_extent() -> None:
    with pytest.raises(ValueError):
        VerticalRange(bottom=5, top=5)
    with pytest.raises(ValueError):
        VerticalRange(bottom=5, top=3)


def test_touching_ranges_do_not_overlap() -> None:
    below = VerticalRange(bottom=0, top=10)
    above = VerticalRange(bottom=10, top=20)

    assert not below.overlaps(above)
    assert not above.overlaps(below)


def test_overlapping_ranges_overlap_both_directions() -> None:
    a = VerticalRange(bottom=0, top=10)
    b = VerticalRange(bottom=9, top=20)

    assert a.overlaps(b)
    assert b.overlaps(a)


def test_fully_contained_range_overlaps() -> None:
    outer = VerticalRange(bottom=0, top=20)
    inner = VerticalRange(bottom=5, top=10)

    assert outer.overlaps(inner)
    assert inner.overlaps(outer)


def test_disjoint_ranges_do_not_overlap() -> None:
    a = VerticalRange(bottom=0, top=5)
    b = VerticalRange(bottom=100, top=105)

    assert not a.overlaps(b)
    assert not b.overlaps(a)


# --- range constructors -------------------------------------------------


def test_commander_vertical_range_uses_rules_height() -> None:
    rules = EngineRules(commander_height=6)
    result = commander_vertical_range(10, rules)

    assert result == VerticalRange(bottom=10, top=16)


def test_commander_vertical_range_default_rules() -> None:
    result = commander_vertical_range(0)

    assert result == VerticalRange(bottom=0, top=DEFAULT_RULES.commander_height)


def test_component_vertical_range_is_ground_rooted() -> None:
    component = Component(x=1, y=1, height=8)

    assert component_vertical_range(component) == VerticalRange(bottom=0, top=8)


def test_robot_vertical_range_is_ground_rooted() -> None:
    robot = RobotFixture(id=EntityId("r1"), owner=PLAYER_ONE, x=1, y=1, height=5)

    assert robot_vertical_range(robot) == VerticalRange(bottom=0, top=5)


def test_robot_fixture_rejects_non_positive_height() -> None:
    with pytest.raises(ValueError):
        RobotFixture(id=EntityId("r1"), owner=PLAYER_ONE, x=0, y=0, height=0)


# --- horizontal movement vs static components -------------------------------


def test_low_commander_blocked_by_tall_static_component() -> None:
    world = _empty_world(blockers=(_blocker("b1", x=6, y=5, height=20),))
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    low_commander = _commander(altitude=0)  # range [0, 4)

    allowed = commander_horizontal_move_allowed(
        state, low_commander, 6, 5, world=world
    )

    assert allowed is False


def test_high_commander_clears_short_static_component() -> None:
    world = _empty_world(blockers=(_blocker("b1", x=6, y=5, height=4),))
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    high_commander = _commander(altitude=10)  # range [10, 14)

    allowed = commander_horizontal_move_allowed(
        state, high_commander, 6, 5, world=world
    )

    assert allowed is True


def test_commander_exactly_resting_on_component_top_may_move_there() -> None:
    """Touching (not overlapping) the component top is allowed horizontally too."""
    world = _empty_world(blockers=(_blocker("b1", x=6, y=5, height=10),))
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    resting_commander = _commander(altitude=10)  # range [10, 14) touches [0, 10)

    allowed = commander_horizontal_move_allowed(
        state, resting_commander, 6, 5, world=world
    )

    assert allowed is True


def test_commander_move_to_empty_cell_is_allowed() -> None:
    world = _empty_world()
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(altitude=0)

    assert commander_horizontal_move_allowed(state, commander, 1, 1, world=world) is True


def test_multi_component_structure_uses_per_cell_height() -> None:
    """A structure's per-cell heights are independent -- issue's M2 requirement."""
    tall_component = Component(x=6, y=5, height=20)
    short_component = Component(x=7, y=5, height=2)
    blocker = Blocker(id=EntityId("mixed"), components=(tall_component, short_component))
    world = _empty_world(blockers=(blocker,))
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(altitude=5)  # range [5, 9)

    assert commander_horizontal_move_allowed(state, commander, 6, 5, world=world) is False
    assert commander_horizontal_move_allowed(state, commander, 7, 5, world=world) is True


# --- vertical movement vs static components ---------------------------------


def test_descent_stops_on_static_surface() -> None:
    world = _empty_world(blockers=(_blocker("b1", x=5, y=5, height=10),))
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(x=5, y=5, altitude=20)

    # Descending to rest exactly on top (touching, not overlapping) is allowed.
    assert commander_vertical_move_allowed(state, commander, 10, world=world) is True
    # Descending further, into the component, is blocked.
    assert commander_vertical_move_allowed(state, commander, 9, world=world) is False
    assert commander_vertical_move_allowed(state, commander, 0, world=world) is False


def test_ascent_away_from_surface_is_allowed() -> None:
    world = _empty_world(blockers=(_blocker("b1", x=5, y=5, height=10),))
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(x=5, y=5, altitude=10)

    assert commander_vertical_move_allowed(state, commander, 20, world=world) is True


# --- robots as physical top surfaces (friendly and enemy, §14) --------------


def test_enemy_robot_acts_as_physical_top_surface() -> None:
    world = _empty_world()
    enemy_robot = RobotFixture(id=EntityId("er1"), owner=PLAYER_TWO, x=5, y=5, height=6)
    state = create_game_state(tick=0, players=(PLAYER_ONE, PLAYER_TWO))
    commander = _commander(player_id=PLAYER_ONE, x=5, y=5, altitude=20)

    # Rest on top of the enemy robot: allowed (touching).
    assert (
        commander_vertical_move_allowed(state, commander, 6, world=world, robots=(enemy_robot,))
        is True
    )
    # Descend through it: blocked. No docking/control-transfer concept here --
    # this module only asserts the physical-surface collision outcome.
    assert (
        commander_vertical_move_allowed(state, commander, 3, world=world, robots=(enemy_robot,))
        is False
    )


def test_friendly_robot_also_acts_as_physical_top_surface() -> None:
    """§14 landing rule aside, collision treats friendly/enemy robots alike."""
    world = _empty_world()
    friendly_robot = RobotFixture(id=EntityId("fr1"), owner=PLAYER_ONE, x=5, y=5, height=6)
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(player_id=PLAYER_ONE, x=5, y=5, altitude=20)

    assert (
        commander_vertical_move_allowed(
            state, commander, 6, world=world, robots=(friendly_robot,)
        )
        is True
    )
    assert (
        commander_vertical_move_allowed(
            state, commander, 3, world=world, robots=(friendly_robot,)
        )
        is False
    )


def test_horizontal_move_onto_robot_column_blocked_when_overlapping() -> None:
    world = _empty_world()
    robot = RobotFixture(id=EntityId("r1"), owner=PLAYER_TWO, x=6, y=5, height=10)
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    low_commander = _commander(x=5, y=5, altitude=0)

    assert (
        commander_horizontal_move_allowed(state, low_commander, 6, 5, world=world, robots=(robot,))
        is False
    )


def test_horizontal_move_over_robot_column_allowed_when_clear() -> None:
    world = _empty_world()
    robot = RobotFixture(id=EntityId("r1"), owner=PLAYER_TWO, x=6, y=5, height=4)
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    high_commander = _commander(x=5, y=5, altitude=10)

    assert (
        commander_horizontal_move_allowed(
            state, high_commander, 6, 5, world=world, robots=(robot,)
        )
        is True
    )


# --- commander-vs-commander (§12) -------------------------------------------


def test_opposing_commanders_share_xy_when_ranges_disjoint() -> None:
    world = _empty_world()
    state = create_game_state(
        tick=0,
        players=(PLAYER_ONE, PLAYER_TWO),
        commanders=(
            _commander(PLAYER_ONE, x=5, y=5, altitude=0),
            _commander(PLAYER_TWO, x=6, y=5, altitude=20),
        ),
    )
    mover = state.commander_for(PLAYER_ONE)
    assert mover is not None

    # Mover's range [0, 4) doesn't overlap the resident's [20, 24).
    assert commander_horizontal_move_allowed(state, mover, 6, 5, world=world) is True


def test_opposing_commanders_block_when_ranges_overlap() -> None:
    world = _empty_world()
    state = create_game_state(
        tick=0,
        players=(PLAYER_ONE, PLAYER_TWO),
        commanders=(
            _commander(PLAYER_ONE, x=5, y=5, altitude=0),
            _commander(PLAYER_TWO, x=6, y=5, altitude=2),
        ),
    )
    mover = state.commander_for(PLAYER_ONE)
    assert mover is not None

    # Mover's range [0, 4) overlaps the resident's [2, 6).
    assert commander_horizontal_move_allowed(state, mover, 6, 5, world=world) is False


def test_opposing_commander_blocks_vertical_move_including_descent() -> None:
    world = _empty_world()
    state = create_game_state(
        tick=0,
        players=(PLAYER_ONE, PLAYER_TWO),
        commanders=(
            _commander(PLAYER_ONE, x=5, y=5, altitude=20),
            _commander(PLAYER_TWO, x=5, y=5, altitude=10),
        ),
    )
    mover = state.commander_for(PLAYER_ONE)
    assert mover is not None

    # Resident at [10, 14). Mover descending to altitude 12 -> [12, 16) overlaps.
    assert commander_vertical_move_allowed(state, mover, 12, world=world) is False
    # Descending to rest exactly on top of the resident -> [14, 18): touching only.
    assert commander_vertical_move_allowed(state, mover, 14, world=world) is True
    # Ascending away is unaffected.
    assert commander_vertical_move_allowed(state, mover, 30, world=world) is True


def test_commander_does_not_block_itself() -> None:
    world = _empty_world()
    state = create_game_state(
        tick=0,
        players=(PLAYER_ONE,),
        commanders=(_commander(PLAYER_ONE, x=5, y=5, altitude=0),),
    )
    mover = state.commander_for(PLAYER_ONE)
    assert mover is not None

    # Moving to its own current cell/altitude must not be self-blocked.
    assert commander_horizontal_move_allowed(state, mover, 5, 5, world=world) is True
    assert commander_vertical_move_allowed(state, mover, 0, world=world) is True


def test_commander_blocks_cell_direct_query() -> None:
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    resident = _commander(PLAYER_ONE, x=5, y=5, altitude=10)  # range [10, 14)

    overlapping = VerticalRange(bottom=12, top=16)
    touching = VerticalRange(bottom=14, top=18)
    disjoint = VerticalRange(bottom=100, top=104)

    assert commander_blocks_cell(state, resident, 5, 5, overlapping) is True
    assert commander_blocks_cell(state, resident, 5, 5, touching) is False
    assert commander_blocks_cell(state, resident, 5, 5, disjoint) is False
    # (x, y) is the anchor of another 2×2 body (CR002.3/CR002.4): any body
    # overlapping the commander's blocks; a body one cell further does not,
    # regardless of vertical range.
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)):
        assert commander_blocks_cell(state, resident, 5 + dx, 5 + dy, overlapping) is True
    for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2), (2, 1)):
        assert commander_blocks_cell(state, resident, 5 + dx, 5 + dy, overlapping) is False


# --- determinism / order-independence ---------------------------------------


def test_horizontal_collision_result_independent_of_robot_tuple_order() -> None:
    world = _empty_world()
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(x=5, y=5, altitude=0)
    robot_a = RobotFixture(id=EntityId("a"), owner=PLAYER_TWO, x=6, y=5, height=2)
    robot_b = RobotFixture(id=EntityId("b"), owner=PLAYER_TWO, x=6, y=5, height=10)

    forward = commander_horizontal_move_allowed(
        state, commander, 6, 5, world=world, robots=(robot_a, robot_b)
    )
    reversed_order = commander_horizontal_move_allowed(
        state, commander, 6, 5, world=world, robots=(robot_b, robot_a)
    )

    assert forward is reversed_order is False


def test_vertical_collision_result_independent_of_commander_tuple_order() -> None:
    world = _empty_world()
    mover = _commander(PLAYER_ONE, x=5, y=5, altitude=20)
    blocker_commander = _commander(PLAYER_TWO, x=5, y=5, altitude=10)

    state_ab = create_game_state(
        tick=0, players=(PLAYER_ONE, PLAYER_TWO), commanders=(mover, blocker_commander)
    )
    state_ba = create_game_state(
        tick=0, players=(PLAYER_ONE, PLAYER_TWO), commanders=(blocker_commander, mover)
    )

    result_ab = commander_vertical_move_allowed(
        state_ab, state_ab.commander_for(PLAYER_ONE), 12, world=world
    )
    result_ba = commander_vertical_move_allowed(
        state_ba, state_ba.commander_for(PLAYER_ONE), 12, world=world
    )

    assert result_ab is result_ba is False


def test_static_component_collision_independent_of_structure_declaration_order() -> None:
    world_a = _empty_world(
        blockers=(_blocker("b1", 6, 5, 20), _blocker("b2", 7, 5, 2))
    )
    world_b = _empty_world(
        blockers=(_blocker("b2", 7, 5, 2), _blocker("b1", 6, 5, 20))
    )
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    commander = _commander(altitude=0)

    result_a = commander_horizontal_move_allowed(state, commander, 6, 5, world=world_a)
    result_b = commander_horizontal_move_allowed(state, commander, 6, 5, world=world_b)

    assert result_a is result_b is False
