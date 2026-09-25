"""M9.4 / M9.6 -- deterministic scripted full PvP match through the real backend stack (issues #116, #118).

The script in ``m9_script.py`` plays the canonical scenario from create/join/
ready to authoritative victory. This module proves, on the real
``MatchManager``/``MatchRuntime``/replay-writer composition:

- every required step of the v1 path is reached, in order (milestones);
- the browser-facing snapshot contract validates on every tick and the last
  broadcast snapshot equals the engine's final state;
- the ``finished`` frame carries the engine's winner and the match ends
  ``FINISHED`` with a durable ``VICTORY`` result;
- the persisted artifact replays through the engine alone to the identical
  final snapshot (M9.6), and matches the committed regression fixture.

Set ``NETHER_EARTH_REGENERATE_M9_FIXTURE=1`` to refresh the committed fixture
after an intentional script/rule change.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from nether_earth.capture import StructureCapturedEvent
from nether_earth.combat import ProjectileTerminatedEvent, RobotDamagedEvent
from nether_earth.commands import Command
from nether_earth.construction_commands import RobotLaunchedEvent
from nether_earth.destruction import RobotDestroyedEvent, StructureDestroyedEvent
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap
from nether_earth.replay import run_from_state
from nether_earth.resource_production import DailyProductionApplied
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.victory import VictoryEvent

from app.match.models import Match, MatchOutcome, MatchRuntimeState
from app.match.world import load_standard_world
from app.protocol import serialize_server_message
from app.replay.verify import (
    ReplayRulesMismatchError,
    load_commands_by_tick,
    load_meta,
    verify_replay,
)
from app.transport.snapshots import build_snapshot_message
from tests.acceptance.m9_script import (
    P1_SCOUT,
    P1_STRIKER,
    P2_GUARD,
    SCRIPT_SEED,
    RuntimeRun,
    drive_runtime,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_MATCH = "m9-full-match"

#: The required path (`09-pvp-vertical-slice.md` "Full scripted acceptance path"), in order.
REQUIRED_MILESTONES = [
    "p1 construction entered",
    "p2 construction entered",
    "p2 guard launched",
    "p1 scout launched",
    "p2 docked on guard",
    "p2 guard in position",
    "p1 captured a neutral factory",
    "p1 scout fired at a structure",
    "p1 scout staged near warbase-2",
    "p1 docked on scout",
    "p1 direct-controlled scout onto warbase-2 capture cell",
    "p1 captured warbase-2",
    "p1 affords striker",
    "p1 striker launched",
    "p2 guard fired directly",
    "p1 striker in position",
]


def _non_empty(commands_by_tick: dict[int, tuple[Command, ...]]) -> dict[int, tuple[Command, ...]]:
    return {tick: commands for tick, commands in commands_by_tick.items() if commands}


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_standard_world()


@pytest.fixture(scope="module")
def run(world: WorldMap, tmp_path_factory: pytest.TempPathFactory) -> RuntimeRun:
    import asyncio

    replay_dir = tmp_path_factory.mktemp("replays")
    result = asyncio.run(drive_runtime(world, replay_dir))
    if os.environ.get("NETHER_EARTH_REGENERATE_M9_FIXTURE") == "1":
        # Keep the fixture compact: meta.json plus only the command-bearing
        # lines of commands.jsonl (the writer also logs a debug event summary
        # for every empty tick, which the loader does not need).
        target = FIXTURE_DIR / FIXTURE_MATCH
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        source = replay_dir / result.match_id
        shutil.copy(source / "meta.json", target / "meta.json")
        with (source / "commands.jsonl").open(encoding="utf-8") as src, (target / "commands.jsonl").open(
            "w", encoding="utf-8"
        ) as dst:
            for line in src:
                record = json.loads(line)
                if record["commands"]:
                    dst.write(json.dumps({"tick": record["tick"], "commands": record["commands"]}) + "\n")
    return result


def test_full_match_reaches_every_required_milestone_in_order(run: RuntimeRun) -> None:
    labels = [label for _tick, label in run.milestones]
    assert [label for label in labels if label in REQUIRED_MILESTONES] == REQUIRED_MILESTONES, run.milestones
    ticks = [tick for tick, _ in run.milestones]
    assert ticks == sorted(ticks)


def test_full_match_ends_in_authoritative_victory_for_player_one(run: RuntimeRun) -> None:
    match = run.match
    assert isinstance(match, Match)
    assert match.state is MatchRuntimeState.FINISHED
    assert match.result is not None
    assert match.result.outcome is MatchOutcome.VICTORY
    assert match.result.winner_player_id == PLAYER_ONE
    assert match.result.decided_tick == run.final_state.tick

    final = run.final_state
    owners = {r.structure_id.value: r.owner for r in final.structure_ownership}
    assert owners["warbase-1"] == PLAYER_ONE
    assert owners["warbase-2"] == PLAYER_ONE
    assert "warbase-4" in {d.value for d in final.structure_destruction}
    assert not any(o == PLAYER_TWO for sid, o in owners.items() if sid.startswith("warbase"))
    assert any(sid.startswith("factory") and o == PLAYER_ONE for sid, o in owners.items())
    # The striker and the guard died in the detonation; the scout survives at its capture cell.
    assert {r.entity_id for r in final.robots} == {P1_SCOUT}
    assert final.robot_for(P1_STRIKER) is None and final.robot_for(P2_GUARD) is None
    # No stale references survive the destructive transition.
    for commander in final.commanders:
        assert commander.docked_robot_id is None or final.robot_for(commander.docked_robot_id) is not None
    assert all(final.robot_for(p.robot_id) is not None for p in final.capture_progress) or not final.capture_progress
    assert final.projectiles == ()


def test_browser_facing_frames_match_engine_state(run: RuntimeRun) -> None:
    # One validated snapshot broadcast per tick, to both players (counted per socket).
    assert run.snapshot_count == 2 * run.final_state.tick
    assert run.last_snapshot is not None
    assert run.last_snapshot["tick"] == run.final_state.tick
    # Byte-for-byte the protocol serialization of the engine's final state.
    expected = json.loads(serialize_server_message(build_snapshot_message(run.match_id, run.final_state)))
    assert run.last_snapshot == expected
    assert expected["state"]["tick"] == run.final_state.tick
    # Exactly one lifecycle frame: `finished` with the engine's winner, once per socket.
    assert run.frames == [
        {
            "protocolVersion": 1,
            "type": "finished",
            "matchId": run.match_id,
            "winnerPlayerId": "p1",
            "tick": run.final_state.tick,
        }
    ] * 2


def test_persisted_artifact_replays_to_the_same_final_state(run: RuntimeRun, world: WorldMap) -> None:
    replay_dir = run.replay_dir
    assert isinstance(replay_dir, Path)
    meta = load_meta(replay_dir, run.match_id)
    assert meta["status"] == "finished"
    assert meta["scenario_id"] == "pvp-v1"
    assert (meta["map_id"], meta["map_version"]) == (world.map_id, world.version)
    assert meta["seed"] == SCRIPT_SEED
    assert meta["final_tick"] == run.final_state.tick
    assert meta["result"] == {
        "outcome": "victory",
        "reason": "zero_war_bases",
        "winner_player_id": "p1",
        "forfeiting_player_id": None,
    }
    assert meta["final_snapshot"] == to_snapshot(run.final_state)

    # Engine-only reproduction from scenario + world + seed + accepted commands.
    result = verify_replay(replay_dir, run.match_id, scenario=default_pvp_scenario(), world=world)
    assert result.matches
    # The persisted stream is exactly what the script submitted.
    assert _non_empty(load_commands_by_tick(replay_dir, run.match_id)) == run.commands_by_tick


def test_run_matches_committed_regression_fixture(run: RuntimeRun) -> None:
    replay_dir = run.replay_dir
    assert isinstance(replay_dir, Path)
    fixture_meta = load_meta(FIXTURE_DIR, FIXTURE_MATCH)
    assert _non_empty(load_commands_by_tick(FIXTURE_DIR, FIXTURE_MATCH)) == run.commands_by_tick, (
        "the script's accepted command stream drifted from the committed fixture; "
        "set NETHER_EARTH_REGENERATE_M9_FIXTURE=1 if the change is intentional"
    )
    assert fixture_meta["final_tick"] == run.final_state.tick
    assert fixture_meta["final_snapshot"] == to_snapshot(run.final_state)


def test_committed_fixture_replays_deterministically_without_the_script(world: WorldMap) -> None:
    """Pure regression: scenario + seed + stored command stream -> stored final snapshot."""
    meta = load_meta(FIXTURE_DIR, FIXTURE_MATCH)
    commands = load_commands_by_tick(FIXTURE_DIR, FIXTURE_MATCH)
    initial = create_initial_state(default_pvp_scenario(), world, seed=meta["seed"])
    final, events = run_from_state(initial, commands, meta["final_tick"], world=world)
    assert to_snapshot(final) == meta["final_snapshot"]

    by_type = {}
    for event in events:
        by_type.setdefault(type(event), []).append(event)
    # Subsystem coverage of the stored match, localized by event type.
    assert len(by_type[RobotLaunchedEvent]) == 3
    assert len(by_type[DailyProductionApplied]) >= 2
    # Both captures are StructureCapturedEvent now: the owner decision of
    # 2026-09-23 removed instant neutral-factory acquisition, so a neutral
    # factory runs the same countdown as warbase-2 and reports the same
    # event with ``previous_owner is None``.
    captured = by_type[StructureCapturedEvent]
    assert any(e.structure_id.value == "warbase-2" for e in captured)
    assert any(
        e.structure_id.value.startswith("factory") and e.previous_owner is None for e in captured
    )
    assert any(e.hit_robot_id is None for e in by_type[ProjectileTerminatedEvent])  # structure shot
    assert any(e.entity_id == P1_STRIKER for e in by_type[RobotDamagedEvent])
    assert [e.structure_id for e in by_type[StructureDestroyedEvent]] == [EntityId("warbase-4")]
    # CR001 §19: no autonomous detonation. The only robot losses are the striker
    # and the guard, both in the single directly fired blast that destroys warbase-4.
    blast_tick = by_type[StructureDestroyedEvent][0].tick
    destroyed = by_type[RobotDestroyedEvent]
    assert {e.entity_id for e in destroyed} == {P1_STRIKER, P2_GUARD}
    assert all(e.tick == blast_tick for e in destroyed)
    victories = by_type[VictoryEvent]
    assert len(victories) == 1 and victories[0].winner == PLAYER_ONE
    assert victories[0].tick == meta["final_tick"]


def test_committed_fixture_verifies_against_the_running_engine_rules(world: WorldMap) -> None:
    result = verify_replay(FIXTURE_DIR, FIXTURE_MATCH, scenario=default_pvp_scenario(), world=world)
    assert result.matches


def test_an_artifact_without_seat_controllers_verifies_as_all_human(
    world: WorldMap, tmp_path: Path
) -> None:
    """CR004.9: an artifact recorded before CR004.7 added ``seat_controllers``
    to ``meta.json`` has no way to state its seats, so it must be treated as
    all-human and verify against `default_pvp_scenario()` -- exactly the
    fallback `verify_replay` derives, not a hand-maintained literal.
    """
    legacy = tmp_path / FIXTURE_MATCH
    shutil.copytree(FIXTURE_DIR / FIXTURE_MATCH, legacy)
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    assert meta["seat_controllers"] == {"p1": "human", "p2": "human"}
    del meta["seat_controllers"]
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    result = verify_replay(tmp_path, FIXTURE_MATCH, scenario=default_pvp_scenario(), world=world)
    assert result.matches


def test_pre_cr001_fixture_is_rejected_as_a_rules_mismatch(world: WorldMap, tmp_path: Path) -> None:
    """A fixture with the old backend-owned ``m7`` header fails on rules identity, not divergence."""
    legacy = tmp_path / FIXTURE_MATCH
    shutil.copytree(FIXTURE_DIR / FIXTURE_MATCH, legacy)
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    meta["rules_version"] = "m7"
    del meta["rules_hash"]
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ReplayRulesMismatchError, match="rules version 'm7'"):
        verify_replay(tmp_path, FIXTURE_MATCH, scenario=default_pvp_scenario(), world=world)


def test_cr001_fixture_is_rejected_as_a_rules_mismatch(world: WorldMap, tmp_path: Path) -> None:
    """A replay recorded before CR002 (rules version ``cr001``) fails on rules identity."""
    legacy = tmp_path / FIXTURE_MATCH
    shutil.copytree(FIXTURE_DIR / FIXTURE_MATCH, legacy)
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    meta["rules_version"] = "cr001"
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ReplayRulesMismatchError, match="rules version 'cr001'"):
        verify_replay(tmp_path, FIXTURE_MATCH, scenario=default_pvp_scenario(), world=world)


def test_cr002_fixture_is_rejected_as_a_rules_mismatch(world: WorldMap, tmp_path: Path) -> None:
    """A replay recorded before CR003 (rules version ``cr002``) fails on rules identity."""
    legacy = tmp_path / FIXTURE_MATCH
    shutil.copytree(FIXTURE_DIR / FIXTURE_MATCH, legacy)
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    meta["rules_version"] = "cr002"
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ReplayRulesMismatchError, match="rules version 'cr002'"):
        verify_replay(tmp_path, FIXTURE_MATCH, scenario=default_pvp_scenario(), world=world)


def test_cr003_fixture_is_rejected_as_a_rules_mismatch(world: WorldMap, tmp_path: Path) -> None:
    """A replay recorded before CR004 (rules version ``cr003``) fails on rules identity."""
    legacy = tmp_path / FIXTURE_MATCH
    shutil.copytree(FIXTURE_DIR / FIXTURE_MATCH, legacy)
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    meta["rules_version"] = "cr003"
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ReplayRulesMismatchError, match="rules version 'cr003'"):
        verify_replay(tmp_path, FIXTURE_MATCH, scenario=default_pvp_scenario(), world=world)
