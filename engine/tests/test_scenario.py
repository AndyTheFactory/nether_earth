from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.scenario import (
    VICTORY_RULE_ZERO_WAR_BASES,
    Scenario,
    create_initial_state,
    default_pvp_scenario,
    initialize_players,
)


def test_default_pvp_scenario_matches_locked_v1_values() -> None:
    scenario = default_pvp_scenario()

    assert scenario.player_starting_warbases == 1
    assert scenario.starting_general_resources == 30
    assert scenario.factory_initial_ownership == "neutral"
    assert scenario.victory_rule == VICTORY_RULE_ZERO_WAR_BASES
    assert scenario.victory_rule


def test_scenario_is_frozen() -> None:
    scenario = default_pvp_scenario()

    try:
        scenario.player_starting_warbases = 2  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("Scenario must be immutable")


def test_scenario_rejects_invalid_values() -> None:
    for kwargs in (
        {"id": ""},
        {"map_id": ""},
        {"map_version": 0},
        {"player_starting_warbases": -1},
        {"starting_general_resources": -1},
        {"victory_rule": ""},
    ):
        base = {
            "id": "pvp-v1",
            "map_id": "zx-spectrum-original",
            "map_version": 1,
            "player_starting_warbases": 1,
        }
        base.update(kwargs)
        try:
            Scenario(**base)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_initialize_players_returns_canonical_two_player_pvp_set() -> None:
    scenario = default_pvp_scenario()

    players = initialize_players(scenario)

    assert players == (PLAYER_ONE, PLAYER_TWO)


def test_initialize_players_is_deterministic_across_equivalent_scenarios() -> None:
    first = initialize_players(default_pvp_scenario())
    second = initialize_players(default_pvp_scenario())

    assert first == second


def test_create_initial_state_is_deterministic_and_starts_at_tick_zero() -> None:
    scenario = default_pvp_scenario()

    first = create_initial_state(scenario)
    second = create_initial_state(scenario)

    assert first == second
    assert first.tick == 0
    assert first.players == (PLAYER_ONE, PLAYER_TWO)


def test_scenario_supports_starting_resource_override() -> None:
    scenario = Scenario(
        id="pvp-custom",
        map_id="zx-spectrum-original",
        map_version=1,
        player_starting_warbases=1,
        starting_general_resources=99,
    )

    assert scenario.starting_general_resources == 99
