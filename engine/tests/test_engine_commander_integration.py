"""Tests for commander integration into ``engine.step`` (issue #42, M3.6).

This is the capstone integration test for M3: it exercises the wiring added
in ``engine.py`` that threads the five standalone commander modules (#37
state shape, #38 movement, #39 collision, #40 docking, #41 heli-pad
detection) through the authoritative ``engine.step`` tick loop, per the
controller rulings recorded on issue #42.

Uses the shared M2 fixture map (`fixtures/world_map_basic.yaml`) for the
tests that need real collision/heli-pad geometry: a ``box-1`` blocker of
height 1 at ``(0, 0)`` (a "tall obstacle" relative to a grounded commander,
since ``commander_height`` defaults to 4 and any overlap blocks), and two
war bases (``warbase-p1``/``warbase-p2``) each with a single-cell
2×2 ``HELI_PAD`` interaction point anchored at ``(4, 1)``/``(4, 3)`` respectively.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from nether_earth.collision import RobotFixture
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import (
    CommanderHorizontalMoveCompletedEvent,
    CommanderHorizontalMoveStartedEvent,
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
    CommanderVerticalUpdatedEvent,
)
from nether_earth.docking import CommanderDockedEvent, CommanderUndockedEvent
from nether_earth.engine import CommandAccepted, new_game, step
from nether_earth.heli_pad import CommanderConstructionEntryEligible
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap, WorldMap, load_world_map
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "world_map_basic.yaml"

P1_HELI_PAD_CELL = (4, 1)  # 2×2 pad anchor (CR002.4)
# The fixture's p1 heli-pad cell sits on warbase-p1's 3-high component: the
# commander lands at that component height (resolved-questions.md "War-base heli-pad location and landing height").
PAD_ROOF_ALTITUDE = 3


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="fixture-scenario",
        map_id="fixture-basic",
        map_version=1,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id="fixture-basic", version=1, width=6, height=4)
    return scenario, map_data


def _world() -> WorldMap:
    return load_world_map(FIXTURE_PATH)


def _base_state(commanders: tuple[Commander, ...] = ()) -> GameState:
    scenario, map_data = _scenario_and_map()
    state = new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=1)
    return state.with_commanders(commanders)


def _free_commander(player_id: PlayerId, x: int, y: int, altitude: int, rising: bool = False) -> Commander:
    return Commander(
        player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude, rising=rising
    )


# --- Horizontal move: command -> step -> event/snapshot -------------------


def test_horizontal_move_starts_and_completes_through_step() -> None:
    commander = _free_commander(PLAYER_ONE, x=1, y=1, altitude=0)
    state = _base_state((commander,))
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    state, events = step(state, [move])
    assert any(isinstance(e, CommandAccepted) for e in events)
    started = [e for e in events if isinstance(e, CommanderHorizontalMoveStartedEvent)]
    assert len(started) == 1
    assert started[0].from_x == 1 and started[0].to_x == 2
    assert state.commander_for(PLAYER_ONE).x == 1  # not yet authoritative
    assert state.commander_for(PLAYER_ONE).horizontal_transition is not None

    # Duration is DEFAULT_RULES.commander_horizontal_move_ticks == 4, started
    # at tick 1; completes once tick >= 5. Step forward with no commands
    # until it resolves.
    completed_events: list[CommanderHorizontalMoveCompletedEvent] = []
    for _ in range(4):
        state, tick_events = step(state, [])
        completed_events.extend(
            e for e in tick_events if isinstance(e, CommanderHorizontalMoveCompletedEvent)
        )

    assert state.tick == 5
    assert len(completed_events) == 1
    assert completed_events[0].x == 2 and completed_events[0].y == 1
    updated = state.commander_for(PLAYER_ONE)
    assert (updated.x, updated.y) == (2, 1)
    assert updated.horizontal_transition is None

    # Snapshot reflects the fully-resolved authoritative position.
    snapshot_commander = to_snapshot(state)["commanders"][0]
    assert (snapshot_commander["x"], snapshot_commander["y"]) == (2, 1)
    assert snapshot_commander["horizontal_transition"] is None


def test_second_move_command_while_in_progress_is_gameplay_rejected() -> None:
    commander = _free_commander(PLAYER_ONE, x=1, y=1, altitude=0)
    state = _base_state((commander,))
    first_move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    state, _events = step(state, [first_move])

    second_move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=0, dy=1)
    state, events = step(state, [second_move])

    # Generic contract still fires CommandAccepted (structurally valid).
    assert any(isinstance(e, CommandAccepted) and e.command == second_move for e in events)
    # But no second gameplay move-started event: the first transition is
    # still in progress (MOVE_IN_PROGRESS gameplay rejection => no event).
    assert not any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    assert state.commander_for(PLAYER_ONE).horizontal_transition.to_x == 2


def test_held_move_chains_cells_without_an_idle_tick() -> None:
    """CR003.10 (#232, resolved-questions.md "Idle tick between commander cells"): a commander_move sent on every
    tick starts a new cell on the tick the previous one completes, so the
    cells start at ticks 1, 5, 9, ... (4 ticks per cell, no idle tick)."""
    state = _base_state((_free_commander(PLAYER_ONE, x=0, y=1, altitude=0),))
    ticks = DEFAULT_RULES.commander_horizontal_move_ticks
    starts: list[int] = []
    completions: list[int] = []
    for sequence in range(3 * ticks + 1):
        move = CommanderMoveCommand(player=PLAYER_ONE, sequence=sequence, dx=1, dy=0)
        state, events = step(state, [move])
        kinds = [type(e) for e in events]
        if CommanderHorizontalMoveStartedEvent in kinds:
            starts.append(state.tick)
        if CommanderHorizontalMoveCompletedEvent in kinds:
            completions.append(state.tick)
            # The completion resolves before the tick's command is applied.
            assert kinds.index(CommanderHorizontalMoveCompletedEvent) < kinds.index(
                CommanderHorizontalMoveStartedEvent
            )

    assert starts == [1, 1 + ticks, 1 + 2 * ticks, 1 + 3 * ticks]
    assert completions == starts[1:]
    commander = state.commander_for(PLAYER_ONE)
    assert commander.x == 3
    assert commander.horizontal_transition is not None
    assert commander.horizontal_transition.to_x == 4


# --- Vertical rise/descend --------------------------------------------------


def test_vertical_rise_through_step() -> None:
    commander = _free_commander(PLAYER_ONE, x=1, y=1, altitude=0)
    state = _base_state((commander,))
    intent = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)

    state, _events = step(state, [intent])
    assert state.commander_for(PLAYER_ONE).rising is True

    vertical_events: list[CommanderVerticalUpdatedEvent] = []
    for _ in range(3):
        state, tick_events = step(state, [])
        vertical_events.extend(e for e in tick_events if isinstance(e, CommanderVerticalUpdatedEvent))

    # Cadence is every 4 ticks (DEFAULT_RULES.commander_vertical_update_ticks);
    # tick 1 set the intent, ticks 2-4 stepped with no commands, so tick 4 is
    # the first vertical-cadence tick.
    assert state.tick == 4
    assert len(vertical_events) == 1
    assert vertical_events[0].from_altitude == 0
    assert vertical_events[0].to_altitude == DEFAULT_RULES.commander_ascent_step
    assert state.commander_for(PLAYER_ONE).altitude == DEFAULT_RULES.commander_ascent_step


def test_vertical_descend_is_the_default_without_rise_intent() -> None:
    commander = _free_commander(PLAYER_ONE, x=1, y=1, altitude=10)
    state = _base_state((commander,))

    for _ in range(4):
        state, _events = step(state, [])

    assert state.tick == 4
    assert state.commander_for(PLAYER_ONE).altitude == 10 - DEFAULT_RULES.commander_descent_step


# --- Blocked move via real WorldMap collision -------------------------------


def test_horizontal_move_blocked_by_world_obstacle() -> None:
    # box-1 blocker (height 1) sits at (0, 0); a grounded commander (altitude
    # 0, commander_height 4) overlaps it and is blocked from entering.
    commander = _free_commander(PLAYER_ONE, x=1, y=0, altitude=0)
    state = _base_state((commander,))
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=-1, dy=0)

    new_state, events = step(state, [move], world=_world())

    assert any(isinstance(e, CommandAccepted) for e in events)
    assert not any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    updated = new_state.commander_for(PLAYER_ONE)
    assert (updated.x, updated.y) == (1, 0)
    assert updated.horizontal_transition is None


def test_horizontal_move_allowed_without_world_is_permissive() -> None:
    """Without a ``world``, no collision check is applied at all (Ruling 1)."""
    commander = _free_commander(PLAYER_ONE, x=1, y=0, altitude=0)
    state = _base_state((commander,))
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=-1, dy=0)

    new_state, events = step(state, [move])  # world=None

    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    assert new_state.commander_for(PLAYER_ONE).horizontal_transition is not None


# --- Friendly docking end-to-end --------------------------------------------


def test_friendly_docking_through_step() -> None:
    robot = RobotFixture(id=EntityId("robot-1"), owner=PLAYER_ONE, x=1, y=1, height=4)
    commander = _free_commander(PLAYER_ONE, x=1, y=1, altitude=4)  # resting exactly on top
    state = _base_state((commander,))

    new_state, events = step(state, [], robots=(robot,))

    docked_events = [e for e in events if isinstance(e, CommanderDockedEvent)]
    assert len(docked_events) == 1
    assert docked_events[0].robot_id == robot.id
    updated = new_state.commander_for(PLAYER_ONE)
    assert updated.mode is CommanderMode.DOCKED
    assert updated.docked_robot_id == robot.id

    snapshot_commander = to_snapshot(new_state)["commanders"][0]
    assert snapshot_commander["mode"] == "docked"
    assert snapshot_commander["docked_robot_id"] == "robot-1"


def test_docked_commander_follows_moving_robot_fixture() -> None:
    robot_id = EntityId("robot-1")
    docked = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=1,
        y=1,
        altitude=4,
        docked_robot_id=robot_id,
    )
    state = _base_state((docked,))
    moved_robot = RobotFixture(id=robot_id, owner=PLAYER_ONE, x=2, y=1, height=4)

    new_state, _events = step(state, [], robots=(moved_robot,))

    updated = new_state.commander_for(PLAYER_ONE)
    assert (updated.x, updated.y, updated.altitude) == (2, 1, 4)
    assert updated.mode is CommanderMode.DOCKED


def test_docked_commander_move_command_is_a_gameplay_noop() -> None:
    robot_id = EntityId("robot-1")
    docked = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=1,
        y=1,
        altitude=4,
        docked_robot_id=robot_id,
    )
    state = _base_state((docked,))
    robot = RobotFixture(id=robot_id, owner=PLAYER_ONE, x=1, y=1, height=4)
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    new_state, events = step(state, [move], robots=(robot,))

    assert any(isinstance(e, CommandAccepted) for e in events)
    assert not any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    updated = new_state.commander_for(PLAYER_ONE)
    assert updated.mode is CommanderMode.DOCKED
    assert (updated.x, updated.y) == (1, 1)


# --- Undocking through step -------------------------------------------------


def _docked_on(robot: RobotFixture) -> GameState:
    docked = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=robot.x,
        y=robot.y,
        altitude=robot.height,
        docked_robot_id=robot.id,
        rising=True,
    )
    return _base_state((docked,))


def test_undocking_through_step() -> None:
    robot_id = EntityId("robot-1")
    robot = RobotFixture(id=robot_id, owner=PLAYER_ONE, x=1, y=1, height=4)
    state = _docked_on(robot)

    new_state, events = step(state, [], robots=(robot,))

    undocked_events = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert len(undocked_events) == 1
    assert undocked_events[0].robot_id == robot_id
    assert undocked_events[0].from_altitude == undocked_events[0].to_altitude == 4

    updated = new_state.commander_for(PLAYER_ONE)
    assert updated.mode is CommanderMode.FREE
    assert updated.docked_robot_id is None
    assert updated.altitude == 4
    # CR002.24: the same 5-update lift as leaving the construction screen.
    assert updated.elevate_updates_remaining == DEFAULT_RULES.commander_exit_elevate_updates

    # Still on the robot top, but it must not re-dock while the lift runs.
    assert not any(isinstance(e, CommanderDockedEvent) for e in events)


def _undock_altitude_trace(rising_after_undock: bool) -> tuple[list[int], list[object]]:
    """Undock at tick 1, then step until re-docked; return per-tick altitudes and events."""
    robot = RobotFixture(id=EntityId("robot-1"), owner=PLAYER_ONE, x=1, y=1, height=4)
    state = _docked_on(robot)
    state, _events = step(state, [], robots=(robot,))
    intent = CommanderSetVerticalIntentCommand(
        player=PLAYER_ONE, sequence=0, rising=rising_after_undock
    )
    trace: list[int] = []
    all_events: list[object] = []
    commands = [intent]
    for _ in range(200):
        state, events = step(state, commands, robots=(robot,))
        commands = []
        all_events.extend(events)
        commander = state.commander_for(PLAYER_ONE)
        assert commander is not None
        trace.append(commander.altitude)
        if commander.mode is CommanderMode.DOCKED or (rising_after_undock and len(trace) > 40):
            return trace, all_events
    raise AssertionError("commander never re-docked")


def test_undock_lift_profile_matches_construction_exit_and_lands_back_on_robot() -> None:
    # Spectrum: #a80d sets Lfd30_player_elevate_timer = 5 on leaving a robot,
    # exactly as Lcb8e does on leaving the construction screen; Lafa2 then
    # climbs +2 per update for 5 updates and gravity drops
    # commander_descent_step (2, CR003.1) per update.
    # La69a re-docks when the ship is back at the robot top on its anchor.
    trace, events = _undock_altitude_trace(rising_after_undock=False)

    peak = 4 + DEFAULT_RULES.commander_exit_elevate_updates * DEFAULT_RULES.commander_ascent_step
    assert max(trace) == peak == 14
    peak_index = trace.index(peak)
    rising = [a for i, a in enumerate(trace[: peak_index + 1]) if i == 0 or a != trace[i - 1]]
    assert rising == [4, 6, 8, 10, 12, 14]  # robot top, then +2 per update
    falling = trace[peak_index:]
    assert all(0 <= a - b <= DEFAULT_RULES.commander_descent_step for a, b in pairwise(falling))
    assert trace[-1] == 4

    docked = [e for e in events if isinstance(e, CommanderDockedEvent)]
    assert len(docked) == 1
    assert docked[0].robot_id == EntityId("robot-1")
    assert docked[0].altitude == 4


def test_rise_intent_during_the_undock_lift_does_not_change_it() -> None:
    # Owner decision (CR002.24, as for the construction exit): up/down input
    # does not shorten the lift, so the first five updates are identical.
    released, _ = _undock_altitude_trace(rising_after_undock=False)
    held, _ = _undock_altitude_trace(rising_after_undock=True)

    lift_ticks = DEFAULT_RULES.commander_exit_elevate_updates * (
        DEFAULT_RULES.commander_vertical_update_ticks
    )
    assert held[:lift_ticks] == released[:lift_ticks]
    assert max(held[:lift_ticks]) == 14
    # After the lift, held rise keeps climbing instead of falling.
    assert max(held) > 14


# --- Heli-pad landing eligibility through step ------------------------------


def test_heli_pad_landing_eligibility_through_step() -> None:
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
    state = _base_state((commander,))

    _new_state, events = step(state, [], world=_world())

    landing_events = [e for e in events if isinstance(e, CommanderConstructionEntryEligible)]
    assert len(landing_events) == 1
    assert landing_events[0].player == PLAYER_ONE
    assert landing_events[0].war_base_id == EntityId("warbase-p1")


def test_heli_pad_landing_not_detected_without_world() -> None:
    """Ruling 1: heli-pad detection is skipped entirely when world is None."""
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
    state = _base_state((commander,))

    _new_state, events = step(state, [])  # world=None

    assert not any(isinstance(e, CommanderConstructionEntryEligible) for e in events)


# --- Backward compatibility: existing M1/M2 call sites unaffected ----------


def test_step_without_world_or_robots_keeps_m1_contract() -> None:
    scenario, map_data = _scenario_and_map()
    state = new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=1)

    new_state, events = step(state, [])

    assert new_state.tick == 1
    assert new_state.commanders == ()
    assert events == ()


# --- Determinism: same commander scenario replays to identical results -----


def _drive_scenario(world: WorldMap | None) -> tuple[GameState, tuple[object, ...]]:
    robot = RobotFixture(id=EntityId("robot-1"), owner=PLAYER_ONE, x=3, y=1, height=4)
    commander_one = _free_commander(PLAYER_ONE, x=1, y=1, altitude=0)
    commander_two = _free_commander(PLAYER_TWO, x=1, y=2, altitude=10)
    state = _base_state((commander_one, commander_two))

    all_events: list[object] = []
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    state, events = step(state, [move], world=world, robots=(robot,))
    all_events.extend(events)

    for _ in range(4):
        # Commander one drifts east toward the robot, resolving each move
        # once it lands, and eventually docks once it rests on the robot's
        # top surface (x reaches 3, altitude reaches 4 is not exercised here
        # -- this loop only proves multi-tick replay of the horizontal
        # transition + vertical descent of commander_two together).
        state, events = step(state, [], world=world, robots=(robot,))
        all_events.extend(events)

    return state, tuple(all_events)


def test_direct_step_replay_is_deterministic_for_commander_scenario() -> None:
    world = _world()

    final_a, events_a = _drive_scenario(world)
    final_b, events_b = _drive_scenario(world)

    assert to_snapshot(final_a) == to_snapshot(final_b)
    assert events_a == events_b


def test_replay_fixture_with_world_and_robots_is_deterministic() -> None:
    scenario, map_data = _scenario_and_map()
    world = _world()
    robot = RobotFixture(id=EntityId("robot-1"), owner=PLAYER_ONE, x=3, y=1, height=4)

    commands_by_tick = {
        1: (CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),),
        2: (CommanderSetVerticalIntentCommand(player=PLAYER_TWO, sequence=0, rising=True),),
    }
    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=7,
        tick_count=10,
        commands_by_tick=commands_by_tick,
        world=world,
        robots=(robot,),
    )

    final_a, events_a = run_fixture(fixture)
    final_b, events_b = run_fixture(fixture)

    assert to_snapshot(final_a) == to_snapshot(final_b)
    assert events_a == events_b
    # world/robots are threaded through: structurally valid commands for
    # players with no attached commander at tick 0 (new_game does not spawn
    # commanders -- see Ruling 2) still replay deterministically as inert
    # generic CommandAccepted events.
    assert final_a.commanders == ()
    assert all(isinstance(e, CommandAccepted) for e in events_a)


def test_replay_fixture_world_and_robots_default_preserves_m1_behavior() -> None:
    scenario, map_data = _scenario_and_map()
    fixture = ReplayFixture(scenario=scenario, map_data=map_data, seed=1, tick_count=3)

    assert fixture.world is None
    assert fixture.robots == ()

    final_state, events = run_fixture(fixture)
    assert final_state.tick == 3
    assert events == ()
