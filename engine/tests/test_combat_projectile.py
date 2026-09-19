"""Tests for projectile creation and advancement (issue #73, M6.4)."""

from __future__ import annotations

from nether_earth.combat import (
    FireRejectionReason,
    FireRequest,
    Projectile,
    ProjectileTerminationReason,
    advance_projectiles,
    apply_fire,
    is_projectile_advance_tick,
    resolve_fire_direction,
)
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType


def _world(
    width: int = 20,
    height: int = 20,
    blockers: tuple[Blocker, ...] = (),
) -> WorldMap:
    return WorldMap(
        map_id="combat-projectile-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width,
            height=height,
            cells={},
            default=TerrainType.NORMAL,
        ),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str = "robot-player-one-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 5,
    y: int = 5,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    electronics: ModuleIdentity | None = None,
    active_projectile_id: EntityId | None = None,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons, electronics=electronics)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        active_projectile_id=active_projectile_id,
    )


def _state(robots: tuple[Robot, ...] = (), projectiles: tuple[Projectile, ...] = ()) -> GameState:
    return create_game_state(
        0,
        (PLAYER_ONE, PLAYER_TWO),
        robots=list(robots),
        projectiles=list(projectiles),
    )


def _request(
    robot_id: str = "robot-player-one-1",
    player: PlayerId = PLAYER_ONE,
    weapon: ModuleIdentity = ModuleIdentity.CANNON,
    target_x: int = 6,
    target_y: int = 5,
) -> FireRequest:
    return FireRequest(
        robot_id=EntityId(robot_id),
        player=player,
        weapon=weapon,
        target_x=target_x,
        target_y=target_y,
    )


def _projectile(
    entity_id: str = "projectile-1",
    owner: PlayerId = PLAYER_ONE,
    source_robot_id: str = "robot-player-one-1",
    weapon: ModuleIdentity = ModuleIdentity.CANNON,
    x: int = 5,
    y: int = 5,
    dx: int = 1,
    dy: int = 0,
    travelled_cells: int = 0,
    max_range_cells: int = 20,
    created_tick: int = 0,
    z: int = 10,
) -> Projectile:
    return Projectile(
        id=EntityId(entity_id),
        owner=owner,
        source_robot_id=EntityId(source_robot_id),
        weapon=weapon,
        x=x,
        y=y,
        z=z,
        dx=dx,
        dy=dy,
        travelled_cells=travelled_cells,
        max_range_cells=max_range_cells,
        created_tick=created_tick,
    )


# --- resolve_fire_direction ----------------------------------------------------


def test_resolve_fire_direction_east() -> None:
    robot = _robot(x=5, y=5)
    direction = resolve_fire_direction(robot, _request(target_x=9, target_y=5))
    assert direction == (1, 0)


def test_resolve_fire_direction_west() -> None:
    robot = _robot(x=5, y=5)
    direction = resolve_fire_direction(robot, _request(target_x=1, target_y=5))
    assert direction == (-1, 0)


def test_resolve_fire_direction_south() -> None:
    robot = _robot(x=5, y=5)
    direction = resolve_fire_direction(robot, _request(target_x=5, target_y=9))
    assert direction == (0, 1)


def test_resolve_fire_direction_north() -> None:
    robot = _robot(x=5, y=5)
    direction = resolve_fire_direction(robot, _request(target_x=5, target_y=1))
    assert direction == (0, -1)


def test_resolve_fire_direction_ties_break_toward_x() -> None:
    robot = _robot(x=5, y=5)
    # |dx| == |dy| == 3; x-axis should win.
    direction = resolve_fire_direction(robot, _request(target_x=8, target_y=8))
    assert direction == (1, 0)

    direction = resolve_fire_direction(robot, _request(target_x=2, target_y=2))
    assert direction == (-1, 0)


def test_resolve_fire_direction_none_when_target_is_own_cell() -> None:
    robot = _robot(x=5, y=5)
    direction = resolve_fire_direction(robot, _request(target_x=5, target_y=5))
    assert direction is None


# --- apply_fire ------------------------------------------------------------------


