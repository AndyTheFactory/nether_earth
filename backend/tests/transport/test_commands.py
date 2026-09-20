"""Tests for ``app.transport.commands`` (M7 Task 9, issue #98).

Compatibility fixtures grouped by subsystem, per the task brief: commander,
construction/economy, movement/orders/capture, firing/combat/nuclear. Each
group proves ``payload_to_command`` builds the *exact* concrete engine
``Command`` the payload names -- same type, same field values -- and that a
schema-valid-but-structurally-malformed payload (diagonal move) is rejected
via ``CommandPayloadError`` rather than crashing or silently coercing.
"""

from __future__ import annotations

import pytest
from nether_earth.combat import FireCommand
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    DeselectModuleCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.ids import EntityId, PlayerId
from nether_earth.orders import (
    Advance,
    Order,
    Retreat,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
)
from nether_earth.robot_build import ModuleIdentity

from app.protocol.common import (
    AdvanceOrderPayload,
    CancelConstructionCommandPayload,
    CommanderMoveCommandPayload,
    CommanderSetVerticalIntentCommandPayload,
    DeselectModuleCommandPayload,
    DirectRobotMoveCommandPayload,
    LaunchRobotCommandPayload,
    RetreatOrderPayload,
    RobotFireCommandPayload,
    RobotOrderPayload,
    SearchCaptureOrderPayload,
    SearchDestroyOrderPayload,
    SelectModuleCommandPayload,
    SetRobotOrderCommandPayload,
    StopAndDefendOrderPayload,
)
from app.transport.commands import CommandPayloadError, payload_to_command

_PLAYER = PlayerId("p1")
_SEQ = 7


# -- commander -----------------------------------------------------------------


def test_commander_move_payload_maps_to_commander_move_command() -> None:
    payload = CommanderMoveCommandPayload(kind="commander_move", dx=1, dy=0)
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == CommanderMoveCommand(player=_PLAYER, sequence=_SEQ, dx=1, dy=0)


def test_commander_move_diagonal_payload_raises_command_payload_error() -> None:
    """`cellDelta` restricts each axis independently, so a diagonal shape
    (both dx/dy nonzero) is schema-valid but rejected by the engine
    dataclass's own `__post_init__` -- see `commander_movement.py`."""
    payload = CommanderMoveCommandPayload(kind="commander_move", dx=1, dy=1)
    with pytest.raises(CommandPayloadError):
        payload_to_command(payload, _PLAYER, _SEQ)


def test_commander_set_vertical_intent_payload_maps() -> None:
    payload = CommanderSetVerticalIntentCommandPayload(
        kind="commander_set_vertical_intent", rising=True
    )
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == CommanderSetVerticalIntentCommand(player=_PLAYER, sequence=_SEQ, rising=True)


# -- construction/economy -------------------------------------------------------


def test_select_module_payload_maps() -> None:
    payload = SelectModuleCommandPayload(kind="select_module", module="tracks")
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == SelectModuleCommand(
        player=_PLAYER, sequence=_SEQ, module=ModuleIdentity.TRACKS
    )


def test_deselect_module_payload_maps() -> None:
    payload = DeselectModuleCommandPayload(kind="deselect_module", module="cannon")
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == DeselectModuleCommand(
        player=_PLAYER, sequence=_SEQ, module=ModuleIdentity.CANNON
    )


def test_cancel_construction_payload_maps() -> None:
    payload = CancelConstructionCommandPayload(kind="cancel_construction")
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == CancelConstructionCommand(player=_PLAYER, sequence=_SEQ)


def test_launch_robot_payload_maps() -> None:
    payload = LaunchRobotCommandPayload(kind="launch_robot")
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == LaunchRobotCommand(player=_PLAYER, sequence=_SEQ)


# -- movement/orders/capture -----------------------------------------------------


def test_direct_robot_move_payload_maps() -> None:
    payload = DirectRobotMoveCommandPayload(kind="direct_robot_move", dx=0, dy=-1)
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == DirectRobotMoveCommand(player=_PLAYER, sequence=_SEQ, dx=0, dy=-1)


def test_direct_robot_move_diagonal_payload_raises_command_payload_error() -> None:
    payload = DirectRobotMoveCommandPayload(kind="direct_robot_move", dx=1, dy=1)
    with pytest.raises(CommandPayloadError):
        payload_to_command(payload, _PLAYER, _SEQ)


@pytest.mark.parametrize(
    ("order_payload", "expected_order"),
    [
        (StopAndDefendOrderPayload(kind="stop_and_defend"), StopAndDefend()),
        (AdvanceOrderPayload(kind="advance", distance_miles=10), Advance(distance_miles=10)),
        (RetreatOrderPayload(kind="retreat", distance_miles=5), Retreat(distance_miles=5)),
        (
            SearchCaptureOrderPayload(kind="search_capture", target="enemy_factory"),
            SearchCapture(target=SearchCaptureTarget.ENEMY_FACTORY),
        ),
        (
            SearchDestroyOrderPayload(kind="search_destroy", target="war_base"),
            SearchDestroy(target=SearchDestroyTarget.WAR_BASE),
        ),
    ],
    ids=["stop_and_defend", "advance", "retreat", "search_capture", "search_destroy"],
)
def test_set_robot_order_payload_maps_every_order_variant(
    order_payload: RobotOrderPayload, expected_order: Order
) -> None:
    payload = SetRobotOrderCommandPayload(
        kind="set_robot_order", entity_id="robot-1", order=order_payload
    )
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == SetRobotOrderCommand(
        player=_PLAYER,
        sequence=_SEQ,
        entity_id=EntityId("robot-1"),
        order=expected_order,
    )


def test_set_robot_order_advance_always_binds_unbound_target_x() -> None:
    """A freshly assigned `Advance`/`Retreat` order always starts with
    `target_x=None` -- the payload has no `targetX` field at all, so there
    is nothing for a client to smuggle a pre-bound goal in with."""
    payload = SetRobotOrderCommandPayload(
        kind="set_robot_order",
        entity_id="robot-1",
        order=AdvanceOrderPayload(kind="advance", distance_miles=20),
    )
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert isinstance(command, SetRobotOrderCommand)
    assert isinstance(command.order, Advance)
    assert command.order.target_x is None


# -- firing/combat/nuclear -------------------------------------------------------


def test_robot_fire_normal_weapon_payload_maps() -> None:
    payload = RobotFireCommandPayload(
        kind="robot_fire", entity_id="robot-1", weapon="cannon", target_x=5, target_y=7
    )
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == FireCommand(
        player=_PLAYER,
        sequence=_SEQ,
        entity_id=EntityId("robot-1"),
        weapon=ModuleIdentity.CANNON,
        target_x=5,
        target_y=7,
    )


def test_robot_fire_nuclear_weapon_payload_maps() -> None:
    """Nuclear reuses the same `robot_fire` payload shape; `targetX`/`targetY`
    are structurally present but ignored by the engine (a nuclear
    detonation always centers on the carrier robot's own position -- see
    `combat.FireCommand`'s own docstring), so the adapter passes them
    through unchanged rather than special-casing nuclear itself (that
    decision belongs to the engine, not the transport adapter)."""
    payload = RobotFireCommandPayload(
        kind="robot_fire", entity_id="robot-1", weapon="nuclear", target_x=0, target_y=0
    )
    command = payload_to_command(payload, _PLAYER, _SEQ)
    assert command == FireCommand(
        player=_PLAYER,
        sequence=_SEQ,
        entity_id=EntityId("robot-1"),
        weapon=ModuleIdentity.NUCLEAR,
        target_x=0,
        target_y=0,
    )
