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
from app.match.runtime import MatchRuntimeRegistry, TickObserver
from app.transport import ConnectionRegistry, create_websocket_router, make_tick_broadcaster


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

    match_manager = MatchManager(runtime=runtime_registry, on_tick_factory=_on_tick_factory)

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
