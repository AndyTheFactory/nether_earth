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

from pydantic import ConfigDict, Field, TypeAdapter

from app.protocol.common import (
    ErrorInfo,
    JoinCode,
    MatchId,
    OpponentMode,
    PlayerId,
    PlayerSummary,
    ProtocolModel,
    ProtocolVersion,
    SessionToken,
    Tick,
    TimestampMs,
)


class ServerCreated(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.created`.

    Sent to the creating player only, in response to a client create
    command. `join_code` is `None` for a solo match (CR004.8, issue #289):
    there is no second human slot to join. `opponent` names which seat the
    server actually created ("computer" only for a solo match) so the
    frontend can skip the waiting/ready screens and name the AI seat in the
    HUD without inferring it from the absence of a join code.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["created"]
    match_id: MatchId
    join_code: JoinCode | None
    player_id: PlayerId
    session_token: SessionToken
    opponent: OpponentMode = "human"


class ServerJoined(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.joined`.

    Sent to the joining player only; roster/readiness is broadcast
    separately via `ServerReadyState`.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["joined"]
    match_id: MatchId
    player_id: PlayerId
    session_token: SessionToken


class ServerReadyState(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.readyState`.

    Broadcast to all connected players whenever match roster or readiness
    changes.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["ready_state"]
    match_id: MatchId
    players: Annotated[list[PlayerSummary], Field(min_length=1, max_length=2)]


class ServerStarted(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.started`."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["started"]
    match_id: MatchId
    tick: Tick


class ServerPaused(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.paused`.

    Sent when the match pauses because a player disconnected.
    `graceDeadlineMs` is wall-clock runtime metadata, not gameplay tick
    state.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["paused"]
    match_id: MatchId
    disconnected_player_id: PlayerId
    grace_deadline_ms: TimestampMs


class ServerResumed(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.resumed`.

    Sent when simulation resumes because both players are connected again.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["resumed"]
    match_id: MatchId
    tick: Tick


class ServerForfeit(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.forfeit`.

    Sent when a disconnected player's reconnect grace deadline expires
    while the opponent remains eligible to win.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["forfeit"]
    match_id: MatchId
    forfeiting_player_id: PlayerId
    winner_player_id: PlayerId
    reason: Literal["disconnect_timeout"]


class ServerNoContest(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.noContest`.

    Sent when both players disconnect and both independent reconnect grace
    deadlines expire without either returning.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["no_contest"]
    match_id: MatchId
    reason: Literal["disconnect_timeout_both"]


class ServerFinished(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.finished`.

    Sent when the match ends because a player owns zero war bases (normal
    victory), per functional-spec.md #4.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["finished"]
    match_id: MatchId
    winner_player_id: PlayerId
    tick: Tick


class ServerError(ProtocolModel):
    """Mirrors server_messages.schema.json `$defs.error`.

    `matchId` is omitted for errors that occur before a match context
    exists (e.g. malformed create/join).
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["error"]
    match_id: MatchId | None = None
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
