"""Tests for authoritative commander horizontal/vertical movement (issue #38)."""

from __future__ import annotations

import pytest

from nether_earth.commander import Commander, CommanderMode, GridTransition, VerticalTransition
from nether_earth.commander_movement import (
    CommanderHorizontalMoveCompletedEvent,
    CommanderHorizontalMoveStartedEvent,
    CommanderMoveCommand,
    CommanderMovementRejectionReason,
    CommanderMoveResult,
    CommanderSetVerticalIntentCommand,
    CommanderVerticalIntentChangedEvent,
    CommanderVerticalUpdatedEvent,
    advance_all_horizontal_transitions,
    advance_commander_movement_tick,
    advance_horizontal_transition,
    apply_automatic_elevation,
    apply_commander_move,
    apply_vertical_physics,
    is_vertical_update_tick,
    set_vertical_intent,
    validate_commander_move,
)
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import create_game_state


def _free_commander(player_id=PLAYER_ONE, x=5, y=5, altitude=0, rising=False):
    return Commander(
        player_id=player_id,
        mode=CommanderMode.FREE,
        x=x,
        y=y,
        altitude=altitude,
        rising=rising,
    )


def _state_with_commanders(*commanders):
    players = tuple({c.player_id for c in commanders})
    return create_game_state(tick=0, players=players, commanders=commanders)


# --------------------------------------------------------------------------
# CommanderMoveCommand structural validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dx,dy", [(1, 0), (-1, 0), (0, 1), (0, -1)])
def test_move_command_accepts_cardinal_directions(dx, dy) -> None:
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=dx, dy=dy)
    assert command.dx == dx
    assert command.dy == dy


def test_move_command_rejects_diagonal() -> None:
    with pytest.raises(ValueError):
        CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=1)


def test_move_command_rejects_no_op() -> None:
    with pytest.raises(ValueError):
        CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=0, dy=0)


def test_move_command_rejects_out_of_range_delta() -> None:
    with pytest.raises(ValueError):
        CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=2, dy=0)


# --------------------------------------------------------------------------
# validate_commander_move
# --------------------------------------------------------------------------


def test_validate_move_rejects_when_no_commander() -> None:
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    result = validate_commander_move(command, state)

    assert not result.accepted
    assert result.reason is CommanderMovementRejectionReason.NO_COMMANDER


def test_validate_move_rejects_docked_commander() -> None:
    docked = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=0,
        y=0,
        altitude=0,
        docked_robot_id=EntityId("robot-1"),
    )
    state = _state_with_commanders(docked)
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    result = validate_commander_move(command, state)

    assert not result.accepted
    assert result.reason is CommanderMovementRejectionReason.NOT_FREE


def test_validate_move_rejects_when_move_already_in_progress() -> None:
    in_flight = _free_commander().with_horizontal_transition(
        GridTransition(from_x=5, from_y=5, to_x=6, to_y=5, started_tick=0, duration_ticks=4)
    )
    state = _state_with_commanders(in_flight)
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=0, dy=1)

    result = validate_commander_move(command, state)

    assert not result.accepted
    assert result.reason is CommanderMovementRejectionReason.MOVE_IN_PROGRESS


def test_validate_move_rejects_when_collision_check_blocks() -> None:
    state = _state_with_commanders(_free_commander())
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    result = validate_commander_move(command, state, horizontal_check=lambda *_: False)

    assert not result.accepted
    assert result.reason is CommanderMovementRejectionReason.BLOCKED


def test_validate_move_accepts_legal_move() -> None:
    state = _state_with_commanders(_free_commander())
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    result = validate_commander_move(command, state)

    assert result.accepted
    assert result.reason is None


def test_move_result_invariant_rejects_accepted_with_reason() -> None:
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    with pytest.raises(ValueError):
        CommanderMoveResult(
            command=command, accepted=True, reason=CommanderMovementRejectionReason.BLOCKED
        )


def test_move_result_invariant_rejects_rejected_without_reason() -> None:
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    with pytest.raises(ValueError):
        CommanderMoveResult(command=command, accepted=False, reason=None)


# --------------------------------------------------------------------------
# apply_commander_move / horizontal transition lifecycle
# --------------------------------------------------------------------------


