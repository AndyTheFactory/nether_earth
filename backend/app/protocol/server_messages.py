"""Server -> client lifecycle/event message models mirroring
`server_messages.schema.json`.

One Pydantic model per `$defs` entry in that schema, plus the discriminated
union `ServerMessage` mirroring its top-level `oneOf`. `snapshot.py` and
`reconnect.py` add the two remaining outbound server message shapes since
they live in their own schema files; `envelope.py` combines all three into
the single union the serialize entry point accepts.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.protocol.common import (
    ErrorInfo,
    JoinCode,
    MatchId,
    PlayerId,
    PlayerSummary,
    ProtocolVersion,
    SessionToken,
    Tick,
    TimestampMs,
)


class ServerCreated(BaseModel):
    """Mirrors server_messages.schema.json `$defs.created`.

    Sent to the creating player only, in response to a client create
    command.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["created"]
    matchId: MatchId
    joinCode: JoinCode
    playerId: PlayerId
    sessionToken: SessionToken


class ServerJoined(BaseModel):
    """Mirrors server_messages.schema.json `$defs.joined`.

    Sent to the joining player only; roster/readiness is broadcast
    separately via `ServerReadyState`.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["joined"]
    matchId: MatchId
    playerId: PlayerId
    sessionToken: SessionToken


class ServerReadyState(BaseModel):
    """Mirrors server_messages.schema.json `$defs.readyState`.

    Broadcast to all connected players whenever match roster or readiness
    changes.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["ready_state"]
    matchId: MatchId
    players: Annotated[list[PlayerSummary], Field(min_length=1, max_length=2)]


class ServerStarted(BaseModel):
    """Mirrors server_messages.schema.json `$defs.started`."""

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["started"]
    matchId: MatchId
    tick: Tick


class ServerPaused(BaseModel):
    """Mirrors server_messages.schema.json `$defs.paused`.

    Sent when the match pauses because a player disconnected.
    `graceDeadlineMs` is wall-clock runtime metadata, not gameplay tick
    state.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["paused"]
    matchId: MatchId
    disconnectedPlayerId: PlayerId
    graceDeadlineMs: TimestampMs


class ServerResumed(BaseModel):
    """Mirrors server_messages.schema.json `$defs.resumed`.

    Sent when simulation resumes because both players are connected again.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["resumed"]
    matchId: MatchId
    tick: Tick


class ServerForfeit(BaseModel):
    """Mirrors server_messages.schema.json `$defs.forfeit`.

    Sent when a disconnected player's reconnect grace deadline expires
    while the opponent remains eligible to win.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["forfeit"]
    matchId: MatchId
    forfeitingPlayerId: PlayerId
    winnerPlayerId: PlayerId
    reason: Literal["disconnect_timeout"]


class ServerNoContest(BaseModel):
    """Mirrors server_messages.schema.json `$defs.noContest`.

    Sent when both players disconnect and both independent reconnect grace
    deadlines expire without either returning.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["no_contest"]
    matchId: MatchId
    reason: Literal["disconnect_timeout_both"]


class ServerFinished(BaseModel):
    """Mirrors server_messages.schema.json `$defs.finished`.

    Sent when the match ends because a player owns zero war bases (normal
    victory), per functional-spec.md #4.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["finished"]
    matchId: MatchId
    winnerPlayerId: PlayerId
    tick: Tick


class ServerError(BaseModel):
    """Mirrors server_messages.schema.json `$defs.error`.

    `matchId` is omitted for errors that occur before a match context
    exists (e.g. malformed create/join).
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["error"]
    matchId: MatchId | None = None
    error: ErrorInfo


#: Mirrors server_messages.schema.json's top-level `oneOf`.
ServerMessage = Annotated[
    ServerCreated
    | ServerJoined
    | ServerReadyState
    | ServerStarted
    | ServerPaused
    | ServerResumed
    | ServerForfeit
    | ServerNoContest
    | ServerFinished
    | ServerError,
    Field(discriminator="type"),
]

ServerMessageAdapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
