"""Tests for the shared robot movement executor (issue #60, M5.1)."""

from __future__ import annotations

import pytest

from nether_earth.commander import Commander, CommanderMode
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.movement import (
    CHASSIS_TERRAIN_PERMISSIONS,
    MovementRejectionReason,
    RobotMoveCancelledEvent,
    RobotMoveCompletedEvent,
    RobotMoveRequest,
    RobotMoveResult,
    RobotMoveStartedEvent,
    advance_all_robot_transitions,
    advance_robot_transition,
    apply_robot_move,
    cancel_robot_move,
    chassis_can_enter,
    folded_robot_occupancy,
    move_duration_ticks,
    robot_move_duration_ticks,
    validate_robot_move,
)
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType

CHASSIS = (ModuleIdentity.BIPOD, ModuleIdentity.TRACKS, ModuleIdentity.ANTI_GRAV)


def _world(
    width: int = 10,
    height: int = 10,
    terrain_cells: dict[tuple[int, int], TerrainType] | None = None,
    blockers: tuple[Blocker, ...] = (),
) -> WorldMap:
    return WorldMap(
        map_id="movement-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width,
            height=height,
            cells=terrain_cells or {},
            default=TerrainType.NORMAL,
        ),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str = "robot-player-one-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 5,
    y: int = 5,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    electronics: ModuleIdentity | None = None,
    movement: RobotMoveTransition | None = None,
) -> Robot:
    build = RobotBuild(
        chassis=chassis, weapons=(ModuleIdentity.CANNON,), electronics=electronics
    )
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        movement=movement,
    )


def _state(
    robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
    tick: int = 0,
) -> GameState:
    return create_game_state(
        tick,
        (PLAYER_ONE, PLAYER_TWO),
        robots=list(robots),
        commanders=list(commanders),
    )


def _commander(
    player_id: PlayerId = PLAYER_TWO, x: int = 6, y: int = 5, altitude: int = 0
) -> Commander:
    return Commander(
        player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude
    )


def _east(entity_id: str = "robot-player-one-1") -> RobotMoveRequest:
    return RobotMoveRequest(entity_id=EntityId(entity_id), dx=1, dy=0)


# --- Terrain capability (locked rules) ---------------------------------------


def test_chassis_terrain_permissions_match_locked_rules_exactly() -> None:
    assert CHASSIS_TERRAIN_PERMISSIONS == {
        ModuleIdentity.BIPOD: frozenset({TerrainType.NORMAL, TerrainType.ROUGH}),
        ModuleIdentity.TRACKS: frozenset({TerrainType.NORMAL, TerrainType.ROUGH}),
        ModuleIdentity.ANTI_GRAV: frozenset(
            {TerrainType.NORMAL, TerrainType.ROUGH, TerrainType.DITCH}
        ),
    }


@pytest.mark.parametrize(
    ("chassis", "terrain", "allowed"),
    [
        (ModuleIdentity.BIPOD, TerrainType.NORMAL, True),
        (ModuleIdentity.BIPOD, TerrainType.ROUGH, True),
        (ModuleIdentity.BIPOD, TerrainType.DITCH, False),
        (ModuleIdentity.TRACKS, TerrainType.NORMAL, True),
        (ModuleIdentity.TRACKS, TerrainType.ROUGH, True),
        (ModuleIdentity.TRACKS, TerrainType.DITCH, False),
        (ModuleIdentity.ANTI_GRAV, TerrainType.NORMAL, True),
        (ModuleIdentity.ANTI_GRAV, TerrainType.ROUGH, True),
        (ModuleIdentity.ANTI_GRAV, TerrainType.DITCH, True),
    ],
)
def test_chassis_can_enter_covers_every_chassis_terrain_pair(
    chassis: ModuleIdentity, terrain: TerrainType, allowed: bool
) -> None:
    assert chassis_can_enter(chassis, terrain) is allowed


def test_chassis_can_enter_rejects_non_chassis_module() -> None:
    with pytest.raises(ValueError):
        chassis_can_enter(ModuleIdentity.CANNON, TerrainType.NORMAL)


def test_electronics_does_not_change_terrain_permissions() -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.DITCH})
    for chassis in (ModuleIdentity.BIPOD, ModuleIdentity.TRACKS):
        robot = _robot(chassis=chassis, electronics=ModuleIdentity.ELECTRONICS)
        result = validate_robot_move(_east(), _state((robot,)), world)
        assert result.reason is MovementRejectionReason.TERRAIN_IMPASSABLE


