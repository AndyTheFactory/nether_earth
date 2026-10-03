"""One handler per inbound ``/ws`` message type, plus session authentication.

``create``/``join`` are valid only on a connection with no bound session.
Every other message carries its own ``matchId``/``playerId``/``sessionToken``
and passes :func:`authenticate` first: the first authenticated message binds
the connection, and any message resolving to a different session -- or a bad
token, even on an unbound socket -- is rejected and closed with 1008, since
such a socket cannot be trusted to do anything else. Command payloads go
through :func:`~app.transport.commands.payload_to_command`, a field-shape
adapter with no legality decision; a payload malformed for its ``Command``
never reaches ``submit_command``. Handlers own no gameplay rules.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from typing import Any

from nether_earth.ids import PlayerId

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
from app.protocol import InboundMessage
from app.protocol.client_messages import (
    ClientCreateMatch,
    ClientGameplayCommand,
    ClientJoinMatch,
    ClientLeaveMatch,
    ClientSetReady,
)
from app.protocol.common import PROTOCOL_VERSION, PlayerSummary
from app.protocol.reconnect import ClientReconnect, ServerResync
from app.protocol.server_messages import (
    ServerCreated,
    ServerForfeit,
    ServerJoined,
    ServerNoContest,
    ServerReadyState,
    ServerStarted,
)
from app.protocol.snapshot import SnapshotMessage
from app.transport.commands import CommandPayloadError, payload_to_command
from app.transport.connection import Connection
from app.transport.connections import broadcast
from app.transport.limits import MAX_FAILED_JOINS, NORMAL_CLOSE_CODE
from app.transport.snapshots import build_snapshot_message, empty_snapshot_message
from app.transport.victory import finished_message

logger = logging.getLogger(__name__)

AuthenticatedClientMessage = (
    ClientSetReady | ClientLeaveMatch | ClientGameplayCommand | ClientReconnect
)


class Outcome(Enum):
    CONTINUE = auto()  # keep receiving
    CLOSED = auto()  # the handler closed the socket; the receive loop returns


Handler = Callable[[Connection, Any, Match, PlayerId], Awaitable[Outcome]]


async def handle_create(conn: Connection, message: ClientCreateMatch) -> Outcome:
    if conn.bound is not None:
        await conn.reject_and_close("already_bound")
        return Outcome.CLOSED
    solo = message.opponent == "computer"
    try:
        if solo:
            solo_result = conn.match_manager.create_solo_match(message.nickname)
            join_code: str | None = None
            create_player_id = solo_result.player_id
            create_session_token = solo_result.session_token
            create_match_id = solo_result.match_id
        else:
            result = conn.match_manager.create_match(message.nickname)
            join_code = result.join_code
            create_player_id = result.player_id
            create_session_token = result.session_token
            create_match_id = result.match_id
    except InvalidNicknameError as exc:
        await conn.send_error("invalid_nickname", str(exc), match_id=None)
        return Outcome.CONTINUE
    except ServerBusyError as exc:
        await conn.send_error("server_busy", str(exc), match_id=None)
        return Outcome.CONTINUE
    conn.bind(create_match_id, create_player_id.value, create_session_token)
    await conn.attach()
    await conn.send(
        ServerCreated(
            protocol_version=PROTOCOL_VERSION,
            type="created",
            match_id=create_match_id,
            join_code=join_code,
            player_id=create_player_id.value,
            session_token=create_session_token,
            # `None` drops `opponent` from the wire on the PvP path, keeping a plain
            # `create` byte-compatible with clients that do not know it.
            opponent="computer" if solo else None,
        )
    )
    return Outcome.CONTINUE


async def handle_join(conn: Connection, message: ClientJoinMatch) -> Outcome:
    if conn.bound is not None:
        await conn.reject_and_close("already_bound")
        return Outcome.CLOSED
    try:
        join_result = conn.match_manager.join_match(message.join_code, message.nickname)
    except (MatchNotFoundError, MatchFullError) as exc:
        conn.failed_joins += 1
        if conn.failed_joins >= MAX_FAILED_JOINS:
            logger.warning(
                "closing websocket: too many failed join attempts",
                extra={"event": "ws_join_attempts_exceeded"},
            )
            await conn.reject_and_close(
                "too_many_join_attempts", "too many failed joins", match_id=None
            )
            return Outcome.CLOSED
        code = "match_full" if isinstance(exc, MatchFullError) else "match_not_found"
        await conn.send_error(code, str(exc), match_id=None)
        return Outcome.CONTINUE
    except InvalidNicknameError as exc:
        await conn.send_error("invalid_nickname", str(exc), match_id=None)
        return Outcome.CONTINUE
    conn.bind(join_result.match_id, join_result.player_id.value, join_result.session_token)
    await conn.attach()
    await conn.send(
        ServerJoined(
            protocol_version=PROTOCOL_VERSION,
            type="joined",
            match_id=join_result.match_id,
            player_id=join_result.player_id.value,
            session_token=join_result.session_token,
        )
    )
    match = conn.match_manager.get_match(join_result.match_id)
    await broadcast(conn.connection_registry, join_result.match_id, _ready_state_message(match))
    return Outcome.CONTINUE


async def authenticate(
    conn: Connection, message: AuthenticatedClientMessage
) -> tuple[Match, PlayerId] | None:
    """Resolve and authorize ``message``'s session; ``None`` after rejecting and closing."""
    try:
        match, engine_player_id = conn.match_manager.resolve_session(message.session_token)
    except InvalidSessionTokenError:
        await conn.reject_and_close("invalid_session")
        return None

    if match.match_id != message.match_id or engine_player_id.value != message.player_id:
        await conn.reject_and_close("session_mismatch", match_id=match.match_id)
        return None

    if conn.bound is None:
        conn.bind(match.match_id, engine_player_id.value, message.session_token)
        await conn.attach()
    elif conn.bound.session_token != message.session_token or conn.bound.match_id != match.match_id:
        # A connection that already authenticated as one session must never
        # be allowed to act as a different one.
        await conn.reject_and_close("session_mismatch")
        return None
    return match, engine_player_id


