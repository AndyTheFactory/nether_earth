"""Authoritative tick clock and game-time conversion helpers.

The game engine has no wall-clock timers. Every notion of elapsed game time
is derived exclusively from the authoritative integer simulation tick
counter maintained by the match/runtime layer (owned elsewhere, not by this
module). See ``_specs/functional-spec.md`` §6 "Game clock" and
``_specs/technical-spec.md`` §5.1-5.3 "Simulation timing and game clock" for
the locked values and rules this module implements.

Locked values:
    - 1 tick = 50 ms real time (20 Hz authoritative simulation rate).
    - 1 in-game hour = 120 ticks.
    - 12 in-game hours = 1,440 ticks.
    - 1 in-game day = 2,880 ticks.

Nothing in this module reads wall-clock time (no ``time.time()``, no
``datetime.now()``); every helper takes an explicit integer tick argument.
"""

from __future__ import annotations

__all__ = [
    "TICKS_PER_GAME_DAY",
    "TICKS_PER_GAME_HOUR",
    "TICKS_PER_GAME_TWELVE_HOURS",
    "TICK_RATE_HZ",
    "game_days_elapsed",
    "game_hours_elapsed",
    "ticks_to_game_days_floor",
    "ticks_to_game_hours_floor",
]

#: Authoritative simulation rate: 1 tick = 50 ms real time.
TICK_RATE_HZ = 20

#: Number of simulation ticks in one in-game hour.
TICKS_PER_GAME_HOUR = 120

#: Number of simulation ticks in twelve in-game hours (e.g. factory capture
#: window duration; the capture rule itself is implemented elsewhere).
TICKS_PER_GAME_TWELVE_HOURS = 1440

#: Number of simulation ticks in one in-game day.
TICKS_PER_GAME_DAY = 2880


def _validate_tick(tick: int) -> None:
    if tick < 0:
        raise ValueError(f"tick must be non-negative, got {tick!r}")


def game_hours_elapsed(tick: int) -> float:
    """Return the fractional number of in-game hours elapsed at ``tick``.

    This mirrors the example helper in ``_specs/technical-spec.md`` §5.2 and
    is intended for display/telemetry purposes. Rule checks that must be
    exact at hour boundaries should use :func:`ticks_to_game_hours_floor`
    instead, to avoid floating-point comparisons.
    """
    _validate_tick(tick)
    return tick / TICKS_PER_GAME_HOUR


def game_days_elapsed(tick: int) -> float:
    """Return the fractional number of in-game days elapsed at ``tick``.

    Same rationale as :func:`game_hours_elapsed`: use
    :func:`ticks_to_game_days_floor` for exact, integer boundary checks.
    """
    _validate_tick(tick)
    return tick / TICKS_PER_GAME_DAY


def ticks_to_game_hours_floor(tick: int) -> int:
    """Return the whole number of complete in-game hours elapsed at ``tick``.

    Integer-only and exact at hour boundaries: tick 119 -> 0, tick 120 -> 1.
    Intended for gameplay rule checks that must not use floating-point
    comparisons.
    """
    _validate_tick(tick)
    return tick // TICKS_PER_GAME_HOUR


def ticks_to_game_days_floor(tick: int) -> int:
    """Return the whole number of complete in-game days elapsed at ``tick``.

    Integer-only and exact at day boundaries: tick 2879 -> 0, tick 2880 -> 1.
    """
    _validate_tick(tick)
    return tick // TICKS_PER_GAME_DAY
