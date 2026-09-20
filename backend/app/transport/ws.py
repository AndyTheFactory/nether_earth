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

Gameplay-command-conversion scope: `ClientGameplayCommand.payload` is
still `PlaceholderCommandPayload` pending issue #98's full command
enumeration (see `app/protocol/common.py`). This module therefore builds
only the generic `nether_earth.commands.Command` envelope (`player`,
`sequence`) from the transport message and hands it to
`MatchRuntimeRegistry.submit_command` -- it does not (and structurally
cannot yet) construct a concrete gameplay command subclass. This is exactly
the "minimal/representative conversion" the task brief calls out as
sufficient for this task; issue #98 is expected to replace this with a real
payload -> `Command` subclass mapping.

Disconnect notification: `MatchManager.mark_disconnected` (added by this
task) is called through a single `teardown_connection()` helper, itself
invoked from exactly one `finally` block plus (redundantly-but-safely, via
the same helper) the `leave` handler, regardless of whether the connection
ends via a clean `leave` message, a WebSocket close/error, or an auth
rejection after a session was already bound. `teardown_connection()` first
unregisters this socket from `ConnectionRegistry` and only notifies if that
unregister reports this socket was still the one currently registered for
its `(match_id, player_id)` slot -- this closes a race where a stale
connection's delayed teardown (e.g. slow TCP close) could otherwise fire a
spurious disconnect notification for a player who has since reconnected on
a newer socket (`ConnectionRegistry.unregister`'s return value exists
specifically to make that race detectable). It is a bookkeeping no-op
today; Task 7 (issue #95) owns the actual pause/grace-timer policy behind
it, which is exactly why this race matters now even though nothing
observable depends on it yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from nether_earth import commands as engine_commands

from app.match.manager import MatchManager
from app.match.models import (
    InvalidNicknameError,
    InvalidSessionTokenError,
    Match,
    MatchFullError,
    MatchNotFoundError,
    MatchRuntimeState,
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
    ServerJoined,
    ServerReadyState,
    ServerStarted,
)
from app.protocol.snapshot import SnapshotMessage
from app.transport.connections import ConnectionRegistry, broadcast
from app.transport.snapshots import build_snapshot_message, empty_snapshot_message

logger = logging.getLogger(__name__)

#: Close code sent for every authentication/session-identity violation
#: (missing, invalid, or wrong-match/wrong-player session token, or a second
#: create/join attempt on an already-bound connection). 1008 = "Policy
#: Violation" per RFC 6455 -- the closest standard code for "you are not who
#: you claimed to be for this connection".
_POLICY_VIOLATION_CLOSE_CODE = 1008

#: Close code sent after a client-initiated, well-formed `leave` message.
_NORMAL_CLOSE_CODE = 1000


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
) -> APIRouter:
    """Build the ``/ws`` router bound to one set of match/runtime/connection stores.

    A factory (rather than a module-level route) so ``app.main.create_app``
    can give every app instance its own isolated ``MatchManager`` /
    ``MatchRuntimeRegistry`` / ``ConnectionRegistry`` -- important for tests,
    which must not leak matches or connections across independent app
    instances.
    """
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        bound: _BoundSession | None = None
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

        async def _reject_and_close(ws: WebSocket, match_id: str | None, code: str) -> None:
            await _send_error(ws, match_id, code, "session rejected; closing connection")
            await ws.close(code=_POLICY_VIOLATION_CLOSE_CODE)

        try:
            while True:
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
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
                    try:
                        result = match_manager.create_match(message.nickname)
                    except InvalidNicknameError as exc:
                        await _send_error(websocket, None, "invalid_nickname", str(exc))
                        continue
                    bound = _BoundSession(
                        match_id=result.match_id,
                        player_id=result.player_id.value,
                        session_token=result.session_token,
                    )
                    connection_registry.register(bound.match_id, bound.player_id, websocket)
                    await websocket.send_text(
                        serialize_server_message(
                            ServerCreated(
                                protocol_version=PROTOCOL_VERSION,
                                type="created",
                                match_id=result.match_id,
                                join_code=result.join_code,
                                player_id=bound.player_id,
                                session_token=bound.session_token,
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
                    except MatchNotFoundError as exc:
                        await _send_error(websocket, None, "match_not_found", str(exc))
                        continue
                    except MatchFullError as exc:
                        await _send_error(websocket, None, "match_full", str(exc))
                        continue
                    except InvalidNicknameError as exc:
                        await _send_error(websocket, None, "invalid_nickname", str(exc))
                        continue
                    bound = _BoundSession(
                        match_id=join_result.match_id,
                        player_id=join_result.player_id.value,
                        session_token=join_result.session_token,
                    )
                    connection_registry.register(bound.match_id, bound.player_id, websocket)
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
                    connection_registry.register(bound.match_id, bound.player_id, websocket)
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
                    await broadcast(
                        connection_registry, match.match_id, _ready_state_message(match)
                    )
                    if was_waiting and match.state is MatchRuntimeState.ACTIVE:
                        # `_start_match_locked` already ran `engine.new_game`
                        # synchronously before flipping `match.state` to
                        # ACTIVE, so `game_state` is guaranteed non-None here
                        # -- this is the tick-0 authoritative state, read
                        # as-is, never advanced. A real exception (not
                        # `assert`, which `python -O` strips, turning this
                        # into a confusing downstream `AttributeError`)
                        # because reaching this branch with no `game_state`
                        # is an engine-manager invariant violation, not a
                        # recoverable client-input error.
                        if match.game_state is None:
                            raise RuntimeError(
                                f"match {match.match_id!r} transitioned to ACTIVE "
                                "with no game_state"
                            )
                        try:
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
                            # Structural race fix (issue #95 review, I-1):
                            # this match's `MatchRuntime` was started with
                            # `require_announcement=True` (see
                            # `MatchManager._start_match_locked`), so it
                            # cannot tick until this fires -- `finally` so a
                            # broadcast failure can never leave the match
                            # permanently un-ticking.
                            runtime_registry.announce_started(match.match_id)
                    continue

                if isinstance(message, ClientLeaveMatch):
                    await teardown_connection()
                    await websocket.close(code=_NORMAL_CLOSE_CODE)
                    return

                if isinstance(message, ClientGameplayCommand):
                    command = engine_commands.Command(
                        player=engine_player_id, sequence=message.client_sequence
                    )
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
                    connection_registry.register(match.match_id, engine_player_id.value, websocket)
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
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            await teardown_connection()

    return router


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
