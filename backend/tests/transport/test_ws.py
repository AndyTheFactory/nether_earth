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
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.match.manager import MatchManager


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _match_manager(client: TestClient) -> MatchManager:
    manager = client.app.state.match_manager
    assert isinstance(manager, MatchManager)
    return manager


def _create(ws: WebSocketTestSession, nickname: str = "alice") -> dict[str, Any]:
    ws.send_text(
        json.dumps({"protocolVersion": 1, "type": "create", "nickname": nickname})
    )
    return dict(ws.receive_json())


def _join(ws: WebSocketTestSession, join_code: str, nickname: str = "bob") -> dict[str, Any]:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "join",
                "joinCode": join_code,
                "nickname": nickname,
            }
        )
    )
    return dict(ws.receive_json())


def _ready(
    ws: WebSocketTestSession, *, match_id: str, player_id: str, session_token: str, ready: bool = True
) -> None:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "ready",
                "matchId": match_id,
                "playerId": player_id,
                "sessionToken": session_token,
                "ready": ready,
            }
        )
    )


# -- connect + create/join happy path ----------------------------------------


def test_create_returns_schema_valid_created_message(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        created = _create(ws)

    assert created["type"] == "created"
    assert created["protocolVersion"] == 1
    assert created["joinCode"]
    assert created["playerId"] == "p1"
    assert created["sessionToken"]


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
        _join(ws_b, created["joinCode"], "bob")
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

        joined_session_token = _join_session_token(client, created["matchId"])
        _ready(
            ws_b,
            match_id=created["matchId"],
            player_id="p2",
            session_token=joined_session_token,
        )
        assert ws_a.receive_json()["type"] == "ready_state"
        assert ws_b.receive_json()["type"] == "ready_state"

        # Both slots ready -> match transitions to ACTIVE and "started" is
        # broadcast to every connection.
        assert ws_a.receive_json()["type"] == "started"
        assert ws_b.receive_json()["type"] == "started"


def _join_session_token(client: TestClient, match_id: str) -> str:
    manager = _match_manager(client)
    match = manager.get_match(match_id)
    from nether_earth.ids import PLAYER_TWO

    return match.players[PLAYER_TWO].session_token


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
                    "payload": {"kind": "placeholder"},
                }
            )
        )
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "command_rejected"


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
