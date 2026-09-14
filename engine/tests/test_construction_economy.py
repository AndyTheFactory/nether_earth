"""Tests for the original Spectrum construction economy (issue #34, M4.3)."""

from __future__ import annotations

import pytest

from nether_earth.construction_economy import (
    ResourcePool,
    SpendRejectionReason,
    SpendResult,
    module_cost,
    refund_module,
    spend_module,
    starting_resource_pool,
)
from nether_earth.robot_build import ModuleIdentity, resource_category
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.structures import FactoryType

# --- EngineRules locked defaults --------------------------------------------


def test_default_rules_match_locked_spectrum_construction_economy() -> None:
    assert DEFAULT_RULES.starting_general_resources == 20
    assert DEFAULT_RULES.module_cost_bipod == 3
    assert DEFAULT_RULES.module_cost_tracks == 5
    assert DEFAULT_RULES.module_cost_anti_grav == 10
    assert DEFAULT_RULES.module_cost_cannon == 2
    assert DEFAULT_RULES.module_cost_missile == 4
    assert DEFAULT_RULES.module_cost_phaser == 4
    assert DEFAULT_RULES.module_cost_nuclear == 20
    assert DEFAULT_RULES.module_cost_electronics == 3


def test_engine_rules_rejects_negative_starting_general_resources() -> None:
    with pytest.raises(ValueError):
        EngineRules(starting_general_resources=-1)


@pytest.mark.parametrize(
    "field_name",
    [
        "module_cost_bipod",
        "module_cost_tracks",
        "module_cost_anti_grav",
        "module_cost_cannon",
        "module_cost_missile",
        "module_cost_phaser",
        "module_cost_nuclear",
        "module_cost_electronics",
    ],
)
def test_engine_rules_rejects_non_positive_module_cost(field_name: str) -> None:
    with pytest.raises(ValueError):
        EngineRules(**{field_name: 0})


def test_module_cost_reads_every_identity_from_rules() -> None:
    assert module_cost(ModuleIdentity.BIPOD, DEFAULT_RULES) == 3
    assert module_cost(ModuleIdentity.TRACKS, DEFAULT_RULES) == 5
    assert module_cost(ModuleIdentity.ANTI_GRAV, DEFAULT_RULES) == 10
    assert module_cost(ModuleIdentity.CANNON, DEFAULT_RULES) == 2
    assert module_cost(ModuleIdentity.MISSILE, DEFAULT_RULES) == 4
    assert module_cost(ModuleIdentity.PHASER, DEFAULT_RULES) == 4
    assert module_cost(ModuleIdentity.NUCLEAR, DEFAULT_RULES) == 20
    assert module_cost(ModuleIdentity.ELECTRONICS, DEFAULT_RULES) == 3


# --- ResourcePool ------------------------------------------------------------


def test_starting_resource_pool_seeds_general_and_zeroes_categories() -> None:
    pool = starting_resource_pool(DEFAULT_RULES)

    assert pool.general == 20
    for category in FactoryType:
        assert pool.amount(category) == 0


def test_resource_pool_fills_omitted_categories_with_zero() -> None:
    pool = ResourcePool(general=5, category={FactoryType.CANNON: 3})

    assert pool.amount(FactoryType.CANNON) == 3
    assert pool.amount(FactoryType.CHASSIS) == 0
    assert pool.amount(FactoryType.NUCLEAR) == 0


def test_resource_pool_rejects_negative_general() -> None:
    with pytest.raises(ValueError):
        ResourcePool(general=-1)


def test_resource_pool_rejects_negative_category_amount() -> None:
    with pytest.raises(ValueError):
        ResourcePool(general=0, category={FactoryType.CANNON: -1})


def test_resource_pool_is_frozen() -> None:
    pool = starting_resource_pool(DEFAULT_RULES)

    with pytest.raises(AttributeError):
        pool.general = 100  # type: ignore[misc]


# --- spend_module: exact-cost spend from type pool alone --------------------


def test_spend_exact_cost_from_type_pool_alone_leaves_general_untouched() -> None:
    pool = ResourcePool(general=20, category={FactoryType.CHASSIS: 3})

    result = spend_module(pool, ModuleIdentity.BIPOD, DEFAULT_RULES)

    assert result.accepted
    assert result.pool is not None
    assert result.pool.amount(FactoryType.CHASSIS) == 0
    assert result.pool.general == 20


