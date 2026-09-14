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
