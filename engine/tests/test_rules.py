"""Tests for centralized engine game-rule configuration (issue #37)."""

from __future__ import annotations

import pytest

from nether_earth.rules import DEFAULT_RULES, EngineRules, miles_to_cells


def test_default_rules_match_locked_spectrum_values() -> None:
    assert DEFAULT_RULES.commander_min_altitude == 0
    assert DEFAULT_RULES.commander_max_altitude == 48
    assert DEFAULT_RULES.commander_vertical_update_ticks == 4
    assert DEFAULT_RULES.commander_ascent_step == 2
    assert DEFAULT_RULES.commander_descent_step == 1


def test_engine_rules_default_constructor_matches_default_rules_instance() -> None:
    assert EngineRules() == DEFAULT_RULES


def test_default_rules_match_evidence_backed_ordinary_terrain_movement_ticks() -> None:
    """Issue #61 (M5.2): ordinary-terrain ticks/cell, disassembly-evidence-backed.

    Derived from `santiontanon/netherearth-disassembly`'s
    `netherearth-annotated.asm` `Lb61d_robot_movement_speed_table`
    (bipod/tracks/anti-grav = 6/4/3 "cycles" on flat terrain) and the
    disassembly's own documented cadence (``MIN_INTERRUPTS_PER_GAME_CYCLE:
    equ 10 ; game maximum speed is 5 frames per second``, i.e. one game
    cycle = 200ms = 4 ticks at the locked 20Hz simulation rate). See
    `rules.py`'s module docstring and `_specs/open-questions.md` §4 for the
    full evidence trail, including the rough/ditch multipliers this
    research pass could NOT resolve to exact disassembly values.
    """
    assert DEFAULT_RULES.robot_move_ticks_bipod == 24
    assert DEFAULT_RULES.robot_move_ticks_tracks == 16
    assert DEFAULT_RULES.robot_move_ticks_anti_grav == 12
    # Evidence-backed: the same disassembly table shows anti-grav identical
    # on flat/rugged terrain (3 cycles both).
    assert DEFAULT_RULES.robot_rough_multiplier_anti_grav == 1


def test_engine_rules_is_frozen() -> None:
    rules = EngineRules()

    with pytest.raises(AttributeError):
        rules.commander_max_altitude = 100  # type: ignore[misc]


def test_engine_rules_accepts_custom_overrides() -> None:
    rules = EngineRules(
        commander_min_altitude=1,
        commander_max_altitude=64,
        commander_vertical_update_ticks=8,
        commander_ascent_step=4,
        commander_descent_step=2,
    )

    assert rules.commander_min_altitude == 1
    assert rules.commander_max_altitude == 64
    assert rules.commander_vertical_update_ticks == 8
    assert rules.commander_ascent_step == 4
    assert rules.commander_descent_step == 2


def test_engine_rules_rejects_negative_min_altitude() -> None:
    with pytest.raises(ValueError):
        EngineRules(commander_min_altitude=-1)


def test_engine_rules_rejects_max_altitude_not_exceeding_min() -> None:
    with pytest.raises(ValueError):
        EngineRules(commander_min_altitude=10, commander_max_altitude=10)


def test_engine_rules_rejects_non_positive_vertical_update_ticks() -> None:
    with pytest.raises(ValueError):
        EngineRules(commander_vertical_update_ticks=0)
    with pytest.raises(ValueError):
        EngineRules(commander_vertical_update_ticks=-1)


def test_engine_rules_rejects_non_positive_ascent_step() -> None:
    with pytest.raises(ValueError):
        EngineRules(commander_ascent_step=0)


def test_engine_rules_rejects_non_positive_descent_step() -> None:
    with pytest.raises(ValueError):
        EngineRules(commander_descent_step=0)


