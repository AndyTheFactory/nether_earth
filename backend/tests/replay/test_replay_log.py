"""Tests for ``app.replay`` (M7 Task 8, issue #97).

Covers the acceptance criteria from the task brief:

- replaying a persisted accepted command stream through the engine directly
  (bypassing the backend entirely) reproduces the final engine snapshot;
- a pause's wall-clock duration never creates fake gameplay ticks in the
  persisted command stream;
- no session token/secret ever appears in a persisted artifact;
- two concurrent matches write to separate, non-colliding paths;
- an incomplete/crashed match's artifact stays distinguishable (``status:
  "in_progress"``) from a finalized one;
- the engine package (``nether_earth``) gains no new dependency on the
  backend from this task -- a static source check, since this is a property
  of what was (not) imported, not of runtime behavior.

Timing strategy (matching ``test_runtime.py``'s own precedent): ticks are
advanced by calling ``MatchRuntime._advance_one_tick`` directly, with no real
sleeps, so this stays fast and fully deterministic.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from nether_earth import engine as engine_module
from nether_earth.commands import Command
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap
from nether_earth.rules import RULES_VERSION, rules_content_hash
from nether_earth.scenario import Scenario, default_pvp_scenario
from nether_earth.snapshot import to_snapshot

from app.match.models import Match, MatchOutcome, MatchResult, MatchRuntimeState, PlayerSlot
from app.match.reconnect import ForfeitEvent, NoContestEvent, PausedEvent, ResumedEvent
from app.match.runtime import MatchRuntime
from app.replay import (
    ReplayRulesMismatchError,
    ReplayWriter,
    load_commands_by_tick,
    load_meta,
    make_replay_lifecycle_notifier,
    make_replay_tick_recorder,
    match_dir,
    verify_replay,
)

_ALICE_TOKEN = "alice-super-secret-session-token"
_BOB_TOKEN = "bob-super-secret-session-token"


def _build_match(match_id: str, *, seed: int = 42) -> tuple[Match, Scenario, BootstrapMap]:
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    game_state = engine_module.new_game(
        map_data, scenario, players=(PLAYER_ONE, PLAYER_TWO), seed=seed
    )
    match = Match(
        match_id=match_id,
        join_code="ABCDEF",
        seed=seed,
        state=MatchRuntimeState.ACTIVE,
        game_state=game_state,
    )
    match.players[PLAYER_ONE] = PlayerSlot(
        player_id=PLAYER_ONE, nickname="alice", session_token=_ALICE_TOKEN
    )
    match.players[PLAYER_TWO] = PlayerSlot(
        player_id=PLAYER_TWO, nickname="bob", session_token=_BOB_TOKEN
    )
    return match, scenario, map_data


async def _play_three_ticks(match: Match, writer: ReplayWriter) -> MatchRuntime:
    on_tick_commands = make_replay_tick_recorder(writer, match.match_id)
    runtime = MatchRuntime(match, on_tick_commands=on_tick_commands)

    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=0)) is True
    await runtime._advance_one_tick()

    assert await runtime.submit_command(Command(player=PLAYER_TWO, sequence=0)) is True
    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=1)) is True
    await runtime._advance_one_tick()

    await runtime._advance_one_tick()  # an empty-batch tick is still a real tick.

    return runtime


# -- replay reproduces final state -------------------------------------------


async def test_replay_reproduces_final_engine_snapshot(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-replay-ok")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    await _play_three_ticks(match, writer)
    writer.finish_match(match)

    meta = load_meta(tmp_path, match.match_id)
    assert meta["status"] == "finished"
    assert meta["final_tick"] == 3
    assert meta["seed"] == match.seed

    result = verify_replay(tmp_path, match.match_id, scenario=scenario, map_data=map_data)

    assert result.matches, (result.reproduced_snapshot, result.persisted_snapshot)
    assert result.reproduced_snapshot == to_snapshot(match.game_state)  # type: ignore[arg-type]


async def test_meta_records_engine_rules_version_and_hash(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-rules-identity")
    ReplayWriter(base_dir=tmp_path).start_match(match, scenario, map_data)

    meta = load_meta(tmp_path, match.match_id)
    assert meta["rules_version"] == RULES_VERSION
    assert meta["rules_hash"] == rules_content_hash()


@pytest.mark.parametrize(
    ("key", "value"),
    [("rules_version", "m7"), ("rules_hash", "0" * 64), ("rules_hash", None)],
)
async def test_replay_with_other_rules_is_rejected_before_replaying(
    tmp_path: Path, key: str, value: str | None
) -> None:
    match, scenario, map_data = _build_match("match-rules-mismatch")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)
    await _play_three_ticks(match, writer)
    writer.finish_match(match)

    meta_path = match_dir(tmp_path, match.match_id) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta[key] = value
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ReplayRulesMismatchError, match=key.replace("_", " ")):
        verify_replay(tmp_path, match.match_id, scenario=scenario, map_data=map_data)


async def test_replay_verification_reconstructs_command_stream_faithfully(tmp_path: Path) -> None:
    """The persisted command stream, read back, actually carries the applied commands."""
    match, scenario, map_data = _build_match("match-replay-commands")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    await _play_three_ticks(match, writer)
    writer.finish_match(match)

    commands_by_tick = load_commands_by_tick(tmp_path, match.match_id)
    assert commands_by_tick[1] == (Command(player=PLAYER_ONE, sequence=0),)
    # Recorded in submission order (the runtime's own queue-drain order, not
    # engine.step's internal (player, sequence) sort -- see
    # `_command_to_json`'s docstring: this module records exactly what was
    # *applied*, and `engine.step` re-sorts deterministically regardless of
    # the order its input tuple arrives in).
    assert set(commands_by_tick[2]) == {
        Command(player=PLAYER_TWO, sequence=0),
        Command(player=PLAYER_ONE, sequence=1),
    }
    assert commands_by_tick.get(3, ()) == ()


# -- pause wall-clock duration never becomes fake ticks ----------------------


async def test_pause_lifecycle_events_never_touch_gameplay_stream(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-pause")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    runtime_before_pause = await _play_three_ticks(match, writer)
    assert runtime_before_pause.tick_count == 3

    notifier = make_replay_lifecycle_notifier(writer)
    await notifier(
        PausedEvent(match=match, disconnected_player_id=PLAYER_ONE, grace_deadline_epoch_ms=123_456)
    )
    await notifier(ResumedEvent(match=match))
    await notifier(
        ForfeitEvent(match=match, forfeiting_player_id=PLAYER_ONE, winner_player_id=PLAYER_TWO)
    )
    await notifier(NoContestEvent(match=match))

    # The gameplay stream is completely untouched by any lifecycle event --
    # still exactly the 3 tick lines from before, no extra "tick" entries
    # invented for the wall-clock time spent "paused".
    commands_by_tick = load_commands_by_tick(tmp_path, match.match_id)
    assert set(commands_by_tick) == {1, 2, 3}

    lifecycle_path = match_dir(tmp_path, match.match_id) / "lifecycle.jsonl"
    lifecycle_lines = [
        line for line in lifecycle_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lifecycle_lines) == 4
    for line in lifecycle_lines:
        # Never a gameplay-shaped record: the "commands" key only ever
        # appears in commands.jsonl, never in a lifecycle event.
        assert '"commands"' not in line


# -- no secrets in any persisted file -----------------------------------------


async def test_no_session_tokens_in_any_persisted_file(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-secrets")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    await _play_three_ticks(match, writer)

    notifier = make_replay_lifecycle_notifier(writer)
    await notifier(
        PausedEvent(match=match, disconnected_player_id=PLAYER_ONE, grace_deadline_epoch_ms=999)
    )
    await notifier(ResumedEvent(match=match))

    writer.finish_match(match)

    directory = match_dir(tmp_path, match.match_id)
    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in directory.iterdir() if path.is_file()
    )
    assert _ALICE_TOKEN not in all_text
    assert _BOB_TOKEN not in all_text
    assert match.players[PLAYER_ONE].session_token not in all_text
    assert match.players[PLAYER_TWO].session_token not in all_text


# -- concurrent matches never collide -----------------------------------------


async def test_concurrent_matches_write_to_separate_non_colliding_paths(tmp_path: Path) -> None:
    match_a, scenario, map_data = _build_match("match-a", seed=1)
    match_b, _scenario_b, _map_data_b = _build_match("match-b", seed=2)
    writer = ReplayWriter(base_dir=tmp_path)

    writer.start_match(match_a, scenario, map_data)
    writer.start_match(match_b, scenario, map_data)

    await _play_three_ticks(match_a, writer)
    # match_b only ever gets one tick -- its own stream must not pick up
    # match_a's ticks or vice versa.
    on_tick_commands_b = make_replay_tick_recorder(writer, match_b.match_id)
    runtime_b = MatchRuntime(match_b, on_tick_commands=on_tick_commands_b)
    await runtime_b._advance_one_tick()

    writer.finish_match(match_a)
    writer.finish_match(match_b)

    dir_a = match_dir(tmp_path, match_a.match_id)
    dir_b = match_dir(tmp_path, match_b.match_id)
    assert dir_a != dir_b
    assert dir_a.exists() and dir_b.exists()

    commands_a = load_commands_by_tick(tmp_path, match_a.match_id)
    commands_b = load_commands_by_tick(tmp_path, match_b.match_id)
    assert set(commands_a) == {1, 2, 3}
    assert set(commands_b) == {1}

    meta_a = load_meta(tmp_path, match_a.match_id)
    meta_b = load_meta(tmp_path, match_b.match_id)
    assert meta_a["match_id"] == match_a.match_id
    assert meta_b["match_id"] == match_b.match_id
    assert meta_a["seed"] == 1
    assert meta_b["seed"] == 2


# -- incomplete/crashed artifacts are distinguishable from finished ones ------


async def test_unfinished_match_artifact_stays_in_progress(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-crashed")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    await _play_three_ticks(match, writer)
    # Deliberately never call writer.finish_match(match) -- simulates a
    # crash/abrupt process exit before the match ever finished.

    meta = load_meta(tmp_path, match.match_id)
    assert meta["status"] == "in_progress"
    assert meta["finished_at_epoch_ms"] is None
    assert meta["final_tick"] is None
    assert meta["final_snapshot"] is None


def test_finish_match_is_idempotent(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-idempotent")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    writer.finish_match(match)
    first_meta = load_meta(tmp_path, match.match_id)

    writer.finish_match(match)
    second_meta = load_meta(tmp_path, match.match_id)

    assert first_meta == second_meta


def test_finish_match_on_unknown_match_is_a_silent_no_op(tmp_path: Path) -> None:
    writer = ReplayWriter(base_dir=tmp_path)
    match, _scenario, _map_data = _build_match("match-never-started")

    # start_match was never called -- there is no artifact directory at all.
    writer.finish_match(match)

    assert not match_dir(tmp_path, match.match_id).exists()


# -- the one production-reachable "finished" result shape (M7 Task 8 review, I2) --


def test_finish_match_persists_a_forfeit_result(tmp_path: Path) -> None:
    """``ReconnectCoordinator`` is currently the only production path into
    ``finish_match`` (via ``bind_finish_hook``) -- a forfeit/no-contest
    ``MatchResult`` is therefore the one result shape that actually appears
    on disk today. Exercises ``_result_to_json``'s non-``None`` branch,
    which no other test in this module reaches.
    """
    match, scenario, map_data = _build_match("match-forfeit-result")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    match.result = MatchResult(
        outcome=MatchOutcome.FORFEIT,
        reason="disconnect_timeout",
        winner_player_id=PLAYER_TWO,
        forfeiting_player_id=PLAYER_ONE,
    )

    writer.finish_match(match)

    meta = load_meta(tmp_path, match.match_id)
    assert meta["status"] == "finished"
    assert meta["result"] == {
        "outcome": "forfeit",
        "reason": "disconnect_timeout",
        "winner_player_id": "p2",
        "forfeiting_player_id": "p1",
    }


def test_finish_match_persists_a_no_contest_result(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-no-contest-result")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    match.result = MatchResult(outcome=MatchOutcome.NO_CONTEST, reason="disconnect_timeout_both")

    writer.finish_match(match)

    meta = load_meta(tmp_path, match.match_id)
    assert meta["result"] == {
        "outcome": "no_contest",
        "reason": "disconnect_timeout_both",
        "winner_player_id": None,
        "forfeiting_player_id": None,
    }


# -- lossy command serialization fails loudly, not silently (M7 Task 8 review, I3) --


async def test_command_serialization_rejects_a_concrete_command_subclass(tmp_path: Path) -> None:
    """A concrete gameplay ``Command`` subclass must raise, not silently lose fields.

    Guards against the moment issue #98 starts submitting real gameplay
    commands: recording only ``player``/``sequence`` for a subclass with
    its own extra fields would produce a persisted artifact that looks
    fine but is quietly corrupted. This must fail loudly instead.
    """
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class _FakeGameplayCommand(Command):
        target: str

    match, scenario, map_data = _build_match("match-lossy-command-guard")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)

    on_tick_commands = make_replay_tick_recorder(writer, match.match_id)
    runtime = MatchRuntime(match, on_tick_commands=on_tick_commands)
    assert (
        await runtime.submit_command(_FakeGameplayCommand(player=PLAYER_ONE, sequence=0, target="x"))
        is True
    )

    with pytest.raises(NotImplementedError):
        await runtime._advance_one_tick()


# -- engine package gains no dependency on the backend ------------------------


def test_engine_package_imports_nothing_from_app() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    engine_src = repo_root / "engine" / "src" / "nether_earth"
    assert engine_src.is_dir(), f"expected engine source at {engine_src}"

    offenders: list[str] = []
    for path in sorted(engine_src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app" or alias.name.startswith("app."):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                is_backend_import = node.module == "app" or node.module.startswith("app.")
                if is_backend_import:
                    offenders.append(f"{path}: from {node.module} import ...")

    assert offenders == [], f"engine package must never import the backend: {offenders}"


def test_backend_does_not_define_its_own_rules_version() -> None:
    """The rules version is engine-owned; the backend only records it."""
    app_root = Path(__file__).resolve().parents[2] / "app"
    for path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            assert "RULES_VERSION" not in names, path
