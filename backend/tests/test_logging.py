"""Structured lifecycle logging, redaction and readiness (M10.5, issue #126)."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.logging_setup import StructuredFormatter
from app.main import create_app
from app.match.manager import MatchManager
from app.replay.writer import ReplayWriter
from tests.transport._helpers import _create, _join, _ready


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [str(getattr(record, "event", "")) for record in caplog.records]


def test_match_lifecycle_is_traceable_and_tokens_never_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    app = create_app(replay_dir=tmp_path, reconnect_grace_seconds=0.2)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws_a:
        with client.websocket_connect("/ws") as ws_b:
            created = _create(ws_a)
            joined = _join(ws_b, created["joinCode"])
            for ws, session in ((ws_a, created), (ws_b, joined)):
                _ready(
                    ws,
                    match_id=session["matchId"],
                    player_id=session["playerId"],
                    session_token=session["sessionToken"],
                )
        # player two dropped; the grace period expires into a forfeit.
        deadline = time.monotonic() + 5
        while "match_finished" not in _events(caplog) and time.monotonic() < deadline:
            time.sleep(0.05)

    events = _events(caplog)
    for expected in (
        "process_started",
        "match_created",
        "match_joined",
        "match_started",
        "replay_started",
        "player_disconnected",
        "match_finished",
        "replay_finalized",
        "process_stopping",
    ):
        assert expected in events, expected
    finished = next(r for r in caplog.records if getattr(r, "event", None) == "match_finished")
    assert finished.__dict__["match_id"] == created["matchId"]
    assert finished.__dict__["outcome"] == "forfeit"

    formatter = StructuredFormatter(json_lines=True)
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    for token in (created["sessionToken"], joined["sessionToken"]):
        assert token not in rendered


def test_json_formatter_emits_context_fields_and_exceptions() -> None:
    formatter = StructuredFormatter(json_lines=True)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "failed %s", ("x",), sys.exc_info()
        )
    record.__dict__.update({"event": "tick_loop_crashed", "match_id": "abc"})
    payload = json.loads(formatter.format(record))
    assert payload["msg"] == "failed x"
    assert payload["level"] == "ERROR"
    assert payload["event"] == "tick_loop_crashed"
    assert payload["match_id"] == "abc"
    assert "RuntimeError: boom" in payload["exc"]


def test_text_formatter_appends_key_values() -> None:
    record = logging.getLogger("t").makeRecord("t", logging.INFO, __file__, 1, "hi", (), None)
    record.__dict__["match_id"] = "abc"
    assert StructuredFormatter(json_lines=False).format(record).endswith("hi match_id=abc")


def test_ready_reports_counts_when_healthy(tmp_path: Path) -> None:
    with (
        TestClient(create_app(replay_dir=tmp_path)) as client,
        client.websocket_connect("/ws") as ws,
    ):
        _create(ws)
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"replay_dir": "ok", "accepting": "ok"}
    assert body["matches"] == 1
    assert body["connections"] == 1


def test_ready_fails_when_replay_dir_unwritable(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("")
    settings = Settings(replay_dir=not_a_dir)
    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/ready")
        assert client.get("/health").status_code == 200  # liveness is unaffected
    assert response.status_code == 503
    assert response.json()["checks"]["replay_dir"] == "unwritable"


def test_replay_write_failure_is_logged_once_and_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("")
    writer = ReplayWriter(base_dir=not_a_dir)
    manager = MatchManager(on_match_start=writer.start_match, on_match_finish=writer.finish_match)
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)  # starts the match -> start_match fails
    writer.record_tick(created.match_id, 1, (), ())
    writer.record_tick(created.match_id, 2, (), ())
    manager.finish_match(created.match_id)

    errors = [r for r in caplog.records if getattr(r, "event", None) == "replay_write_failed"]
    assert [r.levelno for r in errors if r.levelno >= logging.ERROR] == [logging.ERROR]
