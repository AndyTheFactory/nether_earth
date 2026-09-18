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

Issue #34 (M4.3, `_specs/milestones/04-robots-construction-economy.md`,
`_specs/open-questions.md` §10 "Resource spending rules -- RESOLVED") adds
``starting_general_resources`` and the eight ``module_cost_*`` fields: the
canonical original-Spectrum construction economy. Unlike the "documented
placeholder, not independently verified" fields above
(``commander_vertical_update_ticks``, ``commander_height``,
``module_height_*``), these nine values are RESOLVED and locked exactly by
`_specs/open-questions.md` §10's disassembly-derived table -- starting
general resources 20; bipod 3; tracks 5; anti-grav 10; cannon 2; missile 4;
phaser 4; nuclear 20; electronics 3 -- and are still represented as named,
documented, overridable ``EngineRules`` fields (rather than bare literals)
so the one Spectrum-locked source of truth for construction cost lives here
and nowhere else in the engine, following the exact "one field per module
identity" convention the ``module_height_*`` fields established in Task 2
(M4.2) rather than a dict-valued field. The spend/refund algorithm that
consumes these fields lives in `construction_economy.py` (issue #34); this
module owns only the numeric configuration, not the spending logic.

Issue #54 (M4.4, `_specs/milestones/04-robots-construction-economy.md`,
`_specs/open-questions.md` §10 "Resource spending rules -- RESOLVED") adds
``factory_production_amount`` and ``war_base_production_amount``: the
per-game-day production the original Spectrum economy grants each owned
structure. Locked Spectrum defaults per §10: an owned factory produces
``2`` units of its own type-specific resource category per in-game day, and
an owned war base produces ``5`` general resources per in-game day. The
day-length interval itself is *not* duplicated here -- it is already the
single authoritative ``clock.TICKS_PER_GAME_DAY`` constant
(`clock.py`) and production code (`resource_production.py`) imports it
directly rather than this module redeclaring a second day-length value.

Issue #56 (M4.6, `_specs/milestones/04-robots-construction-economy.md`)
adds ``max_robots_per_player``: the per-player robot launch cap enforced by
`robot_launch.py`. `_specs/functional-spec.md` locks this at the original
ZX Spectrum's cap of 24 robots per player at a time -- represented as a
named, documented, overridable ``EngineRules`` field (rather than a bare
``24`` literal inlined at the launch call site) following this module's
established convention for every other rule-legality constant.

