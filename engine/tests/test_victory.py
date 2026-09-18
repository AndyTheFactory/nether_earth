"""Tests for minimal war-base-ownership victory evaluation (issue #66, M5.7)."""

from __future__ import annotations

from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.state import create_game_state
from nether_earth.structures import Component, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType
from nether_earth.victory import VictoryEvent, evaluate_victory


def _world(war_bases: tuple[WarBase, ...]) -> WorldMap:
    return WorldMap(
        map_id="victory-test-map",
        version=1,
        width=10,
        height=10,
        terrain=TerrainGrid(width=10, height=10, cells={}, default=TerrainType.NORMAL),
        war_bases=war_bases,
        factories=(),
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


def _war_base(entity_id: str, x: int, owner: PlayerId | None) -> WarBase:
    return WarBase(id=EntityId(entity_id), components=(Component(x=x, y=0, height=3),), owner=owner)


def test_no_victory_while_both_players_own_a_war_base() -> None:
    world = _world((_war_base("wb1", 0, PLAYER_ONE), _war_base("wb2", 9, PLAYER_TWO)))
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))

    assert evaluate_victory(world, state, tick=1) is None


def test_no_victory_while_a_neutral_war_base_remains_and_both_players_still_have_bases() -> None:
    world = _world(
        (
            _war_base("wb1", 0, PLAYER_ONE),
            _war_base("wb2", 3, PLAYER_TWO),
            _war_base("wb3", 6, None),
        )
    )
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))

    assert evaluate_victory(world, state, tick=1) is None


def test_victory_when_opponent_owns_zero_war_bases() -> None:
    world = _world((_war_base("wb1", 0, PLAYER_ONE), _war_base("wb2", 9, PLAYER_ONE)))
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))

    event = evaluate_victory(world, state, tick=42)

    assert event == VictoryEvent(sequence=0, winner=PLAYER_ONE, tick=42)


def test_victory_still_holds_with_a_remaining_neutral_war_base() -> None:
    world = _world(
        (
            _war_base("wb1", 0, PLAYER_ONE),
            _war_base("wb2", 5, None),
        )
    )
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))

    event = evaluate_victory(world, state, tick=7)

    assert event is not None
    assert event.winner == PLAYER_ONE


def test_no_victory_when_no_player_owns_any_war_base() -> None:
    world = _world((_war_base("wb1", 0, None), _war_base("wb2", 9, None)))
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))

    assert evaluate_victory(world, state, tick=1) is None


def test_no_victory_with_fewer_than_two_players() -> None:
    world = _world((_war_base("wb1", 0, PLAYER_ONE),))
    state = create_game_state(0, (PLAYER_ONE,))

    assert evaluate_victory(world, state, tick=1) is None


def test_sequencer_is_used_when_supplied() -> None:
    world = _world((_war_base("wb1", 0, PLAYER_ONE), _war_base("wb2", 9, PLAYER_ONE)))
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))
    sequencer = EventSequencer(start=7)

    event = evaluate_victory(world, state, tick=1, sequencer=sequencer)

    assert event is not None
    assert event.sequence == 7
