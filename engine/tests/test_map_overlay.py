"""Standalone tests for `nether_earth.map_overlay`.

Spec references: `_specs/milestones/02-map-world-model.md` ("Scenario
overlay" workstream, "Locked v1 PvP scenario overlay"); `_specs/open-
questions.md` §2 (PvP treatment of the remaining war bases — RESOLVED:
Player 1 owns the extreme-left war base, Player 2 owns the extreme-right
war base, the two war bases between them start neutral).

`engine/tests/test_world_map.py` already covers `apply_overlay` as part of
the broader `WorldMap` integration surface (ownership replacement, unknown
id rejection, blocker id rejection, non-mutation of the input). This file
focuses on `map_overlay.py` in isolation and adds the coverage issue #24
calls out specifically: `ScenarioOverlay` construction/validation, factory
ownership (the existing suite only exercises war bases), multi-scenario
reuse of one base map, determinism across independently-loaded-but-equal
maps, the spawn-position bounds check, and `default_pvp_overlay`'s
extreme-left/extreme-right/neutral-middle assignment.
"""

from pathlib import Path

import pytest

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import (
    OverlayValidationError,
    ScenarioOverlay,
    apply_overlay,
    default_pvp_overlay,
)
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.structures import Component, Factory, WarBase
from nether_earth.terrain import TerrainGrid

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "world_map_basic.yaml"
ORIGINAL_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"


def _war_base(entity_id: str, *xs: int) -> WarBase:
    """Build a minimal WarBase spanning the given x-coordinates at y=0."""
    return WarBase(
        id=EntityId(entity_id),
        components=tuple(Component(x=x, y=0, height=1) for x in xs),
    )


def _four_war_base_map() -> WorldMap:
    """An ownership-neutral WorldMap with four war bases at distinct x-extents.

    Used to test `default_pvp_overlay`'s extreme-left/extreme-right/neutral-
    middle assignment: the fixture YAML (`world_map_basic.yaml`) only has
    two war bases, so this builds a `WorldMap` directly instead of adding a
    second fixture file just for this one test's shape.
    """
    return WorldMap(
        map_id="four-war-base-fixture",
        version=1,
        width=20,
        height=5,
        terrain=TerrainGrid(width=20, height=5, cells={}),
        war_bases=(
            _war_base("warbase-left", 0, 1),
            _war_base("warbase-mid-a", 5),
            _war_base("warbase-mid-b", 10),
            _war_base("warbase-right", 18, 19),
        ),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


# --- ScenarioOverlay construction/validation -----------------------------------


def test_scenario_overlay_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ScenarioOverlay(id="", ownership={}, spawn_positions={})


def test_scenario_overlay_rejects_whitespace_only_id() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ScenarioOverlay(id="   ", ownership={}, spawn_positions={})


def test_scenario_overlay_accepts_empty_ownership_and_spawns() -> None:
    overlay = ScenarioOverlay(id="empty-overlay", ownership={}, spawn_positions={})
    assert overlay.id == "empty-overlay"
    assert overlay.ownership == {}
    assert overlay.spawn_positions == {}


# --- Ownership replacement: war bases and factories ----------------------------


def test_apply_overlay_replaces_war_base_ownership() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="war-base-overlay",
        ownership={EntityId("warbase-p1"): PlayerId("p2")},
        spawn_positions={},
    )

    overlaid = apply_overlay(world_map, overlay)

    warbase = overlaid.structure_by_id(EntityId("warbase-p1"))
    assert isinstance(warbase, WarBase)
    assert warbase.owner == PlayerId("p2")


def test_apply_overlay_replaces_factory_ownership() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    factory_before = world_map.structure_by_id(EntityId("factory-1"))
    assert isinstance(factory_before, Factory)
    assert factory_before.owner is None

    overlay = ScenarioOverlay(
        id="factory-overlay",
        ownership={EntityId("factory-1"): PlayerId("p1")},
        spawn_positions={},
    )
    overlaid = apply_overlay(world_map, overlay)

    factory_after = overlaid.structure_by_id(EntityId("factory-1"))
    assert isinstance(factory_after, Factory)
    assert factory_after.owner == PlayerId("p1")
    # Input is untouched.
    assert factory_before.owner is None


