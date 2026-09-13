"""Tests for the evidence-backed original-map ingestion (issue #25, M2.7).

Spec references: `_specs/milestones/02-map-world-model.md` ("Original map
ingestion" workstream, "Milestone integration scenario" — "load the current
original-map YAML and prove it validates and initializes reproducibly");
`_specs/open-questions.md` §2 (RESOLVED — v1 PvP overlay needs exactly four
war bases: two extreme, two neutral interior).

See `data/maps/zx-spectrum-original.md` for the full evidence/provenance
record backing every value in `data/maps/zx-spectrum-original.yaml`,
including which fields are disassembly-verified vs. explicitly-flagged
unverified placeholders pending human review.
"""

from pathlib import Path

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.scenario import default_pvp_scenario
from nether_earth.structures import WarBase

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
