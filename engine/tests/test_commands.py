import random
from dataclasses import dataclass

from nether_earth.commands import (
    Command,
    CommandResult,
    RejectionReason,
    command_sort_key,
    order_commands,
    validate_command,
    validate_command_batch,
)
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.state import create_game_state


@dataclass(frozen=True, slots=True)
class _NoopCommand(Command):
    """Minimal concrete command used only to exercise the base contract.

    No gameplay commands exist yet (later milestones define those); this
    test-local subclass just proves the base ``Command``/ordering/validation
    contract works for any concrete command shape.
    """

    label: str = ""


def _commands(pairs: list[tuple[PlayerId, int]]) -> list[Command]:
    return [_NoopCommand(player=player, sequence=sequence) for player, sequence in pairs]


def test_order_commands_is_deterministic_regardless_of_input_order() -> None:
    pairs = [
        (PLAYER_TWO, 3),
        (PLAYER_ONE, 5),
        (PLAYER_ONE, 1),
        (PLAYER_TWO, 0),
        (PLAYER_ONE, 2),
    ]
    base = _commands(pairs)

    shuffled = list(base)
    random.Random(1234).shuffle(shuffled)

    reversed_order = list(reversed(base))

    expected = order_commands(base)
    assert order_commands(shuffled) == expected
    assert order_commands(reversed_order) == expected
    assert order_commands(set(base)) == expected

    # Canonical order: player id value first, then sequence.
    assert [command_sort_key(command) for command in expected] == [
        ("p1", 1),
        ("p1", 2),
        ("p1", 5),
        ("p2", 0),
        ("p2", 3),
    ]


def test_order_commands_sorts_by_player_then_sequence() -> None:
    a = _NoopCommand(player=PLAYER_ONE, sequence=1)
    b = _NoopCommand(player=PLAYER_ONE, sequence=0)
    c = _NoopCommand(player=PLAYER_TWO, sequence=0)

    assert order_commands([a, b, c]) == (b, a, c)


def test_validate_command_accepts_well_formed_command() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO])
    command = _NoopCommand(player=PLAYER_ONE, sequence=0)

    result = validate_command(command, state)

    assert result == CommandResult.accept(command)
    assert result.accepted is True
    assert result.reason is None


def test_validate_command_rejects_unknown_player_deterministically() -> None:
    state = create_game_state(0, [PLAYER_ONE])
    command = _NoopCommand(player=PLAYER_TWO, sequence=0)

    first = validate_command(command, state)
    second = validate_command(command, state)

    assert first == second
    assert first.accepted is False
    assert first.reason is RejectionReason.UNKNOWN_PLAYER


def test_validate_command_rejects_negative_sequence_deterministically() -> None:
    state = create_game_state(0, [PLAYER_ONE])
    command = _NoopCommand(player=PLAYER_ONE, sequence=-1)

    first = validate_command(command, state)
    second = validate_command(command, state)

    assert first == second
    assert first.accepted is False
    assert first.reason is RejectionReason.INVALID_SEQUENCE


def test_validate_command_has_no_side_effects_on_rejection() -> None:
    """Rejection must be a pure computation: no mutation path is invoked.

    There is no engine state to mutate yet in this issue's scope, so the
    contract-level guarantee is that ``validate_command`` never returns
    anything other than a fresh ``CommandResult`` and never touches the
    ``state``/``command`` objects passed to it - both remain frozen
    dataclasses, so any attempted mutation would raise, and repeated calls
    with identical input produce identical, unrelated result objects.
    """
    state = create_game_state(0, [PLAYER_ONE])
    command = _NoopCommand(player=PLAYER_TWO, sequence=-5)

    before_state = state
    before_command = command

    result = validate_command(command, state)

    assert state == before_state
    assert command == before_command
    assert state is before_state
    assert command is before_command
    # A fresh CommandResult is produced each call rather than any shared
    # mutable object being handed back and forth.
    assert result is not validate_command(command, state)
    assert result == validate_command(command, state)


def test_command_result_invariant_rejects_ambiguous_construction() -> None:
    command = _NoopCommand(player=PLAYER_ONE, sequence=0)

    try:
        CommandResult(command=command, accepted=True, reason=RejectionReason.INVALID_SEQUENCE)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted result must not carry a rejection reason")

    try:
        CommandResult(command=command, accepted=False, reason=None)
    except ValueError:
        pass
    else:
        raise AssertionError("rejected result must carry a rejection reason")


def test_validate_command_batch_rejects_colliding_sequence_deterministically() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO])
    colliding_a = _NoopCommand(player=PLAYER_ONE, sequence=1, label="a")
    colliding_b = _NoopCommand(player=PLAYER_ONE, sequence=1, label="b")
    unique = _NoopCommand(player=PLAYER_TWO, sequence=1, label="c")

    forward = validate_command_batch([colliding_a, colliding_b, unique], state)
    shuffled = validate_command_batch([unique, colliding_b, colliding_a], state)

    assert forward == shuffled

    by_command = {result.command: result for result in forward}
    assert by_command[colliding_a].accepted is False
    assert by_command[colliding_a].reason is RejectionReason.DUPLICATE_SEQUENCE
    assert by_command[colliding_b].accepted is False
    assert by_command[colliding_b].reason is RejectionReason.DUPLICATE_SEQUENCE
    assert by_command[unique].accepted is True


def test_validate_command_batch_accepts_distinct_sequences_per_player() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO])
    commands = _commands([(PLAYER_ONE, 0), (PLAYER_ONE, 1), (PLAYER_TWO, 0)])

    results = validate_command_batch(commands, state)

    assert all(result.accepted for result in results)
    assert [command_sort_key(result.command) for result in results] == [
        ("p1", 0),
        ("p1", 1),
        ("p2", 0),
    ]
