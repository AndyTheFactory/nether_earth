"""Top-level authoritative game state shell.

Mutation convention: ``GameState`` is immutable (``frozen=True, slots=True``,
matching ``map.BootstrapMap``). State never changes in place; a new
``GameState`` is produced via an explicit, named transition method (e.g.
``with_tick``) rather than ad hoc attribute assignment or ``dataclasses.replace``
calls scattered across callers. This keeps every state transition a single,
auditable, deterministic step, which later issues (#4 tick clock, #7
simulation step) build on.

Ordering convention: ``players`` is exposed as a tuple in canonical order
(sorted by ``PlayerId.value``), never as a plain ``set``/``dict`` whose
iteration order could depend on insertion history. This guarantees that
serialization and any future derived output (events, snapshots) are
independent of the order callers happen to supply players in. ``commanders``
(added by issue #37) follows the exact same convention: a tuple sorted by
``Commander.player_id.value``, never a plain ``dict``/``set``.

Scope: this milestone (M1.1) intentionally excludes map/world/entities/
robots/economy/combat. Issue #37 (M3.1) adds the first entity-shaped field,
``commanders`` -- see below -- but robots and map/world integration remain
out of scope for later issues.

Seed field (added by issue #7, the simulation-step integration task): the
engine's match-local RNG ownership contract (``rng.py``) requires "same seed
=> same output sequence", but ``rng.MatchRandom`` is a *mutable*, stateful
wrapper around ``random.Random`` — each draw advances it. Embedding a live
``MatchRandom`` instance directly on this frozen ``GameState`` would make
state equality/serialization depend on private RNG internals and would be
awkward to reason about across ticks. Instead, ``GameState`` carries only the
immutable integer ``seed`` it was created with; any code that actually needs
to draw random numbers (no such gameplay system exists yet in M1) is expected
to construct a fresh ``rng.MatchRandom(state.seed)`` on demand. This keeps
``GameState`` a clean, comparable, serializable-in-spirit value type (relevant
to issue #8's upcoming canonical-snapshot work) while still making "same seed
=> same behavior" structurally checkable today.

Commanders field (added by issue #37): ``commanders`` attaches the
authoritative per-player :class:`~nether_earth.commander.Commander` state
(see ``commander.py``) directly onto ``GameState``, following the same
"immutable tuple in canonical order" convention as ``players`` rather than
introducing a first ``dict``/``Mapping``-shaped field -- this keeps
``GameState`` equality, serialization, and iteration order independent of
insertion history for exactly the reasons documented above. A commander's
``player_id`` must be one of ``players`` (an orphaned commander with no
matching player would make "which players have commanders" ambiguous), and
no two commanders may share a ``player_id`` (a player has at most one
commander). Use :func:`create_game_state` or :meth:`with_commanders` rather
than constructing this dataclass with a pre-sorted/pre-validated tuple by
convention alone.
"""

from dataclasses import dataclass

from nether_earth.commander import Commander
from nether_earth.ids import PlayerId


def _canonical_commanders(commanders: "tuple[Commander, ...]") -> "tuple[Commander, ...]":
    """Return ``commanders`` sorted canonically, after validating them.

    Shared by :func:`create_game_state` and :meth:`GameState.with_commanders`
    so both entry points enforce the identical ordering/uniqueness contract.
    """
    seen_players: set[PlayerId] = set()
    for commander in commanders:
        if commander.player_id in seen_players:
            raise ValueError(
                f"duplicate commander for player {commander.player_id.value!r}: "
                "a player may have at most one commander"
            )
        seen_players.add(commander.player_id)
    return tuple(sorted(commanders, key=lambda commander: commander.player_id.value))


