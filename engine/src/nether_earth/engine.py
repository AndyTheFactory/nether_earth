"""Canonical engine integration API: ``new_game`` and ``step``.

This module is the integration point for issue #7, combining the four
prerequisite building blocks (``state.py``/``ids.py``, ``clock.py``,
``commands.py``/``events.py``, ``scenario.py``/``rng.py``) into the
conceptual contract from `_specs/technical-spec.md` §4.1:

```python
state = engine.new_game(map_data, scenario, players, seed)
state, events = engine.step(state, commands)
```

Scope (locked by the M1 milestone): this module owns deterministic tick
advancement, deterministic command validation/ordering, and deterministic
structural event emission. It intentionally does **not** implement any
concrete gameplay system (movement, combat, economy, construction, ...) —
none exist yet. Applying an accepted command in M1 is therefore a
pass-through: nothing on ``GameState`` exists for a command to mutate, so
"applying" a batch of commands has no observable effect beyond the single
authoritative tick advance every ``step`` call performs. The *contract*
(validate -> order -> apply -> advance tick -> emit ordered events) is real
and tested, not a stub, so later milestones can layer concrete gameplay
commands on top without changing this shape.

RNG ownership: ``GameState.seed`` (see ``state.py``) is the immutable seed a
match was created with. ``new_game`` records it; ``step`` does not draw any
random numbers in this milestone because no gameplay system consumes
randomness yet (`_specs/technical-spec.md` §5.3 requires a match-local seeded
RNG "if randomness is required" — none is required in M1). A future
milestone that needs randomness inside ``step`` should construct
``rng.MatchRandom(state.seed)`` fresh from the state it is given, not thread
a long-lived mutable RNG object through ``GameState``.

Wall-clock convention: nothing in this module reads wall-clock time (no
``time.time()``, no ``datetime.now()``); every notion of progress is the
authoritative integer tick counter (see ``clock.py``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from nether_earth.commands import Command, RejectionReason, validate_command_batch
from nether_earth.events import Event, EventSequencer, order_events
from nether_earth.ids import PlayerId
from nether_earth.map import BootstrapMap
from nether_earth.scenario import Scenario, initialize_players
from nether_earth.state import GameState, create_game_state

__all__ = [
    "CommandAccepted",
    "CommandRejected",
    "new_game",
    "step",
]


@dataclass(frozen=True, slots=True)
class CommandAccepted(Event):
    """Structural event: ``command`` was accepted and applied during a ``step``.

    M1 has no concrete gameplay command types, so this event only records
    which command was accepted and by whom; later milestones' gameplay
    events layer on top of (or alongside) this minimal contract.
    """

    command: Command


@dataclass(frozen=True, slots=True)
class CommandRejected(Event):
    """Structural event: ``command`` was rejected and had no effect on state."""

    command: Command
    reason: RejectionReason


def new_game(
    map_data: BootstrapMap,
    scenario: Scenario,
    players: Iterable[PlayerId] | None = None,
    seed: int = 0,
) -> GameState:
    """Construct the deterministic tick-0 ``GameState`` for a new match.

    Parameters mirror the conceptual contract in
    `_specs/technical-spec.md` §4.1: ``map_data`` (the loaded bootstrap map,
    see ``map.py``), ``scenario`` (see ``scenario.py``), an explicit
    ``players`` set (defaults to the canonical v1 PvP pair from
    :func:`nether_earth.scenario.initialize_players` when omitted), and an
    integer ``seed`` recorded on the resulting state (see the module
    docstring for how/when it is consumed).

    ``map_data`` and ``scenario`` must describe the same map: this is
    validated structurally (``map_id``/``version`` must match
    ``scenario.map_id``/``scenario.map_version``) since a mismatch would make
    "same scenario => same initial state" ambiguous. No map/world geometry is
    otherwise consumed in this milestone (M2+ scope).

    This delegates player-set derivation to
    :func:`nether_earth.scenario.initialize_players` rather than
    reimplementing it, and canonical ordering/dedup to
    :func:`nether_earth.state.create_game_state`, so the same
    ``(map_data, scenario, players, seed)`` always yields a canonical-
    equivalent ``GameState`` at ``tick == 0``.
    """
    if map_data.map_id != scenario.map_id:
        raise ValueError(
            f"map_data.map_id {map_data.map_id!r} does not match "
            f"scenario.map_id {scenario.map_id!r}"
        )
    if map_data.version != scenario.map_version:
        raise ValueError(
            f"map_data.version {map_data.version!r} does not match "
            f"scenario.map_version {scenario.map_version!r}"
        )

    resolved_players = tuple(initialize_players(scenario)) if players is None else tuple(players)
    return create_game_state(0, resolved_players, seed=seed)


def step(state: GameState, commands: Iterable[Command]) -> tuple[GameState, tuple[Event, ...]]:
    """Advance ``state`` by exactly one authoritative tick.

    Contract (see module docstring):

    1. Validate the incoming ``commands`` batch deterministically via
       :func:`nether_earth.commands.validate_command_batch` (this also
       applies canonical ``(player, sequence)`` ordering and rejects
       colliding commands).
    2. "Apply" accepted commands in that canonical order. M1 has no concrete
       gameplay command/state to mutate, so this phase is a structural
       pass-through — it exists so later milestones can insert real
       application logic here without changing ``step``'s contract.
    3. Advance ``state.tick`` by exactly one via ``state.with_tick``.
       Rejected commands never reach the application phase, so a batch
       containing a rejected command cannot cause any mutation beyond what
       the accepted commands in that same batch would have caused.
    4. Emit one ordered :class:`CommandAccepted`/:class:`CommandRejected`
       event per input command, sequenced via
       :class:`nether_earth.events.EventSequencer` in the same canonical
       order the commands were validated/applied in, then re-sorted via
       :func:`nether_earth.events.order_events` for defense in depth.

    Never reads wall-clock time. Same ``(state, commands)`` always produces
    an identical ``(new_state, events)`` pair.
    """
    results = validate_command_batch(commands, state)

    sequencer = EventSequencer()
    events: list[Event] = []
    for result in results:
        sequence = sequencer.next_sequence()
        if result.accepted:
            # Structural pass-through: no gameplay state exists yet to apply
            # an accepted command against (see module docstring).
            events.append(CommandAccepted(sequence=sequence, command=result.command))
        else:
            assert result.reason is not None  # invariant guaranteed by CommandResult
            events.append(
                CommandRejected(sequence=sequence, command=result.command, reason=result.reason)
            )

    new_state = state.with_tick(state.tick + 1)
    return new_state, order_events(events)
