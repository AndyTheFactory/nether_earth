"""Tests for the canonical robot module catalog and build model (issue #52)."""

from __future__ import annotations

import pytest

from nether_earth.robot_build import (
    CANONICAL_WEAPON_ORDER,
    CHASSIS_MODULES,
    ELECTRONICS_MODULES,
    MODULE_RESOURCE_CATEGORY,
    WEAPON_MODULES,
    BuildValidationError,
    ModuleIdentity,
    RobotBuild,
    resource_category,
)
from nether_earth.structures import FactoryType

# --- Catalog shape -----------------------------------------------------


def test_catalog_defines_exactly_eight_module_identities() -> None:
    assert {m.value for m in ModuleIdentity} == {
        "bipod",
        "tracks",
        "anti_grav",
        "cannon",
        "missile",
        "phaser",
        "nuclear",
        "electronics",
    }


def test_chassis_weapon_electronics_module_classes_partition_the_catalog() -> None:
    assert CHASSIS_MODULES == {
        ModuleIdentity.BIPOD,
        ModuleIdentity.TRACKS,
        ModuleIdentity.ANTI_GRAV,
    }
    assert WEAPON_MODULES == {
        ModuleIdentity.CANNON,
        ModuleIdentity.MISSILE,
        ModuleIdentity.PHASER,
        ModuleIdentity.NUCLEAR,
    }
    assert ELECTRONICS_MODULES == {ModuleIdentity.ELECTRONICS}

    # every identity belongs to exactly one module class
    union = CHASSIS_MODULES | WEAPON_MODULES | ELECTRONICS_MODULES
    assert union == set(ModuleIdentity)
    assert len(CHASSIS_MODULES) + len(WEAPON_MODULES) + len(ELECTRONICS_MODULES) == len(
        ModuleIdentity
    )


def test_canonical_weapon_order_matches_locked_stack_order() -> None:
    assert CANONICAL_WEAPON_ORDER == (
        ModuleIdentity.CANNON,
        ModuleIdentity.MISSILE,
        ModuleIdentity.PHASER,
        ModuleIdentity.NUCLEAR,
    )


def test_module_resource_category_mapping_is_locked_and_complete() -> None:
    assert MODULE_RESOURCE_CATEGORY == {
        ModuleIdentity.BIPOD: FactoryType.CHASSIS,
        ModuleIdentity.TRACKS: FactoryType.CHASSIS,
        ModuleIdentity.ANTI_GRAV: FactoryType.CHASSIS,
        ModuleIdentity.CANNON: FactoryType.CANNON,
        ModuleIdentity.MISSILE: FactoryType.MISSILE,
        ModuleIdentity.PHASER: FactoryType.PHASER,
        ModuleIdentity.NUCLEAR: FactoryType.NUCLEAR,
        ModuleIdentity.ELECTRONICS: FactoryType.ELECTRONICS,
    }
    # every identity is covered, no bare literal category string leaks
    assert set(MODULE_RESOURCE_CATEGORY) == set(ModuleIdentity)
    for identity in ModuleIdentity:
        assert resource_category(identity) is MODULE_RESOURCE_CATEGORY[identity]


# --- Valid builds --------------------------------------------------------


def test_valid_minimal_build_one_chassis_one_weapon() -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))

    assert build.chassis is ModuleIdentity.BIPOD
    assert build.weapons == (ModuleIdentity.CANNON,)
    assert build.electronics is None


def test_valid_maximal_build_one_chassis_three_weapons_electronics() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.ANTI_GRAV,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        electronics=ModuleIdentity.ELECTRONICS,
    )

    assert build.chassis is ModuleIdentity.ANTI_GRAV
    assert build.weapons == (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER)
    assert build.electronics is ModuleIdentity.ELECTRONICS


def test_nuke_may_be_the_only_weapon() -> None:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.NUCLEAR,))

    assert build.weapons == (ModuleIdentity.NUCLEAR,)


@pytest.mark.parametrize("chassis", sorted(CHASSIS_MODULES, key=lambda m: m.value))
def test_every_chassis_identity_is_individually_valid(chassis: ModuleIdentity) -> None:
    build = RobotBuild(chassis=chassis, weapons=(ModuleIdentity.CANNON,))
    assert build.chassis is chassis


