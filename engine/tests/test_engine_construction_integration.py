"""Integration tests for construction/economy wiring into ``engine.step`` (issue #57, M4.7).

Exercises the full command pipeline end to end -- ``engine.step`` with real
``Command`` instances, not the underlying pure functions called directly --
covering: automatic construction entry on heli-pad landing (and its
idempotency across multiple ticks), select/deselect/cancel/launch commands,
snapshot completeness, daily production crossing a 2880-tick boundary, and
replay/determinism (including a rejected-launch scenario replaying without
hidden side effects).
"""

from __future__ import annotations

from itertools import pairwise

from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import CommanderMoveCommand, CommanderSetVerticalIntentCommand
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    ConstructionCancelledEvent,
    ConstructionEnteredEvent,
    DeselectModuleCommand,
    LaunchRobotCommand,
    ModuleDeselectedEvent,
    ModuleSelectedEvent,
    RobotLaunchedEvent,
    SelectModuleCommand,
)
from nether_earth.construction_economy import module_cost
from nether_earth.engine import new_game, step
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.resource_pool import starting_player_resource_pool
from nether_earth.resource_production import DailyProductionApplied
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")
WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")
FACTORY_ONE = EntityId("factory-p1")

HELI_PAD_CELL = (4, 0)
EXIT_CELL = (5, 0)


def _world(*, with_factory: bool = False) -> WorldMap:
    war_bases = (
        WarBase(id=WAR_BASE_ONE, components=(Component(x=4, y=0, height=3),), owner=PLAYER_ONE),
        WarBase(id=WAR_BASE_TWO, components=(Component(x=0, y=9, height=3),), owner=PLAYER_TWO),
    )
    factories: tuple[Factory, ...] = ()
    if with_factory:
        factories = (
            Factory(
                id=FACTORY_ONE,
                components=(Component(x=10, y=10, height=2),),
                factory_type=FactoryType.CHASSIS,
                owner=PLAYER_ONE,
            ),
        )
    interaction_points = (
        InteractionPoint(
            id="warbase-p1-helipad",
            kind=InteractionKind.HELI_PAD,
            structure_id=WAR_BASE_ONE,
            footprint=_footprint(HELI_PAD_CELL),
        ),
        InteractionPoint(
            id="warbase-p1-exit",
            kind=InteractionKind.EXIT,
            structure_id=WAR_BASE_ONE,
            footprint=_footprint(EXIT_CELL),
        ),
    )
    return WorldMap(
        map_id="test-construction-integration",
        version=1,
        width=20,
        height=20,
        terrain=TerrainGrid(width=20, height=20, cells={}),
        war_bases=war_bases,
        factories=factories,
        blockers=(),
        interaction_points=interaction_points,
        spawn_positions={},
    )


def _footprint(cell: tuple[int, int]) -> Footprint:
    return Footprint(cells=frozenset({cell}))


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="fixture-scenario",
        map_id="test-construction-integration",
        map_version=1,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id="test-construction-integration", version=1, width=20, height=20)
    return scenario, map_data


def _grounded_commander_on_heli_pad(player_id: PlayerId = PLAYER_ONE) -> Commander:
    x, y = HELI_PAD_CELL
    return Commander(
        player_id=player_id,
        mode=CommanderMode.FREE,
        x=x,
        y=y,
        # The pad cell is warbase-p1's 3-high component: the commander lands
        # on that roof (open-questions.md §18).
        altitude=3,
    )


def _base_state(
    commanders: tuple[Commander, ...] = (), *, seed_resource_pools: bool = True
) -> GameState:
    """Build a tick-0 state for these tests, with player resource pools
    seeded to the starting Spectrum grant by default (matching the
    "player has actual resources before entering construction" precondition
    every construction-flow test in this module relies on --
    ``engine.new_game``/``create_game_state`` do not auto-seed resource
    pools, since seeding match-start resources is a caller/scenario
    responsibility this engine-internals-only test module does not want to
    silently depend on). Pass ``seed_resource_pools=False`` for tests that
    specifically want to exercise the "no resource pool recorded yet"
    default-zeroed path.
    """
    scenario, map_data = _scenario_and_map()
    state = new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=1)
    state = state.with_commanders(commanders)
    if seed_resource_pools:
        state = state.with_resource_pools(
            (
                starting_player_resource_pool(PLAYER_ONE),
                starting_player_resource_pool(PLAYER_TWO),
            )
        )
    return state


# --- Automatic construction entry -------------------------------------------


