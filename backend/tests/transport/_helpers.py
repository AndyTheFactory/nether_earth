"""Shared WebSocket test helpers for ``tests/transport`` (M7 Task 10 review, Important I4).

Extracted from ``test_ws.py`` (M7 Task 5, issue #94) so ``test_ws.py`` and
``test_milestone_integration.py`` (M7 Task 10, issue #99) share exactly one
definition of the create/join/ready/start protocol dance rather than two
near-verbatim, drift-prone copies. Both modules import from here rather than
defining their own.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.match.manager import MatchManager
from app.replay import ReplayWriter


def _match_manager(client: TestClient) -> MatchManager:
    manager = client.app.state.match_manager
    assert isinstance(manager, MatchManager)
    return manager


def _replay_writer(client: TestClient) -> ReplayWriter:
    writer = client.app.state.replay_writer
    assert isinstance(writer, ReplayWriter)
    return writer


def _drain_replays(client: TestClient) -> None:
    """Wait for the app's queued replay writes (NE-02) before reading artifact files."""
    _replay_writer(client).drain()


def _create(
    ws: WebSocketTestSession, nickname: str = "alice", *, opponent: str | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"protocolVersion": 1, "type": "create", "nickname": nickname}
    if opponent is not None:
        message["opponent"] = opponent
    ws.send_text(json.dumps(message))
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


def _start_active_match_keeping_sockets_open(
    ws_a: WebSocketTestSession,
    ws_b: WebSocketTestSession,
    *,
    nickname_a: str = "alice",
    nickname_b: str = "bob",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create+join+ready both slots to ACTIVE, returning ``(created, joined, snapshot)``.

    Leaves both sockets open (unlike a ``with``-scoped helper) so callers
    can keep driving the match -- disconnecting one side deliberately while
    observing broadcasts on the other. ``snapshot`` is the tick-0
    authoritative start snapshot both sides received identically (asserted
    below), returned so callers that want to assert on it (e.g. its
    ``tick``) don't have to re-derive it.
    """
    created = _create(ws_a, nickname_a)
    joined = _join(ws_b, created["joinCode"], nickname_b)
    ws_a.receive_json()  # ready_state (join broadcast)
    ws_b.receive_json()  # ready_state (own echo)

    _ready(ws_a, match_id=created["matchId"], player_id="p1", session_token=created["sessionToken"])
    ws_a.receive_json()
    ws_b.receive_json()

    _ready(ws_b, match_id=created["matchId"], player_id="p2", session_token=joined["sessionToken"])
    ws_a.receive_json()  # ready_state from p2 readying up
    ws_b.receive_json()
    assert ws_a.receive_json()["type"] == "started"
    assert ws_b.receive_json()["type"] == "started"
    snapshot_a = ws_a.receive_json()
    snapshot_b = ws_b.receive_json()
    assert snapshot_a["type"] == "snapshot"
    assert snapshot_a["tick"] == 0
    assert snapshot_b == snapshot_a

    return created, joined, snapshot_a


def _next_non_snapshot(ws: WebSocketTestSession, *, own_match_id: str) -> dict[str, Any]:
    """Return the next message on ``ws`` that is not one of its own match's tick broadcasts.

    The real ``MatchRuntime`` wired by ``create_app`` can legitimately
    interleave a fresh ``snapshot`` for ``own_match_id`` between any two
    lifecycle messages a test explicitly waits for. That is correct ticking,
    not something to structurally suppress, so callers needing a *specific*
    non-snapshot message skip past it here -- while still asserting every
    skipped snapshot belongs to their own match (cross-match isolation).
    """
    for _ in range(1000):
        message = ws.receive_json()
        if message["type"] != "snapshot":
            return dict(message)
        assert message["matchId"] == own_match_id, message
    raise AssertionError("only snapshot messages ever arrived")