def test_apply_overlay_unaffected_structures_are_reused_not_copied() -> None:
    # Reusing the same immutable WarBase/Factory instance for structures the
    # overlay does not touch is expected/fine (they're frozen dataclasses);
    # this just documents that fact and confirms it does not let the
    # overlaid map's unaffected structures silently diverge from the input.
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="partial-overlay",
        ownership={EntityId("warbase-p1"): PlayerId("p2")},
        spawn_positions={},
    )
    overlaid = apply_overlay(world_map, overlay)

    original_p2 = world_map.structure_by_id(EntityId("warbase-p2"))
    overlaid_p2 = overlaid.structure_by_id(EntityId("warbase-p2"))
    assert original_p2 is overlaid_p2

    original_factory = world_map.structure_by_id(EntityId("factory-1"))
    overlaid_factory = overlaid.structure_by_id(EntityId("factory-1"))
    assert original_factory is overlaid_factory

    # And the input map's own fields never visibly change.
    unaffected_warbase = world_map.structure_by_id(EntityId("warbase-p1"))
    assert isinstance(unaffected_warbase, WarBase)
    assert unaffected_warbase.owner == PlayerId("p1")


# --- Unknown reference rejection -------------------------------------------------


def test_apply_overlay_rejects_unknown_structure_id() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="unknown-id-overlay",
        ownership={EntityId("no-such-structure"): PlayerId("p1")},
        spawn_positions={},
    )
    with pytest.raises(OverlayValidationError, match="unknown structure id"):
        apply_overlay(world_map, overlay)


def test_apply_overlay_rejects_blocker_id() -> None:
    # Blockers have no `owner` field; an overlay naming one is an
    # unknown-reference error like any other unrecognized id.
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="blocker-overlay",
        ownership={EntityId("box-1"): PlayerId("p1")},
        spawn_positions={},
    )
    with pytest.raises(OverlayValidationError, match="unknown structure id"):
        apply_overlay(world_map, overlay)


# --- Spawn-position merge/override semantics ------------------------------------


def test_apply_overlay_merges_new_spawn_position() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="new-spawn-overlay",
        ownership={},
        spawn_positions={"neutral_flag": (1, 2)},
    )
    overlaid = apply_overlay(world_map, overlay)

    assert overlaid.spawn_positions["neutral_flag"] == (1, 2)
    # Base map's own spawns are preserved alongside the new one.
    assert overlaid.spawn_positions["p1_commander"] == (8, 2)
    assert overlaid.spawn_positions["p2_commander"] == (8, 5)


def test_apply_overlay_overrides_existing_spawn_position() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="override-spawn-overlay",
        ownership={},
        spawn_positions={"p1_commander": (2, 3)},
    )
    overlaid = apply_overlay(world_map, overlay)

    assert overlaid.spawn_positions["p1_commander"] == (2, 3)
    assert overlaid.spawn_positions["p2_commander"] == (8, 5)


# --- Spawn-position bounds validation --------------------------------------------
#
# Judgment call (see PR description): both `map_overlay.apply_overlay` and
# `map._parse_spawn_positions` lacked bounds validation for spawn positions;
# the base-map loader had the same gap terrain.py already closed for terrain
# cells. Both were fixed for consistency using the same in-bounds pattern
# terrain.py uses (`0 <= x < width and 0 <= y < height`).


def test_apply_overlay_rejects_spawn_position_outside_grid_x() -> None:
    world_map = load_world_map(FIXTURE_PATH)  # width=12, height=8
    overlay = ScenarioOverlay(
        id="out-of-bounds-x",
        ownership={},
        spawn_positions={"bad": (12, 0)},
    )
    with pytest.raises(OverlayValidationError, match="outside the"):
        apply_overlay(world_map, overlay)


def test_apply_overlay_rejects_spawn_position_outside_grid_y() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="out-of-bounds-y",
        ownership={},
        spawn_positions={"bad": (0, 8)},
    )
    with pytest.raises(OverlayValidationError, match="outside the"):
        apply_overlay(world_map, overlay)


def test_apply_overlay_rejects_negative_spawn_position() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="negative-overlay",
        ownership={},
        spawn_positions={"bad": (-1, 0)},
    )
    with pytest.raises(OverlayValidationError, match="outside the"):
        apply_overlay(world_map, overlay)


def test_apply_overlay_accepts_spawn_position_on_grid_boundary() -> None:
    world_map = load_world_map(FIXTURE_PATH)  # width=12, height=8
    overlay = ScenarioOverlay(
        id="boundary-overlay",
        ownership={},
        spawn_positions={"edge": (5, 3)},  # max valid x/y (width-1, height-1)
    )
    overlaid = apply_overlay(world_map, overlay)
    assert overlaid.spawn_positions["edge"] == (5, 3)


# --- Non-mutation of the input ---------------------------------------------------


