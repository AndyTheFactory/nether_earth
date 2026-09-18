"""Tests for centralized robot movement timing configuration (issue #61, M5.2)."""

from __future__ import annotations

import pytest

from nether_earth.movement_rules import (
    DEFAULT_MOVEMENT_RULES,
    GAME_CYCLE_TICKS,
    MovementRules,
)
from nether_earth.robot_build import ModuleIdentity
from nether_earth.terrain import TerrainType


def test_game_cycle_ticks_matches_locked_20hz_conversion() -> None:
    # One disassembly "game cycle" = 200ms (MIN_INTERRUPTS_PER_GAME_CYCLE
    # comment: "game maximum speed is 5 frames per second"). At the locked
    # 50ms/tick (20Hz) simulation rate that is exactly 4 ticks.
    assert GAME_CYCLE_TICKS == 4


def test_ordinary_terrain_ordering_bipod_slower_than_tracks_slower_than_anti_grav() -> None:
    rules = DEFAULT_MOVEMENT_RULES

    bipod = rules.ticks_per_cell(ModuleIdentity.BIPOD, TerrainType.NORMAL)
    tracks = rules.ticks_per_cell(ModuleIdentity.TRACKS, TerrainType.NORMAL)
    anti_grav = rules.ticks_per_cell(ModuleIdentity.ANTI_GRAV, TerrainType.NORMAL)

    # Higher ticks-per-cell means slower, so the locked ordering
    # bipod < tracks < anti-grav (speed) is bipod > tracks > anti-grav (ticks).
    assert bipod > tracks > anti_grav


def test_rough_terrain_ordering_bipod_slower_than_tracks_slower_than_anti_grav() -> None:
    rules = DEFAULT_MOVEMENT_RULES

    bipod = rules.ticks_per_cell(ModuleIdentity.BIPOD, TerrainType.ROUGH)
    tracks = rules.ticks_per_cell(ModuleIdentity.TRACKS, TerrainType.ROUGH)
    anti_grav = rules.ticks_per_cell(ModuleIdentity.ANTI_GRAV, TerrainType.ROUGH)

    assert bipod > tracks > anti_grav


def test_rough_terrain_penalizes_bipod_more_severely_than_tracks() -> None:
    """Locked qualitative ordering (`_specs/open-questions.md` §4): rough
    terrain slows bipod down more severely (proportionally) than tracks.

    See `movement_rules.py`'s module docstring "Rough-terrain severity
    ordering" section: raw disassembly numbers do not unambiguously support
    this ordering, so the shipped defaults are documented placeholders
    chosen to satisfy it rather than a verified exact Spectrum value. This
    test locks the *ordering* the milestone plan requires, not exact
    magnitudes.
    """
    rules = DEFAULT_MOVEMENT_RULES

    bipod_normal = rules.ticks_per_cell(ModuleIdentity.BIPOD, TerrainType.NORMAL)
    bipod_rough = rules.ticks_per_cell(ModuleIdentity.BIPOD, TerrainType.ROUGH)
    tracks_normal = rules.ticks_per_cell(ModuleIdentity.TRACKS, TerrainType.NORMAL)
    tracks_rough = rules.ticks_per_cell(ModuleIdentity.TRACKS, TerrainType.ROUGH)

    bipod_slowdown = bipod_rough / bipod_normal
    tracks_slowdown = tracks_rough / tracks_normal

    assert bipod_slowdown > tracks_slowdown > 1.0


def test_anti_grav_not_uniform_once_ditch_is_included() -> None:
    rules = DEFAULT_MOVEMENT_RULES

    normal = rules.ticks_per_cell(ModuleIdentity.ANTI_GRAV, TerrainType.NORMAL)
    rough = rules.ticks_per_cell(ModuleIdentity.ANTI_GRAV, TerrainType.ROUGH)
    ditch = rules.ticks_per_cell(ModuleIdentity.ANTI_GRAV, TerrainType.DITCH)

    # Evidence-backed: disassembly shows anti-grav identical on flat/rugged
    # but slower on the most extreme terrain tier.
    assert normal == rough
    assert ditch > rough


def test_bipod_and_tracks_cannot_enter_ditch() -> None:
    rules = DEFAULT_MOVEMENT_RULES

    with pytest.raises(ValueError):
        rules.ticks_per_cell(ModuleIdentity.BIPOD, TerrainType.DITCH)
    with pytest.raises(ValueError):
        rules.ticks_per_cell(ModuleIdentity.TRACKS, TerrainType.DITCH)


def test_movement_rules_is_frozen() -> None:
    rules = MovementRules()

    with pytest.raises(AttributeError):
        rules.bipod_normal_ticks_per_cell = 1  # type: ignore[misc]


def test_movement_rules_default_constructor_matches_default_instance() -> None:
    assert MovementRules() == DEFAULT_MOVEMENT_RULES


def test_movement_rules_rejects_non_positive_ticks() -> None:
    with pytest.raises(ValueError):
        MovementRules(bipod_normal_ticks_per_cell=0)


def test_movement_rules_accepts_custom_overrides() -> None:
    rules = MovementRules(
        bipod_normal_ticks_per_cell=100,
        tracks_normal_ticks_per_cell=50,
        anti_grav_normal_ticks_per_cell=25,
    )

    assert rules.ticks_per_cell(ModuleIdentity.BIPOD, TerrainType.NORMAL) == 100
    assert rules.ticks_per_cell(ModuleIdentity.TRACKS, TerrainType.NORMAL) == 50
    assert rules.ticks_per_cell(ModuleIdentity.ANTI_GRAV, TerrainType.NORMAL) == 25
