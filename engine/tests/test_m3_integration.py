"""Milestone 3 integration scenario (issue #43, M3.7).

This is the final M3 ("Commander Movement, Collision & Docking") integration
gate described in `_specs/milestones/03-commander-movement-docking.md` under
"Milestone integration scenario" and issue #43's acceptance criteria. It
composes the already-merged M3 primitives -- ``commander.py`` (#37),
``commander_movement.py`` (#38), ``collision.py`` (#39), ``docking.py``
(#40), ``heli_pad.py`` (#41), and ``engine.py``'s ``step`` wiring (#42) --
end to end, proving the whole commander subsystem works together, driven
entirely through ``engine.new_game``/``engine.step``. No new gameplay rule
is introduced here; every rule exercised below is already unit-tested by
#37-#42 in isolation (see ``test_commander.py``, ``test_commander_movement.py``,
``test_collision.py``, ``test_docking.py``, ``test_heli_pad.py``,
``test_engine_commander_integration.py``).

Fixture reuse: per the issue's explicit instruction to use "M2 world/
interaction contracts rather than test-only alternate geometry semantics",
this module reuses the shared ``fixtures/world_map_basic.yaml`` fixture
(also used by M2's own integration gate, ``test_m2_integration.py``, and by
#42's ``test_engine_commander_integration.py``) rather than inventing a
parallel map. Its relevant geometry:

- ``box-1`` blocker, height 1, at ``(0, 0)``.
- ``factory-1``, height 2, at ``(2, 2)``.
- ``warbase-p1`` (owner ``p1``): components at ``(4, 0, h=3)``/``(5, 0, h=2)``,
  2×2 heli-pad anchored at ``(4, 1)`` (cells x 4..5, y 0..1).
- ``warbase-p2`` (owner ``p2``): components at ``(4, 3, h=3)``/``(5, 3, h=3)``,
  2×2 heli-pad anchored at ``(4, 3)`` (cells x 4..5, y 2..3).
- the map is 12x8, leaving free ground east of the structures.

Commanders (and robot fixtures) are 2×2 bodies anchored at their ``(x, y)``
(CR002.3/CR002.4, `_specs/open-questions.md` §21): the body covers x..x+1,
y-1..y, so two commanders one cell apart overlap.
- terrain variety (rough at ``(1, 1)``, ditch at ``(3, 3)``) -- terrain does
  not affect commander flight collision (only structure/robot/commander
  vertical ranges do), so it is not separately asserted here; its presence
  is exercised structurally by loading the same fixture M2 already proved
  composes correctly.

``new_game`` extension (issue #43's one permitted production change):
``engine.new_game`` gained an additive, optional ``commanders: tuple[
Commander, ...] = ()`` parameter (see ``engine.py``), forwarded straight to
``state.create_game_state``'s existing ``commanders`` parameter. This lets
this module build a tick-0 state with both players' commanders already
present, entirely through the canonical ``new_game`` entry point, rather
than hand-building a ``GameState`` and bypassing it.

Replay-fixture fit (see the module-level note before the replay tests
below): ``replay.ReplayFixture`` has no ``commanders`` field at all (not
just a "fixed robots tuple for the whole run" limitation) -- issue #43's
own ruling anticipated a narrower "robots is fixed for the whole run"
mismatch, but in this codebase's current state ``ReplayFixture`` cannot
seed *any* initial commanders, full stop. Per the ruling's own escape
hatch ("if ReplayFixture cannot cleanly carry your exact ... scenario ...
it is fine to write a SEPARATE, simpler replay-determinism test ... alongside
your main phase-by-phase scenario test that drives engine.step() directly"),
this module's determinism proof for the full commander scenario is a direct
``new_game``+``step`` replay (drive the same recorded command stream twice,
compare results) -- exactly the pattern #42's own
``test_direct_step_replay_is_deterministic_for_commander_scenario`` already
established -- plus one supplementary ``ReplayFixture``/``run_fixture``-based
test (mirroring #42's own ``test_replay_fixture_with_world_and_robots_is_deterministic``)
that proves the underlying replay harness contract itself remains intact for
a representative movement/vertical/world/robots command stream.
"""

