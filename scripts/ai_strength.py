"""CR004.10 (#291): measure the engine AI's win rate against the scripted baseline.

Runs full headless matches (engine only) in parallel, one per (seed, AI seat):
for every seed the AI plays once as p1 and once as p2 against
``ai_harness.baseline_plan``. A match ends at victory or at the tick cap.

Usage (from the repo root):

    PYTHONPATH=engine/src python scripts/ai_strength.py --seeds 1-10 --cap 30000 --jobs 20

``--mirror`` plays AI vs AI instead. ``--unpatched`` runs on the engine's own
robot numbering, which crashes most full matches (see ``ai_harness``).
``--set construction.NAME=INT`` overrides an AI tuning constant, for
experiments. Prints one line per match and a summary. Too slow for the normal
suite (a match takes minutes); the suite runs a bounded smoke version
(``engine/tests/test_ai_harness.py``).
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine" / "tests"))

from ai_harness import MatchResult, run_match
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId


def _seeds(spec: str) -> list[int]:
    seeds: list[int] = []
    for part in spec.split(","):
        low, _, high = part.partition("-")
        seeds.extend(range(int(low), int(high or low) + 1))
    return seeds


def _apply_overrides(overrides: list[str]) -> None:
    """Set AI tuning constants, e.g. ``construction.ELECTRONICS_VALUE=5`` (for experiments)."""
    for override in overrides:
        name, _, value = override.partition("=")
        module_name, _, attribute = name.rpartition(".")
        module = importlib.import_module(f"nether_earth.ai.{module_name}")
        if not hasattr(module, attribute):
            raise SystemExit(f"unknown tunable {name}")
        setattr(module, attribute, int(value))


def _play(job: tuple[int, str, int, bool, bool]) -> tuple[int, str, MatchResult, float]:
    seed, ai_seat, cap, mirror, patch = job
    started = time.perf_counter()
    baseline = (
        None if mirror else (PLAYER_TWO if ai_seat == PLAYER_ONE.value else PLAYER_ONE)
    )
    result = run_match(seed, cap, baseline=baseline, patch_robot_ids=patch)
    return seed, ai_seat, result, time.perf_counter() - started


def _describe(seed: int, ai_seat: str, result: MatchResult, seconds: float) -> str:
    scores = " ".join(
        f"{seat}:wb={score.war_bases},fac={score.factories},robots={score.robots}"
        for seat, score in result.scores.items()
    )
    if result.error is not None:
        outcome = f"engine error after tick {result.ticks}"
    elif result.winner is not None:
        outcome = f"{result.winner.value} wins at tick {result.ticks}"
    else:
        leader = result.leader
        outcome = f"cap, ahead: {leader.value if leader else 'even'}"
    return f"seed={seed:<4} ai={ai_seat} {outcome:<32} [{scores}] ({seconds:.0f}s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default="1-10")
    parser.add_argument("--cap", type=int, default=30_000)
    parser.add_argument("--jobs", type=int, default=20)
    parser.add_argument(
        "--mirror", action="store_true", help="AI vs AI instead of AI vs baseline"
    )
    parser.add_argument(
        "--unpatched",
        action="store_true",
        help="do not work around the engine's robot-id collision (see ai_harness)",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="MODULE.NAME=INT",
        help="override an AI tuning constant in every worker (experiments only)",
    )
    args = parser.parse_args()
    _apply_overrides(args.set)

    seats: tuple[PlayerId, ...] = (
        (PLAYER_ONE,) if args.mirror else (PLAYER_ONE, PLAYER_TWO)
    )
    jobs = [
        (seed, seat.value, args.cap, args.mirror, not args.unpatched)
        for seed in _seeds(args.seeds)
        for seat in seats
    ]
    wins = losses = capped = ahead = errors = 0
    with ProcessPoolExecutor(
        max_workers=args.jobs, initializer=_apply_overrides, initargs=(args.set,)
    ) as pool:
        for seed, ai_seat, result, seconds in pool.map(_play, jobs):
            print(_describe(seed, ai_seat, result, seconds), flush=True)
            if args.mirror:
                continue
            if result.error is not None:
                errors += 1
            elif result.winner is None:
                capped += 1
                ahead += result.leader is not None and result.leader.value == ai_seat
            elif result.winner.value == ai_seat:
                wins += 1
            else:
                losses += 1
    if not args.mirror:
        total = len(jobs)
        print(
            f"AI vs baseline, cap {args.cap} ticks: {wins}/{total} wins, {losses} losses, "
            f"{capped} capped ({ahead} of them with the AI ahead), {errors} engine errors"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
