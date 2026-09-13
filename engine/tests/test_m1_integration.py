"""Milestone 1 integration scenario (issue #9).

This module is the final M1 ("Deterministic Engine Foundation") integration
gate described in `_specs/milestones/01-deterministic-engine-foundation.md`
under "Milestone integration scenario" and exercised through issue #9's
acceptance criteria. It composes the already-merged M1 primitives
(``ids``, ``state``, ``clock``, ``commands``, ``events``, ``scenario``,
``map``, ``engine``, ``rng``, ``snapshot``, ``replay``) end to end and proves
the milestone's determinism contract:

    same initial state + scenario/map version + RNG seed + accepted command
    stream => byte-for-byte / canonical-equivalent final snapshot and ordered
    events; invalid commands fail deterministically; repeated runs never
    intermittently differ.

No new gameplay system is introduced. Like ``test_engine.py``/``test_replay.py``,
this module uses a minimal test-local ``_NoopCommand`` subclass of
:class:`~nether_earth.commands.Command` because no concrete gameplay command
type exists yet in M1 (movement/combat/economy/construction are explicitly
out of scope -- see the milestone doc's "Out of scope" section). Exercising
the generic command/event contract with a no-op command is exactly what
"integrate the M1 primitives without adding M2+ behavior" means here.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import nether_earth
from nether_earth.commands import Command, RejectionReason
from nether_earth.engine import CommandAccepted, CommandRejected
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.map import BootstrapMap
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.scenario import default_pvp_scenario
from nether_earth.snapshot import snapshot_to_json_string, to_snapshot

# Several hundred ticks, per the milestone doc's "known command stream
# spanning several hundred ticks" and issue #9's "at least 300-500 ticks".
TICK_COUNT = 400

#: Fixed seed for the primary scenario (criterion 1: "minimal two-player
#: scenario with a fixed seed").
PRIMARY_SEED = 20260913


@dataclass(frozen=True, slots=True)
class _NoopCommand(Command):
    """Minimal concrete command, mirroring ``test_engine.py``/``test_replay.py``.

    No concrete gameplay command exists yet in M1; this test-local subclass
    exercises the generic ``Command``/validation/ordering/event contract that
    ``engine.step`` (via ``replay.run_fixture``) builds on, without inventing
    any M2+ gameplay behavior.
    """

    label: str = ""


def _bootstrap_map_for(scenario_map_id: str, version: int) -> BootstrapMap:
    return BootstrapMap(map_id=scenario_map_id, version=version, width=10, height=10)


def _command_stream(
    num_ticks: int, *, variant: str = "a"
) -> dict[int, tuple[Command, ...]]:
    """Build a deterministic per-tick command stream spanning ``num_ticks`` ticks.

    Mixes accepted commands with several deliberately invalid/rejected ones
    (criterion 2 -- "including a mix of accepted commands and at least some
    that are deliberately invalid/rejected"):

    - every 7th tick includes a command from an unknown player
      (``RejectionReason.UNKNOWN_PLAYER``);
    - every 11th tick includes a duplicate ``(player, sequence)`` pair for
      ``PLAYER_ONE`` (``RejectionReason.DUPLICATE_SEQUENCE``);
    - every 17th tick includes a command with a negative sequence number
      (``RejectionReason.INVALID_SEQUENCE``).

    ``variant`` lets two independently constructed streams be trivially
    distinguishable (criterion 4's "genuinely different command stream")
    while keeping the same cadence/shape otherwise.
    """
    stream: dict[int, tuple[Command, ...]] = {}
    for tick in range(1, num_ticks + 1):
        commands: list[Command] = [
            _NoopCommand(player=PLAYER_ONE, sequence=tick * 10, label=f"{variant}-p1"),
            _NoopCommand(player=PLAYER_TWO, sequence=tick * 10 + 1, label=f"{variant}-p2"),
        ]
        if tick % 7 == 0:
            commands.append(
                _NoopCommand(player=PlayerId("ghost"), sequence=tick, label=f"{variant}-ghost")
            )
        if tick % 11 == 0:
            commands.append(
                _NoopCommand(player=PLAYER_ONE, sequence=tick * 10, label=f"{variant}-dup")
            )
        if tick % 17 == 0:
            commands.append(
                _NoopCommand(player=PLAYER_TWO, sequence=-1, label=f"{variant}-negative")
            )
        stream[tick] = tuple(commands)
    return stream


def _fixture(
    *, tick_count: int = TICK_COUNT, seed: int = PRIMARY_SEED, variant: str = "a"
) -> ReplayFixture:
    """Criterion 1: minimal two-player scenario with a fixed seed + matching map."""
    scenario = default_pvp_scenario()
    map_data = _bootstrap_map_for(scenario.map_id, scenario.map_version)
    return ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=seed,
        tick_count=tick_count,
        commands_by_tick=_command_stream(tick_count, variant=variant),
    )


# --- criteria 1-3: fixed-seed scenario, known stream, replay identity -----


def test_primary_fixture_spans_several_hundred_ticks_with_mixed_outcomes() -> None:
    """Sanity-check the fixture itself satisfies criterion 2's shape requirements."""
    fixture = _fixture()

    assert fixture.tick_count == TICK_COUNT
    assert TICK_COUNT >= 300

    final_state, events = run_fixture(fixture)

    assert final_state.tick == TICK_COUNT
    accepted = [e for e in events if isinstance(e, CommandAccepted)]
    rejected = [e for e in events if isinstance(e, CommandRejected)]
    assert accepted, "fixture must include accepted commands"
    assert rejected, "fixture must include rejected commands"

    reasons = {e.reason for e in rejected}
    assert reasons == {
        RejectionReason.UNKNOWN_PLAYER,
        RejectionReason.DUPLICATE_SEQUENCE,
        RejectionReason.INVALID_SEQUENCE,
    }


def test_replay_from_identical_inputs_yields_identical_snapshot_and_events() -> None:
    """Criterion 3: replay from identical inputs => identical final state/events.

    Uses both dataclass equality and canonical snapshot/JSON equality for the
    state comparison ("use both for extra signal since that's cheap"), and
    full tuple equality (not just length) for the event sequence.
    """
    fixture = _fixture()

    state_a, events_a = run_fixture(fixture)
    state_b, events_b = run_fixture(fixture)

    assert state_a == state_b
    assert to_snapshot(state_a) == to_snapshot(state_b)
    assert snapshot_to_json_string(state_a) == snapshot_to_json_string(state_b)
    assert events_a == events_b
    assert len(events_a) == len(events_b) > 0


def test_replay_from_a_freshly_built_but_value_equal_fixture_matches() -> None:
    """A second, independently constructed fixture with equal values replays identically."""
    fixture_a = _fixture()
    fixture_b = _fixture()
    assert fixture_a == fixture_b

    state_a, events_a = run_fixture(fixture_a)
    state_b, events_b = run_fixture(fixture_b)

    assert to_snapshot(state_a) == to_snapshot(state_b)
    assert events_a == events_b


# --- criterion 4: alternate seed / alternate command stream -----------------


def test_alternate_seed_changes_only_the_recorded_seed() -> None:
    """Different seed, same command stream: only the recorded seed differs.

    ``engine.step`` draws no randomness in M1 (see ``engine.py``'s module
    docstring: no gameplay system consumes ``GameState.seed`` yet), so
    changing only the seed must leave the event sequence, tick, and players
    identical -- while the seed value itself is still faithfully recorded,
    since it is part of the canonical snapshot.
    """
    fixture_primary = _fixture(seed=PRIMARY_SEED)
    fixture_alt_seed = _fixture(seed=PRIMARY_SEED + 1)

    state_primary, events_primary = run_fixture(fixture_primary)
    state_alt, events_alt = run_fixture(fixture_alt_seed)

    assert events_primary == events_alt
    assert state_primary.tick == state_alt.tick
    assert state_primary.players == state_alt.players

    snapshot_primary = to_snapshot(state_primary)
    snapshot_alt = to_snapshot(state_alt)
    assert snapshot_primary["tick"] == snapshot_alt["tick"]
    assert snapshot_primary["players"] == snapshot_alt["players"]
    assert snapshot_primary["seed"] != snapshot_alt["seed"]
    assert snapshot_primary["seed"] == PRIMARY_SEED
    assert snapshot_alt["seed"] == PRIMARY_SEED + 1


def test_alternate_command_stream_changes_events_where_expected() -> None:
    """Same seed, genuinely different command stream: events differ, contract holds.

    ``variant="b"`` produces commands with different ``label`` values (and
    thus different ``CommandAccepted``/``CommandRejected`` event payloads) on
    every tick, while the rejection cadence (which ticks reject, and why)
    stays identical -- so the *shape* of the determinism contract still
    holds, but the concrete event sequence detectably differs.
    """
    fixture_primary = _fixture(seed=PRIMARY_SEED, variant="a")
    fixture_variant = _fixture(seed=PRIMARY_SEED, variant="b")

    state_primary, events_primary = run_fixture(fixture_primary)
    state_variant, events_variant = run_fixture(fixture_variant)

    assert events_primary != events_variant
    # Tick advancement, player set, and seed are unaffected by which
    # concrete no-op commands were submitted.
    assert state_primary.tick == state_variant.tick
    assert state_primary.players == state_variant.players
    assert state_primary.seed == state_variant.seed

    # The rejection cadence/reasons are identical between variants (both
    # streams reject on the same tick numbers for the same reasons); only
    # the accepted/rejected commands' own payload (label) differs.
    rejected_primary = [e for e in events_primary if isinstance(e, CommandRejected)]
    rejected_variant = [e for e in events_variant if isinstance(e, CommandRejected)]
    assert [e.reason for e in rejected_primary] == [e.reason for e in rejected_variant]
    assert rejected_primary != rejected_variant  # different command payloads


# --- criterion 5: repeated-run regression against hidden nondeterminism ----


def test_repeated_runs_never_intermittently_differ() -> None:
    """Run the same fixture many times (not just twice) to catch flaky nondeterminism.

    Guards specifically against nondeterminism that a single repeat could
    miss "by luck" (e.g. accidental reliance on ``dict``/``set`` iteration
    order for string-keyed collections within a process run).
    """
    fixture = _fixture(tick_count=350)
    repetitions = 8

    baseline_state, baseline_events = run_fixture(fixture)
    baseline_snapshot = snapshot_to_json_string(baseline_state)

    for _ in range(repetitions - 1):
        state, events = run_fixture(fixture)
        assert state == baseline_state
        assert snapshot_to_json_string(state) == baseline_snapshot
        assert events == baseline_events


# --- criterion 6: invalid commands have deterministic outcomes -------------


def test_unknown_player_command_is_rejected_identically_every_replay() -> None:
    """A specific unknown-player command is rejected with the same reason every time."""
    scenario = default_pvp_scenario()
    map_data = _bootstrap_map_for(scenario.map_id, scenario.map_version)
    unknown_command = _NoopCommand(player=PlayerId("unknown-raider"), sequence=0)
    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=PRIMARY_SEED,
        tick_count=1,
        commands_by_tick={1: (unknown_command,)},
    )

    for _ in range(5):
        _state, events = run_fixture(fixture)
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, CommandRejected)
        assert event.command == unknown_command
        assert event.reason is RejectionReason.UNKNOWN_PLAYER


