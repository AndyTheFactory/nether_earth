"""``engine.step`` integration for autonomous robot orders (issue #64, M5.5).

Where `test_orders.py` tests the order subsystem in isolation, this module
tests the Step 2b2 wiring: that orders reach the tick's *single* deconflicted
move batch, that their lifecycle events land in the ordered event stream, and
that an ordered fleet behaves identically across replays.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from nether_earth.capture import (
    StructureCapturedEvent,
    StructureOwnership,
)
from nether_earth.combat import ProjectileFiredEvent
from nether_earth.commander import Commander, CommanderMode
from nether_earth.destruction import RobotDestroyedEvent, StructureDestroyedEvent
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.engine import step
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.movement import RobotMoveCompletedEvent, RobotMoveStartedEvent
from nether_earth.orders import (
    Advance,
    OrderStatus,
    RobotEngagementIntentEvent,
    RobotOrderChangedEvent,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
)
from nether_earth.reservations import DestinationContentionResolvedEvent
from nether_earth.robot import Robot, RobotFacing
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, miles_to_cells
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

TRACKS_TICKS = DEFAULT_RULES.robot_move_ticks_tracks_normal
# Every capture -- neutral factories included, since the owner decision of
# 2026-09-23 removed instant neutral acquisition -- costs this many ticks
# of continuous occupation, so a walk-then-capture run must budget for it.
CAPTURE_TICKS = DEFAULT_RULES.capture_duration_ticks

NEUTRAL_FACTORY = EntityId("factory-neutral")
NEUTRAL_FACTORY_CAPTURE_CELL = (6, 5)
OWN_WAR_BASE = EntityId("warbase-p1")
SECOND_FACTORY = EntityId("factory-second")
SECOND_FACTORY_CAPTURE_CELL = (16, 5)


def _world(width: int = 30, height: int = 12) -> WorldMap:
    return WorldMap(
        map_id="orders-integration-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(width=width, height=height, cells={}, default=TerrainType.NORMAL),
        war_bases=(
            WarBase(
                id=OWN_WAR_BASE,
                components=(Component(x=0, y=0, height=3),),
                owner=PLAYER_ONE,
            ),
        ),
        factories=(
            Factory(
                id=NEUTRAL_FACTORY,
                components=(Component(x=6, y=6, height=3),),
                factory_type=FactoryType.CHASSIS,
                owner=None,
            ),
        ),
        blockers=(),
        interaction_points=(
            InteractionPoint(
                id="neutral-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=NEUTRAL_FACTORY,
                footprint=Footprint(cells=frozenset({NEUTRAL_FACTORY_CAPTURE_CELL})),
            ),
        ),
        spawn_positions={},
    )


def _robot(
    entity_id: str = "robot-a",
    owner: PlayerId = PLAYER_ONE,
    x: int = 2,
    y: int = 5,
    *,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    order: object = None,
    facing: RobotFacing = RobotFacing.EAST,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=weapons)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        order=order,  # type: ignore[arg-type],
        facing=facing,
    )


def _state(
    robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
    seed: int = 7,
) -> GameState:
    return create_game_state(
        0, (PLAYER_ONE, PLAYER_TWO), seed=seed, robots=list(robots), commanders=list(commanders)
    )


def _run(state: GameState, world: WorldMap, ticks: int) -> tuple[GameState, list[Event]]:
    """Advance ``ticks`` plain ticks, accumulating every emitted event."""
    collected: list[Event] = []
    for _ in range(ticks):
        state, events = step(state, (), world)
        collected.extend(events)
    return state, collected


def _of(events: list[Event], kind: type) -> list[Event]:
    return [event for event in events if isinstance(event, kind)]


# --------------------------------------------------------------------------
# Order assignment through the command stream
# --------------------------------------------------------------------------


def test_set_robot_order_command_assigns_and_then_drives_the_robot() -> None:
    world = _world()
    robot = _robot(x=0, y=5)
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=robot.entity_id, order=Advance(2)
    )

    state, events = step(state, (command,), world)

    stored = state.robots[0].order
    assert isinstance(stored, Advance)
    # Assigned PENDING and bound ACTIVE in the same tick.
    assert stored.target_x == miles_to_cells(2)
    statuses = [event.status for event in _of(events, RobotOrderChangedEvent)]
    assert statuses == [OrderStatus.PENDING, OrderStatus.ACTIVE]
    # And the very same tick already started the first step of the advance.
    assert len(_of(events, RobotMoveStartedEvent)) == 1


def test_set_robot_order_command_for_an_enemy_robot_is_a_no_op() -> None:
    world = _world()
    robot = _robot(owner=PLAYER_TWO)
    state = _state((robot,))
    command = SetRobotOrderCommand(
        player=PLAYER_ONE, sequence=0, entity_id=robot.entity_id, order=Advance(2)
    )
    state, events = step(state, (command,), world)
    assert state.robots[0].order is None
    assert _of(events, RobotOrderChangedEvent) == []


# --------------------------------------------------------------------------
# Advance/Retreat lifecycle across real ticks
# --------------------------------------------------------------------------


def test_an_advance_walks_the_locked_number_of_cells_then_stops_and_defends() -> None:
    """Advance 3 miles = 6 cells east, then the locked Stop & Defend transition."""
    world = _world()
    robot = _robot(x=0, y=5, order=Advance(3))
    state = _state((robot,))

    # One extra tick so the final arrival evaluation runs after the last move.
    state, events = _run(state, world, TRACKS_TICKS * 6 + 2)

    assert (state.robots[0].x, state.robots[0].y) == (miles_to_cells(3), 5)
    assert state.robots[0].order == StopAndDefend()
    completions = [
        event
        for event in _of(events, RobotOrderChangedEvent)
        if event.status is OrderStatus.COMPLETED
    ]
    assert len(completions) == 1
    assert completions[0].order == StopAndDefend()


def test_an_advance_moves_exactly_one_cell_per_move_duration() -> None:
    world = _world()
    robot = _robot(x=0, y=5, order=Advance(5))
    state = _state((robot,))
    # The first move starts on tick 1, so one cell lands on tick 1 + duration.
    state, _events = _run(state, world, TRACKS_TICKS)
    assert state.robots[0].x == 0
    state, _events = _run(state, world, 1)
    assert state.robots[0].x == 1
    state, _events = _run(state, world, TRACKS_TICKS)
    assert state.robots[0].x == 2


def test_an_impossible_advance_falls_back_on_the_first_evaluated_tick() -> None:
    world = _world(width=10)
    robot = _robot(x=8, y=5, order=Advance(5))  # the easternmost 2×2 anchor column
    state = _state((robot,))
    state, events = step(state, (), world)
    assert state.robots[0].order == StopAndDefend()
    changes = _of(events, RobotOrderChangedEvent)
    assert [event.status for event in changes] == [OrderStatus.FALLBACK]
    assert _of(events, RobotMoveStartedEvent) == []


# --------------------------------------------------------------------------
# Search & Capture end to end, including capture.py taking over
# --------------------------------------------------------------------------


def _two_factory_world() -> WorldMap:
    """``_world`` plus a second neutral factory ten cells further east."""
    world = _world()
    return replace(
        world,
        factories=(
            *world.factories,
            Factory(
                id=SECOND_FACTORY,
                components=(Component(x=16, y=6, height=3),),
                factory_type=FactoryType.CANNON,
                owner=None,
            ),
        ),
        interaction_points=(
            *world.interaction_points,
            InteractionPoint(
                id="second-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=SECOND_FACTORY,
                footprint=Footprint(cells=frozenset({SECOND_FACTORY_CAPTURE_CELL})),
            ),
        ),
    )


def _owner(state: GameState, structure_id: EntityId) -> PlayerId | None:
    record = state.structure_ownership_for(structure_id)
    return record.owner if record is not None else None


def test_search_capture_walks_to_the_footprint_and_capture_completes_there() -> None:
    world = _world()
    robot = _robot(x=0, y=5, order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY))
    state = _state((robot,))

    state, events = _run(state, world, TRACKS_TICKS * 6 + 2)

    assert (state.robots[0].x, state.robots[0].y) == NEUTRAL_FACTORY_CAPTURE_CELL
    # Reaching the footprint only starts the countdown now; the robot holds
    # the cell while it runs.
    assert _owner(state, NEUTRAL_FACTORY) is None
    state, more = _run(state, world, CAPTURE_TICKS)
    events += more

    # CR003.2: the order persists; with nothing neutral left the robot idles.
    assert state.robots[0].order == SearchCapture(
        SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=NEUTRAL_FACTORY
    )
    assert _owner(state, NEUTRAL_FACTORY) == PLAYER_ONE
    statuses = {event.status for event in _of(events, RobotOrderChangedEvent)}  # type: ignore[attr-defined]
    assert statuses == {OrderStatus.ACTIVE}


def test_one_robot_captures_two_neutral_factories_in_sequence() -> None:
    """CR003.2: after each capture the order retargets and the robot leaves."""
    world = _two_factory_world()
    robot = _robot(x=0, y=5, order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY))
    state = _state((robot,))

    state, events = _run(state, world, TRACKS_TICKS * 6 + 2 + CAPTURE_TICKS)
    assert _owner(state, NEUTRAL_FACTORY) == PLAYER_ONE
    assert _owner(state, SECOND_FACTORY) is None

    state, more = _run(state, world, TRACKS_TICKS * 10 + 2 + CAPTURE_TICKS)
    events += more
    assert (state.robots[0].x, state.robots[0].y) == SECOND_FACTORY_CAPTURE_CELL
    assert _owner(state, SECOND_FACTORY) == PLAYER_ONE
    assert state.robots[0].order == SearchCapture(
        SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=SECOND_FACTORY
    )
    targets = [
        event.order.structure_id  # type: ignore[attr-defined]
        for event in _of(events, RobotOrderChangedEvent)
    ]
    assert targets == [NEUTRAL_FACTORY, SECOND_FACTORY]
    captured = [
        event.structure_id  # type: ignore[attr-defined]
        for event in _of(events, StructureCapturedEvent)
    ]
    assert captured == [NEUTRAL_FACTORY, SECOND_FACTORY]


def test_two_robots_with_the_same_capture_order_split_two_factories() -> None:
    """Lb36c: a structure another same-order robot targets is skipped."""
    world = _two_factory_world()
    order = SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)
    first = _robot("robot-a", x=0, y=5, order=order)
    second = _robot("robot-b", x=0, y=8, order=order)
    state = _state((first, second))

    # Both are nearer NEUTRAL_FACTORY; robot-a (canonical first) takes it on
    # the very first tick, and robot-b sees that claim in the same tick.
    state, _events = step(state, (), world)
    assert [robot.order for robot in state.robots] == [
        SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=NEUTRAL_FACTORY),
        SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY, structure_id=SECOND_FACTORY),
    ]

    state, _events = _run(state, world, TRACKS_TICKS * 30 + CAPTURE_TICKS)
    assert _owner(state, NEUTRAL_FACTORY) == PLAYER_ONE
    assert _owner(state, SECOND_FACTORY) == PLAYER_ONE
    assert (state.robots[0].x, state.robots[0].y) == NEUTRAL_FACTORY_CAPTURE_CELL
    assert (state.robots[1].x, state.robots[1].y) == SECOND_FACTORY_CAPTURE_CELL


def test_search_capture_with_no_target_idles_and_resumes_when_a_factory_changes_hands() -> None:
    """No target keeps the order and holds; a factory lost to the enemy revives it."""
    world = _world()
    order = SearchCapture(SearchCaptureTarget.ENEMY_FACTORY)
    robot = _robot(x=0, y=5, order=order)
    enemy = _robot("robot-enemy", PLAYER_TWO, x=20, y=10)
    state = _state((robot, enemy))

    state, events = _run(state, world, TRACKS_TICKS * 3)
    assert state.robots[0].order == order
    assert (state.robots[0].x, state.robots[0].y) == (0, 5)
    assert _of(events, RobotOrderChangedEvent) == []
    assert _of(events, RobotMoveStartedEvent) == []
    intents = [
        event.intent.robot_id  # type: ignore[attr-defined]
        for event in _of(events, RobotEngagementIntentEvent)
    ]
    assert robot.entity_id in intents

    state = state.with_structure_ownership(
        (StructureOwnership(structure_id=NEUTRAL_FACTORY, owner=PLAYER_TWO),)
    )
    state, events = step(state, (), world)
    assert state.robots[0].order == SearchCapture(
        SearchCaptureTarget.ENEMY_FACTORY, structure_id=NEUTRAL_FACTORY
    )
    started = [
        event.entity_id  # type: ignore[attr-defined]
        for event in _of(events, RobotMoveStartedEvent)
    ]
    assert robot.entity_id in started


# --------------------------------------------------------------------------
# Engagement intent in the event stream
# --------------------------------------------------------------------------


def test_stop_and_defend_emits_engagement_intent_and_fires_through_step() -> None:
    world = _world()
    defender = _robot("robot-a", x=2, y=5, order=StopAndDefend())
    # Six cells away: the fire-tick move (2 cells) cannot reach the enemy's
    # body yet, so the projectile is still in flight after this step.
    enemy = _robot("robot-z", PLAYER_TWO, x=8, y=5)
    state = _state((defender, enemy))

    state, events = step(state, (), world)

    intents = _of(events, RobotEngagementIntentEvent)
    assert len(intents) == 1
    intent = intents[0].intent  # type: ignore[attr-defined]
    assert intent.robot_id == defender.entity_id
    assert intent.target_id == enemy.entity_id
    assert intent.distance_cells == 6

    # M5 produced intent only; M6.10's Step 2c2 now consumes that same
    # intent in the same authoritative step, so the defender fires. Nothing
    # has moved and nothing is destroyed yet -- the projectile still has to
    # travel (see ``test_engine_combat_integration.py``).
    assert _of(events, ProjectileFiredEvent) != []
    assert len(state.robots) == 2
    assert state.robot_for(defender.entity_id).x == 2  # type: ignore[union-attr]
    assert state.robot_for(defender.entity_id).active_projectile_id is not None  # type: ignore[union-attr]
    assert state.robot_for(enemy.entity_id).strength == 100  # type: ignore[union-attr]
    assert len(state.projectiles) == 1


def test_search_destroy_closes_on_its_target_every_tick_it_has_one() -> None:
    world = _world()
    hunter = _robot("robot-a", x=0, y=5, order=SearchDestroy(SearchDestroyTarget.ROBOT))
    # Out of cannon range throughout: a robot with a shot on its update fires
    # instead of moving (CR002.19, #197; see test_autonomous_fire_update.py).
    enemy = _robot("robot-z", PLAYER_TWO, x=20, y=5)
    state = _state((hunter, enemy))

    state, events = _run(state, world, TRACKS_TICKS * 3 + 1)

    assert state.robots[0].x == 3
    assert state.robots[0].order == SearchDestroy(SearchDestroyTarget.ROBOT)
    intents = _of(events, RobotEngagementIntentEvent)
    assert intents  # produced continuously while a target exists
    assert all(event.intent.target_id == enemy.entity_id for event in intents)  # type: ignore[attr-defined]


def _with_electronics(robot: Robot) -> Robot:
    build = replace(robot.build, electronics=ModuleIdentity.ELECTRONICS)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return replace(robot, build=build, stack=stack, height=height)


@pytest.mark.parametrize("electronics", [False, True], ids=["greedy", "electronic"])
@pytest.mark.parametrize("enemy_x", [8, 20])
def test_search_destroy_robot_hunter_closes_damages_and_destroys_its_target(
    electronics: bool, enemy_x: int
) -> None:
    """CR003.4 regression (`_specs/milestones/cr003-playtest-fixes.md`, evidence 6).

    An electronic hunter used to plan to the target's own (occupied) anchor,
    get ``UNREACHABLE`` and drop to Stop & Defend on the first tick.
    """
    world = _world()
    hunter = _robot("robot-a", x=0, y=5, order=SearchDestroy(SearchDestroyTarget.ROBOT))
    if electronics:
        hunter = _with_electronics(hunter)
    enemy = _robot("robot-z", PLAYER_TWO, x=enemy_x, y=5)
    state = _state((hunter, enemy))

    damaged = False
    destroyed_at: int | None = None
    for tick in range(600):
        state, events = step(state, (), world)
        target = state.robot_for(enemy.entity_id)
        if target is None:
            destroyed_at = tick
            assert _of(events, RobotDestroyedEvent)
            break
        damaged = damaged or target.strength < 100
        # The order holds while a hostile robot exists.
        assert state.robot_for(hunter.entity_id).order == SearchDestroy(  # type: ignore[union-attr]
            SearchDestroyTarget.ROBOT
        )

    assert damaged
    assert destroyed_at is not None
    final = state.robot_for(hunter.entity_id)
    assert final is not None
    if enemy_x == 20:
        assert final.x > 0  # it had to close the distance to get in range


def test_search_destroy_of_a_structure_without_a_nuke_falls_back() -> None:
    world = _world()
    hunter = _robot(
        "robot-a",
        x=0,
        y=5,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.PHASER),
        order=SearchDestroy(SearchDestroyTarget.FACTORY),
    )
    state = _state((hunter,))
    state, events = step(state, (), world)
    assert state.robots[0].order == StopAndDefend()
    assert _of(events, RobotEngagementIntentEvent) == []


def _run_search_destroy_factory() -> tuple[int, GameState, list[tuple[int, Event]]]:
    """Drive a nuclear carrier on Search & Destroy (factory) until it detonates.

    Returns the detonation tick, the state after it, and every event tagged
    with the tick it was emitted on.
    """
    world = _world()
    hunter = _robot(
        "robot-a",
        x=0,
        y=5,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        order=SearchDestroy(SearchDestroyTarget.FACTORY),
    )
    state = _state((hunter,))
    tagged: list[tuple[int, Event]] = []
    for _ in range(TRACKS_TICKS * 10):
        state, events = step(state, (), world)
        tagged.extend((state.tick, event) for event in events)
        if state.robot_for(hunter.entity_id) is None:
            return state.tick, state, tagged
    raise AssertionError("the carrier never detonated")


def test_search_destroy_of_a_structure_detonates_exactly_on_arrival() -> None:
    """OQ §19: the carrier walks to the capture cell and detonates there, never earlier."""
    detonation_tick, state, tagged = _run_search_destroy_factory()

    # No structure intent, and nothing destroyed, on any earlier tick.
    before = [event for tick, event in tagged if tick < detonation_tick]
    assert _of(before, RobotEngagementIntentEvent) == []
    assert _of(before, RobotDestroyedEvent) == []
    assert _of(before, StructureDestroyedEvent) == []

    # The move onto the target cell completes in the detonation tick itself
    # (Step 2b), before orders are evaluated: detonation is on arrival.
    arrivals = [
        tick
        for tick, event in tagged
        if isinstance(event, RobotMoveCompletedEvent)
        and (event.x, event.y) == NEUTRAL_FACTORY_CAPTURE_CELL
    ]
    assert arrivals == [detonation_tick]

    at_detonation = [event for tick, event in tagged if tick == detonation_tick]
    completions = [
        event
        for event in _of(at_detonation, RobotOrderChangedEvent)
        if event.status is OrderStatus.COMPLETED  # type: ignore[attr-defined]
    ]
    assert len(completions) == 1
    intents = _of(at_detonation, RobotEngagementIntentEvent)
    assert len(intents) == 1
    assert intents[0].intent.target_id == NEUTRAL_FACTORY  # type: ignore[attr-defined]
    assert intents[0].intent.distance_cells == 0  # type: ignore[attr-defined]
    assert intents[0].intent.weapons == (ModuleIdentity.NUCLEAR,)  # type: ignore[attr-defined]
    assert state.structure_destroyed(NEUTRAL_FACTORY)
    assert _of(at_detonation, StructureCapturedEvent) == []


def test_search_destroy_of_a_structure_detonation_is_replay_identical() -> None:
    first = _run_search_destroy_factory()
    second = _run_search_destroy_factory()
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert [repr(event) for _, event in first[2]] == [repr(event) for _, event in second[2]]


def test_stop_and_defend_nuclear_carrier_never_detonates() -> None:
    """OQ §19: an enemy robot far out of normal-weapon range never triggers the nuke."""
    world = _world()
    carrier = _robot(
        "robot-a",
        x=0,
        y=11,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        order=StopAndDefend(),
    )
    enemy = _robot("robot-z", PLAYER_TWO, x=29, y=0)
    state = _state((carrier, enemy))

    state, events = _run(state, world, TRACKS_TICKS * 5)

    assert _of(events, RobotEngagementIntentEvent) != []  # it does keep an intent
    assert state.robot_for(carrier.entity_id) is not None
    assert state.robot_for(enemy.entity_id) is not None
    assert _of(events, RobotDestroyedEvent) == []
    assert _of(events, StructureDestroyedEvent) == []


def test_stop_and_defend_nuclear_carrier_with_busy_channel_never_detonates() -> None:
    """In range but with its projectile in flight, the carrier waits instead of nuking."""
    world = _world()
    carrier = _robot(
        "robot-a",
        x=2,
        y=5,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        order=StopAndDefend(),
    )
    enemy = _robot("robot-z", PLAYER_TWO, x=5, y=5)
    state = _state((carrier, enemy))

    state, events = _run(state, world, 3)

    assert len(_of(events, ProjectileFiredEvent)) == 1
    assert state.robot_for(carrier.entity_id) is not None
    assert _of(events, RobotDestroyedEvent) == []
    assert _of(events, StructureDestroyedEvent) == []


def test_search_destroy_robots_nuclear_carrier_uses_normal_weapons_only() -> None:
    world = _world()
    hunter = _robot(
        "robot-a",
        x=0,
        y=5,
        weapons=(ModuleIdentity.NUCLEAR,),
        order=SearchDestroy(SearchDestroyTarget.ROBOT),
    )
    enemy = _robot("robot-z", PLAYER_TWO, x=9, y=5)
    state = _state((hunter, enemy))

    state, events = _run(state, world, TRACKS_TICKS * 10)

    assert _of(events, RobotEngagementIntentEvent) != []
    assert state.robot_for(hunter.entity_id) is not None
    assert state.robot_for(enemy.entity_id) is not None
    assert _of(events, RobotDestroyedEvent) == []
    assert _of(events, ProjectileFiredEvent) == []


# --------------------------------------------------------------------------
# One shared movement path: contention, direct control, determinism
# --------------------------------------------------------------------------


def test_autonomous_and_direct_control_moves_share_one_contention_batch() -> None:
    """Both sources contend for one cell in the same seeded draw (§11)."""
    world = _world()
    # 2×2 bodies (CR002.3): robot-a advances east into the body (4..5, 2..3);
    # robot-b is driven north into (5..6, 3..4). The bodies overlap at (5, 3).
    autonomous = _robot("robot-a", x=3, y=3, order=Advance(1))
    # The driven robot is pushed north, so it must already face north: a robot
    # turned the wrong way spends the tick rotating and never enters the
    # contention batch this test is about.
    driven = _robot("robot-b", x=5, y=5, facing=RobotFacing.NORTH)
    commander = Commander(
        player_id=PLAYER_ONE,
        x=5,
        y=5,
        altitude=0,
        mode=CommanderMode.DOCKED,
        docked_robot_id=driven.entity_id,
    )
    state = _state((autonomous, driven), commanders=(commander,))

    _state_after, events = step(state, (DirectRobotMoveCommand(PLAYER_ONE, 0, dx=0, dy=-1),), world)

    contentions = _of(events, DestinationContentionResolvedEvent)
    assert len(contentions) == 1
    assert (contentions[0].x, contentions[0].y) == (4, 3)  # type: ignore[attr-defined]
    assert len(_of(events, RobotMoveStartedEvent)) == 1


def test_a_directly_driven_robot_ignores_its_own_standing_order() -> None:
    world = _world()
    robot = _robot("robot-a", x=5, y=5, order=Advance(5))
    commander = Commander(
        player_id=PLAYER_ONE,
        x=5,
        y=5,
        altitude=0,
        mode=CommanderMode.DOCKED,
        docked_robot_id=robot.entity_id,
    )
    state = _state((robot,), commanders=(commander,))

    state, _events = _run(state, world, TRACKS_TICKS + 1)

    # The order was never evaluated, so its goal is still unbound and the
    # robot has not moved east on its own.
    assert state.robots[0].order == Advance(5)
    assert state.robots[0].x == 5


def test_an_ordered_fleet_replays_identically_from_the_same_seed() -> None:
    world = _world()

    def _fleet() -> GameState:
        return _state(
            (
                _robot("robot-a", x=0, y=2, order=Advance(2)),
                _robot(
                    "robot-b", x=0, y=5, order=SearchCapture(SearchCaptureTarget.NEUTRAL_FACTORY)
                ),
                _robot("robot-c", x=0, y=8, order=SearchDestroy(SearchDestroyTarget.ROBOT)),
                _robot("robot-z", PLAYER_TWO, x=12, y=6, order=StopAndDefend()),
            ),
            seed=1234,
        )

    first_state, first_events = _run(_fleet(), world, TRACKS_TICKS * 4)
    second_state, second_events = _run(_fleet(), world, TRACKS_TICKS * 4)

    assert first_state == second_state
    assert first_events == second_events


def test_order_evaluation_is_independent_of_robot_declaration_order() -> None:
    world = _world()
    robots = (
        _robot("robot-a", x=0, y=2, order=Advance(2)),
        _robot("robot-b", x=0, y=5, order=Advance(2)),
        _robot("robot-c", x=0, y=8, order=Advance(2)),
    )
    forward, forward_events = _run(_state(robots), world, TRACKS_TICKS * 2)
    backward, backward_events = _run(_state(tuple(reversed(robots))), world, TRACKS_TICKS * 2)
    assert forward == backward
    assert forward_events == backward_events


def test_a_world_less_step_never_evaluates_orders() -> None:
    """``world is None`` keeps every pre-M5 call site's behavior unchanged."""
    robot = _robot(x=0, y=5, order=Advance(3))
    state = _state((robot,))
    state, events = step(state, ())
    assert state.robots[0].order == Advance(3)
    assert _of(events, RobotOrderChangedEvent) == []
    assert _of(events, RobotEngagementIntentEvent) == []


def test_robots_without_orders_are_untouched_by_the_order_step() -> None:
    world = _world()
    robot = _robot(x=0, y=5)
    state = _state((robot,))
    before = state.robots
    state, events = step(state, (), world)
    assert state.robots == before
    assert _of(events, RobotOrderChangedEvent) == []
    assert _of(events, RobotEngagementIntentEvent) == []
