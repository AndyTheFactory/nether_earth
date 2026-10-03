"""Runtime security/abuse bounds on the WebSocket transport (M10.4, issue #125)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.match.manager import MatchManager
from app.match.models import InvalidNicknameError, ServerBusyError
from app.replay.writer import match_dir
from app.transport import connections
from app.transport import ws as ws_module
from app.transport.limits import MAX_FAILED_JOINS, MAX_MESSAGE_BYTES, MESSAGE_BURST, TokenBucket
from tests.transport._helpers import _create

_ORIGIN = "https://play.example.com"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(public_base_url=_ORIGIN, replay_dir=tmp_path, max_matches=2)
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client


def _error(ws: Any) -> dict[str, Any]:
    message = dict(ws.receive_json())
    assert message["type"] == "error"
    return message


def test_foreign_origin_handshake_is_refused(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws", headers={"origin": "https://evil.example"}),
    ):
        pass
    assert exc_info.value.code == 1008


@pytest.mark.parametrize("headers", [{"origin": _ORIGIN}, {"origin": _ORIGIN.upper()}, {}])
def test_allowed_or_absent_origin_is_accepted(client: TestClient, headers: dict[str, str]) -> None:
    with client.websocket_connect("/ws", headers=headers) as ws:
        assert _create(ws)["type"] == "created"


def test_oversized_message_is_rejected_and_closed(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text("x" * (MAX_MESSAGE_BYTES + 1))
        assert _error(ws)["error"]["code"] == "message_too_large"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1009


def test_message_flood_is_rate_limited_and_closed(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        for _ in range(MESSAGE_BURST + 5):
            ws.send_text("{}")
        codes = []
        with pytest.raises(WebSocketDisconnect) as exc_info:
            while True:
                codes.append(_error(ws)["error"]["code"])
        assert codes[-1] == "rate_limited"
        assert set(codes[:-1]) == {"invalid_message"}
        assert exc_info.value.code == 1008


def test_join_code_guessing_is_capped_per_connection(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        for attempt in range(1, MAX_FAILED_JOINS + 1):
            ws.send_text(
                json.dumps(
                    {"protocolVersion": 1, "type": "join", "joinCode": "NOPE00", "nickname": "eve"}
                )
            )
            expected = "too_many_join_attempts" if attempt == MAX_FAILED_JOINS else "match_not_found"
            assert _error(ws)["error"]["code"] == expected
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


def test_token_bucket_refills_at_configured_rate() -> None:
    now = [0.0]
    bucket = TokenBucket(rate_per_s=10.0, burst=2, clock=lambda: now[0])
    assert bucket.allow() and bucket.allow()
    assert not bucket.allow()
    now[0] += 0.1
    assert bucket.allow()
    assert not bucket.allow()


def test_match_capacity_returns_server_busy(client: TestClient) -> None:
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        _create(a)
        _create(b)
        with client.websocket_connect("/ws") as c:
            c.send_text(json.dumps({"protocolVersion": 1, "type": "create", "nickname": "carol"}))
            assert _error(c)["error"]["code"] == "server_busy"


def test_manager_capacity_counts_every_match() -> None:
    manager = MatchManager(max_matches=1)
    manager.create_match("alice")
    with pytest.raises(ServerBusyError):
        manager.create_match("bob")


@pytest.mark.parametrize("nickname", ["bad\x00name", "tab\tname", "rtl\u202eeman", "x\u2066y"])
def test_nickname_control_characters_rejected(nickname: str) -> None:
    with pytest.raises(InvalidNicknameError):
        MatchManager().create_match(nickname)


@pytest.mark.parametrize("nickname", ["Commander", "Ünïcødé", "👩‍🚀 pilot", "a b"])
def test_ordinary_nicknames_accepted(nickname: str) -> None:
    assert MatchManager().create_match(nickname).player_id is not None


@pytest.mark.parametrize("match_id", ["../etc", "a/b", "/abs", "..", "", "a" * 65, "x\x00y"])
def test_replay_match_dir_refuses_unsafe_ids(tmp_path: Path, match_id: str) -> None:
    with pytest.raises(ValueError):
        match_dir(tmp_path, match_id)


def test_replay_match_dir_accepts_server_ids(tmp_path: Path) -> None:
    assert match_dir(tmp_path, "0123456789abcdef0123456789abcdef").parent == tmp_path


class _StalledSocket:
    """A peer that never reads: every send blocks forever."""

    def __init__(self) -> None:
        self.closed_with: int | None = None
        self.sends = 0

    async def send_text(self, text: str) -> None:
        self.sends += 1
        await asyncio.Event().wait()

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def test_stalled_peer_is_closed_instead_of_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections, "SEND_TIMEOUT_S", 0.05)
    stalled = _StalledSocket()
    await connections._send_text(stalled, "{}")  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    assert stalled.closed_with == 1011
    # Later sends skip the stalled socket immediately rather than waiting again.
    await asyncio.wait_for(connections._send_text(stalled, "{}"), 0.01)  # type: ignore[arg-type]
    assert stalled.sends == 1


def test_unbound_socket_is_closed_at_the_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NE-13: an accepted socket that never creates/joins/reconnects is dropped."""
    monkeypatch.setattr(ws_module, "UNBOUND_SOCKET_TIMEOUT_S", 0.3)
    with client.websocket_connect("/ws") as ws:
        assert _error(ws)["error"]["code"] == "bind_timeout"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
    assert exc_info.value.code == 1008


def test_invalid_frames_do_not_extend_the_bind_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 1: the deadline is absolute, not reset per received frame."""
    monkeypatch.setattr(ws_module, "UNBOUND_SOCKET_TIMEOUT_S", 0.6)
    with client.websocket_connect("/ws") as ws:
        time.sleep(0.4)
        ws.send_text("not json")
        assert _error(ws)["error"]["code"] == "invalid_message"
        started = time.monotonic()
        assert _error(ws)["error"]["code"] == "bind_timeout"
        assert time.monotonic() - started < 0.45  # ~0.2 s left, not a fresh 0.6 s
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_bound_socket_is_not_subject_to_the_bind_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws_module, "UNBOUND_SOCKET_TIMEOUT_S", 0.3)
    with client.websocket_connect("/ws", headers={"origin": _ORIGIN}) as ws:
        assert _create(ws)["type"] == "created"
        time.sleep(0.4)
        ws.send_text("not json")
        assert _error(ws)["error"]["code"] == "invalid_message"  # still open and serving
