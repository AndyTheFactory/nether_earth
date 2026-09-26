"""CR004.10 (#291) -- headless AI harness: determinism and a bounded strength smoke test.

The full win-rate run is ``scripts/ai_strength.py`` (too slow for the suite);
its numbers are recorded in the CR004.10 PR.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_harness import (
    MatchResult,
    ai_vs_ai_scenario,
    baseline_plan,
    load_world,
    run_match,
    seat_planners,
)

from nether_earth import engine
from nether_earth.ai import planner, seat
from nether_earth.engine import CommandAccepted, CommandRejected
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.map import WorldMap
from nether_earth.orders import SearchCapture, SearchCaptureTarget
from nether_earth.robot_build import ModuleIdentity
from nether_earth.scenario import create_initial_state

#: Long enough for both seats to launch, order and move out to the factories.
DETERMINISM_TICKS = 600
SEED = 20260926
TESTS_DIR = Path(__file__).resolve().parent
ENGINE_SRC = TESTS_DIR.parent / "src"
#: (label, baseline seat): AI vs AI, and AI (p1) vs the scripted baseline (p2).
PAIRINGS = (("ai-vs-ai", None), ("ai-vs-baseline", PLAYER_TWO))


@pytest.fixture(scope="module")
def world() -> WorldMap:
    return load_world()


@pytest.fixture(scope="module")
def reference(world: WorldMap) -> dict[str, MatchResult]:
    """One recorded run per pairing, shared by the determinism tests."""
    return {
        label: run_match(SEED, DETERMINISM_TICKS, baseline=baseline, world=world, record=True)
        for label, baseline in PAIRINGS
    }


# --- determinism ----------------------------------------------------------------------


@pytest.mark.parametrize(("label", "baseline"), PAIRINGS, ids=[p[0] for p in PAIRINGS])
def test_same_seed_gives_an_identical_snapshot_sequence_and_outcome(
    world: WorldMap,
    reference: dict[str, MatchResult],
    label: str,
    baseline: PlayerId | None,
) -> None:
    again = run_match(SEED, DETERMINISM_TICKS, baseline=baseline, world=world, record=True)

    assert again == reference[label]
    assert again.digest is not None
    # The match actually happened: both seats built robots.
    assert all(score.robots > 0 for score in again.scores.values())


def _spawn(hash_seed: str, baseline: PlayerId | None) -> subprocess.Popen[str]:
    seat = repr(baseline.value) if baseline is not None else "None"
    code = (
        "from ai_harness import run_match\n"
        "from nether_earth.ids import PlayerId\n"
        f"b = None if {seat} is None else PlayerId({seat})\n"
        f"print(run_match({SEED}, {DETERMINISM_TICKS}, baseline=b, record=True).digest)\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(ENGINE_SRC), str(TESTS_DIR))),
        "PYTHONHASHSEED": hash_seed,
    }
    return subprocess.Popen(
        [sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True
    )


def test_snapshot_sequence_is_identical_across_processes_and_hash_seeds(
    reference: dict[str, MatchResult],
) -> None:
    """Fresh interpreters with different string-hash seeds reproduce the in-process runs.

    A differing ``PYTHONHASHSEED`` changes the iteration order of str-keyed
    sets and dicts, so this catches any hash-order dependence in the planner,
    the baseline or the engine. The children run concurrently.
    """
    children = {
        (label, hash_seed): _spawn(hash_seed, baseline)
        for label, baseline in PAIRINGS
        for hash_seed in ("1", "4242")
    }
    digests = {}
    for key, child in children.items():
        out, _err = child.communicate(timeout=600)
        assert child.returncode == 0
        digests[key] = out.strip()

    for (label, _hash_seed), digest in digests.items():
        assert digest == reference[label].digest


def test_seat_planners_restores_the_hook_even_on_error() -> None:
    real = seat.plan
    with pytest.raises(RuntimeError), seat_planners({PLAYER_ONE: baseline_plan}):
        assert seat.plan is not real
        raise RuntimeError
    assert seat.plan is real


# --- the scripted baseline --------------------------------------------------------------


def test_baseline_builds_the_cheapest_robot_and_orders_neutral_captures(world: WorldMap) -> None:
    """The baseline's commands are ordinary, accepted commands with the scripted effect."""
    state = create_initial_state(ai_vs_ai_scenario(), world, seed=SEED)
    rejected: list[CommandRejected] = []
    accepted = 0
    with seat_planners({PLAYER_ONE: baseline_plan, PLAYER_TWO: baseline_plan}):
        for _ in range(8):
            state, events = engine.step(state, (), world=world)
            rejected.extend(e for e in events if isinstance(e, CommandRejected))
            accepted += sum(1 for e in events if isinstance(e, CommandAccepted))

    assert rejected == []
    assert accepted > 0
    for player in (PLAYER_ONE, PLAYER_TWO):
        robots = state.robots_for(player)
        assert robots
        for robot in robots:
            assert robot.build.chassis is ModuleIdentity.BIPOD
            assert robot.build.weapons == (ModuleIdentity.CANNON,)
            assert robot.build.electronics is None
            assert isinstance(robot.order, SearchCapture)
            assert robot.order.target is SearchCaptureTarget.NEUTRAL_FACTORY


def test_harness_uses_the_real_planner_for_seats_it_does_not_override(
    monkeypatch: pytest.MonkeyPatch, world: WorldMap
) -> None:
    called: list[str] = []
    real = planner.plan

    def spy(state, memory, world, rules, seed):  # type: ignore[no-untyped-def]
        called.append(memory.player_id.value)
        return real(state, memory, world, rules, seed)

    monkeypatch.setattr(planner, "plan", spy)
    run_match(SEED, 8, baseline=PLAYER_ONE, world=world)

    assert called == ["p2", "p2"]


# --- strength smoke test ---------------------------------------------------------------

#: About 1.4 game days: the AI takes a neutral war base at tick 3956 in this
#: seed, short enough for the normal suite. The real measurement is the full
#: win-rate run (``scripts/ai_strength.py``); this only guards against a
#: planner change that stops the AI out-expanding the scripted baseline.
SMOKE_TICKS = 4100


def test_ai_out_expands_the_scripted_baseline_on_war_bases(world: WorldMap) -> None:
    # The AI takes the second seat, the one it measured weaker in.
    result = run_match(SEED, SMOKE_TICKS, baseline=PLAYER_ONE, world=world)

    assert result.error is None
    ai, baseline = result.scores[PLAYER_TWO.value], result.scores[PLAYER_ONE.value]
    assert ai.war_bases > baseline.war_bases
