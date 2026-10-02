import inspect
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, create_app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_operations_endpoints_run_on_the_event_loop(tmp_path: Path) -> None:
    """NE-05: a threadpool handler iterating loop-mutated dicts can raise mid-iteration."""
    app = create_app(settings=Settings(replay_dir=tmp_path))
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}  # type: ignore[attr-defined]
    assert inspect.iscoroutinefunction(endpoints["/health"])
    assert inspect.iscoroutinefunction(endpoints["/ready"])


def test_ready_hides_counts_in_production(tmp_path: Path) -> None:
    """NE-11: load figures are not public in production."""
    settings = Settings(production=True, public_base_url="https://play.example.com", replay_dir=tmp_path)
    with TestClient(create_app(settings=settings)) as client:
        body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert set(body) == {"status", "checks"}


def test_ready_reports_counts_in_development(tmp_path: Path) -> None:
    with TestClient(create_app(settings=Settings(replay_dir=tmp_path))) as client:
        body = client.get("/ready").json()
    assert body["matches"] == 0 and body["runtimes"] == 0 and body["connections"] == 0
