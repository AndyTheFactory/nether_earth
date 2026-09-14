"""Tests for commander docking/undocking on robots (issue #40, M3.4).

Covers every acceptance criterion in the issue verbatim:

- friendly docking occurs automatically exactly at the physical docking
  condition;
- a docked commander follows the robot fixture's position and derives
  vertical placement from robot height;
- ascent undocks deterministically and leaves a valid FREE state;
- enemy robot contact never creates DOCKED state and never changes
  ownership/control/strength;
- friendly and enemy behavior share the same physical top-surface/collision
  model (this module does not duplicate ``collision.py``'s geometry -- these
  tests exercise ``attempt_auto_dock`` directly, which is the only new
  geometry-adjacent decision this issue adds: "is this specific resting
  contact a dock, or merely a landing?");
- moving fixture while docked, undocking, enemy landing/resting, and replay
  determinism are all covered.
"""

from __future__ import annotations

from nether_earth.collision import RobotFixture
from nether_earth.commander import Commander, CommanderMode
from nether_earth.docking import (
    CommanderDockedEvent,
    CommanderUndockedEvent,
    apply_undock,
    attempt_auto_dock,
    auto_dock_with_event,
    docked_movement_allowed,
    follow_docked_robot,
)
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import create_game_state


def _free_commander(
    player_id=PLAYER_ONE, x=5, y=5, altitude=0, rising=False
) -> Commander:
    return Commander(
        player_id=player_id,
        mode=CommanderMode.FREE,
        x=x,
        y=y,
        altitude=altitude,
        rising=rising,
    )


def _docked_commander(
    player_id=PLAYER_ONE,
    x=5,
    y=5,
    altitude=4,
    docked_robot_id=None,
    rising=False,
) -> Commander:
    if docked_robot_id is None:
        docked_robot_id = EntityId("robot-1")
    return Commander(
        player_id=player_id,
        mode=CommanderMode.DOCKED,
        x=x,
        y=y,
        altitude=altitude,
        docked_robot_id=docked_robot_id,
        rising=rising,
    )


def _friendly_robot(
    robot_id="robot-1", owner=PLAYER_ONE, x=5, y=5, height=4
) -> RobotFixture:
    return RobotFixture(id=EntityId(robot_id), owner=owner, x=x, y=y, height=height)


def _enemy_robot(
    robot_id="robot-enemy", owner=PLAYER_TWO, x=5, y=5, height=4
) -> RobotFixture:
    return RobotFixture(id=EntityId(robot_id), owner=owner, x=x, y=y, height=height)


def _state_with(*commanders) -> object:
    players = tuple({c.player_id for c in commanders} | {PLAYER_ONE, PLAYER_TWO})
    return create_game_state(tick=0, players=players, commanders=commanders)


# --------------------------------------------------------------------------
# Friendly auto-docking
# --------------------------------------------------------------------------


def test_friendly_dock_occurs_exactly_at_resting_altitude() -> None:
    robot = _friendly_robot(height=4)
    commander = _free_commander(altitude=4, x=5, y=5)

    docked = attempt_auto_dock(commander, (robot,))

    assert docked.mode is CommanderMode.DOCKED
    assert docked.docked_robot_id == robot.id
    # Position/altitude carried over unchanged by the dock transition itself.
    assert docked.x == commander.x
    assert docked.y == commander.y
    assert docked.altitude == commander.altitude


def test_no_dock_when_altitude_below_robot_top() -> None:
    robot = _friendly_robot(height=4)
    commander = _free_commander(altitude=3, x=5, y=5)

    result = attempt_auto_dock(commander, (robot,))

    assert result is commander
    assert result.mode is CommanderMode.FREE


def test_no_dock_when_altitude_above_robot_top() -> None:
    robot = _friendly_robot(height=4)
    commander = _free_commander(altitude=5, x=5, y=5)

    result = attempt_auto_dock(commander, (robot,))

    assert result is commander
    assert result.mode is CommanderMode.FREE