def test_landing_on_own_heli_pad_automatically_enters_construction() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))

    state, events = step(state, [], world=world)

    entered = [e for e in events if isinstance(e, ConstructionEnteredEvent)]
    assert len(entered) == 1
    assert entered[0].player == PLAYER_ONE
    assert entered[0].war_base_id == WAR_BASE_ONE
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.war_base_id == WAR_BASE_ONE


def test_lingering_on_heli_pad_across_ticks_does_not_reenter_or_duplicate() -> None:
    """Idempotency: landing is re-detected every grounded tick, but
    ``enter_construction`` rejects an already-active session, so no second
    ``ConstructionEnteredEvent`` fires and the session identity is stable."""
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))

    state, first_events = step(state, [], world=world)
    assert any(isinstance(e, ConstructionEnteredEvent) for e in first_events)
    session_after_first = state.construction_session_for(PLAYER_ONE)
    assert session_after_first is not None

    state, second_events = step(state, [], world=world)
    assert not any(isinstance(e, ConstructionEnteredEvent) for e in second_events)
    session_after_second = state.construction_session_for(PLAYER_ONE)
    assert session_after_second == session_after_first
    assert len(state.construction_sessions) == 1


# --- Full select -> launch flow through the command pipeline ----------------


def test_full_construction_flow_select_and_launch_through_step() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))

    # Tick 1: auto-enter construction.
    state, events = step(state, [], world=world)
    assert state.construction_session_for(PLAYER_ONE) is not None

    # Tick 2: select a chassis and a weapon via commands.
    select_chassis = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_weapon = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, events = step(state, [select_chassis, select_weapon], world=world)
    selected = [e for e in events if isinstance(e, ModuleSelectedEvent)]
    assert {e.module for e in selected} == {ModuleIdentity.BIPOD, ModuleIdentity.CANNON}
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis == ModuleIdentity.BIPOD
    assert session.build.weapons == (ModuleIdentity.CANNON,)
    assert session.build.is_complete()

    # Tick 3: launch.
    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)
    state, events = step(state, [launch], world=world)
    launched = [e for e in events if isinstance(e, RobotLaunchedEvent)]
    assert len(launched) == 1
    assert launched[0].player == PLAYER_ONE

    assert state.construction_session_for(PLAYER_ONE) is None
    assert len(state.robots) == 1
    robot = state.robots[0]
    assert robot.owner == PLAYER_ONE
    assert (robot.x, robot.y) == EXIT_CELL
    assert robot.entity_id == launched[0].robot_id

    pool = state.resource_pool_for(PLAYER_ONE)
    assert pool is not None
    bipod_cost = module_cost(ModuleIdentity.BIPOD, DEFAULT_RULES)
    cannon_cost = module_cost(ModuleIdentity.CANNON, DEFAULT_RULES)
    assert pool.general == DEFAULT_RULES.starting_general_resources - bipod_cost - cannon_cost


def test_deselect_module_refunds_buffer_through_step() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))
    state, _events = step(state, [], world=world)

    select = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.CANNON)
    state, _events = step(state, [select], world=world)
    session_after_select = state.construction_session_for(PLAYER_ONE)
    assert session_after_select is not None
    assert session_after_select.build.contains(ModuleIdentity.CANNON)

    deselect = DeselectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.CANNON)
    state, events = step(state, [deselect], world=world)
    deselected = [e for e in events if isinstance(e, ModuleDeselectedEvent)]
    assert len(deselected) == 1
    assert deselected[0].module == ModuleIdentity.CANNON
    session_after_deselect = state.construction_session_for(PLAYER_ONE)
    assert session_after_deselect is not None
    assert not session_after_deselect.build.contains(ModuleIdentity.CANNON)
    # Fully refunded back to the entry snapshot.
    assert session_after_deselect.buffer == session_after_select.entry_snapshot


def test_select_other_chassis_swaps_through_step_with_deselect_then_select_events() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))
    state, _events = step(state, [], world=world)

    state, _events = step(
        state, [SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)], world=world
    )
    swap = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.TRACKS)
    state, events = step(state, [swap], world=world)

    changes = [e for e in events if isinstance(e, (ModuleDeselectedEvent, ModuleSelectedEvent))]
    assert [(type(e), e.module) for e in changes] == [
        (ModuleDeselectedEvent, ModuleIdentity.BIPOD),
        (ModuleSelectedEvent, ModuleIdentity.TRACKS),
    ]
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis is ModuleIdentity.TRACKS
    assert session.buffer.general == session.entry_snapshot.general - module_cost(ModuleIdentity.TRACKS, DEFAULT_RULES)


