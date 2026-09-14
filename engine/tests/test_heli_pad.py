"""Tests for war-base heli-pad landing detection (issue #41, M3.5).

Uses the shared M2 fixture map (`fixtures/world_map_basic.yaml`), which
declares two war bases (``warbase-p1`` owned by ``p1``, ``warbase-p2``
owned by ``p2``) each with a single-cell ``HELI_PAD`` interaction point:
``warbase-p1-helipad`` at ``(4, 0)`` and ``warbase-p2-helipad`` at
``(4, 3)``. See `_specs/milestones/03-commander-movement-docking.md`
("War-base heli-pad interaction") for the acceptance criteria this file
covers: friendly, enemy, neutral, misaligned (wrong X/Y), insufficient
contact (wrong altitude), and docked-mode cases.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from nether_earth.commander import Commander, CommanderMode
from nether_earth.events import EventSequencer
from nether_earth.heli_pad import CommanderConstructionEntryEligible, detect_heli_pad_landing
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import create_game_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "world_map_basic.yaml"

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")
PLAYER_NEUTRAL_OBSERVER = PlayerId("p3")

# Fixture heli-pad cells, from world_map_basic.yaml.
P1_HELI_PAD_CELL = (4, 0)
P2_HELI_PAD_CELL = (4, 3)


@pytest.fixture()
def world() -> WorldMap:
    return load_world_map(FIXTURE_PATH)


def _state_with_commander(commander: Commander):
    players = {commander.player_id}
    return create_game_state(0, players, commanders=(commander,))


def _free_commander(player_id: PlayerId, x: int, y: int, altitude: int) -> Commander:
    return Commander(player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=altitude)


# --- Friendly landing: the success case -------------------------------------------


def test_friendly_landing_emits_construction_entry_event(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=7)

    assert event is not None
    assert isinstance(event, CommanderConstructionEntryEligible)
    assert event.player == PLAYER_ONE
    assert event.war_base_id == EntityId("warbase-p1")
    assert event.tick == 7
    assert event.sequence == 0


def test_friendly_landing_is_symmetric_for_other_player(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_TWO, *P2_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is not None
    assert event.player == PLAYER_TWO
    assert event.war_base_id == EntityId("warbase-p2")


def test_sequencer_is_used_when_supplied(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(commander)
    sequencer = EventSequencer(start=5)

    event = detect_heli_pad_landing(state, world, commander, tick=1, sequencer=sequencer)

    assert event is not None
    assert event.sequence == 5
    # The sequencer itself advances, matching EventSequencer's contract.
    assert sequencer.next_sequence() == 6


# --- Enemy war-base heli-pad: must not grant construction entry -------------------


def test_landing_on_enemy_war_base_heli_pad_does_not_trigger(world: WorldMap) -> None:
    # p1's commander stands on p2's heli-pad cell.
    commander = _free_commander(PLAYER_ONE, *P2_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


# --- Neutral (unowned) war-base heli-pad: must not grant construction entry -------


def test_landing_on_neutral_war_base_heli_pad_does_not_trigger() -> None:
    # Build a world where the commander's own war base is neutral (owner=None).
    raw = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["war_bases"][0]["owner"] = None

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        yaml.safe_dump(raw, handle)
        temp_path = handle.name

    neutral_world = load_world_map(temp_path)
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, neutral_world, commander, tick=1)

    assert event is None


def test_player_who_owns_no_war_base_never_triggers(world: WorldMap) -> None:
    # A third player (no war base on this map at all) standing on p1's pad.
    commander = _free_commander(PLAYER_NEUTRAL_OBSERVER, *P1_HELI_PAD_CELL, altitude=0)
    state = create_game_state(0, {PLAYER_NEUTRAL_OBSERVER}, commanders=(commander,))

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


# --- Misaligned X/Y: on the ground, but not on any heli-pad cell ------------------


def test_wrong_position_does_not_trigger(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_ONE, 0, 0, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


def test_adjacent_cell_to_heli_pad_does_not_trigger(world: WorldMap) -> None:
    # One cell off from the p1 heli-pad footprint.
    x, y = P1_HELI_PAD_CELL
    commander = _free_commander(PLAYER_ONE, x, y + 1, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


# --- Insufficient contact: wrong altitude -----------------------------------------


def test_airborne_over_own_heli_pad_does_not_trigger(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=2)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


def test_custom_rules_min_altitude_is_respected(world: WorldMap) -> None:
    # A distinctly configured rules object with a non-zero minimum altitude,
    # to prove the check reads rules.commander_min_altitude rather than a
    # hardcoded 0.
    custom_rules = EngineRules(commander_min_altitude=2, commander_max_altitude=48)

    # altitude 0 no longer counts as "grounded" under custom_rules.
    airborne_commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(airborne_commander)
    event = detect_heli_pad_landing(state, world, airborne_commander, tick=1, rules=custom_rules)
    assert event is None

    # altitude 2 does count as "grounded" under custom_rules.
    grounded_commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=2)
    state = _state_with_commander(grounded_commander)
    event = detect_heli_pad_landing(state, world, grounded_commander, tick=1, rules=custom_rules)
    assert event is not None


# --- Docked mode: not independently landing ---------------------------------------


def test_docked_commander_does_not_trigger(world: WorldMap) -> None:
    robot_id = EntityId("robot-1")
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=P1_HELI_PAD_CELL[0],
        y=P1_HELI_PAD_CELL[1],
        altitude=0,
        docked_robot_id=robot_id,
    )
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


# --- War base with no declared heli-pad is a valid, non-triggering map state ------


def test_war_base_without_heli_pad_never_triggers() -> None:
    raw = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    # Remove the p1 heli-pad interaction point entirely.
    raw["interaction_points"] = [
        point for point in raw["interaction_points"] if point["id"] != "warbase-p1-helipad"
    ]

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        yaml.safe_dump(raw, handle)
        temp_path = handle.name

    no_pad_world = load_world_map(temp_path)
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=0)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, no_pad_world, commander, tick=1)

    assert event is None


# --- Default rules sanity check ----------------------------------------------------


def test_default_rules_min_altitude_is_zero() -> None:
    # Documents the assumption the fixture-based tests above rely on.
    assert DEFAULT_RULES.commander_min_altitude == 0