def test_no_dock_when_not_at_robot_xy() -> None:
    robot = _friendly_robot(height=4, x=5, y=5)
    commander = _free_commander(altitude=4, x=6, y=5)

    result = attempt_auto_dock(commander, (robot,))

    assert result is commander
    assert result.mode is CommanderMode.FREE


def test_no_dock_with_no_robots() -> None:
    commander = _free_commander(altitude=4)

    result = attempt_auto_dock(commander, ())

    assert result is commander


def test_already_docked_commander_does_not_redock() -> None:
    robot = _friendly_robot(height=4)
    other_robot = _friendly_robot(robot_id="robot-2", height=4)
    commander = _docked_commander(docked_robot_id=robot.id, altitude=4)

    result = attempt_auto_dock(commander, (robot, other_robot))

    assert result is commander
    assert result.docked_robot_id == robot.id


def test_dock_picks_first_matching_friendly_robot_deterministically() -> None:
    first = _friendly_robot(robot_id="robot-a", height=4)
    second = _friendly_robot(robot_id="robot-b", height=4)
    commander = _free_commander(altitude=4)

    docked = attempt_auto_dock(commander, (first, second))

    assert docked.docked_robot_id == first.id


def test_auto_dock_with_event_emits_docked_event_on_transition() -> None:
    robot = _friendly_robot(height=4)
    commander = _free_commander(altitude=4)
    sequencer = EventSequencer()

    updated, event = auto_dock_with_event(commander, (robot,), tick=10, sequencer=sequencer)

    assert updated.mode is CommanderMode.DOCKED
    assert event is not None
    assert isinstance(event, CommanderDockedEvent)
    assert event.player_id == commander.player_id
    assert event.robot_id == robot.id
    assert event.x == commander.x
    assert event.y == commander.y
    assert event.altitude == commander.altitude
    assert event.tick == 10


def test_auto_dock_with_event_emits_nothing_on_no_op() -> None:
    commander = _free_commander(altitude=0)

    updated, event = auto_dock_with_event(commander, (), tick=10)

    assert updated is commander
    assert event is None


# --------------------------------------------------------------------------
# Enemy robot contact: no docking, no control transfer, no damage
# --------------------------------------------------------------------------


def test_enemy_robot_contact_never_docks() -> None:
    robot = _enemy_robot(height=4)
    commander = _free_commander(player_id=PLAYER_ONE, altitude=4, x=5, y=5)

    result = attempt_auto_dock(commander, (robot,))

    assert result is commander
    assert result.mode is CommanderMode.FREE
    assert result.docked_robot_id is None


def test_enemy_robot_contact_preserves_ownership_and_state_fields() -> None:
    robot = _enemy_robot(height=4, owner=PLAYER_TWO)
    commander = _free_commander(player_id=PLAYER_ONE, altitude=4, x=5, y=5, rising=True)

    result = attempt_auto_dock(commander, (robot,))

    # Nothing about the commander's own state (ownership/control/"strength" --
    # the commander has no strength/health field at all, being indestructible
    # per _specs/functional-spec.md §8) changes from resting on an enemy top.
    assert result == commander
    assert robot.owner == PLAYER_TWO  # robot ownership untouched


def test_enemy_and_friendly_robot_at_same_cell_impossible_but_ownership_gates_dock() -> None:
    # Enemy fixture present but not at the commander's resting cell/altitude:
    # only a *matching* friendly fixture docks; an unrelated enemy fixture
    # elsewhere never interferes with a legitimate friendly dock.
    friendly = _friendly_robot(robot_id="robot-friend", x=5, y=5, height=4)
    enemy_elsewhere = _enemy_robot(robot_id="robot-foe", x=9, y=9, height=4)
    commander = _free_commander(altitude=4, x=5, y=5)

    docked = attempt_auto_dock(commander, (enemy_elsewhere, friendly))

    assert docked.mode is CommanderMode.DOCKED
    assert docked.docked_robot_id == friendly.id


