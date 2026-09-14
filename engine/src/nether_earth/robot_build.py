"""Canonical robot module catalog and build identity model.

Issue #52 (M4.1, `_specs/milestones/04-robots-construction-economy.md`)
defines the single authoritative identity model for robot modules and
concrete robot builds. Every later Milestone 4 system (stack/height
derivation, construction economy costs, resource production, construction
session state, robot launch, engine integration) and Milestone 6 combat
import :class:`ModuleIdentity` and :class:`RobotBuild` from here rather than
re-declaring module names or the resource-category mapping; this module is
the one canonical catalog.

Module identities (`_specs/functional-spec.md` §11-§13):

- three chassis identities: ``BIPOD``, ``TRACKS``, ``ANTI_GRAV``;
- four weapon identities: ``CANNON``, ``MISSILE``, ``PHASER``, ``NUCLEAR``;
- one electronics identity: ``ELECTRONICS``.

Resource-category mapping (`_specs/functional-spec.md` §9 "six factory
production categories", §10.1 "resource pools"): every module identity maps
to exactly one of the six category labels already defined as
:class:`~nether_earth.structures.FactoryType` (chassis/cannon/missile/
phaser/nuclear/electronics) -- those are the same six categories a factory
produces into and a resource pool tracks, so this module reuses
``FactoryType`` as the category label rather than declaring a second,
parallel category enum. This is a *categorical* mapping only: no cost
numbers are defined here. `_specs/functional-spec.md` §10.2's Spectrum cost
table (Bipod 3, Tracks 5, Anti-grav 10, Cannon 2, Missile 4, Phaser 4,
Nuclear 20, Electronics 3) is explicitly out of scope for this task -- it is
Task 3 / M4.3's `EngineRules` scope, attached by module identity against the
catalog defined here without redefining identities.

Build validity (`_specs/functional-spec.md` §11 "Robot construction",
locked): a build has exactly one chassis, one to three weapons, no
duplicate module identity anywhere in the build, and zero or one
electronics module. The nuke may be the only weapon (a single-weapon build
of just ``NUCLEAR`` is legal). :class:`RobotBuild` is an immutable, frozen,
slotted dataclass enforcing this in ``__post_init__``, and its weapon tuple
is always normalized into the canonical bottom-to-top stack order from
`_specs/functional-spec.md` §12 (cannon, missile, phaser, nuke) regardless
of the order callers supply weapons in -- so two builds describing the same
logical loadout are always equal, hashable identically, and independent of
caller-supplied ordering, matching the determinism/replay-safety convention
used by ``GameState``/``Commander`` elsewhere in this package.

Out of scope for this task (left to later Milestone 4 tasks, see the module
docstring above and each task's own module): stack/height derivation,
construction cost values, spending/refund logic, resource pools/production,
construction session state, robot launch/placement, and any combat
properties (range/damage/projectile).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nether_earth.structures import FactoryType

__all__ = [
    "CANONICAL_WEAPON_ORDER",
    "CHASSIS_MODULES",
    "ELECTRONICS_MODULES",
    "MODULE_RESOURCE_CATEGORY",
    "WEAPON_MODULES",
    "BuildValidationError",
    "ModuleIdentity",
    "RobotBuild",
    "resource_category",
]


class ModuleIdentity(Enum):
    """The single authoritative catalog of robot module identities.

    Every robot module -- chassis, weapon, or electronics -- is one of
    these eight values. This is the one enum every later system imports;
    no other module in this package should declare a parallel module-name
    catalog (see the module docstring).
    """

    BIPOD = "bipod"
    TRACKS = "tracks"
    ANTI_GRAV = "anti_grav"
    CANNON = "cannon"
    MISSILE = "missile"
    PHASER = "phaser"
    NUCLEAR = "nuclear"
    ELECTRONICS = "electronics"


#: The three chassis identities (`_specs/functional-spec.md` §13).
CHASSIS_MODULES: frozenset[ModuleIdentity] = frozenset(
    {ModuleIdentity.BIPOD, ModuleIdentity.TRACKS, ModuleIdentity.ANTI_GRAV}
)

#: The four weapon identities (`_specs/functional-spec.md` §17), in the
#: canonical bottom-to-top stack order from `_specs/functional-spec.md`
#: §12. Both the *set* of legal weapon identities and their canonical
#: relative order are derived from this one tuple, so a second ordering
#: never needs to be kept in sync with it.
CANONICAL_WEAPON_ORDER: tuple[ModuleIdentity, ...] = (
    ModuleIdentity.CANNON,
    ModuleIdentity.MISSILE,
    ModuleIdentity.PHASER,
    ModuleIdentity.NUCLEAR,
)
WEAPON_MODULES: frozenset[ModuleIdentity] = frozenset(CANONICAL_WEAPON_ORDER)

#: The single electronics identity, kept as a one-element set for symmetry
#: with ``CHASSIS_MODULES``/``WEAPON_MODULES`` at call sites that check
#: "is this identity a member of this module class".
ELECTRONICS_MODULES: frozenset[ModuleIdentity] = frozenset({ModuleIdentity.ELECTRONICS})

#: The locked module-identity -> resource-category mapping
#: (`_specs/functional-spec.md` §9, §10.1). Categories are
#: ``nether_earth.structures.FactoryType`` values, not a new enum -- see the
#: module docstring for why. This dict is the one place this mapping is
#: defined; later tasks (construction cost, resource production) look up a
#: module's category here rather than re-deriving it.
MODULE_RESOURCE_CATEGORY: dict[ModuleIdentity, FactoryType] = {
    ModuleIdentity.BIPOD: FactoryType.CHASSIS,
    ModuleIdentity.TRACKS: FactoryType.CHASSIS,
    ModuleIdentity.ANTI_GRAV: FactoryType.CHASSIS,
    ModuleIdentity.CANNON: FactoryType.CANNON,
    ModuleIdentity.MISSILE: FactoryType.MISSILE,
    ModuleIdentity.PHASER: FactoryType.PHASER,
    ModuleIdentity.NUCLEAR: FactoryType.NUCLEAR,
    ModuleIdentity.ELECTRONICS: FactoryType.ELECTRONICS,
}


def resource_category(identity: ModuleIdentity) -> FactoryType:
    """Return the resource category ``identity`` draws construction cost from.

    Thin, named lookup over :data:`MODULE_RESOURCE_CATEGORY` so callers do
    not need to reach into the dict directly.
    """
    return MODULE_RESOURCE_CATEGORY[identity]


class BuildValidationError(ValueError):
    """Raised when a candidate robot build is structurally or semantically invalid."""


def _canonical_weapon_order(weapons: tuple[ModuleIdentity, ...]) -> tuple[ModuleIdentity, ...]:
    """Return ``weapons`` sorted into the canonical stack order.

    Shared by :meth:`RobotBuild.__post_init__` and
    :meth:`RobotBuild.from_modules` so both entry points normalize weapon
    order identically, independent of caller-supplied order.
    """
    return tuple(sorted(weapons, key=CANONICAL_WEAPON_ORDER.index))


@dataclass(frozen=True, slots=True)
class RobotBuild:
    """An immutable, validated, concrete robot build.

    A build is exactly one chassis module, one to three weapon modules
    (no duplicates), and zero or one electronics module. ``weapons`` is
    always normalized to the canonical bottom-to-top stack order
    (`_specs/functional-spec.md` §12) regardless of the order supplied to
    the constructor, so two builds naming the same loadout in a different
    order compare equal and hash identically -- construct via
    :meth:`from_modules` when the caller's collection order is not already
    canonical (e.g. player-selected modules), or directly when it is.

    Validation (`_specs/functional-spec.md` §11, locked) is enforced in
    ``__post_init__`` and raises :class:`BuildValidationError`:

    - ``chassis`` must be one of :data:`CHASSIS_MODULES`;
    - ``weapons`` must contain one to three entries, each one of
      :data:`WEAPON_MODULES`, with no duplicates (the nuke may be the only
      weapon);
    - ``electronics`` must be ``None`` or :data:`ModuleIdentity.ELECTRONICS`;
    - no module identity may appear more than once across the whole build.
      This falls directly out of the three checks above rather than needing
      its own separate check: :data:`CHASSIS_MODULES`, :data:`WEAPON_MODULES`,
      and :data:`ELECTRONICS_MODULES` are pairwise disjoint and exhaustive
      over :class:`ModuleIdentity`, so a value that passes its own field's
      membership check can never equal a value from a different field (see
      the comment in ``__post_init__``).
    """

    chassis: ModuleIdentity
    weapons: tuple[ModuleIdentity, ...]
    electronics: ModuleIdentity | None = None

    def __post_init__(self) -> None:
        if self.chassis not in CHASSIS_MODULES:
            raise BuildValidationError(
                f"invalid chassis module {self.chassis!r}: must be one of "
                f"{sorted(m.value for m in CHASSIS_MODULES)}"
            )
        if not 1 <= len(self.weapons) <= 3:
            raise BuildValidationError(
                f"a build must have 1 to 3 weapons, got {len(self.weapons)}"
            )
        for weapon in self.weapons:
            if weapon not in WEAPON_MODULES:
                raise BuildValidationError(
                    f"invalid weapon module {weapon!r}: must be one of "
                    f"{sorted(m.value for m in WEAPON_MODULES)}"
                )
        if len(set(self.weapons)) != len(self.weapons):
            raise BuildValidationError(f"duplicate weapon module in build: {self.weapons!r}")
        if self.electronics is not None and self.electronics not in ELECTRONICS_MODULES:
            raise BuildValidationError(
                f"invalid electronics module {self.electronics!r}: must be "
                f"{ModuleIdentity.ELECTRONICS!r} or None"
            )

        # No separate "no duplicate module identity across chassis/weapons/
        # electronics" check follows: it would be unreachable. CHASSIS_MODULES,
        # WEAPON_MODULES, and ELECTRONICS_MODULES are pairwise disjoint and
        # exhaustive over ModuleIdentity (enforced by the checks above, each
        # field only accepts values from its own set, and proven for the
        # catalog as a whole by
        # test_chassis_weapon_electronics_module_classes_partition_the_catalog
        # in test_robot_build.py) -- so no value that passes its own field's
        # membership check can ever equal a value from a different field. The
        # "no duplicate module identity anywhere in the build" rule from
        # `_specs/functional-spec.md` §11 is therefore already fully enforced
        # by the three per-field membership checks plus the weapons-only
        # duplicate check above.

        object.__setattr__(self, "weapons", _canonical_weapon_order(self.weapons))

    @classmethod
    def from_modules(cls, modules: list[ModuleIdentity] | tuple[ModuleIdentity, ...]) -> RobotBuild:
        """Classify a flat, unordered collection of modules into a build.

        This is the entry point for construction-flow callers (a player
        selecting modules in any order, with no structural guarantee on
        counts) -- it classifies ``modules`` by :data:`CHASSIS_MODULES`/
        :data:`WEAPON_MODULES`/:data:`ELECTRONICS_MODULES` membership and
        constructs a :class:`RobotBuild`, so count violations that are not
        representable by the field-based constructor alone (zero or two-plus
        chassis, zero or four-plus weapons, two-plus electronics) are
        rejected here with the same deterministic :class:`BuildValidationError`.
        """
        chassis_modules = [m for m in modules if m in CHASSIS_MODULES]
        weapon_modules = [m for m in modules if m in WEAPON_MODULES]
        electronics_modules = [m for m in modules if m in ELECTRONICS_MODULES]

        if len(chassis_modules) != 1:
            raise BuildValidationError(
                f"a build must have exactly one chassis, got {len(chassis_modules)}"
            )
        if len(electronics_modules) > 1:
            raise BuildValidationError(
                f"a build must have zero or one electronics module, got "
                f"{len(electronics_modules)}"
            )

        return cls(
            chassis=chassis_modules[0],
            weapons=tuple(weapon_modules),
            electronics=electronics_modules[0] if electronics_modules else None,
        )
