"""Tests for the AI robot-order sub-planner (CR004.5, #286)."""

from __future__ import annotations

import dataclasses

import pytest

from nether_earth import engine
from nether_earth.ai import robot_orders
from nether_earth.ai.robot_orders import (
    HOLD_RADIUS_CELLS,
    THREAT_RADIUS_CELLS,
    approach_order,
    capture_score,
    hit_damage,
    is_closing,
    matchup,
    same_order,
    structure_value,
    weapon_reach,
)
from nether_earth.capture import StructureOwnership
from nether_earth.commands import Command
from nether_earth.engine import CommandAccepted
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.orders import (
    MAX_ORDER_DISTANCE_MILES,
    Advance,
    Order,
    Retreat,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
    apply_set_robot_order,
    evaluate_orders,
)
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.rng import MatchRandom
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.snapshot import ai_memory_from_snapshot, to_snapshot
from nether_earth.state import (
    AiDefenceAssignment,
    AiMemory,
    AiOrderMemory,
    AiSighting,
    GameState,
    create_game_state,
)
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

AI = PLAYER_TWO
HUMAN = PLAYER_ONE
RULES = DEFAULT_RULES

C = ModuleIdentity.CANNON
M = ModuleIdentity.MISSILE
P = ModuleIdentity.PHASER
N = ModuleIdentity.NUCLEAR


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _war_base(ident: str, x: int, owner: PlayerId | None) -> tuple[WarBase, InteractionPoint]:
    base = WarBase(id=EntityId(ident), components=(Component(x=x, y=0, height=3),), owner=owner)
    point = InteractionPoint(
        id=f"{ident}-capture",
        kind=InteractionKind.WARBASE_CAPTURE,
        structure_id=base.id,
        footprint=Footprint(cells=frozenset({(x, 8)})),
    )
    return base, point


def _factory(
    ident: str, x: int, kind: FactoryType, owner: PlayerId | None = None
) -> tuple[Factory, InteractionPoint]:
    factory = Factory(
        id=EntityId(ident),
        components=(Component(x=x, y=0, height=3),),
        factory_type=kind,
        owner=owner,
    )
    point = InteractionPoint(
        id=f"{ident}-capture",
        kind=InteractionKind.FACTORY_CAPTURE,
        structure_id=factory.id,
        footprint=Footprint(cells=frozenset({(x, 3)})),
    )
    return factory, point


def _world(
    *structures: tuple[WarBase | Factory, InteractionPoint], width: int = 400
) -> WorldMap:
    return WorldMap(
        map_id="ai-orders-test-map",
        version=1,
        width=width,
        height=16,
        terrain=TerrainGrid(width=width, height=16, cells={}, default=TerrainType.NORMAL),
        war_bases=tuple(s for s, _ in structures if isinstance(s, WarBase)),
        factories=tuple(s for s, _ in structures if isinstance(s, Factory)),
        blockers=(),
        interaction_points=tuple(point for _, point in structures),
        spawn_positions={},
    )


def _robot(
    ident: str,
    x: int,
    y: int = 8,
    *,
    owner: PlayerId = AI,
    weapons: tuple[ModuleIdentity, ...] = (C,),
    chassis: ModuleIdentity = ModuleIdentity.TRACKS,
    electronics: ModuleIdentity | None = None,
    order: Order | None = None,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons, electronics=electronics)
    stack, height = derive_stack_and_height(build, RULES)
    return Robot(
        entity_id=EntityId(ident),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        order=order if order is not None else StopAndDefend(),
    )


def _state(
    *robots: Robot,
    memory: AiOrderMemory | None = None,
    ownership: tuple[StructureOwnership, ...] = (),
    pools: tuple[PlayerResourcePool, ...] = (),
) -> GameState:
    ai_memory = AiMemory(AI, orders=memory if memory is not None else AiOrderMemory())
    return create_game_state(
        0,
        (HUMAN, AI),
        robots=list(robots),
        structure_ownership=list(ownership),
        resource_pools=list(pools) or None,
        ai_memories=[ai_memory],
    )


def _plan(state: GameState, world: WorldMap) -> tuple[dict[str, Order], AiMemory]:
    memory = state.ai_memory_for(AI)
    assert memory is not None
    commands, updated = robot_orders.plan(state, memory, world, RULES, MatchRandom(0))
    issued: dict[str, Order] = {}
    for command in commands:
        assert isinstance(command, SetRobotOrderCommand)
        assert command.player == AI
        assert command.entity_id.value not in issued, "one order per robot per decision"
        issued[command.entity_id.value] = command.order
    return issued, updated


