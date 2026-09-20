"""``app.transport.victory``: engine ``VictoryEvent`` -> durable result, ``finished`` frame, FINISHED (M9.1 gap G2)."""

from __future__ import annotations

import json

from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_TWO
from nether_earth.victory import VictoryEvent

from app.match.manager import MatchManager
from app.match.models import MatchOutcome, MatchRuntimeState
from app.match.world import load_standard_world
from app.protocol import serialize_server_message
from app.transport.connections import ConnectionRegistry
from app.transport.victory import VICTORY_REASON, finished_message, make_victory_finalizer


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _active_match(manager: MatchManager) -> str:
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token, True)
    manager.set_ready(joined.session_token, True)
    return created.match_id


async def test_victory_event_finishes_match_and_broadcasts_finished() -> None:
    finished: list[str] = []
    manager = MatchManager(world=load_standard_world(), on_match_finish=lambda m: finished.append(m.match_id))
    match_id = _active_match(manager)
    match = manager.get_match(match_id)
    assert match.state is MatchRuntimeState.ACTIVE

    registry = ConnectionRegistry()
    socket_p1, socket_p2 = _FakeSocket(), _FakeSocket()
    registry.register(match_id, "p1", socket_p1)  # type: ignore[arg-type]
    registry.register(match_id, "p2", socket_p2)  # type: ignore[arg-type]
    observer = make_victory_finalizer(manager, registry, match)

    assert match.game_state is not None
    await observer(match.game_state, ())
    assert match.state is MatchRuntimeState.ACTIVE
    assert match.result is None
    assert socket_p1.sent == []

    victory = VictoryEvent(sequence=EventSequencer().next_sequence(), winner=PLAYER_TWO, tick=1234)
    await observer(match.game_state, (victory,))

    assert match.state is MatchRuntimeState.FINISHED
    assert finished == [match_id]
    assert match.result is not None
    assert match.result.outcome is MatchOutcome.VICTORY
    assert match.result.reason == VICTORY_REASON
    assert match.result.winner_player_id == PLAYER_TWO
    assert match.result.decided_tick == 1234
    expected = {
        "protocolVersion": 1,
        "type": "finished",
        "matchId": match_id,
        "winnerPlayerId": "p2",
        "tick": 1234,
    }
    assert socket_p1.sent == [expected]
    assert socket_p2.sent == [expected]

    # Idempotent: a second victory event on an already-finished match is ignored.
    await observer(match.game_state, (victory,))
    assert socket_p1.sent == [expected]

    # And the durable result reproduces the same frame for a later reconnect.
    replayed = finished_message(match)
    assert replayed is not None
    assert json.loads(serialize_server_message(replayed)) == expected


def test_finished_message_is_none_for_undecided_or_forfeit_results() -> None:
    manager = MatchManager()
    match = manager.get_match(_active_match(manager))
    assert finished_message(match) is None
    manager.finish_match(match.match_id)
    assert finished_message(match) is None
