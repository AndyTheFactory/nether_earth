"""Shared protocol field types and envelope pieces.

Mirrors every scalar/object `$def` in `protocol/schemas/common.schema.json`.
Message-specific envelopes (client/server/snapshot/reconnect) live in their
own sibling modules and import these types -- this is the single source for
shared shapes so they cannot silently diverge across message families.

Architecture note (AGENTS.md): this module is a transport/serialization
boundary only. It does not validate gameplay legality; `payload`/`state`
below are still placeholders pending issue #98's full command/state
enumeration, exactly as in the JSON Schema they mirror.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Mirrors common.schema.json `$defs.protocolVersion` (`const: 1`).
ProtocolVersion = Literal[1]
PROTOCOL_VERSION: ProtocolVersion = 1

MatchId = Annotated[str, Field(min_length=1)]
JoinCode = Annotated[str, Field(min_length=1)]
PlayerId = Annotated[str, Field(min_length=1)]
SessionToken = Annotated[str, Field(min_length=1)]
Nickname = Annotated[str, Field(min_length=1, max_length=32)]
SequenceNumber = Annotated[int, Field(ge=0)]
Tick = Annotated[int, Field(ge=0)]
TimestampMs = Annotated[int, Field(ge=0)]
ErrorCode = Annotated[str, Field(min_length=1)]

#: Mirrors common.schema.json `$defs.snapshotState`: a placeholder,
#: `additionalProperties: true` object with no required shape, pending
#: issue #98's full authoritative-state field enumeration.
SnapshotState = dict[str, Any]


class ProtocolEnvelope(BaseModel):
    """The bare envelope: only the required `protocolVersion` const.

    Mirrors common.schema.json's top-level object exactly
    (`protocolVersion` required, `additionalProperties: false`). Every real
    message defined in the sibling modules extends this shape with its own
    `type` discriminator; this class itself is only a message in its own
    right for the `protocol/fixtures/common/` fixture set exercised by the
    schema-conformance tests.
    """

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion


class ErrorInfo(BaseModel):
    """Mirrors common.schema.json `$defs.errorInfo`."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: Annotated[str, Field(min_length=1)]
    details: dict[str, Any] | None = None


class PlayerSummary(BaseModel):
    """Mirrors common.schema.json `$defs.playerSummary`."""

    model_config = ConfigDict(extra="forbid")

    playerId: PlayerId
    nickname: Nickname
    ready: bool


class PlaceholderCommandPayload(BaseModel):
    """Mirrors common.schema.json `$defs.placeholderCommandPayload`.

    Not a real gameplay command; kept only so the envelope/generation
    pipeline has a concrete variant to validate against until issue #98
    lands (see the JSON Schema's own description). `additionalProperties:
    true` in the source schema is preserved via `extra="allow"`.
    """

    model_config = ConfigDict(extra="allow")

    kind: Literal["placeholder"]


#: Mirrors common.schema.json `$defs.commandPayload`: a `oneOf` with a
#: single variant today. Widen this alias (not the discriminant machinery
#: elsewhere) when issue #98 adds real payload variants.
CommandPayload = PlaceholderCommandPayload
