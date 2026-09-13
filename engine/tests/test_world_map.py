"""Tests for the M2 versioned world-map loader and its constituent modules.

Spec references: `_specs/technical-spec.md` §§7-8, 10;
`_specs/functional-spec.md` §§7, 9; `_specs/milestones/02-map-world-model.md`.
"""

from pathlib import Path

import pytest

from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import (
    InteractionKind,
    InteractionValidationError,
    parse_interaction_points,
)
from nether_earth.map import MapValidationError, WorldMap, load_world_map
from nether_earth.map_overlay import OverlayValidationError, ScenarioOverlay, apply_overlay
from nether_earth.occupancy import OccupancyConflictError, OccupancyGrid
from nether_earth.structures import (
    FactoryType,
    Footprint,
    Structure,
    StructureKind,
    StructureValidationError,
    parse_structures,
)
from nether_earth.terrain import TerrainType, TerrainValidationError, parse_terrain_grid

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "world_map_basic.yaml"


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- WorldMap / load_world_map integration -------------------------------------


def test_loading_same_fixture_twice_is_canonically_equal() -> None:
    first = load_world_map(FIXTURE_PATH)
    second = load_world_map(FIXTURE_PATH)
    assert first == second


def test_fixture_loads_expected_identity_and_dimensions() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    assert world_map.map_id == "fixture-basic"
    assert world_map.version == 1
    assert world_map.width == 6
    assert world_map.height == 4


def test_fixture_terrain_query() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    assert world_map.terrain.terrain_at(1, 1) is TerrainType.ROUGH
    assert world_map.terrain.terrain_at(3, 3) is TerrainType.DITCH
    assert world_map.terrain.terrain_at(0, 0) is TerrainType.NORMAL


def test_fixture_structure_query() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    box = world_map.structure_by_id(EntityId("box-1"))
    assert box is not None
    assert box.kind is StructureKind.BLOCKER
    assert box.footprint.cells == frozenset({(0, 0)})

    warbase = world_map.structure_by_id(EntityId("warbase-p1"))
    assert warbase is not None
    assert warbase.footprint.cells == frozenset({(4, 0), (5, 0)})
    assert warbase.owner == PlayerId("p1")

    assert world_map.structure_by_id(EntityId("does-not-exist")) is None


def test_fixture_interaction_points_query() -> None:
    world_map = load_world_map(FIXTURE_PATH)

    warbase_id = EntityId("warbase-p1")
    all_points = world_map.interaction_points_for(warbase_id)
    assert {point.kind for point in all_points} == {InteractionKind.HELI_PAD, InteractionKind.EXIT}

    heli_pads = world_map.interaction_points_for(warbase_id, kind=InteractionKind.HELI_PAD)
    assert len(heli_pads) == 1
    assert heli_pads[0].id == "warbase-p1-helipad"
    assert heli_pads[0].footprint.cells == frozenset({(4, 0)})

    # warbase-p2 declares no warbase_capture point: open-questions.md §6 is
    # unresolved, so the schema must not require one.
    warbase_p2_captures = world_map.interaction_points_for(
        EntityId("warbase-p2"), kind=InteractionKind.WARBASE_CAPTURE
    )
    assert warbase_p2_captures == ()


def test_fixture_occupancy_reflects_structures() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    occupancy = world_map.occupancy()

    assert occupancy.is_occupied(0, 0)
    assert occupancy.occupant_at(0, 0) == EntityId("box-1")
    assert occupancy.is_occupied(4, 0)
    assert occupancy.is_occupied(5, 0)
    assert occupancy.occupant_at(4, 0) == occupancy.occupant_at(5, 0) == EntityId("warbase-p1")
    assert not occupancy.is_occupied(1, 1)  # rough terrain cell, no structure there


def test_fixture_spawn_positions() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    assert world_map.spawn_positions == {
        "p1_commander": (4, 1),
        "p2_commander": (4, 2),
    }


# --- Malformed YAML: each raises deterministically -----------------------------


def test_bad_terrain_type_raises(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "bad_terrain.yaml",
        """
        id: bad-terrain
        version: 1
        width: 2
        height: 2
        terrain:
          cells:
            - {x: 0, y: 0, type: lava}
        """,
    )
    with pytest.raises(TerrainValidationError):
        load_world_map(path)


def test_out_of_range_terrain_cell_raises(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "oob_terrain.yaml",
        """
        id: oob-terrain
        version: 1
        width: 2
        height: 2
        terrain:
          cells:
            - {x: 5, y: 5, type: rough}
        """,
    )
    with pytest.raises(TerrainValidationError):
        load_world_map(path)


def test_duplicate_structure_id_raises(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "dup_structure.yaml",
        """
        id: dup-structure
        version: 1
        width: 3
        height: 3
        structures:
          - {id: dupe, kind: blocker, footprint: {x: 0, y: 0}, height: 1}
          - {id: dupe, kind: blocker, footprint: {x: 1, y: 1}, height: 1}
        """,
    )
    with pytest.raises(StructureValidationError):
        load_world_map(path)


def test_overlapping_structure_footprints_raise(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "overlap.yaml",
        """
        id: overlap
        version: 1
        width: 3
        height: 3
        structures:
          - {id: a, kind: blocker, footprint: {x: 1, y: 1}, height: 1}
          - {id: b, kind: blocker, footprint: {x: 1, y: 1}, height: 1}
        """,
    )
    world_map = load_world_map(path)
    with pytest.raises(OccupancyConflictError):
        world_map.occupancy()


