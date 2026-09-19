"""Tests for minimal war-base-ownership victory evaluation (issue #66, M5.7)."""

from __future__ import annotations

from nether_earth.capture import CapturableStructureKind
from nether_earth.destruction import (
    RobotDestroyedEvent,
    StructureDestroyedEvent,
    evaluate_victory_after_nuclear_detonation,
    execute_nuclear_detonation,
)
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
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


# --------------------------------------------------------------------------
# evaluate_victory_after_nuclear_detonation (issue #79, M6.9)
#
# `destruction.py`'s wiring function -- see that module's own docstring for
# the reasoning. These tests cover the "does the scan-gate actually gate"
# and "does it defer entirely to victory.evaluate_victory" requirements this
# task locks, reusing `test_combat_nuclear.py`'s own fixture shapes for the
# full-integration cases.
# --------------------------------------------------------------------------


RADIUS = DEFAULT_RULES.nuclear_radius_cells


def _nuclear_robot(
    entity_id: str = "robot-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 0,
    y: int = 0,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.NUCLEAR,),
    height: int = 13,
    strength: int = 100,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons, electronics=None)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=(ModuleIdentity.BIPOD, *weapons),
        height=height,
        strength=strength,
        active_projectile_id=None,
    )


def _state_with_robots(robots: tuple[Robot, ...]) -> GameState:
    return create_game_state(0, (PLAYER_ONE, PLAYER_TWO), robots=list(robots))


def test_no_war_base_destruction_event_returns_none_even_though_victory_would_otherwise_fire() -> None:
    # This world/state combination WOULD produce a VictoryEvent if
    # victory.evaluate_victory were called directly against it -- proving
    # the function below returns None because its scan-gate actually gates
    # on the event batch's contents, not merely because the underlying call
    # happens to return None anyway.
    world = _world((_war_base("wb1", 0, PLAYER_ONE), _war_base("wb2", 9, PLAYER_ONE)))
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO))
    assert evaluate_victory(world, state, tick=1) is not None  # sanity check on the fixture

    non_war_base_events = (
        StructureDestroyedEvent(
            sequence=0, structure_id=EntityId("factory-1"), structure_kind=CapturableStructureKind.FACTORY, tick=1
        ),
        RobotDestroyedEvent(
            sequence=1, entity_id=EntityId("robot-1"), owner=PLAYER_TWO, x=0, y=0, tick=1
        ),
    )

    result = evaluate_victory_after_nuclear_detonation(non_war_base_events, world, state, tick=1)

    assert result is None


def test_single_war_base_destruction_completing_the_condition_returns_victory_event() -> None:
    war_base = _war_base("wb-loser-last", 9, PLAYER_TWO)
    world = _world((_war_base("wb-winner", 0, PLAYER_ONE), war_base))
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO), structure_destruction=[war_base.id])

    events = (
        StructureDestroyedEvent(
            sequence=0, structure_id=war_base.id, structure_kind=CapturableStructureKind.WAR_BASE, tick=3
        ),
    )

    result = evaluate_victory_after_nuclear_detonation(events, world, state, tick=3)

    assert result is not None
    assert result.winner == PLAYER_ONE
    assert result.tick == 3


def test_single_war_base_destruction_not_completing_the_condition_returns_none() -> None:
    destroyed_war_base = _war_base("wb-loser-one-of-two", 9, PLAYER_TWO)
    remaining_loser_war_base = _war_base("wb-loser-remaining", 12, PLAYER_TWO)
    world = _world(
        (_war_base("wb-winner", 0, PLAYER_ONE), destroyed_war_base, remaining_loser_war_base)
    )
    state = create_game_state(0, (PLAYER_ONE, PLAYER_TWO), structure_destruction=[destroyed_war_base.id])

    events = (
        StructureDestroyedEvent(
            sequence=0,
            structure_id=destroyed_war_base.id,
            structure_kind=CapturableStructureKind.WAR_BASE,
            tick=3,
        ),
    )

    result = evaluate_victory_after_nuclear_detonation(events, world, state, tick=3)

    assert result is None


def test_multiple_war_base_destructions_in_one_detonation_yield_exactly_one_victory_event() -> None:
    # `_war_base` (defined above this class of tests, from M5.7's own
    # fixtures) places every war base at y=0, so both destroyed bases sit on
    # opposite sides of the carrier along the x axis -- enough to prove "two
    # war bases destroyed by one detonation" without needing a y-aware
    # fixture (this module's existing `_war_base` deliberately has none).
    carrier = _nuclear_robot(entity_id="carrier", x=100, y=0)
    war_base_a = _war_base("wb-loser-a", 100 + RADIUS, PLAYER_TWO)
    war_base_b = _war_base("wb-loser-b", 100 - RADIUS, PLAYER_TWO)
    winner_base = _war_base("wb-winner", 0, PLAYER_ONE)
    state = _state_with_robots((carrier,))
    world = _world((winner_base, war_base_a, war_base_b))

    detonated_state, detonation_events = execute_nuclear_detonation(
        state, world, carrier.entity_id, tick=5
    )
    structure_events = [e for e in detonation_events if isinstance(e, StructureDestroyedEvent)]
    assert {e.structure_id for e in structure_events} == {war_base_a.id, war_base_b.id}

    result = evaluate_victory_after_nuclear_detonation(
        detonation_events, world, detonated_state, tick=5
    )

    assert result is not None
    assert result.winner == PLAYER_ONE
    assert result.tick == 5


def test_full_integration_final_war_base_destruction_triggers_victory_on_same_tick() -> None:
    carrier = _nuclear_robot(entity_id="carrier", x=0, y=0)
    loser_last_war_base = _war_base("wb-loser-last", RADIUS, PLAYER_TWO)
    winner_base = _war_base("wb-winner", 500, PLAYER_ONE)
    state = _state_with_robots((carrier,))
    world = _world((winner_base, loser_last_war_base))

    new_state, detonation_events = execute_nuclear_detonation(
        state, world, carrier.entity_id, tick=9
    )
    assert new_state.structure_destroyed(loser_last_war_base.id)

    victory_event = evaluate_victory_after_nuclear_detonation(
        detonation_events, world, new_state, tick=9
    )

    assert victory_event is not None
    assert victory_event.winner == PLAYER_ONE
    assert victory_event.tick == 9


def test_detonation_and_victory_evaluation_are_deterministic() -> None:
    def run() -> tuple[VictoryEvent | None, tuple[str, ...]]:
        carrier = _nuclear_robot(entity_id="carrier", x=0, y=0)
        loser_last_war_base = _war_base("wb-loser-last", RADIUS, PLAYER_TWO)
        winner_base = _war_base("wb-winner", 500, PLAYER_ONE)
        state = _state_with_robots((carrier,))
        world = _world((winner_base, loser_last_war_base))
        sequencer = EventSequencer()

        new_state, detonation_events = execute_nuclear_detonation(
            state, world, carrier.entity_id, tick=9, sequencer=sequencer
        )
        victory_event = evaluate_victory_after_nuclear_detonation(
            detonation_events, world, new_state, tick=9, sequencer=sequencer
        )
        return victory_event, tuple(repr(e) for e in detonation_events)

    result_a, log_a = run()
    result_b, log_b = run()

    assert result_a == result_b
    assert log_a == log_b
