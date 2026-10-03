"""M9.2 -- canonical v1 PvP scenario dataset: drift guard (issue #114).

The M9 acceptance path (`_specs/functional-spec.md` §4) uses one
locked scenario: ``scenario.default_pvp_scenario()`` (id ``pvp-v1``) on map
``zx-spectrum-original`` v1 with ``map_overlay.default_pvp_overlay`` applied.
Every value below is fixed by `_specs/functional-spec.md` §4/§10.1,
`_specs/technical-spec.md` §6 and `_specs/open-questions.md` §2/§17; this
test fails on accidental drift of any acceptance-critical value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nether_earth.capture import effective_world
from nether_earth.commander import CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import STANDARD_PVP_OVERLAY_ID, apply_overlay, default_pvp_overlay
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import (
    VICTORY_RULE_ZERO_WAR_BASES,
    commander_spawn_key,
    create_initial_state,
    default_pvp_scenario,
)
from nether_earth.snapshot import to_snapshot

#: The one scenario id / map id / map version every M9 test references.
CANONICAL_SCENARIO_ID = "pvp-v1"
CANONICAL_MAP_ID = "zx-spectrum-original"
CANONICAL_MAP_VERSION = 1
ORIGINAL_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / f"{CANONICAL_MAP_ID}.yaml"


@pytest.fixture(scope="module")
def world() -> WorldMap:
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def test_canonical_scenario_identity_and_locked_values() -> None:
    scenario = default_pvp_scenario()
    assert scenario.id == CANONICAL_SCENARIO_ID
    assert scenario.map_id == CANONICAL_MAP_ID
    assert scenario.map_version == CANONICAL_MAP_VERSION
    assert scenario.player_starting_warbases == 1
    assert scenario.starting_general_resources == 20
    assert scenario.starting_general_resources == DEFAULT_RULES.starting_general_resources
    assert scenario.factory_initial_ownership == "neutral"
    assert scenario.victory_rule == VICTORY_RULE_ZERO_WAR_BASES


def test_canonical_world_is_the_real_original_map_with_the_standard_overlay(world: WorldMap) -> None:
    assert (world.map_id, world.version) == (CANONICAL_MAP_ID, CANONICAL_MAP_VERSION)
    assert (world.width, world.height) == (512, 16)
    assert default_pvp_overlay(load_world_map(ORIGINAL_MAP_PATH)).id == STANDARD_PVP_OVERLAY_ID
    # Real map, not a compact fixture: four war bases, 24 factories.
    assert len(world.war_bases) == 4
    assert len(world.factories) == 24


def test_starting_ownership_is_left_right_with_two_neutral_interior_bases(world: WorldMap) -> None:
    by_min_x = sorted(world.war_bases, key=lambda base: min(c.x for c in base.components))
    assert [base.id.value for base in by_min_x] == ["warbase-1", "warbase-2", "warbase-3", "warbase-4"]
    assert [base.owner for base in by_min_x] == [PLAYER_ONE, None, None, PLAYER_TWO]
    assert all(factory.owner is None for factory in world.factories)


def test_fresh_game_from_canonical_scenario_always_starts_with_the_locked_state(world: WorldMap) -> None:
    scenario = default_pvp_scenario()
    first = create_initial_state(scenario, world, seed=12345)
    second = create_initial_state(scenario, world, seed=12345)
    assert first == second
    assert to_snapshot(first) == to_snapshot(second)

    assert first.tick == 0
    assert first.players == (PLAYER_ONE, PLAYER_TWO)
    assert first.seed == 12345

    commanders = {commander.player_id: commander for commander in first.commanders}
    assert set(commanders) == {PLAYER_ONE, PLAYER_TWO}
    for player, commander in commanders.items():
        assert commander.mode is CommanderMode.FREE
        assert commander.altitude == DEFAULT_RULES.commander_min_altitude
        assert (commander.x, commander.y) == world.spawn_positions[commander_spawn_key(player)]
    # Evidence-backed Player 1 start (open-questions §17); mirrored Player 2.
    assert (commanders[PLAYER_ONE].x, commanders[PLAYER_ONE].y) == (17, 10)
    assert (commanders[PLAYER_TWO].x, commanders[PLAYER_TWO].y) == (499, 9)

    pools = {pool.player_id: pool for pool in first.resource_pools}
    assert set(pools) == {PLAYER_ONE, PLAYER_TWO}
    for pool in pools.values():
        assert pool.general == 20
        assert (pool.chassis, pool.electronics, pool.nuclear, pool.missile, pool.phaser, pool.cannon) == (
            0, 0, 0, 0, 0, 0,
        )

    ownership = {record.structure_id.value: record.owner for record in first.structure_ownership}
    assert ownership == {"warbase-1": PLAYER_ONE, "warbase-4": PLAYER_TWO}
    assert first.robots == ()
    assert first.construction_sessions == ()
    assert first.capture_progress == ()
    assert first.projectiles == ()
    assert first.structure_destruction == ()
    # The snapshot is self-contained: layering it back over the base map is the identity.
    assert effective_world(world, first) == world


def test_seed_only_changes_the_seed_field(world: WorldMap) -> None:
    scenario = default_pvp_scenario()
    a = create_initial_state(scenario, world, seed=1)
    b = create_initial_state(scenario, world, seed=2)
    assert a.with_tick(0) != b
    assert to_snapshot(a) | {"seed": 2} == to_snapshot(b)


def test_scenario_rejects_a_world_for_a_different_map(world: WorldMap) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="does not match"):
        create_initial_state(default_pvp_scenario(), replace(world, version=2))
