"""Transport `CommandPayload` -> concrete engine `Command` adapter.

Scope (AGENTS.md non-negotiable): pure field-shape translation only. Given a
`CommandPayload` that has already cleared pydantic/JSON-Schema validation,
plus the issuing `PlayerId` and `sequence`, this module builds the exact
matching `nether_earth.commands.Command` subclass the payload's `kind`
discriminator names -- one `isinstance` branch per variant, one call to that
subclass's constructor, nothing else. It makes **no gameplay legality
decision**: an illegal-but-well-shaped command (unknown player, blocked
move, insufficient resources, wrong owner, ...) is handed to
`nether_earth.engine.step` completely unchanged and is rejected there, via
`nether_earth.commands.validate_command_batch`/the concrete subsystem's own
validation, exactly like any other command.

The one exception -- still not a *gameplay* decision -- is a payload that is
schema-valid but violates one of the target `Command` dataclass's own
`__post_init__` *structural* invariants (e.g. `dx=1, dy=1` for a commander
move: JSON Schema's `cellDelta` only restricts each axis independently to
`{-1, 0, 1}`, it cannot express "exactly one nonzero", so a diagonal shape
passes schema validation and only the engine dataclass itself catches it --
see `commander_movement.CommanderMoveCommand`'s own docstring: "not as a
collision rejection", i.e. not a `validate_command_batch` gameplay outcome
either). :func:`payload_to_command` lets that surface as
:class:`CommandPayloadError`, which the transport layer is expected to treat
exactly like a JSON Schema violation (reject the frame with a `ServerError`,
never touch `MatchRuntimeRegistry`/`engine.step` with it).
"""

from __future__ import annotations

from nether_earth.combat import FireCommand
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.commands import Command
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
    CommandPayload,
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

__all__ = ["CommandPayloadError", "order_payload_to_engine_order", "payload_to_command"]


class CommandPayloadError(ValueError):
    """A schema-valid `CommandPayload` is structurally malformed for its `Command`.

    Raised only when constructing the target engine `Command`/`Order` value
    itself raises `ValueError` for a structural (not gameplay) reason -- see
    the module docstring. Never raised for a gameplay-legality reason; those
    are exclusively `engine.step`'s job.
    """


def order_payload_to_engine_order(payload: RobotOrderPayload) -> Order:
    """Translate one `RobotOrderPayload` variant into its `nether_earth.orders.Order` value.

    Field-shape mapping only, the mirror image of
    `nether_earth.snapshot`'s own `_order_snapshot` tagging.
    `Advance`/`Retreat` are always constructed with the default
    `target_x=None` (freshly assigned orders always start unbound -- see
    `nether_earth.orders.SetRobotOrderCommand`'s own docstring and
    `orders._unbound`); a payload has no `targetX` field at all (see
    `AdvanceOrderPayload`'s own docstring), so there is nothing to smuggle
    in here.
    """
    if isinstance(payload, StopAndDefendOrderPayload):
        return StopAndDefend()
    if isinstance(payload, AdvanceOrderPayload):
        return Advance(distance_miles=payload.distance_miles)
    if isinstance(payload, RetreatOrderPayload):
        return Retreat(distance_miles=payload.distance_miles)
    if isinstance(payload, SearchCaptureOrderPayload):
        return SearchCapture(target=SearchCaptureTarget(payload.target))
    if isinstance(payload, SearchDestroyOrderPayload):
        return SearchDestroy(target=SearchDestroyTarget(payload.target))
    raise AssertionError(  # pragma: no cover - exhaustive over a closed union
        f"unhandled RobotOrderPayload variant: {payload!r}"
    )


def payload_to_command(payload: CommandPayload, player: PlayerId, sequence: int) -> Command:
    """Build the concrete engine `Command` subclass `payload` names.

    Pure field-shape translation, dispatching on `payload`'s own `kind`
    discriminator (already validated by pydantic/JSON Schema before this is
    ever called) -- exactly one variant maps to exactly one `Command`
    subclass, matching `engine.step`'s own `isinstance` dispatch list one
    for one. See the module docstring for the exact scope/limits, and
    :class:`CommandPayloadError` for the one structural-rejection case this
    function can raise.
    """
    try:
        return _dispatch(payload, player, sequence)
    except ValueError as exc:
        raise CommandPayloadError(str(exc)) from exc


def _dispatch(payload: CommandPayload, player: PlayerId, sequence: int) -> Command:
    if isinstance(payload, CommanderMoveCommandPayload):
        return CommanderMoveCommand(player=player, sequence=sequence, dx=payload.dx, dy=payload.dy)
    if isinstance(payload, CommanderSetVerticalIntentCommandPayload):
        return CommanderSetVerticalIntentCommand(
            player=player, sequence=sequence, rising=payload.rising
        )
    if isinstance(payload, DirectRobotMoveCommandPayload):
        return DirectRobotMoveCommand(
            player=player, sequence=sequence, dx=payload.dx, dy=payload.dy
        )
    if isinstance(payload, RobotFireCommandPayload):
        return FireCommand(
            player=player,
            sequence=sequence,
            entity_id=EntityId.from_json(payload.entity_id),
            weapon=ModuleIdentity(payload.weapon),
        )
    if isinstance(payload, SetRobotOrderCommandPayload):
        return SetRobotOrderCommand(
            player=player,
            sequence=sequence,
            entity_id=EntityId.from_json(payload.entity_id),
            order=order_payload_to_engine_order(payload.order),
        )
    if isinstance(payload, SelectModuleCommandPayload):
        return SelectModuleCommand(
            player=player, sequence=sequence, module=ModuleIdentity(payload.module)
        )
    if isinstance(payload, DeselectModuleCommandPayload):
        return DeselectModuleCommand(
            player=player, sequence=sequence, module=ModuleIdentity(payload.module)
        )
    if isinstance(payload, CancelConstructionCommandPayload):
        return CancelConstructionCommand(player=player, sequence=sequence)
    if isinstance(payload, LaunchRobotCommandPayload):
        return LaunchRobotCommand(player=player, sequence=sequence)
    raise AssertionError(  # pragma: no cover - exhaustive over a closed union
        f"unhandled CommandPayload variant: {payload!r}"
    )
