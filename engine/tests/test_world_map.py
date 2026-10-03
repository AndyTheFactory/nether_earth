"""Tests for the M2 versioned world-map loader and its constituent modules.

Spec references: `_specs/technical-spec.md` §§7-8, 10;
`_specs/functional-spec.md` §§7, 9; `docs/mechanics/world-and-map.md`;
`_specs/resolved-questions.md` (compositional structure model).
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
    Blocker,
    Component,
    Factory,
    FactoryType,
    Footprint,
    StructureValidationError,
    WarBase,
    occupied_cells,
    parse_blockers,
    parse_factories,
    parse_war_bases,
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
    assert world_map.width == 12
    assert world_map.height == 8


def test_fixture_terrain_query() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    assert world_map.terrain.terrain_at(1, 1) is TerrainType.ROUGH
    assert world_map.terrain.terrain_at(3, 3) is TerrainType.DITCH
    assert world_map.terrain.terrain_at(0, 0) is TerrainType.NORMAL


def test_loader_accepts_mountain_terrain_cells(tmp_path: Path) -> None:
    """CR001.4 (#151): ``mountain`` is a valid YAML terrain class (§4)."""
    path = _write_yaml(
        tmp_path,
        "mountain.yaml",
        "id: mountain-map\n"
        "version: 1\n"
        "width: 3\n"
        "height: 2\n"
        "terrain:\n"
        "  default: normal\n"
        "  cells:\n"
        "    - {x: 1, y: 0, type: mountain}\n"
        "    - {x: 2, y: 1, type: rough}\n",
    )
    world_map = load_world_map(path)
    assert world_map.terrain.terrain_at(1, 0) is TerrainType.MOUNTAIN
    assert world_map.terrain.terrain_at(2, 1) is TerrainType.ROUGH
    assert world_map.terrain.terrain_at(0, 0) is TerrainType.NORMAL


def test_fixture_structure_query() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    box = world_map.structure_by_id(EntityId("box-1"))
    assert isinstance(box, Blocker)
    assert occupied_cells(box) == frozenset({(0, 0)})

    warbase = world_map.structure_by_id(EntityId("warbase-p1"))
    assert isinstance(warbase, WarBase)
    assert occupied_cells(warbase) == frozenset({(4, 0), (5, 0)})
    assert warbase.owner == PlayerId("p1")

    assert world_map.structure_by_id(EntityId("does-not-exist")) is None


def test_fixture_war_base_components_can_have_different_heights() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    warbase = world_map.structure_by_id(EntityId("warbase-p1"))
    assert isinstance(warbase, WarBase)
    heights = {(component.x, component.y): component.height for component in warbase.components}
    assert heights == {(4, 0): 3, (5, 0): 2}


def test_fixture_factory_has_production_type() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    factory = world_map.structure_by_id(EntityId("factory-1"))
    assert isinstance(factory, Factory)
    assert factory.factory_type is FactoryType.CHASSIS


def test_fixture_interaction_points_query() -> None:
    world_map = load_world_map(FIXTURE_PATH)

    warbase_id = EntityId("warbase-p1")
    all_points = world_map.interaction_points_for(warbase_id)
    assert {point.kind for point in all_points} == {InteractionKind.HELI_PAD, InteractionKind.EXIT}

    heli_pads = world_map.interaction_points_for(warbase_id, kind=InteractionKind.HELI_PAD)
    assert len(heli_pads) == 1
    assert heli_pads[0].id == "warbase-p1-helipad"
    # A 2×2 pad (CR002.4): the four cells a landed commander's body covers.
    assert heli_pads[0].footprint.cells == frozenset({(4, 1), (5, 1), (4, 0), (5, 0)})

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
        "p1_commander": (8, 2),
        "p2_commander": (8, 5),
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
        blockers:
          - {id: dupe, components: [{x: 0, y: 0, height: 1}]}
          - {id: dupe, components: [{x: 1, y: 1, height: 1}]}
        """,
    )
    with pytest.raises(StructureValidationError):
        load_world_map(path)


def test_duplicate_id_across_kinds_raises(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "dup_cross_kind.yaml",
        """
        id: dup-cross-kind
        version: 1
        width: 3
        height: 3
        war_bases:
          - {id: shared, components: [{x: 0, y: 0, height: 1}]}
        factories:
          - {id: shared, components: [{x: 1, y: 1, height: 1}], factory_type: chassis}
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
        blockers:
          - {id: a, components: [{x: 1, y: 1, height: 1}]}
          - {id: b, components: [{x: 1, y: 1, height: 1}]}
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
        blockers:
          - {id: only-structure, components: [{x: 0, y: 0, height: 1}]}
        interaction_points:
          - id: p1
            kind: exit
            structure_id: does-not-exist
            footprint: {x: 0, y: 0}
        """,
    )
    with pytest.raises(InteractionValidationError):
        load_world_map(path)


def test_interaction_point_cannot_reference_blocker(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "blocker_interaction.yaml",
        """
        id: blocker-interaction
        version: 1
        width: 3
        height: 3
        blockers:
          - {id: box-1, components: [{x: 0, y: 0, height: 1}]}
        interaction_points:
          - id: p1
            kind: exit
            structure_id: box-1
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


def test_component_rejects_non_positive_height() -> None:
    with pytest.raises(ValueError):
        Component(x=0, y=0, height=0)
    with pytest.raises(ValueError):
        Component(x=0, y=0, height=-1)


def test_war_base_rejects_empty_components() -> None:
    with pytest.raises(ValueError):
        WarBase(id=EntityId("w1"), components=())


def test_war_base_rejects_duplicate_component_cells() -> None:
    with pytest.raises(ValueError):
        WarBase(
            id=EntityId("w1"),
            components=(
                Component(x=0, y=0, height=1),
                Component(x=0, y=0, height=2),
            ),
        )


def test_factory_rejects_empty_components() -> None:
    with pytest.raises(ValueError):
        Factory(id=EntityId("f1"), components=(), factory_type=FactoryType.CANNON)


def test_blocker_rejects_duplicate_component_cells() -> None:
    with pytest.raises(ValueError):
        Blocker(
            id=EntityId("b1"),
            components=(
                Component(x=1, y=1, height=1),
                Component(x=1, y=1, height=1),
            ),
        )


def test_war_base_components_may_have_different_heights() -> None:
    war_base = WarBase(
        id=EntityId("w1"),
        components=(
            Component(x=0, y=0, height=3),
            Component(x=1, y=0, height=1),
        ),
    )
    heights = {(component.x, component.y): component.height for component in war_base.components}
    assert heights == {(0, 0): 3, (1, 0): 1}


def test_occupied_cells_returns_component_coordinates() -> None:
    blocker = Blocker(id=EntityId("b1"), components=(Component(x=2, y=3, height=1),))
    assert occupied_cells(blocker) == frozenset({(2, 3)})


def test_occupancy_from_structures_raises_on_overlap() -> None:
    a = Blocker(id=EntityId("a"), components=(Component(x=1, y=1, height=1),))
    b = Blocker(id=EntityId("b"), components=(Component(x=1, y=1, height=1),))
    with pytest.raises(OccupancyConflictError):
        OccupancyGrid.from_structures((), (), (a, b))


def test_occupancy_from_structures_combines_all_three_kinds() -> None:
    war_base = WarBase(id=EntityId("w1"), components=(Component(x=0, y=0, height=1),))
    factory = Factory(
        id=EntityId("f1"),
        components=(Component(x=1, y=0, height=1),),
        factory_type=FactoryType.CANNON,
    )
    blocker = Blocker(id=EntityId("b1"), components=(Component(x=2, y=0, height=1),))

    grid = OccupancyGrid.from_structures((war_base,), (factory,), (blocker,))
    assert grid.occupant_at(0, 0) == EntityId("w1")
    assert grid.occupant_at(1, 0) == EntityId("f1")
    assert grid.occupant_at(2, 0) == EntityId("b1")


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


def test_parse_war_bases_rejects_malformed_components() -> None:
    with pytest.raises(StructureValidationError):
        parse_war_bases([{"id": "w1", "components": []}])
    with pytest.raises(StructureValidationError):
        parse_war_bases([{"id": "w1", "components": [{"x": 0, "y": 0}]}])  # missing height


def test_parse_factories_requires_factory_type() -> None:
    with pytest.raises(StructureValidationError):
        parse_factories([{"id": "f1", "components": [{"x": 0, "y": 0, "height": 1}]}])


def test_parse_factories_rejects_unknown_factory_type() -> None:
    with pytest.raises(StructureValidationError):
        parse_factories(
            [
                {
                    "id": "f1",
                    "components": [{"x": 0, "y": 0, "height": 1}],
                    "factory_type": "spaceship",
                }
            ]
        )


def test_parse_blockers_rejects_duplicate_component_cells() -> None:
    with pytest.raises(StructureValidationError):
        parse_blockers(
            [
                {
                    "id": "b1",
                    "components": [
                        {"x": 0, "y": 0, "height": 1},
                        {"x": 0, "y": 0, "height": 2},
                    ],
                }
            ]
        )


def test_parse_terrain_grid_rejects_duplicate_cells() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid(
            {"cells": [{"x": 0, "y": 0, "type": "rough"}, {"x": 0, "y": 0, "type": "ditch"}]},
            width=2,
            height=2,
        )


def test_parse_interaction_points_rejects_unknown_kind() -> None:
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
            {"s1"},
            set(),
            width=10,
            height=10,
        )


