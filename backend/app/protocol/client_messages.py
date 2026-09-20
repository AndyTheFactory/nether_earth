"""Client -> server message models mirroring `client_messages.schema.json`.

One Pydantic model per `$defs` entry in that schema, plus the discriminated
union `ClientMessage` mirroring its top-level `oneOf`. `reconnect.py` adds
the sixth inbound client message (`ClientReconnect`) since it lives in its
own schema file; `envelope.py` combines both into the single union the
WebSocket boundary actually parses against.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.protocol.common import (
    JoinCode,
    MatchId,
    Nickname,
    PlaceholderCommandPayload,
    PlayerId,
    ProtocolVersion,
    SequenceNumber,
    SessionToken,
)


class ClientCreateMatch(BaseModel):
    """Mirrors client_messages.schema.json `$defs.createMatch`."""

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["create"]
    nickname: Nickname


class ClientJoinMatch(BaseModel):
    """Mirrors client_messages.schema.json `$defs.joinMatch`."""

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["join"]
    joinCode: JoinCode
    nickname: Nickname


class ClientSetReady(BaseModel):
    """Mirrors client_messages.schema.json `$defs.setReady`."""

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["ready"]
    matchId: MatchId
    playerId: PlayerId
    sessionToken: SessionToken
    ready: bool


class ClientLeaveMatch(BaseModel):
    """Mirrors client_messages.schema.json `$defs.leaveMatch`."""

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["leave"]
    matchId: MatchId
    playerId: PlayerId
    sessionToken: SessionToken


class ClientGameplayCommand(BaseModel):
    """Mirrors client_messages.schema.json `$defs.gameplayCommand`.

    `payload` is a placeholder shape pending issue #98; the envelope fields
    (matchId/playerId/sessionToken/clientSequence) are stable per that
    schema's description.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["command"]
    matchId: MatchId
    playerId: PlayerId
    sessionToken: SessionToken
    clientSequence: SequenceNumber
    payload: PlaceholderCommandPayload


#: Mirrors client_messages.schema.json's top-level `oneOf`.
ClientMessage = Annotated[
    ClientCreateMatch | ClientJoinMatch | ClientSetReady | ClientLeaveMatch | ClientGameplayCommand,
    Field(discriminator="type"),
]

ClientMessageAdapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