def test_weapons_are_normalized_to_canonical_stack_order_regardless_of_input_order() -> None:
    build = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.PHASER, ModuleIdentity.CANNON, ModuleIdentity.MISSILE),
    )

    assert build.weapons == (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER)


# --- Invalid builds: chassis ---------------------------------------------


def test_zero_chassis_via_from_modules_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild.from_modules([ModuleIdentity.CANNON])


def test_two_chassis_via_from_modules_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild.from_modules(
            [ModuleIdentity.BIPOD, ModuleIdentity.TRACKS, ModuleIdentity.CANNON]
        )


def test_non_chassis_identity_in_chassis_field_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild(chassis=ModuleIdentity.CANNON, weapons=(ModuleIdentity.MISSILE,))


# --- Invalid builds: weapons -----------------------------------------------


def test_zero_weapons_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=())


def test_four_or_more_weapons_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild(
            chassis=ModuleIdentity.BIPOD,
            weapons=(
                ModuleIdentity.CANNON,
                ModuleIdentity.MISSILE,
                ModuleIdentity.PHASER,
                ModuleIdentity.NUCLEAR,
            ),
        )


def test_duplicate_weapon_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild(
            chassis=ModuleIdentity.BIPOD,
            weapons=(ModuleIdentity.CANNON, ModuleIdentity.CANNON),
        )


def test_non_weapon_identity_among_weapons_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.ELECTRONICS,))


# --- Invalid builds: electronics -------------------------------------------


def test_non_electronics_identity_in_electronics_field_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild(
            chassis=ModuleIdentity.BIPOD,
            weapons=(ModuleIdentity.CANNON,),
            electronics=ModuleIdentity.MISSILE,
        )


def test_two_or_more_electronics_via_from_modules_is_rejected() -> None:
    with pytest.raises(BuildValidationError):
        RobotBuild.from_modules(
            [
                ModuleIdentity.BIPOD,
                ModuleIdentity.CANNON,
                ModuleIdentity.ELECTRONICS,
                ModuleIdentity.ELECTRONICS,
            ]
        )


# --- Invalid builds: cross-field duplicate module identity -----------------


def test_duplicate_module_identity_across_chassis_and_electronics_is_rejected() -> None:
    # A module cannot simultaneously classify as chassis and electronics, so
    # this is only reachable by directly constructing an inconsistent build;
    # the shared cross-field duplicate check still deterministically rejects
    # it rather than silently accepting a malformed dataclass.
    with pytest.raises(BuildValidationError):
        RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.BIPOD,))


# --- from_modules classification -------------------------------------------


def test_from_modules_classifies_regardless_of_input_order() -> None:
    build = RobotBuild.from_modules(
        [
            ModuleIdentity.PHASER,
            ModuleIdentity.ELECTRONICS,
            ModuleIdentity.CANNON,
            ModuleIdentity.ANTI_GRAV,
            ModuleIdentity.MISSILE,
        ]
    )

    assert build.chassis is ModuleIdentity.ANTI_GRAV
    assert build.weapons == (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER)
    assert build.electronics is ModuleIdentity.ELECTRONICS


def test_from_modules_accepts_tuple_input_too() -> None:
    build = RobotBuild.from_modules((ModuleIdentity.TRACKS, ModuleIdentity.NUCLEAR))
    assert build.chassis is ModuleIdentity.TRACKS
    assert build.weapons == (ModuleIdentity.NUCLEAR,)


# --- Equality / hashing / replay-safety -------------------------------------


def test_build_equality_is_by_value_and_order_independent_for_weapons() -> None:
    a = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE),
    )
    b = RobotBuild(
        chassis=ModuleIdentity.BIPOD,
        weapons=(ModuleIdentity.MISSILE, ModuleIdentity.CANNON),
    )

    assert a == b
    assert hash(a) == hash(b)


def test_build_inequality_for_different_loadouts() -> None:
    a = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    b = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))

    assert a != b


def test_build_is_frozen() -> None:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))

    with pytest.raises(AttributeError):
        build.chassis = ModuleIdentity.TRACKS  # type: ignore[misc]


def test_build_is_hashable_and_usable_in_a_set() -> None:
    a = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    b = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    c = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))

    assert {a, b, c} == {a, c}