from __future__ import annotations

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
from nether_earth.engine import new_game, step
from nether_earth.heli_pad import CommanderConstructionEntryEligible
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap, WorldMap, load_world_map
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "world_map_basic.yaml"

RULES = DEFAULT_RULES
V_TICKS = RULES.commander_vertical_update_ticks  # 4
H_TICKS = RULES.commander_horizontal_move_ticks  # 4
ASCENT = RULES.commander_ascent_step  # 2
DESCENT = RULES.commander_descent_step  # 1
HEIGHT = RULES.commander_height  # 4
# The fixture's p1 heli-pad cell sits on warbase-p1's 3-high component: the
# commander lands at that component height (open-questions.md §18).
PAD_ROOF_ALTITUDE = 3


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="m3-integration-scenario",
        map_id="fixture-basic",
        map_version=1,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id="fixture-basic", version=1, width=12, height=8)
    return scenario, map_data


def _world() -> WorldMap:
    return load_world_map(FIXTURE_PATH)


def _free(player_id: PlayerId, x: int, y: int, altitude: int, rising: bool = False) -> Commander:
    return Commander(
        player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude, rising=rising
    )


def _new_game(commanders: tuple[Commander, ...]) -> GameState:
    scenario, map_data = _scenario_and_map()
    return new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=1, commanders=commanders)


# ---------------------------------------------------------------------------
# 1. Horizontal + vertical movement, cadence, +2/-1 steps, 0..48 clamping
# ---------------------------------------------------------------------------


def test_horizontal_move_completes_on_the_configured_tick() -> None:
    p1 = _free(PLAYER_ONE, x=7, y=2, altitude=0)
    p2 = _free(PLAYER_TWO, x=7, y=5, altitude=0)
    state = _new_game((p1, p2))
    world = _world()

    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    state, events = step(state, [move], world=world)

    started = [e for e in events if isinstance(e, CommanderHorizontalMoveStartedEvent)]
    assert len(started) == 1
    assert (started[0].from_x, started[0].from_y) == (7, 2)
    assert (started[0].to_x, started[0].to_y) == (8, 2)
    assert started[0].duration_ticks == H_TICKS
    # Authoritative position has not moved yet -- only rendering may
    # interpolate mid-transition.
    assert state.commander_for(PLAYER_ONE).x == 7

    completed: list[CommanderHorizontalMoveCompletedEvent] = []
    for _ in range(H_TICKS - 1):
        state, tick_events = step(state, [], world=world)
        completed.extend(
            e for e in tick_events if isinstance(e, CommanderHorizontalMoveCompletedEvent)
        )
    # Not complete until started_tick(1) + duration_ticks(4) == tick 5.
    assert state.tick == H_TICKS
    assert completed == []
    assert state.commander_for(PLAYER_ONE).x == 7

    state, tick_events = step(state, [], world=world)
    assert state.tick == H_TICKS + 1
    completed = [e for e in tick_events if isinstance(e, CommanderHorizontalMoveCompletedEvent)]
    assert len(completed) == 1
    assert (completed[0].x, completed[0].y) == (8, 2)
    updated = state.commander_for(PLAYER_ONE)
    assert (updated.x, updated.y) == (8, 2)
    assert updated.horizontal_transition is None