def _apply(state: GameState, world: WorldMap) -> tuple[GameState, dict[str, Order]]:
    """Plan, apply the orders as the engine does, write the memory back."""
    memory = state.ai_memory_for(AI)
    assert memory is not None
    commands, updated = robot_orders.plan(state, memory, world, RULES, MatchRandom(0))
    for command in commands:
        assert isinstance(command, SetRobotOrderCommand)
        state, _event = apply_set_robot_order(command, state, tick=state.tick)
    issued = {c.entity_id.value: c.order for c in commands if isinstance(c, SetRobotOrderCommand)}
    return state.with_ai_memory(updated), issued


def _capture_targets(state: GameState, world: WorldMap) -> dict[str, str | None]:
    """The structure each robot's Search & Capture selects on the engine's next evaluation."""
    targets: dict[str, str | None] = {}
    for evaluation in evaluate_orders(state, world, RULES):
        if isinstance(evaluation.order, SearchCapture):
            sid = evaluation.order.structure_id
            targets[evaluation.robot_id.value] = None if sid is None else sid.value
    return targets


# --------------------------------------------------------------------------
# Heuristics: composition (range, damage, height)
# --------------------------------------------------------------------------


def test_weapon_reach_is_the_longest_normal_weapon_plus_electronics() -> None:
    assert weapon_reach(_robot("a", 0, weapons=(C,)), RULES) == RULES.cannon_range_cells
    assert weapon_reach(_robot("a", 0, weapons=(C, M)), RULES) == RULES.missile_range_cells
    with_electronics = _robot("a", 0, weapons=(M,), electronics=ModuleIdentity.ELECTRONICS)
    assert weapon_reach(with_electronics, RULES) == (
        RULES.missile_range_cells + RULES.electronics_range_bonus_cells
    )
    assert weapon_reach(_robot("a", 0, weapons=(N,)), RULES) == 0


def test_hit_damage_uses_the_best_weapon_and_the_target_height() -> None:
    attacker = _robot("a", 0, weapons=(C, P))
    short = _robot("s", 0, chassis=ModuleIdentity.TRACKS, weapons=(C,))
    tall = _robot("t", 0, chassis=ModuleIdentity.BIPOD, weapons=(C, M, P))
    assert tall.height > short.height
    assert hit_damage(attacker, short, RULES) > hit_damage(attacker, tall, RULES) > 0
    # A robot below the bullet altitude is flown over on flat ground.
    below = dataclasses.replace(short, height=RULES.normal_projectile_altitude - 1)
    assert hit_damage(attacker, below, RULES) == 0
    assert hit_damage(_robot("n", 0, weapons=(N,)), short, RULES) == 0


def test_matchup_reach_decides_then_damage() -> None:
    cannon = _robot("c", 0, weapons=(C,))
    missile = _robot("m", 0, weapons=(M,))
    assert matchup(missile, cannon, RULES) == 1
    assert matchup(cannon, missile, RULES) == -1
    assert matchup(cannon, _robot("c2", 0, weapons=(C,)), RULES) == 0
    # Same reach (cannon 10, phaser 10): the phaser hits harder.
    phaser = _robot("p", 0, weapons=(P,))
    assert matchup(phaser, cannon, RULES) == 1
    # A robot that cannot hurt the enemy always loses.
    assert matchup(_robot("n", 0, weapons=(N,)), cannon, RULES) == -1


# --------------------------------------------------------------------------
# Heuristics: valuation, threat, orders
# --------------------------------------------------------------------------


def test_structure_value_ranks_war_bases_then_chassis_and_doubles_enemy_holdings() -> None:
    war_base, _ = _war_base("wb", 10, None)
    chassis, _ = _factory("f1", 10, FactoryType.CHASSIS)
    cannon, _ = _factory("f2", 10, FactoryType.CANNON)
    neutral = [structure_value(s, None, AI, RULES) for s in (war_base, chassis, cannon)]
    assert neutral[0] > neutral[1] > neutral[2] > 0
    assert structure_value(cannon, HUMAN, AI, RULES) == 2 * neutral[2]
    assert structure_value(war_base, AI, AI, RULES) == 0


