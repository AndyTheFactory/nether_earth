"""Tests for the ``/ws`` WebSocket transport (M7 Task 5, issue #94).

Covers the acceptance criteria from the task brief:

- invalid JSON/schema/session messages never reach the engine;
- a player cannot submit commands for another match/player;
- messages from one match are never broadcast to another match's
  connections;
- disconnect reaches the lifecycle notification hook exactly once (whether
  via a clean ``leave`` or an abrupt socket close);
- the handler stays thin (exercised indirectly: every assertion here is
  about wire-level behavior, never about gameplay legality).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nether_earth.commander_movement import CommanderMoveCommand
from nether_earth.commands import Command
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.snapshot import to_snapshot
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.match.manager import MatchManager
from app.match.models import MatchOutcome, MatchResult, MatchRuntimeState
from app.match.runtime import MatchRuntimeRegistry
from app.match.world import load_standard_world
from app.transport import ConnectionRegistry, create_websocket_router
from tests.transport._helpers import (
    _create,
    _join,
    _match_manager,
    _next_non_snapshot,
    _ready,
    _start_active_match_keeping_sockets_open,
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    # `replay_dir=tmp_path` (M7 Task 8, issue #97): without this, `create_app`
    # would fall back to `ReplayWriter`'s own repo-relative default and every
    # match created below would write a real replay artifact onto disk
    # outside of pytest's tmp directory.
    app = create_app(replay_dir=tmp_path / "replays")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def no_tick_client() -> Iterator[TestClient]:
    """An app whose ``MatchManager`` has no ``MatchRuntimeRegistry`` wired in.

    A match still goes WAITING -> ACTIVE normally (``engine.new_game`` still
    runs, so ``game_state`` is real and non-None), but no ``MatchRuntime`` is
    ever started, so ``game_state`` never advances on its own. Used by tests
    that need a deterministic, non-ticking authoritative state to assert
    against (e.g. reconnect's "never mutates/advances the engine" and
    "matches `to_snapshot(match.game_state)` exactly" guarantees) without
    racing the real 20 Hz tick loop `create_app()` wires up in production.
    """
    match_manager = MatchManager()  # no `runtime=` -> no ticking, ever.
    runtime_registry = MatchRuntimeRegistry()
    connection_registry = ConnectionRegistry()
    app = FastAPI()
    app.state.match_manager = match_manager
    app.state.runtime_registry = runtime_registry
    app.state.connection_registry = connection_registry
    app.include_router(create_websocket_router(match_manager, runtime_registry, connection_registry))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def short_grace_client(tmp_path: Path) -> Iterator[TestClient]:
    """The real ``create_app()`` composition root with a tiny reconnect grace period.

    Used by the disconnect/reconnect (M7 Task 7, issue #96) wiring tests
    below: proves ``ServerPaused``/``ServerResumed``/``ServerForfeit`` are
    actually broadcast end to end through the same hook set production
    uses, without waiting anywhere near the real 60s default grace.
    """
    app = create_app(replay_dir=tmp_path / "replays", reconnect_grace_seconds=0.05)
    with TestClient(app) as test_client:
        yield test_client


# -- connect + create/join happy path ----------------------------------------


def test_create_returns_schema_valid_created_message(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        created = _create(ws)

    assert created["type"] == "created"
    assert created["protocolVersion"] == 1
    assert created["joinCode"]
    assert created["playerId"] == "p1"
    assert created["sessionToken"]


def test_pvp_create_reply_carries_no_opponent_key_on_the_wire(client: TestClient) -> None:
    """CR004.8 fix round 1: adding `opponent` to `ServerCreated` must not add
    a new key to every existing PvP `created` reply -- `opponent` is `None`
    on that path and `serialize_server_message` drops it, exactly like any
    other unset optional field, so a pre-CR004.8 client (or a strict-schema
    consumer) sees byte-identical output to before this feature existed.
    Only `join_code` is special-cased to stay present (as `null`) for a
    solo match; this test is the PvP side of that split.
    """
    with client.websocket_connect("/ws") as ws:
        created = _create(ws, "alice")

    assert set(created.keys()) == {
        "protocolVersion",
        "type",
        "matchId",
        "joinCode",
        "playerId",
        "sessionToken",
    }
    assert "opponent" not in created
    # CR004.9: joinCode must stay in its schema-declared position on the
    # wire, not be reinserted at the end of the payload.
    assert list(created.keys()) == [
        "protocolVersion",
        "type",
        "matchId",
        "joinCode",
        "playerId",
        "sessionToken",
    ]


def test_join_returns_joined_and_broadcasts_ready_state_to_creator(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created = _create(ws_a, "alice")

        joined = _join(ws_b, created["joinCode"], "bob")
        assert joined["type"] == "joined"
        assert joined["playerId"] == "p2"

        # The creator (ws_a) receives the roster broadcast triggered by the join.
        ready_state_a = ws_a.receive_json()
        assert ready_state_a["type"] == "ready_state"
        nicknames = {p["nickname"] for p in ready_state_a["players"]}
        assert nicknames == {"alice", "bob"}

        # The joiner also receives its own copy of the broadcast.
        ready_state_b = ws_b.receive_json()
        assert ready_state_b["type"] == "ready_state"


def test_ready_from_both_players_starts_the_match(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created = _create(ws_a, "alice")
        joined = _join(ws_b, created["joinCode"], "bob")
        ws_a.receive_json()  # ready_state from join
        ws_b.receive_json()  # ready_state from join (own echo)

        _ready(
            ws_a,
            match_id=created["matchId"],
            player_id="p1",
            session_token=created["sessionToken"],
        )
        # Both connections get the ready_state broadcast for p1 readying up.
        assert ws_a.receive_json()["type"] == "ready_state"
        assert ws_b.receive_json()["type"] == "ready_state"

        _ready(
            ws_b,
            match_id=created["matchId"],
            player_id="p2",
            session_token=joined["sessionToken"],
        )
        assert ws_a.receive_json()["type"] == "ready_state"
        assert ws_b.receive_json()["type"] == "ready_state"

        # Both slots ready -> match transitions to ACTIVE and "started" is
        # broadcast to every connection.
        assert ws_a.receive_json()["type"] == "started"
        assert ws_b.receive_json()["type"] == "started"


def test_solo_create_reports_no_join_code_and_computer_opponent(client: TestClient) -> None:
    """CR004.8 (issue #289): ``create`` with ``opponent: "computer"`` wires
    to ``MatchManager.create_solo_match`` -- no join code is issued (there
    is no second human slot to join), and ``created.opponent`` states which
    seat the server actually created so the frontend never has to infer it
    from the absence of a join code.
    """
    with client.websocket_connect("/ws") as ws:
        created = _create(ws, "alice", opponent="computer")

    assert created["type"] == "created"
    assert created["joinCode"] is None
    assert created["opponent"] == "computer"
    assert created["playerId"] == "p1"
    assert created["sessionToken"]
    # CR004.9: joinCode is null (not absent) but still in its declared
    # position, ahead of playerId/sessionToken/opponent.
    assert list(created.keys()) == [
        "protocolVersion",
        "type",
        "matchId",
        "joinCode",
        "playerId",
        "sessionToken",
        "opponent",
    ]


def test_solo_create_then_ready_alone_starts_the_match_with_the_ai_seat() -> None:
    """The AI seat is always ready (CR004.7): the human's own ``setReady`` --
    exactly what the frontend's solo flow sends automatically right after
    ``created``, with no waiting screen or ready-button step -- is
    sufficient to reach ``started``, with no second connection ever
    involved.

    Builds its own app (rather than reusing ``no_tick_client``) because
    ``create_solo_match`` needs a real ``world`` (``MatchManager(world=...)``
    ) while still wiring no live ``MatchRuntime``/ticking, so the single
    connection's message order stays deterministic.
    """
    match_manager = MatchManager(world=load_standard_world())
    runtime_registry = MatchRuntimeRegistry()
    connection_registry = ConnectionRegistry()
    app = FastAPI()
    app.state.match_manager = match_manager
    app.state.runtime_registry = runtime_registry
    app.state.connection_registry = connection_registry
    app.include_router(create_websocket_router(match_manager, runtime_registry, connection_registry))

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        created = _create(ws, "alice", opponent="computer")
        assert created["joinCode"] is None
        assert created["opponent"] == "computer"

        _ready(
            ws,
            match_id=created["matchId"],
            player_id=created["playerId"],
            session_token=created["sessionToken"],
        )

        # The human alone readying up is enough: the AI seat contributes no
        # PlayerSlot, so ready_state lists only the human, then the match
        # starts immediately -- no second player's readiness is awaited.
        ready_state = ws.receive_json()
        assert ready_state["type"] == "ready_state"
        assert [p["playerId"] for p in ready_state["players"]] == ["p1"]
        assert ready_state["players"][0]["ready"] is True

        started = ws.receive_json()
        assert started["type"] == "started"
        assert started["matchId"] == created["matchId"]

        # The initial authoritative snapshot follows "started" (see
        # `test_match_start_broadcasts_a_real_initial_authoritative_snapshot`
        # for the PvP case). CR004.6 added `ai_memories` to `SnapshotState`
        # (`app.protocol.common`), so a solo match's tick-0 state -- which
        # carries one `AiMemory` entry for the AI seat -- now round-trips
        # through the wire without crashing the socket.
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["state"]["tick"] == 0
        ai_memories = snapshot["state"]["ai_memories"]
        assert [m["player_id"] for m in ai_memories] == ["p2"]


def test_match_start_lands_a_replay_artifact_on_disk(
    client: TestClient, tmp_path: Path
) -> None:
    """The real ``create_app()`` composition-root wiring actually produces a
    filesystem replay artifact, not just the hand-built ``ReplayWriter``/
    ``MatchRuntime`` objects every other test in ``tests/replay/`` exercises
    directly (M7 Task 8 review, Important I5). A dropped hook or swapped
    factory in ``app.main`` would leave `tests/replay/` fully green while
    production silently wrote zero artifacts -- this is the one test that
    would catch that.

    ``tmp_path`` here is the exact same directory the ``client`` fixture
    passed to ``create_app(replay_dir=...)`` (both fixtures are
    function-scoped, so requesting ``tmp_path`` alongside ``client`` in one
    test yields the same underlying path).
    """
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created = _create(ws_a, "alice")
        joined = _join(ws_b, created["joinCode"], "bob")
        ws_a.receive_json()
        ws_b.receive_json()

        _ready(
            ws_a,
            match_id=created["matchId"],
            player_id="p1",
            session_token=created["sessionToken"],
        )
        ws_a.receive_json()
        ws_b.receive_json()

        _ready(
            ws_b,
            match_id=created["matchId"],
            player_id="p2",
            session_token=joined["sessionToken"],
        )
        ws_a.receive_json()
        ws_b.receive_json()
        assert ws_a.receive_json()["type"] == "started"
        assert ws_b.receive_json()["type"] == "started"

    meta_path = tmp_path / "replays" / created["matchId"] / "meta.json"
    assert meta_path.exists(), (
        f"expected a replay artifact at {meta_path} after the match started -- "
        "the create_app() composition-root wiring did not produce one"
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["match_id"] == created["matchId"]
    assert meta["status"] == "in_progress"


def test_match_start_broadcasts_a_real_initial_authoritative_snapshot(
    no_tick_client: TestClient,
) -> None:
    """The "started" broadcast is immediately followed by a real snapshot
    (tick 0, the state `engine.new_game` produced) -- not the empty
    placeholder, and reconstructable to exactly what the engine computed.

    Uses `no_tick_client` (no live `MatchRuntime`) rather than asserting
    against `manager.get_match(...)` read *after* the `with` block: even
    with the structural first-tick fix (`require_announcement`), a live
    match's runtime resumes ticking immediately once
    `runtime_registry.announce_started(...)` fires (right after these two
    broadcasts complete), so re-deriving "expected state" from the live
    match after the fact would race that ongoing ticking. Asserting the two
    connections' own received messages against each other, plus the
    snapshot's self-reported `tick == 0`, needs no live-match read at all.
    """
    client = no_tick_client

    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created = _create(ws_a, "alice")
        joined = _join(ws_b, created["joinCode"], "bob")
        ws_a.receive_json()
        ws_b.receive_json()

        _ready(
            ws_a,
            match_id=created["matchId"],
            player_id="p1",
            session_token=created["sessionToken"],
        )
        ws_a.receive_json()
        ws_b.receive_json()

        _ready(
            ws_b,
            match_id=created["matchId"],
            player_id="p2",
            session_token=joined["sessionToken"],
        )
        ws_a.receive_json()  # ready_state from p2 readying up
        ws_b.receive_json()
        assert ws_a.receive_json()["type"] == "started"
        assert ws_b.receive_json()["type"] == "started"

        snapshot_a = ws_a.receive_json()
        snapshot_b = ws_b.receive_json()

    assert snapshot_a["type"] == "snapshot"
    assert snapshot_b["type"] == "snapshot"
    assert snapshot_a["matchId"] == created["matchId"]
    assert snapshot_a["tick"] == 0
    assert snapshot_a["state"]["tick"] == 0
    # Both connections received the identical broadcast for this tick.
    assert snapshot_a == snapshot_b


# -- malformed messages never reach the engine --------------------------------


def test_invalid_json_gets_error_and_connection_stays_open(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not valid json at all")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "invalid_message"
        assert "matchId" not in error  # exclude_none=True: no match context yet

        # The connection is still usable afterwards.
        created = _create(ws)
        assert created["type"] == "created"


def test_schema_violation_gets_error_not_a_crash(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        # Unknown "type" discriminator.
        ws.send_text(json.dumps({"protocolVersion": 1, "type": "not_a_real_type"}))
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "invalid_message"


# -- session / auth boundary ---------------------------------------------------


def test_unknown_session_token_is_rejected_and_closes(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "ready",
                    "matchId": "does-not-exist",
                    "playerId": "p1",
                    "sessionToken": "totally-bogus-token",
                    "ready": True,
                }
            )
        )
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "invalid_session"

        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_second_create_on_an_already_bound_socket_is_rejected_and_closes(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws") as ws:
        _create(ws, "alice")

        ws.send_text(json.dumps({"protocolVersion": 1, "type": "create", "nickname": "again"}))
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "already_bound"

        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_second_join_on_an_already_bound_socket_is_rejected_and_closes(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created = _create(ws_a, "alice")
        _create(ws_b, "someone_else")

        ws_b.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "join",
                    "joinCode": created["joinCode"],
                    "nickname": "bob",
                }
            )
        )
        error = ws_b.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "already_bound"

        with pytest.raises(WebSocketDisconnect):
            ws_b.receive_text()


def test_token_for_wrong_match_is_rejected_and_closes(client: TestClient) -> None:
    """A valid token presented alongside a mismatched matchId/playerId is rejected."""
    with (
        client.websocket_connect("/ws") as ws_a,
        client.websocket_connect("/ws") as ws_b,
    ):
        created_a = _create(ws_a, "alice")
        created_b = _create(ws_b, "carol")

        # Use ws_b's own valid session token, but claim ws_a's match/player.
        ws_b.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "ready",
                    "matchId": created_a["matchId"],
                    "playerId": "p1",
                    "sessionToken": created_b["sessionToken"],
                    "ready": True,
                }
            )
        )
        error = ws_b.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "session_mismatch"

        with pytest.raises(WebSocketDisconnect):
            ws_b.receive_text()

        # ws_a's own connection/match is completely unaffected.
        ws_a.send_text(
            json.dumps({"protocolVersion": 1, "type": "not_real"})
        )
        still_alive = ws_a.receive_json()
        assert still_alive["type"] == "error"


# -- cross-match isolation ------------------------------------------------------


def test_no_cross_match_broadcast_leakage(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws") as a1,
        client.websocket_connect("/ws") as a2,
        client.websocket_connect("/ws") as b1,
        client.websocket_connect("/ws") as b2,
    ):
        created_a = _create(a1, "alice")
        joined_a = _join(a2, created_a["joinCode"], "adam")
        a1.receive_json()  # ready_state
        a2.receive_json()  # ready_state (own echo)

        created_b = _create(b1, "beth")
        joined_b = _join(b2, created_b["joinCode"], "ben")
        b1.receive_json()  # ready_state
        b2.receive_json()  # ready_state (own echo)

        # Trigger a broadcast in match A only.
        _ready(
            a1,
            match_id=created_a["matchId"],
            player_id="p1",
            session_token=created_a["sessionToken"],
        )
        ready_state_a1 = a1.receive_json()
        ready_state_a2 = a2.receive_json()
        assert ready_state_a1["type"] == "ready_state"
        assert ready_state_a2["type"] == "ready_state"
        assert ready_state_a1["matchId"] == created_a["matchId"]

        # Immediately probe match B's connections: the very next frame they
        # receive must be the response to *this* probe, proving nothing from
        # match A's broadcast was ever queued on them.
        b1.send_text(json.dumps({"protocolVersion": 1, "type": "not_real"}))
        probe_response_b1 = b1.receive_json()
        assert probe_response_b1["type"] == "error"

        b2.send_text(json.dumps({"protocolVersion": 1, "type": "not_real"}))
        probe_response_b2 = b2.receive_json()
        assert probe_response_b2["type"] == "error"

        assert joined_a["matchId"] != joined_b["matchId"]


# -- gameplay command routing ---------------------------------------------------


def test_command_for_a_non_active_match_is_rejected_not_crashed(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        created = _create(ws)
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "command",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                    "clientSequence": 0,
                    "payload": {"kind": "cancel_construction"},
                }
            )
        )
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "command_rejected"


def test_valid_commander_move_command_reaches_submit_command_as_the_real_engine_command(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M7 Task 9 review (Important I4): prove the payload -> Command adapter
    is actually wired into `ClientGameplayCommand` handling, not merely unit
    tested in isolation. Spies on `MatchRuntimeRegistry.submit_command`
    (rather than reaching into private queue internals) so this asserts on
    the exact object `ws.py` builds and submits: a real
    `CommanderMoveCommand` with the payload's own `dx`/`dy`, never the bare
    `nether_earth.commands.Command` envelope the pre-#98 handler used to
    build.
    """
    created, _joined = _start_active_match(client)
    captured: list[Command] = []
    original_submit_command = MatchRuntimeRegistry.submit_command

    async def spying_submit_command(
        self: MatchRuntimeRegistry, match_id: str, command: Command
    ) -> bool:
        captured.append(command)
        return await original_submit_command(self, match_id, command)

    monkeypatch.setattr(MatchRuntimeRegistry, "submit_command", spying_submit_command)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "command",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                    "clientSequence": 0,
                    "payload": {"kind": "commander_move", "dx": 1, "dy": 0},
                }
            )
        )
        # A valid, accepted gameplay command produces no ack of its own; a
        # second, deliberately malformed frame on the same connection forces
        # strict in-order processing (single-threaded per-connection loop),
        # so receiving *its* error here guarantees the first command was
        # already fully handled -- the same synchronization idiom
        # `test_invalid_json_gets_error_and_connection_stays_open` uses.
        ws.send_text(json.dumps({"protocolVersion": 1, "type": "not_a_real_type"}))
        probe_error = ws.receive_json()
        assert probe_error["error"]["code"] == "invalid_message"

    assert len(captured) == 1
    command = captured[0]
    assert isinstance(command, CommanderMoveCommand)
    assert type(command) is CommanderMoveCommand  # not the bare base Command
    assert command.dx == 1
    assert command.dy == 0
    assert command.player == PLAYER_ONE
    assert command.sequence == 0


