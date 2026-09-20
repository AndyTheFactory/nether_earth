"""Combined inbound/outbound message unions spanning multiple schema files.

`client_messages.py`, `server_messages.py`, `snapshot.py`, and
`reconnect.py` each mirror exactly one schema file's `$defs`/top-level
shape. On the wire, though, a single WebSocket carries every inbound
client-originated shape (the five `client_messages.schema.json` variants
plus `reconnect.schema.json`'s `ClientReconnect`) and every outbound
server-originated shape (the ten `server_messages.schema.json` variants
plus `snapshot.schema.json`'s `SnapshotMessage` and
`reconnect.schema.json`'s `ServerResync`). This module composes those two
wire-level unions from the per-schema models defined elsewhere -- it adds
no new fields or types of its own, so there is nothing here that could
diverge from the schemas.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

from app.protocol.client_messages import (
    ClientCreateMatch,
    ClientGameplayCommand,
    ClientJoinMatch,
    ClientLeaveMatch,
    ClientSetReady,
)
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

#: Every message shape a client may send to the server.
InboundMessage = Annotated[
    ClientCreateMatch
    | ClientJoinMatch
    | ClientSetReady
    | ClientLeaveMatch
    | ClientGameplayCommand
    | ClientReconnect,
    Field(discriminator="type"),
]

InboundMessageAdapter: TypeAdapter[InboundMessage] = TypeAdapter(InboundMessage)

#: Every message shape the server may send to a client.
OutboundMessage = Annotated[
    ServerCreated
    | ServerJoined
    | ServerReadyState
    | ServerStarted
    | ServerPaused
    | ServerResumed
    | ServerForfeit
    | ServerNoContest
    | ServerFinished
    | ServerError
    | SnapshotMessage
    | ServerResync,
    Field(discriminator="type"),
]

OutboundMessageAdapter: TypeAdapter[OutboundMessage] = TypeAdapter(OutboundMessage)