async def handle_ready(
    conn: Connection, message: ClientSetReady, match: Match, engine_player_id: PlayerId
) -> Outcome:
    was_waiting = match.state is MatchRuntimeState.WAITING
    conn.match_manager.set_ready(message.session_token, ready=message.ready)
    just_started = was_waiting and match.state is MatchRuntimeState.ACTIVE
    # Every `await` below is a cancellation point (client drops mid-broadcast).
    # A started runtime cannot tick until `announce_started` fires, so the
    # `finally` covers the *whole* window, `ready_state` broadcast included,
    # or a cancellation there wedges an ACTIVE match forever.
    try:
        await broadcast(conn.connection_registry, match.match_id, _ready_state_message(match))
        if just_started:
            # `engine.new_game` runs before the state flips to ACTIVE, so this
            # is tick-0 state, read as-is. A missing `game_state` is a manager
            # invariant violation: raise (an `assert` is stripped by `-O`).
            if match.game_state is None:
                raise RuntimeError(
                    f"match {match.match_id!r} transitioned to ACTIVE with no game_state"
                )
            await broadcast(
                conn.connection_registry,
                match.match_id,
                ServerStarted(
                    protocol_version=PROTOCOL_VERSION,
                    type="started",
                    match_id=match.match_id,
                    tick=match.game_state.tick,
                ),
            )
            await broadcast(
                conn.connection_registry,
                match.match_id,
                build_snapshot_message(match.match_id, match.game_state),
            )
    finally:
        if just_started:
            conn.runtime_registry.announce_started(match.match_id)
    return Outcome.CONTINUE


async def handle_leave(
    conn: Connection, message: ClientLeaveMatch, match: Match, engine_player_id: PlayerId
) -> Outcome:
    await conn.teardown()
    await conn.close(NORMAL_CLOSE_CODE)
    return Outcome.CLOSED


