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

    Only ``WAITING`` -> ``ACTIVE`` and ``ACTIVE``/``WAITING`` -> ``FINISHED``
    transitions are implemented by this task's ``MatchManager``.
    ``PAUSED_DISCONNECTED`` is part of the state's full vocabulary (required
    by issue #92's acceptance criteria) but no transition into or out of it
    is implemented here -- that is Task 7's (disconnect grace) scope.
    """

    WAITING = "waiting"
    ACTIVE = "active"
    PAUSED_DISCONNECTED = "paused_disconnected"
    FINISHED = "finished"


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
    """

    match_id: str
    join_code: str
    seed: int
    state: MatchRuntimeState = MatchRuntimeState.WAITING
    players: dict[PlayerId, PlayerSlot] = field(default_factory=dict)
    game_state: GameState | None = None

    @property
    def is_full(self) -> bool:
        return len(self.players) >= 2

    def slot_for_token(self, session_token: str) -> PlayerSlot | None:
        for slot in self.players.values():
            if slot.session_token == session_token:
                return slot
        return None

    @property
    def all_ready(self) -> bool:
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
    """Raised when a supplied nickname is empty/whitespace-only."""