def test_spend_with_surplus_in_type_pool_only_drains_the_cost() -> None:
    pool = ResourcePool(general=20, category={FactoryType.CANNON: 10})

    result = spend_module(pool, ModuleIdentity.CANNON, DEFAULT_RULES)

    assert result.accepted
    assert result.pool is not None
    assert result.pool.amount(FactoryType.CANNON) == 8
    assert result.pool.general == 20


def test_spend_never_mutates_the_input_pool() -> None:
    pool = ResourcePool(general=20, category={FactoryType.CHASSIS: 3})

    spend_module(pool, ModuleIdentity.BIPOD, DEFAULT_RULES)

    assert pool.general == 20
    assert pool.amount(FactoryType.CHASSIS) == 3


# --- spend_module: mixed type + general spend --------------------------------


def test_spend_shortfall_drawn_from_general_after_draining_type_pool() -> None:
    # anti-grav costs 10; chassis pool only has 4 -> shortfall of 6 from general.
    pool = ResourcePool(general=20, category={FactoryType.CHASSIS: 4})

    result = spend_module(pool, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)

    assert result.accepted
    assert result.pool is not None
    assert result.pool.amount(FactoryType.CHASSIS) == 0
    assert result.pool.general == 14


def test_spend_with_zero_type_pool_draws_full_cost_from_general() -> None:
    pool = ResourcePool(general=20)

    result = spend_module(pool, ModuleIdentity.NUCLEAR, DEFAULT_RULES)

    assert result.accepted
    assert result.pool is not None
    assert result.pool.amount(FactoryType.NUCLEAR) == 0
    assert result.pool.general == 0


# --- spend_module: insufficient combined resources ---------------------------


def test_spend_rejected_when_combined_resources_insufficient() -> None:
    pool = ResourcePool(general=5, category={FactoryType.NUCLEAR: 2})

    result = spend_module(pool, ModuleIdentity.NUCLEAR, DEFAULT_RULES)

    assert not result.accepted
    assert result.pool is None
    assert result.reason is SpendRejectionReason.INSUFFICIENT_RESOURCES


def test_spend_rejection_performs_no_partial_deduction() -> None:
    pool = ResourcePool(general=5, category={FactoryType.NUCLEAR: 2})

    spend_module(pool, ModuleIdentity.NUCLEAR, DEFAULT_RULES)

    # The original pool object passed in is untouched (pure function); a
    # fresh spend attempt against the same starting values reproduces the
    # identical rejection, proving nothing was silently consumed.
    result_again = spend_module(pool, ModuleIdentity.NUCLEAR, DEFAULT_RULES)
    assert not result_again.accepted
    assert pool.general == 5
    assert pool.amount(FactoryType.NUCLEAR) == 2


def test_spend_result_construction_enforces_accept_reject_invariant() -> None:
    with pytest.raises(ValueError):
        SpendResult(accepted=True, pool=None, reason=None)
    with pytest.raises(ValueError):
        SpendResult(accepted=False, pool=None, reason=None)
    with pytest.raises(ValueError):
        SpendResult(
            accepted=True,
            pool=starting_resource_pool(DEFAULT_RULES),
            reason=SpendRejectionReason.INSUFFICIENT_RESOURCES,
        )


# --- refund_module: deselection/refund correctness ---------------------------


