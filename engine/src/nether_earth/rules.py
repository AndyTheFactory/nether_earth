"""Centralized engine game-rule configuration.

This module is the single source of truth for tunable gameplay numeric
constants that would otherwise be scattered as magic-number literals across
the engine. Issue #37 (`_specs/milestones/03-commander-movement-docking.md`)
introduces the first five values here: the commander vertical-movement
envelope. Later milestones are expected to extend :class:`EngineRules`
(rather than reintroduce ad hoc literals elsewhere) as more gameplay systems
gain configurable numeric constants.

Locked defaults (`_specs/open-questions.md` §13, `_specs/technical-spec.md`
§9, `_specs/functional-spec.md` §8.2) are Spectrum-compatible:

- ``commander_min_altitude = 0``
- ``commander_max_altitude = 48``
- ``commander_vertical_update_ticks = 4``
- ``commander_ascent_step = 2``
- ``commander_descent_step = 1``

Open-question note (`_specs/open-questions.md` §13): the minimum/maximum
altitude and the +2/-1 ascent/descent step sizes are RESOLVED and locked.
The *tick cadence* of a vertical "update" (``commander_vertical_update_ticks``)
is recorded here as its documented canonical default of ``4`` (matching the
value milestone issue #43 references as locked), but the underlying
Spectrum-timing research behind that exact cadence is not independently
verified in `_specs/open-questions.md` §13 -- the spec explicitly flags it as
not fully closed. It is therefore represented as a named, documented,
overridable configuration constant (not a bare literal inlined at call
sites) precisely so that later evidence can correct it in one place without
an architecture change, rather than being silently treated as ground truth.

Issue #39 (`_specs/milestones/03-commander-movement-docking.md`, M3.3) adds
``commander_height``: none of `_specs/functional-spec.md` §8,
`_specs/technical-spec.md` §9, or `_specs/open-questions.md` §12-§14 give the
commander an explicit physical vertical extent in altitude units -- they
describe the collision *rules* (height-aware, vertical-range overlap) but
not a concrete height constant. Height-aware collision cannot be implemented
against a zero-thickness point, so this field supplies a documented,
overridable default (``4``, deliberately small relative to the 0..48
altitude envelope and the +2/-1 step sizes) rather than silently hardcoding
an unverified number inside ``collision.py``. Like
``commander_vertical_update_ticks``, this is a "documented default, not
independently verified" constant: later Spectrum sprite-geometry evidence
may correct it in this one place without an architecture change.

Issue #38 adds ``commander_horizontal_move_ticks``: the number of
simulation ticks a single cell-to-cell horizontal commander move takes to
resolve. `_specs/technical-spec.md` §7.2 defines the generic
``GridTransition`` shape (``started_tick``/``duration_ticks``) that a
cell-to-cell move uses, but -- unlike the vertical envelope above -- no
spec section independently verifies the exact tick duration for a
commander's horizontal move specifically. Following the exact same pattern
as ``commander_vertical_update_ticks``, it is recorded here as a named,
documented, overridable canonical default (``4``, matching the vertical
cadence for a round, easy-to-reason-about default) rather than a bare
literal, so later Spectrum-timing evidence can correct it in one place
without an architecture change.

Issue #53 (M4.2, `_specs/milestones/04-robots-construction-economy.md`)
adds the eight ``module_height_*`` fields: the per-physical-vertical-extent
of each :class:`~nether_earth.robot_build.ModuleIdentity` module, consumed
by `robot_stack.py` to derive a robot build's total physical height as the
sum of its stacked components' heights. `_specs/functional-spec.md` §11-13
and `_specs/technical-spec.md` §12.1 lock the bottom-to-top *order* of the
stack (chassis, cannon, missile, phaser, nuke, electronics) but -- like
``commander_height`` above -- neither they nor `_specs/open-questions.md`
give any module an explicit numeric vertical extent; no Spectrum sprite
geometry for individual module heights is available anywhere in the locked
specs or evidence. Height-summing cannot be implemented against zero-height
components, so each field supplies a documented, overridable default
following the exact same "documented default, not independently verified"
precedent as ``commander_height``: chassis modules (``module_height_bipod``,
``module_height_tracks``, ``module_height_anti_grav``) default to ``4``
(matching ``commander_height``'s magnitude, since a chassis is a robot's
main structural body), and weapon/electronics modules
(``module_height_cannon``, ``module_height_missile``, ``module_height_phaser``,
``module_height_nuclear``, ``module_height_electronics``) default to ``2``
(smaller mounted add-ons stacked above the chassis). These are placeholder
magnitudes only, chosen for internal proportion, not derived from any
Spectrum evidence; later sprite-geometry research may correct any of them
in this one place without an architecture change. Each module identity gets
its own named field (rather than one dict-valued field) to match this
module's existing flat-scalar-field convention and keep ``EngineRules``
trivially hashable/equatable.
"""

