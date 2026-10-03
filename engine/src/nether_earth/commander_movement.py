"""Deterministic commander horizontal/vertical movement.

This module implements authoritative commander movement on top of the
state shape in ``commander.py`` and the shared rule
configuration in ``rules.py``: cell-to-cell horizontal movement via
:class:`~nether_earth.commander.GridTransition`, and the locked Spectrum
vertical cadence (ascend/gravity) via
:class:`~nether_earth.commander.VerticalTransition`.

Everything in this module is pure: every public function takes an
immutable :class:`~nether_earth.state.GameState`/
:class:`~nether_earth.commander.Commander` and returns a new one (or a new
``Commander``), never mutating its arguments. This mirrors the convention
already established by ``state.py``/``commands.py``/``events.py``.

This module does not wire itself into ``engine.step()``; `engine.py` calls
the functions below.

Collision-check contract: this module does not import `collision.py`; it
accepts duck-typed callables rather than a shared interface module:

    HorizontalMoveCheck = Callable[[GameState, Commander, int, int], bool]
    # (state, mover, dest_x, dest_y) -> allowed

    VerticalMoveCheck = Callable[[GameState, Commander, int], bool]
    # (state, mover, dest_altitude) -> allowed

Both return ``True`` if the move is legal with respect to world/robot/
commander collision (board-bounds/altitude-range checks are this module's
own responsibility, not the callable's), ``False`` if blocked. Every
function that needs a collision decision accepts an optional callable of
the matching shape, defaulting to an always-``True`` permissive stub when
not supplied -- see :func:`_permissive_horizontal_check`/
:func:`_permissive_vertical_check`. `engine.py` binds `collision.py`'s
real collision-query functions in as the concrete callables, which are
compatible by construction since both sides follow this documented contract.

Vertical intent model: the original ZX Spectrum game has no "hover" --
releasing the rise control always means descend/fall. This is modeled as a
persistent boolean intent (``Commander.rising``) rather than a discrete
"move up" command, set via :class:`CommanderSetVerticalIntentCommand` and
held until changed again. Vertical physics (:func:`apply_vertical_physics`)
then reads that persisted intent on every cadence tick rather than
requiring a fresh command each update.

Horizontal move command shape: classic 4-directional grid movement (one
cell per command, ``dx``/``dy`` each in ``{-1, 0, 1}``, exactly one
nonzero). Diagonal moves are rejected structurally (``ValueError`` in
``__post_init__``), not as a collision rejection, since "diagonal" is not a
legal move shape at all, independent of world geometry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from nether_earth.commander import Commander, CommanderMode, GridTransition, VerticalTransition
from nether_earth.commands import Command, order_commands
from nether_earth.events import Event, EventSequencer, order_events
from nether_earth.ids import PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "CommanderHorizontalMoveCompletedEvent",
    "CommanderHorizontalMoveStartedEvent",
    "CommanderMoveCommand",
    "CommanderMoveResult",
    "CommanderMovementRejectionReason",
    "CommanderSetVerticalIntentCommand",
    "CommanderVerticalIntentChangedEvent",
    "CommanderVerticalUpdatedEvent",
    "HorizontalMoveCheck",
    "VerticalMoveCheck",
    "advance_all_horizontal_transitions",
    "advance_commander_movement_tick",
    "advance_horizontal_transition",
    "apply_automatic_elevation",
    "apply_commander_move",
    "apply_vertical_physics",
    "is_vertical_update_tick",
    "set_vertical_intent",
    "validate_commander_move",
]


# --------------------------------------------------------------------------
# Collision-check contract
# --------------------------------------------------------------------------

#: (state, mover, dest_x, dest_y) -> allowed. See the module docstring.
HorizontalMoveCheck = Callable[[GameState, Commander, int, int], bool]

#: (state, mover, dest_altitude) -> allowed. See the module docstring.
VerticalMoveCheck = Callable[[GameState, Commander, int], bool]


def _permissive_horizontal_check(
    state: GameState, mover: Commander, dest_x: int, dest_y: int
) -> bool:
    """Default :data:`HorizontalMoveCheck`: always allow. See module docstring."""
    return True


def _permissive_vertical_check(state: GameState, mover: Commander, dest_altitude: int) -> bool:
    """Default :data:`VerticalMoveCheck`: always allow. See module docstring."""
    return True


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommanderMoveCommand(Command):
    """Request to move the issuing player's commander one cell.

    ``dx``/``dy`` are each restricted to ``{-1, 0, 1}`` with exactly one
    nonzero -- classic 4-directional grid movement (see module docstring).
    Diagonal or no-op shapes are rejected structurally, in
    ``__post_init__``, since they are not a legal move shape regardless of
    world state.
    """

    dx: int
    dy: int

    def __post_init__(self) -> None:
        if self.dx not in (-1, 0, 1) or self.dy not in (-1, 0, 1):
            raise ValueError("dx and dy must each be in {-1, 0, 1}")
        if self.dx == 0 and self.dy == 0:
            raise ValueError("dx and dy cannot both be zero (not a move)")
        if self.dx != 0 and self.dy != 0:
            raise ValueError("diagonal movement is not supported: exactly one of dx/dy "
                              "must be nonzero")


@dataclass(frozen=True, slots=True)
class CommanderSetVerticalIntentCommand(Command):
    """Set the issuing player's persistent vertical intent.

    ``rising=True`` means "holding rise" (ascend on the next vertical
    cadence tick); ``rising=False`` means "not holding rise" (descend/fall
    on the next vertical cadence tick) -- see the module docstring for why
    this is a persistent intent rather than a one-shot move.
    """

    rising: bool


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommanderHorizontalMoveStartedEvent(Event):
    """A commander began a cell-to-cell move; authoritative position is
    still ``(from_x, from_y)`` until ``started_tick + duration_ticks``."""

    player_id: PlayerId
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    started_tick: int
    duration_ticks: int


@dataclass(frozen=True, slots=True)
class CommanderHorizontalMoveCompletedEvent(Event):
    """A commander's in-progress move resolved; ``(x, y)`` is now authoritative."""

    player_id: PlayerId
    x: int
    y: int
    tick: int