def test_apply_overlay_never_mutates_input_map() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    baseline = load_world_map(FIXTURE_PATH)  # independent equal copy for comparison

    overlay = ScenarioOverlay(
        id="mutation-check-overlay",
        ownership={
            EntityId("warbase-p1"): PlayerId("p2"),
            EntityId("factory-1"): PlayerId("p1"),
        },
        spawn_positions={"p1_commander": (0, 0), "new_spawn": (1, 1)},
    )

    apply_overlay(world_map, overlay)

    assert world_map == baseline


# --- Multiple scenarios reusing one base map ------------------------------------


def test_two_overlays_from_one_base_map_are_independent() -> None:
    base_map = load_world_map(FIXTURE_PATH)

    overlay_a = ScenarioOverlay(
        id="scenario-a",
        ownership={EntityId("warbase-p1"): PlayerId("p1"), EntityId("warbase-p2"): PlayerId("p1")},
        spawn_positions={"p1_commander": (0, 0)},
    )
    overlay_b = ScenarioOverlay(
        id="scenario-b",
        ownership={EntityId("warbase-p1"): PlayerId("p2"), EntityId("warbase-p2"): PlayerId("p2")},
        spawn_positions={"p2_commander": (1, 1)},
    )

    map_a = apply_overlay(base_map, overlay_a)
    map_b = apply_overlay(base_map, overlay_b)

    # Each overlaid map reflects its own scenario.
    warbase_p1_a = map_a.structure_by_id(EntityId("warbase-p1"))
    warbase_p2_a = map_a.structure_by_id(EntityId("warbase-p2"))
    assert isinstance(warbase_p1_a, WarBase) and isinstance(warbase_p2_a, WarBase)
    assert warbase_p1_a.owner == PlayerId("p1")
    assert warbase_p2_a.owner == PlayerId("p1")
    assert map_a.spawn_positions["p1_commander"] == (0, 0)
    assert map_a.spawn_positions["p2_commander"] == (8, 5)  # base value, untouched by overlay_a

    warbase_p1_b = map_b.structure_by_id(EntityId("warbase-p1"))
    warbase_p2_b = map_b.structure_by_id(EntityId("warbase-p2"))
    assert isinstance(warbase_p1_b, WarBase) and isinstance(warbase_p2_b, WarBase)
    assert warbase_p1_b.owner == PlayerId("p2")
    assert warbase_p2_b.owner == PlayerId("p2")
    assert map_b.spawn_positions["p2_commander"] == (1, 1)
    assert map_b.spawn_positions["p1_commander"] == (8, 2)  # base value, untouched by overlay_b

    # Neither overlaid map affected the other, nor the shared base map.
    assert map_a != map_b
    base_warbase_p1 = base_map.structure_by_id(EntityId("warbase-p1"))
    base_warbase_p2 = base_map.structure_by_id(EntityId("warbase-p2"))
    assert isinstance(base_warbase_p1, WarBase) and isinstance(base_warbase_p2, WarBase)
    assert base_warbase_p1.owner == PlayerId("p1")
    assert base_warbase_p2.owner == PlayerId("p2")
    assert base_map.spawn_positions == {"p1_commander": (8, 2), "p2_commander": (8, 5)}


# --- Determinism -----------------------------------------------------------------


def test_apply_overlay_is_deterministic_across_independent_equal_loads() -> None:
    # Two independently-loaded (but canonically equal) WorldMaps, with two
    # independently-constructed (but equal) ScenarioOverlays, must produce
    # equal results — no hidden ordering dependency (dict/set iteration,
    # etc.) in `apply_overlay`.
    map_one = load_world_map(FIXTURE_PATH)
    map_two = load_world_map(FIXTURE_PATH)
    assert map_one == map_two
    assert map_one is not map_two

    overlay_one = ScenarioOverlay(
        id="determinism-overlay",
        ownership={
            EntityId("warbase-p1"): PlayerId("p2"),
            EntityId("factory-1"): PlayerId("p1"),
        },
        spawn_positions={"p1_commander": (3, 3), "extra": (0, 0)},
    )
    overlay_two = ScenarioOverlay(
        id="determinism-overlay",
        ownership={
            EntityId("factory-1"): PlayerId("p1"),
            EntityId("warbase-p1"): PlayerId("p2"),
        },
        spawn_positions={"extra": (0, 0), "p1_commander": (3, 3)},
    )
    assert overlay_one == overlay_two

    result_one = apply_overlay(map_one, overlay_one)
    result_two = apply_overlay(map_two, overlay_two)

    assert result_one == result_two


# --- default_pvp_overlay: locked v1 standard PvP scenario -----------------------
#
# `_specs/open-questions.md` §2 is RESOLVED: Player 1 owns the extreme-left
# war base, Player 2 owns the extreme-right war base, the two war bases
# between them start neutral. These tests assert exactly that, applied
# through the overlay mechanism (not baked into map geometry).


