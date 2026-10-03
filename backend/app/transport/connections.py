"""Per-match WebSocket connection registry and broadcast primitives.

Scope: tracks which live ``WebSocket`` belongs to
which ``(match_id, player_id)`` pair, and provides the minimal send/broadcast
primitives ``app.transport.ws`` needs. This module owns no gameplay or
lifecycle logic and builds no snapshot/event content -- it only knows how to
address a match's sockets (``app.transport.snapshots`` builds real
snapshot/event payloads on top of :func:`broadcast`/:func:`send_to_player`).

Isolation guarantee: :meth:`ConnectionRegistry.connections_for` only ever
returns sockets registered under the exact ``match_id`` requested, so a
message can never be broadcast to a different match's connections -- there
is no global socket set, only per-match dictionaries.
"""

from __future__ import annotations

import asyncio
import logging
import weakref

from fastapi import WebSocket

from app.protocol import OutboundMessage, serialize_server_message
from app.transport.limits import SEND_TIMEOUT_S

logger = logging.getLogger(__name__)

#: Sockets whose send timed out. Skipped by every later send
#: and closed in the background; the connection's own handler then sees the
#: close and runs the normal disconnect -> pause -> grace policy. Weak, so a
#: finished connection never lingers here.
_stalled: weakref.WeakSet[WebSocket] = weakref.WeakSet()
_background: set[asyncio.Task[None]] = set()


class ConnectionRegistry:
    """Owns the live ``WebSocket`` per ``(match_id, player_id)`` pair.

    Not thread-safe beyond what a single asyncio event loop already
    guarantees (no ``await`` occurs between reading and mutating the
    underlying dicts in any method here), matching every other in-process
    match data structure in this codebase.
    """

    def __init__(self) -> None:
        self._by_match: dict[str, dict[str, WebSocket]] = {}

    def register(self, match_id: str, player_id: str, websocket: WebSocket) -> WebSocket | None:
        """Associate ``websocket`` with ``(match_id, player_id)``.

        Returns the socket this registration replaced (a reconnect taking
        over a slot), or ``None`` if the slot was empty or already held
        this same socket. The caller owns closing the replaced socket; this
        registry never performs I/O.
        """
        players = self._by_match.setdefault(match_id, {})
        previous = players.get(player_id)
        players[player_id] = websocket
        return previous if previous is not websocket else None

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

    def connection_count(self) -> int:
        """Total registered sockets across all matches (readiness/soak diagnostics)."""
        return sum(len(players) for players in self._by_match.values())

    def connection_for_player(self, match_id: str, player_id: str) -> WebSocket | None:
        """Return ``player_id``'s current socket in ``match_id``, if connected."""
        return self._by_match.get(match_id, {}).get(player_id)


async def send_to_player(
    registry: ConnectionRegistry, match_id: str, player_id: str, message: OutboundMessage
) -> None:
    """Send ``message`` to exactly one player's connection, if it exists.

    A no-op if that player has no live connection (nothing to notify) or if
    the send itself fails (e.g. the peer is mid-disconnect) -- either case
    is handled by the transport's disconnect notification path, not by this
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
    connections = registry.connections_for(match_id)
    if not connections:
        return
    text = serialize_server_message(message)
    for websocket in connections:
        await _send_text(websocket, text)


async def _send(websocket: WebSocket, message: OutboundMessage) -> None:
    await _send_text(websocket, serialize_server_message(message))


async def _send_text(websocket: WebSocket, text: str) -> None:
    """Send ``text``; never raise, and never block the caller past ``SEND_TIMEOUT_S``.

    Callers include each match's tick loop (snapshot broadcast), so a peer
    that stops reading must not be able to freeze the match for its
    opponent: a timed-out socket is marked stalled and closed instead.
    """
    if websocket in _stalled:
        return
    try:
        await asyncio.wait_for(websocket.send_text(text), SEND_TIMEOUT_S)
    except TimeoutError:
        _stalled.add(websocket)
        logger.warning(
            "closing stalled websocket: a send blocked for over %ss",
            SEND_TIMEOUT_S,
            extra={"event": "ws_send_stalled"},
        )
        task = asyncio.get_running_loop().create_task(_close_quietly(websocket))
        _background.add(task)
        task.add_done_callback(_background.discard)
    except Exception:
        logger.debug("dropping send to a closed/closing websocket connection", exc_info=True)


async def _close_quietly(websocket: WebSocket) -> None:
    try:
        await asyncio.wait_for(websocket.close(code=1011), SEND_TIMEOUT_S)
    except Exception:
        logger.debug("close of stalled websocket failed", exc_info=True)
