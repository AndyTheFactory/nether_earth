"""Tests for deterministic daily factory/war-base production (issue #54, M4.4).

Uses `fixtures/world_map_production.yaml`, which declares:

- war base ``warbase-p1`` (owner ``p1``), war base ``warbase-p2`` (owner ``p2``);
- two chassis factories owned by ``p1`` (``factory-p1-chassis-a/b``, same
  category -- aggregation) and one cannon factory owned by ``p1``
  (different category);
- one missile factory owned by ``p2``;
- one unowned (neutral) phaser factory that must never produce for anyone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nether_earth import clock
from nether_earth.events import EventSequencer
from nether_earth.ids import PlayerId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.resource_production import DailyProductionApplied, apply_daily_production
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import create_game_state
from nether_earth.structures import FactoryType

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "world_map_production.yaml"

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")


@pytest.fixture()
def world() -> WorldMap:
    return load_world_map(FIXTURE_PATH)


def _state() -> object:
    return create_game_state(0, {PLAYER_ONE, PLAYER_TWO})


# --- Boundary detection: exact integer, once per crossing -------------------


def test_no_production_before_day_boundary(world: WorldMap) -> None:
    state = _state()
    new_state, events = apply_daily_production(
        state, world, tick_before=clock.TICKS_PER_GAME_DAY - 2, tick_after=clock.TICKS_PER_GAME_DAY - 1
    )
    assert events == ()
    assert new_state is state


def test_production_fires_exactly_at_day_boundary(world: WorldMap) -> None:
    state = _state()
    new_state, events = apply_daily_production(
        state, world, tick_before=clock.TICKS_PER_GAME_DAY - 1, tick_after=clock.TICKS_PER_GAME_DAY
    )
    assert {event.player for event in events} == {PLAYER_ONE, PLAYER_TWO}
    p1_pool = new_state.resource_pool_for(PLAYER_ONE)
    assert p1_pool is not None
    assert (
        p1_pool.general
        == DEFAULT_RULES.starting_general_resources + DEFAULT_RULES.war_base_production_amount
    )


def test_no_double_production_just_after_boundary(world: WorldMap) -> None:
    """A second call covering ticks strictly after the boundary produces nothing more."""
    state = _state()
    state, _ = apply_daily_production(
        state, world, tick_before=clock.TICKS_PER_GAME_DAY - 1, tick_after=clock.TICKS_PER_GAME_DAY
    )
    new_state, events = apply_daily_production(
        state, world, tick_before=clock.TICKS_PER_GAME_DAY, tick_after=clock.TICKS_PER_GAME_DAY + 1
    )
    assert events == ()
    assert new_state is state


def test_boundary_check_is_integer_exact_not_floating_point(world: WorldMap) -> None:
    """tick 2879->2880 crosses; 2880->2881 does not (matches clock.py's documented example)."""
    state = _state()
    _, crossing_events = apply_daily_production(state, world, tick_before=2879, tick_after=2880)
    _, noncrossing_events = apply_daily_production(state, world, tick_before=2880, tick_after=2881)
    assert crossing_events != ()
    assert noncrossing_events == ()


# --- Aggregation: multiple factories, same and different types --------------


def test_multiple_same_type_factories_aggregate(world: WorldMap) -> None:
    state = _state()
    _, events = apply_daily_production(state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY)
    p1_event = next(event for event in events if event.player == PLAYER_ONE)
    amounts = dict(p1_event.category_amounts)
    # Two chassis factories owned by p1 -> 2x factory_production_amount.
    assert amounts[FactoryType.CHASSIS] == 2 * DEFAULT_RULES.factory_production_amount


def test_different_type_factories_aggregate_independently(world: WorldMap) -> None:
    state = _state()
    _, events = apply_daily_production(state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY)
    p1_event = next(event for event in events if event.player == PLAYER_ONE)
    amounts = dict(p1_event.category_amounts)
    assert amounts[FactoryType.CANNON] == DEFAULT_RULES.factory_production_amount
    assert amounts[FactoryType.CHASSIS] == 2 * DEFAULT_RULES.factory_production_amount
    # Categories with no owned factory are present but zero.
    assert amounts[FactoryType.MISSILE] == 0
    assert amounts[FactoryType.NUCLEAR] == 0
    assert amounts[FactoryType.ELECTRONICS] == 0
    assert amounts[FactoryType.PHASER] == 0


def test_war_base_production_credits_general_resources(world: WorldMap) -> None:
    state = _state()
    new_state, _ = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY
    )
    p2_pool = new_state.resource_pool_for(PLAYER_TWO)
    assert p2_pool is not None
    assert (
        p2_pool.general
        == DEFAULT_RULES.starting_general_resources + DEFAULT_RULES.war_base_production_amount
    )
    assert p2_pool.amount(FactoryType.MISSILE) == DEFAULT_RULES.factory_production_amount


# --- Ownership filtering ------------------------------------------------------


