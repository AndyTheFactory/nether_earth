"""Tests for canonical robot component stack and height derivation (issue #53)."""

from __future__ import annotations

import itertools

import pytest

from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_height, derive_stack, derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules

# --- Stack order -----------------------------------------------------------


def test_minimal_build_stack_is_chassis_then_single_weapon() -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))

    assert derive_stack(build) == (ModuleIdentity.BIPOD, ModuleIdentity.CANNON)


def test_maximal_build_stack_is_chassis_all_weapons_then_electronics() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.ANTI_GRAV,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        electronics=ModuleIdentity.ELECTRONICS,
    )

    assert derive_stack(build) == (
        ModuleIdentity.ANTI_GRAV,
        ModuleIdentity.CANNON,
        ModuleIdentity.MISSILE,
        ModuleIdentity.PHASER,
        ModuleIdentity.ELECTRONICS,
    )


def test_missing_intermediate_weapon_is_omitted_preserving_relative_order() -> None:
    # cannon + phaser only (missile omitted) -> chassis, cannon, phaser
    build = RobotBuild(
        chassis=ModuleIdentity.TRACKS,
        weapons=(ModuleIdentity.PHASER, ModuleIdentity.CANNON),
    )

    assert derive_stack(build) == (
        ModuleIdentity.TRACKS,
        ModuleIdentity.CANNON,
        ModuleIdentity.PHASER,
    )


def test_nuke_is_always_topmost_weapon_regardless_of_other_weapons_present() -> None:
    build_nuke_with_cannon_and_missile = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.NUCLEAR, ModuleIdentity.CANNON, ModuleIdentity.MISSILE),
    )

    stack = derive_stack(build_nuke_with_cannon_and_missile)

    assert stack == (
        ModuleIdentity.BIPOD,
        ModuleIdentity.CANNON,
        ModuleIdentity.MISSILE,
        ModuleIdentity.NUCLEAR,
    )
    # explicit proof: nuke's stack position is after every other weapon present
    weapon_positions = [i for i, m in enumerate(stack) if m in (
        ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER, ModuleIdentity.NUCLEAR
    )]
    nuke_index = stack.index(ModuleIdentity.NUCLEAR)
    assert nuke_index == max(weapon_positions)


def test_nuke_as_only_weapon_sits_directly_above_chassis() -> None:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.NUCLEAR,))

    assert derive_stack(build) == (ModuleIdentity.TRACKS, ModuleIdentity.NUCLEAR)


def test_electronics_is_always_topmost_component_above_every_weapon() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.NUCLEAR, ModuleIdentity.CANNON),
        electronics=ModuleIdentity.ELECTRONICS,
    )

    stack = derive_stack(build)

    assert stack[-1] is ModuleIdentity.ELECTRONICS
    # electronics is above nuke specifically, not just "somewhere after"
    assert stack.index(ModuleIdentity.ELECTRONICS) > stack.index(ModuleIdentity.NUCLEAR)


def test_no_electronics_means_topmost_component_is_the_topmost_weapon() -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))

    assert derive_stack(build)[-1] is ModuleIdentity.CANNON


def test_stack_is_independent_of_weapon_selection_insertion_order() -> None:
    cannon_then_phaser = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.PHASER),
    )
    phaser_then_cannon = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.PHASER, ModuleIdentity.CANNON),
    )

    assert derive_stack(cannon_then_phaser) == derive_stack(phaser_then_cannon)
    assert derive_height(cannon_then_phaser) == derive_height(phaser_then_cannon)


def test_stack_is_independent_of_from_modules_input_order() -> None:
    modules_a = [
        ModuleIdentity.ELECTRONICS,
        ModuleIdentity.NUCLEAR,
        ModuleIdentity.CANNON,
        ModuleIdentity.BIPOD,
    ]
    modules_b = [
        ModuleIdentity.BIPOD,
        ModuleIdentity.CANNON,
        ModuleIdentity.NUCLEAR,
        ModuleIdentity.ELECTRONICS,
    ]

    build_a = RobotBuild.from_modules(modules_a)
    build_b = RobotBuild.from_modules(modules_b)

    assert derive_stack(build_a) == derive_stack(build_b)
    assert derive_height(build_a) == derive_height(build_b)


# --- Height ------------------------------------------------------------


def test_height_is_sum_of_default_rules_module_heights_for_minimal_build() -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))

    expected = DEFAULT_RULES.module_height_bipod + DEFAULT_RULES.module_height_cannon
    assert derive_height(build) == expected


def test_height_is_sum_of_default_rules_module_heights_for_maximal_build() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.ANTI_GRAV,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        electronics=ModuleIdentity.ELECTRONICS,
    )

    expected = (
        DEFAULT_RULES.module_height_anti_grav
        + DEFAULT_RULES.module_height_cannon
        + DEFAULT_RULES.module_height_missile
        + DEFAULT_RULES.module_height_phaser
        + DEFAULT_RULES.module_height_electronics
    )
    assert derive_height(build) == expected


