"""Guards `app/protocol/envelope.py`'s wire-level unions against drift
(review finding on issue #91's PR).

`InboundMessage`/`OutboundMessage` are hand-recombined from the per-schema-
file unions (`ClientMessage`, `ServerMessage`, `ReconnectMessage`) because a
single WebSocket connection carries variants from more than one schema
file. Nothing about that recombination is checked by
`test_schema_conformance.py` (it validates fixtures against the per-file
unions only), so a future edit that adds a variant to e.g. `ClientMessage`
without also adding it to `InboundMessage` would pass every existing test
while `parse_client_message` silently started rejecting a now-valid
message. These tests compare the *sets of member classes* on both sides so
that drift fails loudly here instead.
"""

from __future__ import annotations

from typing import Any, get_args

from app.protocol.client_messages import ClientMessage
from app.protocol.envelope import InboundMessage, OutboundMessage
from app.protocol.reconnect import ClientReconnect, ReconnectMessage, ServerResync
from app.protocol.server_messages import ServerMessage
from app.protocol.snapshot import SnapshotMessage


def _variant_classes(discriminated_union: Any) -> set[type]:
    """Unwrap `Annotated[A | B | ..., Field(discriminator=...)]` to `{A, B, ...}`."""
    annotated_args = get_args(discriminated_union)
    assert annotated_args, f"expected an Annotated discriminated union, got {discriminated_union!r}"
    union_type = annotated_args[0]
    variants = get_args(union_type)
    assert variants, f"expected a union of model classes, got {union_type!r}"
    return set(variants)


def test_reconnect_message_has_exactly_client_and_server_variants() -> None:
    # Sanity check on the helper/fixture assumption the two tests below rely
    # on: reconnect.schema.json's oneOf is exactly {ClientReconnect, ServerResync}.
    assert _variant_classes(ReconnectMessage) == {ClientReconnect, ServerResync}


def test_inbound_message_matches_client_messages_plus_client_reconnect() -> None:
    expected = _variant_classes(ClientMessage) | {ClientReconnect}
    actual = _variant_classes(InboundMessage)
    assert actual == expected, (
        "InboundMessage (app/protocol/envelope.py) has drifted from ClientMessage "
        f"(client_messages.schema.json) + ClientReconnect (reconnect.schema.json): "
        f"missing={expected - actual} extra={actual - expected}"
    )


def test_outbound_message_matches_server_messages_plus_snapshot_and_resync() -> None:
    expected = _variant_classes(ServerMessage) | {SnapshotMessage, ServerResync}
    actual = _variant_classes(OutboundMessage)
    assert actual == expected, (
        "OutboundMessage (app/protocol/envelope.py) has drifted from ServerMessage "
        "(server_messages.schema.json) + SnapshotMessage (snapshot.schema.json) + "
        f"ServerResync (reconnect.schema.json): missing={expected - actual} extra={actual - expected}"
    )