def test_capture_score_prefers_near_and_uncontested() -> None:
    assert capture_score(10, 5, 0) > capture_score(10, 50, 0)
    assert capture_score(10, 5, 0) > capture_score(10, 5, 1)
    assert capture_score(20, 50, 0) > capture_score(10, 50, 0)
    assert capture_score(0, 0, 0) == 0


@pytest.mark.parametrize(
    ("distance", "previous", "closing"),
    [
        (THREAT_RADIUS_CELLS + 1, None, False),  # outside the radius
        (THREAT_RADIUS_CELLS, None, True),  # just arrived
        (10, 12, True),  # approaching
        (10, 10, False),  # holding off
        (10, 8, False),  # leaving
        (HOLD_RADIUS_CELLS, HOLD_RADIUS_CELLS, True),  # on the doorstep
    ],
)
def test_is_closing(distance: int, previous: int | None, closing: bool) -> None:
    assert is_closing(distance, previous) is closing


def test_approach_order_heads_for_the_column_and_holds_when_there() -> None:
    assert approach_order(10, 40) == Advance(15)
    assert approach_order(40, 11) == Retreat(15)  # 29 cells rounds up to 15 miles
    assert approach_order(40, 40 + HOLD_RADIUS_CELLS) == StopAndDefend()
    assert approach_order(0, 399) == Advance(MAX_ORDER_DISTANCE_MILES)


def test_same_order_ignores_engine_bound_fields() -> None:
    kind = SearchCaptureTarget.NEUTRAL_FACTORY
    assert same_order(SearchCapture(kind, EntityId("f1")), SearchCapture(kind))
    assert not same_order(SearchCapture(kind), SearchCapture(SearchCaptureTarget.ENEMY_FACTORY))
    assert same_order(Advance(3, target_x=40), Advance(9))
    assert not same_order(Advance(3), Retreat(3))
    assert not same_order(None, StopAndDefend())
    robot_hunt = SearchDestroy(SearchDestroyTarget.ROBOT)
    assert not same_order(robot_hunt, SearchDestroy(SearchDestroyTarget.WAR_BASE))


# --------------------------------------------------------------------------
# Capture valuation and the split between robots
# --------------------------------------------------------------------------


def test_two_neutral_factories_are_split_one_robot_each() -> None:
    """Near cheap cannon factory and far chassis factory: each robot takes one.

    The planner orders both robots onto neutral factories. The engine's
    exclusivity (CR003.2) then sends them to different factories, and they match
    the planner's intended split: r1 takes the cannon factory beside it, r2 the
    chassis factory. The third robot gets no capture order, because no neutral
    factory is left for it.
    """
    world = _world(
        _war_base("wb-ai", 390, AI),
        _factory("f-chassis", 100, FactoryType.CHASSIS),
        _factory("f-cannon", 150, FactoryType.CANNON),
    )
    state = _state(_robot("r1", 160), _robot("r2", 170), _robot("r3", 180))
    state, issued = _apply(state, world)
    neutral = SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)
    assert issued == {"r1": neutral, "r2": neutral}
    assert _capture_targets(state, world) == {"r1": "f-cannon", "r2": "f-chassis"}


@pytest.mark.parametrize(
    ("war_base_x", "expected"),
    [
        (100, SearchCaptureTarget.ENEMY_WAR_BASE),  # 60 cells: worth the walk
        (5, SearchCaptureTarget.NEUTRAL_FACTORY),  # 155 cells: the near factory wins
    ],
)
def test_a_neutral_war_base_outranks_a_near_factory_until_it_is_too_far(
    war_base_x: int, expected: SearchCaptureTarget
) -> None:
    world = _world(
        _war_base("wb-ai", 390, AI),
        _war_base("wb-neutral", war_base_x, None),
        _factory("f-chassis", 150, FactoryType.CHASSIS),
    )
    issued, _ = _plan(_state(_robot("r1", 160)), world)
    assert issued == {"r1": SearchCapture(expected)}


def test_both_capture_types_are_used_when_there_are_enough_robots() -> None:
    world = _world(
        _war_base("wb-ai", 390, AI),
        _war_base("wb-neutral", 100, None),
        _factory("f-chassis", 140, FactoryType.CHASSIS),
    )
    issued, _ = _plan(_state(_robot("r1", 160), _robot("r2", 161)), world)
    assert sorted(order.target.value for order in issued.values()) == [  # type: ignore[union-attr]
        SearchCaptureTarget.ENEMY_WAR_BASE.value,
        SearchCaptureTarget.NEUTRAL_FACTORY.value,
    ]


