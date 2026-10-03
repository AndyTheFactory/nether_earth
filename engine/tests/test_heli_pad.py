"""Tests for war-base heli-pad landing detection (issue #41, M3.5).

Uses the shared M2 fixture map (`fixtures/world_map_basic.yaml`), which
declares two war bases (``warbase-p1`` owned by ``p1``, ``warbase-p2``
owned by ``p2``) each with a 2×2 ``HELI_PAD`` interaction point (CR002.4,
`_specs/open-questions.md` §21): ``warbase-p1-helipad`` anchored at
``(4, 1)`` (cells x 4..5, y 0..1) and ``warbase-p2-helipad`` anchored at
``(4, 3)`` (cells x 4..5, y 2..3), each over a 3-high war-base component
(the fixture's "roof"). A commander lands when its 2×2 body lies exactly
over the pad, i.e. its anchor is the pad's anchor. See `docs/mechanics/commander.md`
for the acceptance criteria this file
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
from nether_earth.heli_pad import (
    CommanderConstructionEntryEligible,
    detect_heli_pad_landing,
    heli_pad_surface_altitude,
)
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import create_game_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "world_map_basic.yaml"
ORIGINAL_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")
PLAYER_NEUTRAL_OBSERVER = PlayerId("p3")

# Fixture heli-pad anchors, from world_map_basic.yaml (2×2 pads, CR002.4).
P1_HELI_PAD_CELL = (4, 1)
P2_HELI_PAD_CELL = (4, 3)
# Both fixture pad cells sit on 3-high components: landing is at that height
# (open-questions.md §18: land at the pad cell's component height).
PAD_ROOF_ALTITUDE = 3


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
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=7)

    assert event is not None
    assert isinstance(event, CommanderConstructionEntryEligible)
    assert event.player == PLAYER_ONE
    assert event.war_base_id == EntityId("warbase-p1")
    assert event.tick == 7
    assert event.sequence == 0


def test_friendly_landing_is_symmetric_for_other_player(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_TWO, *P2_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is not None
    assert event.player == PLAYER_TWO
    assert event.war_base_id == EntityId("warbase-p2")


def test_sequencer_is_used_when_supplied(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
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
    commander = _free_commander(PLAYER_ONE, *P2_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
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
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, neutral_world, commander, tick=1)

    assert event is None


def test_player_who_owns_no_war_base_never_triggers(world: WorldMap) -> None:
    # A third player (no war base on this map at all) standing on p1's pad.
    commander = _free_commander(PLAYER_NEUTRAL_OBSERVER, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
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


@pytest.mark.parametrize(("dx", "dy"), [(1, 0), (-1, 0), (0, 1), (1, 1)])
def test_a_body_only_partly_over_the_pad_does_not_trigger(
    world: WorldMap, dx: int, dy: int
) -> None:
    """The whole 2×2 body must lie over the pad (the Spectrum's ship anchor
    must be the "H" decoration's own cell), even at the roof altitude."""
    x, y = P1_HELI_PAD_CELL
    commander = _free_commander(PLAYER_ONE, x + dx, y + dy, altitude=PAD_ROOF_ALTITUDE)

    assert detect_heli_pad_landing(_state_with_commander(commander), world, commander, tick=1) is None


# --- Insufficient contact: wrong altitude -----------------------------------------


def test_airborne_over_own_heli_pad_does_not_trigger(world: WorldMap) -> None:
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE + 1)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, world, commander, tick=1)

    assert event is None


def test_ground_altitude_on_a_roof_pad_cell_does_not_trigger(world: WorldMap) -> None:
    # The pad is on the roof: ground level (commander_min_altitude) is not
    # the pad cell's surface, so it is not a landing.
    commander = _free_commander(
        PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=DEFAULT_RULES.commander_min_altitude
    )
    state = _state_with_commander(commander)

    assert detect_heli_pad_landing(state, world, commander, tick=1) is None


def test_surface_altitude_is_the_pad_cell_component_height(world: WorldMap) -> None:
    assert heli_pad_surface_altitude(world, *P1_HELI_PAD_CELL) == PAD_ROOF_ALTITUDE
    assert heli_pad_surface_altitude(world, *P2_HELI_PAD_CELL) == PAD_ROOF_ALTITUDE


