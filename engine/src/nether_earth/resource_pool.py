"""Authoritative per-player resource pool type.

`_specs/resolved-questions.md` "Resource spending rules" locks
the original Spectrum construction economy: every player has one *general*
resource pool plus one pool per resource category (the same six
:class:`~nether_earth.structures.FactoryType` categories a factory produces
into -- chassis, electronics, nuclear, missile, phaser, cannon). This
module defines the single authoritative, ``GameState``-attached value type
for that per-player pool: :class:`PlayerResourcePool`.

Design ruling: ``construction_economy.ResourcePool`` is ``Mapping[FactoryType, int]``-backed
and, as a result, not hashable -- inconsistent with every other
``GameState``-attached type in this codebase (``EngineRules``,
``GameState``, ``Commander``, ``RobotBuild`` are all frozen dataclasses with
explicit named fields, chosen specifically so they are hashable/equatable
by value). :class:`PlayerResourcePool` therefore does **not** adopt
``ResourcePool`` verbatim. It is its own frozen, slotted dataclass with one
named ``int`` field per category (``general``, ``chassis``, ``cannon``,
``missile``, ``phaser``, ``nuclear``, ``electronics``), mirroring
``EngineRules``'s ``module_cost_*``/``module_height_*`` "one field per
category" idiom exactly, plus the owning ``player_id`` field, mirroring
``Commander``'s "the entity carries its own owning player id" convention
used for ``GameState.commanders``.

Conversion to/from ``construction_economy.ResourcePool``:
:meth:`PlayerResourcePool.to_resource_pool` and
:meth:`PlayerResourcePool.from_resource_pool` perform a trivial, lossless
field-for-field conversion. This module performs the conversion itself so
that the construction session can call :func:`~nether_earth.construction_economy.spend_module`/
:func:`~nether_earth.construction_economy.refund_module` against a
``ResourcePool`` snapshot derived from a real ``PlayerResourcePool`` without
having to invent its own conversion first -- but the *authoritative*,
``GameState``-attached type remains :class:`PlayerResourcePool`, never
``ResourcePool`` itself; ``ResourcePool`` stays construction_economy.py's
own pure-function operand type, converted at the call boundary.

``GameState`` attachment follows ``state.py``'s ``commanders`` convention
exactly: ``GameState.resource_pools`` is an immutable tuple in canonical
order (sorted by ``PlayerResourcePool.player_id.value``), never a
``dict``/``Mapping``-shaped field, with the same "every id must be a
participant in ``state.players``, no duplicate pools per player" invariants
enforced by :meth:`GameState.with_resource_pools`/:func:`create_game_state`
(see `state.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nether_earth.construction_economy import ResourcePool
from nether_earth.ids import PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.structures import FactoryType

__all__ = [
    "PlayerResourcePool",
    "starting_player_resource_pool",
]

#: The six type-specific category field names on :class:`PlayerResourcePool`,
#: in the same declaration order as :class:`~nether_earth.structures.FactoryType`.
#: The one place this field-name <-> ``FactoryType`` mapping is defined.
_CATEGORY_FIELDS: dict[FactoryType, str] = {
    FactoryType.CHASSIS: "chassis",
    FactoryType.ELECTRONICS: "electronics",
    FactoryType.NUCLEAR: "nuclear",
    FactoryType.MISSILE: "missile",
    FactoryType.PHASER: "phaser",
    FactoryType.CANNON: "cannon",
}


@dataclass(frozen=True, slots=True)
class PlayerResourcePool:
    """The authoritative, ``GameState``-attached resource pool for one player.

    ``general`` is the shared general-resource pool; the remaining six
    fields hold one non-negative amount per
    :class:`~nether_earth.structures.FactoryType` category. All seven
    amounts must be non-negative (enforced in ``__post_init__``, matching
    ``EngineRules``'s validation style).

    Hashable and equatable by value, like ``Commander``/``RobotBuild`` and
    every other ``GameState``-attached type in this codebase -- see the
    module docstring for why this is a named-field dataclass rather than a
    ``Mapping``-backed one.
    """

    player_id: PlayerId
    general: int = 0
    chassis: int = 0
    electronics: int = 0
    nuclear: int = 0
    missile: int = 0
    phaser: int = 0
    cannon: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "general",
            "chassis",
            "electronics",
            "nuclear",
            "missile",
            "phaser",
            "cannon",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")

    def amount(self, category: FactoryType) -> int:
        """Return this pool's current amount for ``category``."""
        return int(getattr(self, _CATEGORY_FIELDS[category]))

    def with_amount(self, category: FactoryType, amount: int) -> PlayerResourcePool:
        """Return a copy with ``category``'s amount replaced by ``amount``.

        Pure: never mutates ``self``. ``general`` and every other category
        are carried over unchanged. Dispatches through explicit per-field
        ``replace`` calls (rather than a single ``**kwargs``-unpacked call)
        so this stays soundly typed under ``mypy --strict``.
        """
        if category is FactoryType.CHASSIS:
            return replace(self, chassis=amount)
        if category is FactoryType.ELECTRONICS:
            return replace(self, electronics=amount)
        if category is FactoryType.NUCLEAR:
            return replace(self, nuclear=amount)
        if category is FactoryType.MISSILE:
            return replace(self, missile=amount)
        if category is FactoryType.PHASER:
            return replace(self, phaser=amount)
        if category is FactoryType.CANNON:
            return replace(self, cannon=amount)
        raise AssertionError(f"unreachable: unknown FactoryType {category!r}")

    def with_general(self, general: int) -> PlayerResourcePool:
        """Return a copy with ``general`` replaced. Pure: never mutates ``self``."""
        return replace(self, general=general)

    def to_resource_pool(self) -> ResourcePool:
        """Convert to a `construction_economy.ResourcePool` snapshot.

        Trivial, lossless field-for-field conversion -- see the module
        docstring for why this boundary conversion exists here rather than
        ``PlayerResourcePool`` adopting ``ResourcePool``'s shape directly.
        """
        return ResourcePool(
            general=self.general,
            category={category: self.amount(category) for category in FactoryType},
        )

    @classmethod
    def from_resource_pool(cls, player_id: PlayerId, pool: ResourcePool) -> PlayerResourcePool:
        """Construct a :class:`PlayerResourcePool` from a ``ResourcePool`` snapshot.

        The inverse of :meth:`to_resource_pool`; ``player_id`` is supplied
        explicitly because ``ResourcePool`` itself carries no player
        identity (it is a bare value snapshot used inside a single spend/
        refund call, not a ``GameState``-attached entity).
        """
        return cls(
            player_id=player_id,
            general=pool.general,
            **{field_name: pool.amount(category) for category, field_name in _CATEGORY_FIELDS.items()},
        )


def starting_player_resource_pool(
    player_id: PlayerId, rules: EngineRules = DEFAULT_RULES
) -> PlayerResourcePool:
    """Return a fresh :class:`PlayerResourcePool` for ``player_id`` at match start.

    General resources are seeded from ``rules.starting_general_resources``
    (locked Spectrum default ``20``). Every type-specific category starts
    at ``0``: neither `_specs/functional-spec.md` nor
    `_specs/technical-spec.md` describes any
    starting type-specific resource grant, and
    ``construction_economy.starting_resource_pool`` establishes the same
    "type-specific categories start at zero, general is the only seeded
    pool" precedent already -- production (this module's
    ``resource_production.py`` sibling) is the only source of
    type-specific category resources beyond this starting grant.
    """
    return PlayerResourcePool(player_id=player_id, general=rules.starting_general_resources)
