"""Tests for authoritative commander state (issue #37)."""

from __future__ import annotations

import pytest

from nether_earth.commander import Commander, CommanderMode, create_commander
from nether_earth.ids import PLAYER_ONE, EntityId
from nether_earth.rules import EngineRules


def test_free_commander_without_docked_robot_id_is_valid() -> None:
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.FREE,
        x=3,
        y=4,
        altitude=10,
    )

    assert commander.mode is CommanderMode.FREE
    assert commander.docked_robot_id is None


def test_docked_commander_with_docked_robot_id_is_valid() -> None:
    robot_id = EntityId("robot-1")

    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=3,
        y=4,
        altitude=0,
        docked_robot_id=robot_id,
    )

    assert commander.mode is CommanderMode.DOCKED
    assert commander.docked_robot_id == robot_id


def test_docked_commander_without_docked_robot_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        Commander(
            player_id=PLAYER_ONE,
            mode=CommanderMode.DOCKED,
            x=0,
            y=0,
            altitude=0,
            docked_robot_id=None,
        )


def test_free_commander_with_docked_robot_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        Commander(
            player_id=PLAYER_ONE,
            mode=CommanderMode.FREE,
            x=0,
            y=0,
            altitude=0,
            docked_robot_id=EntityId("robot-1"),
        )


def test_commander_is_frozen() -> None:
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)

    with pytest.raises(AttributeError):
        commander.x = 5  # type: ignore[misc]


def test_create_commander_accepts_altitude_within_default_rules_bounds() -> None:
    commander = create_commander(PLAYER_ONE, CommanderMode.FREE, 0, 0, altitude=48)

    assert commander.altitude == 48


def test_create_commander_rejects_altitude_above_default_max() -> None:
    with pytest.raises(ValueError):
        create_commander(PLAYER_ONE, CommanderMode.FREE, 0, 0, altitude=49)


def test_create_commander_rejects_altitude_below_default_min() -> None:
    with pytest.raises(ValueError):
        create_commander(PLAYER_ONE, CommanderMode.FREE, 0, 0, altitude=-1)


def test_create_commander_honors_custom_rules_override() -> None:
    custom_rules = EngineRules(commander_min_altitude=10, commander_max_altitude=20)

    commander = create_commander(
        PLAYER_ONE, CommanderMode.FREE, 0, 0, altitude=15, rules=custom_rules
    )
    assert commander.altitude == 15

    with pytest.raises(ValueError):
        create_commander(PLAYER_ONE, CommanderMode.FREE, 0, 0, altitude=5, rules=custom_rules)

    with pytest.raises(ValueError):
        create_commander(PLAYER_ONE, CommanderMode.FREE, 0, 0, altitude=25, rules=custom_rules)


def test_create_commander_still_enforces_mode_invariants() -> None:
    with pytest.raises(ValueError):
        create_commander(PLAYER_ONE, CommanderMode.DOCKED, 0, 0, altitude=0)


# --------------------------------------------------------------------------
# with_docking (issue #40)
# --------------------------------------------------------------------------


def test_with_docking_transitions_free_to_docked() -> None:
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=1, y=2, altitude=4)
    robot_id = EntityId("robot-1")

    docked = commander.with_docking(CommanderMode.DOCKED, robot_id)

    assert docked.mode is CommanderMode.DOCKED
    assert docked.docked_robot_id == robot_id
    # every other field carried over unchanged
    assert docked.player_id == commander.player_id
    assert docked.x == commander.x
    assert docked.y == commander.y
    assert docked.altitude == commander.altitude
    assert docked.rising == commander.rising
    assert docked.horizontal_transition == commander.horizontal_transition
    assert docked.vertical_transition == commander.vertical_transition


def test_with_docking_transitions_docked_to_free() -> None:
    robot_id = EntityId("robot-1")
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=1,
        y=2,
        altitude=4,
        docked_robot_id=robot_id,
        rising=True,
    )

    freed = commander.with_docking(CommanderMode.FREE, None)

    assert freed.mode is CommanderMode.FREE
    assert freed.docked_robot_id is None
    assert freed.rising == commander.rising
    assert freed.x == commander.x
    assert freed.y == commander.y
    assert freed.altitude == commander.altitude


def test_with_docking_rejects_inconsistent_mode_and_robot_id() -> None:
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)

    with pytest.raises(ValueError):
        commander.with_docking(CommanderMode.DOCKED, None)

    with pytest.raises(ValueError):
        commander.with_docking(CommanderMode.FREE, EntityId("robot-1"))


def test_with_docking_does_not_mutate_original() -> None:
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)

    commander.with_docking(CommanderMode.DOCKED, EntityId("robot-1"))

    assert commander.mode is CommanderMode.FREE
    assert commander.docked_robot_id is None
