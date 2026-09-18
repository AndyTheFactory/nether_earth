"""Tests for the factory/war-base capture subsystem (issue #66, M5.7)."""

from __future__ import annotations

from nether_earth.capture import (
    CapturableStructureKind,
    CaptureProgress,
    NeutralStructureAcquiredEvent,
    StructureCapturedEvent,
    StructureOwnership,
    advance_capture,
    effective_owner,
    effective_world,
)
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

FACTORY_ID = EntityId("factory-1")
WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")
FACTORY_CAPTURE_CELL = (5, 5)
WARBASE_CAPTURE_CELL = (2, 2)


def _world(
    *,
    factory_owner: PlayerId | None = None,
    war_base_capture: bool = False,
    extra_factory_capture_points: tuple[InteractionPoint, ...] = (),
) -> WorldMap:
    factories = (
        Factory(
            id=FACTORY_ID,
            components=(Component(x=6, y=6, height=3),),
            factory_type=FactoryType.CHASSIS,
            owner=factory_owner,
        ),
    )
    war_bases = (
        WarBase(id=WAR_BASE_ONE, components=(Component(x=0, y=0, height=3),), owner=PLAYER_ONE),
        WarBase(id=WAR_BASE_TWO, components=(Component(x=9, y=9, height=3),), owner=PLAYER_TWO),
    )
    interaction_points = (
        InteractionPoint(
            id="factory-1-capture",
            kind=InteractionKind.FACTORY_CAPTURE,
            structure_id=FACTORY_ID,
            footprint=Footprint(cells=frozenset({FACTORY_CAPTURE_CELL})),
        ),
        *extra_factory_capture_points,
    )
    if war_base_capture:
        interaction_points = (
            *interaction_points,
            InteractionPoint(
                id="warbase-1-capture",
                kind=InteractionKind.WARBASE_CAPTURE,
                structure_id=WAR_BASE_ONE,
                footprint=Footprint(cells=frozenset({WARBASE_CAPTURE_CELL})),
            ),
        )
    return WorldMap(
        map_id="capture-test-map",
        version=1,
        width=10,
        height=10,
        terrain=TerrainGrid(width=10, height=10, cells={}, default=TerrainType.NORMAL),
        war_bases=war_bases,
        factories=factories,
        blockers=(),
        interaction_points=interaction_points,
        spawn_positions={},
    )


def _robot(
    entity_id: str,
    owner: PlayerId,
    x: int,
    y: int,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(entity_id=EntityId(entity_id), owner=owner, x=x, y=y, build=build, stack=stack, height=height)


def _state(robots: tuple[Robot, ...] = (), **kwargs: object) -> GameState:
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=list(robots), **kwargs)  # type: ignore[arg-type]


# --- Neutral factory acquisition ---------------------------------------------