def test_a_target_guarded_by_a_stronger_enemy_is_devalued() -> None:
    """Contested: a missile robot beside the war base out-ranges the cannon robot."""
    world = _world(
        _war_base("wb-ai", 390, AI),
        _war_base("wb-neutral", 100, None),
        _factory("f-chassis", 150, FactoryType.CHASSIS),
    )
    guard = _robot("h1", 100, owner=HUMAN, weapons=(M,))
    uncontested, _ = _plan(_state(_robot("r1", 160)), world)
    contested, _ = _plan(_state(_robot("r1", 160), guard), world)
    assert uncontested == {"r1": SearchCapture(SearchCaptureTarget.ENEMY_WAR_BASE)}
    assert contested == {"r1": SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)}


def test_equal_claims_break_ties_by_robot_id() -> None:
    """Two robots equidistant from the only factory: the lower id takes it."""
    world = _world(_war_base("wb-ai", 390, AI), _factory("f1", 150, FactoryType.CANNON))
    state = _state(_robot("r-b", 160), _robot("r-a", 140))
    first, _ = _plan(state, world)
    again, _ = _plan(state, world)
    assert first == again
    assert first == {"r-a": SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)}


# --------------------------------------------------------------------------
# Destroyers (nuclear carriers)
# --------------------------------------------------------------------------


def test_a_nuclear_carrier_destroys_an_enemy_war_base() -> None:
    world = _world(_war_base("wb-ai", 390, AI), _war_base("wb-human", 300, HUMAN))
    issued, _ = _plan(_state(_robot("n1", 350, weapons=(N,))), world)
    assert issued == {"n1": SearchDestroy(SearchDestroyTarget.WAR_BASE)}


def test_a_nuclear_carrier_is_not_sent_to_blow_up_a_neutral_war_base() -> None:
    """The engine's nearest non-own war base is neutral here, so the nuke captures it."""
    world = _world(
        _war_base("wb-ai", 390, AI),
        _war_base("wb-neutral", 330, None),
        _war_base("wb-human", 10, HUMAN),
    )
    issued, _ = _plan(_state(_robot("n1", 350, weapons=(N,))), world)
    assert issued == {"n1": SearchCapture(SearchCaptureTarget.ENEMY_WAR_BASE)}


def test_two_carriers_do_not_nuke_the_same_building() -> None:
    world = _world(_war_base("wb-ai", 390, AI), _war_base("wb-human", 300, HUMAN))
    state = _state(_robot("n1", 350, weapons=(N,)), _robot("n2", 352, weapons=(N,)))
    issued, _ = _plan(state, world)
    assert issued["n1"] == SearchDestroy(SearchDestroyTarget.WAR_BASE)
    # The base is already being nuked, so capture skips it too: n2 holds.
    assert "n2" not in issued


# --------------------------------------------------------------------------
# No-target fallback: hunt, gated by the matchup
# --------------------------------------------------------------------------


def test_a_robot_with_no_capture_target_hunts_an_enemy_it_beats() -> None:
    world = _world(_war_base("wb-ai", 390, AI))
    state = _state(_robot("r1", 200, weapons=(M,)), _robot("h1", 100, owner=HUMAN, weapons=(C,)))
    issued, _ = _plan(state, world)
    assert issued == {"r1": SearchDestroy(SearchDestroyTarget.ROBOT)}


def test_a_robot_does_not_hunt_an_enemy_that_outranges_it() -> None:
    world = _world(_war_base("wb-ai", 390, AI))
    state = _state(_robot("r1", 200, weapons=(C,)), _robot("h1", 100, owner=HUMAN, weapons=(M,)))
    issued, _ = _plan(state, world)
    assert issued == {}  # already on Stop & Defend


def test_a_hunter_switches_to_capture_when_a_target_appears() -> None:
    world = _world(_war_base("wb-ai", 390, AI), _factory("f1", 150, FactoryType.CANNON, HUMAN))
    hunter = _robot("r1", 200, weapons=(M,), order=SearchDestroy(SearchDestroyTarget.ROBOT))
    state = _state(hunter, _robot("h1", 20, owner=HUMAN, weapons=(C,)))
    issued, _ = _plan(state, world)
    assert issued == {"r1": SearchCapture(SearchCaptureTarget.ENEMY_FACTORY)}