@dataclass(frozen=True, slots=True)
class CommanderVerticalIntentChangedEvent(Event):
    """A commander's persistent vertical (``rising``) intent changed."""

    player_id: PlayerId
    rising: bool
    tick: int


@dataclass(frozen=True, slots=True)
class CommanderVerticalUpdatedEvent(Event):
    """A commander's authoritative altitude changed on a vertical cadence tick."""

    player_id: PlayerId
    from_altitude: int
    to_altitude: int
    tick: int


# --------------------------------------------------------------------------
# Validation / results
# --------------------------------------------------------------------------


class CommanderMovementRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected movement command.

    A dedicated enum (rather than reusing
    :class:`nether_earth.commands.RejectionReason`) because movement
    legality is gameplay-specific and layered on top of the generic
    command contract, per ``commands.py``'s own module docstring -- the
    generic module is deliberately not extended with gameplay-specific
    reasons.
    """

    NO_COMMANDER = "no_commander"
    NOT_FREE = "not_free"
    MOVE_IN_PROGRESS = "move_in_progress"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class CommanderMoveResult:
    """Outcome of validating a :class:`CommanderMoveCommand`.

    Mirrors :class:`nether_earth.commands.CommandResult`'s accept/reject
    invariant (exactly one of "accepted" or "a stable rejection reason"
    holds), with its own reason type since movement legality is
    gameplay-specific (see :class:`CommanderMovementRejectionReason`).
    """

    command: CommanderMoveCommand
    accepted: bool
    reason: CommanderMovementRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError("an accepted CommanderMoveResult must not carry a rejection reason")
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected CommanderMoveResult must carry a rejection reason")

    @classmethod
    def accept(cls, command: CommanderMoveCommand) -> CommanderMoveResult:
        """Build an accepted result for ``command``."""
        return cls(command=command, accepted=True, reason=None)

    @classmethod
    def reject(
        cls, command: CommanderMoveCommand, reason: CommanderMovementRejectionReason
    ) -> CommanderMoveResult:
        """Build a rejected result for ``command`` with a stable ``reason``."""
        return cls(command=command, accepted=False, reason=reason)


def validate_commander_move(
    command: CommanderMoveCommand,
    state: GameState,
    horizontal_check: HorizontalMoveCheck = _permissive_horizontal_check,
) -> CommanderMoveResult:
    """Validate ``command`` against gameplay movement legality.

    Pure function: reads ``command``/``state`` and returns a
    :class:`CommanderMoveResult`; never mutates either argument. Checks, in
    order:

    - the issuing player has a commander at all;
    - the commander is ``FREE`` (a ``DOCKED`` commander has independent
      movement disabled per `_specs/functional-spec.md` §8.4 -- docking
      itself is `docking.py`'s concern, but this module respects the
      rule once a commander happens to be docked);
    - the commander is not already mid-transition (one horizontal move at
      a time -- a second move command while one is in flight is rejected
      rather than queued or overriding the in-flight destination);
    - ``horizontal_check`` allows the destination cell (world/robot/
      commander collision -- see the module docstring; this module does
      not itself know or care *why* a destination is blocked).

    Structural checks (issuing player known to the match, non-negative
    sequence) are the generic :func:`nether_earth.commands.validate_command`
    contract's job, not this function's -- callers are expected to run that
    first, per ``commands.py``'s "layered on top" convention.
    """
    commander = state.commander_for(command.player)
    if commander is None:
        return CommanderMoveResult.reject(command, CommanderMovementRejectionReason.NO_COMMANDER)
    if commander.mode is not CommanderMode.FREE:
        return CommanderMoveResult.reject(command, CommanderMovementRejectionReason.NOT_FREE)
    if commander.horizontal_transition is not None:
        return CommanderMoveResult.reject(
            command, CommanderMovementRejectionReason.MOVE_IN_PROGRESS
        )
    dest_x = commander.x + command.dx
    dest_y = commander.y + command.dy
    if not horizontal_check(state, commander, dest_x, dest_y):
        return CommanderMoveResult.reject(command, CommanderMovementRejectionReason.BLOCKED)
    return CommanderMoveResult.accept(command)


def _replace_commander(state: GameState, updated: Commander) -> GameState:
    """Return ``state`` with ``updated`` replacing the commander for its player."""
    return state.with_commanders(
        tuple(
            updated if commander.player_id == updated.player_id else commander
            for commander in state.commanders
        )
    )


def apply_commander_move(
    command: CommanderMoveCommand,
    state: GameState,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    horizontal_check: HorizontalMoveCheck = _permissive_horizontal_check,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, CommanderMoveResult, CommanderHorizontalMoveStartedEvent | None]:
    """Validate and, if legal, begin ``command``'s horizontal move.

    Returns ``(new_state, result, event)``. When rejected, ``new_state is
    state`` (unchanged) and ``event is None``. When accepted, the mover's
    commander gets a fresh :class:`~nether_earth.commander.GridTransition`
    (``duration_ticks=rules.commander_horizontal_move_ticks``); authoritative
    ``x``/``y`` do not change yet -- see :func:`advance_horizontal_transition`
    for completion. ``sequencer``, when supplied, assigns the started
    event's sequence number; omit it (default ``None``) to get sequence
    ``0``, which is only safe for isolated single-event tests -- callers
    orchestrating multiple events in one tick must supply a shared
    :class:`~nether_earth.events.EventSequencer`.
    """
    result = validate_commander_move(command, state, horizontal_check)
    if not result.accepted:
        return state, result, None

    commander = state.commander_for(command.player)
    assert commander is not None  # guaranteed by validate_commander_move's NO_COMMANDER check

    dest_x = commander.x + command.dx
    dest_y = commander.y + command.dy
    transition = GridTransition(
        from_x=commander.x,
        from_y=commander.y,
        to_x=dest_x,
        to_y=dest_y,
        started_tick=tick,
        duration_ticks=rules.commander_horizontal_move_ticks,
    )
    updated_commander = commander.with_horizontal_transition(transition)
    new_state = _replace_commander(state, updated_commander)

    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderHorizontalMoveStartedEvent(
        sequence=sequence,
        player_id=commander.player_id,
        from_x=transition.from_x,
        from_y=transition.from_y,
        to_x=transition.to_x,
        to_y=transition.to_y,
        started_tick=transition.started_tick,
        duration_ticks=transition.duration_ticks,
    )
    return new_state, result, event


def advance_horizontal_transition(
    commander: Commander,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[Commander, CommanderHorizontalMoveCompletedEvent | None]:
    """Complete ``commander``'s in-progress horizontal move if due by ``tick``.

    Returns ``(commander, None)`` unchanged when there is no in-progress
    transition, or it has not yet elapsed. Otherwise returns a new
    ``Commander`` with authoritative ``x``/``y`` set to the transition's
    destination and ``horizontal_transition`` cleared, plus the completion
    event.
    """
    transition = commander.horizontal_transition
    if transition is None or not transition.is_complete(tick):
        return commander, None
    updated = commander.with_position(transition.to_x, transition.to_y)
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderHorizontalMoveCompletedEvent(
        sequence=sequence,
        player_id=updated.player_id,
        x=updated.x,
        y=updated.y,
        tick=tick,
    )
    return updated, event


def advance_all_horizontal_transitions(
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[CommanderHorizontalMoveCompletedEvent, ...]]:
    """Apply :func:`advance_horizontal_transition` to every commander in ``state``.

    Commanders are processed in ``state.commanders``' canonical order
    (sorted by ``player_id.value``, per ``state.py``), so results/event
    order are independent of any incidental construction order.
    """
    events: list[CommanderHorizontalMoveCompletedEvent] = []
    updated_commanders = []
    for commander in state.commanders:
        updated, event = advance_horizontal_transition(commander, tick, sequencer)
        updated_commanders.append(updated)
        if event is not None:
            events.append(event)
    new_state = state.with_commanders(tuple(updated_commanders))
    return new_state, tuple(events)


# --------------------------------------------------------------------------
# Vertical intent
# --------------------------------------------------------------------------


def set_vertical_intent(
    command: CommanderSetVerticalIntentCommand,
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, CommanderVerticalIntentChangedEvent | None]:
    """Apply ``command``, updating the issuing player's ``rising`` intent.

    Returns ``(state, None)`` unchanged if the player has no commander (the
    generic :func:`nether_earth.commands.validate_command` structural check
    is expected to have already rejected an unknown player before this is
    called; this is a defensive no-op, not a new rejection path). Intent
    may be set regardless of ``FREE``/``DOCKED`` mode -- applying it to
    vertical physics is what respects mode (see
    :func:`apply_vertical_physics`), so a docked commander's rise intent is
    preserved (relevant to later undocking flows) even though it currently
    has no physics effect.
    """
    commander = state.commander_for(command.player)
    if commander is None:
        return state, None
    if commander.rising == command.rising:
        # No-op: intent already matches. Still a legal command, just nothing
        # to change or announce.
        return state, None
    updated = commander.with_rising(command.rising)
    new_state = _replace_commander(state, updated)
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderVerticalIntentChangedEvent(
        sequence=sequence,
        player_id=updated.player_id,
        rising=updated.rising,
        tick=tick,
    )
    return new_state, event


# --------------------------------------------------------------------------
# Vertical physics
# --------------------------------------------------------------------------


def is_vertical_update_tick(tick: int, rules: EngineRules = DEFAULT_RULES) -> bool:
    """Return whether ``tick`` is a vertical-physics cadence tick.

    A cadence tick is any positive multiple of
    ``rules.commander_vertical_update_ticks`` (tick ``0`` -- match start --
    is never itself an update tick, since no time has elapsed yet). With
    the default ``commander_vertical_update_ticks=4`` this is exactly
    ticks 4, 8, 12, ... -- i.e. "vertical state updates exactly every 4
    ticks".
    """
    return tick > 0 and tick % rules.commander_vertical_update_ticks == 0


def _clamped_vertical_step(commander: Commander, rules: EngineRules) -> int:
    """Return the rules-clamped candidate altitude for one vertical update."""
    step = rules.commander_ascent_step if commander.rising else -rules.commander_descent_step
    candidate = commander.altitude + step
    return max(rules.commander_min_altitude, min(rules.commander_max_altitude, candidate))


def apply_vertical_physics(
    commander: Commander,
    state: GameState,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    vertical_check: VerticalMoveCheck = _permissive_vertical_check,
    sequencer: EventSequencer | None = None,
) -> tuple[Commander, CommanderVerticalUpdatedEvent | None]:
    """Apply one vertical-cadence update to ``commander``, if due.

    Callers are expected to only call this on cadence ticks (see
    :func:`is_vertical_update_tick`); this function does not itself gate on
    cadence so tests/``engine.step`` can call it unconditionally against a
    tick they have already confirmed is due, or reuse it for the
    automatic-elevation hook (see :func:`apply_automatic_elevation`, which
    deliberately does *not* gate on cadence).

    A ``DOCKED`` commander has no independent vertical physics (mode
    "independent commander movement is disabled" per
    `_specs/functional-spec.md` §8.4) and is returned unchanged.

    Ascent applies ``+rules.commander_ascent_step`` when ``commander.rising``
    is true; otherwise gravity applies ``-rules.commander_descent_step``,
    stopping on the first surface met on the way down (see
    :func:`_gravity_landing_altitude`). The result is clamped to
    ``[rules.commander_min_altitude, rules.commander_max_altitude]``. If the
    clamped candidate equals the current altitude (already at a bound) or
    ``vertical_check`` disallows the destination, the commander is returned
    unchanged with no event -- collision hooks (e.g. resting on an
    enemy-robot stack) are expected to be expressed via ``vertical_check``,
    not embedded here.

    Automatic ascent: while
    ``commander.elevate_updates_remaining`` is positive the update ascends
    regardless of ``rising`` and consumes one unit of the counter, even when
    the ascent itself is clamped or blocked (Spectrum
    ``Lafa2_player_ship_keyboard_control_altitude`` decrements
    ``Lfd30_player_elevate_timer`` before its ``MAX_PLAYER_ALTITUDE`` check).
    """
    if commander.mode is not CommanderMode.FREE:
        return commander, None
    if commander.elevate_updates_remaining > 0:
        commander = commander.with_elevate_updates(commander.elevate_updates_remaining - 1)
        candidate = min(
            commander.altitude + rules.commander_ascent_step, rules.commander_max_altitude
        )
    else:
        candidate = _clamped_vertical_step(commander, rules)
        if candidate < commander.altitude:
            candidate = _gravity_landing_altitude(commander, state, candidate, vertical_check)
    if candidate == commander.altitude:
        return commander, None
    if not vertical_check(state, commander, candidate):
        return commander, None
    transition = VerticalTransition(
        from_altitude=commander.altitude,
        to_altitude=candidate,
        started_tick=tick,
        duration_ticks=rules.commander_vertical_update_ticks,
    )
    updated = commander.with_altitude(candidate, transition)
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderVerticalUpdatedEvent(
        sequence=sequence,
        player_id=updated.player_id,
        from_altitude=commander.altitude,
        to_altitude=candidate,
        tick=tick,
    )
    return updated, event


def _gravity_landing_altitude(
    commander: Commander,
    state: GameState,
    candidate: int,
    vertical_check: VerticalMoveCheck,
) -> int:
    """Return how far gravity lowers ``commander`` toward ``candidate``.

    A descent step larger than 1 (``commander_descent_step = 2``)
    must not skip past a surface at an odd altitude, nor stop a whole step
    above it: the commander falls one altitude unit at a time and stops on
    the first surface it meets (the last legal altitude before a blocked
    one).
    """
    landed = commander.altitude
    for altitude in range(commander.altitude - 1, candidate - 1, -1):
        if not vertical_check(state, commander, altitude):
            break
        landed = altitude
    return landed


def apply_automatic_elevation(
    commander: Commander,
    state: GameState,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    vertical_check: VerticalMoveCheck = _permissive_vertical_check,
    sequencer: EventSequencer | None = None,
) -> tuple[Commander, CommanderVerticalUpdatedEvent | None]:
    """Apply one automatic ascent step, using the same +step semantics as
    normal rise-intent ascent (``rules.commander_ascent_step``).

    This is a standalone hook for flows (undocking, war-base heli-pad exit)
    that need to auto-elevate a commander away from a robot
    or war base, without duplicating the ascent arithmetic already in
    :func:`apply_vertical_physics`. Unlike that function, this hook does
    NOT gate on :func:`is_vertical_update_tick` -- automatic elevation is
    triggered by a discrete event (exiting a robot/base), not the periodic
    rise/gravity cadence -- and it does not read or require
    ``commander.rising``.
    """
    candidate = min(commander.altitude + rules.commander_ascent_step, rules.commander_max_altitude)
    if candidate == commander.altitude:
        return commander, None
    if not vertical_check(state, commander, candidate):
        return commander, None
    transition = VerticalTransition(
        from_altitude=commander.altitude,
        to_altitude=candidate,
        started_tick=tick,
        duration_ticks=rules.commander_vertical_update_ticks,
    )
    updated = commander.with_altitude(candidate, transition)
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = CommanderVerticalUpdatedEvent(
        sequence=sequence,
        player_id=updated.player_id,
        from_altitude=commander.altitude,
        to_altitude=candidate,
        tick=tick,
    )
    return updated, event


# --------------------------------------------------------------------------
# Whole-tick orchestration (reference for engine.step()'s ordering)
# --------------------------------------------------------------------------


def advance_commander_movement_tick(
    state: GameState,
    tick: int,
    commands: tuple[Command, ...],
    rules: EngineRules = DEFAULT_RULES,
    horizontal_check: HorizontalMoveCheck = _permissive_horizontal_check,
    vertical_check: VerticalMoveCheck = _permissive_vertical_check,
) -> tuple[GameState, tuple[Event, ...]]:
    """Apply one authoritative tick's worth of commander movement.

    This is NOT called by ``engine.step()``; it is a pure, directly
    testable reference implementation of the ordering ``engine.step()``
    uses, and the anchor for the "same initial state + command stream ->
    identical state/events" determinism tests.

    Deterministic processing order, all fixed regardless of ``commands``'
    input order:

    1. ``commands`` are sorted via
       :func:`nether_earth.commands.order_commands` (by
       ``(player.value, sequence)``), then each recognized command
       (:class:`CommanderMoveCommand`, :class:`CommanderSetVerticalIntentCommand`)
       is applied in that order. Unrecognized command types are ignored --
       this function only knows about commander movement.
    2. Any horizontal transitions due to complete by ``tick`` are resolved,
       in ``state.commanders``' canonical (player-id-sorted) order.
    3. If ``tick`` is a vertical-cadence tick (see
       :func:`is_vertical_update_tick`), vertical physics is applied to
       every commander, again in canonical order.

    Horizontal and vertical updates can both occur within the same tick,
    since steps 2 and 3 are
    independent passes over the (possibly already horizontally-updated)
    commander set.

    Returns ``(new_state, events)`` with ``events`` in canonical emission
    order (via :func:`nether_earth.events.order_events`).
    """
    sequencer = EventSequencer()
    events: list[Event] = []

    for command in order_commands(commands):
        if isinstance(command, CommanderMoveCommand):
            state, _move_result, move_event = apply_commander_move(
                command, state, tick, rules, horizontal_check, sequencer
            )
            if move_event is not None:
                events.append(move_event)
        elif isinstance(command, CommanderSetVerticalIntentCommand):
            state, intent_event = set_vertical_intent(command, state, tick, sequencer)
            if intent_event is not None:
                events.append(intent_event)

    state, completed_events = advance_all_horizontal_transitions(state, tick, sequencer)
    events.extend(completed_events)

    if is_vertical_update_tick(tick, rules):
        updated_commanders = []
        for commander in state.commanders:
            updated, vertical_event = apply_vertical_physics(
                commander, state, tick, rules, vertical_check, sequencer
            )
            updated_commanders.append(updated)
            if vertical_event is not None:
                events.append(vertical_event)
        state = state.with_commanders(tuple(updated_commanders))

    return state, order_events(events)
