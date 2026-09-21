"""``app.match.world``: the backend loads the real, scenario-overlaid standard map (M9.1 gap G1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.scenario import Scenario, commander_spawn_key, default_pvp_scenario

from app.match.world import MAP_DIR_ENV_VAR, default_map_dir, load_standard_world, map_path_for


def test_default_map_dir_is_repo_data_maps_unless_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MAP_DIR_ENV_VAR, raising=False)
    assert default_map_dir().name == "maps"
    assert default_map_dir().parent.name == "data"
    assert map_path_for(default_pvp_scenario()).exists()

    monkeypatch.setenv(MAP_DIR_ENV_VAR, "/somewhere/else")
    assert default_map_dir() == Path("/somewhere/else")


def test_load_standard_world_applies_the_locked_pvp_overlay() -> None:
    scenario = default_pvp_scenario()
    world = load_standard_world(scenario)

    assert (world.map_id, world.version) == (scenario.map_id, scenario.map_version)
    owners = {base.id.value: base.owner for base in world.war_bases}
    assert owners == {
        "warbase-1": PLAYER_ONE,
        "warbase-2": None,
        "warbase-3": None,
        "warbase-4": PLAYER_TWO,
    }
    assert all(factory.owner is None for factory in world.factories)
    assert commander_spawn_key(PLAYER_ONE) in world.spawn_positions
    assert commander_spawn_key(PLAYER_TWO) in world.spawn_positions


def test_load_standard_world_rejects_a_map_that_does_not_match_the_scenario() -> None:
    wrong_version = Scenario(
        id="pvp-v1", map_id="zx-spectrum-original", map_version=2, player_starting_warbases=1
    )
    with pytest.raises(ValueError, match="v1"):
        load_standard_world(wrong_version)
