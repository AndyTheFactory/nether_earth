"""Unit tests for the per-message ``/ws`` handlers.

Each handler test runs against a ``Connection`` built on a fake WebSocket and
the real ``MatchManager`` / ``MatchRuntimeRegistry`` / ``ConnectionRegistry``,
with no TestClient and no network. The fail-safe (1011) test is the exception:
that path lives in the endpoint loop, so it drives the router via a TestClient.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.match.manager import MatchManager
from app.match.models import MatchOutcome, MatchResult, MatchRuntimeState
from app.match.runtime import MatchRuntimeRegistry
from app.protocol import parse_client_message, serialize_server_message
from app.protocol.common import PROTOCOL_VERSION
from app.protocol.reconnect import ServerResync
from app.transport import handlers
from app.transport import ws as ws_module
from app.transport.connection import Connection
from app.transport.connections import ConnectionRegistry
from app.transport.handlers import (
    Outcome,
    authenticate,
    dispatch,
    handle_command,
    handle_create,
    handle_join,
    handle_leave,
    handle_ready,
)
from app.transport.limits import MAX_FAILED_JOINS
from app.transport.snapshots import empty_snapshot_message


class FakeWebSocket:
    """Records sent frames and close codes; flips to DISCONNECTED on close."""

    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.sent: list[dict[str, Any]] = []
        self.close_codes: list[int] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)
        self.application_state = WebSocketState.DISCONNECTED


class Stores:
    def __init__(self, manager: MatchManager | None = None) -> None:
        self.manager = manager if manager is not None else MatchManager()
        self.runtime = MatchRuntimeRegistry()
        self.registry = ConnectionRegistry()

    def connect(self) -> tuple[Connection, FakeWebSocket]:
        ws = FakeWebSocket()
        conn = Connection(ws, self.manager, self.runtime, self.registry)  # type: ignore[arg-type]
        return conn, ws

    async def create(self, nickname: str = "alice") -> tuple[Connection, FakeWebSocket]:
        conn, ws = self.connect()
        assert await handle_create(conn, _msg(type="create", nickname=nickname)) is Outcome.CONTINUE
        return conn, ws

    async def join(self, join_code: str, nickname: str = "bob") -> tuple[Connection, FakeWebSocket]:
        conn, ws = self.connect()
        message = _msg(type="join", joinCode=join_code, nickname=nickname)
        assert await handle_join(conn, message) is Outcome.CONTINUE
        return conn, ws


def _msg(**fields: Any) -> Any:
    return parse_client_message(json.dumps({"protocolVersion": PROTOCOL_VERSION, **fields}))


def _auth_msg(type_: str, conn: Connection, **fields: Any) -> Any:
    assert conn.bound is not None
    return _msg(
        type=type_,
        matchId=conn.bound.match_id,
        playerId=conn.bound.player_id,
        sessionToken=conn.bound.session_token,
        **fields,
    )


def _error_code(frame: dict[str, Any]) -> str:
    assert frame["type"] == "error"
    code: str = frame["error"]["code"]
    return code


# -- handle_create ------------------------------------------------------------


async def test_create_pvp_binds_registers_and_sends_created_without_opponent() -> None:
    stores = Stores()
    conn, ws = await stores.create()

    assert conn.bound is not None
    assert stores.registry.connection_for_player(conn.bound.match_id, "p1") is ws
    assert ws.sent == [
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": "created",
            "matchId": conn.bound.match_id,
            "joinCode": ws.sent[0]["joinCode"],
            "playerId": "p1",
            "sessionToken": conn.bound.session_token,
        }
    ]
    assert ws.sent[0]["joinCode"]
    assert ws.close_codes == []


async def test_create_with_invalid_nickname_keeps_socket_open_and_unbound() -> None:
    stores = Stores()
    conn, ws = stores.connect()

    outcome = await handle_create(conn, _msg(type="create", nickname="   "))

    assert outcome is Outcome.CONTINUE
    assert conn.bound is None
    assert _error_code(ws.sent[0]) == "invalid_nickname"
    assert "matchId" not in ws.sent[0]  # None is dropped from the wire
    assert ws.close_codes == []


async def test_create_when_server_is_full_reports_server_busy() -> None:
    stores = Stores(MatchManager(max_matches=0))
    conn, ws = stores.connect()

    outcome = await handle_create(conn, _msg(type="create", nickname="alice"))

    assert outcome is Outcome.CONTINUE
    assert conn.bound is None
    assert _error_code(ws.sent[0]) == "server_busy"
    assert ws.close_codes == []


# -- handle_join --------------------------------------------------------------


async def test_join_unknown_code_counts_a_failed_join() -> None:
    stores = Stores()
    conn, ws = stores.connect()

    outcome = await handle_join(conn, _msg(type="join", joinCode="ZZZZZZ", nickname="bob"))

    assert outcome is Outcome.CONTINUE
    assert conn.failed_joins == 1
    assert _error_code(ws.sent[0]) == "match_not_found"
    assert ws.close_codes == []


async def test_fifth_failed_join_closes_with_too_many_join_attempts() -> None:
    stores = Stores()
    conn, ws = stores.connect()
    message = _msg(type="join", joinCode="ZZZZZZ", nickname="bob")

    outcomes = [await handle_join(conn, message) for _ in range(MAX_FAILED_JOINS)]

    assert outcomes == [Outcome.CONTINUE] * (MAX_FAILED_JOINS - 1) + [Outcome.CLOSED]
    assert [_error_code(f) for f in ws.sent] == ["match_not_found"] * (MAX_FAILED_JOINS - 1) + [
        "too_many_join_attempts"
    ]
    assert ws.close_codes == [1008]


async def test_join_success_sends_joined_and_broadcasts_ready_state_to_creator() -> None:
    stores = Stores()
    _, creator_ws = await stores.create()
    join_code = creator_ws.sent[0]["joinCode"]

    joiner, joiner_ws = await stores.join(join_code)

    assert joiner.bound is not None
    assert [f["type"] for f in joiner_ws.sent] == ["joined", "ready_state"]
    assert creator_ws.sent[-1]["type"] == "ready_state"
    assert [p["nickname"] for p in creator_ws.sent[-1]["players"]] == ["alice", "bob"]


# -- authenticate -------------------------------------------------------------


async def test_authenticate_bad_token_rejects_with_invalid_session() -> None:
    stores = Stores()
    conn, ws = stores.connect()
    message = _msg(type="ready", matchId="m", playerId="p1", sessionToken="bogus", ready=True)

    assert await authenticate(conn, message) is None
    assert _error_code(ws.sent[0]) == "invalid_session"
    assert ws.close_codes == [1008]


async def test_authenticate_wrong_player_id_rejects_with_resolved_match_id() -> None:
    stores = Stores()
    creator, _ = await stores.create()
    assert creator.bound is not None
    conn, ws = stores.connect()
    message = _msg(
        type="ready",
        matchId=creator.bound.match_id,
        playerId="p2",
        sessionToken=creator.bound.session_token,
        ready=True,
    )

    assert await authenticate(conn, message) is None
    assert _error_code(ws.sent[0]) == "session_mismatch"
    assert ws.sent[0]["matchId"] == creator.bound.match_id
    assert ws.close_codes == [1008]


async def test_first_authentication_binds_and_registers() -> None:
    stores = Stores()
    creator, creator_ws = await stores.create()
    assert creator.bound is not None
    conn, ws = stores.connect()

    result = await authenticate(conn, _auth_msg("ready", creator, ready=True))

    assert result is not None
    match, player = result
    assert match.match_id == creator.bound.match_id
    assert player == PLAYER_ONE
    assert conn.bound == creator.bound
    assert stores.registry.connection_for_player(match.match_id, "p1") is ws
    assert creator_ws.close_codes == [4000]


async def test_second_token_on_bound_connection_rejects_with_session_mismatch() -> None:
    stores = Stores()
    conn, ws = await stores.create("alice")
    other, _ = await stores.create("carol")
    assert conn.bound is not None

    assert await authenticate(conn, _auth_msg("ready", other, ready=True)) is None
    assert _error_code(ws.sent[-1]) == "session_mismatch"
    assert ws.sent[-1]["matchId"] == conn.bound.match_id
    assert ws.close_codes == [1008]


# -- handle_ready -------------------------------------------------------------


async def test_ready_announces_start_even_when_the_broadcast_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores = Stores()
    alice, alice_ws = await stores.create("alice")
    bob, _ = await stores.join(alice_ws.sent[0]["joinCode"])
    assert alice.bound is not None
    stores.manager.set_ready(alice.bound.session_token, ready=True)
    announced: list[str] = []
    monkeypatch.setattr(stores.runtime, "announce_started", announced.append)

    async def failing_broadcast(*_: Any) -> None:
        raise ConnectionError("peer went away mid-broadcast")

    monkeypatch.setattr(handlers, "broadcast", failing_broadcast)
    message = _auth_msg("ready", bob, ready=True)
    match, player = stores.manager.resolve_session(message.session_token)

    with pytest.raises(ConnectionError):
        await handle_ready(bob, message, match, player)

    assert match.state is MatchRuntimeState.ACTIVE
    assert announced == [match.match_id]


# -- handle_command -----------------------------------------------------------


async def test_command_with_diagonal_payload_reports_invalid_command_payload() -> None:
    stores = Stores()
    conn, ws = await stores.create()
    message = _auth_msg(
        "command", conn, clientSequence=0, payload={"kind": "commander_move", "dx": 1, "dy": 1}
    )
    match, player = stores.manager.resolve_session(message.session_token)

    assert await handle_command(conn, message, match, player) is Outcome.CONTINUE
    assert _error_code(ws.sent[-1]) == "invalid_command_payload"
    assert ws.sent[-1]["matchId"] == match.match_id
    assert ws.close_codes == []


async def test_command_for_match_without_runtime_reports_command_rejected() -> None:
    stores = Stores()
    conn, ws = await stores.create()
    message = _auth_msg("command", conn, clientSequence=0, payload={"kind": "cancel_construction"})
    match, player = stores.manager.resolve_session(message.session_token)

    assert await handle_command(conn, message, match, player) is Outcome.CONTINUE
    assert _error_code(ws.sent[-1]) == "command_rejected"
    assert ws.close_codes == []


# -- handle_leave -------------------------------------------------------------


async def test_leave_tears_down_closes_1000_and_notifies_disconnect_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores = Stores()
    conn, ws = await stores.create()
    assert conn.bound is not None
    disconnected: list[str] = []
    monkeypatch.setattr(stores.manager, "mark_disconnected", disconnected.append)
    message = _auth_msg("leave", conn)
    match, player = stores.manager.resolve_session(message.session_token)

    assert await handle_leave(conn, message, match, player) is Outcome.CLOSED
    await conn.teardown()  # the endpoint's `finally` runs it again

    assert ws.close_codes == [1000]
    assert stores.registry.connections_for(match.match_id) == ()
    assert disconnected == [conn.bound.session_token]


# -- handle_reconnect ---------------------------------------------------------


async def test_reconnect_on_waiting_match_resyncs_with_empty_snapshot() -> None:
    stores = Stores()
    creator, _ = await stores.create()
    assert creator.bound is not None
    conn, ws = stores.connect()

    assert await dispatch(conn, _auth_msg("reconnect", creator)) is Outcome.CONTINUE

    match_id = creator.bound.match_id
    expected = ServerResync(
        protocol_version=PROTOCOL_VERSION,
        type="resync",
        match_id=match_id,
        player_id="p1",
        snapshot=empty_snapshot_message(match_id),
    )
    assert ws.sent == [json.loads(serialize_server_message(expected))]


async def test_reconnect_to_forfeited_match_sends_forfeit_after_resync() -> None:
    stores = Stores()
    alice, alice_ws = await stores.create("alice")
    bob, _ = await stores.join(alice_ws.sent[0]["joinCode"])
    assert alice.bound is not None and bob.bound is not None
    stores.manager.set_ready(alice.bound.session_token, ready=True)
    stores.manager.set_ready(bob.bound.session_token, ready=True)
    match = stores.manager.get_match(alice.bound.match_id)
    assert match.state is MatchRuntimeState.ACTIVE
    match.result = MatchResult(
        outcome=MatchOutcome.FORFEIT,
        reason="disconnect_timeout",
        winner_player_id=PLAYER_ONE,
        forfeiting_player_id=PLAYER_TWO,
    )
    conn, ws = stores.connect()

    assert await dispatch(conn, _auth_msg("reconnect", alice)) is Outcome.CONTINUE

    assert [f["type"] for f in ws.sent] == ["resync", "forfeit"]
    assert ws.sent[1]["winnerPlayerId"] == "p1"
    assert ws.sent[1]["forfeitingPlayerId"] == "p2"


# -- Connection.attach / teardown ---------------------------------------------


async def test_attach_closes_predecessor_with_4000_and_marks_lobby_occupied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores = Stores()
    first, first_ws = await stores.create()
    assert first.bound is not None
    occupied: list[str] = []
    monkeypatch.setattr(stores.manager, "mark_lobby_occupied", occupied.append)
    second, second_ws = stores.connect()
    second.bind(first.bound.match_id, first.bound.player_id, first.bound.session_token)

    await second.attach()

    assert first_ws.close_codes == [4000]
    assert second_ws.close_codes == []
    assert occupied == [first.bound.match_id]
    assert stores.registry.connection_for_player(first.bound.match_id, "p1") is second_ws


async def test_teardown_is_idempotent_and_abandons_lobby_only_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores = Stores()
    alice, alice_ws = await stores.create("alice")
    bob, _ = await stores.join(alice_ws.sent[0]["joinCode"])
    assert alice.bound is not None
    abandoned: list[str] = []
    disconnected: list[str] = []
    monkeypatch.setattr(stores.manager, "mark_lobby_abandoned", abandoned.append)
    monkeypatch.setattr(stores.manager, "mark_disconnected", disconnected.append)

    await alice.teardown()
    await alice.teardown()
    assert abandoned == []
    assert len(disconnected) == 1

    await bob.teardown()
    await bob.teardown()
    assert abandoned == [alice.bound.match_id]
    assert len(disconnected) == 2


# -- endpoint fail-safe -------------------------------------------------------


def test_unexpected_handler_error_logs_match_id_and_closes_1011(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stores = Stores()
    app = FastAPI()
    app.include_router(
        ws_module.create_websocket_router(stores.manager, stores.runtime, stores.registry)
    )
    real_dispatch = ws_module.dispatch

    async def dispatch_then_fail(conn: Connection, message: Any) -> Outcome:
        if conn.bound is not None:
            raise RuntimeError("boom")
        return await real_dispatch(conn, message)

    monkeypatch.setattr(ws_module, "dispatch", dispatch_then_fail)
    caplog.set_level(logging.ERROR, logger="app.transport.ws")

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"protocolVersion": 1, "type": "create", "nickname": "alice"}))
        created = ws.receive_json()
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
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1011

    failures = [r for r in caplog.records if getattr(r, "event", None) == "ws_handler_failed"]
    assert len(failures) == 1
    assert getattr(failures[0], "match_id", None) == created["matchId"]
    assert created["sessionToken"] not in failures[0].getMessage()