# --------------------------------------------------------------------------
# Defence
# --------------------------------------------------------------------------


def _defence_world() -> WorldMap:
    return _world(
        _war_base("wb-ai", 300, AI),
        _factory("f-ai", 200, FactoryType.CANNON, AI),
        _factory("f-far", 20, FactoryType.CANNON),
    )


def test_an_enemy_closing_on_the_war_base_draws_a_defender() -> None:
    world = _defence_world()
    intruder = _robot("h1", 280, owner=HUMAN, weapons=(C,))
    near = _robot("r1", 310, weapons=(C,))
    far = _robot("r2", 350, weapons=(C,))
    issued, memory = _plan(_state(near, far, intruder), world)
    assert issued["r1"] == SearchDestroy(SearchDestroyTarget.ROBOT)
    assert memory.orders.defences == (
        AiDefenceAssignment(EntityId("r1"), EntityId("h1"), EntityId("wb-ai")),
    )


def test_a_defender_that_would_chase_another_robot_moves_to_the_site_instead() -> None:
    """r1's nearest enemy is a decoy, so Search & Destroy would chase that one."""
    world = _defence_world()
    intruder = _robot("h1", 285, owner=HUMAN, weapons=(C,))
    decoy = _robot("h2", 345, owner=HUMAN, weapons=(C,))
    defender = _robot("r1", 340, weapons=(C,))
    issued, memory = _plan(_state(defender, intruder, decoy), world)
    assert issued["r1"] == Retreat(20)
    assert [(e.intruder_id.value, e.approached) for e in memory.orders.defences] == [("h1", True)]


def test_an_enemy_holding_off_is_not_a_threat_but_one_approaching_is() -> None:
    world = _defence_world()
    intruder = _robot("h1", 285, owner=HUMAN, weapons=(C,))
    robot = _robot("r1", 310, weapons=(C,))
    holding = AiOrderMemory(sightings=(AiSighting(EntityId("h1"), 15),))
    approaching = AiOrderMemory(sightings=(AiSighting(EntityId("h1"), 20),))
    _issued, calm = _plan(_state(robot, intruder, memory=holding), world)
    _issued, alarmed = _plan(_state(robot, intruder, memory=approaching), world)
    assert calm.orders.defences == ()
    assert len(alarmed.orders.defences) == 1
    assert calm.orders.sightings == (AiSighting(EntityId("h1"), 15),)


def test_a_far_enemy_is_ignored() -> None:
    world = _defence_world()
    intruder = _robot("h1", 250, owner=HUMAN, weapons=(C,))  # 50 cells from both sites
    _issued, memory = _plan(_state(_robot("r1", 310, weapons=(C,)), intruder), world)
    assert memory.orders.defences == ()


def test_a_losing_defender_is_sent_for_a_war_base_but_not_for_a_factory() -> None:
    world = _defence_world()
    defender = _robot("r1", 230, weapons=(C,))
    at_war_base = _robot("h1", 290, owner=HUMAN, weapons=(M,))
    at_factory = _robot("h1", 205, owner=HUMAN, weapons=(M,))
    _issued, base_memory = _plan(_state(defender, at_war_base), world)
    _issued, factory_memory = _plan(_state(defender, at_factory), world)
    assert len(base_memory.orders.defences) == 1
    assert factory_memory.orders.defences == ()


def test_a_robot_mid_capture_is_not_pulled_off_to_defend() -> None:
    world = _world(
        _war_base("wb-ai", 300, AI),
        _factory("f-human", 290, FactoryType.CANNON, HUMAN),
    )
    capturing = _robot(
        "r1",
        290,
        3,
        order=SearchCapture(SearchCaptureTarget.ENEMY_FACTORY, EntityId("f-human")),
    )
    intruder = _robot("h1", 285, owner=HUMAN, weapons=(C,))
    issued, memory = _plan(_state(capturing, intruder), world)
    assert issued == {}
    assert memory.orders.defences == ()


def test_the_defender_is_released_when_the_intruder_leaves() -> None:
    world = _defence_world()
    defender = _robot("r1", 310, weapons=(C,), order=SearchDestroy(SearchDestroyTarget.ROBOT))
    assignment = AiDefenceAssignment(EntityId("r1"), EntityId("h1"), EntityId("wb-ai"))
    gone = _robot("h1", 120, owner=HUMAN, weapons=(M,))  # retreated far away
    issued, memory = _plan(
        _state(defender, gone, memory=AiOrderMemory(defences=(assignment,))), world
    )
    assert memory.orders.defences == ()
    # Released, it goes back to work: the neutral factory is its capture target.
    assert issued == {"r1": SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)}


