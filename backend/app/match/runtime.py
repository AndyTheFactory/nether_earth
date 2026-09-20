"""Fixed-tick asyncio Match runtime and deterministic command queue (M7 Task 4, issue #93).

Scope: this module owns the *async* layer that steps the engine at a fixed
tick rate for one ``ACTIVE`` match at a time and queues player commands for
the next eligible tick. It does not do WebSocket I/O (Task 5), does not
drive disconnect/reconnect pause transitions (Task 7 owns writing
``MatchRuntimeState.PAUSED_DISCONNECTED`` -- this module only *honors* it by
treating any non-``ACTIVE`` state as a cheap no-op), does not broadcast
snapshots (Task 6), and does not persist replays (Task 8).

Architecture note (AGENTS.md, non-negotiable): this module never invents
command ordering or gameplay legality. Every tick's queued commands are
handed to ``nether_earth.engine.step`` exactly as accumulated; ``step``
itself deterministically orders and validates them via
``nether_earth.commands.validate_command_batch`` (see that module's and
``engine.step``'s docstrings) -- the runtime's own submission-time dedup
below is a *transport* concern (rejecting replayed/duplicate
``client_sequence`` values across ticks, which the engine has no memory of
across ``step`` calls), not a second gameplay-ordering rule.

Design: ``MatchManager`` (``manager.py``) stays fully synchronous and
unaware of asyncio -- it owns lifecycle bookkeeping (WAITING/ACTIVE/FINISHED,
tokens) under a plain ``threading.Lock`` and is still usable/testable with no
event loop at all, as it is today. This module is a separate, optional layer:
``MatchRuntimeRegistry`` is wired into ``MatchManager`` via a small
constructor hook (see ``manager.py``'s ``runtime`` parameter) so that
lifecycle transitions (``_start_match_locked``/``finish_match``/
``dispose_match``) start/cancel a runtime as a side effect, without
``MatchManager`` itself ever awaiting anything or importing asyncio. Command
*submission* is not routed through ``MatchManager`` at all (it has nothing to
do with session/lifecycle bookkeeping) -- a future transport layer (Task 5)
calls ``MatchRuntimeRegistry.submit_command`` directly, keyed by the
``match_id`` it already resolved via ``MatchManager.resolve_session``.
"""

from __future__ import annotations

import asyncio
import contextlib

from nether_earth import engine as engine_module
from nether_earth.collision import RobotFixture
from nether_earth.commands import Command
from nether_earth.ids import PlayerId
from nether_earth.map import WorldMap

from app.match.models import Match, MatchRuntimeState

#: Authoritative simulation tick rate (`_specs/technical-spec.md` "Authoritative
#: simulation: 20 Hz"). Named constant, not a magic literal, per the task brief.
TICK_RATE_HZ: float = 20.0
TICK_INTERVAL_S: float = 1.0 / TICK_RATE_HZ

#: How often the tick loop polls a non-ACTIVE match (e.g. PAUSED_DISCONNECTED)
#: for a state change, instead of busy-looping. Cheap relative to the 20 Hz
#: tick interval; not itself gameplay-authoritative in any way.
DEFAULT_PAUSE_POLL_INTERVAL_S: float = 0.05


