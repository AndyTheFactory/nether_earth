"""Shared protocol field types and envelope pieces.

Mirrors every scalar/object `$def` in `protocol/schemas/common.schema.json`.
Message-specific envelopes (client/server/snapshot/reconnect) live in their
own sibling modules and import these types -- this is the single source for
shared shapes so they cannot silently diverge across message families.

Architecture note (AGENTS.md): this module is a transport/serialization
boundary only. It does not validate gameplay legality; `payload`/`state`
below are still placeholders pending issue #98's full command/state
enumeration, exactly as in the JSON Schema they mirror.

Naming: wire JSON is camelCase (per the schemas); Python attribute access on
every model is snake_case, matching `app.match`'s existing convention
(`Match.match_id`, `PlayerSlot.session_token`, ...). `ProtocolModel` below
is the shared base that makes that translation automatic via `pydantic`'s
`to_camel` alias generator -- individual models never hand-write aliases.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

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


class ProtocolModel(BaseModel):
    """Shared base for every protocol model.

    `alias_generator=to_camel` derives each field's wire name (`matchId`)
    from its Python (snake_case) name (`match_id`) automatically, so the
    JSON stays schema-conformant while Python call sites use the same
    snake_case convention as `app.match`. `populate_by_name=True` lets code
    construct instances with either the snake_case field name or the
    camelCase alias. Subclasses layer their own `extra=` policy on top (this
    merges with, rather than replaces, this base's `model_config`).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ProtocolEnvelope(ProtocolModel):
    """The bare envelope: only the required `protocolVersion` const.

    Mirrors common.schema.json's top-level object exactly
    (`protocolVersion` required, `additionalProperties: false`). Every real
    message defined in the sibling modules extends this shape with its own
    `type` discriminator; this class itself is only a message in its own
    right for the `protocol/fixtures/common/` fixture set exercised by the
    schema-conformance tests.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion


class ErrorInfo(ProtocolModel):
    """Mirrors common.schema.json `$defs.errorInfo`."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: Annotated[str, Field(min_length=1)]
    details: dict[str, Any] | None = None


class PlayerSummary(ProtocolModel):
    """Mirrors common.schema.json `$defs.playerSummary`."""

    model_config = ConfigDict(extra="forbid")

    player_id: PlayerId
    nickname: Nickname
    ready: bool


class PlaceholderCommandPayload(ProtocolModel):
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