def test_apply_commander_move_starts_transition_without_moving_authoritative_position() -> None:
    state = _state_with_commanders(_free_commander(x=5, y=5))
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    new_state, result, event = apply_commander_move(command, state, tick=10, rules=DEFAULT_RULES)

    assert result.accepted
    commander = new_state.commander_for(PLAYER_ONE)
    assert commander is not None
    # Authoritative position unchanged until the transition resolves.
    assert (commander.x, commander.y) == (5, 5)
    assert commander.horizontal_transition == GridTransition(
        from_x=5, from_y=5, to_x=6, to_y=5, started_tick=10, duration_ticks=4
    )
    assert isinstance(event, CommanderHorizontalMoveStartedEvent)
    assert event.from_x == 5 and event.to_x == 6
    assert event.started_tick == 10
    assert event.duration_ticks == 4


def test_apply_commander_move_rejected_leaves_state_unchanged() -> None:
    state = _state_with_commanders(_free_commander())
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    new_state, result, event = apply_commander_move(
        command, state, tick=0, horizontal_check=lambda *_: False
    )

    assert new_state is state
    assert not result.accepted
    assert event is None


def test_apply_commander_move_uses_custom_horizontal_move_ticks() -> None:
    rules = EngineRules(commander_horizontal_move_ticks=7)
    state = _state_with_commanders(_free_commander())
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    _new_state, result, event = apply_commander_move(command, state, tick=0, rules=rules)

    assert result.accepted
    assert event is not None
    assert event.duration_ticks == 7


def test_advance_horizontal_transition_not_complete_before_duration_elapses() -> None:
    commander = _free_commander().with_horizontal_transition(
        GridTransition(from_x=5, from_y=5, to_x=6, to_y=5, started_tick=0, duration_ticks=4)
    )

    updated, event = advance_horizontal_transition(commander, tick=3)

    assert updated is commander
    assert event is None
    assert (updated.x, updated.y) == (5, 5)


def test_advance_horizontal_transition_completes_exactly_at_boundary() -> None:
    commander = _free_commander().with_horizontal_transition(
        GridTransition(from_x=5, from_y=5, to_x=6, to_y=5, started_tick=0, duration_ticks=4)
    )

    updated, event = advance_horizontal_transition(commander, tick=4)

    assert (updated.x, updated.y) == (6, 5)
    assert updated.horizontal_transition is None
    assert isinstance(event, CommanderHorizontalMoveCompletedEvent)
    assert (event.x, event.y) == (6, 5)
    assert event.tick == 4


def test_advance_horizontal_transition_completes_when_overdue() -> None:
    commander = _free_commander().with_horizontal_transition(
        GridTransition(from_x=5, from_y=5, to_x=6, to_y=5, started_tick=0, duration_ticks=4)
    )

    updated, event = advance_horizontal_transition(commander, tick=100)

    assert (updated.x, updated.y) == (6, 5)
    assert event is not None


def test_advance_horizontal_transition_noop_when_no_transition() -> None:
    commander = _free_commander()

    updated, event = advance_horizontal_transition(commander, tick=100)

    assert updated is commander
    assert event is None


def test_advance_all_horizontal_transitions_canonical_order() -> None:
    c1 = _free_commander(player_id=PLAYER_TWO, x=0, y=0).with_horizontal_transition(
        GridTransition(from_x=0, from_y=0, to_x=1, to_y=0, started_tick=0, duration_ticks=2)
    )
    c2 = _free_commander(player_id=PLAYER_ONE, x=9, y=9).with_horizontal_transition(
        GridTransition(from_x=9, from_y=9, to_x=8, to_y=9, started_tick=0, duration_ticks=2)
    )
    state = _state_with_commanders(c1, c2)

    new_state, events = advance_all_horizontal_transitions(state, tick=2)

    assert len(events) == 2
    # state.commanders is canonically sorted by player_id.value: p1 before p2.
    assert [e.player_id for e in events] == [PLAYER_ONE, PLAYER_TWO]
    assert new_state.commander_for(PLAYER_ONE).x == 8
    assert new_state.commander_for(PLAYER_TWO).x == 1


# --------------------------------------------------------------------------
# Vertical intent
# --------------------------------------------------------------------------


