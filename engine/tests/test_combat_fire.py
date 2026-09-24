"""Tests for the fire-validation gate (issue #71, M6.2)."""

from __future__ import annotations

from nether_earth.combat import FireRejectionReason, FireRequest, validate_fire
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state


def _robot(
    entity_id: str = "robot-player-one-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 5,
    y: int = 5,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    active_projectile_id: EntityId | None = None,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons, electronics=None)
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


def _state(robots: tuple[Robot, ...] = ()) -> GameState:
    return create_game_state(
        0,
        (PLAYER_ONE, PLAYER_TWO),
        robots=list(robots),
    )


def _request(
    robot_id: str = "robot-player-one-1",
    player: PlayerId = PLAYER_ONE,
    weapon: ModuleIdentity = ModuleIdentity.CANNON,
    target_x: int = 6,
    target_y: int = 5
) -> FireRequest:
    return FireRequest(
        robot_id=EntityId(robot_id),
        player=player,
        weapon=weapon,
    )


# --- Validation ---------------------------------------------------------------


def test_unknown_robot_is_rejected() -> None:
    robot = _robot()
    state = _state((robot,))

    result = validate_fire(_request(robot_id="robot-nobody"), state)

    assert result.reason is FireRejectionReason.NO_SUCH_ROBOT
    assert state.robot_for(robot.entity_id) is robot


def test_requester_not_controlling_robot_is_rejected() -> None:
    robot = _robot(owner=PLAYER_ONE)
    state = _state((robot,))

    result = validate_fire(_request(player=PLAYER_TWO), state)

    assert result.reason is FireRejectionReason.NOT_CONTROLLED_BY_PLAYER
    assert state.robot_for(robot.entity_id) is robot


def test_unfitted_weapon_is_rejected() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,))
    state = _state((robot,))

    result = validate_fire(_request(weapon=ModuleIdentity.MISSILE), state)

    assert result.reason is FireRejectionReason.WEAPON_NOT_FITTED
    assert state.robot_for(robot.entity_id) is robot


def test_channel_occupied_rejects_a_second_normal_weapon_fire() -> None:
    robot = _robot(
        weapons=(ModuleIdentity.CANNON,),
        active_projectile_id=EntityId("projectile-1"),
    )
    state = _state((robot,))

    result = validate_fire(_request(weapon=ModuleIdentity.CANNON), state)

    assert result.reason is FireRejectionReason.CHANNEL_OCCUPIED
    assert state.robot_for(robot.entity_id) is robot


def test_normal_weapon_fire_with_free_channel_is_accepted() -> None:
    robot = _robot(weapons=(ModuleIdentity.CANNON,), active_projectile_id=None)
    state = _state((robot,))

    result = validate_fire(_request(weapon=ModuleIdentity.CANNON), state)

    assert result.accepted
    assert result.reason is None


def test_nuclear_fire_bypasses_the_channel_even_when_occupied() -> None:
    robot = _robot(
        weapons=(ModuleIdentity.NUCLEAR,),
        active_projectile_id=EntityId("projectile-in-flight"),
    )
    state = _state((robot,))

    result = validate_fire(_request(weapon=ModuleIdentity.NUCLEAR), state)

    assert result.accepted
    assert result.reason is None


# --- Robot.with_active_projectile ---------------------------------------------


def test_with_active_projectile_replaces_only_that_field() -> None:
    robot = _robot(active_projectile_id=None)

    updated = robot.with_active_projectile(EntityId("projectile-1"))

    assert updated.active_projectile_id == EntityId("projectile-1")
    assert updated.entity_id == robot.entity_id
    assert updated.owner == robot.owner
    assert updated.x == robot.x
    assert updated.y == robot.y
    assert updated.build == robot.build
    assert updated.stack == robot.stack
    assert updated.height == robot.height
    assert updated.movement == robot.movement
    assert updated.order == robot.order


def test_with_active_projectile_can_clear_back_to_none() -> None:
    robot = _robot(active_projectile_id=EntityId("projectile-1"))

    updated = robot.with_active_projectile(None)

    assert updated.active_projectile_id is None
