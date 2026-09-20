"""Tests for the two `app.protocol` entry points (issue #91).

- `parse_client_message`: proves invalid inbound payloads raise
  `pydantic.ValidationError` -- i.e. fail -- before any `app.match` or
  engine code would see them (this module and `app.protocol` do not import
  `app.match` at all, which `test_protocol_package_has_no_match_dependency`
  locks in).
- `serialize_server_message`: proves a representative instance of every
  outbound message shape round-trips to JSON that both the actual JSON
  Schema (via `jsonschema`) and the Pydantic models accept.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.protocol as protocol_package
from app.protocol import parse_client_message, serialize_server_message
from app.protocol.client_messages import ClientCreateMatch
from app.protocol.common import ErrorInfo, PlayerSummary
from app.protocol.envelope import OutboundMessage, OutboundMessageAdapter
from app.protocol.reconnect import ClientReconnect, ServerResync
from app.protocol.server_messages import (
    ServerCreated,
    ServerError,
    ServerFinished,
    ServerForfeit,
    ServerJoined,
    ServerNoContest,
    ServerPaused,
    ServerReadyState,
    ServerResumed,
    ServerStarted,
)
from app.protocol.snapshot import SnapshotMessage

from .schema_registry import validator_for


def test_parse_client_message_accepts_valid_create() -> None:
    raw = json.dumps({"protocolVersion": 1, "type": "create", "nickname": "Alice"})
    message = parse_client_message(raw)
    assert isinstance(message, ClientCreateMatch)
    assert message.nickname == "Alice"


def test_parse_client_message_accepts_reconnect() -> None:
    raw = json.dumps(
        {
            "protocolVersion": 1,
            "type": "reconnect",
            "matchId": "match-1",
            "playerId": "player-1",
            "sessionToken": "session-token-1",
        }
    )
    message = parse_client_message(raw)
    assert isinstance(message, ClientReconnect)


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        json.dumps({"type": "create", "nickname": "Alice"}).encode(),  # missing protocolVersion
        json.dumps({"protocolVersion": 1, "type": "not_a_real_type", "nickname": "Alice"}).encode(),
        json.dumps(
            {"protocolVersion": 1, "type": "create", "nickname": "Alice", "extra": True}
        ).encode(),
        json.dumps({"protocolVersion": 2, "type": "create", "nickname": "Alice"}).encode(),
    ],
    ids=["malformed-json", "missing-protocol-version", "unknown-type", "extra-field", "wrong-version"],
)
def test_parse_client_message_rejects_invalid_payloads(raw: bytes) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(raw)


def test_protocol_package_has_no_match_dependency() -> None:
    """`app.protocol` must never import `app.match` (AGENTS.md: transport
    parsing/serialization must not reach gameplay/lifecycle code)."""
    package_dir = Path(protocol_package.__file__).parent
    for path in package_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            assert not any(name.startswith("app.match") for name in names), (
                f"{path} imports app.match: {names}"
            )


_SNAPSHOT_FOR_RESYNC = SnapshotMessage(
    protocolVersion=1, type="snapshot", matchId="match-1", tick=120, state={"placeholder": True}
)

_REPRESENTATIVE_OUTBOUND: list[tuple[str, OutboundMessage]] = [
    (
        "server_messages",
        ServerCreated(
            protocolVersion=1,
            type="created",
            matchId="match-1",
            joinCode="ABCD1234",
            playerId="player-1",
            sessionToken="session-token-1",
        ),
    ),
    (
        "server_messages",
        ServerJoined(
            protocolVersion=1,
            type="joined",
            matchId="match-1",
            playerId="player-2",
            sessionToken="session-token-2",
        ),
    ),
    (
        "server_messages",
        ServerReadyState(
            protocolVersion=1,
            type="ready_state",
            matchId="match-1",
            players=[
                PlayerSummary(playerId="player-1", nickname="Alice", ready=True),
                PlayerSummary(playerId="player-2", nickname="Bob", ready=False),
            ],
        ),
    ),
    ("server_messages", ServerStarted(protocolVersion=1, type="started", matchId="match-1", tick=0)),
    (
        "server_messages",
        ServerPaused(
            protocolVersion=1,
            type="paused",
            matchId="match-1",
            disconnectedPlayerId="player-1",
            graceDeadlineMs=1717000000000,
        ),
    ),
    (
        "server_messages",
        ServerResumed(protocolVersion=1, type="resumed", matchId="match-1", tick=1200),
    ),
    (
        "server_messages",
        ServerForfeit(
            protocolVersion=1,
            type="forfeit",
            matchId="match-1",
            forfeitingPlayerId="player-1",
            winnerPlayerId="player-2",
            reason="disconnect_timeout",
        ),
    ),
    (
        "server_messages",
        ServerNoContest(
            protocolVersion=1, type="no_contest", matchId="match-1", reason="disconnect_timeout_both"
        ),
    ),
    (
        "server_messages",
        ServerFinished(
            protocolVersion=1, type="finished", matchId="match-1", winnerPlayerId="player-1", tick=54000
        ),
    ),
    (
        "server_messages",
        ServerError(
            protocolVersion=1,
            type="error",
            matchId="match-1",
            error=ErrorInfo(code="invalid_command", message="Unknown command type."),
        ),
    ),
    (
        "server_messages",
        ServerError(
            protocolVersion=1,
            type="error",
            error=ErrorInfo(code="malformed_message", message="Could not parse client message."),
        ),
    ),
    (
        "snapshot",
        SnapshotMessage(
            protocolVersion=1, type="snapshot", matchId="match-1", tick=120, state={"placeholder": True}
        ),
    ),
    (
        "reconnect",
        ServerResync(
            protocolVersion=1,
            type="resync",
            matchId="match-1",
            playerId="player-1",
            snapshot=_SNAPSHOT_FOR_RESYNC,
        ),
    ),
]


@pytest.mark.parametrize(
    ("schema_base_name", "message"),
    _REPRESENTATIVE_OUTBOUND,
    ids=[f"{base}-{message.type}" for base, message in _REPRESENTATIVE_OUTBOUND],
)
def test_serialize_server_message_round_trips_to_schema_valid_json(
    schema_base_name: str, message: OutboundMessage
) -> None:
    raw = serialize_server_message(message)
    data = json.loads(raw)

    errors = list(validator_for(schema_base_name).iter_errors(data))
    assert errors == [], f"serialized {message.type!r} failed JSON Schema: {errors}"

    round_tripped = OutboundMessageAdapter.validate_json(raw)
    assert round_tripped == message
