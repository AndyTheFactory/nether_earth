"""Tests for centralized engine game-rule configuration (issue #37)."""

from __future__ import annotations

import pytest

from nether_earth.rules import DEFAULT_RULES, EngineRules


def test_default_rules_match_locked_spectrum_values() -> None:
    assert DEFAULT_RULES.commander_min_altitude == 0
    assert DEFAULT_RULES.commander_max_altitude == 48
    assert DEFAULT_RULES.commander_vertical_update_ticks == 4
    assert DEFAULT_RULES.commander_ascent_step == 2
    assert DEFAULT_RULES.commander_descent_step == 1


def test_engine_rules_default_constructor_matches_default_rules_instance() -> None:
    assert EngineRules() == DEFAULT_RULES


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