def test_docked_commander_mode_never_arises_from_enemy_fixture_list_alone() -> None:
    # Even with several enemy fixtures at plausible resting spots, no dock
    # ever occurs -- exhaustive sweep over several enemy heights/positions.
    commander = _free_commander(altitude=4, x=5, y=5)
    enemies = tuple(
        _enemy_robot(robot_id=f"enemy-{i}", x=5, y=5, height=h) for i, h in enumerate([2, 3, 4, 5, 6])
    )

    result = attempt_auto_dock(commander, enemies)

    assert result.mode is CommanderMode.FREE
    assert result.docked_robot_id is None


# --------------------------------------------------------------------------
# Following the docked robot
# --------------------------------------------------------------------------


def test_follow_docked_robot_sets_position_and_altitude_from_fixture() -> None:
    robot = _friendly_robot(x=7, y=9, height=6)
    commander = _docked_commander(x=5, y=5, altitude=4, docked_robot_id=robot.id)

    followed = follow_docked_robot(commander, robot)

    assert followed.x == 7
    assert followed.y == 9
    assert followed.altitude == 6
    assert followed.mode is CommanderMode.DOCKED
    assert followed.docked_robot_id == robot.id


def test_follow_docked_robot_tracks_moving_fixture_across_calls() -> None:
    robot = _friendly_robot(x=5, y=5, height=4)
    commander = _docked_commander(x=5, y=5, altitude=4, docked_robot_id=robot.id)

    step1 = follow_docked_robot(commander, robot)
    assert (step1.x, step1.y, step1.altitude) == (5, 5, 4)

    moved_robot = RobotFixture(id=robot.id, owner=robot.owner, x=6, y=5, height=4)
    step2 = follow_docked_robot(step1, moved_robot)
    assert (step2.x, step2.y, step2.altitude) == (6, 5, 4)

    climbed_robot = RobotFixture(id=robot.id, owner=robot.owner, x=6, y=7, height=8)
    step3 = follow_docked_robot(step2, climbed_robot)
    assert (step3.x, step3.y, step3.altitude) == (6, 7, 8)
    assert step3.mode is CommanderMode.DOCKED
    assert step3.docked_robot_id == robot.id


# --------------------------------------------------------------------------
# Independent movement disabled while docked
# --------------------------------------------------------------------------


def test_docked_movement_allowed_false_while_docked() -> None:
    commander = _docked_commander()
    assert docked_movement_allowed(commander) is False


def test_docked_movement_allowed_true_while_free() -> None:
    commander = _free_commander()
    assert docked_movement_allowed(commander) is True


# --------------------------------------------------------------------------
# Undocking (rising away)
# --------------------------------------------------------------------------


def test_undock_requires_docked_mode() -> None:
    commander = _free_commander(rising=True)
    state = _state_with(commander)

    result, event = apply_undock(commander, state, tick=4)

    assert result is commander
    assert event is None


def test_undock_requires_rising_intent() -> None:
    commander = _docked_commander(rising=False)
    state = _state_with(commander)

    result, event = apply_undock(commander, state, tick=4)

    assert result is commander
    assert event is None


def test_undock_transitions_to_free_and_ascends() -> None:
    commander = _docked_commander(altitude=4, rising=True, docked_robot_id=EntityId("robot-1"))
    state = _state_with(commander)

    updated, event = apply_undock(commander, state, tick=4)

    assert updated.mode is CommanderMode.FREE
    assert updated.docked_robot_id is None
    assert updated.altitude == 4 + DEFAULT_RULES.commander_ascent_step
    assert event is not None
    assert isinstance(event, CommanderUndockedEvent)
    assert event.player_id == commander.player_id
    assert event.robot_id == EntityId("robot-1")
    assert event.from_altitude == 4
    assert event.to_altitude == 4 + DEFAULT_RULES.commander_ascent_step
    assert event.tick == 4