def test_neutral_factory_is_acquired_instantly_by_first_qualifying_robot() -> None:
    world = _world(factory_owner=None)
    robot = _robot("robot-p1-1", PLAYER_ONE, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    new_state, events = advance_capture(state, world, tick=1)

    assert new_state.structure_ownership_for(FACTORY_ID) == StructureOwnership(
        structure_id=FACTORY_ID, owner=PLAYER_ONE
    )
    assert new_state.capture_progress == ()
    assert events == (
        NeutralStructureAcquiredEvent(
            sequence=0,
            structure_id=FACTORY_ID,
            structure_kind=CapturableStructureKind.FACTORY,
            new_owner=PLAYER_ONE,
            robot_id=EntityId("robot-p1-1"),
            tick=1,
        ),
    )


def test_neutral_factory_with_no_robot_present_stays_neutral() -> None:
    world = _world(factory_owner=None)
    state = _state(())

    new_state, events = advance_capture(state, world, tick=1)

    assert new_state.structure_ownership == ()
    assert events == ()


def test_neutral_factory_second_tick_is_a_no_op_once_owned() -> None:
    world = _world(factory_owner=None)
    robot = _robot("robot-p1-1", PLAYER_ONE, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    state, _events = advance_capture(state, world, tick=1)
    state, events = advance_capture(state, world, tick=2)

    # Robot now shares ownership with the factory (its own player); no
    # further acquisition/capture activity -- own-structure occupation never
    # qualifies.
    assert events == ()
    assert state.structure_ownership_for(FACTORY_ID) == StructureOwnership(
        structure_id=FACTORY_ID, owner=PLAYER_ONE
    )


def test_neutral_factory_two_candidates_pick_deterministic_smallest_entity_id() -> None:
    robot_a = _robot("robot-p2-9", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    # Same cell cannot host two robots (ground occupancy), so use a
    # multi-cell footprint via a second capture point on a different cell.
    extra_point = InteractionPoint(
        id="factory-1-capture-2",
        kind=InteractionKind.FACTORY_CAPTURE,
        structure_id=FACTORY_ID,
        footprint=Footprint(cells=frozenset({(4, 4)})),
    )
    world = _world(factory_owner=None, extra_factory_capture_points=(extra_point,))
    robot_b = _robot("robot-p1-1", PLAYER_ONE, 4, 4)
    state = _state((robot_a, robot_b))

    _new_state, events = advance_capture(state, world, tick=1)

    assert len(events) == 1
    assert isinstance(events[0], NeutralStructureAcquiredEvent)
    # "robot-p1-1" < "robot-p2-9" lexicographically.
    assert events[0].robot_id == EntityId("robot-p1-1")
    assert events[0].new_owner == PLAYER_ONE


# --- Enemy factory continuous-occupation capture -----------------------------


def test_enemy_capture_progress_accrues_while_continuously_occupied() -> None:
    rules = EngineRules(capture_duration_ticks=3)
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    state, events_tick1 = advance_capture(state, world, tick=1, rules=rules)
    assert events_tick1 == ()
    progress = state.capture_progress_for(FACTORY_ID)
    assert progress == CaptureProgress(
        structure_id=FACTORY_ID,
        capturing_player=PLAYER_TWO,
        robot_id=EntityId("robot-p2-1"),
        elapsed_ticks=1,
        required_ticks=3,
    )

    state, events_tick2 = advance_capture(state, world, tick=2, rules=rules)
    assert events_tick2 == ()
    assert state.capture_progress_for(FACTORY_ID) is not None
    assert state.capture_progress_for(FACTORY_ID).elapsed_ticks == 2  # type: ignore[union-attr]

    # Ownership on WorldMap is still PLAYER_ONE; effective_owner must read
    # progress state, not completion, until the duration boundary.
    assert effective_owner(world, state, world.structure_by_id(FACTORY_ID)) == PLAYER_ONE  # type: ignore[arg-type]


def test_enemy_capture_completes_exactly_at_configured_duration_boundary() -> None:
    rules = EngineRules(capture_duration_ticks=3)
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    for tick in (1, 2):
        state, events = advance_capture(state, world, tick=tick, rules=rules)
        assert events == ()

    state, events = advance_capture(state, world, tick=3, rules=rules)

    assert events == (
        StructureCapturedEvent(
            sequence=0,
            structure_id=FACTORY_ID,
            structure_kind=CapturableStructureKind.FACTORY,
            previous_owner=PLAYER_ONE,
            new_owner=PLAYER_TWO,
            robot_id=EntityId("robot-p2-1"),
            tick=3,
        ),
    )
    assert state.capture_progress == ()
    assert state.structure_ownership_for(FACTORY_ID) == StructureOwnership(
        structure_id=FACTORY_ID, owner=PLAYER_TWO
    )


def test_enemy_capture_interruption_resets_progress_to_zero_immediately() -> None:
    rules = EngineRules(capture_duration_ticks=3)
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    state, _ = advance_capture(state, world, tick=1, rules=rules)
    assert state.capture_progress_for(FACTORY_ID) is not None

    # Robot leaves the capture cell -- occupation is interrupted.
    moved_robot = state.robot_for(EntityId("robot-p2-1")).with_position(0, 5)  # type: ignore[union-attr]
    state = state.with_robots((moved_robot,))

    state, events = advance_capture(state, world, tick=2, rules=rules)

    assert events == ()
    assert state.capture_progress_for(FACTORY_ID) is None
    # No partial credit: robot returns and must restart from elapsed_ticks=1.
    returned_robot = state.robot_for(EntityId("robot-p2-1")).with_position(  # type: ignore[union-attr]
        *FACTORY_CAPTURE_CELL
    )
    state = state.with_robots((returned_robot,))
    state, events = advance_capture(state, world, tick=3, rules=rules)
    assert events == ()
    progress = state.capture_progress_for(FACTORY_ID)
    assert progress is not None
    assert progress.elapsed_ticks == 1


def test_enemy_capture_interruption_when_different_robot_takes_over() -> None:
    """A different occupying robot restarts progress rather than continuing it."""
    rules = EngineRules(capture_duration_ticks=3)
    extra_point = InteractionPoint(
        id="factory-1-capture-2",
        kind=InteractionKind.FACTORY_CAPTURE,
        structure_id=FACTORY_ID,
        footprint=Footprint(cells=frozenset({(4, 4)})),
    )
    world = _world(factory_owner=PLAYER_ONE, extra_factory_capture_points=(extra_point,))
    robot_a = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _state((robot_a,))

    state, _ = advance_capture(state, world, tick=1, rules=rules)
    progress = state.capture_progress_for(FACTORY_ID)
    assert progress is not None and progress.elapsed_ticks == 1

    # robot_a leaves, robot_b (same player, different robot) takes over the
    # capture footprint on the very same tick.
    state = state.with_robots(
        (_robot("robot-p2-1", PLAYER_TWO, 0, 0), _robot("robot-p2-2", PLAYER_TWO, 4, 4))
    )
    state, events = advance_capture(state, world, tick=2, rules=rules)

    assert events == ()
    progress = state.capture_progress_for(FACTORY_ID)
    assert progress is not None
    assert progress.robot_id == EntityId("robot-p2-2")
    assert progress.elapsed_ticks == 1


def test_own_structure_occupation_never_qualifies() -> None:
    rules = EngineRules(capture_duration_ticks=3)
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p1-1", PLAYER_ONE, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    state, events = advance_capture(state, world, tick=1, rules=rules)

    assert events == ()
    assert state.capture_progress == ()
    assert state.structure_ownership == ()


# --- War-base capture ---------------------------------------------------------


def test_war_base_capture_uses_warbase_capture_interaction_point() -> None:
    rules = EngineRules(capture_duration_ticks=2)
    world = _world(factory_owner=PLAYER_ONE, war_base_capture=True)
    robot = _robot("robot-p2-1", PLAYER_TWO, *WARBASE_CAPTURE_CELL)
    state = _state((robot,))

    state, events_tick1 = advance_capture(state, world, tick=1, rules=rules)
    assert events_tick1 == ()
    state, events_tick2 = advance_capture(state, world, tick=2, rules=rules)

    assert events_tick2 == (
        StructureCapturedEvent(
            sequence=0,
            structure_id=WAR_BASE_ONE,
            structure_kind=CapturableStructureKind.WAR_BASE,
            previous_owner=PLAYER_ONE,
            new_owner=PLAYER_TWO,
            robot_id=EntityId("robot-p2-1"),
            tick=2,
        ),
    )


def test_war_base_with_no_capture_interaction_point_is_not_capturable() -> None:
    world = _world(factory_owner=PLAYER_ONE, war_base_capture=False)
    robot = _robot("robot-p2-1", PLAYER_TWO, *WARBASE_CAPTURE_CELL)
    state = _state((robot,))

    _new_state, events = advance_capture(state, world, tick=1)

    # No WARBASE_CAPTURE point declared: robot standing at that cell (which
    # is not even the factory's cell) has no effect on war-base ownership.
    assert not any(
        isinstance(event, StructureCapturedEvent) and event.structure_id in (WAR_BASE_ONE, WAR_BASE_TWO)
        for event in events
    )


# --- effective_world / effective_owner ---------------------------------------


def test_effective_world_is_identity_when_no_overrides() -> None:
    world = _world(factory_owner=None)
    state = _state(())
    assert effective_world(world, state) is world


def test_effective_world_layers_overrides_over_base_map() -> None:
    world = _world(factory_owner=None)
    state = _state(structure_ownership=[StructureOwnership(structure_id=FACTORY_ID, owner=PLAYER_TWO)])

    updated = effective_world(world, state)

    factory = updated.structure_by_id(FACTORY_ID)
    assert factory is not None
    assert factory.owner == PLAYER_TWO  # type: ignore[union-attr]
    # Base world itself is untouched.
    assert world.structure_by_id(FACTORY_ID).owner is None  # type: ignore[union-attr]


# --- Determinism / replay -----------------------------------------------------


def test_advance_capture_is_deterministic_given_same_inputs() -> None:
    rules = EngineRules(capture_duration_ticks=3)
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))

    def run() -> tuple[GameState, tuple[object, ...]]:
        s = state
        all_events: list[object] = []
        for tick in (1, 2, 3):
            s, events = advance_capture(s, world, tick=tick, rules=rules)
            all_events.extend(events)
        return s, tuple(all_events)

    state_a, events_a = run()
    state_b, events_b = run()

    assert state_a == state_b
    assert events_a == events_b


def test_sequencer_is_used_when_supplied() -> None:
    world = _world(factory_owner=None)
    robot = _robot("robot-p1-1", PLAYER_ONE, *FACTORY_CAPTURE_CELL)
    state = _state((robot,))
    sequencer = EventSequencer(start=5)

    _new_state, events = advance_capture(state, world, tick=1, sequencer=sequencer)

    assert events[0].sequence == 5
