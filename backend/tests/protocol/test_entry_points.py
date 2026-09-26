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
from app.protocol.common import ErrorInfo, PlayerSummary, SnapshotState
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
    protocol_version=1,
    type="snapshot",
    match_id="match-1",
    tick=120,
    state=SnapshotState(
        tick=120,
        players=["player-1", "player-2"],
        seed=0,
        commanders=[],
        resource_pools=[],
        construction_sessions=[],
        robots=[],
        structure_ownership=[],
        capture_progress=[],
        projectiles=[],
        structure_destruction=[],
        scenery_debris=[],
    ),
)

_REPRESENTATIVE_OUTBOUND: list[tuple[str, OutboundMessage]] = [
    (
        "server_messages",
        ServerCreated(
            protocol_version=1,
            type="created",
            match_id="match-1",
            join_code="ABCD1234",
            player_id="player-1",
            session_token="session-token-1",
        ),
    ),
    (
        "server_messages",
        ServerJoined(
            protocol_version=1,
            type="joined",
            match_id="match-1",
            player_id="player-2",
            session_token="session-token-2",
        ),
    ),
    (
        "server_messages",
        ServerReadyState(
            protocol_version=1,
            type="ready_state",
            match_id="match-1",
            players=[
                PlayerSummary(player_id="player-1", nickname="Alice", ready=True),
                PlayerSummary(player_id="player-2", nickname="Bob", ready=False),
            ],
        ),
    ),
    (
        "server_messages",
        ServerStarted(protocol_version=1, type="started", match_id="match-1", tick=0),
    ),
    (
        "server_messages",
        ServerPaused(
            protocol_version=1,
            type="paused",
            match_id="match-1",
            disconnected_player_id="player-1",
            grace_deadline_ms=1717000000000,
        ),
    ),
    (
        "server_messages",
        ServerResumed(protocol_version=1, type="resumed", match_id="match-1", tick=1200),
    ),
    (
        "server_messages",
        ServerForfeit(
            protocol_version=1,
            type="forfeit",
            match_id="match-1",
            forfeiting_player_id="player-1",
            winner_player_id="player-2",
            reason="disconnect_timeout",
        ),
    ),
    (
        "server_messages",
        ServerNoContest(
            protocol_version=1, type="no_contest", match_id="match-1", reason="disconnect_timeout_both"
        ),
    ),
    (
        "server_messages",
        ServerFinished(
            protocol_version=1,
            type="finished",
            match_id="match-1",
            winner_player_id="player-1",
            tick=54000,
        ),
    ),
    (
        "server_messages",
        ServerError(
            protocol_version=1,
            type="error",
            match_id="match-1",
            error=ErrorInfo(code="invalid_command", message="Unknown command type."),
        ),
    ),
    (
        "server_messages",
        ServerError(
            protocol_version=1,
            type="error",
            error=ErrorInfo(code="malformed_message", message="Could not parse client message."),
        ),
    ),
    (
        "snapshot",
        SnapshotMessage(
            protocol_version=1,
            type="snapshot",
            match_id="match-1",
            tick=120,
            state=SnapshotState(
                tick=120,
                players=["player-1", "player-2"],
                seed=0,
                commanders=[],
                resource_pools=[],
                construction_sessions=[],
                robots=[],
                structure_ownership=[],
                capture_progress=[],
                projectiles=[],
                structure_destruction=[],
                scenery_debris=[],
            ),
        ),
    ),
    (
        "reconnect",
        ServerResync(
            protocol_version=1,
            type="resync",
            match_id="match-1",
            player_id="player-1",
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


def test_pvp_created_message_is_byte_identical_to_pre_cr004_output() -> None:
    """CR004 final-review fix (I1): `serialize_server_message` special-cases
    `ServerCreated` to drop an unset `opponent` and keep `join_code` in its
    schema position (see the docstring above). That special-casing must not
    change the *bytes* of an existing PvP `created` reply: it must still be
    exactly what `OutboundMessageAdapter.dump_json` (the pre-CR004.8 code
    path, used for every other message type) would have produced for the
    same fields, compact separators included -- not `json.dumps` on a plain
    dict, whose default `", "` / `": "` separators would make the reply
    byte-different from what pre-CR004 clients saw on the wire.
    """
    message = ServerCreated(
        protocol_version=1,
        type="created",
        match_id="match-1",
        join_code="ABCD1234",
        player_id="player-1",
        session_token="session-token-1",
    )
    assert message.opponent is None  # PvP: no `opponent` field set.

    expected = OutboundMessageAdapter.dump_json(
        message, by_alias=True, exclude_none=True
    ).decode("utf-8")

    actual = serialize_server_message(message)

    assert actual == expected
    assert '", "' not in actual
    assert '": "' not in actual
    assert "opponent" not in actual