def test_a_standing_defender_is_kept_while_the_threat_lasts() -> None:
    world = _defence_world()
    defender = _robot("r1", 305, weapons=(C,), order=SearchDestroy(SearchDestroyTarget.ROBOT))
    assignment = AiDefenceAssignment(EntityId("r1"), EntityId("h1"), EntityId("wb-ai"))
    intruder = _robot("h1", 290, owner=HUMAN, weapons=(C,))
    issued, memory = _plan(
        _state(defender, intruder, memory=AiOrderMemory(defences=(assignment,))), world
    )
    assert issued == {}
    assert memory.orders.defences == (assignment,)


# --------------------------------------------------------------------------
# Stability, determinism, visibility, memory
# --------------------------------------------------------------------------


def test_orders_are_not_reissued_when_nothing_changed() -> None:
    world = _world(
        _war_base("wb-ai", 390, AI),
        _war_base("wb-neutral", 100, None),
        _factory("f-chassis", 100, FactoryType.CHASSIS),
        _factory("f-cannon", 150, FactoryType.CANNON),
    )
    enemy = _robot("h1", 10, owner=HUMAN, weapons=(C,))
    state = _state(_robot("r1", 160), _robot("r2", 170), _robot("r3", 180, weapons=(M,)), enemy)
    state, first = _apply(state, world)
    assert len(first) == 3
    _state_after, second = _apply(state, world)
    assert second == {}


def test_the_planner_does_not_read_the_opponents_resources_or_orders() -> None:
    world = _defence_world()
    robots = (_robot("r1", 310, weapons=(C,)), _robot("r2", 150, weapons=(M,)))
    enemy = _robot("h1", 280, owner=HUMAN, weapons=(C,))
    rich = PlayerResourcePool(player_id=HUMAN, general=99, nuclear=99)
    poor = PlayerResourcePool(player_id=HUMAN)
    ordered = dataclasses.replace(enemy, order=SearchCapture(SearchCaptureTarget.ENEMY_WAR_BASE))
    baseline = _plan(_state(*robots, enemy, pools=(poor,)), world)
    assert _plan(_state(*robots, enemy, pools=(rich,)), world) == baseline
    assert _plan(_state(*robots, ordered, pools=(poor,)), world) == baseline


def test_the_planner_draws_no_random_numbers() -> None:
    world = _defence_world()
    state = _state(_robot("r1", 310), _robot("h1", 280, owner=HUMAN))
    memory = state.ai_memory_for(AI)
    assert memory is not None
    a = robot_orders.plan(state, memory, world, RULES, MatchRandom(1))
    b = robot_orders.plan(state, memory, world, RULES, MatchRandom(2))
    assert a == b


def test_order_memory_round_trips_through_the_snapshot() -> None:
    memory = AiOrderMemory(
        defences=(AiDefenceAssignment(EntityId("r1"), EntityId("h1"), EntityId("wb-ai")),),
        sightings=(AiSighting(EntityId("h1"), 12), AiSighting(EntityId("h2"), 30)),
    )
    state = _state(memory=memory)
    entry = to_snapshot(state)["ai_memories"][0]
    assert entry["orders"]["defences"][0]["approached"] is False
    assert entry["orders"]["sightings"] == [
        {"robot_id": "h1", "distance": 12},
        {"robot_id": "h2", "distance": 30},
    ]
    assert ai_memory_from_snapshot(entry) == state.ai_memory_for(AI)


def test_order_memory_must_be_canonical() -> None:
    with pytest.raises(ValueError):
        AiOrderMemory(sightings=(AiSighting(EntityId("h2"), 1), AiSighting(EntityId("h1"), 1)))
    duplicate = AiDefenceAssignment(EntityId("r1"), EntityId("h1"), EntityId("wb"))
    with pytest.raises(ValueError):
        AiOrderMemory(defences=(duplicate, duplicate))


# --------------------------------------------------------------------------
# Through engine.step
# --------------------------------------------------------------------------