def test_vertical_cadence_ascends_and_descends_with_asymmetric_steps() -> None:
    p1 = _free(PLAYER_ONE, x=7, y=2, altitude=10, rising=True)
    p2 = _free(PLAYER_TWO, x=7, y=5, altitude=10, rising=False)
    state = _new_game((p1, p2))
    world = _world()

    ascent_events: list[CommanderVerticalUpdatedEvent] = []
    descent_events: list[CommanderVerticalUpdatedEvent] = []
    for _ in range(V_TICKS):
        state, tick_events = step(state, [], world=world)
        for e in tick_events:
            if isinstance(e, CommanderVerticalUpdatedEvent):
                (ascent_events if e.player_id == PLAYER_ONE else descent_events).append(e)

    # Exactly one cadence tick elapsed (tick == 4): +2 for p1 (rising),
    # -1 for p2 (default gravity/descent), asymmetric per the locked rules.
    assert state.tick == V_TICKS
    assert len(ascent_events) == 1
    assert ascent_events[0].from_altitude == 10
    assert ascent_events[0].to_altitude == 10 + ASCENT
    assert len(descent_events) == 1
    assert descent_events[0].from_altitude == 10
    assert descent_events[0].to_altitude == 10 - DESCENT
    assert state.commander_for(PLAYER_ONE).altitude == 10 + ASCENT
    assert state.commander_for(PLAYER_TWO).altitude == 10 - DESCENT


def test_altitude_clamps_to_0_and_48() -> None:
    # Near the ceiling, rising: the first cadence tick's candidate (49)
    # exceeds 48 and clamps; a second cadence tick at the clamped bound then
    # produces no further change/event (49 -> would-be 51, clamped stable).
    high = _free(PLAYER_ONE, x=7, y=2, altitude=47, rising=True)
    # Near the floor, not rising: two normal descent ticks reach exactly 0,
    # then a third tick's candidate (-1) clamps to a stable 0 with no event.
    low = _free(PLAYER_TWO, x=9, y=5, altitude=2, rising=False)
    world = _world()
    state = _new_game((high, low))

    events_by_cadence_tick: list[list[CommanderVerticalUpdatedEvent]] = []
    for _ in range(3):
        collected: list[CommanderVerticalUpdatedEvent] = []
        for _ in range(V_TICKS):
            state, tick_events = step(state, [], world=world)
            collected.extend(
                e for e in tick_events if isinstance(e, CommanderVerticalUpdatedEvent)
            )
        events_by_cadence_tick.append(collected)

    # Cadence tick 1 (tick 4): high clamps 47+2=49 -> 48; low steps 2-1=1.
    first = events_by_cadence_tick[0]
    high_first = [e for e in first if e.player_id == PLAYER_ONE]
    low_first = [e for e in first if e.player_id == PLAYER_TWO]
    assert len(high_first) == 1 and high_first[0].to_altitude == RULES.commander_max_altitude
    assert len(low_first) == 1 and low_first[0].to_altitude == 1

    # Cadence tick 2 (tick 8): high is stable at the clamped ceiling (no
    # event); low steps 1-1=0.
    second = events_by_cadence_tick[1]
    assert [e for e in second if e.player_id == PLAYER_ONE] == []
    low_second = [e for e in second if e.player_id == PLAYER_TWO]
    assert len(low_second) == 1 and low_second[0].to_altitude == RULES.commander_min_altitude

    # Cadence tick 3 (tick 12): both are stable at their clamped bound (no
    # events at all) -- the clamp never lets altitude leave [0, 48].
    assert events_by_cadence_tick[2] == []
    assert state.commander_for(PLAYER_ONE).altitude == RULES.commander_max_altitude
    assert state.commander_for(PLAYER_TWO).altitude == RULES.commander_min_altitude


def test_horizontal_and_vertical_movement_occur_simultaneously() -> None:
    # The horizontal move is issued on tick 4 (started_tick=4) so its
    # completion (4 + H_TICKS == 8) lands on the same tick as a vertical
    # cadence update (a multiple of V_TICKS), proving both axes can update
    # in one authoritative step.
    p1 = _free(PLAYER_ONE, x=7, y=2, altitude=10, rising=True)
    state = _new_game((p1,))
    world = _world()

    for _ in range(V_TICKS - 1):
        state, _ = step(state, [], world=world)
    assert state.tick == V_TICKS - 1

    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    state, events = step(state, [move], world=world)
    assert state.tick == V_TICKS
    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    # This step is itself a vertical-cadence tick, so the first ascent
    # already lands here too.
    assert any(isinstance(e, CommanderVerticalUpdatedEvent) for e in events)
    assert state.commander_for(PLAYER_ONE).altitude == 10 + ASCENT

    for _ in range(H_TICKS - 1):
        state, _ = step(state, [], world=world)
    assert state.tick == V_TICKS + H_TICKS - 1

    state, tick_events = step(state, [], world=world)
    assert state.tick == V_TICKS + H_TICKS
    assert any(isinstance(e, CommanderHorizontalMoveCompletedEvent) for e in tick_events)
    vertical = [e for e in tick_events if isinstance(e, CommanderVerticalUpdatedEvent)]
    assert len(vertical) == 1
    assert vertical[0].to_altitude == 10 + 2 * ASCENT
    updated = state.commander_for(PLAYER_ONE)
    assert (updated.x, updated.y) == (8, 2)
    assert updated.altitude == 10 + 2 * ASCENT


