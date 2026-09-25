"""Client -> server message models mirroring `client_messages.schema.json`.

One Pydantic model per `$defs` entry in that schema, plus the discriminated
union `ClientMessage` mirroring its top-level `oneOf`. `reconnect.py` adds
the sixth inbound client message (`ClientReconnect`) since it lives in its
own schema file; `envelope.py` combines both into the single union the
WebSocket boundary actually parses against.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from app.protocol.common import (
    CommandPayload,
    JoinCode,
    MatchId,
    Nickname,
    OpponentMode,
    PlayerId,
    ProtocolModel,
    ProtocolVersion,
    SequenceNumber,
    SessionToken,
)


class ClientCreateMatch(ProtocolModel):
    """Mirrors client_messages.schema.json `$defs.createMatch`.

    `opponent` is optional (CR004.8, issue #289) and defaults to `"human"`
    -- absent, it is today's unchanged PvP create. `"computer"` requests a
    solo match against the engine's AI seat; `app.transport.ws` is the only
    place that reads this field to choose
    `MatchManager.create_match`/`create_solo_match`, so no gameplay
    legality decision lives here.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["create"]
    nickname: Nickname
    opponent: OpponentMode = "human"


class ClientJoinMatch(ProtocolModel):
    """Mirrors client_messages.schema.json `$defs.joinMatch`."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["join"]
    join_code: JoinCode
    nickname: Nickname


class ClientSetReady(ProtocolModel):
    """Mirrors client_messages.schema.json `$defs.setReady`."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["ready"]
    match_id: MatchId
    player_id: PlayerId
    session_token: SessionToken
    ready: bool


class ClientLeaveMatch(ProtocolModel):
    """Mirrors client_messages.schema.json `$defs.leaveMatch`."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["leave"]
    match_id: MatchId
    player_id: PlayerId
    session_token: SessionToken


class ClientGameplayCommand(ProtocolModel):
    """Mirrors client_messages.schema.json `$defs.gameplayCommand`.

    `payload` is the real discriminated `commandPayload` union (issue #98,
    `app.protocol.common.CommandPayload`); the envelope fields
    (matchId/playerId/sessionToken/clientSequence) are stable and did not
    change when the real payload variants were added.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["command"]
    match_id: MatchId
    player_id: PlayerId
    session_token: SessionToken
    client_sequence: SequenceNumber
    payload: CommandPayload


#: Mirrors client_messages.schema.json's top-level `oneOf`.
ClientMessage = Annotated[
    ClientCreateMatch | ClientJoinMatch | ClientSetReady | ClientLeaveMatch | ClientGameplayCommand,
    Field(discriminator="type"),
]

ClientMessageAdapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
