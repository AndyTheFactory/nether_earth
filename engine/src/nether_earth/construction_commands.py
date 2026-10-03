"""Construction/economy ``Command``/``Event`` shapes for ``engine.step``.

This module is the construction counterpart of ``commander_movement.py``:
it defines the concrete :class:`~nether_earth.commands.Command` subclasses
and :class:`~nether_earth.events.Event` subclasses that let a player-issued
command stream drive the pure functions
(``construction_session.py``'s :func:`~nether_earth.construction_session.select_module`/
:func:`~nether_earth.construction_session.deselect_module`/
:func:`~nether_earth.construction_session.cancel_construction`, and
``robot_launch.py``'s :func:`~nether_earth.robot_launch.launch_robot`)
through ``engine.step``'s per-tick command pipeline -- see ``engine.py``'s
``step`` docstring for exactly where these are wired in (Step 8).

Command shapes mirror :class:`~nether_earth.commander_movement.CommanderMoveCommand`
exactly: frozen/slotted dataclasses extending :class:`~nether_earth.commands.Command`,
carrying only the fields each action needs beyond the base ``player``/
``sequence`` contract.

Entering construction is deliberately **not** represented as a player-issued
``Command`` here: `_specs/functional-spec.md` §10.3/§8 model construction
entry as an automatic consequence of landing on the player's own war-base
heli-pad (``heli_pad.CommanderConstructionEntryEligible``, the detection
event), not a discrete player action. ``engine.step`` wires
:func:`~nether_earth.construction_session.enter_construction` to fire
automatically, in direct response to that detection event, in the same
step that already detects it (see ``engine.py``'s Step 7) -- following the
exact "detect and immediately apply" style already used there for
auto-dock (Step 5). :class:`ConstructionEnteredEvent` is this module's
event for that automatic transition, emitted only when
``enter_construction`` actually succeeds (mirrors ``docking.py``'s
``auto_dock_with_event``'s "only emit on actual transition" idiom).

Rejection convention: exactly like ``commander_movement.py``'s own
gameplay-level rejections (see ``engine.step``'s module docstring, "a
gameplay-level rejection ... simply produces no additional event -- the
generic ``CommandAccepted`` still fired"), a construction command that is
structurally accepted by :func:`~nether_earth.commands.validate_command_batch`
but rejected by the underlying pure function's own gameplay validation
(no active session, insufficient resources, robot cap reached, ...)
produces no additional domain event here -- only the already-emitted
generic ``CommandAccepted``/``CommandRejected`` records it. No new
richer/gameplay-specific rejection event type is introduced, following the
same locked precedent.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.commands import Command
from nether_earth.events import Event
from nether_earth.ids import EntityId, PlayerId
from nether_earth.robot_build import ModuleIdentity

__all__ = [
    "CancelConstructionCommand",
    "ConstructionCancelledEvent",
    "ConstructionEnteredEvent",
    "DeselectModuleCommand",
    "EnterConstructionRemotelyCommand",
    "LaunchRobotCommand",
    "ModuleDeselectedEvent",
    "ModuleSelectedEvent",
    "RobotLaunchedEvent",
    "SelectModuleCommand",
]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectModuleCommand(Command):
    """Request to select ``module`` into the issuing player's active construction session."""

    module: ModuleIdentity


@dataclass(frozen=True, slots=True)
class DeselectModuleCommand(Command):
    """Request to deselect ``module`` from the issuing player's active construction session."""

    module: ModuleIdentity


@dataclass(frozen=True, slots=True)
class CancelConstructionCommand(Command):
    """Request to discard the issuing player's active construction session, if any."""


@dataclass(frozen=True, slots=True)
class EnterConstructionRemotelyCommand(Command):
    """Request to open a construction session at ``war_base_id`` without landing.

    The AI seat's entry into construction: it has no commander, so it cannot
    land on a heli-pad (see the module docstring). Applied by
    :func:`~nether_earth.construction_session.enter_construction_remotely`,
    which accepts it only from an AI seat that owns ``war_base_id``; from a
    human seat it is a gameplay no-op. It is not part of the client protocol.
    """

    war_base_id: EntityId


@dataclass(frozen=True, slots=True)
class LaunchRobotCommand(Command):
    """Request to launch the issuing player's in-progress robot build."""


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConstructionEnteredEvent(Event):
    """A player's construction session was automatically opened this tick.

    Fired in direct, immediate response to a
    :class:`~nether_earth.heli_pad.CommanderConstructionEntryEligible`
    detection -- see the module docstring. Not tied to any ``Command``; this
    is a structural, engine-driven transition.
    """

    player: PlayerId
    war_base_id: EntityId
    tick: int


@dataclass(frozen=True, slots=True)
class ModuleSelectedEvent(Event):
    """``module`` was successfully selected into ``player``'s active construction session."""

    player: PlayerId
    module: ModuleIdentity
    tick: int


@dataclass(frozen=True, slots=True)
class ModuleDeselectedEvent(Event):
    """``module`` was successfully deselected from ``player``'s active construction session."""

    player: PlayerId
    module: ModuleIdentity
    tick: int


@dataclass(frozen=True, slots=True)
class ConstructionCancelledEvent(Event):
    """``player``'s active construction session was discarded.

    Only emitted when a session actually existed to discard (mirrors
    ``docking.py``'s "only emit on actual transition" idiom) --
    :func:`~nether_earth.construction_session.cancel_construction` is
    itself a silent no-op when the player has no active session, and
    ``engine.step`` checks for an active session before calling it so no
    event fires for a no-op cancel.
    """

    player: PlayerId
    tick: int


@dataclass(frozen=True, slots=True)
class RobotLaunchedEvent(Event):
    """``player``'s in-progress build was successfully launched as ``robot_id``."""

    player: PlayerId
    robot_id: EntityId
    tick: int