def test_undock_leaves_a_structurally_valid_free_commander() -> None:
    commander = _docked_commander(rising=True)
    state = _state_with(commander)

    updated, _event = apply_undock(commander, state, tick=4)

    # Commander.__post_init__ already enforces this on construction, but
    # assert explicitly so a future refactor that broke the invariant would
    # fail loudly here too.
    assert updated.mode is CommanderMode.FREE
    assert updated.docked_robot_id is None


def test_undock_respects_max_altitude_clamp() -> None:
    commander = _docked_commander(altitude=DEFAULT_RULES.commander_max_altitude, rising=True)
    state = _state_with(commander)

    updated, event = apply_undock(commander, state, tick=4)

    assert updated.altitude == DEFAULT_RULES.commander_max_altitude
    assert updated.mode is CommanderMode.FREE
    assert event is not None
    assert event.from_altitude == event.to_altitude == DEFAULT_RULES.commander_max_altitude


def test_undock_blocked_ascent_still_transitions_mode() -> None:
    commander = _docked_commander(altitude=4, rising=True)
    state = _state_with(commander)

    def _always_blocked(state, mover, dest_altitude) -> bool:
        return False

    updated, event = apply_undock(commander, state, tick=4, vertical_check=_always_blocked)

    assert updated.mode is CommanderMode.FREE
    assert updated.docked_robot_id is None
    assert updated.altitude == 4  # ascent step itself was blocked
    assert event is not None
    assert event.from_altitude == event.to_altitude == 4


def test_undocked_commander_does_not_immediately_redock_without_moving() -> None:
    # After one undock step the commander is above the robot's resting
    # altitude, so a subsequent auto-dock check against the same fixture
    # does not immediately re-dock it.
    robot = _friendly_robot(height=4)
    commander = _docked_commander(altitude=4, rising=True, docked_robot_id=robot.id)
    state = _state_with(commander)

    updated, _event = apply_undock(commander, state, tick=4)
    assert updated.mode is CommanderMode.FREE

    rechecked = attempt_auto_dock(updated, (robot,))
    assert rechecked.mode is CommanderMode.FREE


# --------------------------------------------------------------------------
# Determinism / replay
# --------------------------------------------------------------------------


def test_replay_determinism_dock_follow_undock_sequence() -> None:
    """Same initial state + same fixture/tick sequence -> identical results.

    Runs the whole dock -> follow -> undock lifecycle twice from identical
    starting conditions and asserts every intermediate/final commander state
    and every emitted event are equal across both runs.
    """

    def run() -> tuple[list[Commander], list[object]]:
        commander = _free_commander(altitude=4, x=5, y=5)
        state = _state_with(commander)
        sequencer = EventSequencer()
        history: list[Commander] = []
        events: list[object] = []

        robot = _friendly_robot(x=5, y=5, height=4)
        commander, dock_event = auto_dock_with_event(commander, (robot,), tick=4, sequencer=sequencer)
        history.append(commander)
        if dock_event is not None:
            events.append(dock_event)

        robot_moved = RobotFixture(id=robot.id, owner=robot.owner, x=6, y=5, height=4)
        commander = follow_docked_robot(commander, robot_moved)
        history.append(commander)

        robot_climbed = RobotFixture(id=robot.id, owner=robot.owner, x=6, y=5, height=10)
        commander = follow_docked_robot(commander, robot_climbed)
        history.append(commander)

        commander = commander.with_rising(True)
        state = state.with_commanders((commander,))
        commander, undock_event = apply_undock(commander, state, tick=8, sequencer=sequencer)
        history.append(commander)
        if undock_event is not None:
            events.append(undock_event)

        return history, events

    history_a, events_a = run()
    history_b, events_b = run()

    assert history_a == history_b
    assert events_a == events_b


def test_replay_determinism_enemy_contact_sequence() -> None:
    def run() -> Commander:
        commander = _free_commander(altitude=4, x=5, y=5)
        enemy = _enemy_robot(x=5, y=5, height=4)
        return attempt_auto_dock(commander, (enemy,))

    result_a = run()
    result_b = run()

    assert result_a == result_b
    assert result_a.mode is CommanderMode.FREE
