"""Tests for deterministic destination reservations and contention (issue #62, M5.3)."""

from __future__ import annotations

from collections import Counter

from nether_earth import engine
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveCompletedEvent,
    RobotMoveRequest,
    RobotMoveStartedEvent,
    advance_all_robot_transitions,
    apply_robot_move,
    cancel_robot_move,
    folded_robot_occupancy,
    validate_robot_move,
)
from nether_earth.reservations import (
    DestinationContentionResolvedEvent,
    apply_robot_move_batch,
    contention_rng,
    derive_contention_seed,
    destination_available,
    reservations_from_state,
)
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.terrain import TerrainGrid, TerrainType

BIPOD_TICKS = DEFAULT_RULES.robot_move_ticks_bipod


def _world(width: int = 10, height: int = 10) -> WorldMap:
    return WorldMap(
        map_id="reservations-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width, height=height, cells={}, default=TerrainType.NORMAL
        ),
        war_bases=(),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str,
    x: int,
    y: int,
    owner: PlayerId = PLAYER_ONE,
    movement: RobotMoveTransition | None = None,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
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


def _state(robots: tuple[Robot, ...] = (), tick: int = 0, seed: int = 0) -> GameState:
    return create_game_state(
        tick, (PLAYER_ONE, PLAYER_TWO), seed=seed, robots=list(robots)
    )


def _move(entity_id: str, dx: int, dy: int) -> RobotMoveRequest:
    return RobotMoveRequest(entity_id=EntityId(entity_id), dx=dx, dy=dy)


def _converging_state(seed: int = 0, tick: int = 0) -> GameState:
    """Two robots on opposite sides of ``(5, 5)``, each one step away from it."""
    return _state(
        (
            _robot("robot-a", 4, 5),
            _robot("robot-b", 6, 5, owner=PLAYER_TWO),
        ),
        tick=tick,
        seed=seed,
    )


def _four_way_state(seed: int = 0) -> GameState:
    """Four robots surrounding ``(5, 5)``, each one step away from it."""
    return _state(
        (
            _robot("robot-a", 4, 5),
            _robot("robot-b", 6, 5, owner=PLAYER_TWO),
            _robot("robot-c", 5, 4),
            _robot("robot-d", 5, 6, owner=PLAYER_TWO),
        ),
        seed=seed,
    )


_CONVERGING_CLAIMS = (
    _move("robot-a", 1, 0),
    _move("robot-b", -1, 0),
)

_FOUR_WAY_CLAIMS = (
    _move("robot-a", 1, 0),
    _move("robot-b", -1, 0),
    _move("robot-c", 0, 1),
    _move("robot-d", 0, -1),
)


def _winner(result: object) -> EntityId | None:
    """Return the single robot that started a move in ``result``, if any."""
    assert hasattr(result, "started")
    started = result.started  # type: ignore[attr-defined]
    assert len(started) <= 1
    return started[0].entity_id if started else None


# --- Reservation table (derived projection) ----------------------------------


def test_idle_robots_reserve_nothing() -> None:
    table = reservations_from_state(_state((_robot("robot-a", 4, 5),)))

    assert table.holders == {}
    assert table.cells() == ()


def test_a_started_move_reserves_its_destination() -> None:
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _state((_robot("robot-a", 4, 5),)), _world(), tick=0
    )

    table = reservations_from_state(state)

    assert table.holder(5, 5) == EntityId("robot-a")
    assert table.is_reserved(5, 5)
    assert not table.is_reserved(4, 5)  # the origin is occupancy, not a reservation


def test_reservation_table_lists_cells_in_canonical_order() -> None:
    state = _state(
        (
            _robot(
                "robot-a",
                4,
                5,
                movement=RobotMoveTransition(
                    entity_id=EntityId("robot-a"),
                    from_x=4,
                    from_y=5,
                    to_x=5,
                    to_y=5,
                    started_tick=0,
                    duration_ticks=BIPOD_TICKS,
                ),
            ),
            _robot(
                "robot-b",
                8,
                1,
                owner=PLAYER_TWO,
                movement=RobotMoveTransition(
                    entity_id=EntityId("robot-b"),
                    from_x=8,
                    from_y=1,
                    to_x=8,
                    to_y=2,
                    started_tick=0,
                    duration_ticks=BIPOD_TICKS,
                ),
            ),
        )
    )

    assert reservations_from_state(state).cells() == ((5, 5), (8, 2))


# --- destination_available (the movement.py hook) ----------------------------


def test_another_robots_reservation_makes_a_cell_unavailable() -> None:
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), _world(), tick=0
    )
    robot_b = state.robot_for(EntityId("robot-b"))
    assert robot_b is not None

    assert not destination_available(state, robot_b, 5, 5)


