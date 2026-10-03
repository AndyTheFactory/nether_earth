"""Per-socket state for one ``/ws`` connection and the primitives handlers use.

A connection has no bound session until its first successful
``create``/``join``/``ready``/``leave``/``command``/``reconnect`` message
authenticates one; from then on it is pinned to that
``(match_id, player_id, session_token)`` triple for its lifetime.

Disconnect notification: ``MatchManager.mark_disconnected`` is called only
through :meth:`Connection.teardown`, which the endpoint runs from exactly one
``finally`` block and the ``leave`` handler runs again (safely: the second
call is a no-op). ``teardown`` first unregisters this socket from
``ConnectionRegistry`` and notifies only if that unregister reports this
socket was still the one registered for its ``(match_id, player_id)`` slot.
A stale connection's delayed teardown (e.g. slow TCP close) therefore never
reports a spurious disconnect for a player who has since reconnected on a
newer socket -- which matters because ``mark_disconnected`` drives the
pause/grace-timer policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.match.manager import MatchManager
from app.match.runtime import MatchRuntimeRegistry
from app.protocol import OutboundMessage, serialize_server_message
from app.protocol.common import PROTOCOL_VERSION, ErrorInfo
from app.protocol.server_messages import ServerError
from app.transport.connections import ConnectionRegistry
from app.transport.limits import POLICY_VIOLATION_CLOSE_CODE, REPLACED_CLOSE_CODE

logger = logging.getLogger(__name__)


class _Default(Enum):
    BOUND = auto()


#: ``match_id`` default for error messages: the bound session's match, if any.
BOUND_MATCH = _Default.BOUND


@dataclass(slots=True)
class BoundSession:
    """The one session a connection is pinned to, once authenticated."""

    match_id: str
    player_id: str
    session_token: str


@dataclass(slots=True, eq=False)
class Connection:
    """One accepted ``/ws`` socket, its bound session, and its join-failure count."""

    websocket: WebSocket
    match_manager: MatchManager
    runtime_registry: MatchRuntimeRegistry
    connection_registry: ConnectionRegistry
    bound: BoundSession | None = None
    failed_joins: int = 0
    _disconnect_notified: bool = False

    @property
    def match_id(self) -> str | None:
        return self.bound.match_id if self.bound else None

    async def send(self, message: OutboundMessage) -> None:
        await self.websocket.send_text(serialize_server_message(message))

    async def send_error(
        self,
        code: str,
        detail: str,
        *,
        match_id: str | None | Literal[_Default.BOUND] = BOUND_MATCH,
    ) -> None:
        """Send a ``ServerError``; a send to a closed/closing socket is dropped."""
        message = ServerError(
            protocol_version=PROTOCOL_VERSION,
            type="error",
            match_id=self.match_id if match_id is BOUND_MATCH else match_id,
            error=ErrorInfo(code=code, message=detail),
        )
        try:
            await self.send(message)
        except Exception:
            logger.debug(
                "dropping error send to a closed/closing websocket connection", exc_info=True
            )

    async def reject_and_close(
        self,
        code: str,
        detail: str = "session rejected; closing connection",
        close_code: int = POLICY_VIOLATION_CLOSE_CODE,
        *,
        match_id: str | None | Literal[_Default.BOUND] = BOUND_MATCH,
    ) -> None:
        await self.send_error(code, detail, match_id=match_id)
        await self.close(close_code)

    async def close(self, code: int) -> None:
        await _close(self.websocket, code)

    def bind(self, match_id: str, player_id: str, session_token: str) -> None:
        if self.bound is not None:
            raise RuntimeError("connection is already bound to a session")
        self.bound = BoundSession(
            match_id=match_id, player_id=player_id, session_token=session_token
        )

    async def attach(self) -> None:
        """Make this socket the live one for the bound session; close any predecessor."""
        bound = self._require_bound()
        replaced = self.connection_registry.register(
            bound.match_id, bound.player_id, self.websocket
        )
        self.match_manager.mark_lobby_occupied(bound.match_id)
        if replaced is not None:
            await _close(replaced, REPLACED_CLOSE_CODE)

    async def teardown(self) -> None:
        """Unregister this socket and notify disconnect iff it was still current.

        ``ConnectionRegistry.unregister`` returns ``True`` only if this socket
        was in fact the one registered for the bound ``(match_id,
        player_id)`` slot. If a newer connection already took that slot (a
        delayed teardown racing a ``reconnect`` that rebound the player
        elsewhere), this is a silent no-op: the newer connection is still
        live and must not have a spurious disconnect reported for it.
        Idempotent -- a second call finds nothing left to unregister.
        """
        bound = self.bound
        if bound is None:
            return
        removed = self.connection_registry.unregister(
            bound.match_id, bound.player_id, self.websocket
        )
        if removed:
            self._notify_disconnect_once()
            if not self.connection_registry.connections_for(bound.match_id):
                # Nobody is attached any more. A no-op unless the match is
                # still WAITING: abandoned lobbies must not hold capacity for
                # the whole waiting timeout.
                self.match_manager.mark_lobby_abandoned(bound.match_id)

    def _notify_disconnect_once(self) -> None:
        if self._disconnect_notified or self.bound is None:
            return
        self._disconnect_notified = True
        self.match_manager.mark_disconnected(self.bound.session_token)

    def _require_bound(self) -> BoundSession:
        if self.bound is None:
            raise RuntimeError("connection has no bound session")
        return self.bound


async def _close(websocket: WebSocket, code: int) -> None:
    """Close ``websocket`` unless it is already closed/closing; never raises.

    A concurrent broadcast (e.g. the opponent's ``paused`` notification)
    can hit a peer that just went away, which marks the socket disconnected
    before this handler gets to close it.
    """
    if websocket.application_state is not WebSocketState.CONNECTED:
        return
    try:
        await websocket.close(code=code)
    except Exception:
        logger.debug("websocket close failed (already closing)", exc_info=True)