def test_malformed_command_payload_produces_error_without_closing_socket(
    client: TestClient,
) -> None:
    """M7 Task 9 review (Important I4): a schema-valid but structurally
    malformed payload (diagonal move -- `cellDelta` restricts each axis
    independently, so `dx=1, dy=1` passes schema validation and is only
    caught by `CommanderMoveCommand.__post_init__`) must be rejected via
    `app.transport.commands.CommandPayloadError` -> a normal `ServerError`,
    never a crash or connection close, and must never reach
    `MatchRuntimeRegistry.submit_command`/`engine.step` at all.
    """
    created, _joined = _start_active_match(client)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "command",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                    "clientSequence": 0,
                    "payload": {"kind": "commander_move", "dx": 1, "dy": 1},
                }
            )
        )
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "invalid_command_payload"

        # The connection is still usable afterwards -- not closed.
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "command",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                    "clientSequence": 1,
                    "payload": {"kind": "commander_move", "dx": 1, "dy": 1},
                }
            )
        )
        second_error = ws.receive_json()
        assert second_error["type"] == "error"
        assert second_error["error"]["code"] == "invalid_command_payload"


# -- disconnect notification exactly once --------------------------------------


def test_leave_notifies_disconnect_hook_exactly_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _match_manager(client)
    calls: list[str] = []
    original = manager.mark_disconnected

    def counting(session_token: str) -> None:
        calls.append(session_token)
        original(session_token)

    monkeypatch.setattr(manager, "mark_disconnected", counting)

    with client.websocket_connect("/ws") as ws:
        created = _create(ws)
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "leave",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()

    assert calls == [created["sessionToken"]]