# ---------------------------------------------------------------------------
# 2. Height-aware collision: clearance vs. blocked
# ---------------------------------------------------------------------------


def test_low_altitude_move_onto_blocker_is_rejected_then_allowed_once_cleared() -> None:
    # box-1 (height 1) sits at (0, 0); the commander's body anchored at
    # (0, 1) covers it. A grounded commander's range [0, 4) overlaps the
    # blocker's [0, 1) -> rejected.
    p1 = _free(PLAYER_ONE, x=1, y=1, altitude=0)
    state = _new_game((p1,))
    world = _world()
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=-1, dy=0)

    state, events = step(state, [move], world=world)
    assert not any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    assert state.commander_for(PLAYER_ONE).x == 1

    # At altitude == blocker height (1), the commander's range [1, 5)
    # merely touches the blocker's [0, 1) -- no overlap -- so the same move
    # is now allowed (sufficient clearance).
    cleared = _free(PLAYER_ONE, x=1, y=1, altitude=1)
    state2 = _new_game((cleared,))
    state2, events2 = step(state2, [move], world=world)
    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events2)


def test_taller_component_requires_more_clearance_than_a_shorter_one() -> None:
    # factory-1 (height 2) sits at (2, 2). At altitude 1 the commander's
    # range [1, 5) still overlaps [0, 2) -> blocked, even though altitude 1
    # was enough to clear the height-1 blocker above.
    p1 = _free(PLAYER_ONE, x=2, y=1, altitude=1)
    state = _new_game((p1,))
    world = _world()
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=0, dy=1)

    state, events = step(state, [move], world=world)
    assert not any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)

    # At altitude == 2 (the component's own height), range [2, 6) merely
    # touches [0, 2) -> allowed.
    cleared = _free(PLAYER_ONE, x=2, y=1, altitude=2)
    state2 = _new_game((cleared,))
    state2, events2 = step(state2, [move], world=world)
    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events2)


# ---------------------------------------------------------------------------
# 3. Commander-vs-commander: separated vs overlapping vertical ranges
# ---------------------------------------------------------------------------


def test_same_cell_allowed_when_vertical_ranges_are_separated() -> None:
    # The mover's next body (8..9, 2..3) overlaps the stationary one
    # (8..9, 1..2): allowed only because the vertical ranges are separated.
    stationary = _free(PLAYER_ONE, x=8, y=2, altitude=0)  # range [0, 4)
    mover = _free(PLAYER_TWO, x=8, y=4, altitude=10)  # range [10, 14)
    state = _new_game((stationary, mover))
    world = _world()
    move = CommanderMoveCommand(player=PLAYER_TWO, sequence=0, dx=0, dy=-1)

    state, events = step(state, [move], world=world)
    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)


def test_same_cell_blocked_when_vertical_ranges_overlap() -> None:
    stationary = _free(PLAYER_ONE, x=8, y=2, altitude=0)  # range [0, 4)
    mover = _free(PLAYER_TWO, x=8, y=4, altitude=2)  # range [2, 6) overlaps [0, 4)
    state = _new_game((stationary, mover))
    world = _world()
    move = CommanderMoveCommand(player=PLAYER_TWO, sequence=0, dx=0, dy=-1)

    state, events = step(state, [move], world=world)
    assert not any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    assert (state.commander_for(PLAYER_TWO).x, state.commander_for(PLAYER_TWO).y) == (8, 4)


