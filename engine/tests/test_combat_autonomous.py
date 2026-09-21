"""Tests for consuming M5 engagement intent for autonomous firing (issue #77, M6.7)."""

from __future__ import annotations

from dataclasses import replace

from nether_earth.autonomous_combat import (
    consume_engagement_intent,
    consume_engagement_intents,
)
from nether_earth.combat import FireRequest, ProjectileFiredEvent, apply_fire
from nether_earth.destruction import RobotDestroyedEvent, StructureDestroyedEvent
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.orders import (
    EngagementIntent,
    EngagementTargetKind,
    OrderEvaluation,
    OrderStatus,
    StopAndDefend,
)
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

# --------------------------------------------------------------------------
# Fixtures (mirroring test_combat_fire.py / test_combat_nuclear.py)
# --------------------------------------------------------------------------


def _world(
    width: int = 60,
    height: int = 60,
    war_bases: tuple[WarBase, ...] = (),
    factories: tuple[Factory, ...] = (),
) -> WorldMap:
    return WorldMap(
        map_id="combat-autonomous-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(width=width, height=height, cells={}, default=TerrainType.NORMAL),
        war_bases=war_bases,
        factories=factories,
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str = "robot-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 0,
    y: int = 0,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    electronics: ModuleIdentity | None = None,
    active_projectile_id: EntityId | None = None,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons, electronics=electronics)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        active_projectile_id=active_projectile_id,
    )


def _factory(entity_id: str, x: int, y: int, owner: PlayerId | None = None) -> Factory:
    return Factory(
        id=EntityId(entity_id),
        components=(Component(x=x, y=y, height=13),),
        factory_type=FactoryType.CHASSIS,
        owner=owner,
    )


def _war_base(entity_id: str, x: int, y: int, owner: PlayerId | None = None) -> WarBase:
    return WarBase(
        id=EntityId(entity_id),
        components=(Component(x=x, y=y, height=13),),
        owner=owner,
    )


def _state(
    robots: tuple[Robot, ...] = (),
    structure_destruction: tuple[EntityId, ...] = (),
) -> GameState:
    return create_game_state(
        0,
        (PLAYER_ONE, PLAYER_TWO),
        robots=list(robots),
        structure_destruction=list(structure_destruction),
    )


def _intent(
    robot: Robot,
    target_kind: EngagementTargetKind = EngagementTargetKind.ROBOT,
    target_id: EntityId | None = None,
    target_x: int = 5,
    target_y: int = 0,
    distance_cells: int | None = None,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
) -> EngagementIntent:
    if distance_cells is None:
        distance_cells = abs(robot.x - target_x) + abs(robot.y - target_y)
    if target_id is None:
        target_id = EntityId("target-1")
    return EngagementIntent(
        robot_id=robot.entity_id,
        player=robot.owner,
        target_kind=target_kind,
        target_id=target_id,
        target_x=target_x,
        target_y=target_y,
        distance_cells=distance_cells,
        weapons=weapons,
    )


def _evaluation(robot: Robot, intent: EngagementIntent | None) -> OrderEvaluation:
    return OrderEvaluation(
        robot_id=robot.entity_id,
        order=StopAndDefend(),
        previous=StopAndDefend(),
        status=OrderStatus.ACTIVE,
        intent=intent,
    )


# --------------------------------------------------------------------------
# Firing success cases
# --------------------------------------------------------------------------


def test_stop_and_defend_style_intent_fires_when_in_range_with_free_channel() -> None:
    """Stop & Defend produces a ROBOT-target intent; in range, fires."""
    robot = _robot(x=0, y=0, weapons=(ModuleIdentity.CANNON,))
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=5, y=0)
    state = _state((robot, target))
    world = _world()
    intent = _intent(robot, target_id=target.entity_id, target_x=5, target_y=0)

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    fired = [e for e in events if isinstance(e, ProjectileFiredEvent)]
    assert len(fired) == 1
    assert fired[0].source_robot_id == robot.entity_id
    assert new_state.robot_for(robot.entity_id).active_projectile_id is not None


def test_search_and_destroy_style_intent_against_robot_fires() -> None:
    robot = _robot(x=0, y=0, weapons=(ModuleIdentity.MISSILE,))
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=0, y=10)
    state = _state((robot, target))
    world = _world()
    intent = _intent(
        robot,
        target_id=target.entity_id,
        target_x=0,
        target_y=10,
        weapons=(ModuleIdentity.MISSILE,),
    )

    _new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    fired = [e for e in events if isinstance(e, ProjectileFiredEvent)]
    assert len(fired) == 1
    assert fired[0].weapon is ModuleIdentity.MISSILE


