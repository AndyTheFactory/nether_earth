"""Deterministic factory/war-base daily resource production.

`_specs/resolved-questions.md` "Resource spending rules" "Resource spending rules -- RESOLVED" locks
the original Spectrum production rule: on each authoritative game-day
boundary, every player-owned factory produces
``rules.factory_production_amount`` (locked default ``2``) units of its own
type-specific resource category, and every player-owned war base produces
``rules.war_base_production_amount`` (locked default ``5``) general
resources. This module implements that rule as a standalone, pure,
callable building block; ``engine.step`` wires it in (Step 9).

Boundary detection, integer-only
---------------------------------

"Game-day boundary" is detected the same exact-integer way
`clock.py`'s own module docstring mandates for hour boundaries:
``clock.ticks_to_game_days_floor(tick_before) != clock.ticks_to_game_days_floor(tick_after)``.
This never uses floating-point comparisons (``clock.game_days_elapsed`` is
explicitly documented as display/telemetry-only, not for exact boundary
checks). Production fires **once** for a boundary crossing, regardless of
how many ticks ``tick_after - tick_before`` spans -- a caller that skips
ticks (e.g. resuming from a snapshot) still only gets one production event
per crossed day boundary difference, matching
``ticks_to_game_days_floor``'s "whole number of complete in-game days
elapsed" semantics: the production amount scales with the number of day
boundaries crossed (``ticks_to_game_days_floor(tick_after) -
ticks_to_game_days_floor(tick_before)``), not with elapsed ticks.

Ownership, read-only
----------------------

Ownership is read directly from ``structures.py``'s existing
``WarBase.owner``/``Factory.owner`` fields -- the one authoritative
ownership representation in this codebase (`heli_pad.py` establishes the
same "reuse the existing ownership field, do not add a second
representation" precedent). A structure with ``owner is None`` (neutral)
or an ``owner`` that is not the player being checked produces nothing for
that player. Capture/ownership-change logic itself is `capture.py`'s
concern -- ownership is read-only input here.

Aggregation and determinism
------------------------------

Every owned factory/war base for a player contributes independently and
amounts are summed; the resulting per-player, per-category totals are
therefore independent of iteration order (addition is commutative), but
this module still iterates ``world.factories``/``world.war_bases`` in
their declared (stable tuple) order and ``state.players``/
``state.resource_pools`` in canonical order throughout, so nothing here
relies on ``dict``/``set`` iteration order to produce a deterministic
result.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth import clock
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import PlayerId
from nether_earth.map import WorldMap
from nether_earth.resource_pool import PlayerResourcePool, starting_player_resource_pool
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState
from nether_earth.structures import FactoryType

__all__ = [
    "DailyProductionApplied",
    "apply_daily_production",
]


@dataclass(frozen=True, slots=True)
class DailyProductionApplied(Event):
    """Signals that daily production was credited to a player's resource pool.

    One event is emitted per player that received any production (a player
    with no owned factories/war bases at the boundary emits nothing -- an
    empty production is not a noteworthy event). ``general_amount`` is the
    total general resources credited (from owned war bases); ``category_amounts``
    holds the total type-specific amount credited per
    :class:`~nether_earth.structures.FactoryType` category (from owned
    factories) -- omitting categories with a zero contribution would make
    this event's shape depend on which factory types happened to produce,
    so every category is always present, with ``0`` for categories that
    produced nothing this boundary.

    ``day_boundaries_crossed`` records how many day boundaries this single
    application covers (normally ``1``; ``>1`` only if a caller advances
    the tick by more than one day between calls, per the module
    docstring's aggregation-by-boundary-count rule), and ``tick`` is the
    authoritative tick production was detected on (the ``tick_after``
    passed to :func:`apply_daily_production`), so replay/log consumers can
    correlate this event with the exact simulation step.
    """

    player: PlayerId
    tick: int
    day_boundaries_crossed: int
    general_amount: int
    category_amounts: tuple[tuple[FactoryType, int], ...]


#: Deterministic declaration order for ``category_amounts``, matching
#: ``FactoryType``'s own declaration order in ``structures.py``.
_CATEGORY_ORDER: tuple[FactoryType, ...] = tuple(FactoryType)


def _pool_for(state: GameState, player_id: PlayerId, rules: EngineRules) -> PlayerResourcePool:
    """Return ``player_id``'s current pool, or a freshly seeded one if it has none."""
    existing = state.resource_pool_for(player_id)
    if existing is not None:
        return existing
    return starting_player_resource_pool(player_id, rules)


def apply_daily_production(
    state: GameState,
    world: WorldMap,
    tick_before: int,
    tick_after: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[DailyProductionApplied, ...]]:
    """Apply owned-structure production for every game-day boundary crossed.

    ``tick_before``/``tick_after`` bracket the tick advance this call
    covers (``tick_after`` is normally ``tick_before + 1`` for a per-tick
    caller, but any ``tick_after >= tick_before`` is accepted -- see the
    module docstring's boundary-count aggregation rule). Returns
    ``(state, ())`` unchanged when no day boundary is crossed
    (``clock.ticks_to_game_days_floor(tick_before) ==
    clock.ticks_to_game_days_floor(tick_after)``) -- this makes calling
    this function every tick, unconditionally, a valid and idiomatic usage:
    it is a no-op on every tick except the one that crosses a boundary.

    For each player in ``state.players`` (canonical order), sums
    ``rules.war_base_production_amount`` general resources across every
    ``world.war_bases`` entry the player owns, and
    ``rules.factory_production_amount`` type-specific resources (by
    ``factory.factory_type``) across every ``world.factories`` entry the
    player owns, scaled by the number of day boundaries crossed. A player
    with zero total production this call is left unchanged and emits no
    event. The returned ``state`` has an updated (or newly created, seeded
    from :func:`~nether_earth.resource_pool.starting_player_resource_pool`)
    :class:`~nether_earth.resource_pool.PlayerResourcePool` for every player
    that produced something; players untouched this call keep whatever
    pool (or absence of one) they already had.
    """
    if tick_before < 0 or tick_after < 0:
        raise ValueError("tick_before and tick_after must be non-negative")
    if tick_after < tick_before:
        raise ValueError("tick_after must not precede tick_before")

    days_before = clock.ticks_to_game_days_floor(tick_before)
    days_after = clock.ticks_to_game_days_floor(tick_after)
    boundaries_crossed = days_after - days_before
    if boundaries_crossed <= 0:
        return state, ()

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()

    events: list[DailyProductionApplied] = []
    updated_pools: list[PlayerResourcePool] = []

    for player_id in state.players:
        war_base_total = sum(
            rules.war_base_production_amount
            for war_base in world.war_bases
            if war_base.owner == player_id
        )
        category_totals: dict[FactoryType, int] = {category: 0 for category in _CATEGORY_ORDER}
        for factory in world.factories:
            if factory.owner == player_id:
                category_totals[factory.factory_type] += rules.factory_production_amount

        general_amount = war_base_total * boundaries_crossed
        category_amounts = {
            category: amount * boundaries_crossed for category, amount in category_totals.items()
        }

        if general_amount == 0 and all(amount == 0 for amount in category_amounts.values()):
            continue

        pool = _pool_for(state, player_id, rules)
        updated_pool = pool.with_general(pool.general + general_amount)
        for category, amount in category_amounts.items():
            if amount:
                updated_pool = updated_pool.with_amount(
                    category, updated_pool.amount(category) + amount
                )
        updated_pools.append(updated_pool)

        events.append(
            DailyProductionApplied(
                sequence=resolved_sequencer.next_sequence(),
                player=player_id,
                tick=tick_after,
                day_boundaries_crossed=boundaries_crossed,
                general_amount=general_amount,
                category_amounts=tuple(
                    (category, category_amounts[category]) for category in _CATEGORY_ORDER
                ),
            )
        )

    if not updated_pools:
        return state, ()

    updated_player_ids = {pool.player_id for pool in updated_pools}
    unchanged_pools = tuple(
        pool for pool in state.resource_pools if pool.player_id not in updated_player_ids
    )
    new_state = state.with_resource_pools(unchanged_pools + tuple(updated_pools))

    return new_state, tuple(events)