def test_cancel_construction_through_step_discards_session_without_touching_resources() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))
    state, _events = step(state, [], world=world)

    select = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.CANNON)
    state, _events = step(state, [select], world=world)
    assert state.construction_session_for(PLAYER_ONE) is not None
    pools_before_cancel = state.resource_pools

    cancel = CancelConstructionCommand(player=PLAYER_ONE, sequence=0)
    state, events = step(state, [cancel], world=world)

    cancelled = [e for e in events if isinstance(e, ConstructionCancelledEvent)]
    assert len(cancelled) == 1
    assert state.construction_session_for(PLAYER_ONE) is None
    # Cancelling before launch permanently consumes nothing.
    assert state.resource_pools == pools_before_cancel


def test_cancel_with_no_active_session_is_a_silent_no_op_no_event() -> None:
    world = _world()
    state = _base_state()

    cancel = CancelConstructionCommand(player=PLAYER_ONE, sequence=0)
    state, events = step(state, [cancel], world=world)

    assert not any(isinstance(e, ConstructionCancelledEvent) for e in events)
    assert state.construction_session_for(PLAYER_ONE) is None


# --- Failed launch: rejected without hidden side effects, replay-safe -------


def test_launch_with_incomplete_build_is_rejected_without_side_effects_and_replays_identically() -> (
    None
):
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))
    state, _events = step(state, [], world=world)  # enter construction

    select_chassis_only = SelectModuleCommand(
        player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD
    )
    state, _events = step(state, [select_chassis_only], world=world)

    pre_launch_state = state
    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)

    result_1_state, result_1_events = step(pre_launch_state, [launch], world=world)
    result_2_state, result_2_events = step(pre_launch_state, [launch], world=world)

    assert not any(isinstance(e, RobotLaunchedEvent) for e in result_1_events)
    assert result_1_state.robots == ()
    assert result_1_state.construction_session_for(PLAYER_ONE) is not None
    # Repeating the exact same rejected-launch scenario from the same
    # starting state produces an identical resulting state and event stream.
    assert result_1_state == result_2_state
    assert result_1_events == result_2_events
    assert to_snapshot(result_1_state) == to_snapshot(result_2_state)


def test_launch_robot_cap_reached_replays_without_hidden_side_effects() -> None:
    """Regression per M4.6's own rejected-launch scenario, replayed through
    the full ``engine.step`` command pipeline this time (not the pure
    ``launch_robot`` function directly)."""
    world = _world()
    rules_capped_state = _base_state((_grounded_commander_on_heli_pad(),))
    state = rules_capped_state
    state, _events = step(state, [], world=world)  # enter construction

    select_chassis = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_weapon = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, _events = step(state, [select_chassis, select_weapon], world=world)

    # Manually cap the player at the default max (24) robots by injecting
    # dummy robots directly onto state -- cheaper than launching 24 times.
    from nether_earth.robot import Robot
    from nether_earth.robot_build import RobotBuild
    from nether_earth.robot_stack import derive_stack_and_height

    dummy_build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.MISSILE,))
    stack, height = derive_stack_and_height(dummy_build, DEFAULT_RULES)
    dummy_robots = tuple(
        Robot(
            entity_id=EntityId(f"robot-p1-{i}"),
            owner=PLAYER_ONE,
            x=50 + i,
            y=50,
            build=dummy_build,
            stack=stack,
            height=height,
        )
        for i in range(1, DEFAULT_RULES.max_robots_per_player + 1)
    )
    state = state.with_robots(dummy_robots)
    pre_launch_state = state

    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)
    result_1_state, result_1_events = step(pre_launch_state, [launch], world=world)
    result_2_state, result_2_events = step(pre_launch_state, [launch], world=world)

    assert not any(isinstance(e, RobotLaunchedEvent) for e in result_1_events)
    assert len(result_1_state.robots_for(PLAYER_ONE)) == DEFAULT_RULES.max_robots_per_player
    assert result_1_state.construction_session_for(PLAYER_ONE) is not None
    assert result_1_state == result_2_state
    assert result_1_events == result_2_events