# --- Movement duration (centralized configuration) ----------------------------


def test_ordinary_terrain_speed_order_is_bipod_slower_than_tracks_than_anti_grav() -> None:
    bipod = move_duration_ticks(ModuleIdentity.BIPOD, TerrainType.NORMAL)
    tracks = move_duration_ticks(ModuleIdentity.TRACKS, TerrainType.NORMAL)
    anti_grav = move_duration_ticks(ModuleIdentity.ANTI_GRAV, TerrainType.NORMAL)

    assert bipod > tracks > anti_grav


def test_rough_terrain_penalizes_bipod_more_than_tracks() -> None:
    bipod_penalty = move_duration_ticks(
        ModuleIdentity.BIPOD, TerrainType.ROUGH
    ) - move_duration_ticks(ModuleIdentity.BIPOD, TerrainType.NORMAL)
    tracks_penalty = move_duration_ticks(
        ModuleIdentity.TRACKS, TerrainType.ROUGH
    ) - move_duration_ticks(ModuleIdentity.TRACKS, TerrainType.NORMAL)

    assert bipod_penalty > tracks_penalty > 0


@pytest.mark.parametrize("chassis", [ModuleIdentity.BIPOD, ModuleIdentity.TRACKS])
def test_move_duration_rejects_terrain_the_chassis_cannot_enter(
    chassis: ModuleIdentity,
) -> None:
    with pytest.raises(ValueError):
        move_duration_ticks(chassis, TerrainType.DITCH)


def test_move_duration_comes_from_rules_not_literals() -> None:
    rules = EngineRules(
        robot_move_ticks_bipod=5,
        robot_rough_multiplier_bipod=4,
    )

    assert move_duration_ticks(ModuleIdentity.BIPOD, TerrainType.NORMAL, rules) == 5
    assert move_duration_ticks(ModuleIdentity.BIPOD, TerrainType.ROUGH, rules) == 20


def test_robot_move_duration_ticks_uses_the_robots_own_chassis() -> None:
    robot = _robot(chassis=ModuleIdentity.ANTI_GRAV)

    assert robot_move_duration_ticks(robot, TerrainType.DITCH) == move_duration_ticks(
        ModuleIdentity.ANTI_GRAV, TerrainType.DITCH
    )


# --- Request shape ------------------------------------------------------------


def test_move_request_rejects_diagonal_and_no_op_shapes() -> None:
    with pytest.raises(ValueError):
        RobotMoveRequest(entity_id=EntityId("r"), dx=1, dy=1)
    with pytest.raises(ValueError):
        RobotMoveRequest(entity_id=EntityId("r"), dx=0, dy=0)
    with pytest.raises(ValueError):
        RobotMoveRequest(entity_id=EntityId("r"), dx=2, dy=0)


def test_move_result_enforces_accept_reject_invariant() -> None:
    with pytest.raises(ValueError):
        RobotMoveResult(request=_east(), accepted=True, reason=MovementRejectionReason.OCCUPIED)
    with pytest.raises(ValueError):
        RobotMoveResult(request=_east(), accepted=False, reason=None)


# --- Validation ---------------------------------------------------------------


def test_valid_move_on_normal_terrain_is_accepted() -> None:
    result = validate_robot_move(_east(), _state((_robot(),)), _world())

    assert result.accepted
    assert result.reason is None


def test_unknown_robot_is_rejected() -> None:
    result = validate_robot_move(_east("robot-nobody"), _state((_robot(),)), _world())

    assert result.reason is MovementRejectionReason.NO_SUCH_ROBOT


def test_second_move_while_one_is_in_progress_is_rejected() -> None:
    robot = _robot()
    moving = robot.with_movement(
        RobotMoveTransition(
            entity_id=robot.entity_id,
            from_x=5,
            from_y=5,
            to_x=6,
            to_y=5,
            started_tick=0,
            duration_ticks=8,
        )
    )

    result = validate_robot_move(_east(), _state((moving,)), _world())

    assert result.reason is MovementRejectionReason.MOVE_IN_PROGRESS


