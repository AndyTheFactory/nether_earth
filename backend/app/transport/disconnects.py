"""``app.match.reconnect.DisconnectEvent`` -> protocol-message mapping and broadcast.

Scope: a thin field-mapping layer, exactly mirroring
``app.transport.snapshots``'s relationship to ``app.match.runtime``'s
``TickObserver``. ``app.match.reconnect`` never imports ``app.transport``
(AGENTS.md); this module supplies the concrete
``app.match.reconnect.DisconnectNotifier`` the transport layer installs on a
``ReconnectCoordinator`` (see ``app.main``) so that package never has to know
``ServerPaused``/``ServerResumed``/``ServerForfeit``/``ServerNoContest`` or
``ConnectionRegistry``/``broadcast`` exist.
"""

from __future__ import annotations

from app.match.reconnect import (
    DisconnectEvent,
    DisconnectNotifier,
    ForfeitEvent,
    NoContestEvent,
    PausedEvent,
    ResumedEvent,
)
from app.protocol.common import PROTOCOL_VERSION
from app.protocol.server_messages import (
    ServerForfeit,
    ServerNoContest,
    ServerPaused,
    ServerResumed,
)
from app.transport.connections import ConnectionRegistry, broadcast


def make_disconnect_notifier(connection_registry: ConnectionRegistry) -> DisconnectNotifier:
    """Return a ``DisconnectNotifier`` that broadcasts ``event`` to its match's connections.

    ``connection_registry`` is bound once at app-construction time (see
    ``app.main``) via closure, matching ``make_tick_broadcaster``'s own
    style -- one shared registry, no per-match rebuilding.
    """

    async def _notify(event: DisconnectEvent) -> None:
        match_id = event.match.match_id
        if isinstance(event, PausedEvent):
            await broadcast(
                connection_registry,
                match_id,
                ServerPaused(
                    protocol_version=PROTOCOL_VERSION,
                    type="paused",
                    match_id=match_id,
                    disconnected_player_id=event.disconnected_player_id.value,
                    grace_deadline_ms=event.grace_deadline_epoch_ms,
                ),
            )
        elif isinstance(event, ResumedEvent):
            game_state = event.match.game_state
            # `ReconnectCoordinator` only ever resumes a match that already
            # went ACTIVE (the only way to reach `PAUSED_DISCONNECTED` in
            # the first place), so `game_state` is guaranteed non-None here
            # -- see `app.match.manager._start_match_locked`.
            if game_state is None:
                raise RuntimeError(
                    f"match {match_id!r} resumed with no game_state; this should be unreachable"
                )
            await broadcast(
                connection_registry,
                match_id,
                ServerResumed(
                    protocol_version=PROTOCOL_VERSION,
                    type="resumed",
                    match_id=match_id,
                    tick=game_state.tick,
                ),
            )
        elif isinstance(event, ForfeitEvent):
            await broadcast(
                connection_registry,
                match_id,
                ServerForfeit(
                    protocol_version=PROTOCOL_VERSION,
                    type="forfeit",
                    match_id=match_id,
                    forfeiting_player_id=event.forfeiting_player_id.value,
                    winner_player_id=event.winner_player_id.value,
                    reason="disconnect_timeout",
                ),
            )
        elif isinstance(event, NoContestEvent):
            await broadcast(
                connection_registry,
                match_id,
                ServerNoContest(
                    protocol_version=PROTOCOL_VERSION,
                    type="no_contest",
                    match_id=match_id,
                    reason="disconnect_timeout_both",
                ),
            )
        else:  # pragma: no cover - exhaustive union guarded for future additions.
            raise TypeError(f"unhandled DisconnectEvent variant: {event!r}")

    return _notify
