"""In-memory match/session lifecycle layer (M7 Task 2, issue #92).

Public surface: :class:`~app.match.manager.MatchManager` plus the data
types/errors it works with, and (M7 Task 4, issue #93)
:class:`~app.match.runtime.MatchRuntimeRegistry`/
:class:`~app.match.runtime.MatchRuntime`, the async fixed-tick engine-stepping
layer. No WebSocket/transport code lives here -- see ``AGENTS.md``/the M7
plan for how later tasks layer transport on top.
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
from app.match.runtime import (
    TICK_INTERVAL_S,
    TICK_RATE_HZ,
    MatchRuntime,
    MatchRuntimeRegistry,
)

__all__ = [
    "TICK_INTERVAL_S",
    "TICK_RATE_HZ",
    "CreateMatchResult",
    "InvalidNicknameError",
    "InvalidSessionTokenError",
    "JoinMatchResult",
    "Match",
    "MatchError",
    "MatchFullError",
    "MatchManager",
    "MatchNotFoundError",
    "MatchRuntime",
    "MatchRuntimeRegistry",
    "MatchRuntimeState",
    "PlayerSlot",
]
