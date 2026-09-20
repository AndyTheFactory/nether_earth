"""Authoritative snapshot envelope mirroring `snapshot.schema.json`.

`state` is the real, fully enumerated `SnapshotState` (issue #98): a thin
field-shape mirror of `nether_earth.snapshot.to_snapshot`'s own return
shape, covering every field needed to reconstruct authoritative state on
reconnect (players/resources/commanders/robots/ownership/capture progress/
projectiles/structure destruction).
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from app.protocol.common import MatchId, ProtocolModel, ProtocolVersion, SnapshotState, Tick


class SnapshotMessage(ProtocolModel):
    """Mirrors snapshot.schema.json's top-level object."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion
    type: Literal["snapshot"]
    match_id: MatchId
    tick: Tick
    state: SnapshotState