class MatchRuntime:
    """Owns the fixed-tick loop and command queue for exactly one match.

    One ``asyncio.Task`` runs :meth:`_run` for the lifetime of this object
    (started explicitly via :meth:`start`, stopped via :meth:`request_cancel`
    / :meth:`wait_stopped`). That single task is the sole caller of
    ``engine.step`` for this match, which already serializes ``step`` calls
    by construction (single-owner-task discipline); ``_step_lock`` below is
    kept as defense-in-depth documented by the task brief, in case a future
    caller ever needs to trigger a step from outside the loop task.
    """

    def __init__(
        self,
        match: Match,
        *,
        tick_rate_hz: float = TICK_RATE_HZ,
        world: WorldMap | None = None,
        robots: tuple[RobotFixture, ...] = (),
        pause_poll_interval_s: float = DEFAULT_PAUSE_POLL_INTERVAL_S,
    ) -> None:
        if tick_rate_hz <= 0:
            raise ValueError("tick_rate_hz must be positive")
        self._match = match
        self._interval_s = 1.0 / tick_rate_hz
        self._pause_poll_interval_s = pause_poll_interval_s
        self._world = world
        self._robots = robots

        self._pending: list[Command] = []
        self._last_accepted_sequence: dict[PlayerId, int] = {}
        self._queue_lock = asyncio.Lock()
        self._step_lock = asyncio.Lock()

        self._task: asyncio.Task[None] | None = None
        self.tick_count = 0
        self.last_events: tuple[object, ...] = ()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the tick loop task if it is not already running.

        Idempotent: calling this again while a task is already running (and
        not finished) is a no-op, so a caller cannot accidentally spawn a
        second concurrent loop for the same match.
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    def request_cancel(self) -> None:
        """Request cancellation of the tick loop task, if any (fire-and-forget).

        Synchronous and non-blocking so it is safe to call from
        ``MatchManager``'s plain synchronous code (which holds a
        ``threading.Lock`` and must never await). Does not wait for the task
        to actually finish -- use :meth:`wait_stopped` for that (mainly
        useful in tests that need to assert no orphan task remains).
        """
        if self._task is not None:
            self._task.cancel()

    async def wait_stopped(self) -> None:
        """Await the tick loop task's actual completion after cancellation.

        Swallows ``asyncio.CancelledError`` (the expected outcome of a
        cancelled loop task) so callers can simply ``await`` this to know
        the task is gone (no orphan task left behind).
        """
        task = self._task
        if task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- command submission ---------------------------------------------------

    async def submit_command(self, command: Command) -> bool:
        """Enqueue ``command`` for the next eligible tick.

        Returns ``True`` if accepted into the queue, ``False`` if rejected as
        a duplicate/replayed ``client_sequence`` for ``command.player``
        (documented return value, not an exception, since a replayed
        submission -- e.g. a client retry racing its own ack -- is an
        expected, non-exceptional occurrence for transport callers).

        Rejection here is purely a transport-level replay guard: it tracks
        the highest sequence *accepted into the queue* per player and never
        touches gameplay legality (unknown player, negative sequence, ...),
        which is exclusively the engine's ``validate_command_batch``'s job
        once the batch reaches ``engine.step``.

        Safe to call concurrently from multiple coroutines: the per-match
        ``asyncio.Lock`` below makes the accept-or-reject decision and the
        resulting queue/bookkeeping mutation atomic with respect to other
        concurrent ``submit_command`` calls, so two coroutines racing to
        submit out-of-order sequences for the same or different players
        always converge on the same accepted set regardless of which one's
        `await` happens to resume first -- final engine ordering is then
        `engine.step`'s own deterministic ``(player.value, sequence)`` sort
        over that accepted set, independent of submission/scheduling order.
        """
        async with self._queue_lock:
            last = self._last_accepted_sequence.get(command.player)
            if last is not None and command.sequence <= last:
                return False
            self._last_accepted_sequence[command.player] = command.sequence
            self._pending.append(command)
            return True

    async def _drain_queue(self) -> tuple[Command, ...]:
        async with self._queue_lock:
            drained = tuple(self._pending)
            self._pending.clear()
            return drained

    # -- tick loop ------------------------------------------------------------

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick_at = loop.time()
        while True:
            if self._match.state is MatchRuntimeState.FINISHED:
                return
            if self._match.state is not MatchRuntimeState.ACTIVE:
                # PAUSED_DISCONNECTED (or, defensively, WAITING): cheap poll,
                # never a busy loop, and never advances the engine.
                await asyncio.sleep(self._pause_poll_interval_s)
                # Resync the schedule so resuming ACTIVE does not trigger a
                # burst of "catch-up" ticks for time spent paused.
                next_tick_at = loop.time()
                continue

            await self._advance_one_tick()

            # Drift-compensated scheduling: the next tick's target time is
            # always `previous target + fixed interval`, not
            # `now + interval` (which would accumulate scheduler jitter).
            # This never feeds into `engine.step` itself -- every tick is
            # still exactly one `step` call, full stop.
            next_tick_at += self._interval_s
            sleep_for = next_tick_at - loop.time()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                # Fell behind (e.g. a slow tick): resync to now rather than
                # firing a burst of zero-wait catch-up ticks with stale
                # target times.
                next_tick_at = loop.time()

    async def _advance_one_tick(self) -> None:
        commands = await self._drain_queue()
        async with self._step_lock:
            state = self._match.game_state
            if state is None:
                # Defensive: a runtime should only ever be started for a
                # match that has already gone ACTIVE, at which point
                # MatchManager has already produced tick-0 game_state via
                # engine.new_game. Not reachable through the documented
                # start() call sites.
                raise RuntimeError("MatchRuntime ticked before match.game_state was initialized")
            new_state, events = engine_module.step(
                state, commands, world=self._world, robots=self._robots
            )
            self._match.game_state = new_state
            self.last_events = events
        self.tick_count += 1


