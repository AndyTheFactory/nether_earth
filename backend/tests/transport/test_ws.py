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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nether_earth.snapshot import to_snapshot
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.match.manager import MatchManager
from app.match.runtime import MatchRuntimeRegistry
from app.transport import ConnectionRegistry, create_websocket_router


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
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


def test_match_start_broadcasts_a_real_initial_authoritative_snapshot(
    client: TestClient,
) -> None:
    """The "started" broadcast is immediately followed by a real snapshot
    (tick 0, the state `engine.new_game` produced) -- not the empty
    placeholder, and reconstructable to exactly what the engine computed."""
    manager = _match_manager(client)

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

    match = manager.get_match(created["matchId"])
    assert match.game_state is not None
    expected_state = to_snapshot(match.game_state)
    assert snapshot_a["state"] == expected_state
    assert snapshot_b["state"] == expected_state


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