def test_dangling_interaction_point_structure_reference_raises(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "dangling.yaml",
        """
        id: dangling
        version: 1
        width: 3
        height: 3
        structures:
          - {id: only-structure, kind: blocker, footprint: {x: 0, y: 0}, height: 1}
        interaction_points:
          - id: p1
            kind: exit
            structure_id: does-not-exist
            footprint: {x: 0, y: 0}
        """,
    )
    with pytest.raises(InteractionValidationError):
        load_world_map(path)


def test_map_level_fields_still_validated(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "bad_version.yaml",
        """
        id: bad-version
        version: 0
        width: 3
        height: 3
        """,
    )
    with pytest.raises(MapValidationError):
        load_world_map(path)


# --- Focused unit tests ---------------------------------------------------------


def test_footprint_rejects_empty_cell_set() -> None:
    with pytest.raises(ValueError):
        Footprint(cells=frozenset())


def test_structure_requires_factory_type_iff_factory_kind() -> None:
    with pytest.raises(ValueError):
        Structure(
            id=EntityId("f1"),
            kind=StructureKind.FACTORY,
            footprint=Footprint(cells=frozenset({(0, 0)})),
            height=1,
            factory_type=None,
        )
    with pytest.raises(ValueError):
        Structure(
            id=EntityId("b1"),
            kind=StructureKind.BLOCKER,
            footprint=Footprint(cells=frozenset({(0, 0)})),
            height=1,
            factory_type=FactoryType.CANNON,
        )


def test_occupancy_from_structures_raises_on_overlap() -> None:
    a = Structure(
        id=EntityId("a"),
        kind=StructureKind.BLOCKER,
        footprint=Footprint(cells=frozenset({(1, 1)})),
        height=1,
    )
    b = Structure(
        id=EntityId("b"),
        kind=StructureKind.BLOCKER,
        footprint=Footprint(cells=frozenset({(1, 1)})),
        height=1,
    )
    with pytest.raises(OccupancyConflictError):
        OccupancyGrid.from_structures((a, b))


def test_occupancy_with_added_and_with_removed_round_trip() -> None:
    grid = OccupancyGrid()
    entity_id = EntityId("robot-1")
    footprint = Footprint(cells=frozenset({(2, 2)}))

    added = grid.with_added(entity_id, footprint)
    assert added.is_occupied(2, 2)
    assert added.occupant_at(2, 2) == entity_id

    removed = added.with_removed(entity_id)
    assert not removed.is_occupied(2, 2)


def test_occupancy_with_removed_missing_entity_raises() -> None:
    grid = OccupancyGrid()
    with pytest.raises(KeyError):
        grid.with_removed(EntityId("nobody"))


def test_parse_structures_rejects_unknown_kind() -> None:
    with pytest.raises(StructureValidationError):
        parse_structures([{"id": "x", "kind": "spaceship", "footprint": {"x": 0, "y": 0}, "height": 1}])


def test_parse_terrain_grid_rejects_duplicate_cells() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid(
            {"cells": [{"x": 0, "y": 0, "type": "rough"}, {"x": 0, "y": 0, "type": "ditch"}]},
            width=2,
            height=2,
        )


def test_parse_interaction_points_rejects_unknown_kind() -> None:
    structures = parse_structures(
        [{"id": "s1", "kind": "blocker", "footprint": {"x": 0, "y": 0}, "height": 1}]
    )
    with pytest.raises(InteractionValidationError):
        parse_interaction_points(
            [
                {
                    "id": "p1",
                    "kind": "not_a_real_kind",
                    "structure_id": "s1",
                    "footprint": {"x": 0, "y": 0},
                }
            ],
            structures,
        )


def test_apply_overlay_replaces_ownership_and_merges_spawns_without_mutating_input() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    original_structures = world_map.structures
    original_spawns = dict(world_map.spawn_positions)

    overlay = ScenarioOverlay(
        id="test-overlay",
        ownership={EntityId("warbase-p2"): PlayerId("p1")},
        spawn_positions={"p1_commander": (0, 0)},
    )

    overlaid = apply_overlay(world_map, overlay)

    # Input map is untouched.
    assert world_map.structures is original_structures
    assert world_map.spawn_positions == original_spawns
    assert world_map.structure_by_id(EntityId("warbase-p2")).owner == PlayerId("p2")  # type: ignore[union-attr]

    # New map reflects the overlay.
    assert overlaid.structure_by_id(EntityId("warbase-p2")).owner == PlayerId("p1")  # type: ignore[union-attr]
    assert overlaid.spawn_positions["p1_commander"] == (0, 0)
    assert overlaid.spawn_positions["p2_commander"] == (4, 2)  # untouched entry is preserved


def test_apply_overlay_rejects_unknown_structure_id() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="bad-overlay",
        ownership={EntityId("does-not-exist"): PlayerId("p1")},
        spawn_positions={},
    )
    with pytest.raises(OverlayValidationError):
        apply_overlay(world_map, overlay)


def test_world_map_equality_is_not_identity() -> None:
    # Sanity check for the milestone's canonical-equivalence acceptance
    # criterion: two independently constructed WorldMaps with equal field
    # values compare equal even though they are different objects.
    a = load_world_map(FIXTURE_PATH)
    b = load_world_map(FIXTURE_PATH)
    assert a is not b
    assert a == b
    assert isinstance(a, WorldMap)
