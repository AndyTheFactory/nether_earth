"""CR003.1 (#216): commander gravity is -2 per vertical update.

Owner decision 2026-09-22 (`_specs/resolved-questions.md`): ``commander_descent_step`` goes from the
Spectrum's 1 to 2, cadence unchanged (4 ticks). Gravity must still stop on
the surface under the ship when that surface is at an odd altitude, and
docking on an odd-height friendly robot top must still trigger.
"""

from __future__ import annotations

from nether_earth.collision import RobotFixture
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import CommanderVerticalUpdatedEvent
from nether_earth.docking import CommanderDockedEvent
from nether_earth.engine import new_game, step
from nether_earth.ids import PLAYER_ONE, EntityId
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.state import GameState
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType

V_TICKS = DEFAULT_RULES.commander_vertical_update_ticks
X, Y = 5, 5


def _world(*blockers: Blocker) -> WorldMap:
    return WorldMap(
        map_id="descent-map",
        version=1,
        width=12,
        height=12,
        terrain=TerrainGrid(width=12, height=12, cells={}, default=TerrainType.NORMAL),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _game(altitude: int) -> GameState:
    commander = Commander(
        player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=X, y=Y, altitude=altitude, rising=False
    )
    return new_game(
        BootstrapMap(map_id="descent-map", version=1, width=12, height=12),
        Scenario(id="descent", map_id="descent-map", map_version=1, player_starting_warbases=1),
        players=[PLAYER_ONE],
        seed=1,
        commanders=(commander,),
    )


def _run_updates(
    state: GameState, world: WorldMap, updates: int, robots: tuple[RobotFixture, ...] = ()
) -> tuple[GameState, list[int], list[object]]:
    """Step ``updates`` vertical cadences; return state, altitude after each, all events."""
    altitudes: list[int] = []
    events: list[object] = []
    for _ in range(updates):
        for _ in range(V_TICKS):
            state, tick_events = step(state, [], world=world, robots=robots)
            events.extend(tick_events)
        commander = state.commander_for(PLAYER_ONE)
        assert commander is not None
        altitudes.append(commander.altitude)
    return state, altitudes, events


def test_default_descent_step_is_two() -> None:
    assert DEFAULT_RULES.commander_descent_step == 2
    assert DEFAULT_RULES.commander_vertical_update_ticks == 4


def test_descent_from_max_altitude_reaches_ground_in_24_updates() -> None:
    state = _game(DEFAULT_RULES.commander_max_altitude)
    state, altitudes, events = _run_updates(state, _world(), 25)

    assert altitudes[:24] == list(range(46, -1, -2))
    assert altitudes[23] == 0 and altitudes[22] == 2
    assert altitudes[24] == 0  # resting: no further change
    vertical = [e for e in events if isinstance(e, CommanderVerticalUpdatedEvent)]
    assert len(vertical) == 24
    assert all(e.from_altitude - e.to_altitude == 2 for e in vertical)
    assert vertical[-1].tick == 24 * V_TICKS


def test_descent_lands_exactly_on_an_odd_height_box() -> None:
    box = Blocker(id=EntityId("box-3"), components=(Component(x=X, y=Y, height=3),))
    state = _game(8)
    state, altitudes, events = _run_updates(state, _world(box), 4)

    # 8 -> 6 -> 4, then a partial step onto the box top (3), never below.
    assert altitudes == [6, 4, 3, 3]
    vertical = [e for e in events if isinstance(e, CommanderVerticalUpdatedEvent)]
    assert [(e.from_altitude, e.to_altitude) for e in vertical] == [(8, 6), (6, 4), (4, 3)]


def test_descent_docks_on_an_odd_height_friendly_robot_top() -> None:
    robot = RobotFixture(id=EntityId("robot-5"), owner=PLAYER_ONE, x=X, y=Y, height=5)
    assert robot.top == 5
    state = _game(8)
    state, altitudes, events = _run_updates(state, _world(), 2, robots=(robot,))

    # 8 -> 6, then 6 - 2 = 4 would pass the top: clamp to 5 and dock.
    assert altitudes == [6, 5]
    docked = [e for e in events if isinstance(e, CommanderDockedEvent)]
    assert len(docked) == 1 and docked[0].robot_id == robot.id
    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None
    assert commander.mode is CommanderMode.DOCKED
    assert commander.docked_robot_id == robot.id