def test_set_vertical_intent_updates_rising() -> None:
    state = _state_with_commanders(_free_commander(rising=False))
    command = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)

    new_state, event = set_vertical_intent(command, state, tick=1)

    commander = new_state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert commander.rising is True
    assert isinstance(event, CommanderVerticalIntentChangedEvent)
    assert event.rising is True


def test_set_vertical_intent_noop_when_unchanged() -> None:
    state = _state_with_commanders(_free_commander(rising=True))
    command = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)

    new_state, event = set_vertical_intent(command, state, tick=1)

    assert new_state is state
    assert event is None


def test_set_vertical_intent_noop_when_no_commander() -> None:
    state = create_game_state(tick=0, players=(PLAYER_ONE,))
    command = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True)

    new_state, event = set_vertical_intent(command, state, tick=1)

    assert new_state is state
    assert event is None


# --------------------------------------------------------------------------
# Vertical cadence
# --------------------------------------------------------------------------


def test_vertical_update_cadence_matches_default_every_four_ticks() -> None:
    update_ticks = [tick for tick in range(17) if is_vertical_update_tick(tick)]
    assert update_ticks == [4, 8, 12, 16]


def test_vertical_update_cadence_respects_custom_config() -> None:
    rules = EngineRules(commander_vertical_update_ticks=3)
    update_ticks = [tick for tick in range(13) if is_vertical_update_tick(tick, rules)]
    assert update_ticks == [3, 6, 9, 12]


def test_tick_zero_is_never_an_update_tick() -> None:
    assert is_vertical_update_tick(0) is False


# --------------------------------------------------------------------------
# Vertical physics
# --------------------------------------------------------------------------


def test_holding_rise_ascends_plus_two_per_update_until_max() -> None:
    state = _state_with_commanders(_free_commander(altitude=44, rising=True))
    commander = state.commander_for(PLAYER_ONE)

    altitudes = []
    events = []
    for tick in (4, 8, 12, 16):
        commander, event = apply_vertical_physics(commander, state, tick)
        altitudes.append(commander.altitude)
        events.append(event)

    # 44 -> 46 -> 48 -> 48 (clamped, no further change/event) -> 48
    assert altitudes == [46, 48, 48, 48]
    assert events[0] is not None and events[1] is not None
    assert events[2] is None and events[3] is None


def test_ascent_clamps_at_max_altitude_and_stops_emitting_events() -> None:
    state = _state_with_commanders(_free_commander(altitude=47, rising=True))
    commander = state.commander_for(PLAYER_ONE)

    commander, event = apply_vertical_physics(commander, state, tick=4)
    assert commander.altitude == 48
    assert event is not None

    commander, event = apply_vertical_physics(commander, state, tick=8)
    assert commander.altitude == 48
    assert event is None  # already at max: no-op, no event


def test_no_rise_descends_by_descent_step_per_update_until_min() -> None:
    # CR003.1 (#216): gravity is -2 per update; 1 - 2 clamps to 0.
    state = _state_with_commanders(_free_commander(altitude=3, rising=False))
    commander = state.commander_for(PLAYER_ONE)

    altitudes = []
    for tick in (4, 8, 12, 16):
        commander, _event = apply_vertical_physics(commander, state, tick)
        altitudes.append(commander.altitude)

    assert altitudes == [1, 0, 0, 0]


def test_descent_clamps_at_min_altitude_and_stops_emitting_events() -> None:
    state = _state_with_commanders(_free_commander(altitude=0, rising=False))
    commander = state.commander_for(PLAYER_ONE)

    commander, event = apply_vertical_physics(commander, state, tick=4)
    assert commander.altitude == 0
    assert event is None


def test_vertical_physics_respects_custom_asymmetric_steps() -> None:
    rules = EngineRules(commander_ascent_step=5, commander_descent_step=3)
    state = _state_with_commanders(_free_commander(altitude=10, rising=True))
    commander = state.commander_for(PLAYER_ONE)

    commander, _ = apply_vertical_physics(commander, state, tick=4, rules=rules)
    assert commander.altitude == 15

    commander = commander.with_rising(False)
    commander, _ = apply_vertical_physics(commander, state, tick=8, rules=rules)
    assert commander.altitude == 12


