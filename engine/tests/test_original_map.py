"""Tests for the evidence-backed original-map ingestion (issue #25, M2.7).

Spec references: `docs/mechanics/world-and-map.md` (load the current
original-map YAML and prove it validates and initializes reproducibly);
`_specs/open-questions.md` §2 (RESOLVED — v1 PvP overlay needs exactly four
war bases: two extreme, two neutral interior).

See `data/maps/zx-spectrum-original.md` for the full evidence/provenance
record backing every value in `data/maps/zx-spectrum-original.yaml`,
including which fields are disassembly-verified vs. explicitly-flagged
unverified placeholders pending human review.
"""

from collections import Counter, deque
from pathlib import Path

import pytest

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.movement import chassis_can_enter
from nether_earth.robot_build import CHASSIS_MODULES, ModuleIdentity
from nether_earth.scenario import default_pvp_scenario
from nether_earth.structures import WarBase
from nether_earth.terrain import TerrainType

MAP_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
)


def test_original_map_loads_without_raising() -> None:
    load_world_map(MAP_PATH)


def test_original_map_identity_matches_default_scenario() -> None:
    # `scenario.default_pvp_scenario()` already references this map by
    # id/version; this pins that the map file actually matches what the
    # scenario expects.
    world_map = load_world_map(MAP_PATH)
    scenario = default_pvp_scenario()

    assert world_map.map_id == "zx-spectrum-original"
    assert world_map.version == 1
    assert world_map.map_id == scenario.map_id
    assert world_map.version == scenario.map_version


def test_original_map_has_exactly_four_war_bases() -> None:
    # `_specs/open-questions.md` §2 (RESOLVED): the v1 PvP overlay needs
    # exactly four war bases (extreme-left/right for the two players, two
    # neutral interior). This is also disassembly-verified: `N_WARBASES: equ
    # 4` in `netherearth-annotated.asm` — see the evidence doc.
    world_map = load_world_map(MAP_PATH)
    assert len(world_map.war_bases) == 4


def test_original_map_loading_twice_is_canonically_equal() -> None:
    # Determinism guarantee shared by all M2 map fixtures (see map.py's
    # module docstring): loading the same map twice must produce equal
    # WorldMap values.
    first = load_world_map(MAP_PATH)
    second = load_world_map(MAP_PATH)
    assert first == second
    assert first is not second


def test_default_pvp_overlay_applies_and_assigns_two_war_bases() -> None:
    world_map = load_world_map(MAP_PATH)

    overlay = default_pvp_overlay(world_map)
    overlaid = apply_overlay(world_map, overlay)

    owners = {war_base.id: war_base.owner for war_base in overlaid.war_bases}
    assert list(owners.values()).count(PLAYER_ONE) == 1
    assert list(owners.values()).count(PLAYER_TWO) == 1
    assert list(owners.values()).count(None) == 2

    # The base map itself must remain unmutated by the overlay.
    for war_base in world_map.war_bases:
        assert isinstance(war_base, WarBase)
        assert war_base.owner is None


def test_original_map_occupancy_has_no_conflicts() -> None:
    # load_world_map (via WorldMap.occupancy()) would raise
    # OccupancyConflictError if any two structures' components overlapped;
    # this just makes that guarantee an explicit, named regression test for
    # this specific map rather than an incidental side effect of loading it.
    world_map = load_world_map(MAP_PATH)
    occupancy = world_map.occupancy()
    assert occupancy is not None


# --------------------------------------------------------------------------
# Decoded terrain (CR001.5, issue #152)
# --------------------------------------------------------------------------


def test_original_map_terrain_class_counts_match_decoded_spectrum_map() -> None:
    # Pinned from `data/maps/decode_zx_terrain.py` run against the Spectrum
    # disassembly (see the evidence doc, "Terrain"): element types 2-7 rough,
    # 8-11 mountain, 12-14 ditch; everything else stays at the default.
    world_map = load_world_map(MAP_PATH)
    counts = Counter(world_map.terrain.cells.values())
    assert world_map.terrain.default is TerrainType.NORMAL
    assert counts == {TerrainType.ROUGH: 344, TerrainType.MOUNTAIN: 436, TerrainType.DITCH: 204}


def test_original_map_terrain_never_overlaps_a_structure() -> None:
    # War bases/factories are stamped after terrain in `Lbc6f_initialize_map`,
    # so a structure cell never carries a terrain override.
    world_map = load_world_map(MAP_PATH)
    occupancy = world_map.occupancy()
    assert not [cell for cell in world_map.terrain.cells if occupancy.is_occupied(*cell)]


def _reachable_cells(world_map: WorldMap, chassis: ModuleIdentity, start: tuple[int, int]) -> set[tuple[int, int]]:
    occupancy = world_map.occupancy()
    seen = {start}
    frontier = deque([start])
    while frontier:
        x, y = frontier.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (
                0 <= nx < world_map.width
                and 0 <= ny < world_map.height
                and (nx, ny) not in seen
                and not occupancy.is_occupied(nx, ny)
                and chassis_can_enter(chassis, world_map.terrain.terrain_at(nx, ny))
            ):
                seen.add((nx, ny))
                frontier.append((nx, ny))
    return seen


# Cells robots must stand on: the launch exit and both capture kinds. The
# heli-pad is the commander's landing spot, not a robot destination.
_ROBOT_DESTINATION_KINDS = (
    InteractionKind.EXIT,
    InteractionKind.WARBASE_CAPTURE,
    InteractionKind.FACTORY_CAPTURE,
)


@pytest.mark.parametrize("chassis", sorted(CHASSIS_MODULES, key=lambda m: m.value))
def test_every_robot_destination_is_reachable_by_every_chassis_from_every_exit(
    chassis: ModuleIdentity,
) -> None:
    # A robot of any chassis launched from any war base must be able to reach
    # every exit and capture cell on the static map (structures + terrain).
    world_map = load_world_map(MAP_PATH)
    destinations = sorted(
        min(point.footprint.cells)
        for point in world_map.interaction_points
        if point.kind in _ROBOT_DESTINATION_KINDS
    )
    exits = [
        min(point.footprint.cells)
        for point in world_map.interaction_points
        if point.kind is InteractionKind.EXIT
    ]
    assert len(exits) == 4
    for exit_cell in exits:
        reachable = _reachable_cells(world_map, chassis, exit_cell)
        assert [cell for cell in destinations if cell not in reachable] == []


def test_commander_spawns_are_free_cells_on_the_decoded_map() -> None:
    world_map = load_world_map(MAP_PATH)
    overlaid = apply_overlay(world_map, default_pvp_overlay(world_map))
    occupancy = world_map.occupancy()
    for cell in overlaid.spawn_positions.values():
        assert not occupancy.is_occupied(*cell)
