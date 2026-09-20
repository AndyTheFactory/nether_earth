"""FastAPI app wiring: HTTP health endpoint plus the ``/ws`` WebSocket transport.

``create_app`` is a factory (rather than only a module-level ``app``) so each
call gets its own isolated ``MatchManager``/``MatchRuntimeRegistry``/
``ConnectionRegistry`` -- important for tests, which must not leak matches or
connections across independent app instances. ``app`` below is the one
instance used by a real deployment (e.g. ``uvicorn app.main:app``).
"""

from fastapi import FastAPI

from app.match.manager import MatchManager
from app.match.models import Match
from app.match.reconnect import ReconnectCoordinator
from app.match.runtime import MatchRuntimeRegistry, TickObserver
from app.transport import ConnectionRegistry, create_websocket_router
from app.transport.disconnects import make_disconnect_notifier
from app.transport.snapshots import make_tick_broadcaster


def create_app() -> FastAPI:
    fastapi_app = FastAPI(title="Nether Earth", version="0.0.0")

    connection_registry = ConnectionRegistry()
    runtime_registry = MatchRuntimeRegistry()

    def _on_tick_factory(match: Match) -> TickObserver:
        # Bound per match at start time (see `MatchManager.on_tick_factory`'s
        # docstring): broadcasts a fresh authoritative snapshot to every
        # connection registered for `match.match_id` after each tick this
        # match's `MatchRuntime` completes (M7 Task 6, issue #95).
        return make_tick_broadcaster(connection_registry, match.match_id)

    reconnect_coordinator = ReconnectCoordinator(
        notify=make_disconnect_notifier(connection_registry),
        runtime_registry=runtime_registry,
    )

    match_manager = MatchManager(
        runtime=runtime_registry,
        on_tick_factory=_on_tick_factory,
        reconnect=reconnect_coordinator,
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
