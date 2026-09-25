"""CR004.3 (#284) -- the engine AI seat: scenario controller, hook, memory.

Every run here uses the real original map with the standard PvP overlay and
a scenario whose second seat is ``"ai"``. The planner itself is a stub in
this task, so behaviour is probed by swapping ``nether_earth.ai.seat.plan``
for a spy or a planner that issues chosen commands.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import pytest

from nether_earth import engine
from nether_earth.ai import seat
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.commands import Command
from nether_earth.construction_commands import LaunchRobotCommand
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.engine import CommandAccepted
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap, WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.rng import MatchRandom
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.scenario import Scenario, create_initial_state, default_pvp_scenario
from nether_earth.snapshot import ai_memory_from_snapshot, snapshot_to_json_string, to_snapshot
from nether_earth.state import AiMemory, GameState

ORIGINAL_MAP_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
)

Planner = Callable[
    [GameState, AiMemory, WorldMap, EngineRules, MatchRandom],
    tuple[tuple[Command, ...], AiMemory],
]


@pytest.fixture(scope="module")
def world() -> WorldMap:
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def _vs_ai() -> Scenario:
    return dataclasses.replace(default_pvp_scenario(), player_two_controller="ai")


def _run(
    world: WorldMap,
    ticks: int,
    *,
    seed: int = 7,
    commands_by_tick: dict[int, tuple[Command, ...]] | None = None,
) -> tuple[list[str], list[Event], GameState]:
    """Step an AI match ``ticks`` times; return per-tick snapshots, events, final state."""
    state = create_initial_state(_vs_ai(), world, seed=seed)
    snapshots: list[str] = []
    events: list[Event] = []
    for _ in range(ticks):
        batch = (commands_by_tick or {}).get(state.tick + 1, ())
        state, tick_events = engine.step(state, batch, world=world)
        snapshots.append(snapshot_to_json_string(state))
        events.extend(tick_events)
    return snapshots, events, state


# --- scenario / initial state ------------------------------------------------


def test_seat_controllers_default_to_human() -> None:
    scenario = default_pvp_scenario()
    assert scenario.controller_for(PLAYER_ONE) == "human"
    assert scenario.controller_for(PLAYER_TWO) == "human"


def test_scenario_rejects_an_unknown_seat_controller() -> None:
    with pytest.raises(ValueError, match="seat controller"):
        dataclasses.replace(default_pvp_scenario(), player_two_controller="robot")  # type: ignore[arg-type]


def test_ai_seat_starts_with_memory_and_no_commander(world: WorldMap) -> None:
    state = create_initial_state(_vs_ai(), world, seed=1)

    assert state.ai_memories == (AiMemory(player_id=PLAYER_TWO),)
    assert state.commander_for(PLAYER_TWO) is None
    assert state.commander_for(PLAYER_ONE) is not None
    # Everything else about the seat is a normal player.
    human_pool = state.resource_pool_for(PLAYER_ONE)
    assert human_pool is not None
    assert state.resource_pool_for(PLAYER_TWO) == dataclasses.replace(
        human_pool, player_id=PLAYER_TWO
    )


def test_all_human_initial_state_is_unchanged(world: WorldMap) -> None:
    state = create_initial_state(default_pvp_scenario(), world, seed=1)

    assert state.ai_memories == ()
    assert len(state.commanders) == 2
    assert "ai_memories" not in to_snapshot(state)


def test_new_game_gives_an_ai_seat_memory_and_refuses_it_a_commander(world: WorldMap) -> None:
    scenario = _vs_ai()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    human_state = create_initial_state(default_pvp_scenario(), world)

    state = engine.new_game(map_data, scenario, seed=3)
    assert state.ai_memories == (AiMemory(player_id=PLAYER_TWO),)
    with pytest.raises(ValueError, match="must not have a commander"):
        engine.new_game(map_data, scenario, commanders=human_state.commanders)
    with pytest.raises(ValueError, match="must not have a commander"):
        state.with_commanders(human_state.commanders)


# --- cadence -------------------------------------------------------------------


def test_planner_runs_only_on_decision_ticks(
    world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned_ticks: list[int] = []

    def spy(state, memory, world, rules, random):  # type: ignore[no-untyped-def]
        planned_ticks.append(state.tick + 1)
        return (), memory

    monkeypatch.setattr(seat, "plan", spy)
    _run(world, 21)

    interval = DEFAULT_RULES.ai_decision_interval_ticks
    assert interval == 4
    assert planned_ticks == [4, 8, 12, 16, 20]


def test_all_human_match_never_calls_the_planner(
    world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise AssertionError("planner must not run without an AI seat")

    monkeypatch.setattr(seat, "plan", fail)
    state = create_initial_state(default_pvp_scenario(), world, seed=1)
    for _ in range(8):
        state, _events = engine.step(state, (), world=world)


# --- determinism ---------------------------------------------------------------


def test_ai_match_snapshot_sequence_is_byte_identical_across_runs(world: WorldMap) -> None:
    first, first_events, _ = _run(world, 120, seed=20260925)
    second, second_events, _ = _run(world, 120, seed=20260925)

    assert first == second
    assert first_events == second_events
    assert '"ai_memories": [{"player_id": "p2"' in first[-1]


def test_planner_random_stream_is_seeded_from_match_seed_seat_and_tick(
    world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    def draws_for(seed: int) -> list[int]:
        draws: list[int] = []

        def spy(state, memory, world, rules, random):  # type: ignore[no-untyped-def]
            draws.append(random.randint(0, 2**31))
            return (), memory

        monkeypatch.setattr(seat, "plan", spy)
        _run(world, 16, seed=seed)
        return draws

    first = draws_for(11)
    assert first == draws_for(11)
    assert len(set(first)) == len(first)  # a fresh stream per decision tick
    assert first != draws_for(12)


def test_updated_memory_is_written_back_and_round_trips_through_the_snapshot(
    world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[AiMemory] = []

    def spy(state, memory, world, rules, random):  # type: ignore[no-untyped-def]
        seen.append(memory)
        return (), dataclasses.replace(memory)

    monkeypatch.setattr(seat, "plan", spy)
    _snapshots, _events, state = _run(world, 8)

    assert seen == [AiMemory(PLAYER_TWO), AiMemory(PLAYER_TWO)]
    [entry] = to_snapshot(state)["ai_memories"]
    assert ai_memory_from_snapshot(entry) == state.ai_memories[0]


def test_replay_fixture_reproduces_an_ai_match(world: WorldMap) -> None:
    scenario = _vs_ai()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    fixture = ReplayFixture(
        scenario=scenario, map_data=map_data, seed=5, tick_count=12, world=world
    )

    first_state, first_events = run_fixture(fixture)
    second_state, second_events = run_fixture(fixture)

    assert first_state.ai_memories == (AiMemory(PLAYER_TWO),)
    assert to_snapshot(first_state) == to_snapshot(second_state)
    assert first_events == second_events


# --- the AI's commands go through the normal batch ---------------------------------


def _issuing(commands: tuple[Command, ...], calls: list[int]) -> Planner:
    def planner(state, memory, world, rules, random):  # type: ignore[no-untyped-def]
        calls.append(state.tick + 1)
        return commands, memory

    return planner


def _accepted(events: list[Event]) -> list[Command]:
    return [event.command for event in events if isinstance(event, CommandAccepted)]


def test_ai_commands_join_the_tick_batch_with_deterministic_sequence_numbers(
    world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    planned = (
        LaunchRobotCommand(player=PLAYER_TWO, sequence=0),
        CommanderSetVerticalIntentCommand(player=PLAYER_TWO, sequence=0, rising=True),
    )
    monkeypatch.setattr(seat, "plan", _issuing(planned, calls))
    # A command already in the batch for the AI seat pushes the AI's numbering
    # past it; the human's own commands are untouched.
    external = (
        CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True),
        LaunchRobotCommand(player=PLAYER_TWO, sequence=5),
    )

    _snapshots, events, _state = _run(world, 4, commands_by_tick={4: external})

    assert _accepted(events) == [
        external[0],
        external[1],
        dataclasses.replace(planned[0], sequence=6),
        dataclasses.replace(planned[1], sequence=7),
    ]


@pytest.mark.parametrize(
    "illegal",
    [
        DirectRobotMoveCommand(player=PLAYER_TWO, sequence=0, dx=1, dy=0),
        CommanderMoveCommand(player=PLAYER_TWO, sequence=0, dx=1, dy=0),
        CommanderSetVerticalIntentCommand(player=PLAYER_TWO, sequence=0, rising=True),
        LaunchRobotCommand(player=PLAYER_TWO, sequence=0),
    ],
    ids=lambda command: type(command).__name__,
)
def test_an_illegal_ai_command_is_rejected_like_a_humans_and_does_not_stall_the_planner(
    illegal: Command, world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Baseline: the stub planner, which issues nothing.
    baseline_snapshots, baseline_events, _ = _run(world, 40)

    calls: list[int] = []
    monkeypatch.setattr(seat, "plan", _issuing((illegal,), calls))
    snapshots, events, _ = _run(world, 40)

    # Every one of these is illegal for a seat with no commander and no open
    # construction session: the generic accept fires, the gameplay layer
    # does nothing -- the same outcome a human seat gets for the same command.
    assert snapshots == baseline_snapshots
    assert [e for e in events if not isinstance(e, CommandAccepted)] == [
        e for e in baseline_events if not isinstance(e, CommandAccepted)
    ]
    # ...and the planner keeps running on every decision tick afterwards.
    assert calls == list(range(4, 41, 4))


def test_a_planner_issuing_for_another_seat_is_a_programming_error(
    world: WorldMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    rogue = (LaunchRobotCommand(player=PLAYER_ONE, sequence=0),)
    monkeypatch.setattr(seat, "plan", _issuing(rogue, []))

    with pytest.raises(ValueError, match="issued a command for 'p1'"):
        _run(world, 4)
