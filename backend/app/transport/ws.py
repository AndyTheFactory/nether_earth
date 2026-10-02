"""WebSocket transport: session association and message routing (M7 Task 5, issue #94).

Endpoint design: a single ``/ws`` endpoint (no ``{match_id}`` path segment),
not one endpoint per match. Rationale: two of the six inbound message
shapes (``create``/``join``, `client_messages.schema.json`) are sent *before*
a session token -- or, for ``create``, even a ``match_id`` -- exists, so a
path parameter cannot identify them. Every other inbound message
(``ready``/``leave``/``command``/``reconnect``) already carries its own
``matchId``/``sessionToken``/``playerId`` in the schema itself. A single
endpoint that authenticates from each message's own envelope (rather than
from the URL) avoids inventing a second, redundant place to carry the same
identity, and is exactly what lets one connection go
create-or-join -> ready -> command over its own lifetime.

Session binding rule (this task's "associate each connection with exactly
one session" requirement): a connection has no bound session until its first
successful ``create``/``join``/``ready``/``leave``/``command``/``reconnect``
message authenticates one. From that point on the connection is pinned to
that ``(match_id, player_id, session_token)`` triple for its lifetime --
any later message that resolves to a *different* session (wrong token, or a
token for a different match/player than the one already bound) is rejected
with a schema-valid `ServerError` and the connection is closed with close
code 1008 (policy violation). A single bad/unknown token on an
as-yet-unbound connection is treated the same way: reject + close, since
there is nothing legitimate a socket that failed its very first auth check
could still be trusted to do.

Structurally invalid frames (bad JSON, wrong/missing `type`, schema
violations -- anything `parse_client_message` rejects with
`pydantic.ValidationError`) are handled differently: they get a
schema-valid `ServerError` back but the connection is *not* closed, since a
single malformed frame is not a proof of a compromised/mistaken identity the
way a bad session token is -- the client may simply have a bug worth
surfacing, and closing on every typo would make the protocol needlessly
fragile. Either way, no invalid/unauthenticated message ever reaches
`MatchManager` or `MatchRuntimeRegistry.submit_command` (and therefore never
reaches `engine.step`).

Gameplay-command-conversion scope: `ClientGameplayCommand.payload` is the
real, fully enumerated `CommandPayload` discriminated union (issue #98; see
`app/protocol/common.py`). This module converts it to the exact matching
concrete `nether_earth.commands.Command` subclass via
`app.transport.commands.payload_to_command` -- a pure field-shape adapter
that makes no gameplay legality decision (see that module's own docstring)
-- before handing it to `MatchRuntimeRegistry.submit_command`. A payload
that is schema-valid but structurally malformed for its target `Command`
(`CommandPayloadError`; e.g. a diagonal move shape JSON Schema's per-axis
`cellDelta` cannot itself rule out) is rejected the same way a JSON Schema
violation is: a `ServerError`, no `MatchRuntimeRegistry`/`engine.step`
involvement at all.

Disconnect notification: `MatchManager.mark_disconnected` (added by Task 5)
is called through a single `teardown_connection()` helper, itself invoked
from exactly one `finally` block plus (redundantly-but-safely, via the same
helper) the `leave` handler, regardless of whether the connection ends via a
clean `leave` message, a WebSocket close/error, or an auth rejection after a
session was already bound. `teardown_connection()` first unregisters this
socket from `ConnectionRegistry` and only notifies if that unregister
reports this socket was still the one currently registered for its
`(match_id, player_id)` slot -- this closes a race where a stale
connection's delayed teardown (e.g. slow TCP close) could otherwise fire a
spurious disconnect notification for a player who has since reconnected on
a newer socket (`ConnectionRegistry.unregister`'s return value exists
specifically to make that race detectable). `mark_disconnected` now drives
the real pause/grace-timer policy (`app.match.reconnect.ReconnectCoordinator`,
M7 Task 7, issue #96), which is exactly why this race matters -- a spurious
notification here would otherwise pause/grace-timer a player who never
actually disconnected. The `reconnect` message handler below calls the
symmetric `MatchManager.mark_reconnected` to cancel that grace timer and,
once both players are connected again, resume the match.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.match.manager import MatchManager
from app.match.models import (
    InvalidNicknameError,
    InvalidSessionTokenError,
    Match,
    MatchFullError,
    MatchNotFoundError,
    MatchOutcome,
    MatchRuntimeState,
    ServerBusyError,
)
from app.match.runtime import MatchRuntimeRegistry
from app.protocol import ValidationError, parse_client_message, serialize_server_message
from app.protocol.client_messages import (
    ClientCreateMatch,
    ClientGameplayCommand,
    ClientJoinMatch,
    ClientLeaveMatch,
    ClientSetReady,
)
from app.protocol.common import PROTOCOL_VERSION, ErrorInfo, PlayerSummary
from app.protocol.reconnect import ClientReconnect, ServerResync
from app.protocol.server_messages import (
    ServerCreated,
    ServerError,
    ServerForfeit,
    ServerJoined,
    ServerNoContest,
    ServerReadyState,
    ServerStarted,
)
from app.protocol.snapshot import SnapshotMessage
from app.transport.commands import CommandPayloadError, payload_to_command
from app.transport.connections import ConnectionRegistry, broadcast
from app.transport.limits import MAX_FAILED_JOINS, MAX_MESSAGE_BYTES, TokenBucket
from app.transport.snapshots import build_snapshot_message, empty_snapshot_message
from app.transport.victory import finished_message

logger = logging.getLogger(__name__)

#: Close code sent for every authentication/session-identity violation
#: (missing, invalid, or wrong-match/wrong-player session token, or a second
#: create/join attempt on an already-bound connection). 1008 = "Policy
#: Violation" per RFC 6455 -- the closest standard code for "you are not who
#: you claimed to be for this connection".
_POLICY_VIOLATION_CLOSE_CODE = 1008

#: Close code sent after a client-initiated, well-formed `leave` message.
_NORMAL_CLOSE_CODE = 1000

#: RFC 6455 "Message Too Big" / "Internal Error" close codes.
_TOO_BIG_CLOSE_CODE = 1009
_INTERNAL_ERROR_CLOSE_CODE = 1011
#: Close code sent to a socket superseded by a newer connection for the same
#: session (RFC 6455 reserves 4000-4999 for applications). The holder of a
#: token gets exactly one live socket, so a leaked token cannot be used in
#: parallel with its owner unnoticed.
_REPLACED_CLOSE_CODE = 4000


@dataclass(slots=True)
class _BoundSession:
    """The one session a connection is pinned to, once authenticated."""

    match_id: str
    player_id: str
    session_token: str


def create_websocket_router(
    match_manager: MatchManager,
    runtime_registry: MatchRuntimeRegistry,
    connection_registry: ConnectionRegistry,
    *,
    allowed_origins: frozenset[str] = frozenset(),
) -> APIRouter:
    """Build the ``/ws`` router bound to one set of match/runtime/connection stores.

    ``allowed_origins`` (M10.4): when non-empty, a handshake whose ``Origin``
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
            await websocket.close(code=_POLICY_VIOLATION_CLOSE_CODE)
            return
        await websocket.accept()
        bound: _BoundSession | None = None
        bucket = TokenBucket()
        failed_joins = 0
        disconnect_notified = False

        async def notify_disconnect_once() -> None:
            nonlocal disconnect_notified
            if disconnect_notified or bound is None:
                return
            disconnect_notified = True
            match_manager.mark_disconnected(bound.session_token)

        async def teardown_connection() -> None:
            """Unregister this socket and notify disconnect iff it was still current.

            ``ConnectionRegistry.unregister`` returns ``True`` only if
            ``websocket`` was in fact the connection currently registered
            for ``bound``'s ``(match_id, player_id)`` slot. If a newer
            connection already took over that slot (e.g. this socket's
            teardown is a delayed/stale one racing a `reconnect` that
            already rebound the player elsewhere), this is a silent no-op:
            the newer connection is still live and must not have a spurious
            disconnect reported for it. Idempotent -- safe to call more than
            once for the same connection (a second call finds nothing left
            to unregister and reports ``False``).
            """
            if bound is None:
                return
            removed = connection_registry.unregister(bound.match_id, bound.player_id, websocket)
            if removed:
                await notify_disconnect_once()

        async def _reject_and_close(
            ws: WebSocket,
            match_id: str | None,
            code: str,
            detail: str = "session rejected; closing connection",
            close_code: int = _POLICY_VIOLATION_CLOSE_CODE,
        ) -> None:
            await _send_error(ws, match_id, code, detail)
            await _close(ws, close_code)

        async def attach(match_id: str, player_id: str) -> None:
            """Make this socket the live one for ``(match_id, player_id)``; close any predecessor."""
            replaced = connection_registry.register(match_id, player_id, websocket)
            if replaced is not None:
                await _close(replaced, _REPLACED_CLOSE_CODE)

        try:
            while True:
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    return

                current_match_id = bound.match_id if bound else None
                if not bucket.allow():
                    logger.warning(
                        "closing websocket: inbound message rate limit exceeded",
                        extra={"event": "ws_rate_limited", "match_id": current_match_id},
                    )
                    await _reject_and_close(
                        websocket, current_match_id, "rate_limited", "too many messages; closing"
                    )
                    return
                if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await _reject_and_close(
                        websocket,
                        current_match_id,
                        "message_too_large",
                        f"messages are limited to {MAX_MESSAGE_BYTES} bytes; closing",
                        _TOO_BIG_CLOSE_CODE,
                    )
                    return

                try:
                    message = parse_client_message(raw)
                except ValidationError as exc:
                    await _send_error(
                        websocket,
                        match_id=bound.match_id if bound else None,
                        code="invalid_message",
                        detail=f"message failed protocol validation: {exc.error_count()} error(s)",
                    )
                    continue

                if isinstance(message, ClientCreateMatch):
                    if bound is not None:
                        await _reject_and_close(websocket, bound.match_id, "already_bound")
                        return
                    solo = message.opponent == "computer"
                    try:
                        if solo:
                            solo_result = match_manager.create_solo_match(message.nickname)
                            join_code: str | None = None
                            create_player_id = solo_result.player_id
                            create_session_token = solo_result.session_token
                            create_match_id = solo_result.match_id
                        else:
                            result = match_manager.create_match(message.nickname)
                            join_code = result.join_code
                            create_player_id = result.player_id
                            create_session_token = result.session_token
                            create_match_id = result.match_id
                    except InvalidNicknameError as exc:
                        await _send_error(websocket, None, "invalid_nickname", str(exc))
                        continue
                    except ServerBusyError as exc:
                        await _send_error(websocket, None, "server_busy", str(exc))
                        continue
                    bound = _BoundSession(
                        match_id=create_match_id,
                        player_id=create_player_id.value,
                        session_token=create_session_token,
                    )
                    await attach(bound.match_id, bound.player_id)
                    await websocket.send_text(
                        serialize_server_message(
                            ServerCreated(
                                protocol_version=PROTOCOL_VERSION,
                                type="created",
                                match_id=bound.match_id,
                                join_code=join_code,
                                player_id=bound.player_id,
                                session_token=bound.session_token,
                                # `None` on the PvP path: `opponent` is
                                # dropped from the wire for a plain `create`
                                # (see `ServerCreated`'s docstring), keeping
                                # it byte-compatible with pre-CR004.8
                                # clients. Only a solo create states it.
                                opponent="computer" if solo else None,
                            )
                        )
                    )
                    continue

                if isinstance(message, ClientJoinMatch):
                    if bound is not None:
                        await _reject_and_close(websocket, bound.match_id, "already_bound")
                        return
                    try:
                        join_result = match_manager.join_match(message.join_code, message.nickname)
                    except (MatchNotFoundError, MatchFullError) as exc:
                        failed_joins += 1
                        if failed_joins >= MAX_FAILED_JOINS:
                            logger.warning(
                                "closing websocket: too many failed join attempts",
                                extra={"event": "ws_join_attempts_exceeded"},
                            )
                            await _reject_and_close(
                                websocket, None, "too_many_join_attempts", "too many failed joins"
                            )
                            return
                        code = "match_full" if isinstance(exc, MatchFullError) else "match_not_found"
                        await _send_error(websocket, None, code, str(exc))
                        continue
                    except InvalidNicknameError as exc:
                        await _send_error(websocket, None, "invalid_nickname", str(exc))
                        continue
                    bound = _BoundSession(
                        match_id=join_result.match_id,
                        player_id=join_result.player_id.value,
                        session_token=join_result.session_token,
                    )
                    await attach(bound.match_id, bound.player_id)
                    await websocket.send_text(
                        serialize_server_message(
                            ServerJoined(
                                protocol_version=PROTOCOL_VERSION,
                                type="joined",
                                match_id=join_result.match_id,
                                player_id=bound.player_id,
                                session_token=bound.session_token,
                            )
                        )
                    )
                    match = match_manager.get_match(bound.match_id)
                    await broadcast(
                        connection_registry, bound.match_id, _ready_state_message(match)
                    )
                    continue

                # Every remaining inbound variant (ready/leave/command/reconnect)
                # carries its own matchId/playerId/sessionToken; authenticate
                # and authorize it before any routing happens.
                try:
                    match, engine_player_id = match_manager.resolve_session(message.session_token)
                except InvalidSessionTokenError:
                    await _reject_and_close(
                        websocket, bound.match_id if bound else None, "invalid_session"
                    )
                    return

                if match.match_id != message.match_id or engine_player_id.value != message.player_id:
                    await _reject_and_close(websocket, match.match_id, "session_mismatch")
                    return

                if bound is None:
                    bound = _BoundSession(
                        match_id=match.match_id,
                        player_id=engine_player_id.value,
                        session_token=message.session_token,
                    )
                    await attach(bound.match_id, bound.player_id)
                elif (
                    bound.session_token != message.session_token
                    or bound.match_id != match.match_id
                ):
                    # A connection that already authenticated as one session
                    # must never be allowed to act as a different one.
                    await _reject_and_close(websocket, bound.match_id, "session_mismatch")
                    return

                if isinstance(message, ClientSetReady):
                    was_waiting = match.state is MatchRuntimeState.WAITING
                    match_manager.set_ready(message.session_token, ready=message.ready)
                    just_started = was_waiting and match.state is MatchRuntimeState.ACTIVE
                    # From `set_ready` onward, every `await` below is a point
                    # where this handler can be cancelled (client drops mid-
                    # broadcast). The match's `MatchRuntime` was started with
                    # `require_announcement=True` (see
                    # `MatchManager._start_match_locked`) and cannot tick until
                    # `announce_started` fires, so the `finally` must cover the
                    # *whole* window -- including the `ready_state` broadcast --
                    # or a cancellation there wedges an ACTIVE match forever.
                    try:
                        await broadcast(
                            connection_registry, match.match_id, _ready_state_message(match)
                        )
                        if just_started:
                            # `_start_match_locked` already ran `engine.new_game`
                            # synchronously before flipping `match.state` to
                            # ACTIVE, so `game_state` is guaranteed non-None
                            # here -- tick-0 authoritative state, read as-is,
                            # never advanced. A real exception (not `assert`,
                            # which `python -O` strips) because reaching this
                            # branch with no `game_state` is an engine-manager
                            # invariant violation, not a client-input error.
                            if match.game_state is None:
                                raise RuntimeError(
                                    f"match {match.match_id!r} transitioned to ACTIVE "
                                    "with no game_state"
                                )
                            await broadcast(
                                connection_registry,
                                match.match_id,
                                ServerStarted(
                                    protocol_version=PROTOCOL_VERSION,
                                    type="started",
                                    match_id=match.match_id,
                                    tick=match.game_state.tick,
                                ),
                            )
                            await broadcast(
                                connection_registry,
                                match.match_id,
                                build_snapshot_message(match.match_id, match.game_state),
                            )
                    finally:
                        if just_started:
                            runtime_registry.announce_started(match.match_id)
                    continue

                if isinstance(message, ClientLeaveMatch):
                    await teardown_connection()
                    await _close(websocket, _NORMAL_CLOSE_CODE)
                    return

                if isinstance(message, ClientGameplayCommand):
                    try:
                        command = payload_to_command(
                            message.payload, engine_player_id, message.client_sequence
                        )
                    except CommandPayloadError as exc:
                        await _send_error(
                            websocket,
                            match.match_id,
                            "invalid_command_payload",
                            f"command payload is structurally invalid for its command "
                            f"type: {exc}",
                        )
                        continue
                    accepted = await runtime_registry.submit_command(match.match_id, command)
                    if not accepted:
                        await _send_error(
                            websocket,
                            match.match_id,
                            "command_rejected",
                            "command not accepted (match not active, or a "
                            "duplicate/replayed sequence number)",
                        )
                    continue

                if isinstance(message, ClientReconnect):
                    await attach(match.match_id, engine_player_id.value)
                    # Idempotent: if the readying handler was cancelled before
                    # its own `announce_started` could fire, a reconnect must
                    # not leave the runtime's start gate closed forever. A
                    # WAITING match has no runtime yet, so skip it there.
                    if match.state is not MatchRuntimeState.WAITING:
                        runtime_registry.announce_started(match.match_id)
                    # Read `match.game_state` exactly as it stands -- never
                    # advance/mutate the engine merely to produce a
                    # reconnect snapshot. `game_state` is only `None` if the
                    # match has never gone ACTIVE (WAITING -> ACTIVE runs
                    # `engine.new_game` exactly once, synchronously, before
                    # any client can observe `MatchRuntimeState.ACTIVE`; see
                    # `app.match.manager._start_match_locked`), in which case
                    # there is no authoritative gameplay state yet to send.
                    snapshot: SnapshotMessage = (
                        build_snapshot_message(match.match_id, match.game_state)
                        if match.game_state is not None
                        else empty_snapshot_message(match.match_id)
                    )
                    await websocket.send_text(
                        serialize_server_message(
                            ServerResync(
                                protocol_version=PROTOCOL_VERSION,
                                type="resync",
                                match_id=match.match_id,
                                player_id=engine_player_id.value,
                                snapshot=snapshot,
                            )
                        )
                    )
                    # Cancels this player's reconnect-grace deadline watcher
                    # and, once both players are connected again, resumes
                    # the match (M7 Task 7, issue #96). A no-op if this
                    # player was never marked disconnected (e.g. a
                    # reconnect message on an already-connected session).
                    # Sequenced *after* the resync send above (M7 Task 7
                    # review, Minor M1) so this player's own resync is
                    # structurally guaranteed to precede any `resumed`
                    # broadcast a resulting resume might trigger, rather
                    # than relying on incidental ordering.
                    match_manager.mark_reconnected(message.session_token)

                    # A durable forfeit/no-contest result (M7 Task 7 review,
                    # Important I2): the winning side of a both-disconnected
                    # forfeit was, by construction, not connected to receive
                    # the live `ServerForfeit`/`ServerNoContest` broadcast --
                    # replay it to whoever reconnects to an already-decided
                    # match, using the existing message types (no protocol/
                    # schema change), addressed to this socket only (every
                    # other connection already saw it live).
                    match_result = match.result
                    finished = finished_message(match)
                    if finished is not None:
                        await websocket.send_text(serialize_server_message(finished))
                    elif match_result is not None:
                        if match_result.outcome is MatchOutcome.FORFEIT:
                            forfeiting = match_result.forfeiting_player_id
                            winner = match_result.winner_player_id
                            if forfeiting is None or winner is None:
                                # Invariant, not client input: a FORFEIT result
                                # always names both seats. A real exception
                                # (not `assert`, which `python -O` strips).
                                raise RuntimeError(
                                    f"match {match.match_id!r} has a FORFEIT result "
                                    "without both player ids"
                                )
                            await websocket.send_text(
                                serialize_server_message(
                                    ServerForfeit(
                                        protocol_version=PROTOCOL_VERSION,
                                        type="forfeit",
                                        match_id=match.match_id,
                                        forfeiting_player_id=forfeiting.value,
                                        winner_player_id=winner.value,
                                        reason="disconnect_timeout",
                                    )
                                )
                            )
                        else:
                            await websocket.send_text(
                                serialize_server_message(
                                    ServerNoContest(
                                        protocol_version=PROTOCOL_VERSION,
                                        type="no_contest",
                                        match_id=match.match_id,
                                        reason="disconnect_timeout_both",
                                    )
                                )
                            )
                    continue
        except WebSocketDisconnect:
            pass
        except Exception:
            # Fail safe: log with the match id for correlation (never the
            # session token), tell the client nothing internal, close 1011.
            logger.exception(
                "websocket handler failed",
                extra={"event": "ws_handler_failed", "match_id": bound.match_id if bound else None},
            )
            await _close(websocket, _INTERNAL_ERROR_CLOSE_CODE)
        finally:
            await teardown_connection()

    return router


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


async def _send_error(websocket: WebSocket, match_id: str | None, code: str, detail: str) -> None:
    message = ServerError(
        protocol_version=PROTOCOL_VERSION,
        type="error",
        match_id=match_id,
        error=ErrorInfo(code=code, message=detail),
    )
    try:
        await websocket.send_text(serialize_server_message(message))
    except Exception:
        logger.debug("dropping error send to a closed/closing websocket connection", exc_info=True)


def _ready_state_message(match: Match) -> ServerReadyState:
    players = sorted(match.players.values(), key=lambda slot: slot.player_id.value)
    return ServerReadyState(
        protocol_version=PROTOCOL_VERSION,
        type="ready_state",
        match_id=match.match_id,
        players=[
            PlayerSummary(player_id=slot.player_id.value, nickname=slot.nickname, ready=slot.ready)
            for slot in players
        ],
    )
