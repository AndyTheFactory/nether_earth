"""Reconnect handshake models mirroring `reconnect.schema.json`.

`ClientReconnect` is a client -> server message (a sixth inbound message
alongside the five in `client_messages.py`, kept in its own schema file
upstream). `ServerResync` is a server -> client message carrying the
current authoritative snapshot, per the locked disconnect/reconnect policy.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from app.protocol.common import MatchId, PlayerId, ProtocolModel, ProtocolVersion, SessionToken
from app.protocol.snapshot import SnapshotMessage


class ClientReconnect(ProtocolModel):
    """Mirrors reconnect.schema.json `$defs.clientReconnect`.

    Sent by a client re-establishing a WebSocket connection to an existing
    match after a disconnect.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["reconnect"]
    match_id: MatchId
    player_id: PlayerId
    session_token: SessionToken


class ServerResync(ProtocolModel):
    """Mirrors reconnect.schema.json `$defs.serverResync`.

    Sent in response to a successful reconnect, carrying the current
    authoritative snapshot per the locked disconnect/reconnect policy.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["resync"]
    match_id: MatchId
    player_id: PlayerId
    snapshot: SnapshotMessage


#: Mirrors reconnect.schema.json's top-level `oneOf`.
ReconnectMessage = Annotated[
    ClientReconnect | ServerResync,
    Field(discriminator="type"),
]

ReconnectMessageAdapter: TypeAdapter[ReconnectMessage] = TypeAdapter(ReconnectMessage)
