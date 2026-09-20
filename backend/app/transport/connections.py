"""Per-match WebSocket connection registry and broadcast primitives.

Scope (M7 Task 5, issue #94): tracks which live ``WebSocket`` belongs to
which ``(match_id, player_id)`` pair, and provides the minimal send/broadcast
primitives ``app.transport.ws`` needs. This module owns no gameplay or
lifecycle logic and builds no snapshot/event content -- it only knows how to
address a match's sockets (Task 6 builds real snapshot/event payloads on top
of :func:`broadcast`/:func:`send_to_player`).

Isolation guarantee: :meth:`ConnectionRegistry.connections_for` only ever
returns sockets registered under the exact ``match_id`` requested, so a
message can never be broadcast to a different match's connections -- there
is no global socket set, only per-match dictionaries.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

from app.protocol import OutboundMessage, serialize_server_message

logger = logging.getLogger(__name__)


class ConnectionRegistry:
    """Owns the live ``WebSocket`` per ``(match_id, player_id)`` pair.

    Not thread-safe beyond what a single asyncio event loop already
    guarantees (no ``await`` occurs between reading and mutating the
    underlying dicts in any method here), matching every other in-process
    match data structure in this codebase.
    """

    def __init__(self) -> None:
        self._by_match: dict[str, dict[str, WebSocket]] = {}

    def register(self, match_id: str, player_id: str, websocket: WebSocket) -> None:
        """Associate ``websocket`` with ``(match_id, player_id)``.

        A second registration for the same pair (e.g. a reconnect replacing
        a stale connection) silently replaces the previous socket -- this
        registry does not itself close the old one; the caller (the
        WebSocket handler) owns that connection's lifecycle.
        """
        self._by_match.setdefault(match_id, {})[player_id] = websocket

    def unregister(self, match_id: str, player_id: str, websocket: WebSocket) -> bool:
        """Remove ``websocket`` from ``(match_id, player_id)``, if it is still current.

        A no-op if ``player_id``'s current socket is not ``websocket`` (e.g.
        this call is a stale connection's cleanup racing a newer reconnect
        that already replaced it) -- cleanup must never evict a *newer*
        connection than the one it belongs to.

        Returns ``True`` only if ``websocket`` was in fact the currently
        registered connection for ``(match_id, player_id)`` and was removed;
        ``False`` otherwise (already gone, or superseded by a newer
        connection). Callers that gate a once-per-connection side effect
        (e.g. a disconnect notification) on "did my teardown actually win"
        must consult this return value -- see ``app.transport.ws``'s
        disconnect-notification path, which only fires when this is
        ``True``, precisely so a stale connection's delayed teardown can
        never report a spurious disconnect for a player who has already
        reconnected on a newer socket.
        """
        players = self._by_match.get(match_id)
        if players is None:
            return False
        removed = players.get(player_id) is websocket
        if removed:
            del players[player_id]
        if not players:
            self._by_match.pop(match_id, None)
        return removed

    def connections_for(self, match_id: str) -> tuple[WebSocket, ...]:
        """Return every socket currently registered for ``match_id``."""
        return tuple(self._by_match.get(match_id, {}).values())

    def connection_for_player(self, match_id: str, player_id: str) -> WebSocket | None:
        """Return ``player_id``'s current socket in ``match_id``, if connected."""
        return self._by_match.get(match_id, {}).get(player_id)


async def send_to_player(
    registry: ConnectionRegistry, match_id: str, player_id: str, message: OutboundMessage
) -> None:
    """Send ``message`` to exactly one player's connection, if it exists.

    A no-op if that player has no live connection (nothing to notify) or if
    the send itself fails (e.g. the peer is mid-disconnect) -- either case
    is handled by this task's disconnect notification path, not by this
    function raising.
    """
    websocket = registry.connection_for_player(match_id, player_id)
    if websocket is None:
        return
    await _send(websocket, message)


async def broadcast(registry: ConnectionRegistry, match_id: str, message: OutboundMessage) -> None:
    """Send ``message`` to every connection registered for ``match_id``.

    Never touches any other match's connections -- see
    :meth:`ConnectionRegistry.connections_for`.
    """
    for websocket in registry.connections_for(match_id):
        await _send(websocket, message)


async def _send(websocket: WebSocket, message: OutboundMessage) -> None:
    text = serialize_server_message(message)
    try:
        await websocket.send_text(text)
    except Exception:
        logger.debug("dropping send to a closed/closing websocket connection", exc_info=True)