Issue #60 (M5.1, `_specs/milestones/05-orders-navigation-capture.md`) adds
the seven robot-movement timing fields (``robot_move_ticks_*``,
``robot_rough_multiplier_*``, ``robot_ditch_multiplier_anti_grav``): the
integer per-cell movement durations `movement.py`'s shared movement
executor consumes. `_specs/open-questions.md` §4 ("Exact movement speeds
and terrain penalties -- PARTIALLY RESOLVED") locks only the *relative*
behavior -- bipod slowest, tracks faster, anti-grav fastest on ordinary
terrain; bipod's rough-terrain slowdown severe, tracks' smaller;
authoritative timing is integer ticks-per-tile at 20 Hz -- and explicitly
leaves the exact ticks-per-tile per chassis, the exact bipod/tracked
rough penalties, and whether anti-grav speed is identical on every
traversable terrain type UNRESOLVED. These fields therefore follow the
same "documented default, not independently verified" precedent as
``commander_vertical_update_ticks``/``commander_height``: the defaults
below express exactly the locked relative ordering and nothing more, live
named/documented/overridable in this one place (no movement call site
inlines a literal), and are owned for fidelity finalization by milestone
issue #61 (M5.2), which can correct them here without any movement
architecture change. Per-cell cost is expressed as a per-chassis base
ticks-per-cell multiplied by a per-chassis terrain multiplier (``1`` means
"no penalty relative to ordinary terrain"), so evidence resolving either
half of §4 lands as a value change here rather than a shape change.
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
    - ``starting_general_resources``: each player's general resource pool at
      the start of a match (locked Spectrum default ``20``, per
      `_specs/open-questions.md` §10).
    - ``module_cost_bipod`` / ``module_cost_tracks`` / ``module_cost_anti_grav``
      / ``module_cost_cannon`` / ``module_cost_missile`` / ``module_cost_phaser``
      / ``module_cost_nuclear`` / ``module_cost_electronics``: the construction
      resource cost of each :class:`~nether_earth.robot_build.ModuleIdentity`
      module, spent from the module's resource category first and then from
      general resources for any shortfall (`construction_economy.py`, issue
      #34). Locked Spectrum defaults, per `_specs/open-questions.md` §10:
      bipod ``3``, tracks ``5``, anti-grav ``10``, cannon ``2``, missile
      ``4``, phaser ``4``, nuclear ``20``, electronics ``3``.
    - ``factory_production_amount``: type-specific resource units an owned
      factory produces per in-game day (locked Spectrum default ``2``, per
      `_specs/open-questions.md` §10).
    - ``war_base_production_amount``: general resource units an owned war
      base produces per in-game day (locked Spectrum default ``5``, per
      `_specs/open-questions.md` §10).
    - ``max_robots_per_player``: maximum number of robots a player may have
      launched and alive at once (locked Spectrum default ``24``, per
      `_specs/technical-spec.md` §7 (``GameRules.max_robots_per_player``)
      and `_specs/functional-spec.md` §11 "Construction cannot launch
      when: player already has 24 robots"). Enforced by `robot_launch.py`
      (issue #56).
    - ``robot_move_ticks_bipod`` / ``robot_move_ticks_tracks`` /
      ``robot_move_ticks_anti_grav``: simulation ticks a robot with that
      chassis takes to move one cell across ordinary (``NORMAL``) terrain.
      Defaults (``8`` / ``6`` / ``4``) encode only the locked relative
      ordering "bipod < tracks < anti-grav" in speed; the exact values are
      documented defaults pending issue #61 -- see the module docstring.
    - ``robot_rough_multiplier_bipod`` / ``robot_rough_multiplier_tracks``
      / ``robot_rough_multiplier_anti_grav``: multiplier applied to that
      chassis' base per-cell ticks when entering ``ROUGH`` terrain.
      Defaults (``3`` / ``2`` / ``1``) encode only the locked relative
      rule "rough slows bipod severely, tracks less severely"; anti-grav's
      ``1`` reflects `_specs/open-questions.md` §4's still-open question of
      whether anti-grav speed is uniform across traversable terrain, made
      configurable here rather than asserted as verified.
    - ``robot_ditch_multiplier_anti_grav``: multiplier applied to
      anti-grav's base per-cell ticks when entering ``DITCH`` terrain
      (anti-grav is the only chassis permitted to; see `movement.py`).
      Default ``1``, for the same still-open §4 reason as the rough
      multiplier above.
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
    starting_general_resources: int = 20
    module_cost_bipod: int = 3
    module_cost_tracks: int = 5
    module_cost_anti_grav: int = 10
    module_cost_cannon: int = 2
    module_cost_missile: int = 4
    module_cost_phaser: int = 4
    module_cost_nuclear: int = 20
    module_cost_electronics: int = 3
    factory_production_amount: int = 2
    war_base_production_amount: int = 5
    max_robots_per_player: int = 24
    robot_move_ticks_bipod: int = 8
    robot_move_ticks_tracks: int = 6
    robot_move_ticks_anti_grav: int = 4
    robot_rough_multiplier_bipod: int = 3
    robot_rough_multiplier_tracks: int = 2
    robot_rough_multiplier_anti_grav: int = 1
    robot_ditch_multiplier_anti_grav: int = 1

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
        if self.starting_general_resources < 0:
            raise ValueError("starting_general_resources must be non-negative")
        for field_name in (
            "module_cost_bipod",
            "module_cost_tracks",
            "module_cost_anti_grav",
            "module_cost_cannon",
            "module_cost_missile",
            "module_cost_phaser",
            "module_cost_nuclear",
            "module_cost_electronics",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.factory_production_amount <= 0:
            raise ValueError("factory_production_amount must be a positive integer")
        if self.war_base_production_amount <= 0:
            raise ValueError("war_base_production_amount must be a positive integer")
        if self.max_robots_per_player <= 0:
            raise ValueError("max_robots_per_player must be a positive integer")
        for field_name in (
            "robot_move_ticks_bipod",
            "robot_move_ticks_tracks",
            "robot_move_ticks_anti_grav",
            "robot_rough_multiplier_bipod",
            "robot_rough_multiplier_tracks",
            "robot_rough_multiplier_anti_grav",
            "robot_ditch_multiplier_anti_grav",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


#: Canonical, Spectrum-compatible default rule set. Calling code should
#: reference this shared instance rather than constructing ad hoc
#: ``EngineRules()`` copies, so a single object identity represents "the
#: default rules" wherever it is passed around.
DEFAULT_RULES = EngineRules()