def test_descent_is_blocked_by_a_commander_that_would_create_overlap() -> None:
    # Both commanders already share (8, 2). p1 at altitude 4 (range [4, 8))
    # descending would reach 3 (range [3, 7)), which overlaps p2's
    # stationary [0, 4) -- descent must be blocked outright.
    descending = _free(PLAYER_ONE, x=8, y=2, altitude=4, rising=False)
    stationary = _free(PLAYER_TWO, x=8, y=2, altitude=0)
    state = _new_game((descending, stationary))
    world = _world()

    for _ in range(V_TICKS):
        state, tick_events = step(state, [], world=world)
    p1_events = [
        e
        for e in tick_events
        if isinstance(e, CommanderVerticalUpdatedEvent) and e.player_id == PLAYER_ONE
    ]
    assert p1_events == []
    assert state.commander_for(PLAYER_ONE).altitude == 4


# ---------------------------------------------------------------------------
# 4. Friendly docking: descend onto it, follow it, undock through ascent
# ---------------------------------------------------------------------------


def test_friendly_dock_follow_and_undock_through_ascent() -> None:
    robot_id = EntityId("robot-1")
    # One cadence tick above the robot's top surface: descending exactly
    # once (-1) lands the commander precisely on the robot's height.
    commander = _free(PLAYER_ONE, x=7, y=2, altitude=HEIGHT + DESCENT, rising=False)
    state = _new_game((commander,))
    world = _world()
    robot = RobotFixture(id=robot_id, owner=PLAYER_ONE, x=7, y=2, height=HEIGHT)

    dock_events: list[CommanderDockedEvent] = []
    for _ in range(V_TICKS):
        state, tick_events = step(state, [], world=world, robots=(robot,))
        dock_events.extend(e for e in tick_events if isinstance(e, CommanderDockedEvent))

    assert len(dock_events) == 1
    assert dock_events[0].robot_id == robot_id
    updated = state.commander_for(PLAYER_ONE)
    assert updated.mode is CommanderMode.DOCKED
    assert updated.docked_robot_id == robot_id
    assert updated.altitude == HEIGHT

    # Follow: the robot fixture moves one cell east between ticks; the
    # docked commander's position/altitude are derived from it, not moved
    # independently.
    moved_robot = RobotFixture(id=robot_id, owner=PLAYER_ONE, x=8, y=2, height=HEIGHT)
    # The body at (8, 2) is clear of the heli-pads and every structure, so
    # this follow step exercises pure docked-follow geometry only.
    state, _events = step(state, [], world=world, robots=(moved_robot,))
    followed = state.commander_for(PLAYER_ONE)
    assert (followed.x, followed.y, followed.altitude) == (8, 2, HEIGHT)
    assert followed.mode is CommanderMode.DOCKED

    # Undock: holding rise intent while docked flips mode back to FREE and
    # starts one ascent step (+2), in the same authoritative tick.
    rise = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)
    state, events = step(state, [rise], world=world, robots=(moved_robot,))
    undocked_events = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert len(undocked_events) == 1
    assert undocked_events[0].robot_id == robot_id
    assert undocked_events[0].from_altitude == HEIGHT
    assert undocked_events[0].to_altitude == HEIGHT + ASCENT
    freed = state.commander_for(PLAYER_ONE)
    assert freed.mode is CommanderMode.FREE
    assert freed.docked_robot_id is None
    assert freed.altitude == HEIGHT + ASCENT


# ---------------------------------------------------------------------------
# 5. Enemy robot contact: stop at top, never dock, no control/damage change
# ---------------------------------------------------------------------------