def test_search_and_destroy_style_intent_against_structure_with_nuclear_detonates() -> None:
    """A Search & Destroy intent against a factory, carried by a nuclear robot.

    Asserts the detonation actually happened (structure destroyed, carrier
    destroyed): this reuses `destruction.py`'s own already-tested
    execute_nuclear_detonation behavior; this test only checks that this
    module's code correctly triggers it.
    """
    robot = _robot(entity_id="carrier", x=10, y=10, weapons=(ModuleIdentity.NUCLEAR,))
    factory = _factory("factory-1", x=12, y=10, owner=PLAYER_TWO)
    state = _state((robot,))
    # The carrier stands on its target cell, the factory's capture cell
    # (OQ §19), which is also its blast anchor (OQ §20): dx = 0,
    # dy = |10 + 1 - 10| = 1, in range.
    world = replace(
        _world(factories=(factory,)),
        interaction_points=(
            InteractionPoint(
                id="factory-1-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=factory.id,
                footprint=Footprint(cells=frozenset({(10, 10)})),
            ),
        ),
    )
    intent = _intent(
        robot,
        target_kind=EngagementTargetKind.FACTORY,
        target_id=factory.id,
        target_x=10,
        target_y=10,
        weapons=(ModuleIdentity.NUCLEAR,),
    )

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state.robot_for(robot.entity_id) is None
    assert new_state.structure_destroyed(factory.id)
    assert any(isinstance(e, RobotDestroyedEvent) for e in events)
    assert any(isinstance(e, StructureDestroyedEvent) for e in events)


# --------------------------------------------------------------------------
# No-fire cases
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Autonomous nuclear use (OQ §19, CR001.1)
# --------------------------------------------------------------------------


def test_structure_intent_before_arrival_never_detonates() -> None:
    """A structure intent one cell short of the target cell does nothing."""
    robot = _robot(entity_id="carrier", x=10, y=10, weapons=(ModuleIdentity.NUCLEAR,))
    factory = _factory("factory-1", x=12, y=10, owner=PLAYER_TWO)
    state = _state((robot,))
    world = _world(factories=(factory,))
    intent = _intent(
        robot,
        target_kind=EngagementTargetKind.FACTORY,
        target_id=factory.id,
        target_x=11,
        target_y=10,
        weapons=(ModuleIdentity.NUCLEAR,),
    )
    assert intent.distance_cells == 1

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


def test_robot_intent_never_selects_nuclear_even_adjacent() -> None:
    """A nuclear-only carrier with a hostile robot next to it never detonates."""
    robot = _robot(entity_id="carrier", x=10, y=10, weapons=(ModuleIdentity.NUCLEAR,))
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=11, y=10)
    state = _state((robot, target))
    intent = _intent(
        robot, target_id=target.entity_id, target_x=11, target_y=10, weapons=(ModuleIdentity.NUCLEAR,)
    )

    new_state, events = consume_engagement_intent(intent, state, _world(), tick=4)

    assert new_state is state
    assert events == ()


def test_robot_intent_out_of_normal_range_does_not_fall_through_to_nuclear() -> None:
    far = DEFAULT_RULES.cannon_range_cells + 1
    robot = _robot(
        entity_id="carrier", x=0, y=0, weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR)
    )
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=far, y=0)
    state = _state((robot, target))
    intent = _intent(
        robot,
        target_id=target.entity_id,
        target_x=far,
        target_y=0,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
    )

    new_state, events = consume_engagement_intent(intent, state, _world(), tick=4)

    assert new_state is state
    assert events == ()


def test_robot_intent_with_busy_channel_does_not_fall_through_to_nuclear() -> None:
    robot = _robot(
        entity_id="carrier",
        x=0,
        y=0,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        active_projectile_id=EntityId("projectile-1"),
    )
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=5, y=0)
    state = _state((robot, target))
    intent = _intent(
        robot,
        target_id=target.entity_id,
        target_x=5,
        target_y=0,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
    )

    new_state, events = consume_engagement_intent(intent, state, _world(), tick=4)

    assert new_state is state
    assert events == ()


def test_target_robot_destroyed_mid_tick_produces_no_fire_identity() -> None:
    robot = _robot(x=0, y=0)
    state = _state((robot,))  # target robot absent -- destroyed mid-tick
    world = _world()
    intent = _intent(robot, target_id=EntityId("ghost-target"), target_x=5, target_y=0)

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


