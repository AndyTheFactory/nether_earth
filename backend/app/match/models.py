"""In-memory match/session data model for the M7 lifecycle layer.

This module owns *data shapes only* (states, slots, a match record, and the
error types the lifecycle can raise) -- no lifecycle transition logic. See
``manager.py`` for the ``MatchManager`` that owns transitions between these
states.

Architecture note (AGENTS.md, non-negotiable): nothing here decides gameplay
legality. ``Match.game_state`` is an opaque engine ``GameState`` value handed
back by ``nether_earth.engine.new_game`` and never inspected/mutated by this
layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from nether_earth.ids import PlayerId
from nether_earth.state import GameState


class MatchRuntimeState(Enum):
    """Runtime lifecycle state of one match.

    ``WAITING`` -> ``ACTIVE`` and ``ACTIVE``/``WAITING`` -> ``FINISHED``
    transitions are implemented by ``MatchManager`` (M7 Task 2, issue #92).
    ``ACTIVE`` <-> ``PAUSED_DISCONNECTED`` and ``PAUSED_DISCONNECTED`` ->
    ``FINISHED`` (forfeit/no-contest) are implemented by
    ``app.match.reconnect.ReconnectCoordinator`` (M7 Task 7, issue #96) --
    the only path into ``PAUSED_DISCONNECTED`` is a disconnect notification;
    there is no manual pause in v1.
    """

    WAITING = "waiting"
    ACTIVE = "active"
    PAUSED_DISCONNECTED = "paused_disconnected"
    FINISHED = "finished"


class MatchOutcome(Enum):
    """How a match ended via the disconnect/reconnect policy.

    Deliberately does **not** cover a normal in-game (engine) victory --
    that result lives entirely in ``GameState`` and is never duplicated
    here. This enum only names the two runtime-level outcomes
    ``app.match.reconnect.ReconnectCoordinator`` (M7 Task 7, issue #96) can
    produce.
    """

    FORFEIT = "forfeit"
    NO_CONTEST = "no_contest"
    #: Normal in-game victory (a player owns zero war bases), recorded by
    #: ``app.transport.victory`` from the engine's ``VictoryEvent`` so a
    #: reconnecting client and the replay artifact both see the outcome
    #: (M9.1 audit gap G2). The engine itself remains the only authority on
    #: *whether* victory occurred; this only mirrors its event.
    VICTORY = "victory"


@dataclass(frozen=True, slots=True)
class MatchResult:
    """A durable runtime-level result annotation, set once a match ends via
    forfeit or no-contest (M7 Task 7, issue #96 review, Important I2).

    Exists *alongside*, never instead of, whatever engine victory state
    ``GameState`` already carries for a normal in-game win -- this is set
    only by ``ReconnectCoordinator._resolve_expiry``, never by any other
    ``MatchRuntimeState.FINISHED`` transition. Kept on ``Match`` (rather
    than only broadcast transiently) so a player who reconnects after the
    outcome-bearing broadcast has already gone out -- exactly the case for
    the *winning* side of a both-disconnected forfeit, who by construction
    was not connected to receive it live -- still learns the result, and so
    a future replay writer (Task 8) has something durable to persist.
    """

    outcome: MatchOutcome
    reason: str
    winner_player_id: PlayerId | None = None
    forfeiting_player_id: PlayerId | None = None
    #: Authoritative engine tick the victory was evaluated on (``VICTORY``
    #: only); ``None`` for runtime-level outcomes.
    decided_tick: int | None = None


@dataclass(slots=True)
class PlayerSlot:
    """One guest player's seat in a match.

    ``session_token`` is the opaque, unguessable credential issued on
    create/join; it is the *only* thing a reconnecting client presents to
    prove which slot it owns (see ``MatchManager.resolve_session``).
    """

    player_id: PlayerId
    nickname: str
    session_token: str
    ready: bool = False


@dataclass(slots=True)
class Match:
    """One in-memory match: identity, slots, runtime state, and engine state.

    ``game_state`` is ``None`` until the ``WAITING`` -> ``ACTIVE`` transition
    runs ``engine.new_game`` exactly once (``MatchManager`` is the only
    caller of that transition, so this field's ``None``-ness alone is enough
    to tell whether that call has happened yet).

    ``result`` is ``None`` until the match ends: forfeit/no-contest are set
    by ``ReconnectCoordinator``, a normal engine victory by
    ``app.transport.victory`` (see :class:`MatchResult`).

    A solo match (CR004.7, issue #288) has one human ``PlayerSlot`` and an
    AI seat named by ``ai_player_id``. The AI seat is not a ``PlayerSlot``:
    it has no nickname, session token or socket, so nothing that iterates
    ``players`` (broadcasts, tokens, replay nicknames) can mistake it for a
    connection. It counts toward ``is_full`` and is always ready. A solo
    match has no ``join_code``: it is never indexed for joining.
    """

    match_id: str
    join_code: str | None
    seed: int
    state: MatchRuntimeState = MatchRuntimeState.WAITING
    players: dict[PlayerId, PlayerSlot] = field(default_factory=dict)
    game_state: GameState | None = None
    result: MatchResult | None = None
    #: ``MatchManager``'s monotonic clock at creation / first ``FINISHED``
    #: transition; drive disposal of abandoned and finished matches (M10.6).
    created_at: float = 0.0
    finished_at: float | None = None
    #: ``MatchManager``'s clock when the last socket left a WAITING lobby;
    #: ``None`` while someone is attached. Sweep disposes a lobby abandoned
    #: for longer than the configured grace (security review NE-01).
    abandoned_at: float | None = None
    #: The engine-driven seat of a solo match; ``None`` for PvP.
    ai_player_id: PlayerId | None = None

    @property
    def is_solo(self) -> bool:
        return self.ai_player_id is not None

    @property
    def seat_ids(self) -> tuple[PlayerId, ...]:
        """Every seat in the match: the human slots plus the AI seat, if any."""
        seats = tuple(self.players)
        return seats if self.ai_player_id is None else (*seats, self.ai_player_id)

    @property
    def is_full(self) -> bool:
        return len(self.seat_ids) >= 2

    def slot_for_token(self, session_token: str) -> PlayerSlot | None:
        for slot in self.players.values():
            if slot.session_token == session_token:
                return slot
        return None

    @property
    def all_ready(self) -> bool:
        # The AI seat has no slot and is always ready.
        return self.is_full and all(slot.ready for slot in self.players.values())


class MatchError(Exception):
    """Base class for all lifecycle errors raised by ``MatchManager``."""


class MatchNotFoundError(MatchError):
    """Raised when a match id/join code does not resolve to a known match."""


class MatchFullError(MatchError):
    """Raised when a join is attempted against a match that already has two players."""


class InvalidSessionTokenError(MatchError):
    """Raised when a session token does not resolve to any known player/match."""


class InvalidNicknameError(MatchError):
    """Raised when a supplied nickname is empty/whitespace-only or has control characters."""


class ServerBusyError(MatchError):
    """Raised when creating a match would exceed the configured match capacity."""
