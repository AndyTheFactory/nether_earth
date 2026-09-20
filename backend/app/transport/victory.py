"""Engine victory -> match ``FINISHED`` + ``finished`` broadcast (M7 gap fixed in M9.1).

``engine.step`` announces the v1 victory rule as a
``nether_earth.victory.VictoryEvent`` and deliberately carries no "match
over" state (see that module). Something in the runtime layer must therefore
observe the event, end the match lifecycle, and tell the clients -- this
module is that something, in the same ``TickObserver`` shape
``app.transport.snapshots.make_tick_broadcaster`` uses, composed after it in
``app.main`` so the final authoritative snapshot always precedes ``finished``.

Ordering inside the observer matters: the ``finished`` frame is sent
*before* ``MatchManager.finish_match`` because that call cancels the tick
loop task this observer runs in (``MatchRuntime.request_cancel``), and a
self-cancelled task cannot rely on a later ``await`` completing.
"""

from __future__ import annotations

from nether_earth.events import Event
from nether_earth.state import GameState
from nether_earth.victory import VictoryEvent

from app.match.manager import MatchManager
from app.match.models import Match, MatchOutcome, MatchResult, MatchRuntimeState
from app.match.runtime import TickObserver
from app.protocol.common import PROTOCOL_VERSION
from app.protocol.server_messages import ServerFinished
from app.transport.connections import ConnectionRegistry, broadcast

VICTORY_REASON = "zero_war_bases"


def finished_message(match: Match) -> ServerFinished | None:
    """Return the ``finished`` frame for a match decided by engine victory, else ``None``."""
    result = match.result
    if result is None or result.outcome is not MatchOutcome.VICTORY:
        return None
    assert result.winner_player_id is not None
    assert result.decided_tick is not None
    return ServerFinished(
        protocol_version=PROTOCOL_VERSION,
        type="finished",
        match_id=match.match_id,
        winner_player_id=result.winner_player_id.value,
        tick=result.decided_tick,
    )


def make_victory_finalizer(
    match_manager: MatchManager, connection_registry: ConnectionRegistry, match: Match
) -> TickObserver:
    async def _on_tick(state: GameState, events: tuple[Event, ...]) -> None:
        del state
        victory = next((event for event in events if isinstance(event, VictoryEvent)), None)
        if victory is None or match.state is MatchRuntimeState.FINISHED:
            return
        match.result = MatchResult(
            outcome=MatchOutcome.VICTORY,
            reason=VICTORY_REASON,
            winner_player_id=victory.winner,
            decided_tick=victory.tick,
        )
        message = finished_message(match)
        assert message is not None
        await broadcast(connection_registry, match.match_id, message)
        match_manager.finish_match(match.match_id)

    return _on_tick