def test_launch_without_world_is_a_gameplay_no_op() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))
    state, _events = step(state, [], world=world)  # enter construction (needs world)

    select_chassis = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_weapon = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, _events = step(state, [select_chassis, select_weapon], world=world)

    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)
    # No world supplied this call -- launch cannot resolve an exit cell.
    state, events = step(state, [launch])

    assert not any(isinstance(e, RobotLaunchedEvent) for e in events)
    assert state.robots == ()
    assert state.construction_session_for(PLAYER_ONE) is not None


# --- Daily production wired into step, crossing a day boundary -------------


def test_daily_production_applies_once_crossing_2880_tick_boundary() -> None:
    world = _world(with_factory=True)
    state = _base_state()

    production_events: list[DailyProductionApplied] = []
    for _ in range(2880):
        state, tick_events = step(state, [], world=world)
        production_events.extend(e for e in tick_events if isinstance(e, DailyProductionApplied))

    assert state.tick == 2880
    # Both players own a war base in this world, so both produce general
    # resources; only p1 also owns the (optional) factory.
    assert len(production_events) == 2
    events_by_player = {e.player: e for e in production_events}
    assert set(events_by_player) == {PLAYER_ONE, PLAYER_TWO}
    p1_event = events_by_player[PLAYER_ONE]
    assert p1_event.day_boundaries_crossed == 1
    assert p1_event.general_amount == DEFAULT_RULES.war_base_production_amount
    assert dict(p1_event.category_amounts)[FactoryType.CHASSIS] == (
        DEFAULT_RULES.factory_production_amount
    )
    p2_event = events_by_player[PLAYER_TWO]
    assert p2_event.general_amount == DEFAULT_RULES.war_base_production_amount
    assert dict(p2_event.category_amounts)[FactoryType.CHASSIS] == 0

    pool = state.resource_pool_for(PLAYER_ONE)
    assert pool is not None
    assert pool.general == (
        DEFAULT_RULES.starting_general_resources + DEFAULT_RULES.war_base_production_amount
    )
    assert pool.chassis == DEFAULT_RULES.factory_production_amount

    pool_two = state.resource_pool_for(PLAYER_TWO)
    assert pool_two is not None
    assert pool_two.general == (
        DEFAULT_RULES.starting_general_resources + DEFAULT_RULES.war_base_production_amount
    )
    assert pool_two.chassis == 0


def test_no_production_without_world() -> None:
    state = _base_state(seed_resource_pools=False)
    for _ in range(2880):
        state, tick_events = step(state, [])
        assert not any(isinstance(e, DailyProductionApplied) for e in tick_events)
    # No world means no production system runs at all; no pools created.
    assert state.resource_pools == ()


def test_production_boundary_crossing_replays_identically_twice() -> None:
    world = _world(with_factory=True)

    def _run() -> tuple[object, tuple[object, ...]]:
        state = _base_state()
        all_events: list[object] = []
        for _ in range(2880):
            state, tick_events = step(state, [], world=world)
            all_events.extend(tick_events)
        return state, tuple(all_events)

    state_1, events_1 = _run()
    state_2, events_2 = _run()

    assert state_1 == state_2
    assert events_1 == events_2
    assert to_snapshot(state_1) == to_snapshot(state_2)


# --- Snapshot completeness ----------------------------------------------------


def test_snapshot_contains_resource_pools_construction_sessions_and_robots() -> None:
    world = _world()
    commander = _grounded_commander_on_heli_pad()
    state = _base_state((commander,))
    state, _events = step(state, [], world=world)

    select_chassis = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_weapon = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, _events = step(state, [select_chassis, select_weapon], world=world)

    snap_mid_construction = to_snapshot(state)
    assert snap_mid_construction["construction_sessions"]
    session_snap = snap_mid_construction["construction_sessions"][0]
    assert session_snap["player_id"] == PLAYER_ONE.value
    assert session_snap["build"]["chassis"] == ModuleIdentity.BIPOD.value
    assert session_snap["build"]["weapons"] == [ModuleIdentity.CANNON.value]

    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)
    state, _events = step(state, [launch], world=world)

    snap_after_launch = to_snapshot(state)
    assert snap_after_launch["construction_sessions"] == []
    assert len(snap_after_launch["robots"]) == 1
    robot_snap = snap_after_launch["robots"][0]
    assert robot_snap["owner"] == PLAYER_ONE.value
    assert robot_snap["build"]["chassis"] == ModuleIdentity.BIPOD.value
    assert len(snap_after_launch["resource_pools"]) == 2
    pool_ids = {pool["player_id"] for pool in snap_after_launch["resource_pools"]}
    assert pool_ids == {PLAYER_ONE.value, PLAYER_TWO.value}


