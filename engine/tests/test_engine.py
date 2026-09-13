"""Tests for the canonical engine integration API: ``new_game`` and ``step``.

These tests exercise the contract described in ``engine.py``'s module
docstring: deterministic initialization, exactly-one-tick advancement per
``step`` call, deterministic ordered event emission, transactional rejection
behavior, and reproducibility across several hundred ticks.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from nether_earth import engine
from nether_earth.commands import Command, RejectionReason
from nether_earth.engine import CommandAccepted, CommandRejected, new_game, step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.map import BootstrapMap
from nether_earth.scenario import Scenario, default_pvp_scenario


@dataclass(frozen=True, slots=True)
class _NoopCommand(Command):
    """Minimal concrete command, mirroring ``test_commands.py``'s pattern.

    No concrete gameplay command exists yet in this milestone; this
    test-local subclass exercises the generic ``Command``/validation/
    ordering contract that ``engine.step`` builds on.
    """

    label: str = ""


def _bootstrap_map(scenario_map_id: str = "zx-spectrum-original", version: int = 1) -> BootstrapMap:
    return BootstrapMap(map_id=scenario_map_id, version=version, width=10, height=10)


def _default_map_and_scenario() -> tuple[BootstrapMap, Scenario]:
    scenario = default_pvp_scenario()
    map_data = _bootstrap_map(scenario.map_id, scenario.map_version)
    return map_data, scenario


# --- new_game ----------------------------------------------------------


def test_new_game_is_deterministic_across_repeated_calls() -> None:
    map_data, scenario = _default_map_and_scenario()

    first = new_game(map_data, scenario, seed=1234)
    second = new_game(map_data, scenario, seed=1234)

    assert first == second
    assert first.tick == 0
    assert first.players == (PLAYER_ONE, PLAYER_TWO)
    assert first.seed == 1234


def test_new_game_records_seed_and_defaults_to_zero() -> None:
    map_data, scenario = _default_map_and_scenario()

    default_seed_state = new_game(map_data, scenario)
    assert default_seed_state.seed == 0

    seeded_state = new_game(map_data, scenario, seed=42)
    assert seeded_state.seed == 42


def test_new_game_accepts_explicit_players_and_still_canonicalizes_order() -> None:
    map_data, scenario = _default_map_and_scenario()

    state = new_game(map_data, scenario, players=[PLAYER_TWO, PLAYER_ONE], seed=7)

    assert state.players == (PLAYER_ONE, PLAYER_TWO)


def test_new_game_rejects_mismatched_map_id() -> None:
    _, scenario = _default_map_and_scenario()
    mismatched_map = BootstrapMap(map_id="some-other-map", version=scenario.map_version, width=1, height=1)

    with pytest.raises(ValueError):
        new_game(mismatched_map, scenario)


def test_new_game_rejects_mismatched_map_version() -> None:
    _, scenario = _default_map_and_scenario()
    mismatched_map = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version + 1, width=1, height=1)

    with pytest.raises(ValueError):
        new_game(mismatched_map, scenario)


# --- step: tick advancement --------------------------------------------


def test_step_advances_tick_by_exactly_one() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)

    new_state, _events = step(state, [])

    assert new_state.tick == state.tick + 1
    assert new_state.tick == 1


def test_step_preserves_players_and_seed() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=99)

    new_state, _events = step(state, [])

    assert new_state.players == state.players
    assert new_state.seed == state.seed


def test_step_does_not_mutate_input_state() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    before = state

    step(state, [_NoopCommand(player=PLAYER_ONE, sequence=0)])

    assert state == before
    assert state.tick == 0


# --- step: events --------------------------------------------------------


def test_step_emits_accepted_event_for_valid_command() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    command = _NoopCommand(player=PLAYER_ONE, sequence=0)

    _new_state, events = step(state, [command])

    assert len(events) == 1
    assert isinstance(events[0], CommandAccepted)
    assert events[0].command == command


def test_step_emits_rejected_event_for_unknown_player() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    command = _NoopCommand(player=PlayerId("unknown"), sequence=0)

    _new_state, events = step(state, [command])

    assert len(events) == 1
    assert isinstance(events[0], CommandRejected)
    assert events[0].command == command
    assert events[0].reason is RejectionReason.UNKNOWN_PLAYER


def test_step_events_are_ordered_by_canonical_command_order_not_input_order() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    a = _NoopCommand(player=PLAYER_TWO, sequence=0)
    b = _NoopCommand(player=PLAYER_ONE, sequence=5)
    c = _NoopCommand(player=PLAYER_ONE, sequence=1)

    _new_state, events = step(state, [a, b, c])

    accepted_events = [event for event in events if isinstance(event, CommandAccepted)]
    assert [event.command for event in accepted_events] == [c, b, a]
    assert [event.sequence for event in events] == [0, 1, 2]


def test_step_event_order_is_independent_of_input_collection_order() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    commands = [
        _NoopCommand(player=PLAYER_TWO, sequence=3),
        _NoopCommand(player=PLAYER_ONE, sequence=5),
        _NoopCommand(player=PLAYER_ONE, sequence=1),
    ]

    forward = step(state, commands)
    reversed_run = step(state, list(reversed(commands)))
    shuffled_run = step(state, [commands[1], commands[2], commands[0]])

    assert forward == reversed_run
    assert forward == shuffled_run


# --- step: rejection does not partially mutate --------------------------


def test_rejected_command_in_batch_does_not_change_outcome_vs_accepted_only() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    accepted_command = _NoopCommand(player=PLAYER_ONE, sequence=0)
    rejected_command = _NoopCommand(player=PlayerId("unknown"), sequence=0)

    accepted_only_state, _ = step(state, [accepted_command])
    mixed_state, mixed_events = step(state, [accepted_command, rejected_command])

    # Tick advance is identical either way (advancing the tick is the only
    # state effect in M1; nothing else exists yet for commands to mutate).
    assert accepted_only_state == mixed_state

    # The rejected command is still deterministically reported.
    reasons = {
        (event.command, event.reason)
        for event in mixed_events
        if isinstance(event, CommandRejected)
    }
    assert (rejected_command, RejectionReason.UNKNOWN_PLAYER) in reasons


def test_duplicate_sequence_batch_rejects_both_and_is_reproducible() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)
    colliding_a = _NoopCommand(player=PLAYER_ONE, sequence=0, label="a")
    colliding_b = _NoopCommand(player=PLAYER_ONE, sequence=0, label="b")

    first_state, first_events = step(state, [colliding_a, colliding_b])
    second_state, second_events = step(state, [colliding_a, colliding_b])

    assert first_state == second_state
    assert first_events == second_events
    rejected_events = [event for event in first_events if isinstance(event, CommandRejected)]
    assert len(rejected_events) == len(first_events)
    assert all(event.reason is RejectionReason.DUPLICATE_SEQUENCE for event in rejected_events)


# --- determinism across many ticks --------------------------------------


def _generate_command_stream(num_ticks: int) -> list[list[Command]]:
    """Build a deterministic, varied per-tick command stream for testing.

    Includes accepted commands, an unknown-player rejection, and a
    duplicate-sequence rejection on a periodic cadence so the run exercises
    every rejection path across many ticks.
    """
    stream: list[list[Command]] = []
    for tick in range(num_ticks):
        commands: list[Command] = [
            _NoopCommand(player=PLAYER_ONE, sequence=tick * 10),
            _NoopCommand(player=PLAYER_TWO, sequence=tick * 10 + 1),
        ]
        if tick % 7 == 0:
            commands.append(_NoopCommand(player=PlayerId("ghost"), sequence=tick))
        if tick % 11 == 0:
            # Duplicate sequence for PLAYER_ONE within this tick's batch.
            commands.append(_NoopCommand(player=PLAYER_ONE, sequence=tick * 10, label="dup"))
        stream.append(commands)
    return stream


def _run(num_ticks: int) -> tuple[object, tuple[tuple[object, ...], ...]]:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=20260913)
    stream = _generate_command_stream(num_ticks)

    all_events: list[tuple[object, ...]] = []
    for commands in stream:
        state, events = step(state, commands)
        all_events.append(events)
    return state, tuple(all_events)


def test_five_hundred_ticks_is_deterministic_and_reproducible() -> None:
    num_ticks = 500

    final_state_a, events_a = _run(num_ticks)
    final_state_b, events_b = _run(num_ticks)

    assert final_state_a == final_state_b
    assert events_a == events_b
    assert final_state_a.tick == num_ticks  # type: ignore[attr-defined]


def test_five_hundred_ticks_final_tick_matches_call_count() -> None:
    map_data, scenario = _default_map_and_scenario()
    state = new_game(map_data, scenario, seed=1)

    for _ in range(500):
        state, _events = step(state, [])

    assert state.tick == 500


# --- static guard: no wall-clock access ----------------------------------


def test_module_does_not_reference_wall_clock_apis() -> None:
    """Static guard mirroring ``test_clock.py``'s AST-based check."""
    source = Path(inspect.getfile(engine)).read_text()
    tree = ast.parse(source)

    forbidden_names = {"time", "datetime"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_names, (
                    f"engine.py must not import {alias.name!r}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] not in forbidden_names, (
                f"engine.py must not import from {module!r}"
            )
