"""CR004.6 (#287) -- an AI-seat match's snapshot validates end to end.

`app.protocol.common.SnapshotState` was `extra="forbid"` with no
``ai_memories`` field (task-3-report.md's "commander_for / commanders
consumers" section, backend gap): every real snapshot from a match against
the computer opponent (CR004.3/#284's AI seat, which has no commander) would
have failed backend validation before this fix. This module builds a real
AI-seat ``GameState`` directly (no match/session layer -- CR004.7 owns
that), steps it, and proves the resulting ``to_snapshot`` output round-trips
through both the Pydantic model and the actual JSON Schema, for a state that
has one commander (the human's) and no commander for the second seat.

Also proves the fix does not change the wire shape of an all-human (PvP)
match: ``ai_memories`` stays fully elided, matching `to_snapshot`'s own
elision, so this task cannot regress the M9 PvP contract.
"""

from __future__ import annotations

import dataclasses

from nether_earth import engine
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.snapshot import to_snapshot

from app.match.world import load_standard_world
from app.protocol.common import SnapshotState

from .schema_registry import validator_for


def _vs_ai_state(*, seed: int = 3):
    world = load_standard_world()
    scenario = dataclasses.replace(default_pvp_scenario(), player_two_controller="ai")
    return create_initial_state(scenario, world, seed=seed), world


def test_an_ai_seat_snapshot_with_no_commander_for_that_seat_validates() -> None:
    state, world = _vs_ai_state()
    assert state.commander_for(PLAYER_TWO) is None
    assert len(state.commanders) == 1

    for _ in range(20):
        state, _events = engine.step(state, (), world=world)

    snapshot = to_snapshot(state)
    assert "ai_memories" in snapshot
    assert [c["player_id"] for c in snapshot["commanders"]] == [PLAYER_ONE.to_json()]

    # Pydantic (the backend's own validation boundary)...
    model = SnapshotState.model_validate(snapshot)
    assert model.model_dump() == snapshot

    # ...and the canonical JSON Schema independently agree the shape is
    # valid, wrapped in the same envelope shape `test_schema_conformance.py`
    # validates real fixtures against.
    envelope = {
        "protocolVersion": 1,
        "type": "snapshot",
        "matchId": "m1",
        "tick": snapshot["tick"],
        "state": snapshot,
    }
    errors = list(validator_for("snapshot").iter_errors(envelope))
    assert errors == [], errors


def test_an_all_human_match_snapshot_still_elides_ai_memories() -> None:
    """Regression guard: adding the field must not touch PvP's wire shape."""
    world = load_standard_world()
    state = create_initial_state(default_pvp_scenario(), world, seed=3)

    snapshot = to_snapshot(state)
    assert "ai_memories" not in snapshot

    model = SnapshotState.model_validate(snapshot)
    assert "ai_memories" not in model.model_dump()
    assert model.model_dump() == snapshot