def test_a_robot_never_contends_with_its_own_reservation() -> None:
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), _world(), tick=0
    )
    robot_a = state.robot_for(EntityId("robot-a"))
    assert robot_a is not None

    assert destination_available(state, robot_a, 5, 5)


def test_an_unreserved_cell_is_available() -> None:
    state = _converging_state()
    robot_b = state.robot_for(EntityId("robot-b"))
    assert robot_b is not None

    assert destination_available(state, robot_b, 5, 5)


def test_reserved_destination_is_rejected_through_the_movement_gate() -> None:
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), _world(), tick=0
    )

    result = validate_robot_move(
        _move("robot-b", -1, 0),
        state,
        _world(),
        DEFAULT_RULES,
        destination_available,
    )

    assert not result.accepted
    assert result.reason is MovementRejectionReason.DESTINATION_UNAVAILABLE


# --- Reservation lifecycle ---------------------------------------------------


def test_reservation_is_released_on_completion_and_becomes_occupancy() -> None:
    world = _world()
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), world, tick=0
    )
    assert reservations_from_state(state).is_reserved(5, 5)

    state, events = advance_all_robot_transitions(state, BIPOD_TICKS)

    assert len(events) == 1
    assert reservations_from_state(state).holders == {}
    assert folded_robot_occupancy(world, state).is_occupied(5, 5)


def test_reservation_is_released_on_cancellation() -> None:
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), _world(), tick=0
    )

    state, cancel_event = cancel_robot_move(state, EntityId("robot-a"), tick=2)

    assert cancel_event is not None
    assert reservations_from_state(state).holders == {}
    robot_a = state.robot_for(EntityId("robot-a"))
    assert robot_a is not None
    assert (robot_a.x, robot_a.y) == (4, 5)


def test_a_cancelled_reservation_can_be_taken_by_the_other_contender() -> None:
    world = _world()
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), world, tick=0
    )
    state, _cancel_event = cancel_robot_move(state, EntityId("robot-a"), tick=2)

    batch = apply_robot_move_batch((_move("robot-b", -1, 0),), state, world, tick=3)

    assert _winner(batch) == EntityId("robot-b")
    assert reservations_from_state(batch.state).holder(5, 5) == EntityId("robot-b")


def test_cancelling_twice_releases_once_and_leaks_nothing() -> None:
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), _world(), tick=0
    )

    state, first = cancel_robot_move(state, EntityId("robot-a"), tick=2)
    state, second = cancel_robot_move(state, EntityId("robot-a"), tick=3)

    assert first is not None
    assert second is None
    assert reservations_from_state(state).holders == {}


def test_a_failed_move_leaks_no_reservation() -> None:
    world = _world()
    # robot-b sits on (6, 5); robot-a at (5, 5) moving east is OCCUPIED.
    state = _state((_robot("robot-a", 5, 5), _robot("robot-b", 6, 5, owner=PLAYER_TWO)))

    new_state, result, event = apply_robot_move(
        _move("robot-a", 1, 0), state, world, tick=0, destination_check=destination_available
    )

    assert not result.accepted
    assert result.reason is MovementRejectionReason.OCCUPIED
    assert event is None
    assert new_state is state
    assert reservations_from_state(new_state).holders == {}


def test_a_held_reservation_survives_until_the_move_completes() -> None:
    world = _world()
    state, _result, _event = apply_robot_move(
        _move("robot-a", 1, 0), _converging_state(), world, tick=0
    )

    for tick in range(1, BIPOD_TICKS):
        state, _events = advance_all_robot_transitions(state, tick)
        assert reservations_from_state(state).holder(5, 5) == EntityId("robot-a")

    state, _events = advance_all_robot_transitions(state, BIPOD_TICKS)
    assert reservations_from_state(state).holders == {}


# --- Batched same-tick claims ------------------------------------------------


def test_uncontested_claims_all_start() -> None:
    state = _state((_robot("robot-a", 1, 1), _robot("robot-b", 8, 8, owner=PLAYER_TWO)))

    batch = apply_robot_move_batch(
        (_move("robot-a", 1, 0), _move("robot-b", -1, 0)), state, _world(), tick=0
    )

    assert all(result.accepted for result in batch.results)
    assert len(batch.started) == 2
    assert batch.contentions == ()
    assert reservations_from_state(batch.state).cells() == ((2, 1), (7, 8))


def test_only_one_contender_takes_a_contested_cell() -> None:
    batch = apply_robot_move_batch(
        _CONVERGING_CLAIMS, _converging_state(seed=11), _world(), tick=0
    )

    accepted = [result for result in batch.results if result.accepted]
    rejected = [result for result in batch.results if not result.accepted]

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].reason is MovementRejectionReason.DESTINATION_UNAVAILABLE
    assert reservations_from_state(batch.state).cells() == ((5, 5),)