def _every_legal_build() -> list[RobotBuild]:
    weapons = (
        ModuleIdentity.CANNON,
        ModuleIdentity.MISSILE,
        ModuleIdentity.PHASER,
        ModuleIdentity.NUCLEAR,
    )
    return [
        RobotBuild(chassis=chassis, weapons=combo, electronics=electronics)
        for chassis in (ModuleIdentity.BIPOD, ModuleIdentity.TRACKS, ModuleIdentity.ANTI_GRAV)
        for count in (1, 2, 3)
        for combo in itertools.combinations(weapons, count)
        for electronics in (None, ModuleIdentity.ELECTRONICS)
    ]


def test_spectrum_piece_heights_bound_robot_height_13_to_38() -> None:
    """CR003.3 (#218): the disassembly header's shortest/tallest robots.

    Shortest: tracks + cannon = 7 + 6 = 13. Tallest: bipod + missile +
    phaser + nuclear + electronics = 11 + 6 + 7 + 7 + 7 = 38.
    """
    heights = {build: derive_height(build) for build in _every_legal_build()}

    assert min(heights.values()) == 13
    assert max(heights.values()) == 38
    assert derive_height(RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))) == 13
    tallest = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.MISSILE, ModuleIdentity.PHASER, ModuleIdentity.NUCLEAR),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    assert derive_height(tallest) == 38


def test_tallest_robot_on_a_mountain_stays_within_the_commander_ceiling() -> None:
    # Mountains are the highest walkable ground (6, ``Ld7bc_map_piece_heights``);
    # the ship must still be able to rest on the tallest robot there.
    mountain = 6
    tallest = max(derive_height(build) for build in _every_legal_build())
    assert tallest + mountain == 44 <= DEFAULT_RULES.commander_max_altitude


def test_height_respects_custom_rules_override() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.TRACKS,
        weapons=(ModuleIdentity.NUCLEAR,),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    rules = EngineRules(
        module_height_tracks=10,
        module_height_nuclear=7,
        module_height_electronics=3,
    )

    assert derive_height(build, rules) == 20
    assert derive_height(build) != 20  # default rules differ


def test_height_uses_same_component_set_as_stack_no_drift() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    rules = EngineRules(
        module_height_bipod=1,
        module_height_cannon=2,
        module_height_missile=3,
        module_height_electronics=4,
    )

    stack = derive_stack(build)
    heights_by_module = {
        ModuleIdentity.BIPOD: rules.module_height_bipod,
        ModuleIdentity.CANNON: rules.module_height_cannon,
        ModuleIdentity.MISSILE: rules.module_height_missile,
        ModuleIdentity.ELECTRONICS: rules.module_height_electronics,
    }
    expected_height = sum(heights_by_module[m] for m in stack)

    assert derive_height(build, rules) == expected_height


# --- Combined helper -----------------------------------------------------


def test_derive_stack_and_height_matches_individual_functions() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.ANTI_GRAV,
        weapons=(ModuleIdentity.MISSILE, ModuleIdentity.NUCLEAR),
        electronics=ModuleIdentity.ELECTRONICS,
    )

    stack, height = derive_stack_and_height(build)

    assert stack == derive_stack(build)
    assert height == derive_height(build)


def test_derive_stack_and_height_respects_custom_rules() -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    rules = EngineRules(module_height_bipod=100, module_height_cannon=50)

    stack, height = derive_stack_and_height(build, rules)

    assert stack == (ModuleIdentity.BIPOD, ModuleIdentity.CANNON)
    assert height == 150


# --- Determinism across every valid weapon combination --------------------


@pytest.mark.parametrize(
    "weapons",
    [
        (ModuleIdentity.CANNON,),
        (ModuleIdentity.MISSILE,),
        (ModuleIdentity.PHASER,),
        (ModuleIdentity.NUCLEAR,),
        (ModuleIdentity.CANNON, ModuleIdentity.MISSILE),
        (ModuleIdentity.CANNON, ModuleIdentity.PHASER),
        (ModuleIdentity.CANNON, ModuleIdentity.NUCLEAR),
        (ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        (ModuleIdentity.MISSILE, ModuleIdentity.NUCLEAR),
        (ModuleIdentity.PHASER, ModuleIdentity.NUCLEAR),
        (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.NUCLEAR),
        (ModuleIdentity.CANNON, ModuleIdentity.PHASER, ModuleIdentity.NUCLEAR),
        (ModuleIdentity.MISSILE, ModuleIdentity.PHASER, ModuleIdentity.NUCLEAR),
    ],
)
def test_every_legal_weapon_combination_produces_deterministic_repeated_derivation(
    weapons: tuple[ModuleIdentity, ...],
) -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons)

    first_stack, first_height = derive_stack_and_height(build)
    second_stack, second_height = derive_stack_and_height(build)

    assert first_stack == second_stack
    assert first_height == second_height
    # stack weapons appear in canonical order regardless of input order used above
    stack_weapons = tuple(m for m in first_stack if m in weapons)
    assert stack_weapons == tuple(
        m
        for m in (
            ModuleIdentity.CANNON,
            ModuleIdentity.MISSILE,
            ModuleIdentity.PHASER,
            ModuleIdentity.NUCLEAR,
        )
        if m in weapons
    )
