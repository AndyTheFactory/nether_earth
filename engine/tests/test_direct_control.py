"""Tests for direct-control robot movement interaction state (issue #63, M5.4).

Covers this issue's acceptance criteria at the unit level:

- direct-control moves are converted into exactly the same
  ``~nether_earth.movement.RobotMoveRequest`` shape autonomous control would
  submit (no parallel legality path is invented here);
- the interaction-state gate rejects a command deterministically, with zero
  side effects, when the issuing player has no commander or their commander
  is not ``DOCKED``;
- the gate resolves the controlled robot from the existing docked-commander
  link (``Commander.docked_robot_id``), never from a caller-supplied id.

``engine.step`` integration (routing through the shared batch, reservation/
commander-blocking enforcement, and leaving direct control via undocking) is
covered separately in ``test_engine_direct_control_integration.py``.
"""

from __future__ import annotations

import pytest

from nether_earth.commander import Commander, CommanderMode
from nether_earth.direct_control import (
    DirectControlRejectionReason,
    DirectRobotMoveCommand,
    DirectRobotMoveResult,
    direct_robot_move_request,
    validate_direct_robot_move,
)
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.movement import RobotMoveRequest
from nether_earth.state import create_game_state

ROBOT_ID = EntityId("robot-1")


def _free_commander(player_id=PLAYER_ONE, x=5, y=5, altitude=0) -> Commander:
    return Commander(player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)


def _docked_commander(player_id=PLAYER_ONE, x=5, y=5, altitude=4, robot_id=ROBOT_ID) -> Commander:
    return Commander(
        player_id=player_id,
        mode=CommanderMode.DOCKED,
        x=x,
        y=y,
        altitude=altitude,
        docked_robot_id=robot_id,
    )


def _state(commanders=()):
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), seed=0).with_commanders(commanders)


def _command(player=PLAYER_ONE, dx=1, dy=0, sequence=0) -> DirectRobotMoveCommand:
    return DirectRobotMoveCommand(player=player, sequence=sequence, dx=dx, dy=dy)


# --------------------------------------------------------------------------
# Command shape validation
# --------------------------------------------------------------------------


def test_diagonal_command_is_rejected_structurally() -> None:
    with pytest.raises(ValueError):
        DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=1)


def test_no_op_command_is_rejected_structurally() -> None:
    with pytest.raises(ValueError):
        DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=0, dy=0)


def test_out_of_range_command_is_rejected_structurally() -> None:
    with pytest.raises(ValueError):
        DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=2, dy=0)


# --------------------------------------------------------------------------
# Interaction-state gate
# --------------------------------------------------------------------------


def test_rejects_when_player_has_no_commander() -> None:
    state = _state(())
    result = validate_direct_robot_move(_command(), state)
    assert not result.accepted
    assert result.reason is DirectControlRejectionReason.NO_COMMANDER
    assert result.entity_id is None


def test_rejects_when_commander_is_free() -> None:
    state = _state((_free_commander(),))
    result = validate_direct_robot_move(_command(), state)
    assert not result.accepted
    assert result.reason is DirectControlRejectionReason.NOT_DOCKED
    assert result.entity_id is None


def test_accepts_when_commander_is_docked_and_names_docked_robot() -> None:
    state = _state((_docked_commander(),))
    result = validate_direct_robot_move(_command(), state)
    assert result.accepted
    assert result.reason is None
    assert result.entity_id == ROBOT_ID


def test_gate_is_per_player() -> None:
    """Player two's undocked commander does not let them drive player one's robot."""
    state = _state((_docked_commander(player_id=PLAYER_ONE), _free_commander(player_id=PLAYER_TWO)))
    result = validate_direct_robot_move(_command(player=PLAYER_TWO), state)
    assert not result.accepted
    assert result.reason is DirectControlRejectionReason.NOT_DOCKED


def test_result_invariant_rejects_inconsistent_construction() -> None:
    command = _command()
    with pytest.raises(ValueError):
        DirectRobotMoveResult(command=command, accepted=True, reason=DirectControlRejectionReason.NOT_DOCKED)
    with pytest.raises(ValueError):
        DirectRobotMoveResult(command=command, accepted=False)
    with pytest.raises(ValueError):
        DirectRobotMoveResult(command=command, accepted=True)
    with pytest.raises(ValueError):
        DirectRobotMoveResult(command=command, accepted=False, reason=DirectControlRejectionReason.NOT_DOCKED, entity_id=ROBOT_ID)


# --------------------------------------------------------------------------
# Request derivation: the sole bridge into the shared movement executor
# --------------------------------------------------------------------------


def test_accepted_gate_produces_matching_robot_move_request() -> None:
    state = _state((_docked_commander(),))
    command = _command(dx=0, dy=-1)
    result, request = direct_robot_move_request(command, state)
    assert result.accepted
    assert request == RobotMoveRequest(entity_id=ROBOT_ID, dx=0, dy=-1)


def test_rejected_gate_produces_no_request() -> None:
    state = _state((_free_commander(),))
    result, request = direct_robot_move_request(_command(), state)
    assert not result.accepted
    assert request is None