def test_duplicate_sequence_command_is_rejected_identically_every_replay() -> None:
    """A specific duplicate-(player, sequence) pair rejects identically every time."""
    scenario = default_pvp_scenario()
    map_data = _bootstrap_map_for(scenario.map_id, scenario.map_version)
    first = _NoopCommand(player=PLAYER_ONE, sequence=0, label="first")
    duplicate = _NoopCommand(player=PLAYER_ONE, sequence=0, label="duplicate")
    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=PRIMARY_SEED,
        tick_count=1,
        commands_by_tick={1: (first, duplicate)},
    )

    for _ in range(5):
        _state, events = run_fixture(fixture)
        assert len(events) == 2
        rejected_events = [event for event in events if isinstance(event, CommandRejected)]
        assert len(rejected_events) == 2
        assert all(
            event.reason is RejectionReason.DUPLICATE_SEQUENCE for event in rejected_events
        )


# --- criterion 7: engine independence from FastAPI/network/frontend -------


def test_engine_package_has_no_forbidden_dependency_leakage() -> None:
    """Statically confirm no engine module imports FastAPI/network/frontend code.

    Mirrors the AST-based static guard pattern already used in
    ``test_clock.py`` (wall-clock guard) and ``test_replay.py`` (per-module
    network guard), but sweeps every module in the ``nether_earth`` package
    rather than a single file -- this is the package-wide version of the
    milestone's "Engine imports independently of FastAPI/network/frontend
    code" acceptance criterion.
    """
    forbidden_roots = {
        "fastapi",
        "starlette",
        "uvicorn",
        "websockets",
        "frontend",
        "backend",
        "requests",
        "httpx",
        "aiohttp",
    }

    package_dir = Path(inspect.getfile(nether_earth)).parent
    module_paths = sorted(package_dir.glob("*.py"))
    assert module_paths, "expected to find nether_earth source modules to scan"

    offenders: list[str] = []
    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0].lower()
                    if root in forbidden_roots:
                        offenders.append(f"{module_path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root = module.split(".")[0].lower()
                if root in forbidden_roots:
                    offenders.append(f"{module_path.name}: from {module} import ...")

    assert offenders == [], f"forbidden imports found in engine package: {offenders}"


def test_engine_package_imports_without_pulling_in_forbidden_modules() -> None:
    """Runtime companion to the static guard: importing the engine loads no forbidden module.

    Even if a forbidden import were hidden behind indirection the static
    walk above might miss (e.g. ``importlib.import_module`` with a
    dynamically built name), this checks the interpreter's actual module
    table after importing the full engine surface used by this test module.
    """
    import sys

    forbidden_roots = {"fastapi", "starlette", "uvicorn", "websockets"}
    loaded_roots = {name.split(".")[0].lower() for name in sys.modules}
    assert not (loaded_roots & forbidden_roots), (
        f"forbidden modules present in sys.modules after importing the engine: "
        f"{loaded_roots & forbidden_roots}"
    )