def test_default_rules_match_documented_module_height_placeholder_defaults() -> None:
    assert DEFAULT_RULES.module_height_bipod == 4
    assert DEFAULT_RULES.module_height_tracks == 4
    assert DEFAULT_RULES.module_height_anti_grav == 4
    assert DEFAULT_RULES.module_height_cannon == 2
    assert DEFAULT_RULES.module_height_missile == 2
    assert DEFAULT_RULES.module_height_phaser == 2
    assert DEFAULT_RULES.module_height_nuclear == 2
    assert DEFAULT_RULES.module_height_electronics == 2


def test_engine_rules_accepts_custom_module_height_overrides() -> None:
    rules = EngineRules(
        module_height_bipod=10,
        module_height_tracks=11,
        module_height_anti_grav=12,
        module_height_cannon=13,
        module_height_missile=14,
        module_height_phaser=15,
        module_height_nuclear=16,
        module_height_electronics=17,
    )

    assert rules.module_height_bipod == 10
    assert rules.module_height_tracks == 11
    assert rules.module_height_anti_grav == 12
    assert rules.module_height_cannon == 13
    assert rules.module_height_missile == 14
    assert rules.module_height_phaser == 15
    assert rules.module_height_nuclear == 16
    assert rules.module_height_electronics == 17


@pytest.mark.parametrize(
    "field_name",
    [
        "module_height_bipod",
        "module_height_tracks",
        "module_height_anti_grav",
        "module_height_cannon",
        "module_height_missile",
        "module_height_phaser",
        "module_height_nuclear",
        "module_height_electronics",
    ],
)
def test_engine_rules_rejects_non_positive_module_height(field_name: str) -> None:
    with pytest.raises(ValueError):
        EngineRules(**{field_name: 0})
    with pytest.raises(ValueError):
        EngineRules(**{field_name: -1})


def test_default_rules_match_locked_combat_range_and_effect_defaults() -> None:
    """Issue #70 (M6.1): locked weapon ranges and nuclear radius.

    All values are Spectrum-compatible locked defaults from
    `_specs/milestones/06-combat-damage-victory.md` "Canonical default
    ranges/effects", converted from miles to cells via the shared
    mile-to-cell conversion (1 mile = 2 cells).
    """
    assert DEFAULT_RULES.cannon_range_cells == miles_to_cells(10)
    assert DEFAULT_RULES.missile_range_cells == miles_to_cells(14)
    assert DEFAULT_RULES.phaser_range_cells == miles_to_cells(10)
    assert DEFAULT_RULES.electronics_range_bonus_cells == miles_to_cells(3)
    assert DEFAULT_RULES.nuclear_radius_cells == miles_to_cells(8)
    assert DEFAULT_RULES.normal_projectile_altitude == 10
    assert DEFAULT_RULES.cannon_damage_multiplier == 2
    assert DEFAULT_RULES.missile_damage_multiplier == 3
    assert DEFAULT_RULES.phaser_damage_multiplier == 4
    assert DEFAULT_RULES.projectile_max_range_cells == 28  # longest range


def test_engine_rules_accepts_custom_combat_overrides() -> None:
    rules = EngineRules(
        cannon_range_cells=25,
        missile_range_cells=30,
        normal_projectile_altitude=15,
    )

    assert rules.cannon_range_cells == 25
    assert rules.missile_range_cells == 30
    assert rules.normal_projectile_altitude == 15
    # Others remain at defaults
    assert rules.phaser_range_cells == miles_to_cells(10)


@pytest.mark.parametrize(
    "field_name",
    [
        "cannon_range_cells",
        "missile_range_cells",
        "phaser_range_cells",
        "electronics_range_bonus_cells",
        "nuclear_radius_cells",
        "normal_projectile_altitude",
        "cannon_damage_multiplier",
        "missile_damage_multiplier",
        "phaser_damage_multiplier",
        "projectile_max_range_cells",
    ],
)
def test_engine_rules_rejects_non_positive_combat_fields(field_name: str) -> None:
    with pytest.raises(ValueError):
        EngineRules(**{field_name: 0})
    with pytest.raises(ValueError):
        EngineRules(**{field_name: -1})

