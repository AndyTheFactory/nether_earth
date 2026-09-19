"""Direct-control robot movement interaction state (issue #63, M5.4).

`_specs/functional-spec.md` §15 locks the precondition: "Available after
docking: command menu; direct control; orders menu; combat control" and
"Direct control uses exactly the same movement rules as autonomous
movement." This module is the engine-side interaction gate and command type
that implement that rule, built entirely on the *existing* M3/M4 commander/
robot interaction contract rather than a new one:

- A commander must be ``DOCKED`` to a robot
  (:attr:`~nether_earth.commander.Commander.mode` /
  :attr:`~nether_earth.commander.Commander.docked_robot_id`) before that
  player may directly move it -- the same docked-commander precondition
  ``docking.py``'s module docstring already documents as unlocking "command
  menu, direct control, orders menu, combat control" for a player. A
  ``FREE`` commander has no robot to directly drive.
- No second "who is directly controlling this robot" field is added to
  either :class:`~nether_earth.robot.Robot` or
  :class:`~nether_earth.commander.Commander`: the docked commander <->
  robot link *is* the control relationship, per ``commander.py``'s
  already-locked ``FREE``/``DOCKED`` invariant set (a ``DOCKED`` commander
  always names exactly the one robot it is docked to, and a docked robot
  has exactly one controlling commander by construction -- no additional
  bookkeeping can add information the existing invariant does not already
  guarantee). This module only adds the player-facing command and the
  interaction-state gate that reads that existing link; it does not invent
  a parallel ownership model.
- Leaving direct control reuses ``docking.py``'s existing undock transition
  (:func:`~nether_earth.docking.apply_undock`, already wired into
  ``engine.step``'s Step 3): holding rise while ``DOCKED`` undocks the
  commander back to ``FREE`` in the same authoritative step, at which point
  :func:`validate_direct_robot_move` (this module) starts rejecting further
  direct-move commands with :attr:`DirectControlRejectionReason.NOT_DOCKED`
  -- exactly mirroring how
  :func:`~nether_earth.docking.docked_movement_allowed` already gates
  independent *commander* movement on the same mode. No new "leave direct
  control" command or event type is introduced here: undocking already is
  that transition, and it is snapshot/replay-safe because ``Commander``
  itself already is (issue #37).

Movement legality itself is intentionally NOT re-implemented here: once the
docking gate passes, a :class:`DirectRobotMoveCommand` becomes exactly a
:class:`~nether_earth.movement.RobotMoveRequest`, submitted to
:func:`~nether_earth.reservations.apply_robot_move_batch` -- the single
batched move-start path every robot control source (this module, and
autonomous orders in a later M5 task) must use. Terrain capability,
occupancy, commander blocking, and destination reservation/contention are
therefore checked exactly once, inside ``movement.py``/``reservations.py``,
never duplicated here -- direct control cannot bypass any of them because
this module never gets the chance to decide movement legality at all.

Determinism: every function here is pure (state in, result/request out,
never mutated), draws no randomness, and reads no wall-clock time. A
rejected command never produces a :class:`~nether_earth.movement.
RobotMoveRequest`, so it cannot reach the shared movement executor at all --
zero side effects by construction, not by discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nether_earth.commander import CommanderMode
from nether_earth.commands import Command
from nether_earth.ids import EntityId
from nether_earth.movement import RobotMoveRequest
from nether_earth.state import GameState

__all__ = [
    "DirectControlRejectionReason",
    "DirectRobotMoveCommand",
    "DirectRobotMoveResult",
    "direct_robot_move_request",
    "validate_direct_robot_move",
]


@dataclass(frozen=True, slots=True)
class DirectRobotMoveCommand(Command):
    """Request to move the issuing player's directly-controlled robot one cell.

    ``dx``/``dy`` use the same classic 4-directional, one-cell-per-command
    shape as :class:`~nether_earth.commander_movement.CommanderMoveCommand`
    and :class:`~nether_earth.movement.RobotMoveRequest` (each restricted to
    ``{-1, 0, 1}`` with exactly one nonzero; diagonal or no-op shapes raise
    ``ValueError`` in ``__post_init__`` -- a structural rejection, not a
    gameplay one, matching those two types).

    There is deliberately no ``entity_id`` field: per the module docstring,
    exactly one robot is ever directly controllable by a player at a time --
    whichever one their commander is currently docked to -- so letting a
    command name an arbitrary robot would itself be a control-bypass vector
    rather than a legitimate input shape. The controlled robot is always
    resolved server-side from the issuing player's own docked commander (see
    :func:`validate_direct_robot_move`).
    """

    dx: int
    dy: int

    def __post_init__(self) -> None:
        if self.dx not in (-1, 0, 1) or self.dy not in (-1, 0, 1):
            raise ValueError("dx and dy must each be in {-1, 0, 1}")
        if self.dx == 0 and self.dy == 0:
            raise ValueError("dx and dy cannot both be zero (not a move)")
        if self.dx != 0 and self.dy != 0:
            raise ValueError(
                "diagonal movement is not supported: exactly one of dx/dy must be nonzero"
            )


class DirectControlRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected direct-control gate check.

    A dedicated enum, not :class:`~nether_earth.movement.MovementRejectionReason`
    or :class:`~nether_earth.commander_movement.CommanderMovementRejectionReason`:
    this module only ever reports the *interaction-state* gate ("is this
    player currently entitled to directly drive a robot at all"), never a
    movement-legality outcome -- once the gate passes, the resulting
    :class:`~nether_earth.movement.RobotMoveRequest` is validated entirely
    by ``movement.py``/``reservations.py``, whose own rejection reasons
    apply unchanged from that point on.
    """

    NO_COMMANDER = "no_commander"
    NOT_DOCKED = "not_docked"


