"""Disconnect/reconnect pause, grace-deadline, forfeit, and no-contest policy (M7 Task 7, issue #96).

Scope: this module owns the *runtime* (wall-clock, asyncio) half of the
locked disconnect/reconnect policy. It never touches `nether_earth.engine`
or `GameState`/tick count -- pausing/resuming/forfeiting/no-contesting a
match is purely `Match.state` bookkeeping plus wall-clock deadline tracking
(`time.monotonic`-style, never engine ticks). The fixed-tick loop
(`app.match.runtime.MatchRuntime._run`) already treats any non-``ACTIVE``
state as a cheap no-op poll (Task 4), so flipping `Match.state` here is
sufficient to stop/resume ticking with no `runtime.py` changes.

Locked policy (see the M7 Task 7 brief / issue #96 -- non-negotiable):

- First disconnect pauses the match immediately.
- Reconnect grace defaults to 60s, configurable per :class:`ReconnectCoordinator`.
- Simulation resumes only when BOTH players are connected.
- A disconnected player whose grace deadline expires while the opponent
  remains eligible (connected, or still within their own grace) forfeits;
  the opponent wins.
- Both players disconnected -> independent deadlines. Whichever expires
  first while the other is still eligible resolves as a normal forfeit.
- Both deadlines expiring with neither player returning -> no-contest.
  Never invented as a winner.
- No manual pause exists in v1 -- the only path into `PAUSED_DISCONNECTED`
  is a disconnect notification.

Design: one :class:`ReconnectCoordinator` is shared across every match (like
`MatchRuntimeRegistry`), keyed internally by `match_id`. For each
disconnected player it schedules exactly one `asyncio.Task` (the
"deadline watcher") that sleeps until that player's deadline and then
resolves the outcome; reconnecting cancels that player's watcher.
Resolution compares both players' deadlines *by value* at the moment a
watcher fires (not by "which task happened to run first") so near-
simultaneous expiries resolve deterministically into no-contest rather than
racing to a forfeit -- see :meth:`ReconnectCoordinator._resolve_expiry`.

This module never imports `app.transport`/FastAPI/websockets (AGENTS.md):
protocol/broadcast concerns are pushed onto the caller-supplied ``notify``
callback (a :data:`DisconnectNotifier`), mirroring how
`app.match.runtime.TickObserver` keeps this package transport-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nether_earth.ids import PlayerId

from app.match.models import Match, MatchRuntimeState
from app.match.runtime import MatchRuntimeRegistry

logger = logging.getLogger(__name__)

#: Default reconnect grace period (locked policy: 60 seconds). Every branch
#: that needs this value reads it from a `ReconnectCoordinator` instance's
#: `_grace_seconds`, never as a bare literal -- this constant exists only to
#: give that field a documented, named default.
DEFAULT_GRACE_SECONDS: float = 60.0


@dataclass(frozen=True, slots=True)
class PausedEvent:
    """A match just paused because ``disconnected_player_id`` disconnected.

    ``grace_deadline_epoch_ms`` is wall-clock (epoch) metadata for the wire
    message only -- never used for internal scheduling (that uses the
    injectable monotonic clock; see `ReconnectCoordinator`).
    """

    match: Match
    disconnected_player_id: PlayerId
    grace_deadline_epoch_ms: int


@dataclass(frozen=True, slots=True)
class ResumedEvent:
    """Both players are connected again; simulation may resume."""

    match: Match


@dataclass(frozen=True, slots=True)
class ForfeitEvent:
    """``forfeiting_player_id``'s grace deadline expired while the opponent was still eligible."""

    match: Match
    forfeiting_player_id: PlayerId
    winner_player_id: PlayerId


@dataclass(frozen=True, slots=True)
class NoContestEvent:
    """Both players' independent grace deadlines expired with neither returning."""

    match: Match


#: Every outcome `ReconnectCoordinator` can report. Deliberately generic
#: (engine/`app.match` types only) rather than the concrete
#: `app.protocol.server_messages` models -- see module docstring.
DisconnectEvent = PausedEvent | ResumedEvent | ForfeitEvent | NoContestEvent

#: Awaited once per event, in the order events are decided. A transport
#: caller supplies the concrete implementation (translate to a protocol
#: message, broadcast it) -- see `app.transport.disconnects`.
DisconnectNotifier = Callable[[DisconnectEvent], Awaitable[None]]


def _other_player(match: Match, player_id: PlayerId) -> PlayerId:
    """Return ``match``'s other player id (v1 is fixed at exactly two seats)."""
    for candidate in match.players:
        if candidate != player_id:
            return candidate
    raise KeyError(f"match {match.match_id!r} has no opponent seat for {player_id!r}")