@dataclass(frozen=True, slots=True)
class GameState:
    """Minimal authoritative engine state: tick, players, and commanders.

    ``players`` is always stored in canonical (sorted-by-id) order; use
    :func:`create_game_state` or :meth:`with_tick` rather than constructing
    this dataclass with a pre-sorted tuple by convention alone.

    ``seed`` is the immutable match seed this state (and any future state
    derived from it via :meth:`with_tick`) was initialized with. It defaults
    to ``0`` so existing callers that construct ``GameState`` without a seed
    keep working unchanged. See the module docstring for why this is a plain
    ``int`` rather than a live ``rng.MatchRandom`` instance.

    ``commanders`` is always stored in canonical (sorted by
    ``Commander.player_id.value``) order, matching ``players``; it defaults
    to ``()`` so existing callers that construct ``GameState`` without
    commander state keep working unchanged. See the module docstring for the
    ownership invariants ``create_game_state``/``with_commanders`` enforce.
    """

    tick: int
    players: tuple[PlayerId, ...]
    seed: int = 0
    commanders: "tuple[Commander, ...]" = ()

    def with_tick(self, tick: int) -> "GameState":
        """Return a new ``GameState`` with ``tick`` replaced.

        This is the explicit, controlled transition for advancing the
        authoritative tick counter; callers must not mutate ``tick`` in
        place. ``players``, ``seed``, and ``commanders`` are carried over
        unchanged.
        """
        return GameState(
            tick=tick, players=self.players, seed=self.seed, commanders=self.commanders
        )

    def with_commanders(self, commanders: "tuple[Commander, ...]") -> "GameState":
        """Return a new ``GameState`` with ``commanders`` replaced.

        ``commanders`` may be supplied in any order; the result is
        normalized to canonical (sorted by ``player_id.value``) order. Every
        commander's ``player_id`` must already be a participant in
        ``self.players`` and no two commanders may share a ``player_id`` --
        see the module docstring for why these invariants live here rather
        than being left to caller discipline. ``tick``, ``players``, and
        ``seed`` are carried over unchanged.
        """
        for commander in commanders:
            if commander.player_id not in self.players:
                raise ValueError(
                    f"commander player_id {commander.player_id.value!r} is not a "
                    "participant in this GameState's players"
                )
        canonical_commanders = _canonical_commanders(tuple(commanders))
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=canonical_commanders,
        )

    def commander_for(self, player_id: PlayerId) -> "Commander | None":
        """Return ``player_id``'s commander, or ``None`` if it has none."""
        for commander in self.commanders:
            if commander.player_id == player_id:
                return commander
        return None


def create_game_state(
    tick: int,
    players: "tuple[PlayerId, ...] | set[PlayerId] | list[PlayerId]",
    seed: int = 0,
    commanders: "tuple[Commander, ...] | list[Commander] | None" = None,
) -> GameState:
    """Construct a ``GameState`` with ``players``/``commanders`` normalized.

    ``players`` may be supplied in any order or as any iterable of unique
    ``PlayerId`` values; the resulting ``GameState.players`` is always the
    deduplicated tuple sorted by ``PlayerId.value``, so two calls describing
    the same logical set of players always produce an identical state.

    ``seed`` is recorded on the resulting state unchanged (see the module
    docstring); it defaults to ``0`` for callers that do not care about
    seeded randomness.

    ``commanders`` defaults to no commanders (``()``); when supplied, every
    commander's ``player_id`` must be a member of the resolved ``players``
    set and no two commanders may share a ``player_id`` (see the module
    docstring). The resulting ``GameState.commanders`` is always sorted by
    ``player_id.value``.
    """
    if tick < 0:
        raise ValueError("tick must be non-negative")
    canonical_players = tuple(sorted(set(players), key=lambda player_id: player_id.value))
    resolved_commanders = () if commanders is None else tuple(commanders)
    for commander in resolved_commanders:
        if commander.player_id not in canonical_players:
            raise ValueError(
                f"commander player_id {commander.player_id.value!r} is not a "
                "participant in players"
            )
    canonical_commanders = _canonical_commanders(resolved_commanders)
    return GameState(
        tick=tick,
        players=canonical_players,
        seed=seed,
        commanders=canonical_commanders,
    )
