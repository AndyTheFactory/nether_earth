"""Tests for ``app.transport.snapshots`` (M7 Task 6, issue #95).

Covers: the ``to_snapshot`` -> ``SnapshotMessage`` mapping is a thin,
lossless pass-through (nothing picked out, renamed, or recomputed); the
message round-trips through the protocol's own serialization/validation; and
the tick-broadcast closure it builds sends exactly what ``build_snapshot_message``
would have produced, to the right match's connections only.
"""

from __future__ import annotations

import json

from nether_earth import engine as engine_module
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap
from nether_earth.scenario import default_pvp_scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

from app.protocol import serialize_server_message
from app.protocol.envelope import OutboundMessageAdapter
from app.protocol.snapshot import SnapshotMessage
from app.transport.connections import ConnectionRegistry
from app.transport.snapshots import build_snapshot_message, make_tick_broadcaster


def _new_game_state() -> GameState:
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    return engine_module.new_game(map_data, scenario, players=(PLAYER_ONE, PLAYER_TWO), seed=7)


# -- build_snapshot_message: thin, lossless mapping ---------------------------


def test_build_snapshot_message_carries_to_snapshot_verbatim() -> None:
    state = _new_game_state()

    message = build_snapshot_message("m1", state)

    assert isinstance(message, SnapshotMessage)
    assert message.match_id == "m1"
    assert message.tick == state.tick
    assert message.state.model_dump() == to_snapshot(state)


def test_build_snapshot_message_reflects_a_ticked_state() -> None:
    """A client can reconstruct the exact engine-computed state at any tick,
    not just tick 0 -- this is what "reconstruct all currently exposed
    authoritative state" requires."""
    state = _new_game_state()
    new_state, _events = engine_module.step(state, ())

    message = build_snapshot_message("m1", new_state)

    assert message.tick == new_state.tick == state.tick + 1
    assert message.state.model_dump() == to_snapshot(new_state)
    assert message.state.model_dump() != to_snapshot(state)


def test_snapshot_message_round_trips_through_serialization_and_schema_validation() -> None:
    state = _new_game_state()
    message = build_snapshot_message("m1", state)

    wire_text = serialize_server_message(message)
    payload = json.loads(wire_text)

    assert payload["type"] == "snapshot"
    assert payload["matchId"] == "m1"
    assert payload["tick"] == state.tick
    assert payload["state"] == to_snapshot(state)

    # Round-trips back through the same discriminated-union validator every
    # other outbound message is checked against.
    revalidated = OutboundMessageAdapter.validate_json(wire_text)
    assert isinstance(revalidated, SnapshotMessage)
    assert revalidated.state.model_dump() == to_snapshot(state)


# -- make_tick_broadcaster ----------------------------------------------------


async def test_tick_broadcaster_sends_the_current_snapshot_to_its_own_match_only() -> None:
    registry = ConnectionRegistry()
    sent_a: list[str] = []
    sent_b: list[str] = []

    class _FakeSocket:
        def __init__(self, sink: list[str]) -> None:
            self._sink = sink

        async def send_text(self, text: str) -> None:
            self._sink.append(text)

    registry.register("match-a", "p1", _FakeSocket(sent_a))  # type: ignore[arg-type]
    registry.register("match-b", "p1", _FakeSocket(sent_b))  # type: ignore[arg-type]

    observer = make_tick_broadcaster(registry, "match-a")
    state = _new_game_state()

    await observer(state, ())

    assert len(sent_a) == 1
    assert sent_b == []

    payload = json.loads(sent_a[0])
    assert payload["type"] == "snapshot"
    assert payload["matchId"] == "match-a"
    assert payload["state"] == to_snapshot(state)


def test_real_match_snapshot_keeps_required_nullable_fields_on_the_wire() -> None:
    """A snapshot with commanders/robots carries `null` for required nullable fields (M9.6)."""
    from nether_earth.scenario import create_initial_state, default_pvp_scenario

    from app.match.world import load_standard_world

    world = load_standard_world()
    state = create_initial_state(default_pvp_scenario(), world, seed=3)
    wire_text = serialize_server_message(build_snapshot_message("m1", state))
    payload = json.loads(wire_text)
    assert payload["state"] == to_snapshot(state)
    assert payload["state"]["commanders"][0]["docked_robot_id"] is None
    revalidated = OutboundMessageAdapter.validate_json(wire_text)
    assert isinstance(revalidated, SnapshotMessage)
    assert revalidated.state.model_dump() == to_snapshot(state)
