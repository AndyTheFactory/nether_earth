"""Original ZX Spectrum construction-resource spend/refund algorithm (issue #34, M4.3).

`_specs/open-questions.md` §10 "Resource spending rules -- RESOLVED" locks
the original Spectrum construction economy: every player has one *general*
resource pool plus one pool per resource category (the same six
:class:`~nether_earth.structures.FactoryType` categories a factory produces
into, see `robot_build.py`'s module docstring for why that enum is reused
rather than redeclared), and selecting a module for construction spends
against those pools with a fixed, deterministic algorithm:

1. Spend the selected module's cost from its type-specific resource pool
   first.
2. If that pool is insufficient, zero it and pay only the shortfall from
   general resources.
3. If general resources cannot cover the shortfall, reject the selection
   (no partial deduction).
4. Deselecting/removing a module restores its type-specific pool up to the
   amount available before entering construction, then returns any
   remainder to general resources.
5. Selecting/deselecting operates on a temporary buffer, not a player's
   actual resources.
6. Actual player resources are committed atomically only on successful
   launch.
7. Exiting/scrapping before launch permanently consumes nothing.

This module implements steps 1-4 as two pure functions -- :func:`spend_module`
and :func:`refund_module` -- operating on an explicit :class:`ResourcePool`
snapshot. Steps 5-7 (the construction-session temporary buffer that tracks
"pre-construction" category amounts across a sequence of selections/
deselections, and atomic commit-on-launch of a *player's* actual resources)
are Task 5 (M4.5, construction session state) and Task 6 (M4.6, launch)'s
scope, not this module's -- :func:`spend_module`/:func:`refund_module` are
the pure, composable primitives those later systems build session logic on
top of. In particular, :func:`refund_module` takes the "pre-construction"
category amount as an explicit parameter rather than tracking it itself,
because *remembering* that value across a sequence of selections is exactly
the session-buffer responsibility this task intentionally does not own.

Resource-pool type note for Task 4 (M4.4, `_specs/milestones/04-robots-
construction-economy.md`): Task 4 is separately responsible for the
authoritative, ``GameState``-attached, per-player resource-pool type. This
module needs *some* concrete resource-pool value to spend/refund against
today, before Task 4 lands, so it defines :class:`ResourcePool` here as a
minimal, immutable value type (one ``general`` int plus one int per
:class:`~nether_earth.structures.FactoryType` category). This is a
deliberate, flagged integration point, not a second permanently-parallel
resource-pool representation: Task 4 is expected to either adopt this exact
type as (or provide a trivial, lossless conversion to/from) its
``GameState``-attached per-player pool, so :func:`spend_module`/
:func:`refund_module` keep working unmodified against real player state. If
Task 4's implementer finds a reason ``ResourcePool`` cannot serve that role
unchanged, that is a design conflict to raise explicitly, not to route
around silently in a duplicate type.

No cost numbers are (re)defined here: they are read from
:class:`~nether_earth.rules.EngineRules`'s ``module_cost_*`` fields (the one
authoritative Spectrum-locked source of truth, see `rules.py`), looked up by
:func:`~nether_earth.robot_build.resource_category` against the module
identity catalog from `robot_build.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from nether_earth.robot_build import ModuleIdentity, resource_category
from nether_earth.rules import EngineRules
from nether_earth.structures import FactoryType

__all__ = [
    "ResourcePool",
    "SpendRejectionReason",
    "SpendResult",
    "module_cost",
    "refund_module",
    "spend_module",
    "starting_resource_pool",
]


#: Field name on :class:`~nether_earth.rules.EngineRules` holding each
#: module identity's construction cost. The one place this identity ->
#: field-name mapping is defined; :func:`module_cost` is the only reader.
_MODULE_COST_FIELD: Mapping[ModuleIdentity, str] = MappingProxyType(
    {
        ModuleIdentity.BIPOD: "module_cost_bipod",
        ModuleIdentity.TRACKS: "module_cost_tracks",
        ModuleIdentity.ANTI_GRAV: "module_cost_anti_grav",
        ModuleIdentity.CANNON: "module_cost_cannon",
        ModuleIdentity.MISSILE: "module_cost_missile",
        ModuleIdentity.PHASER: "module_cost_phaser",
        ModuleIdentity.NUCLEAR: "module_cost_nuclear",
        ModuleIdentity.ELECTRONICS: "module_cost_electronics",
    }
)


def module_cost(identity: ModuleIdentity, rules: EngineRules) -> int:
    """Return ``identity``'s construction resource cost from ``rules``.

    Thin, named lookup over ``rules``'s ``module_cost_*`` fields so callers
    do not need to know the per-identity field-name convention.
    """
    return int(getattr(rules, _MODULE_COST_FIELD[identity]))


@dataclass(frozen=True, slots=True)
class ResourcePool:
    """An immutable snapshot of one player's construction resources.

    ``general`` is the shared general-resource pool; ``category`` holds one
    non-negative amount per :class:`~nether_earth.structures.FactoryType`
    resource category (chassis, electronics, nuclear, missile, phaser,
    cannon). See the module docstring for why this type exists and how
    Task 4 (M4.4) is expected to relate to it.

    Construct via :func:`starting_resource_pool` for a fresh player pool, or
    directly (e.g. in tests) with an explicit ``category`` mapping; a
    partial mapping is filled in with ``0`` for any omitted category so
    every :class:`ResourcePool` always has an entry for all six categories.
    """

    general: int
    category: Mapping[FactoryType, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.general < 0:
            raise ValueError("general resources must be non-negative")
        filled = {ft: self.category.get(ft, 0) for ft in FactoryType}
        for ft, amount in filled.items():
            if amount < 0:
                raise ValueError(f"resource amount for {ft!r} must be non-negative")
        object.__setattr__(self, "category", MappingProxyType(filled))

    def amount(self, category: FactoryType) -> int:
        """Return the pool's current amount for ``category``."""
        return self.category[category]

    def _with(self, *, general: int, category: FactoryType, amount: int) -> ResourcePool:
        """Return a copy with ``general`` and one ``category`` entry replaced.

        Private helper shared by :func:`spend_module`/:func:`refund_module`;
        never mutates ``self``.
        """
        updated = dict(self.category)
        updated[category] = amount
        return ResourcePool(general=general, category=updated)