def test_enemy_robot_contact_stops_descent_without_docking_or_control_transfer() -> None:
    enemy_robot_id = EntityId("enemy-robot-1")
    # p2's commander descends onto a robot fixture owned by p1.
    commander = _free(PLAYER_TWO, x=8, y=5, altitude=HEIGHT + DESCENT, rising=False)
    state = _new_game((commander,))
    world = _world()
    enemy_robot = RobotFixture(id=enemy_robot_id, owner=PLAYER_ONE, x=8, y=5, height=HEIGHT)

    for _ in range(V_TICKS):
        state, tick_events = step(state, [], world=world, robots=(enemy_robot,))
    dock_events = [e for e in tick_events if isinstance(e, CommanderDockedEvent)]
    assert dock_events == []
    resting = state.commander_for(PLAYER_TWO)
    assert resting.altitude == HEIGHT
    assert resting.mode is CommanderMode.FREE
    assert resting.docked_robot_id is None
    assert resting.player_id == PLAYER_TWO

    # Further descent attempts remain blocked (resting at the enemy robot's
    # top, indefinitely) -- no further altitude change/event, and the
    # commander's identity/control/mode remain completely unaffected: no
    # damage/health concept exists on Commander at all, so mode/player_id/
    # docked_robot_id staying fixed is the strongest available proof that
    # contact never transfers control or deals damage.
    further_events: list[CommanderVerticalUpdatedEvent] = []
    for _ in range(V_TICKS):
        state, tick_events = step(state, [], world=world, robots=(enemy_robot,))
        further_events.extend(
            e for e in tick_events if isinstance(e, CommanderVerticalUpdatedEvent)
        )
    assert further_events == []
    still_resting = state.commander_for(PLAYER_TWO)
    assert still_resting.altitude == HEIGHT
    assert still_resting.mode is CommanderMode.FREE
    assert still_resting.docked_robot_id is None
    assert still_resting.player_id == PLAYER_TWO


# ---------------------------------------------------------------------------
# 6. Heli-pad interaction: friendly grants entry, enemy/other does not
# ---------------------------------------------------------------------------


def test_landing_on_own_heli_pad_emits_construction_entry_eligible() -> None:
    # p1's own 2×2 heli-pad, resting on warbase-p1's 3-high roof component
    # (open-questions.md §18: land at the pad's component height).
    p1 = _free(PLAYER_ONE, x=4, y=1, altitude=PAD_ROOF_ALTITUDE)
    state = _new_game((p1,))
    world = _world()

    _state, events = step(state, [], world=world)

    landing = [e for e in events if isinstance(e, CommanderConstructionEntryEligible)]
    assert len(landing) == 1
    assert landing[0].player == PLAYER_ONE
    assert landing[0].war_base_id == EntityId("warbase-p1")


def test_landing_on_the_other_players_heli_pad_grants_no_entry() -> None:
    # p1's commander lands on p2's heli-pad cell -- not p1's own war base.
    p1 = _free(PLAYER_ONE, x=4, y=3, altitude=PAD_ROOF_ALTITUDE)
    state = _new_game((p1,))
    world = _world()

    _state, events = step(state, [], world=world)
    assert not any(isinstance(e, CommanderConstructionEntryEligible) for e in events)


def test_descending_onto_own_roof_heli_pad_settles_and_enters_through_step() -> None:
    # Flying one descent step above the pad; height-aware collision lets the
    # commander settle on the 3-high roof component, where it lands.
    p1 = _free(PLAYER_ONE, x=4, y=1, altitude=PAD_ROOF_ALTITUDE + DESCENT)
    state = _new_game((p1,))
    world = _world()

    landing: list[CommanderConstructionEntryEligible] = []
    for _ in range(3 * V_TICKS):
        state, events = step(state, [], world=world)
        landing.extend(e for e in events if isinstance(e, CommanderConstructionEntryEligible))

    assert state.commander_for(PLAYER_ONE).altitude == PAD_ROOF_ALTITUDE  # type: ignore[union-attr]
    assert landing and landing[0].war_base_id == EntityId("warbase-p1")


def test_ground_level_below_the_roof_heli_pad_is_not_a_landing() -> None:
    p1 = _free(PLAYER_ONE, x=4, y=1, altitude=0)
    state = _new_game((p1,))

    _state, events = step(state, [], world=_world())
    assert not any(isinstance(e, CommanderConstructionEntryEligible) for e in events)


