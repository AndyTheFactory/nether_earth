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

import logging
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from nether_earth.events import Event
from nether_earth.map import WorldMap
from nether_earth.state import GameState

from app.config import Settings, load_settings
from app.logging_setup import configure_logging
from app.match.manager import MatchManager
from app.match.models import Match
from app.match.reconnect import (
    DEFAULT_GRACE_SECONDS,
    DisconnectEvent,
    DisconnectNotifier,
    ReconnectCoordinator,
)
from app.match.runtime import TICK_RATE_HZ, MatchRuntimeRegistry, TickCommandObserver, TickObserver
from app.match.world import load_standard_world
from app.replay import ReplayWriter, make_replay_lifecycle_notifier, make_replay_tick_recorder
from app.transport import ConnectionRegistry, create_websocket_router
from app.transport.disconnects import make_disconnect_notifier
from app.transport.snapshots import make_tick_broadcaster
from app.transport.victory import make_victory_finalizer

logger = logging.getLogger(__name__)


def _replay_dir_status(base_dir: Path) -> str:
    """``"ok"`` iff a file can be created (and removed) in ``base_dir`` right now."""
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=base_dir, prefix=".ready-"):
            pass
    except OSError:
        return "unwritable"
    return "ok"


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


def _compose_tick_observers(*observers: TickObserver) -> TickObserver:
    """Return a ``TickObserver`` that awaits every one of ``observers`` in order."""

    async def _on_tick(state: GameState, events: tuple[Event, ...]) -> None:
        for observer in observers:
            await observer(state, events)

    return _on_tick


def create_app(
    *,
    replay_dir: Path | None = None,
    reconnect_grace_seconds: float = DEFAULT_GRACE_SECONDS,
    tick_rate_hz: float = TICK_RATE_HZ,
    _reconnect_monotonic_clock: Callable[[], float] = time.monotonic,
    world: WorldMap | None = None,
    settings: Settings | None = None,
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

    ``_reconnect_monotonic_clock`` is a leading-underscore, test-only seam
    (never overridden by a real deployment, which always wants the real
    ``time.monotonic``): it exists solely so a test can force two real,
    sequential disconnects to compute the exact same grace deadline (a
    genuine tie -- see ``ReconnectCoordinator._resolve_expiry``'s own
    docstring for why only an exact tie resolves to no-contest) without
    hand-assembling a second copy of this function's wiring (M7 Task 10
    review, Important I3).

    ``settings`` carries deployment-only configuration (``app.config``);
    ``None`` means development defaults. An explicit ``replay_dir`` wins over
    ``settings.replay_dir``. Production disables the interactive API docs.
    """
    settings = settings if settings is not None else Settings()
    if replay_dir is None:
        replay_dir = settings.replay_dir
    docs_enabled = not settings.production
    shutting_down = False

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal shutting_down
        logger.info(
            "backend started",
            extra={
                "event": "process_started",
                "environment": "production" if settings.production else "development",
                "replay_dir": str(replay_writer.base_dir),
                "public_base_url": settings.public_base_url,
                "max_matches": settings.max_matches,
            },
        )
        yield
        shutting_down = True
        logger.info(
            "backend stopping; in-memory matches end with the process",
            extra={"event": "process_stopping", "matches": len(match_manager)},
        )

    fastapi_app = FastAPI(
        title="Nether Earth",
        version="0.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    # The scenario-overlaid real map every match on this app plays on (M9.1
    # audit gap G1). ``world`` lets a test inject a fixture world; a real
    # deployment always loads the standard v1 map from ``data/maps``.
    resolved_world = world if world is not None else load_standard_world()

    connection_registry = ConnectionRegistry()
    runtime_registry = MatchRuntimeRegistry(tick_rate_hz=tick_rate_hz)
    replay_writer = ReplayWriter(base_dir=replay_dir)

    def _on_tick_factory(match: Match) -> TickObserver:
        # Bound per match at start time (see `MatchManager.on_tick_factory`'s
        # docstring): broadcasts a fresh authoritative snapshot to every
        # connection registered for `match.match_id` after each tick this
        # match's `MatchRuntime` completes (M7 Task 6, issue #95).
        # Snapshot first, then victory finalization (M9.1 audit gap G2), so
        # the final authoritative snapshot always precedes ``finished``.
        return _compose_tick_observers(
            make_tick_broadcaster(connection_registry, match.match_id),
            make_victory_finalizer(match_manager, connection_registry, match),
        )

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
        monotonic_clock=_reconnect_monotonic_clock,
    )

    match_manager = MatchManager(
        world=resolved_world,
        runtime=runtime_registry,
        on_tick_factory=_on_tick_factory,
        reconnect=reconnect_coordinator,
        on_tick_commands_factory=_on_tick_commands_factory,
        on_match_start=replay_writer.start_match,
        on_match_finish=replay_writer.finish_match,
        max_matches=settings.max_matches,
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

    fastapi_app.state.settings = settings
    fastapi_app.state.match_manager = match_manager
    fastapi_app.state.runtime_registry = runtime_registry
    fastapi_app.state.connection_registry = connection_registry

    fastapi_app.include_router(
        create_websocket_router(
            match_manager,
            runtime_registry,
            connection_registry,
            allowed_origins=settings.allowed_origins,
        )
    )

    @fastapi_app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Liveness: the process is up and serving HTTP. No gameplay logic."""
        return {"status": "ok"}

    @fastapi_app.get("/ready", tags=["operations"])
    def ready(response: Response) -> dict[str, object]:
        """Readiness: can this process accept and persist new matches right now?

        503 while shutting down or when the replay directory is not writable
        (matches would run but their replay artifacts would be lost). The
        counts are operational totals only, no match or player data.
        """
        checks = {
            "replay_dir": _replay_dir_status(replay_writer.base_dir),
            "accepting": "no" if shutting_down else "ok",
        }
        is_ready = all(value == "ok" for value in checks.values())
        if not is_ready:
            response.status_code = 503
        return {
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
            "matches": len(match_manager),
            "runtimes": len(runtime_registry),
            "connections": connection_registry.connection_count(),
        }

    return fastapi_app


_settings = load_settings()
configure_logging(_settings.log_level, _settings.log_format)
app = create_app(settings=_settings)
