"""FastAPI app wiring: HTTP health endpoint plus the ``/ws`` WebSocket transport.

``create_app`` is a factory (rather than only a module-level ``app``) so each
call gets its own isolated ``MatchManager``/``MatchRuntimeRegistry``/
``ConnectionRegistry`` -- important for tests, which must not leak matches or
connections across independent app instances. ``app`` below is the one
instance used by a real deployment (e.g. ``uvicorn app.main:app``).

``replay_dir`` (M7 Task 8, issue #97) is likewise an explicit, optional
factory parameter rather than always falling back to
``ReplayWriter``'s own env-var/repo-relative default: a test building its
own app via ``create_app()`` must not silently write real replay artifacts
onto the developer's filesystem outside of a ``tmp_path`` -- see
``tests/transport/test_ws.py``'s ``client`` fixture, which passes a
``tmp_path``-scoped directory for exactly this reason.
"""

from pathlib import Path

from fastapi import FastAPI

from app.match.manager import MatchManager
from app.match.models import Match
from app.match.reconnect import (
    DEFAULT_GRACE_SECONDS,
    DisconnectEvent,
    DisconnectNotifier,
    ReconnectCoordinator,
)
from app.match.runtime import TICK_RATE_HZ, MatchRuntimeRegistry, TickCommandObserver, TickObserver
from app.replay import ReplayWriter, make_replay_lifecycle_notifier, make_replay_tick_recorder
from app.transport import ConnectionRegistry, create_websocket_router
from app.transport.disconnects import make_disconnect_notifier
from app.transport.snapshots import make_tick_broadcaster


def _combine_disconnect_notifiers(*notifiers: DisconnectNotifier) -> DisconnectNotifier:
    """Return a ``DisconnectNotifier`` that awaits every one of ``notifiers`` in order.

    ``ReconnectCoordinator`` takes exactly one ``notify`` callback (see its
    module docstring), but this app wants two independent consumers of the
    same disconnect-policy events: the WebSocket broadcast
    (``make_disconnect_notifier``) and the filesystem replay writer
    (``make_replay_lifecycle_notifier``, M7 Task 8, issue #97). Composing
    them here keeps both packages mutually unaware of each other, matching
    every other hook in this module.
    """

    async def _notify(event: DisconnectEvent) -> None:
        for notifier in notifiers:
            await notifier(event)

    return _notify


def create_app(
    *,
    replay_dir: Path | None = None,
    reconnect_grace_seconds: float = DEFAULT_GRACE_SECONDS,
    tick_rate_hz: float = TICK_RATE_HZ,
) -> FastAPI:
    """Build a fresh, fully-wired app instance.

    ``replay_dir``, if supplied, overrides where this app's ``ReplayWriter``
    persists match artifacts (see this module's own docstring); ``None``
    (the default) falls back to ``ReplayWriter``'s own
    ``$NETHER_EARTH_REPLAY_DIR``-or-repo-relative default, which is what a
    real deployment (the module-level ``app`` below) wants.

    ``reconnect_grace_seconds``/``tick_rate_hz`` default to this app's real
    production values (``ReconnectCoordinator``'s locked 60s grace,
    ``MatchRuntime``'s locked 20Hz tick rate -- see those modules' own
    docstrings for why those specific values are non-negotiable gameplay
    policy) and exist purely so a test can build the *exact* same
    composition-root wiring as a real deployment while substituting a short
    grace period/fast tick interval, instead of duplicating this function's
    wiring in a second, drift-prone copy (M7 Task 10, issue #99).
    """
    fastapi_app = FastAPI(title="Nether Earth", version="0.0.0")

    connection_registry = ConnectionRegistry()
    runtime_registry = MatchRuntimeRegistry(tick_rate_hz=tick_rate_hz)
    replay_writer = ReplayWriter(base_dir=replay_dir)

    def _on_tick_factory(match: Match) -> TickObserver:
        # Bound per match at start time (see `MatchManager.on_tick_factory`'s
        # docstring): broadcasts a fresh authoritative snapshot to every
        # connection registered for `match.match_id` after each tick this
        # match's `MatchRuntime` completes (M7 Task 6, issue #95).
        return make_tick_broadcaster(connection_registry, match.match_id)

    def _on_tick_commands_factory(match: Match) -> TickCommandObserver:
        # Bound per match at start time, mirroring `_on_tick_factory` above:
        # appends every tick's accepted command batch to this match's
        # filesystem replay artifact (M7 Task 8, issue #97).
        return make_replay_tick_recorder(replay_writer, match.match_id)

    reconnect_coordinator = ReconnectCoordinator(
        notify=_combine_disconnect_notifiers(
            make_disconnect_notifier(connection_registry),
            make_replay_lifecycle_notifier(replay_writer),
        ),
        grace_seconds=reconnect_grace_seconds,
        runtime_registry=runtime_registry,
    )

    match_manager = MatchManager(
        runtime=runtime_registry,
        on_tick_factory=_on_tick_factory,
        reconnect=reconnect_coordinator,
        on_tick_commands_factory=_on_tick_commands_factory,
        on_match_start=replay_writer.start_match,
        on_match_finish=replay_writer.finish_match,
    )
    # Breaks the construction-order cycle (this coordinator must exist
    # before `MatchManager` can be constructed with it, but the natural
    # finish hook is `MatchManager.finish_match` itself) -- see
    # `ReconnectCoordinator.bind_finish_hook`'s docstring (M7 Task 7
    # review, Important I3). This makes forfeit/no-contest finalization
    # go through the exact same path (state transition + runtime-loop
    # cancellation, plus any future finish-time logic such as Task 8's
    # replay persistence) as every other `FINISHED` transition.
    reconnect_coordinator.bind_finish_hook(match_manager.finish_match)

    fastapi_app.state.match_manager = match_manager
    fastapi_app.state.runtime_registry = runtime_registry
    fastapi_app.state.connection_registry = connection_registry

    fastapi_app.include_router(
        create_websocket_router(match_manager, runtime_registry, connection_registry)
    )

    @fastapi_app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Lightweight process health endpoint; contains no gameplay logic."""
        return {"status": "ok"}

    return fastapi_app


app = create_app()
