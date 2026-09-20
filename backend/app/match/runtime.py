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
import logging
from collections.abc import Awaitable, Callable

from nether_earth import engine as engine_module
from nether_earth.collision import RobotFixture
from nether_earth.commands import Command
from nether_earth.events import Event
from nether_earth.ids import PlayerId
from nether_earth.map import WorldMap
from nether_earth.state import GameState

from app.match.models import Match, MatchRuntimeState

logger = logging.getLogger(__name__)

#: Fires once after every successful `engine.step` call, in tick order, with
#: that tick's resulting `GameState` and `Event` tuple. Generic over only
#: types this module already imports (`GameState`, `Event`) so this module
#: never imports `app.transport` -- the transport layer supplies the
#: concrete callback (M7 Task 6, issue #95; see `manager.py`'s
#: `on_tick_factory`). Awaited synchronously and serially by the sole tick
#: task (see `_advance_one_tick`), so calls never race or reorder each
#: other; never invoked for a tick whose `engine.step` raised. This sits on
#: the tick loop's own critical path -- a slow observer delays every later
#: tick of this match (see `_advance_one_tick`'s note on that cost).
TickObserver = Callable[[GameState, tuple[Event, ...]], Awaitable[None]]

#: A second, separate observer (M7 Task 8, issue #97): fires once after
#: every successful `engine.step` call, exactly like `TickObserver`, but
#: also carries the tick number and the exact accepted command batch that
#: was applied -- the one thing `TickObserver` does not expose (see that
#: type's own docstring: only the resulting `GameState`/`Event`s). A
#: second callback rather than widening `TickObserver`'s own tuple was the
#: deliberate choice here: `TickObserver` is already depended on by Task
#: 6/7's callers (`app.transport.snapshots.make_tick_broadcaster`,
#: `MatchManager.on_tick_factory`) with its exact two-argument shape, and
#: changing it would force every existing call site to change too for a
#: capability (the command stream) only the replay writer needs. Awaited
#: synchronously, immediately *before* `TickObserver` (see
#: `_advance_one_tick`'s Important I1 note on why that order is load-
#: bearing, not arbitrary), under the same single-owner-task,
#: no-reordering guarantee.
TickCommandObserver = Callable[
    [int, tuple[Command, ...], GameState, tuple[Event, ...]], Awaitable[None]
]

#: Authoritative simulation tick rate (`_specs/technical-spec.md` "Authoritative
#: simulation: 20 Hz"). Named constant, not a magic literal, per the task brief.
TICK_RATE_HZ: float = 20.0
TICK_INTERVAL_S: float = 1.0 / TICK_RATE_HZ

#: How often the tick loop polls a non-ACTIVE match (e.g. PAUSED_DISCONNECTED)
#: for a state change, instead of busy-looping. Cheap relative to the 20 Hz
#: tick interval; not itself gameplay-authoritative in any way.
DEFAULT_PAUSE_POLL_INTERVAL_S: float = 0.05

#: How many consecutive tick-overruns (a step taking longer than one tick
#: interval) trigger a repeated warning log. Purely observability -- ticking
#: itself is never skipped or batched to "catch up"; see `_run`.
_OVERRUN_WARNING_EVERY_N_TICKS: int = 100


def _assert_called_from_tasks_loop(task: asyncio.Task[None]) -> None:
    """Raise ``RuntimeError`` if the calling thread does not own ``task``'s loop.

    ``asyncio.get_running_loop()`` itself already raises ``RuntimeError``
    loudly when the calling thread has no running loop at all (e.g. a
    FastAPI sync endpoint running in Starlette's worker threadpool); this
    additionally catches the rarer case of a *different* loop running on the
    calling thread. Both are "you cannot safely touch this task from here"
    and both must fail loudly, never silently no-op (issue #93 review).
    """
    running_loop = asyncio.get_running_loop()
    if running_loop is not task.get_loop():
        raise RuntimeError(
            "MatchRuntime task methods must be called from the event loop thread "
            "that owns the tick task; asyncio.Task.cancel() is not thread-safe "
            "across event loops. Use loop.call_soon_threadsafe(...) instead."
        )


