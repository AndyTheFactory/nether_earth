"""Autonomous robots fire only on their own robot update (CR002.19, #197).

Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`):
`Lb154_robot_ai_update` returns until ``ROBOT_STRICT_CYCLES_TO_NEXT_UPDATE``
reaches 0; a firing update sets the desired direction to 0 (no move), and
`Lb20d_move_robot` -> `Lb5f3_determine_speed_based_on_terrain` reloads the
counter from `Lb61d_robot_movement_speed_table` for the cell the robot is on.
See `_specs/open-questions.md` §8.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest

from nether_earth.autonomous_combat import (
    autonomous_update_due,
    autonomous_update_period_ticks,
    consume_engagement_intent,
    gate_order_requests,
)
from nether_earth.combat import FireCommand, ProjectileFiredEvent
from nether_earth.engine import step
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.movement import RobotMoveRequest
from nether_earth.orders import (
    EngagementIntent,
    EngagementTargetKind,
    OrderEvaluation,
    OrderStatus,
    SearchDestroy,
    SearchDestroyTarget,
    StopAndDefend,
)
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.terrain import TerrainGrid, TerrainType

#: 1 game cycle = 4 ticks (`_specs/open-questions.md` §4).
CYCLE = 4

#: `Lb61d_robot_movement_speed_table` in cycles, per (chassis, terrain) class
#: the chassis can stand on (§4's table; ditch is altitude 0, so flat speed).
SPEED_TABLE_CYCLES = {
    (ModuleIdentity.BIPOD, TerrainType.NORMAL): 6,
    (ModuleIdentity.BIPOD, TerrainType.ROUGH): 8,
    (ModuleIdentity.TRACKS, TerrainType.NORMAL): 4,
    (ModuleIdentity.TRACKS, TerrainType.ROUGH): 6,
    (ModuleIdentity.TRACKS, TerrainType.MOUNTAIN): 7,
    (ModuleIdentity.ANTI_GRAV, TerrainType.NORMAL): 3,
    (ModuleIdentity.ANTI_GRAV, TerrainType.ROUGH): 3,
    (ModuleIdentity.ANTI_GRAV, TerrainType.MOUNTAIN): 4,
    (ModuleIdentity.ANTI_GRAV, TerrainType.DITCH): 3,
}

HUNTER_X, ROW = 10, 10
WEAPONS = (ModuleIdentity.CANNON,)


def _world(cells: dict[tuple[int, int], TerrainType] | None = None) -> WorldMap:
    return WorldMap(
        map_id="autonomous-fire-update-map",
        version=1,
        width=60,
        height=20,
        terrain=TerrainGrid(width=60, height=20, cells=cells or {}, default=TerrainType.NORMAL),
        war_bases=(),
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str,
    owner: PlayerId,
    x: int,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    *,
    order: object | None = None,
    strength: int = 100,
    weapons: tuple[ModuleIdentity, ...] = WEAPONS,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=ROW,
        build=build,
        stack=stack,
        height=height,
        order=order,  # type: ignore[arg-type]
        strength=strength,
    )


def _hunter(chassis: ModuleIdentity = ModuleIdentity.BIPOD, **kwargs: object) -> Robot:
    return _robot(
        "robot-a",
        PLAYER_ONE,
        HUNTER_X,
        chassis,
        order=SearchDestroy(SearchDestroyTarget.ROBOT),
        **kwargs,  # type: ignore[arg-type]
    )


def _prey(x: int) -> Robot:
    """No order, so it never fires back; strong enough to survive every hit.

    Three weapons make it tall enough to stop a shot (height >=
    ``normal_projectile_altitude``), so every shot ends on the fire tick and
    the hunter's bullet channel is free again at its next update.
    """
    return _robot(
        "robot-z",
        PLAYER_TWO,
        x,
        ModuleIdentity.BIPOD,
        strength=1_000_000,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
    )


def _state(robots: tuple[Robot, ...]) -> GameState:
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), seed=7, robots=list(robots))


def _run(
    state: GameState, world: WorldMap, ticks: int
) -> tuple[GameState, list[int], list[tuple[int, int]]]:
    """Step ``ticks`` ticks; return the hunter's fire ticks and its per-tick cell."""
    fire_ticks: list[int] = []
    cells: list[tuple[int, int]] = []
    for _ in range(ticks):
        state, events = step(state, (), world)
        fire_ticks += [e.tick for e in _fired_by(events, "robot-a")]
        hunter = state.robot_for(EntityId("robot-a"))
        assert hunter is not None
        cells.append((hunter.x, hunter.y))
    return state, fire_ticks, cells