def test_vertical_physics_respects_custom_altitude_bounds() -> None:
    rules = EngineRules(commander_min_altitude=10, commander_max_altitude=20)
    state = _state_with_commanders(_free_commander(altitude=19, rising=True))
    commander = state.commander_for(PLAYER_ONE)

    commander, event = apply_vertical_physics(commander, state, tick=4, rules=rules)

    assert commander.altitude == 20
    assert event is not None


def test_vertical_physics_blocked_by_collision_hook_leaves_altitude_unchanged() -> None:
    state = _state_with_commanders(_free_commander(altitude=10, rising=False))
    commander = state.commander_for(PLAYER_ONE)

    commander, event = apply_vertical_physics(
        commander, state, tick=4, vertical_check=lambda *_: False
    )

    assert commander.altitude == 10
    assert event is None


def test_vertical_physics_noop_for_docked_commander() -> None:
    docked = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=0,
        y=0,
        altitude=10,
        docked_robot_id=EntityId("robot-1"),
        rising=True,
    )
    state = _state_with_commanders(docked)

    updated, event = apply_vertical_physics(docked, state, tick=4)

    assert updated is docked
    assert event is None


def test_vertical_physics_sets_vertical_transition_record() -> None:
    state = _state_with_commanders(_free_commander(altitude=10, rising=True))
    commander = state.commander_for(PLAYER_ONE)

    commander, _ = apply_vertical_physics(commander, state, tick=4)

    assert commander.vertical_transition == VerticalTransition(
        from_altitude=10, to_altitude=12, started_tick=4, duration_ticks=4
    )


# --------------------------------------------------------------------------
# Automatic elevation hook
# --------------------------------------------------------------------------


def test_automatic_elevation_applies_ascent_step_regardless_of_cadence_tick() -> None:
    state = _state_with_commanders(_free_commander(altitude=10, rising=False))
    commander = state.commander_for(PLAYER_ONE)

    # tick=5 is not a vertical-cadence tick, but automatic elevation is not
    # cadence-gated.
    updated, event = apply_automatic_elevation(commander, state, tick=5)

    assert updated.altitude == 12
    assert event is not None
    assert event.from_altitude == 10 and event.to_altitude == 12


def test_automatic_elevation_clamps_at_max_altitude() -> None:
    state = _state_with_commanders(_free_commander(altitude=47))
    commander = state.commander_for(PLAYER_ONE)

    updated, event = apply_automatic_elevation(commander, state, tick=1)

    assert updated.altitude == 48
    assert event is not None


def test_automatic_elevation_blocked_by_collision_hook() -> None:
    state = _state_with_commanders(_free_commander(altitude=10))
    commander = state.commander_for(PLAYER_ONE)

    updated, event = apply_automatic_elevation(
        commander, state, tick=1, vertical_check=lambda *_: False
    )

    assert updated is commander
    assert event is None


# --------------------------------------------------------------------------
# Whole-tick orchestration / determinism
# --------------------------------------------------------------------------


def test_advance_commander_movement_tick_supports_simultaneous_horizontal_and_vertical() -> None:
    state = _state_with_commanders(_free_commander(x=5, y=5, altitude=10, rising=True))
    commands = (CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),)

    # tick 4 is both a horizontal-move-start tick and a vertical cadence tick.
    state, events = advance_commander_movement_tick(state, tick=4, commands=commands)

    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert commander.altitude == 12  # vertical update applied this same tick
    assert commander.horizontal_transition is not None  # horizontal move started, not yet done
    assert any(isinstance(e, CommanderHorizontalMoveStartedEvent) for e in events)
    assert any(isinstance(e, CommanderVerticalUpdatedEvent) for e in events)


def test_advance_commander_movement_tick_completes_horizontal_move_after_duration() -> None:
    state = _state_with_commanders(_free_commander(x=5, y=5))
    commands = (CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),)

    state, _ = advance_commander_movement_tick(state, tick=0, commands=commands)
    commander = state.commander_for(PLAYER_ONE)
    assert (commander.x, commander.y) == (5, 5)  # still mid-transition

    state, events = advance_commander_movement_tick(state, tick=4, commands=())
    commander = state.commander_for(PLAYER_ONE)
    assert (commander.x, commander.y) == (6, 5)
    assert any(isinstance(e, CommanderHorizontalMoveCompletedEvent) for e in events)