def _log_task_failure(task: asyncio.Task[None]) -> None:
    """`asyncio.Task` done-callback: log (not silently drop) an unretrieved exception.

    Mirrors `MatchRuntime._run`'s own discipline of never letting a
    fire-and-forget task fail silently (issue #93 review). These tasks
    (deadline watchers, one-shot notifier dispatches) are never awaited by
    production code, so nothing else would ever surface a bug here.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("reconnect-policy task failed", exc_info=exc)


class ReconnectCoordinator:
    """Owns per-match disconnect/deadline tracking across every live match.

    Not thread-safe beyond "single asyncio event loop" (matching every other
    async component in this codebase): every public method here must be
    called from the event loop thread that owns the deadline-watcher tasks
    this class creates.

    ``monotonic_clock``/``sleep_fn``/``epoch_clock`` are injectable purely
    for deterministic testing (see the M7 Task 7 brief's testing
    constraint: no real 60s sleeps). Production callers should leave all
    three at their defaults (`time.monotonic`, `asyncio.sleep`,
    `time.time`).
    """

    def __init__(
        self,
        *,
        notify: DisconnectNotifier,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        runtime_registry: MatchRuntimeRegistry | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        epoch_clock: Callable[[], float] = time.time,
    ) -> None:
        if grace_seconds <= 0:
            raise ValueError("grace_seconds must be positive")
        self._notify = notify
        self._grace_seconds = grace_seconds
        self._runtime_registry = runtime_registry
        self._monotonic_clock = monotonic_clock
        self._sleep_fn = sleep_fn
        self._epoch_clock = epoch_clock

        # match_id -> {player_id -> monotonic deadline}, present only for
        # players *currently* tracked as disconnected. A match_id's absence
        # (or an empty inner dict) means "no one is currently disconnected".
        self._deadlines: dict[str, dict[PlayerId, float]] = {}
        # match_id -> {player_id -> that player's deadline-watcher task}.
        self._watchers: dict[str, dict[PlayerId, asyncio.Task[None]]] = {}

    # -- disconnect / reconnect entry points ---------------------------------

    def mark_disconnected(self, match: Match, player_id: PlayerId) -> None:
        """Record ``player_id`` as disconnected from ``match`` and start their grace timer.

        A no-op for a match with no live game to pause (`WAITING`, already
        `FINISHED`) and idempotent for a player already tracked as
        disconnected (e.g. a duplicate notification -- `app.transport.ws`'s
        `notify_disconnect_once` already guards against this in practice,
        but this method does not rely on that caller discipline alone).

        Pauses ``match`` (flips `MatchRuntimeState.ACTIVE` ->
        `PAUSED_DISCONNECTED` and emits :class:`PausedEvent`) iff this is
        the *first* currently-disconnected player for this match -- a
        second disconnect while already paused starts that second player's
        own independent deadline without re-pausing or re-notifying.
        """
        if match.state not in (MatchRuntimeState.ACTIVE, MatchRuntimeState.PAUSED_DISCONNECTED):
            return

        deadlines = self._deadlines.setdefault(match.match_id, {})
        if player_id in deadlines:
            return

        now = self._monotonic_clock()
        deadline = now + self._grace_seconds
        deadlines[player_id] = deadline

        is_first_disconnect = match.state is MatchRuntimeState.ACTIVE
        if is_first_disconnect:
            match.state = MatchRuntimeState.PAUSED_DISCONNECTED

        watchers = self._watchers.setdefault(match.match_id, {})
        watchers[player_id] = self._spawn(self._watch(match, player_id, deadline))

        if is_first_disconnect:
            grace_deadline_epoch_ms = int((self._epoch_clock() + self._grace_seconds) * 1000)
            self._spawn(
                self._notify(
                    PausedEvent(
                        match=match,
                        disconnected_player_id=player_id,
                        grace_deadline_epoch_ms=grace_deadline_epoch_ms,
                    )
                )
            )

    def mark_reconnected(self, match: Match, player_id: PlayerId) -> None:
        """Record ``player_id`` as reconnected to ``match`` and cancel their grace timer.

        A no-op if ``player_id`` was not currently tracked as disconnected
        (e.g. a reconnect for a player who was never marked disconnected, or
        a second reconnect notification for the same player). Resumes
        ``match`` (flips back to `ACTIVE` and emits :class:`ResumedEvent`)
        iff this reconnect leaves *no* player still tracked as disconnected
        -- i.e. only once both players are connected, never on the first of
        two reconnects.
        """
        deadlines = self._deadlines.get(match.match_id)
        if deadlines is None or player_id not in deadlines:
            return
        deadlines.pop(player_id, None)

        watchers = self._watchers.get(match.match_id)
        task = watchers.pop(player_id, None) if watchers is not None else None
        if task is not None and not task.done():
            task.cancel()

        if deadlines:
            return  # the opponent is still disconnected; stay paused.

        self._deadlines.pop(match.match_id, None)
        if watchers is not None and not watchers:
            self._watchers.pop(match.match_id, None)

        if match.state is MatchRuntimeState.PAUSED_DISCONNECTED:
            match.state = MatchRuntimeState.ACTIVE
            self._spawn(self._notify(ResumedEvent(match=match)))

    # -- disposal -------------------------------------------------------------

    def cancel(self, match_id: str) -> None:
        """Cancel every pending deadline-watcher task for ``match_id``.

        Called by `MatchManager.finish_match`/`dispose_match` so a match
        that ends for any reason (engine victory, forfeit, no-contest, or
        explicit disposal) never leaves an orphan watcher task behind --
        mirrors the same discipline `MatchRuntimeRegistry.cancel`/`dispose`
        already enforce for the tick-loop task (issue #93 review).
        Idempotent: a `match_id` with nothing tracked is a harmless no-op.
        """
        watchers = self._watchers.pop(match_id, None)
        if watchers:
            for task in watchers.values():
                if not task.done():
                    task.cancel()
        self._deadlines.pop(match_id, None)

    def dispose(self, match_id: str) -> None:
        """Alias of :meth:`cancel`; called by `MatchManager.dispose_match`."""
        self.cancel(match_id)

    # -- deadline watcher -------------------------------------------------------

    async def _watch(self, match: Match, player_id: PlayerId, deadline: float) -> None:
        """Sleep until ``deadline`` (recomputed against the current clock), then resolve.

        The delay is computed relative to *now*, not fixed at task-creation
        time, so a scheduling gap between `mark_disconnected` creating this
        task and the event loop actually starting it never shifts the
        effective deadline -- `deadline` itself (an absolute monotonic
        timestamp) is the only source of truth.

        Cancellation (a reconnect within grace) simply ends this coroutine
        before :meth:`_resolve_expiry` ever runs -- no forfeit/no-contest
        outcome is produced for a cancelled watcher.
        """
        delay = deadline - self._monotonic_clock()
        if delay > 0:
            await self._sleep_fn(delay)
        await self._resolve_expiry(match, player_id)

    async def _resolve_expiry(self, match: Match, expired_player_id: PlayerId) -> None:
        """Resolve ``expired_player_id``'s grace-deadline expiry exactly once.

        Deterministic even for two near-simultaneous expiries: this compares
        the *values* of both players' deadlines against the current clock at
        resolution time, not "which watcher task happened to run first" --
        see the class docstring. A defensive no-op if this match/player was
        already resolved or reconnected concurrently (should not be
        reachable given `mark_reconnected`/`cancel` cancel watcher tasks
        before removing their bookkeeping, but resolving an outcome twice
        for the same match would violate "exactly once", so this is guarded
        explicitly rather than assumed).
        """
        match_id = match.match_id
        deadlines = self._deadlines.get(match_id)
        if deadlines is None or expired_player_id not in deadlines:
            return
        if match.state is MatchRuntimeState.FINISHED:
            return

        opponent_id = _other_player(match, expired_player_id)
        now = self._monotonic_clock()
        opponent_deadline = deadlines.get(opponent_id)
        # Opponent is still eligible (connected, i.e. absent from
        # `deadlines`, or disconnected but their own deadline has not yet
        # been reached) -> normal forfeit. Both already past their deadline
        # -> no-contest; never invent a winner.
        is_no_contest = opponent_deadline is not None and opponent_deadline <= now

        # Finalize match.state (and cancel the tick loop) *before* the
        # `await self._notify(...)` below, synchronously with no
        # intervening await -- this is what makes "exactly once" hold even
        # if the opponent's own watcher is about to fire concurrently: its
        # own `_resolve_expiry` call will see `match.state is FINISHED` and
        # return immediately (see the guard above).
        match.state = MatchRuntimeState.FINISHED
        if self._runtime_registry is not None:
            self._runtime_registry.cancel(match_id)

        watchers = self._watchers.get(match_id, {})
        opponent_task = watchers.pop(opponent_id, None)
        if opponent_task is not None and not opponent_task.done():
            opponent_task.cancel()
        watchers.pop(expired_player_id, None)
        if not watchers:
            self._watchers.pop(match_id, None)
        self._deadlines.pop(match_id, None)

        event: DisconnectEvent
        if is_no_contest:
            event = NoContestEvent(match=match)
        else:
            event = ForfeitEvent(
                match=match,
                forfeiting_player_id=expired_player_id,
                winner_player_id=opponent_id,
            )
        await self._notify(event)

    # -- internal helpers -------------------------------------------------------

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        """`asyncio.create_task` plus a done-callback that logs (never swallows) failures."""
        task = asyncio.ensure_future(coro)
        assert isinstance(task, asyncio.Task)  # `ensure_future` on a coroutine always returns one.
        task.add_done_callback(_log_task_failure)
        return task
