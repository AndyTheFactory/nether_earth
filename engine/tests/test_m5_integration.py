"""M5 milestone integration scenario (issue #68, M5.9).

The final Milestone-5 acceptance gate: one coherent, deterministic
battlefield exercising the composition of every M5 system rather than each
in isolation -- movement/terrain permissions, movement durations, commander
blocking, destination reservations/contention, direct control, autonomous
orders (Advance/Retreat/Search & Capture/Search & Destroy/Stop & Defend),
navigation (non-electronic vs electronic), capture (neutral acquisition,
continuous capture, interruption/reset), war-base capture triggering
same-step victory, and engagement intent. M6 firing/damage is not asserted
here, but since CR003.3 (Spectrum piece heights, every robot taller than the
bullet altitude) the engine's combat step does hit and destroy some lane
robots, so lanes are checked when their behaviour completes.

Everything here runs through the real ``engine.new_game``/``engine.step``
pipeline (via ``replay.run_fixture`` and a local recording variant of the
same loop) -- no alternate movement/capture semantics are invented in the
test. See ``tests/fixtures/world_map_m5_integration.yaml`` for the
battlefield layout and its "lanes" rationale.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

from nether_earth import engine
from nether_earth.capture import (
    CapturableStructureKind,
    NeutralStructureAcquiredEvent,
    StructureCapturedEvent,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import CommanderMoveCommand
from nether_earth.commands import Command
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.events import Event, order_events
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap, load_world_map
from nether_earth.movement import RobotMoveStartedEvent
from nether_earth.orders import (
    Advance,
    RobotEngagementIntentEvent,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
)
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.reservations import DestinationContentionResolvedEvent
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, miles_to_cells
from nether_earth.scenario import Scenario
from nether_earth.snapshot import snapshot_to_json_string, to_snapshot
from nether_earth.state import GameState
from nether_earth.victory import VictoryEvent

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "world_map_m5_integration.yaml"
MAP_ID = "fixture-m5-integration"
MAP_VERSION = 1

SEED = 68680919

BIPOD_TICKS = DEFAULT_RULES.robot_move_ticks_bipod_normal
TRACKS_TICKS = DEFAULT_RULES.robot_move_ticks_tracks_normal
ANTI_TICKS = DEFAULT_RULES.robot_move_ticks_anti_grav_normal
ROUGH_BIPOD_TICKS = DEFAULT_RULES.robot_move_ticks_bipod_rough
ROUGH_TRACKS_TICKS = DEFAULT_RULES.robot_move_ticks_tracks_rough
ROUGH_ANTI_TICKS = DEFAULT_RULES.robot_move_ticks_anti_grav_rough
DITCH_ANTI_TICKS = DEFAULT_RULES.robot_move_ticks_anti_grav_ditch
CAPTURE_TICKS = DEFAULT_RULES.capture_duration_ticks

# --------------------------------------------------------------------------
# Entity ids
# --------------------------------------------------------------------------

ROBOT_BIPOD = EntityId("robot-bipod")
ROBOT_TRACKS = EntityId("robot-tracks")
ROBOT_ANTIGRAV = EntityId("robot-antigrav")

ROBOT_NAV_DUMB = EntityId("robot-nav-dumb")
ROBOT_NAV_SMART = EntityId("robot-nav-smart")

ROBOT_CONTEND_WEST = EntityId("robot-contend-west")
ROBOT_CONTEND_EAST = EntityId("robot-contend-east")

ROBOT_BLOCKED = EntityId("robot-blocked")

ROBOT_DEFENDER = EntityId("robot-defender")
ROBOT_ATTACKER = EntityId("robot-attacker")

ROBOT_DIRECT = EntityId("robot-direct")

ROBOT_FACTORY_CAPTOR = EntityId("robot-factory-captor")
ROBOT_WARBASE_CAPTOR = EntityId("robot-warbase-captor")

WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")
FACTORY_NEUTRAL = EntityId("factory-neutral")
FACTORY_ENEMY = EntityId("factory-enemy")

# 2×2 bodies (CR002.3): the neutral factory has a two-cell capture point so
# the two contention robots each have their own nearest capture cell (two
# robots can never claim the same anchor: a robot one step from it already
# overlaps the other's body).
FACTORY_NEUTRAL_CELLS = ((19, 17), (20, 17))
FACTORY_ENEMY_CELL = (25, 30)
WARBASE_ONE_CELL = (2, 31)

# --------------------------------------------------------------------------
# Command-schedule tick constants
# --------------------------------------------------------------------------

TICK_ORDERS = 1
TICK_INTERRUPT = 10
TICK_RECAPTURE = 40
TICK_UNBLOCK = 30

# Long enough for the war-base recapture (issued at TICK_RECAPTURE, after a
# 2-cell/32-tick transit) to reach the full CAPTURE_TICKS continuous
# occupation and for victory to be evaluated in that same step, plus slack
# for every other, much-shorter lane.
TOTAL_TICKS = TICK_RECAPTURE + 2 * TRACKS_TICKS + CAPTURE_TICKS + 40


def _robot(
    entity_id: EntityId,
    owner: PlayerId,
    x: int,
    y: int,
    *,
    chassis: ModuleIdentity,
    electronics: ModuleIdentity | None = None,
    order=None,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=(ModuleIdentity.CANNON,), electronics=electronics)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=entity_id,
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        order=order,
    )


def _initial_robots() -> tuple[Robot, ...]:
    return (
        # Chassis terrain-permission / movement-duration lane.
        _robot(ROBOT_BIPOD, PLAYER_ONE, 0, 1, chassis=ModuleIdentity.BIPOD, order=Advance(3)),
        _robot(ROBOT_TRACKS, PLAYER_ONE, 0, 3, chassis=ModuleIdentity.TRACKS, order=Advance(3)),
        _robot(
            ROBOT_ANTIGRAV, PLAYER_ONE, 0, 5, chassis=ModuleIdentity.ANTI_GRAV, order=Advance(3)
        ),
        # Navigation lane: identical Advance goal, dumb vs electronic tracks.
        _robot(
            ROBOT_NAV_DUMB,
            PLAYER_ONE,
            10,
            8,
            chassis=ModuleIdentity.TRACKS,
            order=Advance(8),
        ),
        _robot(
            ROBOT_NAV_SMART,
            PLAYER_ONE,
            10,
            11,
            chassis=ModuleIdentity.TRACKS,
            electronics=ModuleIdentity.ELECTRONICS,
            order=Advance(8),
        ),
        # Contention lane: both order Search & Capture(neutral factory) at
        # tick 1, one step west/east of the two-cell capture point.
        _robot(
            ROBOT_CONTEND_WEST,
            PLAYER_ONE,
            FACTORY_NEUTRAL_CELLS[0][0] - 1,
            FACTORY_NEUTRAL_CELLS[0][1],
            chassis=ModuleIdentity.TRACKS,
        ),
        _robot(
            ROBOT_CONTEND_EAST,
            PLAYER_TWO,
            FACTORY_NEUTRAL_CELLS[1][0] + 1,
            FACTORY_NEUTRAL_CELLS[1][1],
            chassis=ModuleIdentity.TRACKS,
        ),
        # Commander-blocking lane.
        _robot(ROBOT_BLOCKED, PLAYER_ONE, 1, 22, chassis=ModuleIdentity.TRACKS, order=Advance(1)),
        # Search & Destroy / engagement-intent lane.
        _robot(
            ROBOT_DEFENDER,
            PLAYER_ONE,
            5,
            26,
            chassis=ModuleIdentity.TRACKS,
            order=StopAndDefend(),
        ),
        _robot(
            ROBOT_ATTACKER,
            PLAYER_TWO,
            7,
            26,
            chassis=ModuleIdentity.TRACKS,
            order=SearchDestroy(target=SearchDestroyTarget.ROBOT),
        ),
        # Direct-control lane.
        _robot(ROBOT_DIRECT, PLAYER_ONE, 2, 28, chassis=ModuleIdentity.TRACKS),
        # Capture lane.
        _robot(
            ROBOT_FACTORY_CAPTOR,
            PLAYER_TWO,
            23,
            30,
            chassis=ModuleIdentity.TRACKS,
            order=SearchCapture(target=SearchCaptureTarget.ENEMY_FACTORY),
        ),
        _robot(
            ROBOT_WARBASE_CAPTOR,
            PLAYER_TWO,
            WARBASE_ONE_CELL[0],
            WARBASE_ONE_CELL[1],
            chassis=ModuleIdentity.TRACKS,
        ),
    )


def _commanders() -> tuple[Commander, ...]:
    return (
        Commander(
            player_id=PLAYER_ONE,
            mode=CommanderMode.DOCKED,
            x=2,
            y=28,
            altitude=0,
            docked_robot_id=ROBOT_DIRECT,
        ),
        # Its body (3..4, 22..23) overlaps the lane robot's next body.
        Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=3, y=23, altitude=0),
    )


def _commands_by_tick() -> dict[int, tuple[Command, ...]]:
    commands: dict[int, list[Command]] = {}

    def add(tick: int, command: Command) -> None:
        commands.setdefault(tick, []).append(command)

    # Direct-control move: p1 nudges its docked robot east one cell.
    add(TICK_ORDERS, DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0))

    # Contention: both robots order Search & Capture onto the same cell.
    add(
        TICK_ORDERS,
        SetRobotOrderCommand(
            player=PLAYER_ONE,
            sequence=1,
            entity_id=ROBOT_CONTEND_WEST,
            order=SearchCapture(target=SearchCaptureTarget.NEUTRAL_FACTORY),
        ),
    )
    add(
        TICK_ORDERS,
        SetRobotOrderCommand(
            player=PLAYER_TWO,
            sequence=1,
            entity_id=ROBOT_CONTEND_EAST,
            order=SearchCapture(target=SearchCaptureTarget.NEUTRAL_FACTORY),
        ),
    )

    # p2's commander unblocks the commander-blocking lane by stepping aside
    # (its body leaves the lane's rows entirely, so it never re-blocks the
    # robot's goal at x=3).
    add(TICK_UNBLOCK, CommanderMoveCommand(player=PLAYER_TWO, sequence=0, dx=0, dy=1))

    # Interrupt the war-base captor's in-progress capture, then send it
    # back to finish the job (Search & Capture re-selects the same target).
    add(
        TICK_INTERRUPT,
        SetRobotOrderCommand(
            player=PLAYER_TWO,
            sequence=2,
            entity_id=ROBOT_WARBASE_CAPTOR,
            order=Advance(1),
        ),
    )
    add(
        TICK_RECAPTURE,
        SetRobotOrderCommand(
            player=PLAYER_TWO,
            sequence=3,
            entity_id=ROBOT_WARBASE_CAPTOR,
            order=SearchCapture(target=SearchCaptureTarget.ENEMY_WAR_BASE),
        ),
    )

    return {tick: tuple(cmds) for tick, cmds in commands.items()}


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="m5-integration-scenario",
        map_id=MAP_ID,
        map_version=MAP_VERSION,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id=MAP_ID, version=MAP_VERSION, width=40, height=32)
    return scenario, map_data


def _fixture() -> ReplayFixture:
    scenario, map_data = _scenario_and_map()
    world = load_world_map(FIXTURE_PATH)
    return ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=SEED,
        tick_count=TOTAL_TICKS,
        commands_by_tick=_commands_by_tick(),
        world=world,
        commanders=_commanders(),
        initial_robots=_initial_robots(),
    )


@dataclass(frozen=True, slots=True)
class Drive:
    states: tuple[GameState, ...]  # states[t] is the state after t ticks
    events: tuple[Event, ...]
    world: object


def _drive(fixture: ReplayFixture) -> Drive:
    """Run ``fixture`` tick by tick, recording the state after every tick.

    ``replay.run_fixture`` only returns the final state; this scenario's
    assertions need to observe intermediate states (e.g. "still stuck at
    tick 25" or "progress reset by tick 30"), so this mirrors
    ``run_fixture``'s own loop while additionally keeping every
    intermediate ``GameState``.
    """
    state = engine.new_game(
        fixture.map_data, fixture.scenario, seed=fixture.seed, commanders=fixture.commanders
    )
    if fixture.initial_robots:
        state = state.with_robots(fixture.initial_robots)

    states = [state]
    all_events: list[Event] = []
    for tick in range(1, fixture.tick_count + 1):
        cmds = fixture.commands_by_tick.get(tick, ())
        state, tick_events = engine.step(state, cmds, world=fixture.world, robots=fixture.robots)
        states.append(state)
        all_events.extend(order_events(tick_events))

    return Drive(states=tuple(states), events=tuple(all_events), world=fixture.world)


def _first_tick(states: tuple[GameState, ...], predicate) -> int | None:
    for tick, state in enumerate(states):
        if predicate(state):
            return tick
    return None


def _events_of(events: tuple[Event, ...], cls) -> list:
    return [e for e in events if isinstance(e, cls)]


# --------------------------------------------------------------------------
# The scenario, driven once and shared by every assertion in this module.
# --------------------------------------------------------------------------


def test_full_milestone_scenario_composes_all_m5_rules() -> None:
    drive = _drive(_fixture())
    states = drive.states
    events = drive.events
    final = states[-1]

    move_started_events = _events_of(events, RobotMoveStartedEvent)
    move_started_by_robot: dict[EntityId, list[RobotMoveStartedEvent]] = {}
    for e in move_started_events:
        move_started_by_robot.setdefault(e.entity_id, []).append(e)

    # ---------------------------------------------------------------
    # 1. Chassis terrain permissions + centralized movement durations.
    # ---------------------------------------------------------------
    assert BIPOD_TICKS > TRACKS_TICKS > ANTI_TICKS  # locked relative speed ranking

    # 2×2 bodies (CR002.3): each move is charged by the governing terrain
    # of the destination body, and a body with any cell the chassis cannot
    # enter is refused.
    bipod_moves = move_started_by_robot[ROBOT_BIPOD]
    assert bipod_moves[0].duration_ticks == BIPOD_TICKS
    assert bipod_moves[0].to_x == 1 and bipod_moves[0].to_y == 1  # body over NORMAL
    assert bipod_moves[1].duration_ticks == ROUGH_BIPOD_TICKS
    assert bipod_moves[1].to_x == 2 and bipod_moves[1].to_y == 1  # body over ROUGH
    # Bipod cannot enter a body over the ditch at x=4: no further move is
    # ever started, so it remains stuck on the rough body for the rest of
    # the run.
    assert len(bipod_moves) == 2
    bipod_robot = final.robot_for(ROBOT_BIPOD)
    assert bipod_robot is not None
    assert (bipod_robot.x, bipod_robot.y) == (2, 1)

    tracks_moves = move_started_by_robot[ROBOT_TRACKS]
    assert tracks_moves[0].duration_ticks == TRACKS_TICKS
    assert tracks_moves[1].duration_ticks == ROUGH_TRACKS_TICKS
    assert len(tracks_moves) == 2  # tracks cannot enter the ditch either
    tracks_robot = final.robot_for(ROBOT_TRACKS)
    assert tracks_robot is not None
    assert (tracks_robot.x, tracks_robot.y) == (2, 3)

    antigrav_moves = move_started_by_robot[ROBOT_ANTIGRAV]
    assert antigrav_moves[0].duration_ticks == ANTI_TICKS  # normal
    assert antigrav_moves[1].duration_ticks == ROUGH_ANTI_TICKS  # rough
    assert antigrav_moves[2].duration_ticks == ROUGH_ANTI_TICKS  # rough + ditch: rough governs
    assert antigrav_moves[3].duration_ticks == DITCH_ANTI_TICKS  # ditch
    # Anti-grav may enter every terrain type and completes the full
    # Advance(3 miles = 6 cells) order, transitioning to Stop & Defend.
    antigrav_robot = final.robot_for(ROBOT_ANTIGRAV)
    assert antigrav_robot is not None
    assert (antigrav_robot.x, antigrav_robot.y) == (0 + miles_to_cells(3), 5)
    assert antigrav_robot.order == StopAndDefend()

    # ---------------------------------------------------------------
    # 2. Advance/Retreat distance via the shared miles-to-cells helper.
    # ---------------------------------------------------------------
    assert miles_to_cells(3) == 6
    assert antigrav_robot.x == 6

    # ---------------------------------------------------------------
    # 3. Commander blocking (M3 contract): robot-blocked cannot pass
    #    through p2's commander cell until it moves away.
    # ---------------------------------------------------------------
    before_unblock = states[TICK_UNBLOCK - 1]
    still_blocked = before_unblock.robot_for(ROBOT_BLOCKED)
    assert still_blocked is not None
    assert (still_blocked.x, still_blocked.y) == (1, 22)  # never advanced past the commander
    # Checked when the Advance completes: with the Spectrum piece heights
    # (CR003.3) every robot is tall enough to be hit, so later in the run
    # the enemy war-base captor's cannon destroys this robot.
    unblocked_tick = _first_tick(
        states,
        lambda s: (r := s.robot_for(ROBOT_BLOCKED)) is not None and r.order == StopAndDefend(),
    )
    assert unblocked_tick is not None and unblocked_tick > TICK_UNBLOCK
    after_unblock = states[unblocked_tick].robot_for(ROBOT_BLOCKED)
    assert after_unblock is not None
    assert (after_unblock.x, after_unblock.y) == (1 + miles_to_cells(1), 22)

    # ---------------------------------------------------------------
    # 4. Same-tick destination contention: exactly one contest, one winner.
    # ---------------------------------------------------------------
    contentions = _events_of(events, DestinationContentionResolvedEvent)
    assert len(contentions) == 1
    contention = contentions[0]
    assert set(contention.contenders) == {ROBOT_CONTEND_WEST, ROBOT_CONTEND_EAST}
    contend_started = [
        e for e in move_started_events if e.entity_id in (ROBOT_CONTEND_WEST, ROBOT_CONTEND_EAST)
    ]
    assert len(contend_started) == 1  # only the seeded winner actually started a move
    winner_id = contend_started[0].entity_id
    loser_id = ROBOT_CONTEND_EAST if winner_id == ROBOT_CONTEND_WEST else ROBOT_CONTEND_WEST
    # With the Spectrum piece heights (CR003.3) every robot is tall enough to
    # be hit, so the two contenders later shoot each other; check the
    # outcome once the winner's move completes.
    contend_done_tick = contend_started[0].started_tick + contend_started[0].duration_ticks
    winner = states[contend_done_tick].robot_for(winner_id)
    assert winner is not None and states[contend_done_tick].robot_for(loser_id) is not None
    assert (winner.x, winner.y) in FACTORY_NEUTRAL_CELLS
    # The loser never reaches the capture point: the winner's body blocks it.
    assert all(
        (loser.x, loser.y) not in FACTORY_NEUTRAL_CELLS
        for state in states
        if (loser := state.robot_for(loser_id)) is not None
    )

    # ---------------------------------------------------------------
    # 5. Neutral factory instant acquisition.
    # ---------------------------------------------------------------
    acquisitions = _events_of(events, NeutralStructureAcquiredEvent)
    neutral_acq = [e for e in acquisitions if e.structure_id == FACTORY_NEUTRAL]
    assert len(neutral_acq) == 1
    assert neutral_acq[0].robot_id == winner_id
    assert neutral_acq[0].structure_kind == CapturableStructureKind.FACTORY

    # ---------------------------------------------------------------
    # 6. Enemy factory continuous capture.
    # ---------------------------------------------------------------
    factory_captures = [
        e for e in _events_of(events, StructureCapturedEvent) if e.structure_id == FACTORY_ENEMY
    ]
    assert len(factory_captures) == 1
    fc = factory_captures[0]
    assert fc.structure_kind == CapturableStructureKind.FACTORY
    assert fc.previous_owner == PLAYER_ONE
    assert fc.new_owner == PLAYER_TWO
    assert fc.robot_id == ROBOT_FACTORY_CAPTOR

    # ---------------------------------------------------------------
    # 7. Capture interruption resets progress immediately, then the
    #    structure is re-captured and completion triggers same-step
    #    victory for p2 (this is p1's only war base).
    # ---------------------------------------------------------------
    before_interrupt = states[TICK_INTERRUPT - 1]
    progress_before = before_interrupt.capture_progress_for(WAR_BASE_ONE)
    assert progress_before is not None
    assert progress_before.elapsed_ticks == TICK_INTERRUPT - 1
    assert progress_before.capturing_player == PLAYER_TWO

    # Well after the interrupting move completed (TICK_INTERRUPT + TRACKS_TICKS)
    # but before the recapture order is issued: progress must be gone.
    reset_check_tick = TICK_INTERRUPT + TRACKS_TICKS + 1
    assert reset_check_tick < TICK_RECAPTURE
    reset_state = states[reset_check_tick]
    assert reset_state.capture_progress_for(WAR_BASE_ONE) is None
    warbase_captures_so_far = [
        e
        for e in events
        if isinstance(e, StructureCapturedEvent)
        and e.structure_id == WAR_BASE_ONE
        and e.tick <= reset_check_tick
    ]
    assert warbase_captures_so_far == []  # not captured yet -- interrupted first

    warbase_captures = [
        e for e in _events_of(events, StructureCapturedEvent) if e.structure_id == WAR_BASE_ONE
    ]
    assert len(warbase_captures) == 1
    wb = warbase_captures[0]
    assert wb.previous_owner == PLAYER_ONE
    assert wb.new_owner == PLAYER_TWO
    assert wb.robot_id == ROBOT_WARBASE_CAPTOR

    victories = _events_of(events, VictoryEvent)
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_TWO
    assert victories[0].tick == wb.tick  # same-step victory evaluation

    # ---------------------------------------------------------------
    # 8. Direct control movement.
    # ---------------------------------------------------------------
    direct_moves = move_started_by_robot[ROBOT_DIRECT]
    assert len(direct_moves) == 1
    assert direct_moves[0].to_x == 3 and direct_moves[0].to_y == 28
    # Checked when the move completes (the war-base captor's cannon later
    # destroys this robot, CR003.3 heights).
    direct_done_tick = direct_moves[0].started_tick + direct_moves[0].duration_ticks
    direct_robot = states[direct_done_tick].robot_for(ROBOT_DIRECT)
    assert direct_robot is not None
    assert (direct_robot.x, direct_robot.y) == (3, 28)

    # ---------------------------------------------------------------
    # 9. Search & Destroy target selection + engagement intent (M6
    #    firing/damage is not asserted here; since CR003.3's Spectrum
    #    piece heights the engine's combat step does hit these robots).
    # ---------------------------------------------------------------
    intents = _events_of(events, RobotEngagementIntentEvent)
    defender_intents = [e for e in intents if e.intent.robot_id == ROBOT_DEFENDER]
    attacker_intents = [e for e in intents if e.intent.robot_id == ROBOT_ATTACKER]
    assert defender_intents, "Stop & Defend must produce engagement intent vs. the nearest hostile"
    assert attacker_intents, "Search & Destroy must produce engagement intent vs. its target"
    assert defender_intents[0].intent.target_id == ROBOT_ATTACKER
    assert attacker_intents[0].intent.target_id == ROBOT_DEFENDER
    # While both combatants exist neither moves onto the other's cell: they
    # stay adjacent (the engine's combat step eventually destroys one).
    both_alive = [
        (defender, attacker)
        for state in states
        if (defender := state.robot_for(ROBOT_DEFENDER)) is not None
        and (attacker := state.robot_for(ROBOT_ATTACKER)) is not None
    ]
    assert len(both_alive) > 1
    for defender, attacker in both_alive:
        assert (defender.x, defender.y) == (5, 26)
        assert (attacker.x, attacker.y) == (7, 26)

    # ---------------------------------------------------------------
    # 10. Navigation: non-electronic stuck vs electronic replanning.
    # ---------------------------------------------------------------
    dumb_final = final.robot_for(ROBOT_NAV_DUMB)
    smart_final = final.robot_for(ROBOT_NAV_SMART)
    assert dumb_final is not None and smart_final is not None
    # The dumb robot never gets past the wall's leading edge: its 2×2 body
    # (13..14) stops against the wall at x=15. It has no detour logic even
    # though the rows 12-13 gap is a valid, shorter-than-infinite route.
    assert dumb_final.x == 13
    assert dumb_final.order != StopAndDefend()  # never completes -- permanently stuck
    # The electronic robot successfully routes around the wall and
    # completes its Advance.
    assert smart_final.x == 10 + miles_to_cells(8)
    assert smart_final.order == StopAndDefend()
    smart_arrival_tick = _first_tick(
        states, lambda s: (r := s.robot_for(ROBOT_NAV_SMART)) is not None and r.x == smart_final.x
    )
    assert smart_arrival_tick is not None
    assert smart_arrival_tick < TOTAL_TICKS - 40  # arrives with room to spare, not on the last tick


def test_full_milestone_scenario_replays_identically() -> None:
    fixture = _fixture()
    state_a, events_a = run_fixture(fixture)
    state_b, events_b = run_fixture(fixture)

    assert state_a == state_b
    assert to_snapshot(state_a) == to_snapshot(state_b)
    assert snapshot_to_json_string(state_a) == snapshot_to_json_string(state_b)
    assert events_a == events_b

    # Also cross-check against the recording drive used by the main
    # scenario test, so both code paths agree on the final outcome.
    drive = _drive(fixture)
    assert drive.states[-1] == state_a
    assert drive.events == events_a
