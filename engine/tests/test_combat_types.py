"""Tests for combat engine types: fire requests, results, projectiles (issue #70, M6.1)."""

from __future__ import annotations

import pytest

from nether_earth.combat import (
    FireRejectionReason,
    FireRequest,
    FireResult,
    Projectile,
)
from nether_earth.ids import PLAYER_ONE, EntityId, PlayerId
from nether_earth.robot_build import ModuleIdentity


def _fire_request(
    robot_id: str = "robot-1",
    player: PlayerId = PLAYER_ONE,
    weapon: ModuleIdentity = ModuleIdentity.CANNON,
    target_x: int = 10,
    target_y: int = 10,
) -> FireRequest:
    return FireRequest(
        robot_id=EntityId(robot_id),
        player=player,
        weapon=weapon,
        target_x=target_x,
        target_y=target_y,
    )


# --- FireResult accept/reject invariant -----------


def test_fire_result_accept_constructs_correctly() -> None:
    request = _fire_request()
    result = FireResult.accept(request)

    assert result.request is request
    assert result.accepted is True
    assert result.reason is None


def test_fire_result_reject_constructs_correctly() -> None:
    request = _fire_request()
    reason = FireRejectionReason.WEAPON_NOT_FITTED
    result = FireResult.reject(request, reason)

    assert result.request is request
    assert result.accepted is False
    assert result.reason is reason


def test_fire_result_enforces_accept_reject_invariant() -> None:
    request = _fire_request()

    # accepted=True with a reason violates the invariant
    with pytest.raises(ValueError):
        FireResult(request=request, accepted=True, reason=FireRejectionReason.WEAPON_NOT_FITTED)

    # accepted=False without a reason violates the invariant
    with pytest.raises(ValueError):
        FireResult(request=request, accepted=False, reason=None)


def test_fire_result_accepts_various_rejection_reasons() -> None:
    request = _fire_request()

    for reason in FireRejectionReason:
        result = FireResult.reject(request, reason)
        assert result.reason is reason


# --- Projectile construction and validation -----------


def test_projectile_constructs_with_valid_cardinal_direction() -> None:
    projectile = Projectile(
        id=EntityId("proj-1"),
        owner=PLAYER_ONE,
        source_robot_id=EntityId("robot-1"),
        weapon=ModuleIdentity.CANNON,
        x=5,
        y=5,
        z=10,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=20,
        created_tick=0,
    )

    assert projectile.x == 5
    assert projectile.y == 5
    assert projectile.z == 10
    assert projectile.dx == 1
    assert projectile.dy == 0


def test_projectile_constructs_with_all_four_cardinal_directions() -> None:
    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        projectile = Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.MISSILE,
            x=0,
            y=0,
            z=10,
            dx=dx,
            dy=dy,
            travelled_cells=0,
            max_range_cells=28,
            created_tick=0,
        )

        assert projectile.dx == dx
        assert projectile.dy == dy


def test_projectile_rejects_both_zero_direction() -> None:
    with pytest.raises(ValueError, match="cannot both be zero"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.PHASER,
            x=0,
            y=0,
            z=10,
            dx=0,
            dy=0,
            travelled_cells=0,
            max_range_cells=20,
            created_tick=0,
        )


def test_projectile_rejects_diagonal_direction() -> None:
    with pytest.raises(ValueError, match="diagonal"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.CANNON,
            x=0,
            y=0,
            z=10,
            dx=1,
            dy=1,
            travelled_cells=0,
            max_range_cells=20,
            created_tick=0,
        )

    with pytest.raises(ValueError, match="diagonal"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.MISSILE,
            x=0,
            y=0,
            z=10,
            dx=-1,
            dy=1,
            travelled_cells=0,
            max_range_cells=28,
            created_tick=0,
        )


def test_projectile_rejects_invalid_dx_dy_ranges() -> None:
    with pytest.raises(ValueError, match="must each be in"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.CANNON,
            x=0,
            y=0,
            z=10,
            dx=2,
            dy=0,
            travelled_cells=0,
            max_range_cells=20,
            created_tick=0,
        )

    with pytest.raises(ValueError, match="must each be in"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.PHASER,
            x=0,
            y=0,
            z=10,
            dx=0,
            dy=-2,
            travelled_cells=0,
            max_range_cells=20,
            created_tick=0,
        )


def test_projectile_rejects_non_positive_z() -> None:
    with pytest.raises(ValueError, match="z.*must be a positive integer"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.CANNON,
            x=0,
            y=0,
            z=0,
            dx=1,
            dy=0,
            travelled_cells=0,
            max_range_cells=20,
            created_tick=0,
        )

    with pytest.raises(ValueError, match="z.*must be a positive integer"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.MISSILE,
            x=0,
            y=0,
            z=-5,
            dx=1,
            dy=0,
            travelled_cells=0,
            max_range_cells=28,
            created_tick=0,
        )


def test_projectile_rejects_negative_travelled_cells() -> None:
    with pytest.raises(ValueError, match="travelled_cells must be non-negative"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.PHASER,
            x=5,
            y=5,
            z=10,
            dx=1,
            dy=0,
            travelled_cells=-1,
            max_range_cells=20,
            created_tick=0,
        )


def test_projectile_accepts_zero_travelled_cells() -> None:
    projectile = Projectile(
        id=EntityId("proj-1"),
        owner=PLAYER_ONE,
        source_robot_id=EntityId("robot-1"),
        weapon=ModuleIdentity.CANNON,
        x=5,
        y=5,
        z=10,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=20,
        created_tick=0,
    )

    assert projectile.travelled_cells == 0


def test_projectile_rejects_non_positive_max_range_cells() -> None:
    with pytest.raises(ValueError, match="max_range_cells must be a positive integer"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.CANNON,
            x=0,
            y=0,
            z=10,
            dx=1,
            dy=0,
            travelled_cells=0,
            max_range_cells=0,
            created_tick=0,
        )

    with pytest.raises(ValueError, match="max_range_cells must be a positive integer"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.MISSILE,
            x=0,
            y=0,
            z=10,
            dx=1,
            dy=0,
            travelled_cells=0,
            max_range_cells=-10,
            created_tick=0,
        )


def test_projectile_rejects_negative_created_tick() -> None:
    with pytest.raises(ValueError, match="created_tick must be non-negative"):
        Projectile(
            id=EntityId("proj-1"),
            owner=PLAYER_ONE,
            source_robot_id=EntityId("robot-1"),
            weapon=ModuleIdentity.PHASER,
            x=5,
            y=5,
            z=10,
            dx=1,
            dy=0,
            travelled_cells=0,
            max_range_cells=20,
            created_tick=-1,
        )


def test_projectile_accepts_zero_created_tick() -> None:
    projectile = Projectile(
        id=EntityId("proj-1"),
        owner=PLAYER_ONE,
        source_robot_id=EntityId("robot-1"),
        weapon=ModuleIdentity.CANNON,
        x=5,
        y=5,
        z=10,
        dx=1,
        dy=0,
        travelled_cells=0,
        max_range_cells=20,
        created_tick=0,
    )

    assert projectile.created_tick == 0
