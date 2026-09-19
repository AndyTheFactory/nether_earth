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
executor consumes. Per-cell cost is expressed as a per-chassis base
ticks-per-cell multiplied by a per-chassis terrain multiplier (``1`` means
"no penalty relative to ordinary terrain"), so evidence resolving either
half of `_specs/open-questions.md` §4 lands as a value change here rather
than a shape change.

Issue #61 (M5.2) is the fidelity-finalization pass over these same seven
fields, per `_specs/open-questions.md` §4 ("Exact movement speeds and
terrain penalties -- PARTIALLY RESOLVED"). It resolved the three
``robot_move_ticks_*`` (ordinary-terrain) defaults directly from disassembly
evidence (`santiontanon/netherearth-disassembly`,
``netherearth-annotated.asm``'s ``Lb61d_robot_movement_speed_table``: bipod/
tracks/anti-grav = 6/4/3 "cycles" on flat terrain) mapped onto the locked
20 Hz tick rate via the disassembly's own documented cadence
(``MIN_INTERRUPTS_PER_GAME_CYCLE: equ 10 ; game maximum speed is 5 frames
per second``, i.e. 1 cycle = 200 ms = 4 ticks): bipod ``24``, tracks ``16``,
anti-grav ``12``. It also confirmed ``robot_rough_multiplier_anti_grav = 1``
against the same table (anti-grav identical on flat/rugged: 3 cycles both).

It could **not** resolve ``robot_rough_multiplier_bipod`` /
``robot_rough_multiplier_tracks`` / ``robot_ditch_multiplier_anti_grav``
to exact disassembly values: the raw evidence does not unambiguously
support the locked "tracks penalized less severely than bipod on rough
terrain" ordering (tied under an absolute-cycle-increase reading, reversed
under a proportional reading), and neither the rough nor the ditch raw
ratios (1.33x-1.5x) are representable as a clean integer multiplier on
this ``int`` schema without inventing precision. Per `_specs/open-questions.md`
§4, all three remain the pre-existing, explicitly unverified placeholder
values (``3`` / ``2`` / ``1``) -- chosen only to satisfy the locked
qualitative ordering, not presented as recovered exact Spectrum constants
-- pending a human decision on how to reconcile the evidence conflict.

Issue #70 (M6.1, `_specs/milestones/06-combat-damage-victory.md`, "Locked
combat rules") adds the nine combat metadata fields: ``cannon_range_cells``,
``missile_range_cells``, ``phaser_range_cells``,
``electronics_range_bonus_cells``, ``nuclear_radius_cells``,
``normal_projectile_altitude``, ``cannon_damage_multiplier``,
``missile_damage_multiplier``, ``phaser_damage_multiplier``. These are the
canonical locked Spectrum weapon range/effect defaults from the milestone
spec's "Canonical default ranges/effects" section, converted from miles to
cells via the shared ``miles_to_cells`` helper already defined in this
module: cannon 10 miles = 20 cells, missile 14 miles = 28 cells, phaser
10 miles = 20 cells, electronics bonus 3 miles = 6 cells, nuclear radius
8 miles = 16 cells. All weapon ranges and the nuclear radius resolve from
`_specs/open-questions.md` §3 ("miles/grid conversion -- RESOLVED") and are
locked game rules; projectile altitude and damage multipliers are explicitly
locked Spectrum defaults per the milestone spec. Together these form the
authoritative rule set for all later combat tasks.