def test_landing_off_a_heli_pad_cell_grants_no_entry() -> None:
    p1 = _free(PLAYER_ONE, x=2, y=1, altitude=0)  # grounded, but not a heli-pad cell
    state = _new_game((p1,))
    world = _world()

    _state, events = step(state, [], world=world)
    assert not any(isinstance(e, CommanderConstructionEntryEligible) for e in events)


# ---------------------------------------------------------------------------
# 7. Full phase-by-phase milestone scenario + direct replay determinism
# ---------------------------------------------------------------------------


def _drive_full_scenario(world: WorldMap) -> tuple[GameState, tuple[object, ...]]:
    """Drive one coherent multi-phase scenario exercising every M3 rule.

    Phases (all against the shared fixture map/world):

    1. p1 moves horizontally while rising; p2 begins descending
       simultaneously (asymmetric +2/-1 vertical cadence exercised on both
       commanders at once, alongside p1's in-flight horizontal move).
    2. p1's move completes and a vertical-cadence ascent lands on the same
       tick (simultaneous horizontal/vertical movement, per the M3.2
       acceptance criteria).
    3. p1 continues flying toward a friendly robot fixture and docks onto
       it automatically by descending exactly onto its top.
    4. p2 continues descending onto an *enemy*-owned robot fixture at the
       same time, resting at its top without ever docking.
    5. p1 undocks (through one ascent step) and starts climbing away.

    Landing on the friendly war-base heli-pad (bullet 9 of the milestone
    scenario) is deliberately *not* chained onto this same flight: it is
    exercised by ``test_landing_on_own_heli_pad_emits_construction_entry_eligible``
    and ``test_descending_onto_own_roof_heli_pad_settles_and_enters_through_step``
    above (the pad is on ``warbase-p1``'s 3-high roof component, where
    height-aware collision lets the commander settle; open-questions.md §18).

    Returns the final state and the full ordered event stream, so callers
    can replay this twice and diff the results for determinism.
    """
    # 2×2 bodies: the two robots (and so the two commanders over them) sit
    # on separate rows so they never overlap one another.
    friendly_robot = RobotFixture(id=EntityId("robot-1"), owner=PLAYER_ONE, x=8, y=2, height=HEIGHT)
    enemy_robot = RobotFixture(id=EntityId("robot-2"), owner=PLAYER_ONE, x=8, y=5, height=HEIGHT)
    robots = (friendly_robot, enemy_robot)

    p1 = _free(PLAYER_ONE, x=6, y=2, altitude=HEIGHT + DESCENT, rising=True)
    p2 = _free(PLAYER_TWO, x=8, y=5, altitude=HEIGHT + DESCENT, rising=False)
    state = _new_game((p1, p2))

    all_events: list[object] = []

    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    state, events = step(state, [move], world=world, robots=robots)
    all_events.extend(events)

    # Ticks 2..4: p1's horizontal move resolves and the first vertical
    # cadence tick lands simultaneously; p1 stops rising once its move has
    # resolved so it can begin its controlled descent onto the friendly
    # robot at x=8.
    for _ in range(V_TICKS - 1):
        state, events = step(state, [], world=world, robots=robots)
        all_events.extend(events)

    stop_rising = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=False)
    state, events = step(state, [stop_rising], world=world, robots=robots)
    all_events.extend(events)

    # Advance until p1 has walked from x=7 to x=8 (one more horizontal
    # move) and both commanders have descended onto their respective
    # robots.
    move_to_robot = CommanderMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0)
    state, events = step(state, [move_to_robot], world=world, robots=robots)
    all_events.extend(events)

    for _ in range(3 * V_TICKS):
        state, events = step(state, [], world=world, robots=robots)
        all_events.extend(events)

    # p1 should now be docked on the friendly robot; p2 resting (not
    # docked) on the enemy robot.
    p1_state = state.commander_for(PLAYER_ONE)
    p2_state = state.commander_for(PLAYER_TWO)
    assert p1_state.mode is CommanderMode.DOCKED
    assert p2_state.mode is CommanderMode.FREE
    assert p2_state.altitude == HEIGHT
    assert p2_state.docked_robot_id is None

    # Undock p1 through ascent: hold rise while DOCKED flips it back to
    # FREE and starts one ascent step in the same authoritative tick.
    rise = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=2, rising=True)
    state, events = step(state, [], world=world, robots=robots)  # settle any pending completions
    all_events.extend(events)
    state, events = step(state, [rise], world=world, robots=robots)
    all_events.extend(events)

    # A few more cadence ticks of continued ascent away from the robot.
    for _ in range(2 * V_TICKS):
        state, events = step(state, [], world=world, robots=robots)
        all_events.extend(events)

    return state, tuple(all_events)