@pytest.mark.parametrize(
    ("dx", "dy", "x", "y"),
    [(1, 0, 9, 5), (-1, 0, 0, 5), (0, 1, 5, 9), (0, -1, 5, 0)],
)
def test_move_off_the_battlefield_is_rejected(dx: int, dy: int, x: int, y: int) -> None:
    robot = _robot(x=x, y=y)
    request = RobotMoveRequest(entity_id=robot.entity_id, dx=dx, dy=dy)

    result = validate_robot_move(request, _state((robot,)), _world())

    assert result.reason is MovementRejectionReason.OUT_OF_BOUNDS


@pytest.mark.parametrize("chassis", [ModuleIdentity.BIPOD, ModuleIdentity.TRACKS])
def test_ground_chassis_cannot_enter_a_ditch(chassis: ModuleIdentity) -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.DITCH})

    result = validate_robot_move(_east(), _state((_robot(chassis=chassis),)), world)

    assert result.reason is MovementRejectionReason.TERRAIN_IMPASSABLE


def test_anti_grav_may_enter_a_ditch() -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.DITCH})
    robot = _robot(chassis=ModuleIdentity.ANTI_GRAV)

    assert validate_robot_move(_east(), _state((robot,)), world).accepted


@pytest.mark.parametrize("chassis", CHASSIS)
def test_every_chassis_may_enter_rough_terrain(chassis: ModuleIdentity) -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.ROUGH})

    assert validate_robot_move(_east(), _state((_robot(chassis=chassis),)), world).accepted


def test_destination_occupied_by_a_structure_is_rejected() -> None:
    world = _world(
        blockers=(
            Blocker(id=EntityId("blocker-1"), components=(Component(x=6, y=5, height=4),)),
        )
    )

    result = validate_robot_move(_east(), _state((_robot(),)), world)

    assert result.reason is MovementRejectionReason.OCCUPIED


def test_destination_occupied_by_another_robot_is_rejected() -> None:
    robots = (_robot(), _robot(entity_id="robot-player-two-1", owner=PLAYER_TWO, x=6, y=5))

    result = validate_robot_move(_east(), _state(robots), _world())

    assert result.reason is MovementRejectionReason.OCCUPIED


def test_occupancy_check_uses_the_shared_m2_fold() -> None:
    robots = (_robot(), _robot(entity_id="robot-player-two-1", owner=PLAYER_TWO, x=6, y=5))
    grid = folded_robot_occupancy(_world(), _state(robots))

    assert grid.occupant_at(6, 5) == EntityId("robot-player-two-1")
    assert grid.occupant_at(5, 5) == EntityId("robot-player-one-1")


def test_commander_overlapping_the_destination_blocks_the_move() -> None:
    result = validate_robot_move(
        _east(), _state((_robot(),), commanders=(_commander(altitude=0),)), _world()
    )

    assert result.reason is MovementRejectionReason.COMMANDER_BLOCKED


def test_commander_hovering_clear_of_the_robot_does_not_block() -> None:
    robot = _robot()
    high_commander = _commander(altitude=robot.height)

    result = validate_robot_move(
        _east(), _state((robot,), commanders=(high_commander,)), _world()
    )

    assert result.accepted


def test_commander_elsewhere_does_not_block() -> None:
    result = validate_robot_move(
        _east(), _state((_robot(),), commanders=(_commander(x=8, y=8),)), _world()
    )

    assert result.accepted


