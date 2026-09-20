"""In-memory match/session lifecycle layer (M7 Task 2, issue #92).

Public surface: :class:`~app.match.manager.MatchManager` plus the data
types/errors it works with. No WebSocket/transport code lives here -- see
``AGENTS.md``/the M7 plan for how later tasks layer transport on top.
"""

from app.match.manager import CreateMatchResult, JoinMatchResult, MatchManager
from app.match.models import (
    InvalidNicknameError,
    InvalidSessionTokenError,
    Match,
    MatchError,
    MatchFullError,
    MatchNotFoundError,
    MatchRuntimeState,
    PlayerSlot,
)

__all__ = [
    "CreateMatchResult",
    "InvalidNicknameError",
    "InvalidSessionTokenError",
    "JoinMatchResult",
    "Match",
    "MatchError",
    "MatchFullError",
    "MatchManager",
    "MatchNotFoundError",
    "MatchRuntimeState",
    "PlayerSlot",
]