class MatchRuntime:
    """Owns the fixed-tick loop and command queue for exactly one match.

    One ``asyncio.Task`` runs :meth:`_run` for the lifetime of this object
    (started explicitly via :meth:`start`, stopped via :meth:`request_cancel`
    / :meth:`wait_stopped`). That single task is the sole caller of
    :meth:`_advance_one_tick`, which already serializes the drain -> step ->
    bookkeeping sequence by construction (single-owner-task discipline).
    ``_step_lock`` wraps the *entire* drain/step/bookkeeping sequence inside
    :meth:`_advance_one_tick` (not just the ``engine.step`` call) so that
    guarantee would still hold even if a future caller ever triggered a tick
    from outside the loop task -- the lock's scope must match what it
    documents to protect, or it is a false guarantee (see issue #93 review).
    """

    def __init__(
        self,
        match: Match,
        *,
        tick_rate_hz: float = TICK_RATE_HZ,
        world: WorldMap | None = None,
        robots: tuple[RobotFixture, ...] = (),
        pause_poll_interval_s: float = DEFAULT_PAUSE_POLL_INTERVAL_S,
        on_tick: TickObserver | None = None,
        on_tick_commands: TickCommandObserver | None = None,
        require_announcement: bool = False,
    ) -> None:
        if tick_rate_hz <= 0:
            raise ValueError("tick_rate_hz must be positive")
        self._match = match
        self._interval_s = 1.0 / tick_rate_hz
        self._pause_poll_interval_s = pause_poll_interval_s
        self._world = world
        self._robots = robots
        self._on_tick = on_tick
        self._on_tick_commands = on_tick_commands
        self._require_announcement = require_announcement
        self._announced = asyncio.Event()

        self._pending: list[Command] = []
        self._last_accepted_sequence: dict[PlayerId, int] = {}
        self._queue_lock = asyncio.Lock()
        self._step_lock = asyncio.Lock()

        self._task: asyncio.Task[None] | None = None
        self.tick_count = 0
        self.last_events: tuple[Event, ...] = ()

    def announce_started(self) -> None:
        """Release this runtime's first tick, if ``require_announcement=True``.

        The *structural* fix for the "first tick races the code that starts
        this runtime" hazard: a caller that needs its own "match started"
        messaging to reach clients strictly before any tick's own broadcast
        passes ``require_announcement=True`` at construction and calls this
        exactly once it has finished that messaging (see
        ``app.transport.ws``'s ``ClientSetReady`` handler). Until this is
        called, :meth:`_run` never advances past its very first tick check --
        not "probably won't", structurally cannot, regardless of I/O
        backpressure or scheduling.

        A harmless no-op if ``require_announcement=False`` (the event is
        simply never awaited) or if called more than once (idempotent, like
        ``asyncio.Event.set()``). Must be called from the event loop thread
        that owns this runtime's tick task, like every other method here
        that touches this object's asyncio primitives.
        """
        self._announced.set()

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

        ``asyncio.Task.cancel()`` is documented as callable from anywhere,
        but it is only *thread-safe* when called from the thread that owns
        the task's event loop -- calling it from a different thread (e.g. a
        FastAPI sync/non-``async def`` endpoint, which Starlette runs in a
        worker threadpool) races the loop's internals and can silently fail
        to schedule the cancellation at all. This method therefore asserts
        it is being called from the task's own loop thread and raises
        ``RuntimeError`` loudly rather than risking that silent no-op; a
        caller that legitimately needs to cancel from another thread should
        route through ``loop.call_soon_threadsafe(runtime.request_cancel)``
        instead (where ``loop`` is the task's own loop, e.g.
        ``self._task.get_loop()``).
        """
        if self._task is None:
            return
        _assert_called_from_tasks_loop(self._task)
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

        Safe to call concurrently from multiple coroutines in the sense that
        the per-match ``asyncio.Lock`` below makes each individual
        accept-or-reject decision atomic: no two concurrent calls can ever
        observe/mutate ``_last_accepted_sequence``/``_pending`` in a torn
        state, and once a command *is* accepted, `engine.step`'s own
        deterministic ``(player.value, sequence)`` sort makes its place in
        that tick's applied order independent of submission/scheduling
        order.

        This does **not** mean the *accepted set* is independent of
        scheduling when two concurrent calls submit out-of-order sequences
        for the *same* player: the dedup rule is a high-water-mark
        (``sequence <= last_accepted`` is rejected), so which of two
        concurrently-submitted sequences "wins" the lock first determines
        which one raises the high-water mark and which one is then rejected
        as stale. e.g. sequences 6 and 7 for the same player submitted
        concurrently deterministically resolve to "whichever's `await`
        acquires the lock first is accepted; the other is rejected if it is
        `<=` the winner" -- not to "both accepted". See
        ``test_out_of_order_concurrent_submission_for_same_player_is_scheduling_dependent_by_design``
        in ``tests/match/test_runtime.py`` for a pinned-down example of both
        deterministic-given-a-fixed-schedule outcomes.

        **Callers submitting multiple commands for the same player MUST
        serialize those submissions** (`await` each ``submit_command`` call
        to completion before reading/dispatching the next inbound command
        for that player) rather than firing them concurrently -- a future
        WebSocket handler (Task 5) should await each inbound frame's
        ``submit_command`` before reading the connection's next frame, which
        naturally guarantees in-order submission per connection/player. This
        is a transport-layer discipline requirement, not something this
        method can enforce on the caller's behalf.
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
        consecutive_overruns = 0
        try:
            if self._require_announcement:
                # Structural: genuinely cannot tick before `announce_started()`
                # is called, no matter how slow that caller's own I/O is --
                # not a timing-based approximation of "probably after".
                await self._announced.wait()
                next_tick_at = loop.time()
            else:
                # No announcement to race: wait one full interval before the
                # first tick, same as every later one, so a caller with no
                # observer at all still gets a steady 1/tick_rate_hz cadence
                # from the start rather than an immediate tick 1.
                next_tick_at = loop.time() + self._interval_s
                await asyncio.sleep(self._interval_s)
            while True:
                if self._match.state is MatchRuntimeState.FINISHED:
                    return
                if self._match.state is not MatchRuntimeState.ACTIVE:
                    # PAUSED_DISCONNECTED (or, defensively, WAITING): cheap
                    # poll, never a busy loop, and never advances the engine.
                    await asyncio.sleep(self._pause_poll_interval_s)
                    # Resync the schedule so resuming ACTIVE does not
                    # trigger a burst of "catch-up" ticks for time spent
                    # paused.
                    next_tick_at = loop.time()
                    consecutive_overruns = 0
                    continue

                await self._advance_one_tick()

                # Drift-compensated scheduling: the next tick's target time
                # is always `previous target + fixed interval`, not
                # `now + interval` (which would accumulate scheduler
                # jitter). This never feeds into `engine.step` itself --
                # every tick is still exactly one `step` call, full stop.
                next_tick_at += self._interval_s
                sleep_for = next_tick_at - loop.time()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                    consecutive_overruns = 0
                else:
                    # Fell behind (e.g. a slow tick): resync to now rather
                    # than firing a burst of zero-wait catch-up ticks with
                    # stale target times. Critically, still `await
                    # asyncio.sleep(0)` here rather than looping straight
                    # back to the top: on an uncontended `asyncio.Lock`,
                    # `.acquire()` does not suspend (CPython fast path), and
                    # `engine.step` is synchronous, so a persistently slow
                    # match's loop would otherwise never yield to the event
                    # loop at all -- starving every other match's tick loop
                    # (and, once Task 5 lands, every WebSocket task) on the
                    # same event loop (issue #93 review, Important #1).
                    consecutive_overruns += 1
                    if consecutive_overruns % _OVERRUN_WARNING_EVERY_N_TICKS == 0:
                        logger.warning(
                            "match %s tick loop has overrun its %.4fs tick budget for "
                            "%d consecutive ticks (engine.step is taking longer than one "
                            "tick interval)",
                            self._match.match_id,
                            self._interval_s,
                            consecutive_overruns,
                        )
                    await asyncio.sleep(0)
                    next_tick_at = loop.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            # An unhandled exception here would otherwise kill this task
            # silently: nothing in production awaits it (`request_cancel` is
            # fire-and-forget), so asyncio's "Task exception was never
            # retrieved" warning may not fire until GC, `match.state` stays
            # ACTIVE, and the match freezes forever with zero log output
            # (issue #93 review, Important #3). Log loudly and re-raise so
            # the task still ends in an observable failed state for
            # anything that does inspect it (e.g. tests, future
            # monitoring), rather than swallowing the exception outright.
            # Deciding *what* MatchManager/the match layer should do about a
            # crashed runtime (flip match.state, notify clients, ...) is a
            # lifecycle policy decision left to a later task -- this module
            # only guarantees the failure is loud, not silent.
            logger.exception(
                "match %s tick loop crashed; ticking has stopped but match.state "
                "remains %s",
                self._match.match_id,
                self._match.state,
            )
            raise

    async def _advance_one_tick(self) -> None:
        # The entire drain -> step -> bookkeeping sequence lives inside
        # `_step_lock`, not just the `engine.step` call, so the lock's scope
        # actually matches what its class docstring promises: a future
        # caller triggering a tick from outside the loop task could not
        # observe or apply a partial/interleaved batch (issue #93 review,
        # Important #2).
        async with self._step_lock:
            commands = await self._drain_queue()
            state = self._match.game_state
            if state is None:
                # Defensive: a runtime should only ever be started for a
                # match that has already gone ACTIVE, at which point
                # MatchManager has already produced tick-0 game_state via
                # engine.new_game. Not reachable through the documented
                # start() call sites.
                raise RuntimeError("MatchRuntime ticked before match.game_state was initialized")
            # world=None/robots=() (the current MatchManager call site's
            # default) means engine.step skips every collision/heli-pad/
            # launch/robot_moves check this tick -- a pre-existing gap from
            # M7 Task 2 (manager.py already documents that a real WorldMap
            # is deferred to whichever task first needs one for
            # engine.step; this module is that first caller, so it is
            # tracked here too rather than only in manager.py).
            new_state, events = engine_module.step(
                state, commands, world=self._world, robots=self._robots
            )
            self._match.game_state = new_state
            self.last_events = events
            self.tick_count += 1

        # Outside `_step_lock`: notifying observers is not part of the
        # drain/step/bookkeeping sequence the lock's scope documents (see the
        # class docstring), and a slow observer (e.g. broadcasting over a
        # WebSocket) must never be able to widen that lock's hold time. The
        # single-owner-task discipline (`_run` is the sole caller of this
        # method) still guarantees observers see ticks in strict order with
        # no interleaving, even though this call sits outside the lock.
        #
        # `on_tick_commands` fires *before* `on_tick`, deliberately, not
        # arbitrarily (M7 Task 8 review, Important I1): `request_cancel`
        # (called by `MatchManager.finish_match`) delivers
        # `asyncio.CancelledError` at this task's next suspension point,
        # which -- if `on_tick` (a WebSocket broadcast) ran first -- would
        # most likely be *inside* that `await`, since it is the slower of
        # the two. A cancellation landing there would skip
        # `on_tick_commands` entirely, silently dropping this tick's
        # commands from a replay writer's gameplay stream even though
        # `match.game_state`/`tick_count` (read moments later by
        # `MatchManager.finish_match` -> a bound `on_match_finish` hook)
        # already reflect this tick -- a persisted artifact whose
        # `final_tick` is ahead of its own recorded command stream. Running
        # the (cheap, local, synchronous-under-the-hood) command recorder
        # first closes that window: by the time `on_tick`'s slower/
        # network-bound await can be cancelled, this tick's commands are
        # already durably recorded.
        if self._on_tick_commands is not None:
            await self._on_tick_commands(self.tick_count, commands, new_state, events)

        # Cost, not just an ordering guarantee: this `await` is still on the
        # tick loop's own critical path -- a slow/backpressured `on_tick`
        # (e.g. a peer whose `send_text` is stalled) delays every later tick
        # of *this match*, feeding the same overrun path documented above
        # (issue #93 review, Important #1). A fix (e.g. a per-connection
        # queue with drop-oldest, decoupling broadcast speed from tick
        # cadence) is deliberately deferred, not built here -- YAGNI until a
        # real workload shows this coupling matters.
        if self._on_tick is not None:
            await self._on_tick(new_state, events)


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
        on_tick: TickObserver | None = None,
        on_tick_commands: TickCommandObserver | None = None,
        require_announcement: bool = False,
    ) -> MatchRuntime:
        """Start (or return the already-running) runtime for ``match``.

        Synchronous and non-blocking (``asyncio.create_task`` schedules but
        does not run the loop inline), so this is safe to call from
        ``MatchManager``'s synchronous ``_start_match_locked`` -- it must
        still be called from a thread with a running event loop (true of any
        real deployment, since this is only ever wired up from an async
        FastAPI app; a caller with no running loop gets asyncio's own
        ``RuntimeError`` rather than a silently swallowed no-op).

        ``on_tick``/``on_tick_commands``/``require_announcement`` are passed
        straight through to this match's ``MatchRuntime`` -- see
        :data:`TickObserver`, :data:`TickCommandObserver`, and
        :meth:`MatchRuntime.announce_started`. If ``require_announcement`` is
        set, the caller must eventually call
        :meth:`announce_started`/``MatchRuntime.announce_started`` for this
        ``match_id`` or this runtime never ticks.

        If a runtime for ``match.match_id`` already exists (i.e. this is
        called a second time for the same match), every keyword argument is
        silently ignored and the existing runtime's original values keep
        being used -- ``MatchManager``'s WAITING -> ACTIVE transition (the
        only production call site) only ever calls this once per match, so
        this is not reachable in practice, but a caller relying on a second
        ``start()`` call to *change* any of them on a live match would be
        surprised; construct a new ``MatchRuntimeRegistry``/``MatchRuntime``
        instead if that is ever needed.
        """
        runtime = self._runtimes.get(match.match_id)
        if runtime is None:
            runtime = MatchRuntime(
                match,
                tick_rate_hz=self._tick_rate_hz,
                world=world,
                robots=robots,
                pause_poll_interval_s=self._pause_poll_interval_s,
                on_tick=on_tick,
                on_tick_commands=on_tick_commands,
                require_announcement=require_announcement,
            )
            self._runtimes[match.match_id] = runtime
        runtime.start()
        return runtime

    def get(self, match_id: str) -> MatchRuntime | None:
        return self._runtimes.get(match_id)

    def announce_started(self, match_id: str) -> None:
        """Release ``match_id``'s runtime's first tick, if any (see
        :meth:`MatchRuntime.announce_started`). A silent no-op for an
        unknown ``match_id``.
        """
        runtime = self._runtimes.get(match_id)
        if runtime is not None:
            runtime.announce_started()

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

        Any commands still sitting in the runtime's pending queue at
        disposal time (submitted but not yet applied by a tick) are simply
        discarded along with the rest of the ``MatchRuntime`` object -- no
        record of them is kept anywhere. Replay persistence (Task 8) logs
        commands as they are *applied* by ``engine.step``, not as they sit
        queued, so this is expected and not this task's concern; noted here
        for Task 8's benefit in case a truly-final tick before disposal is
        ever desired.
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