def test_target_structure_destroyed_mid_tick_produces_no_fire_identity() -> None:
    robot = _robot(entity_id="carrier", x=10, y=10, weapons=(ModuleIdentity.NUCLEAR,))
    factory = _factory("factory-1", x=12, y=10, owner=PLAYER_TWO)
    state = _state((robot,), structure_destruction=(factory.id,))
    world = _world(factories=(factory,))
    intent = _intent(
        robot,
        target_kind=EngagementTargetKind.FACTORY,
        target_id=factory.id,
        target_x=12,
        target_y=10,
        weapons=(ModuleIdentity.NUCLEAR,),
    )

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


def test_out_of_range_intent_produces_no_fire() -> None:
    robot = _robot(x=0, y=0, weapons=(ModuleIdentity.CANNON,))
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=DEFAULT_RULES.cannon_range_cells + 1, y=0)
    state = _state((robot, target))
    world = _world()
    intent = _intent(
        robot,
        target_id=target.entity_id,
        target_x=target.x,
        target_y=0,
    )
    assert intent.distance_cells > DEFAULT_RULES.cannon_range_cells

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


def test_channel_occupied_intent_produces_no_fire_via_natural_rejection() -> None:
    robot = _robot(
        x=0, y=0, weapons=(ModuleIdentity.CANNON,), active_projectile_id=EntityId("projectile-1")
    )
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=5, y=0)
    state = _state((robot, target))
    world = _world()
    intent = _intent(robot, target_id=target.entity_id, target_x=5, target_y=0)

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


def test_no_weapon_in_range_across_multiple_weapons_produces_no_fire() -> None:
    robot = _robot(x=0, y=0, weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE))
    far = DEFAULT_RULES.missile_range_cells + 1
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=far, y=0)
    state = _state((robot, target))
    world = _world()
    intent = _intent(
        robot,
        target_id=target.entity_id,
        target_x=far,
        target_y=0,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE),
    )

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


# --------------------------------------------------------------------------
# Electronics range bonus
# --------------------------------------------------------------------------


def test_electronics_range_bonus_extends_eligibility() -> None:
    distance = DEFAULT_RULES.cannon_range_cells + DEFAULT_RULES.electronics_range_bonus_cells
    robot_with_electronics = _robot(
        entity_id="robot-electronics",
        x=0,
        y=0,
        weapons=(ModuleIdentity.CANNON,),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=distance, y=0)
    state = _state((robot_with_electronics, target))
    world = _world()
    intent = _intent(
        robot_with_electronics, target_id=target.entity_id, target_x=distance, target_y=0
    )

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert any(isinstance(e, ProjectileFiredEvent) for e in events)
    assert new_state is not state


def test_same_distance_without_electronics_does_not_fire() -> None:
    distance = DEFAULT_RULES.cannon_range_cells + DEFAULT_RULES.electronics_range_bonus_cells
    robot_without_electronics = _robot(
        entity_id="robot-plain", x=0, y=0, weapons=(ModuleIdentity.CANNON,), electronics=None
    )
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=distance, y=0)
    state = _state((robot_without_electronics, target))
    world = _world()
    intent = _intent(
        robot_without_electronics, target_id=target.entity_id, target_x=distance, target_y=0
    )

    new_state, events = consume_engagement_intent(intent, state, world, tick=4)

    assert new_state is state
    assert events == ()


# --------------------------------------------------------------------------
# Shared code path (no autonomous-vs-direct branching)
# --------------------------------------------------------------------------


