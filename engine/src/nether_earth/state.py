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
independent of the order callers happen to supply players in.

Scope: this milestone (M1.1) intentionally excludes map/world/entities/
commander/robots/economy/combat. This is a minimal shell composed on by
later issues.
"""

from dataclasses import dataclass

from nether_earth.ids import PlayerId


@dataclass(frozen=True, slots=True)
class GameState:
    """Minimal authoritative engine state: a tick counter and player set.

    ``players`` is always stored in canonical (sorted-by-id) order; use
    :func:`create_game_state` or :meth:`with_tick` rather than constructing
    this dataclass with a pre-sorted tuple by convention alone.
    """

    tick: int
    players: tuple[PlayerId, ...]

    def with_tick(self, tick: int) -> "GameState":
        """Return a new ``GameState`` with ``tick`` replaced.

        This is the explicit, controlled transition for advancing the
        authoritative tick counter; callers must not mutate ``tick`` in
        place.
        """
        return GameState(tick=tick, players=self.players)


def create_game_state(tick: int, players: "tuple[PlayerId, ...] | set[PlayerId] | list[PlayerId]") -> GameState:
    """Construct a ``GameState`` with ``players`` normalized to canonical order.

    ``players`` may be supplied in any order or as any iterable of unique
    ``PlayerId`` values; the resulting ``GameState.players`` is always the
    deduplicated tuple sorted by ``PlayerId.value``, so two calls describing
    the same logical set of players always produce an identical state.
    """
    if tick < 0:
        raise ValueError("tick must be non-negative")
    canonical_players = tuple(sorted(set(players), key=lambda player_id: player_id.value))
    return GameState(tick=tick, players=canonical_players)