# --- Full-run determinism: identical command stream -> identical outcome ---


def test_identical_command_stream_produces_identical_state_events_and_snapshot() -> None:
    world = _world(with_factory=True)

    def _run() -> tuple[object, tuple[object, ...]]:
        commander = _grounded_commander_on_heli_pad()
        state = _base_state((commander,))
        all_events: list[object] = []

        state, tick_events = step(state, [], world=world)  # tick 1: auto-enter
        all_events.extend(tick_events)

        select_chassis = SelectModuleCommand(
            player=PLAYER_ONE, sequence=0, module=ModuleIdentity.ANTI_GRAV
        )
        select_weapon_1 = SelectModuleCommand(
            player=PLAYER_ONE, sequence=1, module=ModuleIdentity.PHASER
        )
        select_weapon_2 = SelectModuleCommand(
            player=PLAYER_ONE, sequence=2, module=ModuleIdentity.CANNON
        )
        state, tick_events = step(
            state, [select_chassis, select_weapon_1, select_weapon_2], world=world
        )
        all_events.extend(tick_events)

        deselect = DeselectModuleCommand(
            player=PLAYER_ONE, sequence=0, module=ModuleIdentity.CANNON
        )
        state, tick_events = step(state, [deselect], world=world)
        all_events.extend(tick_events)

        launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)
        state, tick_events = step(state, [launch], world=world)
        all_events.extend(tick_events)

        return state, tuple(all_events)

    state_1, events_1 = _run()
    state_2, events_2 = _run()

    assert state_1 == state_2
    assert events_1 == events_2
    assert to_snapshot(state_1) == to_snapshot(state_2)
    # Sanity: the run actually did what it claims.
    assert len(state_1.robots) == 1
    assert state_1.robots[0].build.chassis == ModuleIdentity.ANTI_GRAV
    assert state_1.robots[0].build.weapons == (ModuleIdentity.PHASER,)


# --- Leaving the construction screen (CR002.12 #179 / CR002.13 #180) --------
#
# Spectrum semantics: EXIT MENU (``Lcb8e_construction_screen_exit``) discards
# the build and sets ``Lfd30_player_elevate_timer`` to 5; START ROBOT
# (``Lcb52_construction_screen_start_robot``) commits the robot and falls
# through to the same exit. The ship then ascends ``commander_ascent_step``
# per vertical update for 5 updates before gravity applies again, so the
# screen does not re-open on the next tick.

PAD_ALTITUDE = 3  # the pad cell's component height in `_world()`
EXIT_PEAK = PAD_ALTITUDE + DEFAULT_RULES.commander_construction_exit_elevate_updates * (
    DEFAULT_RULES.commander_ascent_step
)


def _in_session_with(modules: tuple[ModuleIdentity, ...]) -> GameState:
    world = _world()
    state = _base_state((_grounded_commander_on_heli_pad(),))
    state, _events = step(state, [], world=world)
    selects = [
        SelectModuleCommand(player=PLAYER_ONE, sequence=i, module=m) for i, m in enumerate(modules)
    ]
    state, _events = step(state, selects, world=world)
    assert state.construction_session_for(PLAYER_ONE) is not None
    return state


def _altitudes_until_reentry(state: GameState, world: WorldMap) -> tuple[GameState, list[int]]:
    """Step with no commands until construction re-opens; return the altitude trace."""
    trace: list[int] = []
    for _ in range(200):
        state, events = step(state, [], world=world)
        commander = state.commander_for(PLAYER_ONE)
        assert commander is not None
        trace.append(commander.altitude)
        if any(isinstance(e, ConstructionEnteredEvent) for e in events):
            return state, trace
    raise AssertionError("construction never re-opened")


def _assert_exit_ascent(trace: list[int]) -> None:
    # Up by +2 per vertical update to exactly pad + 10, then gravity -1 per
    # update back onto the pad; no overshoot, no teleport.
    peak_index = trace.index(max(trace))
    assert max(trace) == EXIT_PEAK
    assert trace[-1] == PAD_ALTITUDE
    rising = [a for i, a in enumerate(trace[: peak_index + 1]) if i == 0 or a != trace[i - 1]]
    assert rising == list(range(PAD_ALTITUDE + 2, EXIT_PEAK + 1, 2))
    falling = trace[peak_index:]
    assert all(b in (a, a - 1) for a, b in pairwise(falling))