def test_abrupt_disconnect_notifies_hook_exactly_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _match_manager(client)
    calls: list[str] = []
    original = manager.mark_disconnected

    def counting(session_token: str) -> None:
        calls.append(session_token)
        original(session_token)

    monkeypatch.setattr(manager, "mark_disconnected", counting)

    with client.websocket_connect("/ws") as ws:
        created = _create(ws)

    assert calls == [created["sessionToken"]]


def test_disconnect_before_any_session_bound_does_not_notify(client: TestClient) -> None:
    # A connection that never authenticates has nothing to notify about, and
    # must not raise/crash on teardown.
    with client.websocket_connect("/ws"):
        pass


# -- reconnect ------------------------------------------------------------------


def test_reconnect_returns_resync_snapshot_and_rebinds_connection(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first_ws:
        created = _create(first_ws)

    with client.websocket_connect("/ws") as second_ws:
        second_ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        resync = second_ws.receive_json()

    assert resync["type"] == "resync"
    assert resync["matchId"] == created["matchId"]
    assert resync["playerId"] == "p1"
    assert resync["snapshot"]["type"] == "snapshot"


def test_second_socket_with_same_session_closes_the_first(client: TestClient) -> None:
    """NE-06: a token holder gets exactly one live socket; the superseded one is closed 4000."""
    with client.websocket_connect("/ws") as first_ws:
        created = _create(first_ws)
        with client.websocket_connect("/ws") as second_ws:
            second_ws.send_text(
                json.dumps(
                    {
                        "protocolVersion": 1,
                        "type": "reconnect",
                        "matchId": created["matchId"],
                        "playerId": "p1",
                        "sessionToken": created["sessionToken"],
                    }
                )
            )
            assert second_ws.receive_json()["type"] == "resync"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                first_ws.receive_json()
            assert exc_info.value.code == 4000
            # The newer socket is still the live one: a ready toggle on it is served.
            _ready(
                second_ws,
                match_id=created["matchId"],
                player_id="p1",
                session_token=created["sessionToken"],
                ready=False,
            )
            assert second_ws.receive_json()["type"] == "ready_state"


def test_reconnect_to_a_still_waiting_match_returns_a_valid_empty_snapshot_not_a_crash(
    client: TestClient,
) -> None:
    """Regression test for M7 Task 9 review (Important I1).

    `app.transport.snapshots.empty_snapshot_message` is the reconnect-side
    fallback for a match that has never gone ACTIVE (`match.game_state is
    None`) -- exercised whenever a player reconnects to a still-`WAITING`
    match, exactly as here (only `create`, never `ready`, so the match never
    reaches `ACTIVE`). The interrupted prior attempt at issue #98 left this
    building `SnapshotMessage(..., state={})`, which validated fine against
    the old placeholder `dict[str, Any]` `SnapshotState` but raises
    `pydantic.ValidationError` now that `SnapshotState` is a real,
    fully-required model -- a live crash on this exact path, caught here by
    asserting the resync/snapshot round-trips as valid JSON with the full,
    correctly-empty `SnapshotState` shape (every list field empty, not a
    bare `{}`), rather than only checking the connection didn't blow up.
    """
    with client.websocket_connect("/ws") as first_ws:
        created = _create(first_ws)

    with client.websocket_connect("/ws") as second_ws:
        second_ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        resync = second_ws.receive_json()

    assert resync["type"] == "resync"
    state = resync["snapshot"]["state"]
    assert state == {
        "tick": 0,
        "players": [],
        "seed": 0,
        "commanders": [],
        "resource_pools": [],
        "construction_sessions": [],
        "robots": [],
        "structure_ownership": [],
        "capture_progress": [],
        "projectiles": [],
        "structure_destruction": [],
        "scenery_debris": [],
    }


def _start_active_match(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create+join+ready both slots to ACTIVE; return (created, joined) results."""
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created = _create(ws_a, "alice")
        joined = _join(ws_b, created["joinCode"], "bob")
        ws_a.receive_json()
        ws_b.receive_json()

        _ready(
            ws_a,
            match_id=created["matchId"],
            player_id="p1",
            session_token=created["sessionToken"],
        )
        ws_a.receive_json()
        ws_b.receive_json()

        _ready(
            ws_b,
            match_id=created["matchId"],
            player_id="p2",
            session_token=joined["sessionToken"],
        )
        ws_a.receive_json()  # ready_state from p2 readying up
        ws_b.receive_json()
        ws_a.receive_json()  # started
        ws_b.receive_json()
        ws_a.receive_json()  # initial snapshot
        ws_b.receive_json()

    return created, joined


def test_reconnect_snapshot_reconstructs_the_current_authoritative_state(
    no_tick_client: TestClient,
) -> None:
    """A reconnecting client's snapshot must match `to_snapshot(match.game_state)`
    exactly (the real acceptance criterion: full reconstructability), not the
    old empty placeholder.

    Uses `no_tick_client` (no live `MatchRuntime`) so `match.game_state` is a
    fixed, known value -- this test is about the *mapping* being exact, not
    about racing a real 20 Hz tick loop, which `test_no_cross_match..."/
    the manager-level runtime tests already cover independently.
    """
    client = no_tick_client
    manager = _match_manager(client)
    created, _joined = _start_active_match(client)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        resync = ws.receive_json()

    match = manager.get_match(created["matchId"])
    assert match.game_state is not None
    assert resync["snapshot"]["tick"] == match.game_state.tick
    assert resync["snapshot"]["state"] == to_snapshot(match.game_state)


def test_reconnect_never_advances_or_mutates_engine_state(no_tick_client: TestClient) -> None:
    """Sending a reconnect must never call `engine.step`/advance the tick --
    the snapshot is read exactly as `match.game_state` stands."""
    client = no_tick_client
    manager = _match_manager(client)
    created, _joined = _start_active_match(client)

    match_before = manager.get_match(created["matchId"])
    assert match_before.game_state is not None
    tick_before = match_before.game_state.tick
    state_before = to_snapshot(match_before.game_state)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        resync = ws.receive_json()

    assert resync["snapshot"]["tick"] == tick_before
    assert resync["snapshot"]["state"] == state_before


def test_reconnect_snapshot_round_trips_through_protocol_validation(
    no_tick_client: TestClient,
) -> None:
    from app.protocol.envelope import OutboundMessageAdapter
    from app.protocol.reconnect import ServerResync

    client = no_tick_client
    created, _joined = _start_active_match(client)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        raw = ws.receive_text()

    revalidated = OutboundMessageAdapter.validate_json(raw)
    assert isinstance(revalidated, ServerResync)
    assert revalidated.snapshot.type == "snapshot"


def test_stale_connections_teardown_after_reconnect_does_not_fire_spurious_disconnect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the reconnect-vs-teardown race (code review finding).

    Reproduces: a player reconnects on a *new* socket, rebinding the same
    session before the *old* socket's own teardown has run (e.g. a
    slow/delayed TCP close arriving after the reconnect already succeeded).
    The old socket's eventual teardown must be a silent no-op for
    disconnect-notification purposes -- the reconnected player is still
    live -- and only the socket that is genuinely current at teardown time
    may trigger ``MatchManager.mark_disconnected``.

    Ordering is pinned deterministically (not via real delayed I/O) by using
    nested ``with`` blocks: ``fresh_ws`` is opened *first* as the outer
    connection (so it stays open across the whole test) but does not act
    until later; ``stale_ws`` is opened and creates the match inside the
    nested block; ``fresh_ws`` then reconnects (taking over the
    ``ConnectionRegistry`` slot) while ``stale_ws`` is still open; the
    nested block then exits, closing ``stale_ws`` -- whose teardown, per
    ``WebSocketTestSession.__exit__``, is fully awaited before the ``with``
    statement returns -- while ``fresh_ws`` remains registered as current.
    Only afterwards does the outer block close ``fresh_ws``.
    """
    manager = _match_manager(client)
    calls: list[str] = []
    original = manager.mark_disconnected

    def counting(session_token: str) -> None:
        calls.append(session_token)
        original(session_token)

    monkeypatch.setattr(manager, "mark_disconnected", counting)

    with client.websocket_connect("/ws") as fresh_ws:
        with client.websocket_connect("/ws") as stale_ws:
            created = _create(stale_ws)

            fresh_ws.send_text(
                json.dumps(
                    {
                        "protocolVersion": 1,
                        "type": "reconnect",
                        "matchId": created["matchId"],
                        "playerId": "p1",
                        "sessionToken": created["sessionToken"],
                    }
                )
            )
            resync = fresh_ws.receive_json()
            assert resync["type"] == "resync"
            # `fresh_ws` has now taken over the (match, player) slot in the
            # ConnectionRegistry while `stale_ws` is still open.

        # `stale_ws` has just closed (and its teardown fully completed) while
        # it was no longer the current connection for this player -- it must
        # NOT have fired a disconnect notification.
        assert calls == []

    # `fresh_ws` now closes: it *was* the current connection, so exactly one
    # notification fires here, and only here.
    assert calls == [created["sessionToken"]]


def test_connection_registry_unregister_reports_whether_it_was_current() -> None:
    """Focused unit test for the return-value contract the race fix relies on."""
    from app.transport.connections import ConnectionRegistry

    class _FakeWebSocket:
        """Minimal stand-in; ``ConnectionRegistry`` never calls any method on it."""

    registry = ConnectionRegistry()
    old_socket = _FakeWebSocket()
    new_socket = _FakeWebSocket()

    registry.register("m1", "p1", old_socket)  # type: ignore[arg-type]

    # A newer connection (simulating a reconnect) takes over the slot.
    registry.register("m1", "p1", new_socket)  # type: ignore[arg-type]

    # The old socket's own (now-stale) teardown must report it was NOT the
    # one removed, and must not evict the newer connection.
    assert registry.unregister("m1", "p1", old_socket) is False  # type: ignore[arg-type]
    assert registry.connection_for_player("m1", "p1") is new_socket  # type: ignore[comparison-overlap]

    # The new socket's own teardown is the one that actually wins.
    assert registry.unregister("m1", "p1", new_socket) is True  # type: ignore[arg-type]
    assert registry.connection_for_player("m1", "p1") is None

    # Idempotent: a second unregister of an already-removed socket is a
    # harmless no-op that reports False.
    assert registry.unregister("m1", "p1", new_socket) is False  # type: ignore[arg-type]


# -- disconnect/reconnect pause, grace, forfeit (M7 Task 7, issue #96) -------
#
# `_start_active_match_keeping_sockets_open` lives in `tests/transport/_helpers.py`
# (shared with `test_milestone_integration.py`, M7 Task 10 review, Important I4).


def test_disconnect_pauses_match_and_broadcasts_paused_to_the_remaining_player(
    short_grace_client: TestClient,
) -> None:
    client = short_grace_client
    manager = _match_manager(client)
    with client.websocket_connect("/ws") as ws_b:
        with client.websocket_connect("/ws") as ws_a:
            created, _joined, _snapshot = _start_active_match_keeping_sockets_open(ws_a, ws_b)
            # ws_a closes here (end of its own, inner `with` block) -- an
            # abrupt disconnect for p1. `ws_b`'s own `with` is still open,
            # so it stays connected to observe the broadcast.

        paused = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert paused["type"] == "paused"
        assert paused["matchId"] == created["matchId"]
        assert paused["disconnectedPlayerId"] == "p1"
        assert isinstance(paused["graceDeadlineMs"], int)

        match = manager.get_match(created["matchId"])
        assert match.state is MatchRuntimeState.PAUSED_DISCONNECTED


def test_reconnect_within_grace_broadcasts_resumed_once_both_players_are_back(
    short_grace_client: TestClient,
) -> None:
    client = short_grace_client
    manager = _match_manager(client)
    with client.websocket_connect("/ws") as ws_b:
        with client.websocket_connect("/ws") as ws_a:
            created, _joined, _snapshot = _start_active_match_keeping_sockets_open(ws_a, ws_b)

        paused = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert paused["type"] == "paused"

        with client.websocket_connect("/ws") as ws_a_again:
            ws_a_again.send_text(
                json.dumps(
                    {
                        "protocolVersion": 1,
                        "type": "reconnect",
                        "matchId": created["matchId"],
                        "playerId": "p1",
                        "sessionToken": created["sessionToken"],
                    }
                )
            )
            resync = ws_a_again.receive_json()
            assert resync["type"] == "resync"

            resumed = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
            assert resumed["type"] == "resumed"
            assert resumed["matchId"] == created["matchId"]

            # Assert state *before* `ws_a_again` closes below -- its own
            # closure is itself a fresh disconnect that would re-pause the
            # match, which is correct behavior but not what this assertion
            # is about.
            match = manager.get_match(created["matchId"])
            assert match.state is MatchRuntimeState.ACTIVE


def test_grace_expiry_broadcasts_forfeit_to_the_remaining_player(
    short_grace_client: TestClient,
) -> None:
    """`short_grace_client` is wired with a 50ms grace period -- well short of
    a real 60s wait -- so this test observes a real grace-deadline expiry
    end to end without any sleeps beyond what `receive_json()` itself blocks
    for waiting on the forfeit broadcast."""
    client = short_grace_client
    manager = _match_manager(client)
    with client.websocket_connect("/ws") as ws_b:
        with client.websocket_connect("/ws") as ws_a:
            created, _joined, _snapshot = _start_active_match_keeping_sockets_open(ws_a, ws_b)
            # ws_a closes here: p1 disconnects and never returns.

        paused = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert paused["type"] == "paused"

        forfeit = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert forfeit["type"] == "forfeit"
        assert forfeit["matchId"] == created["matchId"]
        assert forfeit["forfeitingPlayerId"] == "p1"
        assert forfeit["winnerPlayerId"] == "p2"
        assert forfeit["reason"] == "disconnect_timeout"

        match = manager.get_match(created["matchId"])
        assert match.state is MatchRuntimeState.FINISHED


def test_reconnect_to_an_already_forfeited_match_replays_the_durable_result(
    no_tick_client: TestClient,
) -> None:
    """M7 Task 7 review, Important I2: the *winning* side of a both-
    disconnected forfeit was, by construction, not connected to receive the
    live `ServerForfeit` broadcast (it was disconnected too, just with a
    later deadline). `Match.result` persists the outcome durably so a
    reconnect to an already-decided match still delivers it, using the
    existing `ServerForfeit` message type (no protocol/schema change).

    Sets up the finished/forfeited state directly (rather than racing real
    grace timers) to test exactly the `ClientReconnect` wiring this finding
    is about, deterministically.
    """
    client = no_tick_client
    manager = _match_manager(client)
    created, joined = _start_active_match(client)

    match = manager.get_match(created["matchId"])
    match.state = MatchRuntimeState.FINISHED
    match.result = MatchResult(
        outcome=MatchOutcome.FORFEIT,
        reason="disconnect_timeout",
        winner_player_id=PLAYER_TWO,
        forfeiting_player_id=PLAYER_ONE,
    )

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p2",
                    "sessionToken": joined["sessionToken"],
                }
            )
        )
        resync = ws.receive_json()
        assert resync["type"] == "resync"

        forfeit = ws.receive_json()
        assert forfeit["type"] == "forfeit"
        assert forfeit["matchId"] == created["matchId"]
        assert forfeit["forfeitingPlayerId"] == "p1"
        assert forfeit["winnerPlayerId"] == "p2"
        assert forfeit["reason"] == "disconnect_timeout"


def test_reconnect_to_an_already_no_contested_match_replays_the_durable_result(
    no_tick_client: TestClient,
) -> None:
    """Same as above, for the no-contest outcome."""
    client = no_tick_client
    manager = _match_manager(client)
    created, _joined = _start_active_match(client)

    match = manager.get_match(created["matchId"])
    match.state = MatchRuntimeState.FINISHED
    match.result = MatchResult(outcome=MatchOutcome.NO_CONTEST, reason="disconnect_timeout_both")

    with client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "reconnect",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                }
            )
        )
        resync = ws.receive_json()
        assert resync["type"] == "resync"

        no_contest = ws.receive_json()
        assert no_contest["type"] == "no_contest"
        assert no_contest["matchId"] == created["matchId"]
        assert no_contest["reason"] == "disconnect_timeout_both"


def test_resync_is_sent_before_any_resumed_broadcast_it_triggers(
    short_grace_client: TestClient,
) -> None:
    """M7 Task 7 review, Minor M1: the reconnecting player's own `resync`
    must be sent before `mark_reconnected` can trigger a `resumed`
    broadcast, so the sequencing is structural, not incidental.

    Only p1 disconnects, so p1 reconnecting alone completes the resume
    (p2/`ws_b` never left) -- the resulting `resumed` broadcast reaches
    every connection for the match, *including* the reconnecting player's
    own socket (already re-registered before the resync send). This makes
    the ordering directly observable on that single socket: resync must
    arrive strictly before resumed on it, never the other way around.
    """
    client = short_grace_client
    manager = _match_manager(client)
    with client.websocket_connect("/ws") as ws_b:
        with client.websocket_connect("/ws") as ws_a:
            created, _joined, _snapshot = _start_active_match_keeping_sockets_open(ws_a, ws_b)
        # ws_a (p1) closed: paused. p2/ws_b never disconnects in this test.

        paused = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert paused["type"] == "paused"

        with client.websocket_connect("/ws") as ws_a_again:
            ws_a_again.send_text(
                json.dumps(
                    {
                        "protocolVersion": 1,
                        "type": "reconnect",
                        "matchId": created["matchId"],
                        "playerId": "p1",
                        "sessionToken": created["sessionToken"],
                    }
                )
            )
            # The resync is sent while the match is still paused (no ticks
            # possible yet), so it is necessarily the very first frame; the
            # `resumed` that follows can be preceded by a real tick's snapshot.
            first_message = ws_a_again.receive_json()
            second_message = _next_non_snapshot(ws_a_again, own_match_id=created["matchId"])
            assert first_message["type"] == "resync"
            assert second_message["type"] == "resumed"

            # Assert *before* `ws_a_again` closes below -- its own closure
            # is itself a fresh disconnect that would re-pause the match.
            match = manager.get_match(created["matchId"])
            assert match.state is MatchRuntimeState.ACTIVE


def test_opponents_token_cannot_issue_commands_from_another_players_socket(
    client: TestClient,
) -> None:
    """§9.8: a socket bound as P2 presenting P1's valid token is a session mismatch, not P1."""
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created, _joined, _snapshot = _start_active_match_keeping_sockets_open(ws_a, ws_b)
        ws_b.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "command",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                    "clientSequence": 0,
                    "payload": {"kind": "commander_move", "dx": 1, "dy": 0},
                }
            )
        )
        message = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert message["type"] == "error"
        assert message["error"]["code"] == "session_mismatch"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            for _ in range(1000):
                ws_b.receive_json()
        assert exc_info.value.code == 1008