def test_full_milestone_scenario_docks_undocks_and_leaves_enemy_contact_undisturbed() -> None:
    world = _world()
    final_state, events = _drive_full_scenario(world)

    # Every locked M3 rule left an observable trace in the event stream.
    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    assert any(isinstance(e, CommanderHorizontalMoveCompletedEvent) for e in events)
    assert any(isinstance(e, CommanderVerticalUpdatedEvent) for e in events)
    docked = [e for e in events if isinstance(e, CommanderDockedEvent)]
    undocked = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert len(docked) == 1
    assert len(undocked) == 1
    assert docked[0].robot_id == EntityId("robot-1")
    assert undocked[0].robot_id == EntityId("robot-1")

    p1_final = final_state.commander_for(PLAYER_ONE)
    assert p1_final.mode is CommanderMode.FREE
    assert p1_final.docked_robot_id is None
    assert p1_final.altitude > HEIGHT  # risen clear of the robot since undocking

    # p2 never docked on the enemy robot and is still resting at its top,
    # completely undisturbed (no health/damage/control concept exists on
    # Commander -- mode/docked_robot_id/player_id staying fixed is the
    # strongest available proof of "no contact damage or control transfer").
    p2_final = final_state.commander_for(PLAYER_TWO)
    assert p2_final.mode is CommanderMode.FREE
    assert p2_final.docked_robot_id is None
    assert p2_final.altitude == HEIGHT
    assert p2_final.player_id == PLAYER_TWO


def test_full_milestone_scenario_replays_identically() -> None:
    world = _world()

    final_a, events_a = _drive_full_scenario(world)
    final_b, events_b = _drive_full_scenario(world)

    assert to_snapshot(final_a) == to_snapshot(final_b)
    assert events_a == events_b


# ---------------------------------------------------------------------------
# 8. Supplementary ReplayFixture/run_fixture determinism (harness contract)
# ---------------------------------------------------------------------------


def test_replay_fixture_harness_is_deterministic_for_a_representative_stream() -> None:
    """Proves the replay-fixture contract itself for a shorter command stream.

    See the module docstring: ``ReplayFixture`` has no ``commanders`` field,
    so it cannot seed the pre-existing commanders this module's main
    phase-by-phase scenario needs; the full-scenario determinism proof above
    therefore drives ``engine.step`` directly instead. This supplementary
    test mirrors #42's own ``test_replay_fixture_with_world_and_robots_is_deterministic``:
    it proves ``ReplayFixture``/``run_fixture`` themselves replay a
    world+robots-bearing command stream identically, twice, end to end.
    """
    scenario, map_data = _scenario_and_map()
    world = _world()
    robot = RobotFixture(id=EntityId("robot-1"), owner=PLAYER_ONE, x=3, y=1, height=HEIGHT)

    commands_by_tick = {
        1: (CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),),
        2: (CommanderSetVerticalIntentCommand(player=PLAYER_TWO, sequence=0, rising=True),),
    }
    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=11,
        tick_count=12,
        commands_by_tick=commands_by_tick,
        world=world,
        robots=(robot,),
    )

    final_a, events_a = run_fixture(fixture)
    final_b, events_b = run_fixture(fixture)

    assert to_snapshot(final_a) == to_snapshot(final_b)
    assert events_a == events_b