def test_default_pvp_overlay_assigns_extreme_left_and_right_leaves_middle_neutral() -> None:
    base_map = _four_war_base_map()

    overlay = default_pvp_overlay(base_map)
    overlaid = apply_overlay(base_map, overlay)

    left = overlaid.structure_by_id(EntityId("warbase-left"))
    mid_a = overlaid.structure_by_id(EntityId("warbase-mid-a"))
    mid_b = overlaid.structure_by_id(EntityId("warbase-mid-b"))
    right = overlaid.structure_by_id(EntityId("warbase-right"))
    assert isinstance(left, WarBase)
    assert isinstance(mid_a, WarBase)
    assert isinstance(mid_b, WarBase)
    assert isinstance(right, WarBase)

    assert left.owner == PLAYER_ONE
    assert right.owner == PLAYER_TWO
    assert mid_a.owner is None
    assert mid_b.owner is None

    # Base map itself is untouched.
    untouched_left = base_map.structure_by_id(EntityId("warbase-left"))
    assert isinstance(untouched_left, WarBase)
    assert untouched_left.owner is None


def test_default_pvp_overlay_ownership_only_names_the_two_extremes() -> None:
    base_map = _four_war_base_map()
    overlay = default_pvp_overlay(base_map)

    assert dict(overlay.ownership) == {
        EntityId("warbase-left"): PLAYER_ONE,
        EntityId("warbase-right"): PLAYER_TWO,
    }
    # Commander spawns are declared as data for both players (M9 / open
    # question §17): Player 1 from disassembly evidence, Player 2 mirrored.
    assert set(overlay.spawn_positions) == {"p1_commander", "p2_commander"}
    for cell in overlay.spawn_positions.values():
        assert 0 <= cell[0] < base_map.width and 0 <= cell[1] < base_map.height


def test_default_pvp_overlay_works_with_exactly_two_war_bases() -> None:
    base_map = WorldMap(
        map_id="two-war-base-fixture",
        version=1,
        width=10,
        height=3,
        terrain=TerrainGrid(width=10, height=3, cells={}),
        war_bases=(_war_base("warbase-a", 0), _war_base("warbase-b", 9)),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )

    overlay = default_pvp_overlay(base_map)
    overlaid = apply_overlay(base_map, overlay)

    a = overlaid.structure_by_id(EntityId("warbase-a"))
    b = overlaid.structure_by_id(EntityId("warbase-b"))
    assert isinstance(a, WarBase)
    assert isinstance(b, WarBase)
    assert a.owner == PLAYER_ONE
    assert b.owner == PLAYER_TWO


def test_default_pvp_overlay_rejects_single_war_base_map() -> None:
    base_map = WorldMap(
        map_id="one-war-base-fixture",
        version=1,
        width=5,
        height=3,
        terrain=TerrainGrid(width=5, height=3, cells={}),
        war_bases=(_war_base("only-warbase", 2),),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )

    with pytest.raises(OverlayValidationError, match="at least two war bases"):
        default_pvp_overlay(base_map)


def test_default_pvp_overlay_rejects_map_with_no_war_bases() -> None:
    base_map = WorldMap(
        map_id="no-war-base-fixture",
        version=1,
        width=5,
        height=3,
        terrain=TerrainGrid(width=5, height=3, cells={}),
        war_bases=(),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )

    with pytest.raises(OverlayValidationError, match="at least two war bases"):
        default_pvp_overlay(base_map)


def test_default_pvp_overlay_pins_locked_commander_spawns_on_original_map() -> None:
    """Open question §17 (RESOLVED, CR001): locked commander start cells.

    Player 1 starts at (17, 10), altitude 0 (Spectrum ``La600_start``);
    Player 2 starts at the x-mirrored offset from the extreme-right war base,
    (499, 9), altitude 0 -- a locked PvP adaptation confirmed by the owner.
    """
    base_map = load_world_map(ORIGINAL_MAP_PATH)
    overlay = default_pvp_overlay(base_map)
    assert dict(overlay.spawn_positions) == {"p1_commander": (17, 10), "p2_commander": (499, 9)}

    world = apply_overlay(base_map, overlay)
    state = create_initial_state(default_pvp_scenario(), world, seed=0)
    commanders = {commander.player_id: commander for commander in state.commanders}
    assert (commanders[PLAYER_ONE].x, commanders[PLAYER_ONE].y, commanders[PLAYER_ONE].altitude) == (17, 10, 0)
    assert (commanders[PLAYER_TWO].x, commanders[PLAYER_TWO].y, commanders[PLAYER_TWO].altitude) == (499, 9, 0)