def test_apply_fire_creates_projectile_with_correct_range() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,))
    state = _state((robot,))
    world = _world()

    new_state, result, event = apply_fire(_request(), state, world, tick=10)

    assert result.accepted
    assert event is not None
    assert len(new_state.projectiles) == 1
    projectile = new_state.projectiles[0]
    assert projectile.owner == PLAYER_ONE
    assert projectile.source_robot_id == robot.entity_id
    assert projectile.weapon == ModuleIdentity.CANNON
    assert projectile.x == robot.x
    assert projectile.y == robot.y
    assert projectile.z == DEFAULT_RULES.normal_projectile_altitude
    assert projectile.dx == 1
    assert projectile.dy == 0
    assert projectile.travelled_cells == 0
    assert projectile.max_range_cells == DEFAULT_RULES.cannon_range_cells
    assert projectile.created_tick == 10

    updated_robot = new_state.robot_for(robot.entity_id)
    assert updated_robot is not None
    assert updated_robot.active_projectile_id == projectile.id


def test_apply_fire_missile_range() -> None:
    robot = _robot(weapons=(ModuleIdentity.MISSILE,))
    state = _state((robot,))
    world = _world()

    new_state, result, _event = apply_fire(
        _request(weapon=ModuleIdentity.MISSILE), state, world, tick=1
    )

    assert result.accepted
    assert new_state.projectiles[0].max_range_cells == DEFAULT_RULES.missile_range_cells


def test_apply_fire_range_includes_electronics_bonus() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,), electronics=ModuleIdentity.ELECTRONICS)
    state = _state((robot,))
    world = _world()

    new_state, result, _event = apply_fire(_request(), state, world, tick=1)

    assert result.accepted
    expected = DEFAULT_RULES.cannon_range_cells + DEFAULT_RULES.electronics_range_bonus_cells
    assert new_state.projectiles[0].max_range_cells == expected


def test_apply_fire_without_electronics_has_no_bonus() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,), electronics=None)
    state = _state((robot,))
    world = _world()

    new_state, result, _event = apply_fire(_request(), state, world, tick=1)

    assert result.accepted
    assert new_state.projectiles[0].max_range_cells == DEFAULT_RULES.cannon_range_cells


def test_apply_fire_rejection_passes_through_unchanged_state() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,))
    state = _state((robot,))
    world = _world()

    new_state, result, event = apply_fire(
        _request(weapon=ModuleIdentity.MISSILE), state, world, tick=1
    )

    assert not result.accepted
    assert result.reason is FireRejectionReason.WEAPON_NOT_FITTED
    assert new_state is state
    assert event is None
    assert new_state.projectiles == ()


def test_apply_fire_channel_occupied_rejection() -> None:
    robot = _robot(
        weapons=(ModuleIdentity.CANNON,),
        active_projectile_id=EntityId("projectile-existing"),
    )
    state = _state((robot,))
    world = _world()

    new_state, result, event = apply_fire(_request(), state, world, tick=1)

    assert not result.accepted
    assert result.reason is FireRejectionReason.CHANNEL_OCCUPIED
    assert new_state is state
    assert event is None


def test_apply_fire_rejects_degenerate_aim_at_own_cell() -> None:
    robot = _robot(x=5, y=5, weapons=(ModuleIdentity.CANNON,))
    state = _state((robot,))
    world = _world()

    new_state, result, event = apply_fire(
        _request(target_x=5, target_y=5), state, world, tick=1
    )

    assert not result.accepted
    assert result.reason is FireRejectionReason.TARGET_OUT_OF_RANGE
    assert new_state is state
    assert event is None
    assert new_state.projectiles == ()


def test_apply_fire_nuclear_accepted_creates_no_projectile_and_no_channel_touch() -> None:
    robot = _robot(weapons=(ModuleIdentity.NUCLEAR,))
    state = _state((robot,))
    world = _world()

    new_state, result, event = apply_fire(
        _request(weapon=ModuleIdentity.NUCLEAR), state, world, tick=1
    )

    assert result.accepted
    assert event is None
    assert new_state is state
    assert new_state.projectiles == ()
    updated_robot = new_state.robot_for(robot.entity_id)
    assert updated_robot is not None
    assert updated_robot.active_projectile_id is None