def _fired_by(events: tuple[Event, ...], robot_id: str) -> list[ProjectileFiredEvent]:
    return [
        e
        for e in events
        if isinstance(e, ProjectileFiredEvent) and e.source_robot_id == EntityId(robot_id)
    ]


# --------------------------------------------------------------------------
# Through the engine step
# --------------------------------------------------------------------------


def test_adjacent_search_destroy_bipod_on_flat_fires_once_per_update_and_never_moves() -> None:
    world = _world()
    state = _state((_hunter(), _prey(HUNTER_X + 1)))

    _state_after, fire_ticks, cells = _run(state, world, 80)

    period = 6 * CYCLE  # bipod, flat
    assert fire_ticks == [1, 1 + period, 1 + 2 * period, 1 + 3 * period]
    assert set(cells) == {(HUNTER_X, ROW)}


@pytest.mark.parametrize(
    ("chassis", "terrain"), sorted(SPEED_TABLE_CYCLES, key=lambda k: (k[0].value, k[1].value))
)
def test_fire_period_follows_the_speed_table_for_the_hunters_own_cell(
    chassis: ModuleIdentity, terrain: TerrainType
) -> None:
    world = _world({(HUNTER_X, ROW): terrain})
    state = _state((_hunter(chassis), _prey(HUNTER_X + 1)))

    _state_after, fire_ticks, cells = _run(state, world, 3 * 9 * CYCLE)

    period = SPEED_TABLE_CYCLES[(chassis, terrain)] * CYCLE
    assert fire_ticks[:3] == [1, 1 + period, 1 + 2 * period]
    assert all(b - a == period for a, b in pairwise(fire_ticks))
    assert set(cells) == {(HUNTER_X, ROW)}
    hunter = state.robot_for(EntityId("robot-a"))
    assert hunter is not None
    assert autonomous_update_period_ticks(hunter, world) == period


def test_a_robot_with_a_shot_on_its_update_fires_instead_of_moving() -> None:
    """Target 4 cells away in line: navigation would step, but every update fires."""
    world = _world()
    state = _state((_hunter(), _prey(HUNTER_X + 4)))

    _state_after, fire_ticks, cells = _run(state, world, 60)

    assert fire_ticks == [1, 25, 49]
    assert set(cells) == {(HUNTER_X, ROW)}


def test_a_robot_without_a_shot_keeps_moving_at_its_move_cadence() -> None:
    world = _world()
    far = HUNTER_X + DEFAULT_RULES.cannon_range_cells + 10
    state = _state((_hunter(), _prey(far)))

    _state_after, fire_ticks, cells = _run(state, world, 3 * 6 * CYCLE + 1)

    assert fire_ticks == []
    assert cells[-1] == (HUNTER_X + 3, ROW)


def test_direct_fire_is_not_tied_to_the_robot_update() -> None:
    """Combat-mode fire keeps CR002.2's once-per-cycle rule (owner decision, #197)."""
    world = _world()
    shooter = _robot("robot-a", PLAYER_ONE, HUNTER_X)  # no order: not autonomous
    state = _state((shooter, _prey(HUNTER_X + 1)))

    fire_ticks: list[int] = []
    for _ in range(16):
        command = FireCommand(
            player=PLAYER_ONE,
            sequence=0,
            entity_id=shooter.entity_id,
            weapon=ModuleIdentity.CANNON,
            target_x=HUNTER_X + 1,
            target_y=ROW,
        )
        state, events = step(state, (command,), world)
        fire_ticks += [e.tick for e in _fired_by(events, "robot-a")]

    assert fire_ticks == [1, 4, 8, 12, 16]


