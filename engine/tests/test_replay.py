"""Tests for deterministic replay fixtures (issue #8).

Mirrors the ``_NoopCommand`` test-local pattern established in
``test_engine.py`` -- no concrete gameplay command type exists yet, so these
tests exercise the generic ``Command``/replay contract with a minimal
subclass.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from nether_earth import replay
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commands import Command, RejectionReason
from nether_earth.engine import CommandAccepted, CommandRejected
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario, default_pvp_scenario
from nether_earth.snapshot import to_snapshot


@dataclass(frozen=True, slots=True)
class _NoopCommand(Command):
    label: str = ""


def _bootstrap_map(scenario: Scenario) -> BootstrapMap:
    return BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=10, height=10)


def _default_scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = default_pvp_scenario()
    return scenario, _bootstrap_map(scenario)


def _command_stream(num_ticks: int, *, variant: str = "a") -> dict[int, tuple[Command, ...]]:
    """Build a deterministic per-tick command stream spanning ``num_ticks`` ticks.

    ``variant`` lets callers build two streams that are trivially
    distinguishable (different command labels/sequences) to prove a
    different command stream produces a different event sequence.
    """
    stream: dict[int, tuple[Command, ...]] = {}
    for tick in range(1, num_ticks + 1):
        commands: list[Command] = [
            _NoopCommand(player=PLAYER_ONE, sequence=tick * 10, label=f"{variant}-one"),
            _NoopCommand(player=PLAYER_TWO, sequence=tick * 10 + 1, label=f"{variant}-two"),
        ]
        if tick % 13 == 0:
            commands.append(_NoopCommand(player=PlayerId("ghost"), sequence=tick, label=variant))
        stream[tick] = tuple(commands)
    return stream


def _fixture(
    *, tick_count: int, seed: int = 20260913, variant: str = "a"
) -> ReplayFixture:
    scenario, map_data = _default_scenario_and_map()
    return ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=seed,
        tick_count=tick_count,
        commands_by_tick=_command_stream(tick_count, variant=variant),
    )


# --- ReplayFixture validation -------------------------------------------


def test_replay_fixture_rejects_negative_tick_count() -> None:
    scenario, map_data = _default_scenario_and_map()
    with pytest.raises(ValueError):
        ReplayFixture(scenario=scenario, map_data=map_data, seed=0, tick_count=-1)


def test_replay_fixture_rejects_tick_outside_range() -> None:
    scenario, map_data = _default_scenario_and_map()
    with pytest.raises(ValueError):
        ReplayFixture(
            scenario=scenario,
            map_data=map_data,
            seed=0,
            tick_count=2,
            commands_by_tick={5: ()},
        )


def test_replay_fixture_defaults_to_empty_commands() -> None:
    scenario, map_data = _default_scenario_and_map()
    fixture = ReplayFixture(scenario=scenario, map_data=map_data, seed=0, tick_count=3)
    assert fixture.commands_by_tick == {}


def test_replay_fixture_defaults_to_no_initial_entities() -> None:
    """Pre-#67 fixtures keep their exact previous behavior (empty tick-0 state)."""
    scenario, map_data = _default_scenario_and_map()
    fixture = ReplayFixture(scenario=scenario, map_data=map_data, seed=0, tick_count=3)

    assert fixture.commanders == ()
    assert fixture.initial_robots == ()

    final_state, _events = run_fixture(fixture)
    assert final_state.commanders == ()
    assert final_state.robots == ()


def test_run_fixture_places_initial_commanders_and_robots_on_tick_zero() -> None:
    """``commanders``/``initial_robots`` (issue #67) reach the tick-0 state.

    Without them no M5 behavior (movement, reservations, orders, capture)
    could be expressed as a replay fixture at all -- see
    ``test_m5_replay_integration.py``, which builds on this.
    """
    scenario, map_data = _default_scenario_and_map()
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    robot = Robot(
        entity_id=EntityId("robot-1"),
        owner=PLAYER_ONE,
        x=2,
        y=3,
        build=build,
        stack=stack,
        height=height,
    )
    commander = Commander(
        player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=2, y=3, altitude=10
    )
    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=7,
        tick_count=0,
        commanders=(commander,),
        initial_robots=(robot,),
    )

    final_state, events = run_fixture(fixture)

    assert events == ()
    assert final_state.commanders == (commander,)
    assert final_state.robots == (robot,)


# --- run_fixture: basic shape --------------------------------------------


def test_run_fixture_advances_to_tick_count() -> None:
    fixture = _fixture(tick_count=10)

    final_state, _events = run_fixture(fixture)

    assert final_state.tick == 10
    assert final_state.seed == fixture.seed
    assert final_state.players == (PLAYER_ONE, PLAYER_TWO)