# --- is_projectile_advance_tick --------------------------------------------------


def test_is_projectile_advance_tick_matches_cadence() -> None:
    rules = DEFAULT_RULES
    assert rules.projectile_advance_ticks == 4
    assert is_projectile_advance_tick(0, rules) is False
    assert is_projectile_advance_tick(1, rules) is False
    assert is_projectile_advance_tick(4, rules) is True
    assert is_projectile_advance_tick(8, rules) is True
    assert is_projectile_advance_tick(9, rules) is False


def test_is_projectile_advance_tick_custom_cadence() -> None:
    rules = EngineRules(projectile_advance_ticks=5)
    assert is_projectile_advance_tick(5, rules) is True
    assert is_projectile_advance_tick(10, rules) is True
    assert is_projectile_advance_tick(6, rules) is False


# --- advance_projectiles ---------------------------------------------------------


def test_advance_projectiles_noop_on_non_cadence_tick() -> None:
    projectile = _projectile()
    state = _state((_robot(),), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=1)

    assert new_state is state
    assert events == ()


def test_advance_projectiles_moves_projectile_on_cadence_tick() -> None:
    projectile = _projectile(x=5, y=5, dx=1, dy=0, travelled_cells=0, max_range_cells=20)
    state = _state((_robot(),), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    assert events == ()
    assert len(new_state.projectiles) == 1
    updated = new_state.projectiles[0]
    assert updated.x == 6
    assert updated.y == 5
    assert updated.travelled_cells == 1


def test_advance_projectiles_terminates_on_range_exhaustion() -> None:
    """A projectile that already reached its max range (on a prior tick)
    terminates in place -- no move is attempted, and the reported
    coordinates are its actual current (resting) cell, not a new one."""
    robot = _robot(active_projectile_id=EntityId("projectile-1"))
    projectile = _projectile(
        entity_id="projectile-1",
        source_robot_id="robot-player-one-1",
        x=8,
        y=5,
        dx=1,
        dy=0,
        travelled_cells=3,
        max_range_cells=3,
    )
    state = _state((robot,), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    assert new_state.projectiles == ()
    assert len(events) == 1
    event = events[0]
    assert event.reason is ProjectileTerminationReason.RANGE_EXHAUSTED
    assert event.hit_robot_id is None
    # Reported coordinates are the projectile's actual resting cell (it did
    # not move this tick), not one cell further along its direction.
    assert event.x == 8
    assert event.y == 5

    updated_robot = new_state.robot_for(robot.entity_id)
    assert updated_robot is not None
    assert updated_robot.active_projectile_id is None


def test_advance_projectiles_reaches_and_survives_exactly_its_max_range_cell() -> None:
    """A projectile must still advance INTO its Nth cell (travelled_cells
    becoming exactly max_range_cells) before expiring -- the target cell at
    exactly the weapon's nominal range must remain reachable/hittable, not
    permanently one cell short of it."""
    projectile = _projectile(
        entity_id="projectile-1", x=5, y=5, dx=1, dy=0, travelled_cells=2, max_range_cells=3
    )
    state = _state((_robot(active_projectile_id=EntityId("projectile-1")),), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    # Still in flight: reached travelled_cells == max_range_cells, but that
    # is the arrival tick, not an expiry tick -- it must not terminate yet.
    assert events == ()
    assert len(new_state.projectiles) == 1
    survivor = new_state.projectiles[0]
    assert survivor.x == 6
    assert survivor.y == 5
    assert survivor.travelled_cells == 3

    # On the FOLLOWING advance call, it now expires in place.
    final_state, final_events = advance_projectiles(new_state, world, tick=8)
    assert final_state.projectiles == ()
    assert len(final_events) == 1
    assert final_events[0].reason is ProjectileTerminationReason.RANGE_EXHAUSTED
    assert final_events[0].x == 6
    assert final_events[0].y == 5


def test_advance_projectiles_hits_robot_standing_exactly_at_max_range_cell() -> None:
    """A robot standing exactly ``max_range_cells`` cells from the firer's
    origin must be reachable/hittable -- reproduces the reviewer's cannon
    range-20 probe with a smaller range for test speed."""
    firer = _robot(entity_id="robot-firer", active_projectile_id=EntityId("projectile-1"), x=5, y=5)
    target = _robot(entity_id="robot-target", owner=PLAYER_TWO, x=8, y=5)
    tall_target = Robot(
        entity_id=target.entity_id,
        owner=target.owner,
        x=target.x,
        y=target.y,
        build=target.build,
        stack=target.stack,
        height=DEFAULT_RULES.normal_projectile_altitude,
    )
    # travelled_cells=2, max_range_cells=3: the candidate cell (x=8) is
    # exactly the Nth (3rd) cell travelled -- new_travelled becomes 3,
    # equal to max_range_cells, and collision must still be checked there.
    projectile = _projectile(
        entity_id="projectile-1",
        source_robot_id="robot-firer",
        x=7,
        y=5,
        dx=1,
        dy=0,
        travelled_cells=2,
        max_range_cells=3,
    )
    state = _state((firer, tall_target), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    assert new_state.projectiles == ()
    assert len(events) == 1
    event = events[0]
    assert event.reason is ProjectileTerminationReason.ROBOT_HIT
    assert event.hit_robot_id == tall_target.entity_id
    assert event.x == 8
    assert event.y == 5


def test_advance_projectiles_terminates_on_out_of_bounds() -> None:
    robot = _robot(active_projectile_id=EntityId("projectile-1"), x=19, y=5)
    projectile = _projectile(
        entity_id="projectile-1",
        source_robot_id="robot-player-one-1",
        x=19,
        y=5,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=20,
    )
    state = _state((robot,), (projectile,))
    world = _world(width=20, height=20)

    new_state, events = advance_projectiles(state, world, tick=4)

    assert new_state.projectiles == ()
    assert len(events) == 1
    assert events[0].reason is ProjectileTerminationReason.OUT_OF_BOUNDS
    assert events[0].hit_robot_id is None


def test_advance_projectiles_static_collision_at_blocking_height() -> None:
    blocker = Blocker(
        id=EntityId("blocker-1"),
        components=(Component(x=6, y=5, height=DEFAULT_RULES.normal_projectile_altitude),),
    )
    robot = _robot(active_projectile_id=EntityId("projectile-1"))
    projectile = _projectile(entity_id="projectile-1", x=5, y=5, dx=1, dy=0)
    state = _state((robot,), (projectile,))
    world = _world(blockers=(blocker,))

    new_state, events = advance_projectiles(state, world, tick=4)

    assert new_state.projectiles == ()
    assert len(events) == 1
    assert events[0].reason is ProjectileTerminationReason.STATIC_COLLISION

    updated_robot = new_state.robot_for(robot.entity_id)
    assert updated_robot is not None
    assert updated_robot.active_projectile_id is None


def test_advance_projectiles_static_passthrough_below_blocking_height() -> None:
    blocker = Blocker(
        id=EntityId("blocker-1"),
        components=(
            Component(x=6, y=5, height=DEFAULT_RULES.normal_projectile_altitude - 1),
        ),
    )
    robot = _robot(active_projectile_id=EntityId("projectile-1"))
    projectile = _projectile(entity_id="projectile-1", x=5, y=5, dx=1, dy=0, max_range_cells=20)
    state = _state((robot,), (projectile,))
    world = _world(blockers=(blocker,))

    new_state, events = advance_projectiles(state, world, tick=4)

    assert events == ()
    assert len(new_state.projectiles) == 1
    assert new_state.projectiles[0].x == 6


def test_advance_projectiles_robot_hit_at_blocking_height() -> None:
    firer = _robot(entity_id="robot-firer", active_projectile_id=EntityId("projectile-1"))
    target = _robot(entity_id="robot-target", owner=PLAYER_TWO, x=6, y=5)
    # Ensure the target robot's height is >= normal_projectile_altitude by
    # constructing it with a tall chassis-equivalent build is not directly
    # controllable via _robot(); instead assert against its own derived
    # height for the "at blocking height" boundary test using the robot's
    # natural (non-tall) height for the passthrough case below, and force
    # a blocking height here via a nuclear/electronics-heavy build if
    # needed. Default derived robot height is well below 10, so use a
    # build with maximal module count to approach it, or fall back to
    # constructing a robot with an explicit height override.
    tall_target = Robot(
        entity_id=target.entity_id,
        owner=target.owner,
        x=target.x,
        y=target.y,
        build=target.build,
        stack=target.stack,
        height=DEFAULT_RULES.normal_projectile_altitude,
    )
    projectile = _projectile(entity_id="projectile-1", source_robot_id="robot-firer", x=5, y=5, dx=1, dy=0)
    state = _state((firer, tall_target), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    assert new_state.projectiles == ()
    assert len(events) == 1
    event = events[0]
    assert event.reason is ProjectileTerminationReason.ROBOT_HIT
    assert event.hit_robot_id == tall_target.entity_id


def test_advance_projectiles_robot_passthrough_below_blocking_height() -> None:
    firer = _robot(entity_id="robot-firer", active_projectile_id=EntityId("projectile-1"))
    target = _robot(entity_id="robot-target", owner=PLAYER_TWO, x=6, y=5)
    short_target = Robot(
        entity_id=target.entity_id,
        owner=target.owner,
        x=target.x,
        y=target.y,
        build=target.build,
        stack=target.stack,
        height=DEFAULT_RULES.normal_projectile_altitude - 1,
    )
    projectile = _projectile(
        entity_id="projectile-1", source_robot_id="robot-firer", x=5, y=5, dx=1, dy=0, max_range_cells=20
    )
    state = _state((firer, short_target), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    assert events == ()
    assert len(new_state.projectiles) == 1
    assert new_state.projectiles[0].x == 6


def test_advance_projectiles_source_robot_destroyed_mid_flight_does_not_crash() -> None:
    projectile = _projectile(
        entity_id="projectile-1",
        source_robot_id="robot-gone",
        x=5,
        y=5,
        dx=1,
        dy=0,
        travelled_cells=1,
        max_range_cells=1,
    )
    # No robot named "robot-gone" exists in state.robots.
    state = _state((), (projectile,))
    world = _world()

    new_state, events = advance_projectiles(state, world, tick=4)

    assert new_state.projectiles == ()
    assert len(events) == 1
    assert events[0].reason is ProjectileTerminationReason.RANGE_EXHAUSTED
    assert new_state.robots == ()


def test_advance_projectiles_deterministic_regardless_of_input_order() -> None:
    blocker_a = Blocker(
        id=EntityId("blocker-a"),
        components=(Component(x=8, y=5, height=DEFAULT_RULES.normal_projectile_altitude),),
    )
    blocker_b = Blocker(
        id=EntityId("blocker-b"),
        components=(Component(x=9, y=9, height=DEFAULT_RULES.normal_projectile_altitude),),
    )
    robot_a = _robot(entity_id="robot-a", active_projectile_id=EntityId("projectile-a"), x=5, y=5)
    robot_b = _robot(entity_id="robot-b", active_projectile_id=EntityId("projectile-b"), x=5, y=9)
    projectile_a = _projectile(
        entity_id="projectile-a", source_robot_id="robot-a", x=7, y=5, dx=1, dy=0, max_range_cells=20
    )
    projectile_b = _projectile(
        entity_id="projectile-b", source_robot_id="robot-b", x=8, y=9, dx=1, dy=0, max_range_cells=20
    )

    world = _world(blockers=(blocker_a, blocker_b))

    state_order_1 = _state((robot_a, robot_b), (projectile_a, projectile_b))
    state_order_2 = _state((robot_b, robot_a), (projectile_b, projectile_a))

    new_state_1, events_1 = advance_projectiles(state_order_1, world, tick=4)
    new_state_2, events_2 = advance_projectiles(state_order_2, world, tick=4)

    assert new_state_1.projectiles == new_state_2.projectiles
    reasons_1 = tuple((e.entity_id, e.reason) for e in events_1)
    reasons_2 = tuple((e.entity_id, e.reason) for e in events_2)
    assert sorted(reasons_1, key=lambda item: item[0].value) == sorted(
        reasons_2, key=lambda item: item[0].value
    )