def _world_with_ground_level_p1_pad(cell: tuple[int, int]) -> WorldMap:
    """Move p1's 2×2 pad so it is anchored at ``cell``."""
    raw = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    x, y = cell
    for point in raw["interaction_points"]:
        if point["id"] == "warbase-p1-helipad":
            point["footprint"] = [
                {"x": x, "y": y},
                {"x": x + 1, "y": y},
                {"x": x, "y": y - 1},
                {"x": x + 1, "y": y - 1},
            ]

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        yaml.safe_dump(raw, handle)
        temp_path = handle.name

    return load_world_map(temp_path)


def test_pad_cell_without_a_component_lands_at_custom_min_altitude() -> None:
    # A pad on a free cell has a ground-level surface: rules.commander_min_altitude,
    # read from the rules object rather than a hardcoded 0.
    free_cell = (8, 6)
    ground_world = _world_with_ground_level_p1_pad(free_cell)
    custom_rules = EngineRules(commander_min_altitude=2, commander_max_altitude=48)
    assert heli_pad_surface_altitude(ground_world, *free_cell, custom_rules) == 2

    airborne_commander = _free_commander(PLAYER_ONE, *free_cell, altitude=3)
    state = _state_with_commander(airborne_commander)
    event = detect_heli_pad_landing(state, ground_world, airborne_commander, tick=1, rules=custom_rules)
    assert event is None

    grounded_commander = _free_commander(PLAYER_ONE, *free_cell, altitude=2)
    state = _state_with_commander(grounded_commander)
    event = detect_heli_pad_landing(state, ground_world, grounded_commander, tick=1, rules=custom_rules)
    assert event is not None


def test_original_map_roof_pad_lands_at_15_not_at_the_anchor_on_the_ground() -> None:
    # open-questions.md §18 / Spectrum `cp 15`: the pad is at (anchor.x,
    # anchor.y - 4) on the 15-high roof; the anchor is ground level.
    raw = yaml.safe_load(ORIGINAL_MAP_PATH.read_text(encoding="utf-8"))
    raw["war_bases"][0]["owner"] = "p1"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        yaml.safe_dump(raw, handle)
        temp_path = handle.name
    original = load_world_map(temp_path)
    anchor_x, anchor_y = 22, 9

    on_roof = _free_commander(PLAYER_ONE, anchor_x, anchor_y - 4, altitude=15)
    event = detect_heli_pad_landing(_state_with_commander(on_roof), original, on_roof, tick=1)
    assert event is not None and event.war_base_id == EntityId("warbase-1")

    for altitude in (0, 14, 16):
        near = _free_commander(PLAYER_ONE, anchor_x, anchor_y - 4, altitude=altitude)
        assert detect_heli_pad_landing(_state_with_commander(near), original, near, tick=1) is None

    # The 2×2 pad (CR002.4): a body shifted by one cell is not over the "H".
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        shifted = _free_commander(PLAYER_ONE, anchor_x + dx, anchor_y - 4 + dy, altitude=15)
        assert (
            detect_heli_pad_landing(_state_with_commander(shifted), original, shifted, tick=1)
            is None
        )

    at_anchor = _free_commander(PLAYER_ONE, anchor_x, anchor_y, altitude=0)
    assert detect_heli_pad_landing(_state_with_commander(at_anchor), original, at_anchor, tick=1) is None


# --- Docked mode: not independently landing ---------------------------------------


def test_docked_commander_does_not_trigger(world: WorldMap) -> None:
    robot_id = EntityId("robot-1")
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=P1_HELI_PAD_CELL[0],
        y=P1_HELI_PAD_CELL[1],
        altitude=PAD_ROOF_ALTITUDE,
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
    commander = _free_commander(PLAYER_ONE, *P1_HELI_PAD_CELL, altitude=PAD_ROOF_ALTITUDE)
    state = _state_with_commander(commander)

    event = detect_heli_pad_landing(state, no_pad_world, commander, tick=1)

    assert event is None


# --- Default rules sanity check ----------------------------------------------------


def test_default_rules_min_altitude_is_zero() -> None:
    # Documents the assumption the fixture-based tests above rely on.
    assert DEFAULT_RULES.commander_min_altitude == 0