def test_neutral_factory_never_produces(world: WorldMap) -> None:
    state = _state()
    new_state, events = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY
    )
    for event in events:
        amounts = dict(event.category_amounts)
        assert amounts[FactoryType.PHASER] == 0
    for pool in new_state.resource_pools:
        assert pool.amount(FactoryType.PHASER) == 0


def test_enemy_owned_factory_does_not_produce_for_other_player(world: WorldMap) -> None:
    state = _state()
    new_state, _ = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY
    )
    p1_pool = new_state.resource_pool_for(PLAYER_ONE)
    assert p1_pool is not None
    # factory-p2-missile is owned by p2, must not credit p1.
    assert p1_pool.amount(FactoryType.MISSILE) == 0


def test_enemy_owned_war_base_does_not_produce_for_other_player(world: WorldMap) -> None:
    state = _state()
    new_state, _ = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY
    )
    p2_pool = new_state.resource_pool_for(PLAYER_TWO)
    assert p2_pool is not None
    # p2 owns only its own war base (5), never p1's.
    assert (
        p2_pool.general
        == DEFAULT_RULES.starting_general_resources + DEFAULT_RULES.war_base_production_amount
    )


# --- Config overrides change amounts without code changes -------------------


def test_config_override_changes_production_amounts(world: WorldMap) -> None:
    custom_rules = EngineRules(factory_production_amount=7, war_base_production_amount=11)
    state = _state()
    new_state, _ = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY, rules=custom_rules
    )
    p1_pool = new_state.resource_pool_for(PLAYER_ONE)
    assert p1_pool is not None
    assert p1_pool.general == DEFAULT_RULES.starting_general_resources + 11
    assert p1_pool.amount(FactoryType.CHASSIS) == 2 * 7
    assert p1_pool.amount(FactoryType.CANNON) == 7


# --- Multi-boundary aggregation (e.g. resuming after a skip) ----------------


def test_multiple_boundaries_crossed_in_one_call_scale_production(world: WorldMap) -> None:
    state = _state()
    new_state, events = apply_daily_production(
        state, world, tick_before=0, tick_after=3 * clock.TICKS_PER_GAME_DAY
    )
    p2_event = next(event for event in events if event.player == PLAYER_TWO)
    assert p2_event.day_boundaries_crossed == 3
    assert p2_event.general_amount == 3 * DEFAULT_RULES.war_base_production_amount

    p2_pool = new_state.resource_pool_for(PLAYER_TWO)
    assert p2_pool is not None
    assert (
        p2_pool.general
        == DEFAULT_RULES.starting_general_resources + 3 * DEFAULT_RULES.war_base_production_amount
    )


# --- Starts from an existing pool rather than clobbering it -----------------


def test_production_adds_onto_existing_pool_rather_than_replacing_it(world: WorldMap) -> None:
    existing_pool = PlayerResourcePool(player_id=PLAYER_ONE, general=100, chassis=3)
    state = create_game_state(0, {PLAYER_ONE, PLAYER_TWO}, resource_pools=(existing_pool,))

    new_state, _ = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY
    )

    p1_pool = new_state.resource_pool_for(PLAYER_ONE)
    assert p1_pool is not None
    assert p1_pool.general == 100 + DEFAULT_RULES.war_base_production_amount
    assert p1_pool.amount(FactoryType.CHASSIS) == 3 + 2 * DEFAULT_RULES.factory_production_amount


def test_untouched_players_keep_their_existing_pool_unchanged(world: WorldMap) -> None:
    """A player with zero owned structures keeps whatever pool it already had."""
    third_player = PlayerId("p3")
    existing_pool = PlayerResourcePool(player_id=third_player, general=9)
    state = create_game_state(
        0, {PLAYER_ONE, PLAYER_TWO, third_player}, resource_pools=(existing_pool,)
    )

    new_state, events = apply_daily_production(
        state, world, tick_before=0, tick_after=clock.TICKS_PER_GAME_DAY
    )

    assert third_player not in {event.player for event in events}
    assert new_state.resource_pool_for(third_player) == existing_pool


# --- Event sequencing ---------------------------------------------------------


def test_events_use_shared_sequencer_deterministically(world: WorldMap) -> None:
    state = _state()
    sequencer = EventSequencer()
    _, events = apply_daily_production(
        state,
        world,
        tick_before=0,
        tick_after=clock.TICKS_PER_GAME_DAY,
        sequencer=sequencer,
    )
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    for event in events:
        assert isinstance(event, DailyProductionApplied)


# --- Argument validation ------------------------------------------------------


def test_rejects_tick_after_before_tick_before(world: WorldMap) -> None:
    state = _state()
    with pytest.raises(ValueError):
        apply_daily_production(state, world, tick_before=10, tick_after=5)


def test_rejects_negative_ticks(world: WorldMap) -> None:
    state = _state()
    with pytest.raises(ValueError):
        apply_daily_production(state, world, tick_before=-1, tick_after=5)