def test_exit_menu_with_build_in_progress_closes_screen_and_lifts_commander() -> None:
    world = _world()
    state = _in_session_with((ModuleIdentity.BIPOD, ModuleIdentity.CANNON))
    pools_before = state.resource_pools

    exit_menu = CancelConstructionCommand(player=PLAYER_ONE, sequence=0)
    state, events = step(state, [exit_menu], world=world)

    assert any(isinstance(e, ConstructionCancelledEvent) for e in events)
    assert state.construction_session_for(PLAYER_ONE) is None
    assert state.resource_pools == pools_before
    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert commander.elevate_updates_remaining == 5
    assert commander.altitude == PAD_ALTITUDE

    # Regression (#180): the screen used to re-open on the very next tick.
    state, events = step(state, [], world=world)
    assert state.construction_session_for(PLAYER_ONE) is None
    assert not any(isinstance(e, ConstructionEnteredEvent) for e in events)

    state, trace = _altitudes_until_reentry(state, world)
    _assert_exit_ascent(trace)
    # Left alone on the pad, the screen re-opens with a fresh, empty build.
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis is None and session.build.weapons == ()


def test_exit_menu_without_build_in_progress_closes_screen_and_lifts_commander() -> None:
    world = _world()
    state = _in_session_with(())
    pools_before = state.resource_pools

    state, _events = step(state, [CancelConstructionCommand(player=PLAYER_ONE, sequence=0)], world=world)

    assert state.construction_session_for(PLAYER_ONE) is None
    assert state.resource_pools == pools_before
    state, trace = _altitudes_until_reentry(state, world)
    _assert_exit_ascent(trace)


def test_start_robot_launches_closes_screen_and_lifts_commander() -> None:
    world = _world()
    state = _in_session_with((ModuleIdentity.BIPOD, ModuleIdentity.CANNON))

    state, events = step(state, [LaunchRobotCommand(player=PLAYER_ONE, sequence=0)], world=world)

    assert any(isinstance(e, RobotLaunchedEvent) for e in events)
    assert state.construction_session_for(PLAYER_ONE) is None
    assert [(r.x, r.y) for r in state.robots_for(PLAYER_ONE)] == [EXIT_CELL]
    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert (commander.x, commander.y) == HELI_PAD_CELL
    assert commander.elevate_updates_remaining == 5

    # Regression (#179): no immediate re-entry; the commander lifts off the
    # pad by the Spectrum exit ascent, not further.
    state, events = step(state, [], world=world)
    assert state.construction_session_for(PLAYER_ONE) is None
    state, trace = _altitudes_until_reentry(state, world)
    _assert_exit_ascent(trace)


def test_rejected_start_robot_keeps_the_screen_open_and_commander_on_pad() -> None:
    world = _world()
    state = _in_session_with((ModuleIdentity.BIPOD,))  # no weapon

    state, _events = step(state, [LaunchRobotCommand(player=PLAYER_ONE, sequence=0)], world=world)

    assert state.construction_session_for(PLAYER_ONE) is not None
    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert commander.elevate_updates_remaining == 0
    assert commander.altitude == PAD_ALTITUDE


def test_commander_cannot_leave_the_pad_while_the_screen_is_open() -> None:
    world = _world()
    state = _in_session_with(())
    rise = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0)

    state, _events = step(state, [rise, move], world=world)
    for _ in range(12):
        state, _events = step(state, [], world=world)

    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert (commander.x, commander.y, commander.altitude) == (*HELI_PAD_CELL, PAD_ALTITUDE)
    assert commander.horizontal_transition is None
    assert state.construction_session_for(PLAYER_ONE) is not None


def test_after_exit_menu_the_commander_can_fly_away_without_reentering() -> None:
    world = _world()
    state = _in_session_with(())
    state, _events = step(state, [CancelConstructionCommand(player=PLAYER_ONE, sequence=0)], world=world)

    rise = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)
    move = CommanderMoveCommand(player=PLAYER_ONE, sequence=1, dx=0, dy=1)
    state, _events = step(state, [rise, move], world=world)
    for _ in range(40):
        state, events = step(state, [], world=world)
        assert not any(isinstance(e, ConstructionEnteredEvent) for e in events)

    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert (commander.x, commander.y) == (HELI_PAD_CELL[0], HELI_PAD_CELL[1] + 1)
    assert commander.altitude > EXIT_PEAK  # rise held: keeps climbing after the exit ascent
    assert state.construction_session_for(PLAYER_ONE) is None
