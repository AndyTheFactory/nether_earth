"""Base engine command contract, deterministic ordering, and validation model.

This module defines the *shape* that all gameplay commands (commander
movement, robot orders, fire, construction, ...) extend; the concrete
commands live in their own modules on top of the :class:`Command` base.

Ordering convention: wall-clock/arrival order is never authoritative. Every
command carries an explicit, caller-supplied ``sequence`` alongside the
issuing :class:`~nether_earth.ids.PlayerId`. A tick's command batch is always
sorted by ``(player.value, sequence)`` before it is applied, so the same
logical set of commands produces the same order regardless of what order
callers happened to collect/submit them in (see :func:`order_commands`).

Validation convention: :func:`validate_command` is a pure function of
``(command, state)`` -> :class:`CommandResult`. It performs no mutation and
has no side effects; a rejected command never touches any mutation path
because none is invoked to compute the result in the first place. Concrete
gameplay validation (movement legality, capture rules, ...) is layered on top
by those modules; this module only defines the generic, structural
contract every command must satisfy (a known issuing player and a
non-negative sequence number) plus per-tick batch uniqueness.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from nether_earth.ids import PlayerId
from nether_earth.state import GameState


@dataclass(frozen=True, slots=True)
class Command:
    """Base contract for all engine-level commands.

    Concrete gameplay commands (defined in their own modules) subclass
    ``Command`` and add their own fields. Every command must carry:

    - ``player``: the issuing :class:`~nether_earth.ids.PlayerId`.
    - ``sequence``: a non-negative, caller-supplied integer that is unique
      per issuing player within a single tick's command batch. This is the
      deterministic tie-break key; it must not be derived from wall-clock
      time or arrival order.
    """

    player: PlayerId
    sequence: int


def command_sort_key(command: Command) -> tuple[str, int]:
    """Return the deterministic ordering key for ``command``.

    Commands sort by issuing player id value first, then by sequence. Two
    commands from the same player must have distinct sequence numbers (see
    :func:`validate_command_batch`); commands from different players are
    always ordered by ``PlayerId.value`` regardless of submission order.
    """
    return (command.player.value, command.sequence)


def order_commands(commands: Iterable[Command]) -> tuple[Command, ...]:
    """Return ``commands`` sorted into canonical deterministic order.

    Sorting is by :func:`command_sort_key`. The same logical set of commands
    (regardless of the iteration order of the collection it is supplied in -
    list, set, generator, shuffled list, ...) always sorts to an identical
    result, provided each command has a unique ``(player, sequence)`` key.
    Callers that cannot guarantee unique keys should validate the batch with
    :func:`validate_command_batch` first, which deterministically rejects
    colliding commands.
    """
    return tuple(sorted(commands, key=command_sort_key))


class RejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected command.

    Values are stable strings (not free-form text) so rejection results are
    reproducible and comparable across runs.
    """

    INVALID_SEQUENCE = "invalid_sequence"
    UNKNOWN_PLAYER = "unknown_player"
    DUPLICATE_SEQUENCE = "duplicate_sequence"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of validating a single :class:`Command`.

    Exactly one of "accepted" or "a stable rejection reason" holds; the
    dataclass invariant is enforced on construction so a ``CommandResult``
    can never represent an ambiguous accept/reject state.
    """

    command: Command
    accepted: bool
    reason: RejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError("an accepted CommandResult must not carry a rejection reason")
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected CommandResult must carry a rejection reason")

    @classmethod
    def accept(cls, command: Command) -> "CommandResult":
        """Build an accepted result for ``command``."""
        return cls(command=command, accepted=True, reason=None)

    @classmethod
    def reject(cls, command: Command, reason: RejectionReason) -> "CommandResult":
        """Build a rejected result for ``command`` with a stable ``reason``."""
        return cls(command=command, accepted=False, reason=reason)


def validate_command(command: Command, state: GameState) -> CommandResult:
    """Validate a single command against generic, structural rules.

    This is a pure function: it reads ``command`` and ``state`` and returns a
    :class:`CommandResult`; it never mutates either argument and has no
    other observable side effect. The same ``(command, state)`` pair always
    yields the same result.

    Only generic, gameplay-agnostic checks live here:

    - ``sequence`` must be non-negative.
    - ``player`` must be a participant in ``state``.

    Concrete gameplay commands layer additional legality checks on top of
    this.
    """
    if command.sequence < 0:
        return CommandResult.reject(command, RejectionReason.INVALID_SEQUENCE)
    if command.player not in state.players:
        return CommandResult.reject(command, RejectionReason.UNKNOWN_PLAYER)
    return CommandResult.accept(command)


def validate_command_batch(
    commands: Iterable[Command], state: GameState
) -> tuple[CommandResult, ...]:
    """Validate a tick's worth of commands and return deterministic results.

    Each command is validated independently via :func:`validate_command`
    (pure, no side effects). In addition, any commands that collide on
    ``(player, sequence)`` are rejected with
    :data:`RejectionReason.DUPLICATE_SEQUENCE`, since a colliding key makes
    :func:`order_commands` ambiguous - *all* commands sharing a colliding key
    are rejected (not an arbitrary "first" one), which keeps the outcome
    independent of the order ``commands`` was supplied in.

    The returned tuple is sorted by :func:`command_sort_key`, with a final
    tie-break on ``repr(command)`` so that even colliding-key entries have a
    fully deterministic relative order regardless of input order.
    """
    commands = list(commands)
    key_counts: dict[tuple[str, int], int] = {}
    for command in commands:
        key = command_sort_key(command)
        key_counts[key] = key_counts.get(key, 0) + 1

    results: list[CommandResult] = []
    for command in commands:
        result = validate_command(command, state)
        if result.accepted and key_counts[command_sort_key(command)] > 1:
            result = CommandResult.reject(command, RejectionReason.DUPLICATE_SEQUENCE)
        results.append(result)

    return tuple(
        sorted(
            results,
            key=lambda result: (*command_sort_key(result.command), repr(result.command)),
        )
    )
