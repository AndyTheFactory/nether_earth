"""WebSocket transport: the ``/ws`` endpoint and its receive loop.

Endpoint design: a single ``/ws`` endpoint (no ``{match_id}`` path segment),
not one endpoint per match. Two of the six inbound message shapes
(``create``/``join``, `client_messages.schema.json`) are sent *before* a
session token -- or, for ``create``, even a ``match_id`` -- exists, so a path
parameter cannot identify them. Every other inbound message
(``ready``/``leave``/``command``/``reconnect``) already carries its own
``matchId``/``sessionToken``/``playerId`` in the schema itself. A single
endpoint that authenticates from each message's own envelope (rather than
from the URL) avoids a second, redundant place to carry the same identity,
and lets one connection go create-or-join -> ready -> command over its own
lifetime. Session binding and per-message handling live in
``app.transport.handlers``; per-socket state in ``app.transport.connection``.

Structurally invalid frames (bad JSON, wrong/missing `type`, schema
violations -- anything `parse_client_message` rejects with
`pydantic.ValidationError`) get a schema-valid `ServerError` back but the
connection is *not* closed: a single malformed frame is not proof of a
compromised/mistaken identity the way a bad session token is -- the client
may simply have a bug worth surfacing, and closing on every typo would make
the protocol needlessly fragile. Either way, no invalid/unauthenticated
message ever reaches `MatchManager` or `MatchRuntimeRegistry.submit_command`
(and therefore never reaches `engine.step`).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.match.manager import MatchManager
from app.match.runtime import MatchRuntimeRegistry
from app.protocol import ValidationError, parse_client_message
from app.transport.connection import Connection
from app.transport.connections import ConnectionRegistry
from app.transport.handlers import Outcome, dispatch
from app.transport.limits import (
    INTERNAL_ERROR_CLOSE_CODE,
    MAX_MESSAGE_BYTES,
    POLICY_VIOLATION_CLOSE_CODE,
    TOO_BIG_CLOSE_CODE,
    TokenBucket,
)

logger = logging.getLogger(__name__)

#: Seconds an accepted socket may stay without a bound session (no
#: successful create/join/ready/leave/command/reconnect) before it is
#: closed. Bounds idle unauthenticated sockets independently of the
#: gateway's proxy_read_timeout.
UNBOUND_SOCKET_TIMEOUT_S = 30.0


def create_websocket_router(
    match_manager: MatchManager,
    runtime_registry: MatchRuntimeRegistry,
    connection_registry: ConnectionRegistry,
    *,
    allowed_origins: frozenset[str] = frozenset(),
) -> APIRouter:
    """Build the ``/ws`` router bound to one set of match/runtime/connection stores.

    ``allowed_origins``: when non-empty, a handshake whose ``Origin``
    header is present and not in this set is refused before ``accept`` (the
    client sees HTTP 403), which blocks cross-site WebSocket hijacking from
    other web pages. Browsers always send ``Origin``; non-browser clients
    (smoke/soak tools) may omit it and are not cross-site attack vectors.

    A factory (rather than a module-level route) so ``app.main.create_app``
    can give every app instance its own isolated ``MatchManager`` /
    ``MatchRuntimeRegistry`` / ``ConnectionRegistry`` -- important for tests,
    which must not leak matches or connections across independent app
    instances.
    """
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if allowed_origins and origin is not None and origin.lower() not in allowed_origins:
            logger.warning(
                "websocket handshake refused: origin not allowed",
                extra={"event": "ws_origin_refused", "origin": origin},
            )
            await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
            return
        await websocket.accept()
        conn = Connection(websocket, match_manager, runtime_registry, connection_registry)
        bind_deadline = asyncio.get_running_loop().time() + UNBOUND_SOCKET_TIMEOUT_S
        bucket = TokenBucket()

        try:
            while True:
                raw = await _receive(conn, bind_deadline)
                if raw is None:
                    return

                current_match_id = conn.match_id
                if not bucket.allow():
                    logger.warning(
                        "closing websocket: inbound message rate limit exceeded",
                        extra={"event": "ws_rate_limited", "match_id": current_match_id},
                    )
                    await conn.reject_and_close("rate_limited", "too many messages; closing")
                    return
                if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await conn.reject_and_close(
                        "message_too_large",
                        f"messages are limited to {MAX_MESSAGE_BYTES} bytes; closing",
                        TOO_BIG_CLOSE_CODE,
                    )
                    return

                try:
                    message = parse_client_message(raw)
                except ValidationError as exc:
                    await conn.send_error(
                        "invalid_message",
                        f"message failed protocol validation: {exc.error_count()} error(s)",
                    )
                    continue

                if await dispatch(conn, message) is Outcome.CLOSED:
                    return
        except WebSocketDisconnect:
            pass
        except Exception:
            # Fail safe: log with the match id for correlation (never the
            # session token), tell the client nothing internal, close 1011.
            logger.exception(
                "websocket handler failed",
                extra={"event": "ws_handler_failed", "match_id": conn.match_id},
            )
            await conn.close(INTERNAL_ERROR_CLOSE_CODE)
        finally:
            await conn.teardown()

    return router


async def _receive(conn: Connection, bind_deadline: float) -> str | None:
    """Next text frame, or ``None`` once the peer is gone or the bind deadline closed it.

    Only an unbound socket is subject to ``bind_deadline`` (absolute, in
    event-loop time); a bound one waits indefinitely.
    """
    try:
        if conn.bound is None:
            remaining = bind_deadline - asyncio.get_running_loop().time()
            return await asyncio.wait_for(conn.websocket.receive_text(), max(remaining, 0.0))
        return await conn.websocket.receive_text()
    except WebSocketDisconnect:
        return None
    except TimeoutError:
        logger.info(
            "closing websocket: no session bound before the deadline",
            extra={"event": "ws_bind_timeout"},
        )
        await conn.reject_and_close(
            "bind_timeout", "no session bound in time; closing", match_id=None
        )
        return None
