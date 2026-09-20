"""Replay-verification helper: reconstruct a persisted artifact and re-run it through the engine directly.

Scope (M7 Task 8, issue #97): given a completed ``ReplayWriter`` artifact,
reconstruct the accepted command stream from ``commands.jsonl`` *only*, feed
it to ``nether_earth.engine`` exactly the way
``nether_earth.replay.run_fixture`` already does for engine-side test
fixtures, and compare the reproduced final ``GameState`` snapshot against the
one ``ReplayWriter.finish_match`` persisted alongside the live match. This
directly proves the acceptance criterion "replaying persisted gameplay input
reproduces the final engine snapshot/result" -- and it does so by calling
``nether_earth.engine``/``nether_earth.replay`` directly, never by re-running
any backend/transport code, so it also proves the backend layer added no
gameplay logic of its own for this to depend on.

This module is backend-only, like the rest of ``app.replay``: it imports
``nether_earth.replay``/``nether_earth.snapshot`` (backend -> engine), never
the reverse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nether_earth.commands import Command
from nether_earth.ids import PlayerId
from nether_earth.map import BootstrapMap
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.scenario import Scenario
from nether_earth.snapshot import to_snapshot

from app.replay.writer import match_dir

__all__ = [
    "ReplayVerificationResult",
    "load_commands_by_tick",
    "load_meta",
    "verify_replay",
]


def load_meta(base_dir: Path, match_id: str) -> dict[str, Any]:
    """Return the parsed ``meta.json`` for ``match_id``'s artifact under ``base_dir``."""
    path = match_dir(base_dir, match_id) / "meta.json"
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    return loaded  # type: ignore[no-any-return]


def _command_from_json(data: dict[str, Any]) -> Command:
    """Inverse of ``ReplayWriter``'s ``_command_to_json`` -- see that function's own docstring
    for why only the generic ``Command`` base contract is supported today.
    """
    return Command(player=PlayerId.from_json(data["player"]), sequence=data["sequence"])


def load_commands_by_tick(base_dir: Path, match_id: str) -> dict[int, tuple[Command, ...]]:
    """Return ``match_id``'s persisted accepted command stream, keyed by tick.

    Reads ``commands.jsonl`` line by line (the on-disk format
    ``ReplayWriter.record_tick`` appends); a trailing blank line (e.g. the
    empty file ``ReplayWriter.start_match`` creates before any tick is
    appended) is skipped rather than raising.
    """
    path = match_dir(base_dir, match_id) / "commands.jsonl"
    commands_by_tick: dict[int, tuple[Command, ...]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            record = json.loads(line)
            commands_by_tick[record["tick"]] = tuple(
                _command_from_json(entry) for entry in record["commands"]
            )
    return commands_by_tick


@dataclass(frozen=True, slots=True)
class ReplayVerificationResult:
    """Outcome of replaying a persisted artifact independently through the engine.

    ``matches`` is the acceptance-criterion check itself: the reproduced
    final snapshot equals the one persisted at match finish. ``reproduced_
    snapshot``/``persisted_snapshot`` are exposed too so a caller (or a
    failing test assertion) can see exactly where they diverge, rather than
    only a boolean.
    """

    matches: bool
    reproduced_snapshot: dict[str, Any]
    persisted_snapshot: dict[str, Any] | None


def verify_replay(
    base_dir: Path,
    match_id: str,
    *,
    scenario: Scenario,
    map_data: BootstrapMap,
) -> ReplayVerificationResult:
    """Reconstruct ``match_id``'s persisted command stream and replay it through the engine directly.

    ``scenario``/``map_data`` are supplied by the caller rather than read
    back from ``meta.json`` verbatim: a ``Scenario`` is a code-defined value
    (referenced by id, not itself serialized -- see
    ``nether_earth.scenario``'s and ``nether_earth.replay``'s own
    conventions), so persisting only its ``id``/``map_id``/``map_version``
    identity in ``meta.json`` and requiring the verifying caller to supply
    the matching ``Scenario``/``BootstrapMap`` object mirrors exactly how a
    real ``ReplayFixture`` is constructed everywhere else in this codebase.

    This calls ``nether_earth.replay.run_fixture`` -- i.e. ``engine.new_game``
    plus one ``engine.step`` call per persisted tick -- directly. No backend
    module (``MatchManager``, ``MatchRuntime``, transport) is imported or
    exercised by this call path; only ``commands.jsonl``/``meta.json`` (the
    filesystem artifact) and the engine package itself are involved, proving
    the persisted command stream alone is sufficient to reproduce the match.
    """
    meta = load_meta(base_dir, match_id)
    commands_by_tick = load_commands_by_tick(base_dir, match_id)
    tick_count = meta["final_tick"]
    if tick_count is None:
        raise ValueError(
            f"match {match_id!r}'s artifact has no final_tick recorded "
            "(status is still 'in_progress' -- verify only a finished artifact)"
        )

    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=meta["seed"],
        tick_count=tick_count,
        commands_by_tick=commands_by_tick,
    )
    final_state, _events = run_fixture(fixture)
    reproduced_snapshot = to_snapshot(final_state)
    persisted_snapshot = meta.get("final_snapshot")

    return ReplayVerificationResult(
        matches=reproduced_snapshot == persisted_snapshot,
        reproduced_snapshot=reproduced_snapshot,
        persisted_snapshot=persisted_snapshot,
    )