def test_apply_overlay_replaces_ownership_and_merges_spawns_without_mutating_input() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    original_war_bases = world_map.war_bases
    original_spawns = dict(world_map.spawn_positions)

    overlay = ScenarioOverlay(
        id="test-overlay",
        ownership={EntityId("warbase-p2"): PlayerId("p1")},
        spawn_positions={"p1_commander": (0, 0)},
    )

    overlaid = apply_overlay(world_map, overlay)

    # Input map is untouched.
    assert world_map.war_bases is original_war_bases
    assert world_map.spawn_positions == original_spawns
    original_warbase_p2 = world_map.structure_by_id(EntityId("warbase-p2"))
    assert isinstance(original_warbase_p2, WarBase)
    assert original_warbase_p2.owner == PlayerId("p2")

    # New map reflects the overlay.
    new_warbase_p2 = overlaid.structure_by_id(EntityId("warbase-p2"))
    assert isinstance(new_warbase_p2, WarBase)
    assert new_warbase_p2.owner == PlayerId("p1")
    assert overlaid.spawn_positions["p1_commander"] == (0, 0)
    assert overlaid.spawn_positions["p2_commander"] == (8, 5)  # untouched entry is preserved


def test_apply_overlay_rejects_unknown_structure_id() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="bad-overlay",
        ownership={EntityId("does-not-exist"): PlayerId("p1")},
        spawn_positions={},
    )
    with pytest.raises(OverlayValidationError):
        apply_overlay(world_map, overlay)


def test_apply_overlay_rejects_blocker_id() -> None:
    world_map = load_world_map(FIXTURE_PATH)
    overlay = ScenarioOverlay(
        id="blocker-overlay",
        ownership={EntityId("box-1"): PlayerId("p1")},
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
