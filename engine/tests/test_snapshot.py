"""Tests for canonical ``GameState`` snapshot serialization (issue #8)."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from nether_earth import snapshot
from nether_earth.commander import Commander, CommanderMode, GridTransition, VerticalTransition
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.snapshot import snapshot_to_json_string, to_snapshot
from nether_earth.state import GameState, create_game_state


def test_dataclass_equal_states_serialize_identically() -> None:
    first = create_game_state(5, [PLAYER_ONE, PLAYER_TWO], seed=42)
    second = create_game_state(5, [PLAYER_ONE, PLAYER_TWO], seed=42)

    assert first == second
    assert to_snapshot(first) == to_snapshot(second)
    assert snapshot_to_json_string(first) == snapshot_to_json_string(second)


def test_snapshot_unaffected_by_input_player_order_since_state_canonicalizes() -> None:
    """Regression guard for accidental unsorted/differently-ordered serialization.

    ``create_game_state`` normalizes ``players`` to canonical (sorted) order
    regardless of the order callers pass them in. Two states built from
    logically equal but differently-input-ordered player collections must
    still be dataclass-equal and serialize identically -- if snapshot
    serialization (or state construction) ever regressed to preserve
    caller-supplied order instead, this test would catch it.
    """
    forward = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=1)
    reversed_input = create_game_state(0, [PLAYER_TWO, PLAYER_ONE], seed=1)

    assert forward == reversed_input
    assert to_snapshot(forward) == to_snapshot(reversed_input)
    assert to_snapshot(forward)["players"] == ["p1", "p2"]


def test_different_tick_serializes_differently() -> None:
    a = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=1)
    b = create_game_state(1, [PLAYER_ONE, PLAYER_TWO], seed=1)

    assert a != b
    assert to_snapshot(a) != to_snapshot(b)


def test_different_seed_serializes_differently() -> None:
    a = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=1)
    b = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=2)

    assert a != b
    assert to_snapshot(a) != to_snapshot(b)


def test_different_players_serializes_differently() -> None:
    a = create_game_state(0, [PLAYER_ONE], seed=1)
    b = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=1)

    assert a != b
    assert to_snapshot(a) != to_snapshot(b)


def test_player_ids_use_to_json_convention() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=0)

    result = to_snapshot(state)

    assert result["players"] == [PLAYER_ONE.to_json(), PLAYER_TWO.to_json()]
    assert result["players"] == ["p1", "p2"]


def test_snapshot_uses_only_json_safe_primitives() -> None:
    state = create_game_state(3, [PLAYER_ONE, PLAYER_TWO], seed=99)

    result = to_snapshot(state)

    # Round-trips cleanly through the stdlib json encoder with no custom
    # encoder/default hook required -- proves every value is a plain
    # dict/list/str/int/bool/None.
    reloaded = json.loads(json.dumps(result))
    assert reloaded == result


def test_snapshot_to_json_string_is_stable_and_matches_dict() -> None:
    state = create_game_state(7, [PLAYER_ONE, PLAYER_TWO], seed=5)

    first = snapshot_to_json_string(state)
    second = snapshot_to_json_string(state)

    assert first == second
    assert json.loads(first) == to_snapshot(state)


def test_snapshot_key_order_is_fixed() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=0)

    result = to_snapshot(state)

    assert list(result.keys()) == ["tick", "players", "seed", "commanders"]


def test_arbitrary_player_id_serializes_via_to_json_not_reimplemented() -> None:
    custom = create_game_state(0, [PlayerId("zzz"), PLAYER_ONE], seed=0)

    result = to_snapshot(custom)

    # create_game_state sorts by PlayerId.value, so "p1" < "zzz".
    assert result["players"] == ["p1", "zzz"]


def test_module_does_not_reference_network_or_frontend_apis() -> None:
    forbidden = {"fastapi", "starlette", "websockets", "requests", "httpx"}
    source = Path(inspect.getfile(snapshot)).read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0].lower() not in forbidden
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0].lower() not in forbidden


def test_to_snapshot_return_type_is_plain_dict() -> None:
    state: GameState = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=0)
    assert type(to_snapshot(state)) is dict


def test_commanders_default_to_empty_list_in_snapshot() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO], seed=0)

    result = to_snapshot(state)

    assert result["commanders"] == []


def test_commander_snapshot_round_trips_all_fields() -> None:
    robot_id = EntityId("robot-7")
    docked = Commander(
        player_id=PLAYER_TWO,
        mode=CommanderMode.DOCKED,
        x=5,
        y=6,
        altitude=0,
        docked_robot_id=robot_id,
    )
    free = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=1, y=2, altitude=12)
    state = create_game_state(3, [PLAYER_ONE, PLAYER_TWO], seed=1, commanders=[docked, free])

    result = to_snapshot(state)
    commanders = result["commanders"]

    assert len(commanders) == 2
    assert commanders[0]["player_id"] == "p1"
    assert commanders[0]["mode"] == "free"
    assert commanders[0]["x"] == 1
    assert commanders[0]["y"] == 2
    assert commanders[0]["altitude"] == 12
    assert commanders[0]["docked_robot_id"] is None
    assert commanders[0]["rising"] is False
    assert commanders[0]["horizontal_transition"] is None
    assert commanders[0]["vertical_transition"] is None
    assert commanders[1]["player_id"] == "p2"
    assert commanders[1]["mode"] == "docked"
    assert commanders[1]["x"] == 5
    assert commanders[1]["y"] == 6
    assert commanders[1]["altitude"] == 0
    assert commanders[1]["docked_robot_id"] == "robot-7"
    assert commanders[1]["rising"] is False
    assert commanders[1]["horizontal_transition"] is None
    assert commanders[1]["vertical_transition"] is None


def test_commander_snapshot_includes_movement_fields() -> None:
    transitioning = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.FREE,
        x=1,
        y=1,
        altitude=4,
        rising=True,
        horizontal_transition=GridTransition(
            from_x=1, from_y=1, to_x=2, to_y=1, started_tick=3, duration_ticks=4
        ),
        vertical_transition=VerticalTransition(
            from_altitude=2, to_altitude=4, started_tick=2, duration_ticks=4
        ),
    )
    state = create_game_state(3, [PLAYER_ONE], seed=1, commanders=[transitioning])

    result = to_snapshot(state)
    commander_snapshot = result["commanders"][0]

    assert commander_snapshot["rising"] is True
    assert commander_snapshot["horizontal_transition"] == {
        "from_x": 1,
        "from_y": 1,
        "to_x": 2,
        "to_y": 1,
        "started_tick": 3,
        "duration_ticks": 4,
    }
    assert commander_snapshot["vertical_transition"] == {
        "from_altitude": 2,
        "to_altitude": 4,
        "started_tick": 2,
        "duration_ticks": 4,
    }
    # Round-trips cleanly through the stdlib json encoder.
    assert json.loads(json.dumps(result)) == result


def test_commander_snapshot_is_json_safe_and_stable() -> None:
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=1, y=2, altitude=12)
    state = create_game_state(0, [PLAYER_ONE], seed=0, commanders=[commander])

    result = to_snapshot(state)
    reloaded = json.loads(json.dumps(result))
    assert reloaded == result

    first = snapshot_to_json_string(state)
    second = snapshot_to_json_string(state)
    assert first == second


def test_dataclass_equal_states_with_commanders_serialize_identically() -> None:
    commander_a = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=1, y=2, altitude=12)
    commander_b = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=1, y=2, altitude=12)

    first = create_game_state(0, [PLAYER_ONE], seed=0, commanders=[commander_a])
    second = create_game_state(0, [PLAYER_ONE], seed=0, commanders=[commander_b])

    assert first == second
    assert to_snapshot(first) == to_snapshot(second)


def test_different_commander_altitude_serializes_differently() -> None:
    low = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)
    high = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=10)

    a_state = create_game_state(0, [PLAYER_ONE], seed=0, commanders=[low])
    b_state = create_game_state(0, [PLAYER_ONE], seed=0, commanders=[high])

    assert a_state != b_state
    assert to_snapshot(a_state) != to_snapshot(b_state)
