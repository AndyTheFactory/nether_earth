"""In-memory match/session lifecycle layer.

Public surface: :class:`~app.match.manager.MatchManager` plus the data
types/errors it works with, and
:class:`~app.match.runtime.MatchRuntimeRegistry`/
:class:`~app.match.runtime.MatchRuntime`, the async fixed-tick engine-stepping
layer. No WebSocket/transport code lives here -- see ``AGENTS.md``;
``app.transport`` layers transport on top.
"""

from app.match.manager import (
    SOLO_AI_SEAT,
    CreateMatchResult,
    CreateSoloMatchResult,
    JoinMatchResult,
    MatchManager,
)
from app.match.models import (
    InvalidNicknameError,
    InvalidSessionTokenError,
    Match,
    MatchError,
    MatchFullError,
    MatchNotFoundError,
    MatchRuntimeState,
    PlayerSlot,
    ServerBusyError,
)
from app.match.runtime import (
    TICK_INTERVAL_S,
    TICK_RATE_HZ,
    MatchRuntime,
    MatchRuntimeRegistry,
)

__all__ = [
    "SOLO_AI_SEAT",
    "TICK_INTERVAL_S",
    "TICK_RATE_HZ",
    "CreateMatchResult",
    "CreateSoloMatchResult",
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
    "ServerBusyError",
]
