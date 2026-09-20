"""Pydantic transport boundary mirroring `protocol/schemas/*.schema.json`.

This package owns *parsing and serialization only*: it turns raw JSON on
the wire into typed, schema-shaped Python objects (and back), and rejects
anything that does not match the canonical JSON Schema shapes in
`protocol/schemas/`. It does not decide gameplay legality or match
lifecycle rules -- that is `app.match`'s and the engine's job (see
AGENTS.md's non-negotiable architecture rules).

Two entry points cover the whole boundary:

- `parse_client_message`: raw JSON in -> a validated `InboundMessage`, or a
  `pydantic.ValidationError` raised before anything reaches `app.match` or
  the engine.
- `serialize_server_message`: a validated `OutboundMessage` -> schema-valid
  JSON text.

Every message shape is a plain mirror of one `$defs`/top-level entry in
`protocol/schemas/*.schema.json` (see `common.py`, `client_messages.py`,
`server_messages.py`, `snapshot.py`, `reconnect.py`, and `envelope.py` for
the wire-level unions that span multiple schema files). There is no second,
hand-authored catalog of message types/enums anywhere in this package --
`backend/tests/protocol/test_schema_conformance.py` proves that by running
the JSON Schema fixture corpus from `protocol/fixtures/` through both these
models and the actual JSON Schema files.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.protocol.envelope import (
    InboundMessage,
    InboundMessageAdapter,
    OutboundMessage,
    OutboundMessageAdapter,
)

__all__ = [
    "InboundMessage",
    "OutboundMessage",
    "ValidationError",
    "parse_client_message",
    "serialize_server_message",
]


def parse_client_message(raw: str | bytes) -> InboundMessage:
    """Parse and validate raw inbound JSON into a typed client message.

    Raises `pydantic.ValidationError` for malformed JSON, an unknown/missing
    `type` discriminator, a wrong/missing `protocolVersion`, missing
    required fields, or unexpected extra fields -- i.e. for anything that
    would fail `protocol/schemas/client_messages.schema.json` or
    `protocol/schemas/reconnect.schema.json`'s `clientReconnect` variant.
    Callers (the WebSocket handler, in a later task) must let this
    exception reject the message before it reaches `app.match` or engine
    code.
    """
    return InboundMessageAdapter.validate_json(raw)


def serialize_server_message(message: OutboundMessage) -> str:
    """Serialize a validated server message to schema-valid JSON text.

    `by_alias=True` emits each field under its wire (camelCase) alias
    rather than its Python (snake_case) attribute name -- see
    `ProtocolModel` in `common.py`. `exclude_none=True` drops optional
    fields left unset (e.g. `ServerError.match_id`, `ErrorInfo.details`)
    rather than emitting them as JSON `null`, since none of the source
    schemas' properties accept a `null` type.
    """
    return OutboundMessageAdapter.dump_json(message, by_alias=True, exclude_none=True).decode(
        "utf-8"
    )