def _ai_orders(events: tuple[object, ...]) -> list[Command]:
    return [
        event.command
        for event in events
        if isinstance(event, CommandAccepted)
        and isinstance(event.command, SetRobotOrderCommand)
        and event.command.player == AI
    ]


def test_engine_run_orders_each_robot_once_and_captures() -> None:
    world = _world(
        _war_base("wb-ai", 390, AI),
        _factory("f-chassis", 150, FactoryType.CHASSIS),
        _factory("f-cannon", 170, FactoryType.CANNON),
    )
    state = _state(_robot("r1", 175, 8), _robot("r2", 185, 8))
    issued: list[Command] = []
    # Travel plus the 1,440-tick capture; the robots then hold their prizes.
    for _ in range(2400):
        state, events = engine.step(state, (), world=world)
        issued.extend(_ai_orders(events))
    assert sorted(c.entity_id.value for c in issued) == ["r1", "r2"]  # type: ignore[attr-defined]
    owners = {record.structure_id.value: record.owner for record in state.structure_ownership}
    assert owners == {"f-cannon": AI, "f-chassis": AI}


def test_engine_run_is_deterministic() -> None:
    world = _defence_world()

    def run() -> list[dict[str, object]]:
        state = _state(
            _robot("r1", 310, weapons=(C,)),
            _robot("r2", 250, weapons=(M,)),
            _robot("h1", 260, owner=HUMAN, weapons=(C,)),
        )
        snapshots = []
        for _ in range(60):
            state, _events = engine.step(state, (), world=world)
            snapshots.append(to_snapshot(state))
        return snapshots

    assert run() == run()


# --------------------------------------------------------------------------
# Fix round 1: no defence churn
# --------------------------------------------------------------------------


def test_an_unreachable_approach_is_not_reissued_every_decision() -> None:
    """Regression: an approach goal the engine proves unreachable falls back to
    Stop & Defend at once; the planner must not re-issue it every decision tick.

    The electronic defender r1's Retreat goal is (300, 1), beside the war base
    component at (300, 0). The decoy h2 is its nearest enemy, so Search &
    Destroy would chase the decoy and the planner approaches instead.
    """
    world = _defence_world()
    state = _state(
        _robot("r1", 340, 1, weapons=(C,), electronics=ModuleIdentity.ELECTRONICS),
        _robot("h1", 290, 8, owner=HUMAN, weapons=(C,)),
        _robot("h2", 336, 4, owner=HUMAN, weapons=(C,)),
    )
    issued: list[Command] = []
    for _ in range(80):
        state, events = engine.step(state, (), world=world)
        issued.extend(_ai_orders(events))
    to_r1 = [c for c in issued if c.entity_id.value == "r1"]  # type: ignore[attr-defined]
    assert len(to_r1) <= 2, [c.order for c in to_r1]  # type: ignore[attr-defined]
    memory = state.ai_memory_for(AI)
    assert memory is not None
    assert [entry.approached for entry in memory.orders.defences] == [True]


def test_a_standing_hunter_keeps_hunting_when_the_nearest_enemy_alternates() -> None:
    world = _defence_world()
    defender = _robot("r1", 340, weapons=(C,), order=SearchDestroy(SearchDestroyTarget.ROBOT))
    assignment = AiDefenceAssignment(EntityId("r1"), EntityId("h1"), EntityId("wb-ai"))
    intruder = _robot("h1", 290, owner=HUMAN, weapons=(C,))
    decoy = _robot("h2", 345, owner=HUMAN, weapons=(C,))  # now nearer to r1 than h1
    issued, memory = _plan(
        _state(defender, intruder, decoy, memory=AiOrderMemory(defences=(assignment,))), world
    )
    assert issued == {}
    assert memory.orders.defences == (assignment,)


def test_an_approach_is_issued_once_per_assignment() -> None:
    world = _defence_world()
    intruder = _robot("h1", 285, owner=HUMAN, weapons=(C,))
    decoy = _robot("h2", 345, owner=HUMAN, weapons=(C,))
    fallen_back = _robot("r1", 340, weapons=(C,))  # Stop & Defend after an approach
    assignment = AiDefenceAssignment(
        EntityId("r1"), EntityId("h1"), EntityId("wb-ai"), approached=True
    )
    issued, memory = _plan(
        _state(fallen_back, intruder, decoy, memory=AiOrderMemory(defences=(assignment,))),
        world,
    )
    assert issued == {}
    assert memory.orders.defences == (assignment,)
