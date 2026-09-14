"""Tests for the authoritative per-player resource pool type (issue #54, M4.4)."""

from __future__ import annotations

import pytest

from nether_earth.construction_economy import ResourcePool
from nether_earth.ids import PlayerId
from nether_earth.resource_pool import PlayerResourcePool, starting_player_resource_pool
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import create_game_state
from nether_earth.structures import FactoryType

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")


# --- Construction / validation -----------------------------------------------


def test_defaults_to_all_zero() -> None:
    pool = PlayerResourcePool(player_id=PLAYER_ONE)
    assert pool.general == 0
    for category in FactoryType:
        assert pool.amount(category) == 0


def test_rejects_negative_general() -> None:
    with pytest.raises(ValueError):
        PlayerResourcePool(player_id=PLAYER_ONE, general=-1)


@pytest.mark.parametrize(
    "field_name",
    ["chassis", "electronics", "nuclear", "missile", "phaser", "cannon"],
)
def test_rejects_negative_category(field_name: str) -> None:
    with pytest.raises(ValueError):
        PlayerResourcePool(player_id=PLAYER_ONE, **{field_name: -1})


def test_starting_pool_seeds_general_only() -> None:
    pool = starting_player_resource_pool(PLAYER_ONE, DEFAULT_RULES)
    assert pool.player_id == PLAYER_ONE
    assert pool.general == DEFAULT_RULES.starting_general_resources == 20
    for category in FactoryType:
        assert pool.amount(category) == 0


def test_starting_pool_respects_rules_override() -> None:
    rules = EngineRules(starting_general_resources=42)
    pool = starting_player_resource_pool(PLAYER_ONE, rules)
    assert pool.general == 42


# --- Hashability / equality (parity with Commander/RobotBuild) --------------


def test_pool_is_hashable_and_equatable() -> None:
    a = PlayerResourcePool(player_id=PLAYER_ONE, general=5, chassis=3)
    b = PlayerResourcePool(player_id=PLAYER_ONE, general=5, chassis=3)
    c = PlayerResourcePool(player_id=PLAYER_ONE, general=6, chassis=3)

    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert {a, b, c} == {a, c}  # a and b collapse in a set


# --- with_amount / with_general (pure transitions) --------------------------


def test_with_amount_returns_new_pool_unmutated_original() -> None:
    pool = PlayerResourcePool(player_id=PLAYER_ONE, chassis=1)
    updated = pool.with_amount(FactoryType.CHASSIS, 9)

    assert pool.amount(FactoryType.CHASSIS) == 1
    assert updated.amount(FactoryType.CHASSIS) == 9
    assert updated.player_id == PLAYER_ONE
    assert updated.general == pool.general


def test_with_general_returns_new_pool_unmutated_original() -> None:
    pool = PlayerResourcePool(player_id=PLAYER_ONE, general=10)
    updated = pool.with_general(20)

    assert pool.general == 10
    assert updated.general == 20


# --- Conversion to/from construction_economy.ResourcePool --------------------


def test_round_trips_through_resource_pool() -> None:
    original = PlayerResourcePool(
        player_id=PLAYER_ONE,
        general=7,
        chassis=1,
        electronics=2,
        nuclear=3,
        missile=4,
        phaser=5,
        cannon=6,
    )

    converted = original.to_resource_pool()
    assert isinstance(converted, ResourcePool)
    assert converted.general == 7
    for category in FactoryType:
        assert converted.amount(category) == original.amount(category)

    back = PlayerResourcePool.from_resource_pool(PLAYER_ONE, converted)
    assert back == original


def test_from_resource_pool_uses_supplied_player_id() -> None:
    pool = ResourcePool(general=1)
    converted = PlayerResourcePool.from_resource_pool(PLAYER_TWO, pool)
    assert converted.player_id == PLAYER_TWO


# --- GameState attachment (mirrors commanders convention) -------------------


def test_game_state_attaches_resource_pools_in_canonical_order() -> None:
    pool_two = PlayerResourcePool(player_id=PLAYER_TWO, general=1)
    pool_one = PlayerResourcePool(player_id=PLAYER_ONE, general=2)

    state = create_game_state(
        0, {PLAYER_ONE, PLAYER_TWO}, resource_pools=(pool_two, pool_one)
    )

    assert state.resource_pools == (pool_one, pool_two)
    assert state.resource_pool_for(PLAYER_ONE) == pool_one
    assert state.resource_pool_for(PLAYER_TWO) == pool_two
    assert state.resource_pool_for(PlayerId("p3")) is None


def test_game_state_rejects_resource_pool_for_nonparticipant_player() -> None:
    orphan_pool = PlayerResourcePool(player_id=PlayerId("p3"), general=1)
    with pytest.raises(ValueError):
        create_game_state(0, {PLAYER_ONE}, resource_pools=(orphan_pool,))


def test_game_state_rejects_duplicate_resource_pools_for_same_player() -> None:
    pool_a = PlayerResourcePool(player_id=PLAYER_ONE, general=1)
    pool_b = PlayerResourcePool(player_id=PLAYER_ONE, general=2)
    with pytest.raises(ValueError):
        create_game_state(0, {PLAYER_ONE}, resource_pools=(pool_a, pool_b))


def test_with_resource_pools_replaces_and_normalizes_order() -> None:
    state = create_game_state(0, {PLAYER_ONE, PLAYER_TWO})
    pool_two = PlayerResourcePool(player_id=PLAYER_TWO, general=1)
    pool_one = PlayerResourcePool(player_id=PLAYER_ONE, general=2)

    new_state = state.with_resource_pools((pool_two, pool_one))

    assert new_state.resource_pools == (pool_one, pool_two)
    # Original state is untouched (immutability).
    assert state.resource_pools == ()
    # Other fields carried over unchanged.
    assert new_state.tick == state.tick
    assert new_state.players == state.players
    assert new_state.commanders == state.commanders


def test_with_resource_pools_rejects_nonparticipant_player() -> None:
    state = create_game_state(0, {PLAYER_ONE})
    orphan_pool = PlayerResourcePool(player_id=PlayerId("p3"), general=1)
    with pytest.raises(ValueError):
        state.with_resource_pools((orphan_pool,))