def starting_resource_pool(rules: EngineRules) -> ResourcePool:
    """Return a fresh player :class:`ResourcePool` at match start.

    General resources are seeded from ``rules.starting_general_resources``;
    every type-specific category starts at ``0`` (the original Spectrum
    economy grants no starting type-specific resources -- production, Task
    4's scope, is the only source of category resources beyond this).
    """
    return ResourcePool(general=rules.starting_general_resources)


class SpendRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected :func:`spend_module` call."""

    INSUFFICIENT_RESOURCES = "insufficient_resources"


@dataclass(frozen=True, slots=True)
class SpendResult:
    """Outcome of :func:`spend_module`.

    Mirrors `commands.py`'s ``CommandResult`` accept/reject shape: exactly
    one of "accepted, carrying the resulting pool" or "a stable rejection
    reason" holds, enforced on construction, so callers can branch on
    ``accepted`` without exception-driven control flow for the ordinary
    "can't afford it" case.
    """

    accepted: bool
    pool: ResourcePool | None = None
    reason: SpendRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and (self.pool is None or self.reason is not None):
            raise ValueError("an accepted SpendResult must carry a pool and no rejection reason")
        if not self.accepted and (self.pool is not None or self.reason is None):
            raise ValueError("a rejected SpendResult must carry a reason and no pool")

    @classmethod
    def accept(cls, pool: ResourcePool) -> SpendResult:
        """Build an accepted result carrying the resulting ``pool``."""
        return cls(accepted=True, pool=pool, reason=None)

    @classmethod
    def reject(cls, reason: SpendRejectionReason) -> SpendResult:
        """Build a rejected result with a stable ``reason``."""
        return cls(accepted=False, pool=None, reason=reason)


def spend_module(pool: ResourcePool, identity: ModuleIdentity, rules: EngineRules) -> SpendResult:
    """Spend ``identity``'s construction cost from ``pool``.

    Pure: never mutates ``pool``; returns a new :class:`ResourcePool` inside
    an accepted :class:`SpendResult`, or a rejected one, leaving ``pool``
    itself untouched either way.

    Implements steps 1-3 of the locked Spectrum spending rule (see the
    module docstring): the module's type-specific category pool is drained
    first; only the shortfall (if any) is drawn from general resources; if
    general resources cannot cover the shortfall, the whole spend is
    rejected with :data:`SpendRejectionReason.INSUFFICIENT_RESOURCES` and no
    partial deduction occurs.
    """
    cost = module_cost(identity, rules)
    category = resource_category(identity)
    category_amount = pool.amount(category)

    if category_amount >= cost:
        return SpendResult.accept(
            pool._with(general=pool.general, category=category, amount=category_amount - cost)
        )

    shortfall = cost - category_amount
    if pool.general < shortfall:
        return SpendResult.reject(SpendRejectionReason.INSUFFICIENT_RESOURCES)

    return SpendResult.accept(
        pool._with(general=pool.general - shortfall, category=category, amount=0)
    )


def refund_module(
    pool: ResourcePool,
    identity: ModuleIdentity,
    rules: EngineRules,
    pre_construction_category_amount: int,
) -> ResourcePool:
    """Refund ``identity``'s construction cost into ``pool``.

    Pure: never mutates ``pool``; returns a new :class:`ResourcePool`.

    Implements step 4 of the locked Spectrum spending rule (see the module
    docstring): the module's type-specific category pool is restored by the
    module's cost, but capped so it never exceeds
    ``pre_construction_category_amount`` -- the amount that category held
    *before* construction began (before any spending in the current
    construction session) -- and any remainder beyond that cap is returned
    to general resources instead. This is the exact reversal of
    :func:`spend_module`'s "type pool first, general resources for the
    shortfall" order: refunding replenishes the type pool up to its
    pre-construction level first, then general resources absorb whatever
    does not fit.

    ``pre_construction_category_amount`` is supplied by the caller (Task 5's
    construction-session state) rather than tracked here, since remembering
    it across a sequence of selections/deselections is exactly the session
    responsibility this module does not own -- see the module docstring.
    """
    if pre_construction_category_amount < 0:
        raise ValueError("pre_construction_category_amount must be non-negative")

    cost = module_cost(identity, rules)
    category = resource_category(identity)
    category_amount = pool.amount(category)

    room = max(0, pre_construction_category_amount - category_amount)
    restore_to_category = min(cost, room)
    remainder = cost - restore_to_category

    return pool._with(
        general=pool.general + remainder,
        category=category,
        amount=category_amount + restore_to_category,
    )