# --------------------------------------------------------------------------
# The gates in isolation
# --------------------------------------------------------------------------


def _intent(robot: Robot, target_x: int) -> EngagementIntent:
    return EngagementIntent(
        robot_id=robot.entity_id,
        player=robot.owner,
        target_kind=EngagementTargetKind.ROBOT,
        target_id=EntityId("robot-z"),
        target_x=target_x,
        target_y=ROW,
        distance_cells=abs(target_x - robot.x),
        weapons=WEAPONS,
    )


def test_update_is_due_only_without_a_move_in_flight_and_a_period_after_the_last_shot() -> None:
    world = _world()
    robot = _hunter()
    period = 6 * CYCLE

    assert autonomous_update_due(robot, world, tick=5)
    fired = replace(robot, last_fire_tick=5)
    assert not autonomous_update_due(fired, world, tick=5 + period - 1)
    assert autonomous_update_due(fired, world, tick=5 + period)

    moving = robot.with_movement(
        RobotMoveTransition(
            entity_id=robot.entity_id,
            from_x=HUNTER_X,
            from_y=ROW,
            to_x=HUNTER_X + 1,
            to_y=ROW,
            started_tick=3,
            duration_ticks=period,
        )
    )
    assert not autonomous_update_due(moving, world, tick=10)


def test_consume_does_not_fire_between_updates() -> None:
    world = _world()
    robot = replace(_hunter(), last_fire_tick=1)
    state = _state((robot, _prey(HUNTER_X + 1)))

    unchanged, events = consume_engagement_intent(_intent(robot, HUNTER_X + 1), state, world, 24)
    assert unchanged is state
    assert events == ()

    _fired_state, events = consume_engagement_intent(
        _intent(robot, HUNTER_X + 1), state, world, 25
    )
    assert _fired_by(events, "robot-a") != []


def _evaluation(robot: Robot, intent: EngagementIntent | None) -> OrderEvaluation:
    return OrderEvaluation(
        robot_id=robot.entity_id,
        order=StopAndDefend(),
        previous=StopAndDefend(),
        status=OrderStatus.ACTIVE,
        request=RobotMoveRequest(entity_id=robot.entity_id, dx=1, dy=0),
        intent=intent,
    )


def test_gate_drops_a_move_on_a_firing_update_and_between_updates() -> None:
    world = _world()
    robot = _hunter()
    prey = _prey(HUNTER_X + 4)
    state = _state((robot, prey))

    # A firing update does not move.
    assert gate_order_requests((_evaluation(robot, _intent(robot, prey.x)),), state, world, 1) == ()
    # No shot (no intent): the update moves.
    free = _evaluation(robot, None)
    assert gate_order_requests((free,), state, world, 1) == (free.request,)

    # After a shot, no move until the next update.
    fired_state = state.with_robots((replace(robot, last_fire_tick=1), prey))
    assert gate_order_requests((free,), fired_state, world, 24) == ()
    assert gate_order_requests((free,), fired_state, world, 25) == (free.request,)


def test_robot_copies_keep_last_fire_tick() -> None:
    """Regression: the ``with_*`` copies dropped ``last_fire_tick`` (#191 follow-up)."""
    robot = replace(_hunter(), last_fire_tick=9)
    assert robot.with_active_projectile(EntityId("p")).last_fire_tick == 9
    assert robot.with_strength(50).last_fire_tick == 9
    assert robot.with_order(None).last_fire_tick == 9
    assert robot.with_position(1, 1).last_fire_tick == 9
    assert robot.with_movement(None).last_fire_tick == 9