class MatchRuntimeRegistry:
    """Owns one :class:`MatchRuntime` per active match, keyed by ``match_id``.

    This is the object ``MatchManager`` is given (optionally) so its
    lifecycle transitions can start/cancel a runtime without importing
    asyncio itself -- see ``manager.py``'s ``runtime`` constructor parameter.
    """

    def __init__(
        self,
        *,
        tick_rate_hz: float = TICK_RATE_HZ,
        pause_poll_interval_s: float = DEFAULT_PAUSE_POLL_INTERVAL_S,
    ) -> None:
        self._tick_rate_hz = tick_rate_hz
        self._pause_poll_interval_s = pause_poll_interval_s
        self._runtimes: dict[str, MatchRuntime] = {}

    def start(
        self,
        match: Match,
        *,
        world: WorldMap | None = None,
        robots: tuple[RobotFixture, ...] = (),
    ) -> MatchRuntime:
        """Start (or return the already-running) runtime for ``match``.

        Synchronous and non-blocking (``asyncio.create_task`` schedules but
        does not run the loop inline), so this is safe to call from
        ``MatchManager``'s synchronous ``_start_match_locked`` -- it must
        still be called from a thread with a running event loop (true of any
        real deployment, since this is only ever wired up from an async
        FastAPI app; a caller with no running loop gets asyncio's own
        ``RuntimeError`` rather than a silently swallowed no-op).
        """
        runtime = self._runtimes.get(match.match_id)
        if runtime is None:
            runtime = MatchRuntime(
                match,
                tick_rate_hz=self._tick_rate_hz,
                world=world,
                robots=robots,
                pause_poll_interval_s=self._pause_poll_interval_s,
            )
            self._runtimes[match.match_id] = runtime
        runtime.start()
        return runtime

    def get(self, match_id: str) -> MatchRuntime | None:
        return self._runtimes.get(match_id)

    def cancel(self, match_id: str) -> None:
        """Request cancellation of ``match_id``'s runtime, if any (fire-and-forget).

        Does not remove the runtime from the registry -- see :meth:`dispose`
        for that. Called by ``MatchManager.finish_match``.
        """
        runtime = self._runtimes.get(match_id)
        if runtime is not None:
            runtime.request_cancel()

    def dispose(self, match_id: str) -> None:
        """Cancel and forget ``match_id``'s runtime, if any.

        Called by ``MatchManager.dispose_match``. Idempotent: disposing an
        id with no registered runtime (e.g. a match that never went ACTIVE)
        is a no-op.
        """
        self.cancel(match_id)
        self._runtimes.pop(match_id, None)

    async def wait_stopped(self, match_id: str) -> None:
        """Await ``match_id``'s runtime task's actual completion, if any."""
        runtime = self._runtimes.get(match_id)
        if runtime is not None:
            await runtime.wait_stopped()

    async def submit_command(self, match_id: str, command: Command) -> bool:
        """Enqueue ``command`` for ``match_id``'s next eligible tick.

        Returns ``False`` (rather than raising) both for an unknown/never-
        started ``match_id`` and for a rejected duplicate/replayed sequence
        -- from a transport caller's perspective both are simply "not
        accepted", and ``MatchRuntime.submit_command`` already documents the
        duplicate-sequence case as a non-exceptional return value.
        """
        runtime = self._runtimes.get(match_id)
        if runtime is None:
            return False
        return await runtime.submit_command(command)

    def __len__(self) -> int:
        return len(self._runtimes)