def test_autonomous_and_direct_fire_share_the_identical_code_path() -> None:
    """Proof by construction, not inspection.

    An equivalent direct-control FireRequest and an autonomous-path
    EngagementIntent, naming the same effective robot/weapon/target, are
    fired through combat.apply_fire directly (direct control) and through
    consume_engagement_intent (autonomous) respectively, against separately
    constructed but equivalent starting states. If either code path
    performed a hidden "is this autonomous" branch that changed legality or
    outcome shape, the two FireResult/event shapes below would diverge; they
    do not, because consume_engagement_intent constructs the very same
    FireRequest and calls the very same apply_fire function combat.py
    already exposes for direct control -- there is only one fire-execution
    entry point for normal weapons, by construction. (The only difference is
    the evidence-backed ``autonomous=True`` fire-cycle timing flag, CR002.2
    #169, which changes the projectile's ``first_advance_tick``, not the
    fire result or event shape.)
    """
    direct_robot = _robot(entity_id="direct-robot", x=0, y=0, weapons=(ModuleIdentity.CANNON,))
    direct_state = _state((direct_robot,))
    world = _world()
    direct_request = FireRequest(
        robot_id=direct_robot.entity_id,
        player=direct_robot.owner,
        weapon=ModuleIdentity.CANNON,
        target_x=5,
        target_y=0,
    )
    direct_new_state, direct_result, direct_events = apply_fire(
        direct_request, direct_state, world, tick=4
    )

    auto_robot = _robot(entity_id="direct-robot", x=0, y=0, weapons=(ModuleIdentity.CANNON,))
    auto_target = _robot(entity_id="some-target", owner=PLAYER_TWO, x=5, y=0)
    auto_state = _state((auto_robot, auto_target))
    auto_intent = _intent(
        auto_robot, target_id=auto_target.entity_id, target_x=5, target_y=0
    )
    auto_new_state, auto_events = consume_engagement_intent(auto_intent, auto_state, world, tick=4)

    assert direct_result.accepted
    assert len(direct_events) == 1
    direct_event = direct_events[0]
    assert len(auto_events) == 1
    auto_event = auto_events[0]

    assert type(direct_event) is type(auto_event)
    assert isinstance(direct_event, ProjectileFiredEvent)
    assert isinstance(auto_event, ProjectileFiredEvent)
    assert direct_event.source_robot_id == auto_event.source_robot_id
    assert direct_event.owner == auto_event.owner
    assert direct_event.weapon == auto_event.weapon
    assert direct_event.x == auto_event.x
    assert direct_event.y == auto_event.y
    assert direct_event.dx == auto_event.dx
    assert direct_event.dy == auto_event.dy

    direct_fired_robot = direct_new_state.robot_for(direct_robot.entity_id)
    auto_fired_robot = auto_new_state.robot_for(auto_robot.entity_id)
    assert direct_fired_robot.active_projectile_id is not None
    assert auto_fired_robot.active_projectile_id is not None


# --------------------------------------------------------------------------
# consume_engagement_intents (batch)
# --------------------------------------------------------------------------


def test_consume_engagement_intents_skips_none_intents_and_threads_state() -> None:
    robot_a = _robot(entity_id="robot-a", x=0, y=0, weapons=(ModuleIdentity.CANNON,))
    robot_b = _robot(entity_id="robot-b", x=0, y=20, weapons=(ModuleIdentity.CANNON,))
    target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=5, y=0)
    state = _state((robot_a, robot_b, target))
    world = _world()

    intent_a = _intent(robot_a, target_id=target.entity_id, target_x=5, target_y=0)
    evaluations = (
        _evaluation(robot_a, intent_a),
        _evaluation(robot_b, None),
    )

    new_state, events = consume_engagement_intents(evaluations, state, world, tick=4)

    fired = [e for e in events if isinstance(e, ProjectileFiredEvent)]
    assert len(fired) == 1
    assert fired[0].source_robot_id == robot_a.entity_id
    assert new_state.robot_for(robot_a.entity_id).active_projectile_id is not None
    assert new_state.robot_for(robot_b.entity_id).active_projectile_id is None


def test_consume_engagement_intents_is_deterministic_across_repeated_calls() -> None:
    """Replay reproduces the exact same autonomous fire/no-fire decision.

    Combat is not yet wired into engine.step (deferred to Task 10), so
    this is demonstrated via direct repeated-call determinism rather than
    replay.run_fixture, matching the precedent already used in Task 6's
    and Task 8's own tests.
    """

    def run() -> tuple[GameState, list[str]]:
        robot = _robot(entity_id="carrier", x=10, y=10, weapons=(ModuleIdentity.NUCLEAR,))
        other = _robot(entity_id="other", owner=PLAYER_TWO, x=0, y=0, weapons=(ModuleIdentity.CANNON,))
        target = _robot(entity_id="target-1", owner=PLAYER_TWO, x=0, y=5)
        factory = _factory("factory-1", x=12, y=10, owner=PLAYER_TWO)
        state = _state((robot, other, target))
        world = _world(factories=(factory,))
        sequencer = EventSequencer()

        intent_nuke = _intent(
            robot,
            target_kind=EngagementTargetKind.FACTORY,
            target_id=factory.id,
            target_x=10,
            target_y=10,
            weapons=(ModuleIdentity.NUCLEAR,),
        )
        intent_cannon = _intent(other, target_id=target.entity_id, target_x=0, target_y=5)
        evaluations = (
            _evaluation(robot, intent_nuke),
            _evaluation(other, intent_cannon),
        )

        final_state, events = consume_engagement_intents(
            evaluations, state, world, tick=4, sequencer=sequencer
        )
        return final_state, [repr(e) for e in events]

    state_a, log_a = run()
    state_b, log_b = run()

    assert state_a == state_b
    assert log_a == log_b