def test_the_loser_stays_put_with_no_partial_mutation() -> None:
    state = _converging_state(seed=11)

    batch = apply_robot_move_batch(_CONVERGING_CLAIMS, state, _world(), tick=0)

    winner = _winner(batch)
    for robot in batch.state.robots:
        original = state.robot_for(robot.entity_id)
        assert original is not None
        assert (robot.x, robot.y) == (original.x, original.y)
        if robot.entity_id == winner:
            assert robot.movement is not None
        else:
            assert robot.movement is None


def test_an_all_rejected_batch_returns_the_callers_state_object() -> None:
    state = _state((_robot("robot-a", 0, 0),))

    batch = apply_robot_move_batch((_move("robot-a", -1, 0),), state, _world(), tick=0)

    assert batch.state is state
    assert batch.results[0].reason is MovementRejectionReason.OUT_OF_BOUNDS


def test_a_robot_cannot_claim_twice_in_one_batch() -> None:
    state = _state((_robot("robot-a", 5, 5),))

    batch = apply_robot_move_batch(
        (_move("robot-a", 1, 0), _move("robot-a", 0, 1)), state, _world(), tick=0
    )

    assert [result.accepted for result in batch.results] == [True, False]
    assert batch.results[1].reason is MovementRejectionReason.MOVE_IN_PROGRESS
    assert len(batch.started) == 1


def test_every_request_gets_exactly_one_result() -> None:
    batch = apply_robot_move_batch(_FOUR_WAY_CLAIMS, _four_way_state(seed=5), _world(), tick=0)

    assert len(batch.results) == len(_FOUR_WAY_CLAIMS)
    assert {result.request for result in batch.results} == set(_FOUR_WAY_CLAIMS)


def test_a_contested_cell_emits_one_contention_event_naming_every_contender() -> None:
    batch = apply_robot_move_batch(
        _FOUR_WAY_CLAIMS, _four_way_state(seed=5), _world(), tick=0, sequencer=EventSequencer()
    )

    assert len(batch.contentions) == 1
    event = batch.contentions[0]
    assert (event.x, event.y) == (5, 5)
    assert event.contenders == tuple(
        EntityId(name) for name in ("robot-a", "robot-b", "robot-c", "robot-d")
    )
    assert event.winner in event.contenders
    assert _winner(batch) == event.winner


def test_losers_can_retry_on_a_later_tick() -> None:
    world = _world()
    state = _converging_state(seed=11)

    batch = apply_robot_move_batch(_CONVERGING_CLAIMS, state, world, tick=0)
    winner = _winner(batch)
    loser = next(
        result.request.entity_id
        for result in batch.results
        if not result.accepted
    )
    state = batch.state

    # The winner completes and vacates its reservation (it now occupies the
    # cell), so the loser's identical retry is now an occupancy rejection,
    # not a reservation one -- a stable, actionable reason either way.
    state, _events = advance_all_robot_transitions(state, BIPOD_TICKS)
    retry = apply_robot_move_batch(
        (_move(loser.value, -1 if loser.value == "robot-b" else 1, 0),),
        state,
        world,
        tick=BIPOD_TICKS,
    )

    assert winner is not None
    assert not retry.results[0].accepted
    assert retry.results[0].reason is MovementRejectionReason.OCCUPIED


# --- Determinism and the seeded coin flip ------------------------------------


def test_same_seed_state_and_claims_yield_the_same_winner_every_run() -> None:
    winners = {
        _winner(
            apply_robot_move_batch(
                _CONVERGING_CLAIMS, _converging_state(seed=11), _world(), tick=0
            )
        )
        for _ in range(25)
    }

    assert len(winners) == 1


def test_submission_order_does_not_decide_the_winner() -> None:
    forward = apply_robot_move_batch(
        _CONVERGING_CLAIMS, _converging_state(seed=11), _world(), tick=0
    )
    reversed_order = apply_robot_move_batch(
        tuple(reversed(_CONVERGING_CLAIMS)), _converging_state(seed=11), _world(), tick=0
    )

    assert _winner(forward) == _winner(reversed_order)


def test_two_way_contention_is_a_seeded_coin_flip_not_priority_by_id() -> None:
    # Each seed is an independent match, so this measures the distribution of
    # the locked 50/50 coin flip rather than one RNG stream's autocorrelation.
    trials = 2000
    winners = Counter(
        _winner(
            apply_robot_move_batch(
                _CONVERGING_CLAIMS, _converging_state(seed=seed), _world(), tick=0
            )
        )
        for seed in range(trials)
    )

    assert set(winners) == {EntityId("robot-a"), EntityId("robot-b")}
    # Binomial(2000, 0.5) has sigma ~22.4; +/-5% of the trials is > 4 sigma,
    # so this is a tight fairness bound that is still not flaky.
    for count in winners.values():
        assert abs(count - trials / 2) < trials * 0.05


