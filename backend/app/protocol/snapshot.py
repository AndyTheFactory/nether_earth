"""Authoritative snapshot envelope mirroring `snapshot.schema.json`.

`state` is a placeholder pending issue #98's full field enumeration
(players/resources/commanders/robots/ownership/projectiles/map+scenario+
rules versions/result per technical-spec.md #22), exactly as in the source
schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.protocol.common import MatchId, ProtocolVersion, SnapshotState, Tick


class SnapshotMessage(BaseModel):
    """Mirrors snapshot.schema.json's top-level object."""

    model_config = ConfigDict(extra="forbid")

    protocolVersion: ProtocolVersion
    type: Literal["snapshot"]
    matchId: MatchId
    tick: Tick
    state: SnapshotState