def test_refund_restores_type_pool_when_pre_construction_amount_covers_it() -> None:
    # Spend bipod (cost 3) entirely from a chassis pool of 3.
    start_pool = ResourcePool(general=20, category={FactoryType.CHASSIS: 3})
    spent = spend_module(start_pool, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert spent.accepted and spent.pool is not None

    refunded = refund_module(
        spent.pool,
        ModuleIdentity.BIPOD,
        DEFAULT_RULES,
        pre_construction_category_amount=3,
    )

    assert refunded.amount(FactoryType.CHASSIS) == 3
    assert refunded.general == 20
    assert refunded == start_pool


def test_refund_reversible_buffer_scenario_restores_up_to_pre_construction_then_general() -> None:
    """Exact Spectrum reversible-buffer scenario from the acceptance criteria.

    Spend more of a type than the type pool had at construction start
    (forcing general-resource spending), then refund, and verify the type
    pool is restored only up to its pre-construction amount with the
    remainder going to general.
    """
    # Anti-grav costs 10. Chassis pool at construction start has only 4;
    # the remaining 6 is drawn from general.
    pre_construction_chassis = 4
    pool = ResourcePool(general=20, category={FactoryType.CHASSIS: pre_construction_chassis})

    spent = spend_module(pool, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
    assert spent.accepted and spent.pool is not None
    assert spent.pool.amount(FactoryType.CHASSIS) == 0
    assert spent.pool.general == 14

    refunded = refund_module(
        spent.pool,
        ModuleIdentity.ANTI_GRAV,
        DEFAULT_RULES,
        pre_construction_category_amount=pre_construction_chassis,
    )

    # Type pool restored only up to its pre-construction amount (4)...
    assert refunded.amount(FactoryType.CHASSIS) == 4
    # ...and the remainder (10 - 4 = 6) returns to general, restoring it
    # fully to its pre-spend value.
    assert refunded.general == 20
    assert refunded == pool


def test_refund_with_zero_pre_construction_amount_returns_everything_to_general() -> None:
    pool = ResourcePool(general=20)
    spent = spend_module(pool, ModuleIdentity.NUCLEAR, DEFAULT_RULES)
    assert spent.accepted and spent.pool is not None

    refunded = refund_module(
        spent.pool, ModuleIdentity.NUCLEAR, DEFAULT_RULES, pre_construction_category_amount=0
    )

    assert refunded.amount(FactoryType.NUCLEAR) == 0
    assert refunded.general == 20


def test_refund_never_mutates_the_input_pool() -> None:
    pool = ResourcePool(general=14, category={FactoryType.CHASSIS: 0})

    refund_module(pool, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES, pre_construction_category_amount=4)

    assert pool.general == 14
    assert pool.amount(FactoryType.CHASSIS) == 0


def test_refund_rejects_negative_pre_construction_amount() -> None:
    pool = starting_resource_pool(DEFAULT_RULES)

    with pytest.raises(ValueError):
        refund_module(pool, ModuleIdentity.BIPOD, DEFAULT_RULES, pre_construction_category_amount=-1)


# --- repeated spend/refund/spend cycle consistency ---------------------------


def test_repeated_spend_refund_spend_cycle_is_consistent() -> None:
    pre_construction_chassis = 4
    pool = ResourcePool(general=20, category={FactoryType.CHASSIS: pre_construction_chassis})

    for _ in range(5):
        spent = spend_module(pool, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
        assert spent.accepted and spent.pool is not None
        refunded = refund_module(
            spent.pool,
            ModuleIdentity.ANTI_GRAV,
            DEFAULT_RULES,
            pre_construction_category_amount=pre_construction_chassis,
        )
        assert refunded == pool
        # Cycle back to the same starting point for the next iteration.
        pool = refunded


def test_select_then_deselect_then_reselect_different_module_is_consistent() -> None:
    pool = starting_resource_pool(DEFAULT_RULES)

    bipod_spent = spend_module(pool, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert bipod_spent.accepted and bipod_spent.pool is not None

    bipod_refunded = refund_module(
        bipod_spent.pool, ModuleIdentity.BIPOD, DEFAULT_RULES, pre_construction_category_amount=0
    )
    assert bipod_refunded == pool

    cannon_spent = spend_module(bipod_refunded, ModuleIdentity.CANNON, DEFAULT_RULES)
    assert cannon_spent.accepted and cannon_spent.pool is not None
    # Cannon pool was 0 (unrelated to the earlier bipod/chassis activity), so
    # the whole cost (2) is drawn from general resources.
    assert cannon_spent.pool.amount(FactoryType.CANNON) == 0
    assert cannon_spent.pool.general == 20 - module_cost(ModuleIdentity.CANNON, DEFAULT_RULES)


# --- resource-category mapping reuse -----------------------------------------


def test_every_module_identity_has_a_stable_resource_category_and_cost() -> None:
    for identity in ModuleIdentity:
        category = resource_category(identity)
        assert isinstance(category, FactoryType)
        assert module_cost(identity, DEFAULT_RULES) > 0