Issue #70 also adds one placeholder field, ``projectile_max_range_cells``,
for an as-yet-unresolved projectile-lifetime rule. This field will be
consumed by a later task to gate projectile termination; the default is
set to the longest locked weapon range (missile at 28 cells), a documented,
explicitly unverified policy choice (see the module docstring's "documented
default, not independently verified" precedent for the rationale). Later
Spectrum evidence on projectile expiry may correct this in one place
without an architecture change.
"""

from dataclasses import dataclass

__all__ = ["CELLS_PER_MILE", "DEFAULT_RULES", "EngineRules", "miles_to_cells"]


#: How many grid cells one in-game *mile* spans.
#:
#: `_specs/open-questions.md` §3 locks this as a resolved rule: "1 mile = 2
#: cells". Every spec-facing distance in this game is stated in miles
#: (``Advance 0-50 miles``, weapon ranges, the nuclear blast radius) while
#: every engine-facing distance is stated in cells, so the conversion is
#: needed by more than one subsystem. §3 requires it to exist exactly once
#: in shared game-rule/helper code -- this module -- rather than being
#: re-spelled as a literal ``* 2`` at each call site. `orders.py`
#: re-exports :func:`miles_to_cells` for convenience, but this is its only
#: definition, so a later combat milestone can convert weapon ranges
#: without taking a dependency on the orders subsystem.
#:
#: It is a module constant rather than an :class:`EngineRules` field
#: because it is a *unit definition*, not a tunable: an ``EngineRules``
#: override that made a mile three cells would silently reinterpret every
#: spec quotation in the codebase rather than retune a balance value.
CELLS_PER_MILE = 2


def miles_to_cells(miles: int) -> int:
    """Convert a spec-stated distance in miles to engine grid cells.

    The single conversion point described on :data:`CELLS_PER_MILE`.
    Integer in, integer out -- gameplay distances are never floating point
    (see `AGENTS.md`'s "avoid floating-point gameplay state when integer
    ticks/grid values can express the rule").

    Negative inputs are rejected: a distance is a magnitude, and direction
    is the caller's own concern (``Retreat`` converts its magnitude and
    then applies the westward sign itself).
    """
    if miles < 0:
        raise ValueError("miles must be non-negative")
    return miles * CELLS_PER_MILE


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
      Defaults (``24`` / ``16`` / ``12``) are evidence-backed (issue #61,
      disassembly ``Lb61d_robot_movement_speed_table`` flat-terrain row x
      the evidence-derived 4-ticks-per-game-cycle conversion factor -- see
      the module docstring) rather than placeholders; they encode both the
      locked relative ordering "bipod < tracks < anti-grav" in speed and
      the specific magnitude.
    - ``robot_rough_multiplier_bipod`` / ``robot_rough_multiplier_tracks``:
      multiplier applied to that chassis' base per-cell ticks when entering
      ``ROUGH`` terrain. Defaults (``3`` / ``2``) remain the pre-existing,
      explicitly *unverified* placeholders (issue #61 could not derive a
      disassembly-exact integer multiplier -- see the module docstring);
      they encode only the locked relative rule "rough slows bipod
      severely, tracks less severely" and nothing more.
    - ``robot_rough_multiplier_anti_grav``: multiplier applied to
      anti-grav's base per-cell ticks when entering ``ROUGH`` terrain.
      Default ``1`` is evidence-backed (issue #61: the disassembly table
      shows anti-grav identical on flat and rugged terrain).
    - ``robot_ditch_multiplier_anti_grav``: multiplier applied to
      anti-grav's base per-cell ticks when entering ``DITCH`` terrain
      (anti-grav is the only chassis permitted to; see `movement.py`).
      Default ``1`` remains the pre-existing placeholder -- issue #61 found
      disassembly evidence that anti-grav is *not* uniform across every
      traversable terrain type (it is measurably slower on the most
      extreme terrain tier), but that evidence's ~1.33x ratio has no clean
      integer-multiplier representation on this schema, so this field is
      flagged in `_specs/open-questions.md` §4 as a known likely
      under-estimate rather than silently "corrected" with an invented
      integer.
    - ``capture_duration_ticks``: the number of continuous authoritative
      ticks a qualifying enemy robot must occupy a factory's or war base's
      canonical capture interaction location before ownership transfers
      (`capture.py`, issue #66, M5.7). RESOLVED and locked by
      `_specs/open-questions.md` §6 ("War-base capture mechanics") -- default
      duration 12 in-game hours = 1,440 simulation ticks at the locked 20 Hz
      tick rate = 72 real seconds -- and applies identically to enemy
      factory capture per that same section ("War-base capture uses the
      same continuous-occupation rule as factory capture by default").
      Represented as a named, documented, overridable ``EngineRules`` field
      (rather than a bare ``1440`` literal inlined at any capture call
      site) so scenario data can override it, per §6's explicit "capture
      duration is configurable game-rule/scenario data."  Neutral factory
      acquisition (`_specs/functional-spec.md` §9) is instantaneous for the
      first qualifying robot and does not consume this field at all.
    - ``cannon_range_cells``: the maximum firing range of a cannon-equipped
      robot, in grid cells (issue #70, M6.1,
      `_specs/milestones/06-combat-damage-victory.md` "Locked combat rules").
      Locked Spectrum default: 10 miles = 20 cells.
    - ``missile_range_cells``: the maximum firing range of a
      missile-equipped robot, in grid cells (issue #70, M6.1). Locked
      Spectrum default: 14 miles = 28 cells.
    - ``phaser_range_cells``: the maximum firing range of a
      phaser-equipped robot, in grid cells (issue #70, M6.1). Locked
      Spectrum default: 10 miles = 20 cells.
    - ``electronics_range_bonus_cells``: the maximum additional range granted
      by an electronics module when fitted, in grid cells (issue #70, M6.1).
      Locked Spectrum default: 3 miles = 6 cells. This is a nominal bonus;
      exact electronics accuracy and resistance mechanics remain research-owned.
    - ``nuclear_radius_cells``: the blast radius of a nuclear detonation,
      in grid cells (issue #70, M6.1). Locked Spectrum default: 8 miles =
      16 cells. All robots and structures within this radius of the
      detonation point are destroyed; the carrier robot is always destroyed.
    - ``normal_projectile_altitude``: the fixed altitude at which normal
      (cannon/missile/phaser) projectiles travel, in the same altitude units
      as the commander vertical envelope (issue #70, M6.1). Locked Spectrum
      default: ``10``. This altitude is independent of the firing robot's
      height and applies uniformly to all three normal weapon types.
    - ``cannon_damage_multiplier``: the damage multiplier for cannon hits
      (issue #70, M6.1). Locked Spectrum default: ``2``. Final damage is
      computed as ``base_damage * multiplier`` where ``base_damage`` is
      derived from the target robot's height, stack height, and ground height.
    - ``missile_damage_multiplier``: the damage multiplier for missile hits
      (issue #70, M6.1). Locked Spectrum default: ``3``.
    - ``phaser_damage_multiplier``: the damage multiplier for phaser hits
      (issue #70, M6.1). Locked Spectrum default: ``4``.
    - ``projectile_max_range_cells``: a documented, unverified placeholder
      for the maximum distance a projectile may travel before terminating
      (issue #70, M6.1, consumed by M6.4). Spectrum evidence on whether
      projectile lifetime is tied to absolute range/distance or a tick
      duration remains unresolved (`_specs/open-questions.md` §8). This field
      defaults to ``28`` cells, the longest locked weapon range (missile),
      as an explicitly provisional policy choice pending later evidence; it
      is *not* presented as a verified Spectrum constant, only as a named
      configurable placeholder. Later research may correct it in this one
      place without an architecture change.
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
    robot_move_ticks_bipod: int = 24
    robot_move_ticks_tracks: int = 16
    robot_move_ticks_anti_grav: int = 12
    robot_rough_multiplier_bipod: int = 3
    robot_rough_multiplier_tracks: int = 2
    robot_rough_multiplier_anti_grav: int = 1
    robot_ditch_multiplier_anti_grav: int = 1
    capture_duration_ticks: int = 1440
    cannon_range_cells: int = miles_to_cells(10)
    missile_range_cells: int = miles_to_cells(14)
    phaser_range_cells: int = miles_to_cells(10)
    electronics_range_bonus_cells: int = miles_to_cells(3)
    nuclear_radius_cells: int = miles_to_cells(8)
    normal_projectile_altitude: int = 10
    cannon_damage_multiplier: int = 2
    missile_damage_multiplier: int = 3
    phaser_damage_multiplier: int = 4
    projectile_max_range_cells: int = 28

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
        if self.capture_duration_ticks <= 0:
            raise ValueError("capture_duration_ticks must be a positive integer")
        for field_name in (
            "cannon_range_cells",
            "missile_range_cells",
            "phaser_range_cells",
            "electronics_range_bonus_cells",
            "nuclear_radius_cells",
            "normal_projectile_altitude",
            "cannon_damage_multiplier",
            "missile_damage_multiplier",
            "phaser_damage_multiplier",
            "projectile_max_range_cells",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


#: Canonical, Spectrum-compatible default rule set. Calling code should
#: reference this shared instance rather than constructing ad hoc
#: ``EngineRules()`` copies, so a single object identity represents "the
#: default rules" wherever it is passed around.
DEFAULT_RULES = EngineRules()