def test_advance_commander_movement_tick_is_deterministic_regardless_of_command_order() -> None:
    initial_state = _state_with_commanders(
        _free_commander(player_id=PLAYER_ONE, x=1, y=1, altitude=10, rising=True),
        _free_commander(player_id=PLAYER_TWO, x=2, y=2, altitude=10, rising=False),
    )
    commands_forward = (
        CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),
        CommanderSetVerticalIntentCommand(player=PLAYER_TWO, sequence=0, rising=True),
    )
    commands_reversed = tuple(reversed(commands_forward))

    state_a, events_a = advance_commander_movement_tick(initial_state, 4, commands_forward)
    state_b, events_b = advance_commander_movement_tick(initial_state, 4, commands_reversed)

    assert state_a == state_b
    assert events_a == events_b


def test_same_initial_state_and_command_stream_reproduces_identical_state_and_events() -> None:
    def run() -> tuple[object, tuple]:
        state = _state_with_commanders(
            _free_commander(player_id=PLAYER_ONE, x=0, y=0, altitude=0, rising=True),
            _free_commander(player_id=PLAYER_TWO, x=5, y=5, altitude=48, rising=False),
        )
        all_events: list = []
        for tick in range(1, 9):
            commands: tuple = ()
            if tick == 1:
                commands = (CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),)
            elif tick == 3:
                commands = (
                    CommanderSetVerticalIntentCommand(player=PLAYER_TWO, sequence=0, rising=True),
                )
            state, tick_events = advance_commander_movement_tick(state, tick, commands)
            all_events.extend(tick_events)
        return state, tuple(all_events)

    result_1 = run()
    result_2 = run()

    assert result_1 == result_2


def test_shared_sequencer_assigns_monotonic_sequences_within_tick() -> None:
    state = _state_with_commanders(
        _free_commander(player_id=PLAYER_ONE, x=0, y=0, altitude=10, rising=True),
        _free_commander(player_id=PLAYER_TWO, x=5, y=5, altitude=10, rising=True),
    )
    commands = (
        CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),
        CommanderMoveCommand(player=PLAYER_TWO, sequence=0, dx=1, dy=0),
    )

    _, events = advance_commander_movement_tick(state, tick=4, commands=commands)

    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_event_sequencer_explicit_usage_across_multiple_calls() -> None:
    sequencer = EventSequencer()
    state = _state_with_commanders(_free_commander())
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)

    _, _, event1 = apply_commander_move(command, state, tick=0, sequencer=sequencer)
    state2 = _state_with_commanders(_free_commander(y=6))
    command2 = CommanderMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0)
    _, _, event2 = apply_commander_move(command2, state2, tick=0, sequencer=sequencer)

    assert event1 is not None and event2 is not None
    assert event2.sequence == event1.sequence + 1


# --- Construction-exit automatic ascent (CR002.12/CR002.13) -------------------


def test_elevate_counter_ascends_regardless_of_rise_intent_and_counts_down() -> None:
    commander = _free_commander(altitude=15).with_elevate_updates(5)
    state = _state_with_commanders(commander)
    altitudes = []
    for tick in (4, 8, 12, 16, 20, 24, 28):
        commander, _event = apply_vertical_physics(commander, state, tick)
        altitudes.append(commander.altitude)
    assert altitudes == [17, 19, 21, 23, 25, 23, 21]
    assert commander.elevate_updates_remaining == 0


def test_elevate_counter_is_consumed_even_when_the_ascent_is_blocked() -> None:
    commander = _free_commander(altitude=15).with_elevate_updates(2)
    state = _state_with_commanders(commander)
    blocked, event = apply_vertical_physics(
        commander, state, 4, vertical_check=lambda _s, _c, _a: False
    )
    assert event is None
    assert blocked.altitude == 15
    assert blocked.elevate_updates_remaining == 1


def test_commander_rejects_negative_elevate_counter() -> None:
    with pytest.raises(ValueError):
        _free_commander().with_elevate_updates(-1)
