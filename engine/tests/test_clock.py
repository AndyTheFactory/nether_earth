"""Tests for the authoritative tick clock and game-time conversion helpers.

These tests must never read wall-clock time (no ``time.time()``, no
``datetime.now()``); every case exercises the clock module with explicit
integer tick values only.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from nether_earth import clock


def test_constants_match_locked_spec_values() -> None:
    assert clock.TICK_RATE_HZ == 20
    assert clock.TICKS_PER_GAME_HOUR == 120
    assert clock.TICKS_PER_GAME_TWELVE_HOURS == 1440
    assert clock.TICKS_PER_GAME_DAY == 2880


def test_game_hours_elapsed_at_tick_zero() -> None:
    assert clock.game_hours_elapsed(0) == 0.0


def test_game_hours_elapsed_hour_boundary() -> None:
    # Just before the hour boundary.
    assert clock.game_hours_elapsed(119) == pytest.approx(119 / 120)
    assert clock.game_hours_elapsed(119) < 1.0
    # Exactly at the hour boundary.
    assert clock.game_hours_elapsed(120) == 1.0


def test_game_hours_elapsed_multi_hour_value() -> None:
    # 300 ticks = 2.5 in-game hours.
    assert clock.game_hours_elapsed(300) == pytest.approx(2.5)


def test_game_days_elapsed_day_boundary() -> None:
    assert clock.game_days_elapsed(2879) < 1.0
    assert clock.game_days_elapsed(2879) == pytest.approx(2879 / 2880)
    assert clock.game_days_elapsed(2880) == 1.0


def test_game_days_elapsed_multi_day_value() -> None:
    # 7200 ticks = 2.5 in-game days.
    assert clock.game_days_elapsed(7200) == pytest.approx(2.5)


def test_ticks_to_game_hours_floor_exact_at_boundaries() -> None:
    assert clock.ticks_to_game_hours_floor(0) == 0
    assert clock.ticks_to_game_hours_floor(119) == 0
    assert clock.ticks_to_game_hours_floor(120) == 1
    assert clock.ticks_to_game_hours_floor(239) == 1
    assert clock.ticks_to_game_hours_floor(240) == 2


def test_ticks_to_game_hours_floor_twelve_hour_and_multi_hour() -> None:
    assert clock.ticks_to_game_hours_floor(clock.TICKS_PER_GAME_TWELVE_HOURS) == 12
    assert clock.ticks_to_game_hours_floor(1439) == 11
    # 25 in-game hours worth of ticks.
    assert clock.ticks_to_game_hours_floor(25 * 120) == 25


def test_ticks_to_game_days_floor_exact_at_boundaries() -> None:
    assert clock.ticks_to_game_days_floor(0) == 0
    assert clock.ticks_to_game_days_floor(2879) == 0
    assert clock.ticks_to_game_days_floor(2880) == 1
    assert clock.ticks_to_game_days_floor(5759) == 1
    assert clock.ticks_to_game_days_floor(5760) == 2


def test_ticks_to_game_days_floor_multi_day_value() -> None:
    # 3 in-game days worth of ticks.
    assert clock.ticks_to_game_days_floor(3 * 2880) == 3


@pytest.mark.parametrize(
    "func",
    [
        clock.game_hours_elapsed,
        clock.game_days_elapsed,
        clock.ticks_to_game_hours_floor,
        clock.ticks_to_game_days_floor,
    ],
)
def test_negative_tick_raises_value_error(func: object) -> None:
    with pytest.raises(ValueError):
        func(-1)  # type: ignore[operator]


def test_module_does_not_reference_wall_clock_apis() -> None:
    """Static guard: the clock module must not import or call wall-clock APIs."""
    source = Path(inspect.getfile(clock)).read_text()
    tree = ast.parse(source)

    forbidden_names = {"time", "datetime"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_names, (
                    f"clock.py must not import {alias.name!r}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] not in forbidden_names, (
                f"clock.py must not import from {module!r}"
            )
