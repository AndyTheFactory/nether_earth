"""CR004.4 (#285): the AI construction planner and the commanderless construction entry."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nether_earth import engine
from nether_earth.ai import construction
from nether_earth.ai.construction import (
    DESIGNS,
    choose_design,
    defence_reserve,
    design_cost,
    design_value,
    min_weapons,
    owned_war_bases,
    pool_after,
    threat_radius_cells,
    threatened_war_bases,
    wants_nuclear,
    war_base_order,
)
from nether_earth.capture import StructureOwnership
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    ConstructionEnteredEvent,
    EnterConstructionRemotelyCommand,
    LaunchRobotCommand,
    RobotLaunchedEvent,
    SelectModuleCommand,
)
from nether_earth.construction_economy import ResourcePool, starting_resource_pool
from nether_earth.construction_session import (
    ConstructionEntryRejectionReason,
    enter_construction_remotely,
    exit_construction,
    select_module,
)
from nether_earth.destruction import effective_world
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.rng import MatchRandom
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_launch import LaunchRejectionReason, resolve_launch_exit
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.snapshot import ai_memory_from_snapshot, to_snapshot
from nether_earth.state import AiConstructionMemory, AiMemory, GameState
from nether_earth.structures import FactoryType

ORIGINAL_MAP_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
)
AI_BASE = EntityId("warbase-4")
HUMAN_BASE = EntityId("warbase-1")
NEUTRAL_BASE = EntityId("warbase-3")

B, T, A = ModuleIdentity.BIPOD, ModuleIdentity.TRACKS, ModuleIdentity.ANTI_GRAV
C, M, P, N = ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER, ModuleIdentity.NUCLEAR
E = ModuleIdentity.ELECTRONICS


@pytest.fixture(scope="module")
def world() -> WorldMap:
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def _vs_ai(world: WorldMap, seed: int = 7) -> GameState:
    scenario = dataclasses.replace(default_pvp_scenario(), player_two_controller="ai")
    return create_initial_state(scenario, world, seed=seed)


def _robot(entity_id: str, x: int, y: int, owner=PLAYER_ONE, weapons=(C,)) -> Robot:  # type: ignore[no-untyped-def]
    build = RobotBuild(chassis=B, weapons=tuple(weapons))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id), owner=owner, x=x, y=y, build=build, stack=stack, height=height
    )


def _army(world: WorldMap, count: int) -> tuple[Robot, ...]:
    """``count`` AI robots on free cells well away from every war base."""
    grid = world.occupancy()
    spots = [(x, 5) for x in range(200, 300, 2) if not grid.blocks_unit(x, 5)]
    assert len(spots) >= count
    return tuple(
        _robot(f"robot-p2-{i}", x, y, owner=PLAYER_TWO) for i, (x, y) in enumerate(spots[:count], 1)
    )


def _with_pool(state: GameState, general: int, **category: int) -> GameState:
    pool = PlayerResourcePool(player_id=PLAYER_TWO, general=general, **category)
    others = tuple(p for p in state.resource_pools if p.player_id != PLAYER_TWO)
    return state.with_resource_pools((*others, pool))


def _plan(state: GameState, world: WorldMap) -> tuple[tuple[object, ...], AiMemory]:
    memory = state.ai_memory_for(PLAYER_TWO)
    assert memory is not None
    return construction.plan(state, memory, effective_world(world, state), DEFAULT_RULES, MatchRandom(0))


def _exit_cell(world: WorldMap, state: GameState, base: EntityId) -> tuple[int, int]:
    cell = resolve_launch_exit(world, state, base, robot_height=1)
    assert not isinstance(cell, LaunchRejectionReason)
    return cell


def _two_ai_bases(state: GameState) -> GameState:
    return state.with_structure_ownership((StructureOwnership(NEUTRAL_BASE, PLAYER_TWO),))


def _step(state: GameState, world: WorldMap, ticks: int, commands=()) -> tuple[GameState, list[Event]]:  # type: ignore[no-untyped-def]
    events: list[Event] = []
    for index in range(ticks):
        state, tick_events = engine.step(state, commands if index == 0 else (), world=world)
        events.extend(tick_events)
    return state, events


# --- designs and scoring -------------------------------------------------------


def test_designs_are_every_legal_build_once() -> None:
    assert len(DESIGNS) == 3 * 14 * 2
    builds = {RobotBuild.from_modules(design) for design in DESIGNS}
    assert len(builds) == len(DESIGNS)


def test_design_value_rates_firepower_mobility_and_electronics() -> None:
    assert design_value((B, C)) == 2
    assert design_value((T, M, P)) == 2 + 4 + 4
    assert design_value((A, C, M, P, E)) == 3 + 2 + 4 + 4 + 2
    assert design_value((T, N)) == 2


def test_design_cost_reads_the_rules() -> None:
    assert design_cost((B, C), DEFAULT_RULES) == 5
    assert design_cost((A, N, E), DEFAULT_RULES) == 33


def test_pool_after_follows_the_engine_spend_rule() -> None:
    pool = ResourcePool(general=10, category={FactoryType.CANNON: 1, FactoryType.CHASSIS: 5})
    after = pool_after(pool, (T, C), DEFAULT_RULES)
    assert after is not None
    assert after.general == 9 and after.amount(FactoryType.CANNON) == 0
    assert after.amount(FactoryType.CHASSIS) == 0
    assert pool_after(ResourcePool(general=6), (T, C), DEFAULT_RULES) is None


def test_min_weapons_ramps_with_the_army() -> None:
    assert [min_weapons(n) for n in (0, 3, 4, 7, 8, 20)] == [1, 1, 2, 2, 3, 3]


def test_defence_reserve_is_one_cheapest_robot_once_the_army_exists() -> None:
    assert defence_reserve(0, DEFAULT_RULES) == 0
    assert defence_reserve(construction.RESERVE_MIN_ARMY - 1, DEFAULT_RULES) == 0
    assert defence_reserve(construction.RESERVE_MIN_ARMY, DEFAULT_RULES) == 5


def test_wants_nuclear_only_with_an_army_and_no_live_nuke() -> None:
    assert not wants_nuclear(construction.NUCLEAR_MIN_ARMY - 1, 0)
    assert wants_nuclear(construction.NUCLEAR_MIN_ARMY, 0)
    assert not wants_nuclear(construction.NUCLEAR_MIN_ARMY, 1)


def test_choose_design_picks_the_best_affordable_robot() -> None:
    assert choose_design(starting_resource_pool(DEFAULT_RULES), 0, 0, False, DEFAULT_RULES) == (
        T, C, M, P, E,
    )
    assert choose_design(ResourcePool(general=7), 0, 0, False, DEFAULT_RULES) == (B, M)
    assert choose_design(ResourcePool(general=4), 0, 0, False, DEFAULT_RULES) is None


def test_choose_design_keeps_the_defence_reserve() -> None:
    # 10 general, army of 2: must leave 5, so a 5-cost robot at most.
    assert choose_design(ResourcePool(general=10), 2, 0, False, DEFAULT_RULES) == (B, C)
    assert choose_design(ResourcePool(general=9), 2, 0, False, DEFAULT_RULES) is None


def test_choose_design_honours_the_weapon_floor() -> None:
    design = choose_design(ResourcePool(general=40), 8, 1, False, DEFAULT_RULES)
    assert design is not None and len([m for m in design if m in (C, M, P, N)]) == 3
    # Two weapons not enough for robot 9 on: waits rather than builds.
    assert choose_design(ResourcePool(general=14), 8, 1, False, DEFAULT_RULES) is None


def test_a_threat_releases_the_reserve_and_the_floor() -> None:
    assert choose_design(ResourcePool(general=9), 8, 1, True, DEFAULT_RULES) == (B, C, M)


def test_choose_design_saves_for_a_nuke_when_it_wants_one() -> None:
    army = construction.NUCLEAR_MIN_ARMY
    assert choose_design(ResourcePool(general=20), army, 0, False, DEFAULT_RULES) is None
    design = choose_design(ResourcePool(general=40), army, 0, False, DEFAULT_RULES)
    assert design is not None and N in design
    # Not while threatened: a defender comes first.
    threatened = choose_design(ResourcePool(general=40), army, 0, True, DEFAULT_RULES)
    assert threatened is not None and N not in threatened


# --- war bases -------------------------------------------------------------------


def test_owned_war_bases_in_map_order(world: WorldMap) -> None:
    state = _two_ai_bases(_vs_ai(world))
    assert owned_war_bases(effective_world(world, state), PLAYER_TWO) == (NEUTRAL_BASE, AI_BASE)
    assert owned_war_bases(world, PLAYER_ONE) == (HUMAN_BASE,)


def test_war_base_order_round_robins_and_puts_threatened_first() -> None:
    a, b, c = EntityId("a"), EntityId("b"), EntityId("c")
    assert war_base_order((a, b, c), None) == (a, b, c)
    assert war_base_order((a, b, c), a) == (b, c, a)
    assert war_base_order((a, b, c), c) == (a, b, c)
    assert war_base_order((a, b, c), EntityId("gone")) == (a, b, c)
    assert war_base_order((a, b, c), a, threatened=(c,)) == (c, b, a)


def test_threat_radius_is_the_longest_reach(world: WorldMap) -> None:
    assert threat_radius_cells(DEFAULT_RULES) == 16


def test_threatened_war_bases_sees_enemy_robots_only(world: WorldMap) -> None:
    state = _vs_ai(world)
    ex, ey = _exit_cell(world, state, AI_BASE)
    assert threatened_war_bases(state, world, PLAYER_TWO, DEFAULT_RULES) == ()
    own = state.with_robots((_robot("robot-p2-1", ex, ey, owner=PLAYER_TWO),))
    assert threatened_war_bases(own, world, PLAYER_TWO, DEFAULT_RULES) == ()
    enemy = state.with_robots((_robot("robot-p1-1", ex - 10, ey),))
    assert threatened_war_bases(enemy, world, PLAYER_TWO, DEFAULT_RULES) == (AI_BASE,)
    far = state.with_robots((_robot("robot-p1-1", ex - 60, ey),))
    assert threatened_war_bases(far, world, PLAYER_TWO, DEFAULT_RULES) == ()


# --- the commanderless entry -------------------------------------------------------


def test_ai_seat_enters_construction_at_a_war_base_it_owns(world: WorldMap) -> None:
    state = _vs_ai(world)
    result = enter_construction_remotely(state, world, PLAYER_TWO, AI_BASE, 3)
    assert result.accepted and result.state is not None
    session = result.state.construction_session_for(PLAYER_TWO)
    assert session is not None and session.war_base_id == AI_BASE and session.entry_tick == 3
    assert result.state.resource_pools == state.resource_pools


@pytest.mark.parametrize("base", [HUMAN_BASE, NEUTRAL_BASE, EntityId("no-such-base")])
def test_ai_seat_cannot_enter_at_a_war_base_it_does_not_own(world: WorldMap, base: EntityId) -> None:
    result = enter_construction_remotely(_vs_ai(world), world, PLAYER_TWO, base, 3)
    assert result.reason is ConstructionEntryRejectionReason.NOT_OWN_WAR_BASE


def test_a_human_seat_cannot_use_the_commanderless_entry(world: WorldMap) -> None:
    state = _vs_ai(world)
    result = enter_construction_remotely(state, world, PLAYER_ONE, HUMAN_BASE, 3)
    assert result.reason is ConstructionEntryRejectionReason.NOT_AI_SEAT
    # Through the engine: accepted structurally, no session, no entry event.
    command = EnterConstructionRemotelyCommand(player=PLAYER_ONE, sequence=0, war_base_id=HUMAN_BASE)
    after, events = _step(state, world, 1, (command,))
    assert after.construction_session_for(PLAYER_ONE) is None
    assert not any(isinstance(e, ConstructionEnteredEvent) for e in events)


def test_a_human_seat_in_pvp_cannot_use_the_commanderless_entry(world: WorldMap) -> None:
    state = create_initial_state(default_pvp_scenario(), world, seed=7)
    for player, base in ((PLAYER_ONE, HUMAN_BASE), (PLAYER_TWO, AI_BASE)):
        result = enter_construction_remotely(state, world, player, base, 3)
        assert result.reason is ConstructionEntryRejectionReason.NOT_AI_SEAT


def test_commanderless_entry_rejects_a_second_session(world: WorldMap) -> None:
    state = _two_ai_bases(_vs_ai(world))
    live = effective_world(world, state)
    first = enter_construction_remotely(state, live, PLAYER_TWO, AI_BASE, 3)
    assert first.state is not None
    second = enter_construction_remotely(first.state, live, PLAYER_TWO, NEUTRAL_BASE, 3)
    assert second.reason is ConstructionEntryRejectionReason.ALREADY_IN_SESSION


def test_engine_applies_the_ai_entry_only_for_an_owned_base(world: WorldMap) -> None:
    state = _vs_ai(world)
    # Tick 1 is not an AI decision tick, so only these commands run.
    enemy = EnterConstructionRemotelyCommand(player=PLAYER_TWO, sequence=0, war_base_id=HUMAN_BASE)
    after, events = _step(state, world, 1, (enemy,))
    assert after.construction_session_for(PLAYER_TWO) is None
    own = EnterConstructionRemotelyCommand(player=PLAYER_TWO, sequence=0, war_base_id=AI_BASE)
    after, events = _step(state, world, 1, (own,))
    assert after.construction_session_for(PLAYER_TWO) is not None
    assert [e.war_base_id for e in events if isinstance(e, ConstructionEnteredEvent)] == [AI_BASE]


def test_exit_construction_for_the_commanderless_seat_only_drops_the_session(world: WorldMap) -> None:
    state = _vs_ai(world)
    entered = enter_construction_remotely(state, world, PLAYER_TWO, AI_BASE, 3).state
    assert entered is not None
    selected = select_module(entered, PLAYER_TWO, T).state
    assert selected is not None
    exited = exit_construction(selected, PLAYER_TWO)
    assert exited.construction_session_for(PLAYER_TWO) is None
    assert exited.commanders == state.commanders
    assert exited.resource_pools == state.resource_pools


# --- the planner -----------------------------------------------------------------------


def test_planner_builds_through_the_construction_commands(world: WorldMap) -> None:
    commands, memory = _plan(_vs_ai(world), world)
    assert commands == (
        EnterConstructionRemotelyCommand(player=PLAYER_TWO, sequence=0, war_base_id=AI_BASE),
        *(SelectModuleCommand(player=PLAYER_TWO, sequence=0, module=m) for m in (T, C, M, P, E)),
        LaunchRobotCommand(player=PLAYER_TWO, sequence=0),
    )
    assert memory.construction == AiConstructionMemory(last_war_base_id=AI_BASE)


def test_planner_issues_nothing_it_cannot_afford(world: WorldMap) -> None:
    state = _with_pool(_vs_ai(world), general=4)
    commands, memory = _plan(state, world)
    assert commands == ()
    assert memory == state.ai_memory_for(PLAYER_TWO)


def test_planner_skips_a_war_base_whose_exit_is_blocked(world: WorldMap) -> None:
    state = _two_ai_bases(_vs_ai(world))
    live = effective_world(world, state)
    x, y = _exit_cell(live, state, NEUTRAL_BASE)
    blocked = state.with_robots((_robot("robot-p1-1", x, y),))
    commands, _memory = _plan(blocked, world)
    assert isinstance(commands[0], EnterConstructionRemotelyCommand)
    assert commands[0].war_base_id == AI_BASE
    # Every exit blocked: no session is opened at all.
    ax, ay = _exit_cell(live, state, AI_BASE)
    all_blocked = state.with_robots(
        (_robot("robot-p1-1", x, y), _robot("robot-p1-2", ax, ay))
    )
    assert _plan(all_blocked, world)[0] == ()


def test_planner_builds_at_every_owned_war_base_in_turn(world: WorldMap) -> None:
    state = _two_ai_bases(_vs_ai(world))
    bases = []
    for _ in range(3):
        commands, memory = _plan(state, world)
        entry = commands[0]
        assert isinstance(entry, EnterConstructionRemotelyCommand)
        bases.append(entry.war_base_id)
        state = state.with_ai_memory(memory)
    assert bases == [NEUTRAL_BASE, AI_BASE, NEUTRAL_BASE]


def test_planner_builds_a_defender_at_the_threatened_base(world: WorldMap) -> None:
    state = _two_ai_bases(_vs_ai(world))
    live = effective_world(world, state)
    ax, ay = _exit_cell(live, state, AI_BASE)
    army = _army(world, 8)
    threatened = _with_pool(state.with_robots((*army, _robot("robot-p1-1", ax - 12, ay))), general=9)
    commands, _memory = _plan(threatened, world)
    assert isinstance(commands[0], EnterConstructionRemotelyCommand)
    assert commands[0].war_base_id == AI_BASE
    assert [c.module for c in commands if isinstance(c, SelectModuleCommand)] == [B, C, M]
    # Same economy without the threat: the floor (3 weapons) and reserve hold it back.
    calm = _with_pool(state.with_robots(army), general=9)
    assert _plan(calm, world)[0] == ()


def test_planner_stops_at_the_robot_cap(world: WorldMap) -> None:
    army = _army(world, DEFAULT_RULES.max_robots_per_player)
    state = _with_pool(_vs_ai(world).with_robots(army), general=99)
    assert _plan(state, world)[0] == ()


def test_planner_cancels_a_leftover_session(world: WorldMap) -> None:
    state = _with_pool(_vs_ai(world), general=4)
    entered = enter_construction_remotely(state, world, PLAYER_TWO, AI_BASE, 3).state
    assert entered is not None
    commands, _memory = _plan(entered, world)
    assert commands == (CancelConstructionCommand(player=PLAYER_TWO, sequence=0),)


def test_memory_round_trips_through_the_snapshot(world: WorldMap) -> None:
    _commands, memory = _plan(_vs_ai(world), world)
    state = _vs_ai(world).with_ai_memory(memory)
    (entry,) = to_snapshot(state)["ai_memories"]
    assert entry["construction"] == {"last_war_base_id": "warbase-4"}
    assert ai_memory_from_snapshot(entry) == memory
    fresh = to_snapshot(_vs_ai(world))["ai_memories"][0]
    assert ai_memory_from_snapshot(fresh).construction == AiConstructionMemory()


# --- in the engine -----------------------------------------------------------------------


def test_ai_builds_and_launches_a_legal_robot_within_one_decision(world: WorldMap) -> None:
    state, events = _step(_vs_ai(world, seed=11), world, DEFAULT_RULES.ai_decision_interval_ticks)
    launched = [e for e in events if isinstance(e, RobotLaunchedEvent)]
    assert [(e.player, e.tick) for e in launched] == [(PLAYER_TWO, 4)]
    robot = state.robot_for(launched[0].robot_id)
    assert robot is not None and robot.owner == PLAYER_TWO
    assert robot.build == RobotBuild.from_modules((T, C, M, P, E))
    pool = state.resource_pool_for(PLAYER_TWO)
    assert pool is not None and pool.general == 20 - 18
    assert state.construction_sessions == ()


def test_ai_never_leaves_a_session_open_or_overspends(world: WorldMap) -> None:
    """Over a game day and a half: every entry launches in the same tick."""
    state = _vs_ai(world, seed=3)
    launches = 0
    for _ in range(4400):
        state, events = engine.step(state, (), world=world)
        entered = [e for e in events if isinstance(e, ConstructionEnteredEvent)]
        launched = [e for e in events if isinstance(e, RobotLaunchedEvent)]
        assert len(entered) == len(launched)
        assert state.construction_sessions == ()
        launches += len(launched)
    assert launches == 2
    pool = state.resource_pool_for(PLAYER_TWO)
    assert pool is not None and pool.general >= 0


def test_ai_match_is_deterministic(world: WorldMap) -> None:
    def run() -> list[str]:
        state = _vs_ai(world, seed=5)
        out = []
        for _ in range(40):
            state, _events = engine.step(state, (), world=world)
            out.append(repr(to_snapshot(state)))
        return out

    assert run() == run()


def test_a_session_at_a_destroyed_war_base_is_cancelled_cleanly(world: WorldMap) -> None:
    state = _vs_ai(world)
    entered = enter_construction_remotely(state, world, PLAYER_TWO, AI_BASE, 0).state
    assert entered is not None
    entered = select_module(entered, PLAYER_TWO, T).state
    assert entered is not None
    destroyed = entered.with_structure_destruction((AI_BASE,))
    after, events = _step(destroyed, world, DEFAULT_RULES.ai_decision_interval_ticks)
    assert after.construction_session_for(PLAYER_TWO) is None
    assert after.robots_for(PLAYER_TWO) == ()
    assert after.resource_pools == state.resource_pools
    assert not any(isinstance(e, (ConstructionEnteredEvent, RobotLaunchedEvent)) for e in events)


def test_a_session_at_a_captured_war_base_is_cancelled_cleanly(world: WorldMap) -> None:
    state = _vs_ai(world)
    entered = enter_construction_remotely(state, world, PLAYER_TWO, AI_BASE, 0).state
    assert entered is not None
    captured = entered.with_structure_ownership((StructureOwnership(AI_BASE, PLAYER_ONE),))
    after, events = _step(captured, world, DEFAULT_RULES.ai_decision_interval_ticks)
    assert after.construction_session_for(PLAYER_TWO) is None
    assert after.robots_for(PLAYER_TWO) == ()
    assert after.resource_pools == state.resource_pools
    assert not any(isinstance(e, (ConstructionEnteredEvent, RobotLaunchedEvent)) for e in events)
