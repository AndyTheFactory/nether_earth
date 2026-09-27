"""CR004.13 (#299) -- a robot's cached hunt route crosses the protocol boundary.

The engine serializes :attr:`Robot.hunt_route` as an optional ``"hunt_route"``
robot key, elided when ``None``. Both the Pydantic ``SnapshotState`` and the
canonical JSON Schema must accept it when present and keep it absent when not.
"""

from __future__ import annotations

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.robot import Robot, RobotHuntRoute
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.snapshot import to_snapshot
from nether_earth.state import create_game_state

from app.protocol.common import SnapshotState

from .schema_registry import validator_for


def _snapshot(hunt_route: RobotHuntRoute | None) -> dict:
    build = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.PHASER,),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    robot = Robot(
        entity_id=EntityId("robot-hunter"),
        owner=PLAYER_ONE,
        x=4,
        y=5,
        build=build,
        stack=stack,
        height=height,
        hunt_route=hunt_route,
    )
    return to_snapshot(create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=[robot]))


def _schema_errors(snapshot: dict) -> list:
    envelope = {
        "protocolVersion": 1,
        "type": "snapshot",
        "matchId": "m1",
        "tick": snapshot["tick"],
        "state": snapshot,
    }
    return list(validator_for("snapshot").iter_errors(envelope))


def test_a_robot_without_a_hunt_route_keeps_the_key_elided() -> None:
    snapshot = _snapshot(None)
    assert "hunt_route" not in snapshot["robots"][0]
    model = SnapshotState.model_validate(snapshot)
    assert "hunt_route" not in model.model_dump()["robots"][0]
    assert model.model_dump() == snapshot
    assert _schema_errors(snapshot) == []


def test_a_cached_hunt_route_validates_and_round_trips() -> None:
    for steps in ("EENNWS", "", None):
        snapshot = _snapshot(
            RobotHuntRoute(
                target_id=EntityId("robot-p2-1"),
                planned_tick=40,
                origin_x=4,
                origin_y=5,
                steps=steps,
            )
        )
        assert snapshot["robots"][0]["hunt_route"]["steps"] == steps
        model = SnapshotState.model_validate(snapshot)
        assert model.model_dump() == snapshot
        assert _schema_errors(snapshot) == []


def test_a_malformed_hunt_route_is_rejected_by_both_validators() -> None:
    snapshot = _snapshot(
        RobotHuntRoute(
            target_id=EntityId("robot-p2-1"), planned_tick=0, origin_x=4, origin_y=5, steps="E"
        )
    )
    snapshot["robots"][0]["hunt_route"]["steps"] = "EX"
    assert _schema_errors(snapshot) != []
    try:
        SnapshotState.model_validate(snapshot)
    except ValueError:
        pass
    else:
        raise AssertionError("SnapshotState accepted a malformed hunt route")
