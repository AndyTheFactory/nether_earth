"""Disposal of finished and abandoned matches (M10.6, issue #127)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.match.manager import MatchManager
from app.match.models import InvalidSessionTokenError, MatchNotFoundError, MatchRuntimeState
from tests.transport._helpers import _create


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _manager(clock: _Clock) -> MatchManager:
    return MatchManager(finished_retention_s=300, waiting_timeout_s=900, clock=clock)


def test_finished_match_is_disposed_after_retention() -> None:
    clock = _Clock()
    manager = _manager(clock)
    created = manager.create_match("alice")
    manager.finish_match(created.match_id)

    clock.now += 299
    assert manager.sweep() == []
    manager.resolve_session(created.session_token)  # result still resolvable

    clock.now += 1
    disposed = manager.sweep()
    assert [m.match_id for m in disposed] == [created.match_id]
    assert disposed[0].state is MatchRuntimeState.FINISHED
    assert len(manager) == 0
    with pytest.raises(InvalidSessionTokenError):
        manager.resolve_session(created.session_token)


def test_abandoned_lobby_is_disposed_after_waiting_timeout() -> None:
    clock = _Clock()
    manager = _manager(clock)
    created = manager.create_match("alice")
    clock.now += 899
    assert manager.sweep() == []
    clock.now += 1
    assert [m.match_id for m in manager.sweep()] == [created.match_id]
    with pytest.raises(MatchNotFoundError):  # join code index cleared too
        manager.get_match_by_join_code(created.join_code)


def test_live_matches_are_never_swept() -> None:
    clock = _Clock()
    manager = _manager(clock)
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)
    assert manager.get_match(created.match_id).state is MatchRuntimeState.ACTIVE
    clock.now += 10_000
    assert manager.sweep() == []


def test_no_policy_keeps_matches() -> None:
    clock = _Clock()
    manager = MatchManager(clock=clock)
    manager.finish_match(manager.create_match("alice").match_id)
    clock.now += 10**9
    assert manager.sweep() == []


def test_expired_lobby_owner_is_told_and_disconnected(tmp_path: Path) -> None:
    app = create_app(settings=Settings(replay_dir=tmp_path, waiting_timeout_s=1))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        _create(ws)
        time.sleep(1.05)
        client.portal.call(app.state.sweep_expired_matches)  # type: ignore[union-attr]
        message = ws.receive_json()
        assert message["error"]["code"] == "match_expired"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
        assert client.get("/ready").json()["matches"] == 0


def test_abandoned_lobby_is_disposed_after_grace() -> None:
    """NE-01: capacity held by a lobby nobody is connected to is released after a short grace."""
    clock = _Clock()
    manager = MatchManager(waiting_timeout_s=900, abandoned_lobby_grace_s=30, clock=clock)
    created = manager.create_match("alice")
    manager.mark_lobby_abandoned(created.match_id)
    clock.now += 29
    assert manager.sweep() == []
    clock.now += 1
    assert [m.match_id for m in manager.sweep()] == [created.match_id]
    assert len(manager) == 0


def test_reoccupied_lobby_is_not_disposed() -> None:
    """A creator who refreshes the page inside the grace keeps the lobby and its join code."""
    clock = _Clock()
    manager = MatchManager(waiting_timeout_s=900, abandoned_lobby_grace_s=30, clock=clock)
    created = manager.create_match("alice")
    manager.mark_lobby_abandoned(created.match_id)
    clock.now += 10
    manager.mark_lobby_occupied(created.match_id)
    clock.now += 100
    assert manager.sweep() == []
    manager.get_match_by_join_code(created.join_code)  # still joinable


def test_abandon_mark_is_ignored_for_a_started_match() -> None:
    clock = _Clock()
    manager = MatchManager(abandoned_lobby_grace_s=1, clock=clock)
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)
    manager.mark_lobby_abandoned(created.match_id)  # ACTIVE: reconnect policy owns this, not sweep
    clock.now += 10_000
    assert manager.sweep() == []


def test_abandon_and_occupy_unknown_match_are_no_ops() -> None:
    manager = MatchManager(abandoned_lobby_grace_s=1)
    manager.mark_lobby_abandoned("missing")
    manager.mark_lobby_occupied("missing")


def test_capacity_is_released_when_lobby_creator_disconnects(tmp_path: Path) -> None:
    """The review's regression test: create at capacity, drop the socket, sweep, create again."""
    app = create_app(settings=Settings(replay_dir=tmp_path, max_matches=1, abandoned_lobby_grace_s=1))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert _create(ws)["type"] == "created"
        with client.websocket_connect("/ws") as ws:
            assert _create(ws)["error"]["code"] == "server_busy"  # still inside the grace window
        time.sleep(1.05)
        client.portal.call(app.state.sweep_expired_matches)  # type: ignore[union-attr]
        with client.websocket_connect("/ws") as ws:
            assert _create(ws)["type"] == "created"