def test_the_same_contention_flips_over_successive_ticks() -> None:
    # A single match-seed RNG reused verbatim every tick would hand the same
    # index the win forever; the per-tick stream must not do that.
    world = _world()
    winners = {
        _winner(
            apply_robot_move_batch(
                _CONVERGING_CLAIMS, _converging_state(seed=11, tick=tick), world, tick=tick
            )
        )
        for tick in range(40)
    }

    assert len(winners) == 2


def test_n_way_contention_is_a_uniform_seeded_choice() -> None:
    trials = 2000
    winners = Counter(
        _winner(
            apply_robot_move_batch(_FOUR_WAY_CLAIMS, _four_way_state(seed=seed), _world(), tick=0)
        )
        for seed in range(trials)
    )

    assert set(winners) == {
        EntityId(name) for name in ("robot-a", "robot-b", "robot-c", "robot-d")
    }
    # Binomial(2000, 0.25) has sigma ~19.4; +/-5% of the trials is > 5 sigma.
    for count in winners.values():
        assert abs(count - trials / 4) < trials * 0.05


def test_independent_contested_cells_draw_independently_within_one_tick() -> None:
    # Two separate 2-way contentions in one tick must not be forced to the
    # same positional outcome by sharing the tick's stream.
    claims = (
        _move("robot-a", 1, 0),
        _move("robot-b", -1, 0),
        _move("robot-c", 1, 0),
        _move("robot-d", -1, 0),
    )
    outcomes = set()
    for seed in range(60):
        state = _state(
            (
                _robot("robot-a", 1, 1),
                _robot("robot-b", 3, 1, owner=PLAYER_TWO),
                _robot("robot-c", 1, 8),
                _robot("robot-d", 3, 8, owner=PLAYER_TWO),
            ),
            seed=seed,
        )
        batch = apply_robot_move_batch(claims, state, _world(), tick=0)
        assert len(batch.contentions) == 2
        outcomes.add(tuple(event.winner.value for event in batch.contentions))

    assert len(outcomes) == 4


def test_derive_contention_seed_is_stable_and_tick_dependent() -> None:
    assert derive_contention_seed(11, 3) == derive_contention_seed(11, 3)
    assert derive_contention_seed(11, 3) != derive_contention_seed(11, 4)
    assert derive_contention_seed(11, 3) != derive_contention_seed(12, 3)
    assert derive_contention_seed(-7, 0) == derive_contention_seed(-7, 0)


def test_contention_rng_is_reproducible_for_a_seed_and_tick() -> None:
    first = contention_rng(11, 3)
    second = contention_rng(11, 3)

    assert [first.random() for _ in range(5)] == [second.random() for _ in range(5)]


# --- engine.step integration and replay --------------------------------------


def test_engine_step_batches_same_tick_claims_before_resolving() -> None:
    world = _world()

    state, events = engine.step(
        _converging_state(seed=11), (), world, robot_moves=_CONVERGING_CLAIMS
    )

    contentions = [e for e in events if isinstance(e, DestinationContentionResolvedEvent)]
    started = [e for e in events if isinstance(e, RobotMoveStartedEvent)]
    assert len(contentions) == 1
    assert len(started) == 1
    assert started[0].entity_id == contentions[0].winner
    assert reservations_from_state(state).holder(5, 5) == contentions[0].winner


def test_engine_step_without_robot_moves_is_unchanged() -> None:
    state, events = engine.step(_converging_state(seed=11), (), _world())

    assert state.tick == 1
    assert events == ()
    assert reservations_from_state(state).holders == {}


def test_engine_step_replays_identical_reservation_and_winner_outcomes() -> None:
    world = _world()

    def run() -> tuple[GameState, tuple[Event, ...]]:
        state = _converging_state(seed=11)
        collected: list[Event] = []
        for _ in range(BIPOD_TICKS + 2):
            state, tick_events = engine.step(state, (), world, robot_moves=_CONVERGING_CLAIMS)
            collected.extend(tick_events)
        return state, tuple(collected)

    first_state, first_events = run()
    second_state, second_events = run()

    assert first_state == second_state
    assert first_events == second_events
    assert any(isinstance(event, DestinationContentionResolvedEvent) for event in first_events)
    assert any(isinstance(event, RobotMoveCompletedEvent) for event in first_events)


def test_no_two_robots_ever_hold_the_same_reservation_across_a_match() -> None:
    world = _world()
    state = _four_way_state(seed=11)

    for _ in range(BIPOD_TICKS * 3):
        state, _events = engine.step(state, (), world, robot_moves=_FOUR_WAY_CLAIMS)
        destinations = [
            (robot.movement.to_x, robot.movement.to_y)
            for robot in state.robots
            if robot.movement is not None
        ]
        assert len(destinations) == len(set(destinations))
        positions = [(robot.x, robot.y) for robot in state.robots]
        assert len(positions) == len(set(positions))