def test_run_fixture_emits_events_for_every_tick_in_order() -> None:
    fixture = _fixture(tick_count=3)

    _final_state, events = run_fixture(fixture)

    # Each tick has 2 accepted commands (PLAYER_ONE, PLAYER_TWO); none of
    # ticks 1..3 hit the "% 13 == 0" ghost-rejection branch.
    assert len(events) == 6
    assert all(isinstance(event, CommandAccepted) for event in events)


# --- run_fixture: reproducibility (core acceptance criterion) ------------


def test_run_fixture_is_repeatable_across_many_ticks() -> None:
    fixture = _fixture(tick_count=75)

    final_state_a, events_a = run_fixture(fixture)
    final_state_b, events_b = run_fixture(fixture)

    assert final_state_a == final_state_b
    assert to_snapshot(final_state_a) == to_snapshot(final_state_b)
    assert events_a == events_b
    assert final_state_a.tick == 75


def test_run_fixture_is_repeatable_with_a_fresh_equal_fixture_instance() -> None:
    """Two separately constructed but value-equal fixtures replay identically."""
    fixture_a = _fixture(tick_count=60)
    fixture_b = _fixture(tick_count=60)

    assert fixture_a == fixture_b

    final_state_a, events_a = run_fixture(fixture_a)
    final_state_b, events_b = run_fixture(fixture_b)

    assert to_snapshot(final_state_a) == to_snapshot(final_state_b)
    assert events_a == events_b


# --- run_fixture: detecting real differences ------------------------------


def test_different_command_stream_changes_the_event_sequence() -> None:
    """A different command stream is detectably different in the output.

    Unlike ``seed`` (see below), the command stream *is* something M1's
    ``engine.step`` observably threads into events -- each ``CommandAccepted``/
    ``CommandRejected`` event carries the exact command object, so changing
    the stream's command labels changes the event sequence.
    """
    fixture_a = _fixture(tick_count=20, variant="a")
    fixture_b = _fixture(tick_count=20, variant="b")

    _state_a, events_a = run_fixture(fixture_a)
    _state_b, events_b = run_fixture(fixture_b)

    assert events_a != events_b


def test_different_seed_still_reproduces_its_own_snapshot_deterministically() -> None:
    """Different seeds are NOT asserted to change output.

    ``engine.py``'s module docstring is explicit that M1 draws no randomness
    in ``step`` (no gameplay system consumes ``GameState.seed`` yet), so
    "different seed => different output" is currently false and would be a
    wrong assertion about the engine, not a real regression. What IS
    guaranteed: each seed's run is independently, individually reproducible.
    """
    fixture_seed_1 = _fixture(tick_count=30, seed=1)
    fixture_seed_2 = _fixture(tick_count=30, seed=2)

    state_1a, events_1a = run_fixture(fixture_seed_1)
    state_1b, events_1b = run_fixture(fixture_seed_1)
    state_2a, events_2a = run_fixture(fixture_seed_2)
    state_2b, events_2b = run_fixture(fixture_seed_2)

    assert to_snapshot(state_1a) == to_snapshot(state_1b)
    assert events_1a == events_1b
    assert to_snapshot(state_2a) == to_snapshot(state_2b)
    assert events_2a == events_2b

    # The seed value itself is still faithfully recorded on the resulting
    # state, even though it has no other observable effect in M1.
    assert state_1a.seed == 1
    assert state_2a.seed == 2


def test_rejection_events_are_preserved_across_repeated_runs() -> None:
    fixture = _fixture(tick_count=26)  # includes a tick%13==0 ghost-rejection tick

    _state_a, events_a = run_fixture(fixture)
    _state_b, events_b = run_fixture(fixture)

    rejected_a = [e for e in events_a if isinstance(e, CommandRejected)]
    rejected_b = [e for e in events_b if isinstance(e, CommandRejected)]

    assert len(rejected_a) == 2  # ticks 13 and 26
    assert rejected_a == rejected_b
    assert all(event.reason is RejectionReason.UNKNOWN_PLAYER for event in rejected_a)


def test_zero_tick_count_returns_initial_state_and_no_events() -> None:
    scenario, map_data = _default_scenario_and_map()
    fixture = ReplayFixture(scenario=scenario, map_data=map_data, seed=5, tick_count=0)

    final_state, events = run_fixture(fixture)

    assert final_state.tick == 0
    assert events == ()


# --- static guard: no network/frontend imports ----------------------------


def test_module_does_not_reference_network_or_frontend_apis() -> None:
    forbidden = {"fastapi", "starlette", "websockets", "requests", "httpx"}
    source = Path(inspect.getfile(replay)).read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0].lower() not in forbidden
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0].lower() not in forbidden
