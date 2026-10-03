import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
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


def test_ready_probes_the_replay_dir_off_the_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The writability probe does blocking filesystem I/O; it must not run on the loop thread."""
    seen: list[bool] = []

    def fake_status(base_dir: Path) -> str:
        seen.append(asyncio._get_running_loop() is not None)
        return "ok"

    monkeypatch.setattr(app_main, "_replay_dir_status", fake_status)
    with TestClient(create_app(settings=Settings(replay_dir=tmp_path))) as client:
        assert client.get("/ready").status_code == 200
    assert seen == [False]


def test_ready_reports_counts_in_production_to_loopback(tmp_path: Path) -> None:
    """Operators read counts from inside the container; gateway traffic is not loopback."""
    settings = Settings(production=True, public_base_url="https://play.example.com", replay_dir=tmp_path)
    with TestClient(create_app(settings=settings), client=("127.0.0.1", 50000)) as client:
        body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["matches"] == 0 and body["runtimes"] == 0 and body["connections"] == 0
