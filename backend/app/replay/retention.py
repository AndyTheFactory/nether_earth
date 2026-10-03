"""Replay directory housekeeping: orphan marking at startup and age-based pruning.

Both functions do blocking filesystem work and are meant to be called off
the event loop (``asyncio.to_thread``). Neither touches a live match's
artifact: ``mark_interrupted`` runs only at startup, when no match can be
live, and ``prune_replays`` never deletes a ``status: "in_progress"``
artifact.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from app.replay.writer import write_meta_atomic

logger = logging.getLogger(__name__)

_META_FILENAME = "meta.json"


def _read_meta(directory: Path) -> dict[str, Any] | None:
    """Parsed ``meta.json`` for an artifact directory, or ``None`` if absent/unreadable."""
    path = directory / _META_FILENAME
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _artifact_dirs(base_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in base_dir.iterdir() if p.is_dir())
    except OSError:
        return []


def mark_interrupted(base_dir: Path) -> list[str]:
    """Flip every ``in_progress`` artifact under ``base_dir`` to ``interrupted``.

    Startup only: a process that is just starting has no live matches, so
    every ``in_progress`` artifact belongs to a match the previous process
    never finished. Returns the ids changed. Unreadable directories are
    skipped with a warning, never raised.
    """
    changed: list[str] = []
    for directory in _artifact_dirs(base_dir):
        meta = _read_meta(directory)
        if meta is None:
            logger.warning(
                "skipping replay directory with unreadable meta.json",
                extra={"event": "replay_meta_unreadable", "path": str(directory)},
            )
            continue
        if meta.get("status") != "in_progress":
            continue
        meta["status"] = "interrupted"
        try:
            write_meta_atomic(directory, meta)
        except OSError:
            logger.warning(
                "could not mark replay artifact interrupted",
                exc_info=True,
                extra={"event": "replay_mark_interrupted_failed", "path": str(directory)},
            )
            continue
        changed.append(directory.name)
    if changed:
        logger.info(
            "marked orphaned replay artifacts interrupted",
            extra={"event": "replay_orphans_marked", "count": len(changed)},
        )
    return changed


def prune_replays(base_dir: Path, *, max_age_s: float, now: float | None = None) -> list[str]:
    """Delete artifact directories finished/interrupted more than ``max_age_s`` ago.

    Age is ``meta.json``'s mtime, which the writer rewrites at finish, so
    it measures time since the match ended. ``in_progress`` artifacts are
    never deleted. Returns the ids removed.
    """
    current = time.time() if now is None else now
    removed: list[str] = []
    for directory in _artifact_dirs(base_dir):
        meta = _read_meta(directory)
        if meta is None or meta.get("status") == "in_progress":
            continue
        try:
            age = current - (directory / _META_FILENAME).stat().st_mtime
            if age < max_age_s:
                continue
            shutil.rmtree(directory)
        except OSError:
            logger.warning(
                "could not prune replay artifact",
                exc_info=True,
                extra={"event": "replay_prune_failed", "path": str(directory)},
            )
            continue
        removed.append(directory.name)
    if removed:
        logger.info(
            "pruned replay artifacts past retention",
            extra={"event": "replay_pruned", "count": len(removed)},
        )
    return removed
