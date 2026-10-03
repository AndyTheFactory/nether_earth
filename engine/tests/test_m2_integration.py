"""Milestone 2 integration scenario (issue #26).

This module is the final M2 ("Map & World Model") integration gate
described in `docs/mechanics/world-and-map.md` and issue #26's
acceptance criteria. It composes the
already-merged M2 primitives -- ``terrain``, ``structures``,
``interactions``, ``occupancy``, ``map`` (``WorldMap``/``load_world_map``),
and ``map_overlay`` -- end to end, proving the whole milestone works
together rather than re-testing any one module's isolated edge cases (those
already live in ``test_world_map.py``, ``test_terrain.py``,
``test_interactions.py``, ``test_occupancy.py``, ``test_map_overlay.py``,
and ``test_original_map.py``).

No new gameplay system is introduced here. Per the issue's explicit
non-goals, this module adds no movement, commander, capture, economy,
combat, or renderer behavior -- only composition/consistency assertions
across the already-merged M2 surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import load_world_map
from nether_earth.map_overlay import ScenarioOverlay, apply_overlay, default_pvp_overlay
from nether_earth.occupancy import OccupancyConflictError
from nether_earth.structures import Blocker, Factory, WarBase, occupied_cells
from nether_earth.terrain import TerrainType

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "world_map_basic.yaml"
ORIGINAL_MAP_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
)


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- 1/2: compact fixture composing all terrain types, a blocker, a
# factory, and two war bases; terrain and occupancy queries agree ----------


def test_fixture_covers_all_terrain_types_a_blocker_a_factory_and_two_war_bases() -> None:
    """Sanity-check the shared fixture actually has the shape this scenario needs.

    `docs/mechanics/world-and-map.md`'s
    integration scenario calls for "a compact deterministic fixture map containing all
    terrain types, generic blockers, a composed factory, and two composed
    war bases" -- ``world_map_basic.yaml`` already provides exactly this, so
    it is reused rather than adding a second near-duplicate fixture.
    """
    world_map = load_world_map(FIXTURE_PATH)

    terrain_types = {
        world_map.terrain.terrain_at(x, y)
        for x in range(world_map.width)
        for y in range(world_map.height)
    }
    assert terrain_types == {TerrainType.NORMAL, TerrainType.ROUGH, TerrainType.DITCH}

    assert len(world_map.blockers) >= 1
    assert len(world_map.factories) >= 1
    assert len(world_map.war_bases) == 2


def test_terrain_and_occupancy_queries_agree_on_which_cells_are_occupied() -> None:
    """Terrain and occupancy are orthogonal per-cell properties that must not contradict.

    A rough/ditch cell may or may not be occupied by a structure -- terrain
    type never implies solid occupancy, and vice versa. This walks every
    cell in the fixture grid and confirms both query systems can be asked
    about any cell independently and agree with the structures actually on
    the map.
    """
    world_map = load_world_map(FIXTURE_PATH)
    occupancy = world_map.occupancy()

    # The declared rough/ditch cells carry no structure.
    assert world_map.terrain.terrain_at(1, 1) is TerrainType.ROUGH
    assert not occupancy.is_occupied(1, 1)
    assert world_map.terrain.terrain_at(3, 3) is TerrainType.DITCH
    assert not occupancy.is_occupied(3, 3)

    # Every structure's occupied cells are consistent with a terrain query
    # (i.e. querying terrain for an occupied cell never raises -- it is
    # still a valid in-bounds grid cell, just also solid).
    all_structures: tuple[WarBase | Factory | Blocker, ...] = (
        *world_map.war_bases,
        *world_map.factories,
        *world_map.blockers,
    )
    for structure in all_structures:
        for x, y in occupied_cells(structure):
            assert occupancy.occupant_at(x, y) == structure.id
            # Terrain query succeeds and is one of the three known types.
            assert world_map.terrain.terrain_at(x, y) in TerrainType

    # Occupancy's own view of occupied cells matches the union of every
    # structure's occupied_cells exactly -- no extra, no missing.
    expected_cells = {
        cell for structure in all_structures for cell in occupied_cells(structure)
    }
    assert set(occupancy.cells()) == expected_cells


# --- 3: deterministic overlap rejection ------------------------------------


def test_two_structures_sharing_a_component_cell_conflict_deterministically(
    tmp_path: Path,
) -> None:
    """A deliberately conflicting variant of the fixture rejects the overlap.

    ``load_world_map`` itself does not eagerly compute occupancy (see
    ``map.py``'s ``WorldMap.occupancy()`` docstring -- it is computed on
    demand, not cached/validated at load time), so the document parses
    successfully and the conflict surfaces the first time occupancy is
    requested, exactly like ``test_world_map.py``'s
    ``test_overlapping_structure_footprints_raise``. This test additionally
    proves that behavior is deterministic across repeated calls, and that it
    holds for a multi-cell war base overlapping a single-cell blocker (not
    just two single-cell blockers).
    """
    path = _write_yaml(
        tmp_path,
        "m2_integration_overlap.yaml",
        """
        id: m2-integration-overlap
        version: 1
        width: 6
        height: 4
        blockers:
          - id: conflicting-box
            components:
              - {x: 4, y: 0, height: 1}
        war_bases:
          - id: conflicting-warbase
            components:
              - {x: 4, y: 0, height: 3}
              - {x: 5, y: 0, height: 2}
            owner: p1
        """,
    )

    # Parsing/loading itself must not raise -- the conflict is an occupancy
    # concern, not a structural-parse concern.
    world_map = load_world_map(path)

    for _ in range(3):
        with pytest.raises(OccupancyConflictError):
            world_map.occupancy()


# --- 4: canonical interaction metadata queryable across structure kinds ----


def test_canonical_interaction_metadata_is_queryable_for_war_bases_and_factory() -> None:
    world_map = load_world_map(FIXTURE_PATH)

    for war_base in world_map.war_bases:
        heli_pads = world_map.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)
        exits = world_map.interaction_points_for(war_base.id, kind=InteractionKind.EXIT)
        assert len(heli_pads) == 1
        assert len(exits) == 1
        # Each interaction point's footprint lands on an in-bounds cell of
        # the shared world grid -- interaction geometry and map dimensions
        # are part of one consistent contract.
        for point in (*heli_pads, *exits):
            for x, y in point.footprint.cells:
                assert 0 <= x < world_map.width
                assert 0 <= y < world_map.height

    for factory in world_map.factories:
        captures = world_map.interaction_points_for(
            factory.id, kind=InteractionKind.FACTORY_CAPTURE
        )
        assert len(captures) == 1


# --- 5: multiple scenario overlays over one base map -----------------------


def test_two_overlays_over_the_same_base_map_produce_independent_worlds_and_are_reproducible() -> (
    None
):
    """Two different overlays over one base map: independent, base untouched, reproducible.

    Combines ``test_map_overlay.py``'s "two overlays are independent" and
    "determinism across independent loads" checks with a fresh
    full-pipeline pass: load -> apply, twice, across two different overlays,
    then re-applies one of them to an independently-loaded copy of the base
    map and checks canonical equality end to end (not just ``apply_overlay``
    called on the same in-memory object).
    """
    base_map = load_world_map(FIXTURE_PATH)
    baseline_for_mutation_check = load_world_map(FIXTURE_PATH)

    overlay_a = ScenarioOverlay(
        id="integration-scenario-a",
        ownership={
            EntityId("warbase-p1"): PLAYER_ONE,
            EntityId("warbase-p2"): PLAYER_TWO,
        },
        spawn_positions={"p1_commander": (0, 0)},
    )
    overlay_b = ScenarioOverlay(
        id="integration-scenario-b",
        ownership={
            EntityId("warbase-p1"): PLAYER_TWO,
            EntityId("warbase-p2"): PLAYER_ONE,
        },
        spawn_positions={"p2_commander": (1, 1)},
    )

    world_a = apply_overlay(base_map, overlay_a)
    world_b = apply_overlay(base_map, overlay_b)

    warbase_p1_a = world_a.structure_by_id(EntityId("warbase-p1"))
    warbase_p1_b = world_b.structure_by_id(EntityId("warbase-p1"))
    assert isinstance(warbase_p1_a, WarBase)
    assert isinstance(warbase_p1_b, WarBase)
    assert warbase_p1_a.owner == PLAYER_ONE
    assert warbase_p1_b.owner == PLAYER_TWO
    assert world_a != world_b

    # The base map itself was never mutated by either overlay application.
    assert base_map == baseline_for_mutation_check

    # Determinism across the *full* load+overlay pipeline: a freshly loaded
    # copy of the base map, with the same overlay re-applied, is canonically
    # equal to the first result -- not merely `is`-equal or built from the
    # same in-memory WorldMap object.
    fresh_base_map = load_world_map(FIXTURE_PATH)
    world_a_repeated = apply_overlay(fresh_base_map, overlay_a)
    assert world_a_repeated == world_a


# --- 6: original ZX Spectrum map, loaded and overlaid reproducibly --------


def test_original_map_validates_and_produces_canonically_equal_worlds_when_reloaded() -> None:
    first = load_world_map(ORIGINAL_MAP_PATH)
    second = load_world_map(ORIGINAL_MAP_PATH)
    assert first == second
    assert first is not second


def test_original_map_default_pvp_overlay_assigns_two_of_four_war_bases_and_leaves_two_neutral() -> (
    None
):
    world_map = load_world_map(ORIGINAL_MAP_PATH)
    assert len(world_map.war_bases) == 4

    overlay = default_pvp_overlay(world_map)
    overlaid = apply_overlay(world_map, overlay)

    owners: list[PlayerId | None] = [war_base.owner for war_base in overlaid.war_bases]
    assert owners.count(PLAYER_ONE) == 1
    assert owners.count(PLAYER_TWO) == 1
    assert owners.count(None) == 2

    # Base map geometry/ownership is untouched by the overlay.
    for war_base in world_map.war_bases:
        assert war_base.owner is None


def test_original_map_repeat_load_and_overlay_produces_canonically_equal_results() -> None:
    """Full "repeat loads/runs and compare canonical snapshots/state" acceptance line.

    Loads the original map twice independently, applies the *same* default
    PvP overlay to each, and asserts the two overlaid results are
    canonically equal -- the determinism guarantee spans the entire
    load-YAML -> parse-structures -> compute-overlay -> apply-overlay
    pipeline, not just one stage of it in isolation.
    """
    map_one = load_world_map(ORIGINAL_MAP_PATH)
    map_two = load_world_map(ORIGINAL_MAP_PATH)
    assert map_one == map_two

    overlay_one = default_pvp_overlay(map_one)
    overlay_two = default_pvp_overlay(map_two)
    assert overlay_one == overlay_two

    overlaid_one = apply_overlay(map_one, overlay_one)
    overlaid_two = apply_overlay(map_two, overlay_two)
    assert overlaid_one == overlaid_two
