"""Canonical robot component stack and height derivation (issue #53, M4.2).

`_specs/technical-spec.md` §12.1 and `_specs/milestones/04-robots-construction-
economy.md` (M4.2) require "one engine function [that] derives physical/
render order and total height" from a valid robot build, with rendering,
collision, docking, construction preview, and projectile interaction all
consuming the *same* stack metadata rather than each re-deriving it. This
module is that one canonical source of truth. Task 1 (M4.1, issue #52,
`robot_build.py`) already owns *build validity* (which module identities may
coexist and in what normalized weapon order); this module owns turning an
already-valid :class:`~nether_earth.robot_build.RobotBuild` into (a) its
physical bottom-to-top component stack and (b) its total physical height.
Nothing here re-validates a build or re-declares module identities/order --
see the module docstring of `robot_build.py` for why that catalog must not
be duplicated.

Locked stack order (`_specs/technical-spec.md` §12.1, bottom to top)::

    chassis
    cannon
    missile
    phaser
    nuke
    electronics

("commander (when docked)" is also listed in §12.1 as a conceptually higher
layer, but docking is M3's existing, already-implemented concern
(`collision.py`, `docking.py`) and is out of scope here -- this module
derives only the *robot's own* component stack from a ``RobotBuild``.)
Missing intermediate weapons are omitted while the remaining weapons keep
their relative order; :class:`~nether_earth.robot_build.RobotBuild` already
guarantees ``weapons`` is normalized to this exact canonical order
(`robot_build.py`'s ``_canonical_weapon_order``), so this module does not
need to re-sort anything -- it only concatenates chassis, the
already-ordered weapons, and electronics (if fitted). This is also why the
stack is order-independent of caller module-selection order: normalization
already happened once, in ``RobotBuild.__post_init__``/``from_modules``, and
a :class:`~nether_earth.robot_build.RobotBuild` value is equal regardless of
the order modules were supplied in, so two builds naming the same loadout
always derive an identical stack and height here.

Height source: same metadata as the stack, no duplicate table
-----------------------------------------------------------------
Total height is the sum of each present component's height, sourced from
the eight ``module_height_*`` fields of
:class:`~nether_earth.rules.EngineRules` (the Spectrum's
``Ld7b4_piece_heights`` values since CR003.3 -- see ``rules.py``'s module
docstring). :func:`derive_stack` and
:func:`derive_height` both walk the same :func:`_module_heights` mapping
built from one ``rules`` argument, so stack order and height are always
read from the identical per-module metadata -- there is no second height
table anywhere in this module or callers to silently drift out of sync.

Relationship to ``structures.Component.height`` / ``collision.py``
------------------------------------------------------------------
`structures.py`'s ``Component.height`` and this module's
``module_height_*`` are a *distinct* concept, not two names for the same
thing: ``Component.height`` is the physical height of one cell of a
*static* structure (war base/factory/blocker composition, `_specs/technical-
spec.md` §7.3), used by `collision.py`'s ground-rooted
``component_vertical_range`` (``[0, height)``) for commander collision
against the *map*. The per-module heights here describe one *mobile robot
build's own* component stack (chassis/weapons/electronics) -- an entirely
different structural layer that has no ``Component``/map-cell identity at
all. `collision.py`'s ``RobotFixture.height`` (a placeholder scalar "one
robot, one height" stand-in, explicitly documented there as *not* the real
robot model) is the closest existing relative: once M5/M6 wire a real robot
entity through, ``derive_height`` from this module is expected to be the
authoritative source that placeholder's scalar height is replaced with --
but that wiring is out of scope for this task (see `robot_stack.py`'s scope
note below and the milestone's task list).

Explicitly out of scope for this task (left to later M4/M5/M6 tasks)
----------------------------------------------------------------------
Rendering, commander docking behavior/height changes, weapon firing/damage,
resource spending, construction session state, and robot launch/placement.
This module only derives stack order and total height from an already-valid
``RobotBuild``; later systems (construction preview, launch, collision,
combat) are expected to call into this module rather than re-deriving or
duplicating this logic themselves.
"""

from __future__ import annotations

from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.rules import DEFAULT_RULES, EngineRules

__all__ = ["derive_height", "derive_stack", "derive_stack_and_height"]


def _module_heights(rules: EngineRules) -> dict[ModuleIdentity, int]:
    """Return the per-module physical height mapping backing ``rules``.

    The one place this task reads ``rules.module_height_*`` into a
    :class:`~nether_earth.robot_build.ModuleIdentity`-keyed mapping, so
    :func:`derive_stack`'s ordering and :func:`derive_height`'s summation
    both draw from this same mapping for a given ``rules`` value -- see the
    module docstring's "same source, no duplicate table" note.
    """
    return {
        ModuleIdentity.BIPOD: rules.module_height_bipod,
        ModuleIdentity.TRACKS: rules.module_height_tracks,
        ModuleIdentity.ANTI_GRAV: rules.module_height_anti_grav,
        ModuleIdentity.CANNON: rules.module_height_cannon,
        ModuleIdentity.MISSILE: rules.module_height_missile,
        ModuleIdentity.PHASER: rules.module_height_phaser,
        ModuleIdentity.NUCLEAR: rules.module_height_nuclear,
        ModuleIdentity.ELECTRONICS: rules.module_height_electronics,
    }


def derive_stack(build: RobotBuild) -> tuple[ModuleIdentity, ...]:
    """Return ``build``'s physical bottom-to-top component stack.

    Locked order (`_specs/technical-spec.md` §12.1): chassis, then
    ``build.weapons`` (already normalized to cannon/missile/phaser/nuke
    order by :class:`~nether_earth.robot_build.RobotBuild`, with missing
    intermediate weapons already omitted while the rest keep their relative
    order), then electronics if fitted. This function does no additional
    sorting or validation -- ``build`` is assumed already valid, which
    ``RobotBuild.__post_init__``/``from_modules`` guarantee for any
    constructed instance.
    """
    stack: list[ModuleIdentity] = [build.chassis, *build.weapons]
    if build.electronics is not None:
        stack.append(build.electronics)
    return tuple(stack)


def derive_height(build: RobotBuild, rules: EngineRules = DEFAULT_RULES) -> int:
    """Return ``build``'s total physical height as an integer.

    Sum of each stacked component's ``module_height_*`` value from
    ``rules`` (default :data:`~nether_earth.rules.DEFAULT_RULES`), walked in
    :func:`derive_stack` order. Order does not affect the sum, but deriving
    it from :func:`derive_stack` (rather than iterating ``build`` directly)
    keeps this function reading the exact same component set the stack
    exposes, per the module docstring's single-source-of-truth requirement.
    """
    heights = _module_heights(rules)
    return sum(heights[module] for module in derive_stack(build))


def derive_stack_and_height(
    build: RobotBuild, rules: EngineRules = DEFAULT_RULES
) -> tuple[tuple[ModuleIdentity, ...], int]:
    """Return ``(derive_stack(build), derive_height(build, rules))`` together.

    Convenience for callers that need both the stack and the height in one
    call (e.g. a construction preview or launch that wants both without two
    separate lookups) -- computes each ``module_height_*`` mapping from
    ``rules`` exactly once rather than twice.
    """
    heights = _module_heights(rules)
    stack = derive_stack(build)
    return stack, sum(heights[module] for module in stack)