@dataclass(frozen=True, slots=True)
class DirectRobotMoveResult:
    """Outcome of the direct-control interaction-state gate for one command.

    Mirrors :class:`~nether_earth.commander_movement.CommanderMoveResult`'s
    accept/reject invariant. ``entity_id`` names the docked robot being
    controlled; it is set exactly when ``accepted`` is ``True`` (there is
    nothing to name on a rejection, since the gate never resolved a
    controlled robot in that case).
    """

    command: DirectRobotMoveCommand
    accepted: bool
    reason: DirectControlRejectionReason | None = None
    entity_id: EntityId | None = None

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError(
                "an accepted DirectRobotMoveResult must not carry a rejection reason"
            )
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected DirectRobotMoveResult must carry a rejection reason")
        if self.accepted and self.entity_id is None:
            raise ValueError("an accepted DirectRobotMoveResult must carry the docked entity_id")
        if not self.accepted and self.entity_id is not None:
            raise ValueError("a rejected DirectRobotMoveResult must not carry an entity_id")

    @classmethod
    def accept(
        cls, command: DirectRobotMoveCommand, entity_id: EntityId
    ) -> DirectRobotMoveResult:
        """Build an accepted result naming the controlled robot's ``entity_id``."""
        return cls(command=command, accepted=True, reason=None, entity_id=entity_id)

    @classmethod
    def reject(
        cls, command: DirectRobotMoveCommand, reason: DirectControlRejectionReason
    ) -> DirectRobotMoveResult:
        """Build a rejected result for ``command`` with a stable ``reason``."""
        return cls(command=command, accepted=False, reason=reason, entity_id=None)


def validate_direct_robot_move(
    command: DirectRobotMoveCommand, state: GameState
) -> DirectRobotMoveResult:
    """Validate ``command`` against the direct-control interaction-state gate.

    Pure function: reads ``command``/``state`` and returns a
    :class:`DirectRobotMoveResult`, never mutating either argument. Checks,
    in order:

    1. the issuing player has a commander at all
       (:attr:`~DirectControlRejectionReason.NO_COMMANDER`);
    2. that commander is :attr:`~nether_earth.commander.CommanderMode.DOCKED`
       (:attr:`~DirectControlRejectionReason.NOT_DOCKED`) -- per
       `_specs/functional-spec.md` §15, direct control is only available
       after docking; a ``FREE`` commander has no robot to directly drive.

    This function knows nothing about movement legality: terrain, occupancy,
    commander blocking, and destination reservation are all checked later,
    when the resulting :class:`~nether_earth.movement.RobotMoveRequest` is
    submitted to :func:`~nether_earth.reservations.apply_robot_move_batch`
    (see :func:`direct_robot_move_request`).
    """
    commander = state.commander_for(command.player)
    if commander is None:
        return DirectRobotMoveResult.reject(command, DirectControlRejectionReason.NO_COMMANDER)
    if commander.mode is not CommanderMode.DOCKED:
        return DirectRobotMoveResult.reject(command, DirectControlRejectionReason.NOT_DOCKED)
    docked_robot_id = commander.docked_robot_id
    assert docked_robot_id is not None  # guaranteed by the DOCKED invariant
    return DirectRobotMoveResult.accept(command, docked_robot_id)


def direct_robot_move_request(
    command: DirectRobotMoveCommand, state: GameState
) -> tuple[DirectRobotMoveResult, RobotMoveRequest | None]:
    """Validate ``command`` and, if the gate passes, build its move request.

    Returns ``(result, None)`` when the interaction-state gate rejects the
    command: no :class:`~nether_earth.movement.RobotMoveRequest` is ever
    built for a player not currently entitled to direct control, so an
    invalid direct-control command cannot reach the shared movement executor
    at all -- zero side effects, per this issue's acceptance criteria.

    Returns ``(result, request)`` when the gate passes. ``request`` still
    has to survive :func:`~nether_earth.movement.validate_robot_move`'s own
    legality checks (terrain/occupancy/commander-blocking/reservation) once
    submitted to :func:`~nether_earth.reservations.apply_robot_move_batch`
    -- this function only decides whether the player is entitled to attempt
    the move, never whether the move itself succeeds.
    """
    result = validate_direct_robot_move(command, state)
    if not result.accepted:
        return result, None
    assert result.entity_id is not None  # guaranteed by an accepted result
    request = RobotMoveRequest(entity_id=result.entity_id, dx=command.dx, dy=command.dy)
    return result, request
