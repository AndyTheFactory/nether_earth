"""Replay directory retention and orphan marking (security review NE-03)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from app.replay import mark_interrupted, prune_replays


def _artifact(base: Path, match_id: str, status: str, *, age_s: float = 0.0) -> Path:
    directory = base / match_id
    directory.mkdir(parents=True)
    meta = directory / "meta.json"
    meta.write_text(json.dumps({"match_id": match_id, "status": status}), encoding="utf-8")
    (directory / "commands.jsonl").write_text("", encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(meta, (stamp, stamp))
    return directory


def test_mark_interrupted_flips_only_in_progress(tmp_path: Path) -> None:
    _artifact(tmp_path, "a", "in_progress")
    _artifact(tmp_path, "b", "finished")
    assert mark_interrupted(tmp_path) == ["a"]
    assert json.loads((tmp_path / "a" / "meta.json").read_text())["status"] == "interrupted"
    assert json.loads((tmp_path / "b" / "meta.json").read_text())["status"] == "finished"
    assert mark_interrupted(tmp_path) == []  # idempotent


def test_mark_interrupted_skips_unreadable_meta(tmp_path: Path) -> None:
    """Review Focus 4: a corrupt or foreign file must not stop startup."""
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "meta.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "stray-file").write_text("x", encoding="utf-8")
    _artifact(tmp_path, "ok", "in_progress")
    assert mark_interrupted(tmp_path) == ["ok"]


def test_mark_interrupted_on_missing_dir_is_a_no_op(tmp_path: Path) -> None:
    assert mark_interrupted(tmp_path / "nope") == []


def test_prune_deletes_old_finished_and_interrupted_only(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-finished", "finished", age_s=100)
    _artifact(tmp_path, "old-interrupted", "interrupted", age_s=100)
    _artifact(tmp_path, "old-live", "in_progress", age_s=100)
    _artifact(tmp_path, "new-finished", "finished", age_s=1)
    deleted = prune_replays(tmp_path, max_age_s=50)
    assert sorted(deleted) == ["old-finished", "old-interrupted"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new-finished", "old-live"]


def test_prune_uses_injected_now(tmp_path: Path) -> None:
    _artifact(tmp_path, "m", "finished", age_s=0)
    assert prune_replays(tmp_path, max_age_s=10, now=time.time() + 11) == ["m"]


def test_startup_marks_orphans_interrupted(tmp_path: Path) -> None:
    _artifact(tmp_path, "orphan", "in_progress")
    with TestClient(create_app(settings=Settings(replay_dir=tmp_path))):
        pass
    assert json.loads((tmp_path / "orphan" / "meta.json").read_text())["status"] == "interrupted"


def test_retention_setting_is_optional() -> None:
    assert load_settings({}).replay_retention_days is None
    assert load_settings({"NETHER_EARTH_REPLAY_RETENTION_DAYS": "30"}).replay_retention_days == 30
    with pytest.raises(ConfigError, match="NETHER_EARTH_REPLAY_RETENTION_DAYS"):
        load_settings({"NETHER_EARTH_REPLAY_RETENTION_DAYS": "-1"})