def test_commander_blocking_uses_the_rules_passed_in_not_the_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``validate_robot_move``'s ``rules`` must reach the M3 query.

    A commander's blocking volume is
    ``[altitude, altitude + rules.commander_height)``, so the M3
    commander-blocking query is rules-dependent and
    :func:`~nether_earth.collision.commander_horizontal_move_allowed`
    forwards ``rules`` into the identical call. Robot movement previously
    omitted it, silently evaluating every caller against ``DEFAULT_RULES``.

    Asserted on the call itself rather than on an outcome: with both
    volumes ground-rooted, a commander at altitude ``a`` overlaps a robot's
    ``[0, height)`` exactly when ``a < height``, independently of
    ``commander_height``, so no current fixture can distinguish the two
    rule sets by result alone. The guarantee under test is that the
    caller's rule set is what the query is evaluated against -- which is
    what must not silently regress if commander geometry ever gains a
    rules-dependent term.
    """
    import nether_earth.movement as movement_module

    seen: list[EngineRules] = []
    real_query = movement_module.commander_blocks_cell

    def spy(
        state: GameState,
        commander: Commander,
        x: int,
        y: int,
        vertical_range: object,
        *,
        rules: EngineRules = DEFAULT_RULES,
    ) -> bool:
        seen.append(rules)
        return real_query(state, commander, x, y, vertical_range, rules=rules)  # type: ignore[arg-type]

    monkeypatch.setattr(movement_module, "commander_blocks_cell", spy)
    custom_rules = EngineRules(commander_height=17)

    validate_robot_move(
        _east(),
        _state((_robot(),), commanders=(_commander(x=8, y=8),)),
        _world(),
        custom_rules,
    )

    assert seen == [custom_rules]


def test_destination_availability_hook_can_reject_the_move() -> None:
    def unavailable(state: GameState, robot: Robot, dest_x: int, dest_y: int) -> bool:
        return not (dest_x == 6 and dest_y == 5)

    result = validate_robot_move(
        _east(), _state((_robot(),)), _world(), DEFAULT_RULES, unavailable
    )

    assert result.reason is MovementRejectionReason.DESTINATION_UNAVAILABLE


# --- Execution ----------------------------------------------------------------


def test_accepted_move_starts_a_transition_without_moving_the_robot() -> None:
    state = _state((_robot(),))

    new_state, result, event = apply_robot_move(_east(), state, _world(), tick=10)

    assert result.accepted
    robot = new_state.robot_for(EntityId("robot-player-one-1"))
    assert robot is not None
    assert (robot.x, robot.y) == (5, 5)
    assert robot.movement == RobotMoveTransition(
        entity_id=robot.entity_id,
        from_x=5,
        from_y=5,
        to_x=6,
        to_y=5,
        started_tick=10,
        duration_ticks=DEFAULT_RULES.robot_move_ticks_bipod,
    )
    assert isinstance(event, RobotMoveStartedEvent)
    assert (event.from_x, event.to_x, event.duration_ticks) == (
        5,
        6,
        DEFAULT_RULES.robot_move_ticks_bipod,
    )


def test_move_into_rough_terrain_uses_the_rough_duration() -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.ROUGH})

    new_state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), world, tick=0)

    robot = new_state.robot_for(EntityId("robot-player-one-1"))
    assert robot is not None
    assert robot.movement is not None
    assert robot.movement.duration_ticks == move_duration_ticks(
        ModuleIdentity.BIPOD, TerrainType.ROUGH
    )


def test_rejected_move_causes_no_partial_state_mutation() -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.DITCH})
    state = _state((_robot(),))

    new_state, result, event = apply_robot_move(_east(), state, world, tick=3)

    assert not result.accepted
    assert event is None
    assert new_state is state
    robot = new_state.robot_for(EntityId("robot-player-one-1"))
    assert robot is not None
    assert (robot.x, robot.y, robot.movement) == (5, 5, None)


def test_transition_completes_exactly_at_the_configured_duration_boundary() -> None:
    state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), _world(), tick=0)
    duration = DEFAULT_RULES.robot_move_ticks_bipod

    robot = state.robot_for(EntityId("robot-player-one-1"))
    assert robot is not None

    for tick in range(duration):
        unchanged, event = advance_robot_transition(robot, tick)
        assert event is None
        assert (unchanged.x, unchanged.y) == (5, 5)
        assert unchanged.movement is not None

    completed, completion = advance_robot_transition(robot, duration)
    assert isinstance(completion, RobotMoveCompletedEvent)
    assert (completed.x, completed.y) == (6, 5)
    assert completed.movement is None


def test_advance_all_transitions_walks_robots_in_canonical_order() -> None:
    robots = (
        _robot(entity_id="robot-player-one-2", x=1, y=1),
        _robot(entity_id="robot-player-one-1", x=5, y=5),
    )
    state = _state(robots)
    world = _world()
    state, _r1, _e1 = apply_robot_move(_east("robot-player-one-1"), state, world, tick=0)
    state, _r2, _e2 = apply_robot_move(_east("robot-player-one-2"), state, world, tick=0)

    sequencer = EventSequencer()
    state, events = advance_all_robot_transitions(
        state, DEFAULT_RULES.robot_move_ticks_bipod, sequencer
    )

    assert [event.entity_id.value for event in events] == [
        "robot-player-one-1",
        "robot-player-one-2",
    ]
    assert [(robot.x, robot.y) for robot in state.robots] == [(6, 5), (2, 1)]


def test_advance_all_transitions_is_a_no_op_without_moves() -> None:
    state = _state((_robot(),))

    new_state, events = advance_all_robot_transitions(state, 100)

    assert events == ()
    assert new_state == state


def test_cancel_releases_an_in_progress_move_and_leaves_the_robot_at_its_origin() -> None:
    state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), _world(), tick=0)

    cancelled_state, event = cancel_robot_move(state, EntityId("robot-player-one-1"), tick=2)

    assert isinstance(event, RobotMoveCancelledEvent)
    robot = cancelled_state.robot_for(EntityId("robot-player-one-1"))
    assert robot is not None
    assert (robot.x, robot.y, robot.movement) == (5, 5, None)


def test_cancelling_twice_is_a_safe_no_op() -> None:
    state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), _world(), tick=0)
    state, _first = cancel_robot_move(state, EntityId("robot-player-one-1"), tick=2)

    unchanged, event = cancel_robot_move(state, EntityId("robot-player-one-1"), tick=3)

    assert event is None
    assert unchanged is state


def test_cancelling_an_unknown_robot_is_a_safe_no_op() -> None:
    state = _state((_robot(),))

    unchanged, event = cancel_robot_move(state, EntityId("robot-nobody"), tick=1)

    assert event is None
    assert unchanged is state


def test_a_cancelled_move_can_be_reissued() -> None:
    world = _world()
    state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), world, tick=0)
    state, _cancel_event = cancel_robot_move(state, EntityId("robot-player-one-1"), tick=1)

    _state_after, result, _event2 = apply_robot_move(_east(), state, world, tick=2)

    assert result.accepted


def test_robot_rejects_a_transition_belonging_to_another_robot() -> None:
    foreign = RobotMoveTransition(
        entity_id=EntityId("robot-player-two-1"),
        from_x=5,
        from_y=5,
        to_x=6,
        to_y=5,
        started_tick=0,
        duration_ticks=4,
    )

    with pytest.raises(ValueError):
        _robot().with_movement(foreign)


# --- Determinism / replay safety ----------------------------------------------


def test_identical_inputs_produce_identical_states_and_events() -> None:
    world = _world(terrain_cells={(6, 5): TerrainType.ROUGH})

    def run() -> tuple[GameState, tuple[object, ...]]:
        state = _state((_robot(), _robot(entity_id="robot-player-one-2", x=1, y=1)))
        sequencer = EventSequencer()
        emitted: list[object] = []
        for request in (_east("robot-player-one-1"), _east("robot-player-one-2")):
            state, _result, event = apply_robot_move(
                request, state, world, 0, DEFAULT_RULES, sequencer=sequencer
            )
            if event is not None:
                emitted.append(event)
        for tick in range(1, 30):
            state, completions = advance_all_robot_transitions(state, tick, sequencer)
            emitted.extend(completions)
        return state, tuple(emitted)

    first_state, first_events = run()
    second_state, second_events = run()

    assert first_state == second_state
    assert first_events == second_events


def test_move_state_survives_a_snapshot_round_trip_shape() -> None:
    from nether_earth.snapshot import to_snapshot

    state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), _world(), tick=7)

    snapshot = to_snapshot(state)
    assert snapshot["robots"][0]["movement"] == {
        "entity_id": "robot-player-one-1",
        "from_x": 5,
        "from_y": 5,
        "to_x": 6,
        "to_y": 5,
        "started_tick": 7,
        "duration_ticks": DEFAULT_RULES.robot_move_ticks_bipod,
    }


def test_idle_robot_snapshots_a_null_movement() -> None:
    from nether_earth.snapshot import to_snapshot

    assert to_snapshot(_state((_robot(),)))["robots"][0]["movement"] is None


def test_engine_step_completes_an_in_flight_move() -> None:
    from nether_earth import engine

    world = _world()
    state, _result, _event = apply_robot_move(_east(), _state((_robot(),)), world, tick=0)

    events: list[object] = []
    for _ in range(DEFAULT_RULES.robot_move_ticks_bipod):
        state, tick_events = engine.step(state, (), world)
        events.extend(tick_events)

    robot = state.robot_for(EntityId("robot-player-one-1"))
    assert robot is not None
    assert (robot.x, robot.y, robot.movement) == (6, 5, None)
    assert any(isinstance(event, RobotMoveCompletedEvent) for event in events)
