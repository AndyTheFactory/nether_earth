"""Replay fixture format and deterministic replay-running test helper.

This module implements the "replay fixtures" half of the "Snapshot and
replay fixtures" workstream (`_specs/milestones/01-deterministic-engine-
foundation.md`, issue #8). It records everything needed to reproduce a run
of the engine end to end -- map, scenario, seed, and a per-tick command
stream -- and provides a helper that replays that recording through the
canonical ``engine.new_game``/``engine.step`` contract.

Scope: this module is engine-local and transport-independent. It performs no
I/O and assumes no file format; "replay fixture" here means the in-memory
dataclass shape consumed by engine tests, not a persisted replay log (that is
explicitly out of scope -- see issue #8's non-goals, which defer filesystem
replay logging and the browser-facing protocol snapshot schema to M7).

Fixture shape convention: ``commands_by_tick`` is a ``Mapping[int, tuple[
Command, ...]]`` rather than a flat sorted sequence of ``(tick, Command)``
pairs. A mapping keyed by tick lets :func:`run_fixture` look up "the
commands for this tick" by direct key access while iterating
``range(tick_count)``, which mirrors how a real caller drives ``engine.step``
tick-by-tick and keeps the per-tick command batch grouping explicit in the
fixture's own type rather than requiring a groupby step before replay. Ticks
with no recorded commands simply key to an empty tuple (or are absent, which
:func:`run_fixture` treats identically via ``dict.get``).

``tick_count`` is an explicit field (not inferred from
``max(commands_by_tick)``) so a fixture can deliberately describe "run N
ticks with no further commands after the last scheduled one" -- inferring the
run length from the highest keyed tick would silently truncate that case.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from nether_earth import engine
from nether_earth.collision import RobotFixture
from nether_earth.commander import Commander
from nether_earth.commands import Command
from nether_earth.events import Event, order_events
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.robot import Robot
from nether_earth.scenario import Scenario
from nether_earth.state import GameState

__all__ = [
    "ReplayFixture",
    "run_fixture",
]


@dataclass(frozen=True, slots=True)
class ReplayFixture:
    """Everything needed to deterministically reproduce one engine run.

    - ``scenario``/``map_data``: passed through to ``engine.new_game``
      unchanged; ``map_data.map_id``/``version`` must match
      ``scenario.map_id``/``map_version`` (``new_game`` itself enforces
      this).
    - ``seed``: the match seed recorded on the resulting tick-0
      ``GameState`` (see ``engine.py``'s module docstring for the current
      scope of what consumes it -- no gameplay system draws randomness in
      M1).
    - ``commands_by_tick``: the commands to submit to ``engine.step`` for
      each tick, keyed by the 1-based tick number being advanced *to* (i.e.
      the commands passed to the ``step`` call that produces that tick). A
      tick with no entry is stepped with an empty command batch.
    - ``tick_count``: exactly how many ``engine.step`` calls :func:`run_fixture`
      performs. Explicit rather than inferred from
      ``max(commands_by_tick)`` (see module docstring).
    - ``commanders``/``initial_robots`` (added by issue #67, M5.8): the
      authoritative entities placed on the tick-0 state, so a fixture can
      describe a match that already has commanders and launched robots
      rather than only the empty bootstrap state ``engine.new_game``
      produces on its own. ``commanders`` is forwarded to
      ``engine.new_game``; ``initial_robots`` is applied to the resulting
      state via ``GameState.with_robots`` (which canonicalizes/validates
      them exactly as ``new_game`` would). Without these, no M5 behavior --
      movement, reservations/contention, orders, capture -- could be
      expressed as a replay fixture at all, since every one of them needs a
      robot on the map before tick 1.

      Note the deliberate distinction from ``robots`` below:
      ``initial_robots`` are real :class:`~nether_earth.robot.Robot`
      entities living on ``GameState``, whereas ``robots`` is the M3-era
      :class:`~nether_earth.collision.RobotFixture` stand-in used only for
      commander collision/auto-dock checks.
    - ``world``/``robots`` (added by issue #42, M3.6): passed straight
      through to every ``engine.step`` call this fixture drives, matching
      the optional, backward-compatible parameters #42 added to
      ``engine.step`` itself. ``world`` defaults to ``None`` (no collision
      checks applied to any commander, matching every pre-#42 fixture's
      behavior unchanged) and ``robots`` defaults to ``()``. This is
      required groundwork for issue #43's full commander scenario replay
      against a real map/collision setup.
    """

    scenario: Scenario
    map_data: BootstrapMap
    seed: int
    tick_count: int
    commands_by_tick: Mapping[int, tuple[Command, ...]] = field(default_factory=dict)
    world: WorldMap | None = None
    robots: tuple[RobotFixture, ...] = ()
    commanders: tuple[Commander, ...] = ()
    initial_robots: tuple[Robot, ...] = ()

    def __post_init__(self) -> None:
        if self.tick_count < 0:
            raise ValueError("tick_count must be non-negative")
        for tick in self.commands_by_tick:
            if tick < 1 or tick > self.tick_count:
                raise ValueError(
                    f"commands_by_tick key {tick!r} is outside the fixture's "
                    f"1..{self.tick_count} tick range"
                )


def run_fixture(fixture: ReplayFixture) -> tuple[GameState, tuple[Event, ...]]:
    """Replay ``fixture`` from tick 0 and return the final state and events.

    Calls ``engine.new_game(fixture.map_data, fixture.scenario,
    seed=fixture.seed, commanders=fixture.commanders)`` to build the initial
    state and attaches ``fixture.initial_robots`` to it, then calls
    ``engine.step`` once per tick in ``1..fixture.tick_count`` (in order),
    passing ``fixture.commands_by_tick.get(tick, ())`` as that call's command
    batch.

    Returns the final ``GameState`` after all ticks and the full ordered
    event sequence across the whole run: events from an earlier tick always
    precede events from a later tick (ticks are replayed strictly in order,
    one ``step`` call at a time), and within a tick, events are in the order
    ``engine.step`` already returns (itself canonical per
    ``events.order_events``). The overall multi-tick concatenation is
    re-passed through :func:`nether_earth.events.order_events` per tick
    before being appended, so the combined result is a well-defined,
    reproducible sequence independent of any incidental collection type used
    internally.

    Same ``fixture`` (by value) always produces an identical
    ``(final_state, events)`` pair -- this is the "replaying the same
    fixture repeatedly yields the same snapshot and event sequence"
    acceptance criterion from issue #8.
    """
    state = engine.new_game(
        fixture.map_data,
        fixture.scenario,
        seed=fixture.seed,
        commanders=fixture.commanders,
    )
    if fixture.initial_robots:
        state = state.with_robots(fixture.initial_robots)
    return run_from_state(
        state,
        fixture.commands_by_tick,
        fixture.tick_count,
        world=fixture.world,
        robots=fixture.robots,
    )


def run_from_state(
    state: GameState,
    commands_by_tick: Mapping[int, tuple[Command, ...]],
    tick_count: int,
    *,
    world: WorldMap | None = None,
    robots: tuple[RobotFixture, ...] = (),
) -> tuple[GameState, tuple[Event, ...]]:
    """Advance ``state`` by ``tick_count`` ticks, feeding ``commands_by_tick`` per tick.

    The single replay loop shared by :func:`run_fixture` (hand-composed
    fixtures) and by callers that already hold an authoritative tick-0
    state, e.g. one built by ``scenario.create_initial_state(scenario,
    world)`` for a persisted real-match artifact (M7 replay verification,
    M9.6). ``state.tick`` need not be 0; ticks are numbered from
    ``state.tick + 1`` so ``commands_by_tick`` keys stay absolute.
    """
    all_events: list[Event] = []
    for tick in range(state.tick + 1, state.tick + tick_count + 1):
        commands = commands_by_tick.get(tick, ())
        state, tick_events = engine.step(state, commands, world=world, robots=robots)
        all_events.extend(order_events(tick_events))
    return state, tuple(all_events)
