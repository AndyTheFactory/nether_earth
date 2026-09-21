"""M9.5 / M9.6 -- pause freezes in-flight dynamic state; reconnect resync converges (issues #117, #118).

Runs the real app (``create_app``: 20 Hz loop replaced by a fast one, short
grace) over the WebSocket protocol, exactly like a browser would:

1. Player 1 lands on the pad, builds and launches a robot, and orders it to
   advance, so a robot move transition and an active order are in progress.
2. Player 2 disconnects mid-move: the match pauses, and the engine tick *and*
   the in-flight movement transition stay frozen across a wall-clock wait.
3. Player 2 reconnects within grace: the resync snapshot is byte-identical to
   the server's canonical serialization of ``match.game_state``; after
   ``resumed`` the ticks continue from that snapshot and the robot completes
   its move -- no browser-side gameplay state was needed to resume.

M7's own integration scenario already covers forfeit/no-contest/replay with
pause metadata on the same real world; this test adds the "dynamic state in
progress" composition M9.5 asks for.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.main import create_app
from app.match.models import MatchRuntimeState
from app.protocol import serialize_server_message
from app.transport.snapshots import build_snapshot_message
from tests.transport._helpers import (
    _match_manager,
    _next_non_snapshot,
    _start_active_match_keeping_sockets_open,
)

_TICK_RATE_HZ = 100.0
_GRACE_SECONDS = 2.0


def _command(ws: WebSocketTestSession, session: dict[str, Any], sequence: int, payload: dict[str, Any]) -> None:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "command",
                "matchId": session["matchId"],
                "playerId": session["playerId"],
                "sessionToken": session["sessionToken"],
                "clientSequence": sequence,
                "payload": payload,
            }
        )
    )


def _wait_snapshot(ws: WebSocketTestSession, predicate: Any, *, limit: int = 4000) -> dict[str, Any]:
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == "snapshot" and predicate(message["state"]):
            return dict(message)
        assert message["type"] in ("snapshot", "ready_state"), message
    raise AssertionError("condition never observed in snapshots")


def _commander(state: dict[str, Any], player: str) -> dict[str, Any]:
    return next(c for c in state["commanders"] if c["player_id"] == player)


def _reconnect(ws: WebSocketTestSession, session: dict[str, Any]) -> None:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "reconnect",
                "matchId": session["matchId"],
                "playerId": session["playerId"],
                "sessionToken": session["sessionToken"],
            }
        )
    )


def _server_snapshot(manager: Any, match_id: str) -> dict[str, Any]:
    match = manager.get_match(match_id)
    assert match.game_state is not None
    return dict(json.loads(serialize_server_message(build_snapshot_message(match_id, match.game_state))))


def test_pause_freezes_in_flight_robot_move_and_resync_converges(tmp_path: Path) -> None:
    app = create_app(
        replay_dir=tmp_path / "replays", reconnect_grace_seconds=_GRACE_SECONDS, tick_rate_hz=_TICK_RATE_HZ
    )
    with TestClient(app) as client:
        manager = _match_manager(client)
        with client.websocket_connect("/ws") as ws_a:
            with client.websocket_connect("/ws") as ws_b_first:
                created, joined, _ = _start_active_match_keeping_sockets_open(ws_a, ws_b_first)
                match_id = created["matchId"]
                session_a = {"matchId": match_id, "playerId": "p1", "sessionToken": created["sessionToken"]}
                session_b = {"matchId": match_id, "playerId": "p2", "sessionToken": joined["sessionToken"]}

                # -- 1. Player 1: fly onto the roof-top pad (open-questions §18),
                #       build, launch, order an advance.
                sequence = 0
                _command(ws_a, session_a, sequence, {"kind": "commander_set_vertical_intent", "rising": True})
                sequence += 1
                _wait_snapshot(ws_a, lambda s: _commander(s, "p1")["altitude"] >= 16)
                for dx, dy in [(1, 0)] * 5 + [(0, -1)] * 5:
                    _command(ws_a, session_a, sequence, {"kind": "commander_move", "dx": dx, "dy": dy})
                    sequence += 1
                    _wait_snapshot(ws_a, lambda s: _commander(s, "p1").get("horizontal_transition") is None and s["tick"] > 0)
                    _wait_snapshot(ws_a, lambda s: _commander(s, "p1").get("horizontal_transition") is None)
                _command(ws_a, session_a, sequence, {"kind": "commander_set_vertical_intent", "rising": False})
                sequence += 1
                state = _wait_snapshot(ws_a, lambda s: (_commander(s, "p1")["x"], _commander(s, "p1")["y"]) == (22, 5))["state"]
                _wait_snapshot(ws_a, lambda s: any(c["player_id"] == "p1" for c in s["construction_sessions"]))
                # Electronics: the 2×2 robot starts in the base's doorway, whose
                # walls block the east step a non-electronic Advance would take
                # (CR002.3); electronic routing steps south out of it first.
                for module in ("bipod", "cannon", "electronics"):
                    _command(ws_a, session_a, sequence, {"kind": "select_module", "module": module})
                    sequence += 1
                _wait_snapshot(ws_a, lambda s: s["construction_sessions"][0]["build"]["electronics"] == "electronics")
                _command(ws_a, session_a, sequence, {"kind": "launch_robot"})
                sequence += 1
                state = _wait_snapshot(ws_a, lambda s: len(s["robots"]) == 1)["state"]
                robot_id = state["robots"][0]["entity_id"]
                _command(
                    ws_a,
                    session_a,
                    sequence,
                    {"kind": "set_robot_order", "entityId": robot_id, "order": {"kind": "advance", "distanceMiles": 5}},
                )
                sequence += 1
                moving = _wait_snapshot(ws_a, lambda s: s["robots"][0].get("movement") is not None)["state"]
                assert moving["robots"][0]["order"]["kind"] == "advance"

                # -- 2. Player 2 drops mid-move -> paused; tick and transition freeze.
            paused = _next_non_snapshot(ws_a, own_match_id=match_id)
            assert paused["type"] == "paused" and paused["disconnectedPlayerId"] == "p2"
            frozen = _server_snapshot(manager, match_id)
            assert frozen["state"]["robots"][0].get("movement") is not None
            assert client.portal is not None
            client.portal.call(asyncio.sleep, 20 / _TICK_RATE_HZ)
            assert _server_snapshot(manager, match_id) == frozen, "gameplay state changed while paused"
            assert manager.get_match(match_id).state is MatchRuntimeState.PAUSED_DISCONNECTED

            # -- 3. Reconnect within grace: resync == server truth; then convergence.
            with client.websocket_connect("/ws") as ws_b:
                _reconnect(ws_b, session_b)
                resync = ws_b.receive_json()
                assert resync["type"] == "resync" and resync["playerId"] == "p2"
                assert resync["snapshot"] == frozen
                assert resync["snapshot"]["state"]["robots"][0].get("movement") == moving["robots"][0].get("movement")
                assert _next_non_snapshot(ws_b, own_match_id=match_id)["type"] == "resumed"
                assert _next_non_snapshot(ws_a, own_match_id=match_id)["type"] == "resumed"

                last_tick = frozen["tick"]
                for _ in range(200):
                    message = ws_b.receive_json()
                    if message["type"] != "snapshot":
                        continue
                    assert message["tick"] == last_tick + 1, "ticks must continue exactly from the resync snapshot"
                    last_tick = message["tick"]
                    robot = message["state"]["robots"][0]
                    frozen_robot = frozen["state"]["robots"][0]
                    if (robot["x"], robot["y"]) != (frozen_robot["x"], frozen_robot["y"]):
                        break
                else:
                    raise AssertionError("robot move never completed after resume")
                # The frozen in-flight move completed to exactly its reserved destination.
                movement = frozen_robot["movement"]
                assert (robot["x"], robot["y"]) == (movement["to_x"], movement["to_y"])
                # Both players keep seeing one identical authoritative stream.
                a_view = _wait_snapshot(ws_a, lambda s: s["tick"] >= last_tick)
                b_view = _wait_snapshot(ws_b, lambda s: s["tick"] >= a_view["tick"])
                if a_view["tick"] == b_view["tick"]:
                    assert a_view == b_view