from dataclasses import dataclass

__all__ = ["DEFAULT_RULES", "EngineRules"]


@dataclass(frozen=True, slots=True)
class EngineRules:
    """Centralized, overridable engine gameplay-rule configuration.

    Every numeric gameplay constant that governs rule legality (as opposed
    to one-off local behavior) should live here rather than as a literal at
    its point of use, so a single object is the authoritative source for
    "what are the current rule values" and so tests can exercise
    non-default configurations without monkeypatching module-level
    constants.

    Fields:

    - ``commander_min_altitude``: lowest legal commander altitude (locked
      Spectrum default ``0``).
    - ``commander_max_altitude``: highest legal commander altitude (locked
      Spectrum default ``48``).
    - ``commander_vertical_update_ticks``: number of simulation ticks
      between vertical-physics updates (documented canonical default ``4``;
      see the module docstring for why the exact cadence is still flagged as
      research-pending rather than fully verified).
    - ``commander_ascent_step``: altitude gained per vertical update while
      ascending (locked Spectrum default ``2``).
    - ``commander_descent_step``: altitude lost per vertical update while
      descending/falling (locked Spectrum default ``1``; intentionally
      asymmetric with ascent per `_specs/open-questions.md` §13).
    - ``commander_height``: the commander's physical vertical extent, in the
      same altitude units as ``commander_min_altitude``/``commander_max_altitude``,
      used by height-aware collision (`collision.py`, issue #39) to turn an
      ``altitude`` scalar into an occupied vertical range
      ``[altitude, altitude + commander_height)``. Documented default ``4``,
      not independently verified against Spectrum sprite geometry -- see the
      module docstring.
    - ``commander_horizontal_move_ticks``: number of simulation ticks a
      single cell-to-cell horizontal commander move takes to resolve
      (documented canonical default ``4``; see the module docstring for why
      this exact duration is not independently spec-verified, unlike the
      vertical envelope above).
    - ``module_height_bipod`` / ``module_height_tracks`` /
      ``module_height_anti_grav`` / ``module_height_cannon`` /
      ``module_height_missile`` / ``module_height_phaser`` /
      ``module_height_nuclear`` / ``module_height_electronics``: the
      physical vertical extent (altitude units) of each
      :class:`~nether_earth.robot_build.ModuleIdentity` module, used by
      `robot_stack.py` (issue #53) to derive a robot build's total physical
      height. Documented placeholder defaults (chassis modules ``4``,
      weapon/electronics modules ``2``), not independently verified against
      Spectrum sprite geometry -- see the module docstring.
    """

    commander_min_altitude: int = 0
    commander_max_altitude: int = 48
    commander_vertical_update_ticks: int = 4
    commander_ascent_step: int = 2
    commander_descent_step: int = 1
    commander_height: int = 4
    commander_horizontal_move_ticks: int = 4
    module_height_bipod: int = 4
    module_height_tracks: int = 4
    module_height_anti_grav: int = 4
    module_height_cannon: int = 2
    module_height_missile: int = 2
    module_height_phaser: int = 2
    module_height_nuclear: int = 2
    module_height_electronics: int = 2

    def __post_init__(self) -> None:
        if self.commander_min_altitude < 0:
            raise ValueError("commander_min_altitude must be non-negative")
        if self.commander_max_altitude <= self.commander_min_altitude:
            raise ValueError("commander_max_altitude must exceed commander_min_altitude")
        if self.commander_vertical_update_ticks <= 0:
            raise ValueError("commander_vertical_update_ticks must be a positive integer")
        if self.commander_ascent_step <= 0:
            raise ValueError("commander_ascent_step must be a positive integer")
        if self.commander_descent_step <= 0:
            raise ValueError("commander_descent_step must be a positive integer")
        if self.commander_height <= 0:
            raise ValueError("commander_height must be a positive integer")
        if self.commander_horizontal_move_ticks <= 0:
            raise ValueError("commander_horizontal_move_ticks must be a positive integer")
        for field_name in (
            "module_height_bipod",
            "module_height_tracks",
            "module_height_anti_grav",
            "module_height_cannon",
            "module_height_missile",
            "module_height_phaser",
            "module_height_nuclear",
            "module_height_electronics",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


#: Canonical, Spectrum-compatible default rule set. Calling code should
#: reference this shared instance rather than constructing ad hoc
#: ``EngineRules()`` copies, so a single object identity represents "the
#: default rules" wherever it is passed around.
DEFAULT_RULES = EngineRules()