async def handle_command(
    conn: Connection, message: ClientGameplayCommand, match: Match, engine_player_id: PlayerId
) -> Outcome:
    try:
        command = payload_to_command(message.payload, engine_player_id, message.client_sequence)
    except CommandPayloadError as exc:
        await conn.send_error(
            "invalid_command_payload",
            f"command payload is structurally invalid for its command type: {exc}",
            match_id=match.match_id,
        )
        return Outcome.CONTINUE
    accepted = await conn.runtime_registry.submit_command(match.match_id, command)
    if not accepted:
        await conn.send_error(
            "command_rejected",
            "command not accepted (match not active, or a duplicate/replayed sequence number)",
            match_id=match.match_id,
        )
    return Outcome.CONTINUE


async def handle_reconnect(
    conn: Connection, message: ClientReconnect, match: Match, engine_player_id: PlayerId
) -> Outcome:
    await conn.attach()
    # Idempotent: reopens a start gate left closed by a cancelled ready
    # handler. A WAITING match has no runtime yet.
    if match.state is not MatchRuntimeState.WAITING:
        conn.runtime_registry.announce_started(match.match_id)
    # `game_state` is read as it stands, never advanced for a snapshot; it is
    # `None` only before the match first goes ACTIVE, when there is no
    # authoritative gameplay state to send.
    snapshot: SnapshotMessage = (
        build_snapshot_message(match.match_id, match.game_state)
        if match.game_state is not None
        else empty_snapshot_message(match.match_id)
    )
    await conn.send(
        ServerResync(
            protocol_version=PROTOCOL_VERSION,
            type="resync",
            match_id=match.match_id,
            player_id=engine_player_id.value,
            snapshot=snapshot,
        )
    )
    # Cancels this player's grace timer and resumes once both are connected
    # (a no-op if never marked disconnected). After the resync send, so this
    # player's resync always precedes any `resumed` broadcast it triggers.
    conn.match_manager.mark_reconnected(message.session_token)

    # Replay a decided result to this socket only: the winner of a
    # both-disconnected forfeit was not connected for the live broadcast.
    match_result = match.result
    finished = finished_message(match)
    if finished is not None:
        await conn.send(finished)
    elif match_result is not None:
        if match_result.outcome is MatchOutcome.FORFEIT:
            forfeiting = match_result.forfeiting_player_id
            winner = match_result.winner_player_id
            if forfeiting is None or winner is None:
                # Invariant, not client input: FORFEIT always names both seats.
                raise RuntimeError(
                    f"match {match.match_id!r} has a FORFEIT result without both player ids"
                )
            await conn.send(
                ServerForfeit(
                    protocol_version=PROTOCOL_VERSION,
                    type="forfeit",
                    match_id=match.match_id,
                    forfeiting_player_id=forfeiting.value,
                    winner_player_id=winner.value,
                    reason="disconnect_timeout",
                )
            )
        else:
            await conn.send(
                ServerNoContest(
                    protocol_version=PROTOCOL_VERSION,
                    type="no_contest",
                    match_id=match.match_id,
                    reason="disconnect_timeout_both",
                )
            )
    return Outcome.CONTINUE


#: Handlers for the messages that require an authenticated session.
#: ``create``/``join`` are dispatched before authentication.
HANDLERS: dict[type[AuthenticatedClientMessage], Handler] = {
    ClientSetReady: handle_ready,
    ClientLeaveMatch: handle_leave,
    ClientGameplayCommand: handle_command,
    ClientReconnect: handle_reconnect,
}


async def dispatch(conn: Connection, message: InboundMessage) -> Outcome:
    if isinstance(message, ClientCreateMatch):
        return await handle_create(conn, message)
    if isinstance(message, ClientJoinMatch):
        return await handle_join(conn, message)
    authenticated = await authenticate(conn, message)
    if authenticated is None:
        return Outcome.CLOSED
    match, engine_player_id = authenticated
    return await HANDLERS[type(message)](conn, message, match, engine_player_id)


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
