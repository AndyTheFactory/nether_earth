"""Centralized engine game-rule configuration.

This module is the single source of truth for tunable gameplay numeric
constants that would otherwise be scattered as magic-number literals across
the engine. Issue #37 (`_specs/milestones/03-commander-movement-docking.md`)
introduces the first five values here: the commander vertical-movement
envelope. Later milestones are expected to extend :class:`EngineRules`
(rather than reintroduce ad hoc literals elsewhere) as more gameplay systems
gain configurable numeric constants.

Locked defaults (`_specs/open-questions.md` §13, `_specs/technical-spec.md`
§9, `_specs/functional-spec.md` §8.2) are Spectrum-compatible except the
descent step:

- ``commander_min_altitude = 0``
- ``commander_max_altitude = 48``
- ``commander_vertical_update_ticks = 4``
- ``commander_ascent_step = 2``
- ``commander_descent_step = 2``

Open-question note (`_specs/open-questions.md` §13): the minimum/maximum
altitude and the +2/-2 ascent/descent step sizes are RESOLVED and locked.
The Spectrum's gravity is -1 per game cycle (``Lafc3_gravity``); the owner
changed it to -2 (CR003.1 #216, owner decision 2026-09-22), a deliberate
deviation so 48 -> 0 takes 4.8 s like the ascent. Gravity still stops on
the surface under the ship at an odd altitude
(``commander_movement._gravity_landing_altitude``).
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
altitude envelope and the +2/-2 step sizes) rather than silently hardcoding
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
stack (chassis, cannon, missile, phaser, nuke, electronics). CR003.3 (#218,
`_specs/milestones/cr003-playtest-fixes.md`) locks each module's extent to
the Spectrum's ``Ld7b4_piece_heights`` table: bipod ``11``, tracks ``7``, anti-grav ``8``,
cannon ``6``, missile ``6``, phaser ``7``, nuclear ``7``, electronics
``7``. The disassembly's header notes confirm the consequences: the
shortest robot is tracks + cannon = 13 and the tallest bipod + missile +
phaser + nuclear + electronics = 38, and weapon damage is
``(60 - (robot height + ground height)) // 4`` times the weapon multiplier
(so a phaser hit on a tracks + cannon robot at ground 0 deals 44). The
tallest robot on the highest walkable ground (38 + 6 = 44) stays below
``commander_max_altitude`` (48), so the ship can always rest on any robot.
These replace the earlier placeholder magnitudes (chassis 4, others 2).
Each module identity gets its own named field (rather than one dict-valued
field) to match this module's existing flat-scalar-field convention and
keep ``EngineRules`` trivially hashable/equatable.

Issue #34 (M4.3, `_specs/milestones/04-robots-construction-economy.md`,
`_specs/open-questions.md` §10 "Resource spending rules -- RESOLVED") adds
``starting_general_resources`` and the eight ``module_cost_*`` fields: the
canonical original-Spectrum construction economy. Unlike the "documented
placeholder, not independently verified" fields above
(``commander_vertical_update_ticks``, ``commander_height``), these nine values are RESOLVED and locked exactly by
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

Issue #60 (M5.1, `_specs/milestones/05-orders-navigation-capture.md`)
introduced the robot-movement timing fields that `movement.py`'s shared
movement executor consumes; issue #61 (M5.2) derived the ordinary-terrain
values from the disassembly.

CR001.4 (issue #151) replaces the earlier base-ticks x terrain-multiplier
shape with one integer field per enterable (chassis, terrain) pair,
``robot_move_ticks_<chassis>_<terrain>``, per `_specs/open-questions.md` §4
("Exact movement speeds and terrain penalties -- RESOLVED", owner decision
2026-09-21) and `_specs/technical-spec.md` §13. The values come straight
from ``netherearth-annotated.asm``'s ``Lb61d_robot_movement_speed_table``
(cycles per move; bipod/tracks/anti-grav: flat 6/4/3, rugged 8/6/3,
mountains 9/7/4) at 1 game cycle = 4 ticks
(``MIN_INTERRUPTS_PER_GAME_CYCLE: equ 10``, i.e. 5 cycles/s at the locked
20 Hz tick rate)::

                normal  rough  mountain  ditch
    bipod         24      32      -        -
    tracks        16      24     28        -
    anti-grav     12      12     16       12

Anti-grav's ditch speed equals its flat speed because ditch element types
have height 0 and the speed row is chosen by altitude (§4). The blocked
pairs (``-``) are chassis terrain *legality*, owned by `movement.py`'s
``CHASSIS_TERRAIN_PERMISSIONS``; they have no field here.

Issue #70 (M6.1, `_specs/milestones/06-combat-damage-victory.md`, "Locked
combat rules") adds the nine combat metadata fields: ``cannon_range_cells``,
``missile_range_cells``, ``phaser_range_cells``,
``electronics_range_bonus_cells``, ``nuclear_radius_cells`` (replaced by
per-kind blast-shape fields in CR001.2, `_specs/open-questions.md` §20),
``normal_projectile_altitude``, ``cannon_damage_multiplier``,
``missile_damage_multiplier``, ``phaser_damage_multiplier``. These are the
canonical locked Spectrum weapon range/effect defaults from the milestone
spec's "Canonical default ranges/effects" section, converted from miles to
cells via the shared ``miles_to_cells`` helper already defined in this
module. CR001 (#150, `_specs/open-questions.md` §8 resolution) superseded
the mile-derived weapon ranges (20/28/20 cells, +6 electronics) with the
Spectrum code values defined directly in cells: cannon 10, missile 14,
phaser 10, electronics +2. Projectile altitude and damage multipliers are explicitly
locked Spectrum defaults per the milestone spec. Together these form the
authoritative rule set for all later combat tasks.

Issue #73 (M6.4, `_specs/milestones/06-combat-damage-victory.md`) adds
``projectile_advance_ticks``: the cadence (in simulation ticks) at which
in-flight projectiles advance one cell, consumed by `combat.py`'s
``is_projectile_advance_tick``/``advance_projectiles``. This value is
evidence-backed: issue #72's disassembly research
(`_specs/open-questions.md` §8) found bullets and robots share the same
per-game-cycle dispatcher, and issue #61 (M5.2) already mapped that shared
game-cycle boundary onto this project's locked 20 Hz tick rate as "1 cycle
= 4 ticks" -- reused here unchanged, default ``4``.

(Issue #70's original placeholder field ``projectile_max_range_cells`` --
an as-yet-unresolved projectile-lifetime rule -- was removed as dead code
during the M6 final review: the final per-weapon range design
(`combat.weapon_range_cells()` plus the electronics range bonus) made it
obsolete before anything ever consumed it. Per-weapon range is what
actually gates projectile range/termination; see `combat.py`'s
:func:`~nether_earth.combat.weapon_range_cells` and
:func:`~nether_earth.combat.advance_projectiles`.)
"""

import hashlib
import json
from dataclasses import asdict, dataclass

__all__ = [
    "CELLS_PER_MILE",
    "DEFAULT_RULES",
    "RULES_VERSION",
    "EngineRules",
    "miles_to_cells",
    "rules_content_hash",
]

#: Version of the engine's gameplay rules, recorded in every replay artifact.
#: Bump it whenever a rule change (an ``EngineRules`` value or rule logic)
#: makes older replays non-reproducible. ``rules_content_hash`` catches
#: ``EngineRules`` value changes on its own; logic changes are caught only by
#: this bump.
RULES_VERSION = "cr003"


#: How many grid cells one in-game *mile* spans.
#:
#: `_specs/open-questions.md` §3 locks this as a resolved rule: "1 mile = 2
#: cells". Spec-facing order distances are stated in miles (``Advance 0-50
#: miles``) while every engine-facing distance is stated in cells, so the
#: conversion is needed by more than one subsystem. Weapon ranges are the
#: exception: since CR001 (§8) they are defined directly in cells from the
#: Spectrum code and do not go through this helper. §3 requires it to exist exactly once
#: in shared game-rule/helper code -- this module -- rather than being
#: re-spelled as a literal ``* 2`` at each call site. `orders.py`
#: re-exports :func:`miles_to_cells` for convenience, but this is its only
#: definition, so other subsystems can convert mile distances without
#: taking a dependency on the orders subsystem.
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
      descending/falling (default ``2``, owner deviation from the
      Spectrum's ``1``, CR003.1 #216 and `_specs/open-questions.md` §13).
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
    - ``commander_exit_elevate_updates``: number of vertical
      updates for which a commander automatically ascends
      (``commander_ascent_step`` each) after leaving the construction screen
      by EXIT MENU or START ROBOT (CR002.12/CR002.13) or leaving a robot it
      was docked on (CR002.24). Spectrum evidence: both
      ``Lcb8e_construction_screen_exit`` and the robot HUD's EXIT option
      (``#a7fd``--``#a80f``, falling through to ``La812_exit_robot``) set
      ``Lfd30_player_elevate_timer`` to 5, and
      ``Lafa2_player_ship_keyboard_control_altitude`` ascends 2 per update
      while the timer runs. Default ``5``.
    - ``module_height_bipod`` / ``module_height_tracks`` /
      ``module_height_anti_grav`` / ``module_height_cannon`` /
      ``module_height_missile`` / ``module_height_phaser`` /
      ``module_height_nuclear`` / ``module_height_electronics``: the
      physical vertical extent (altitude units) of each
      :class:`~nether_earth.robot_build.ModuleIdentity` module, used by
      `robot_stack.py` (issue #53) to derive a robot build's total physical
      height. Spectrum ``Ld7b4_piece_heights`` values (CR003.3): bipod
      ``11``, tracks ``7``, anti-grav ``8``, cannon ``6``, missile ``6``,
      phaser ``7``, nuclear ``7``, electronics ``7`` -- see the module
      docstring.
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
    - ``robot_move_ticks_<chassis>_<terrain>`` (nine fields, one per
      enterable pair): simulation ticks a robot with that chassis takes to
      move one cell into a cell of that terrain class. Locked,
      evidence-backed defaults per `_specs/open-questions.md` §4 (see the
      module docstring): bipod normal ``24`` / rough ``32``; tracks normal
      ``16`` / rough ``24`` / mountain ``28``; anti-grav normal ``12`` /
      rough ``12`` / mountain ``16`` / ditch ``12``. Bipod on mountain or
      ditch and tracks on ditch are blocked, not slow, so have no field.
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
      robot, in grid cells (issue #70, M6.1; value from CR001, #150).
      Spectrum code value: 10 cells (`_specs/open-questions.md` §8:
      ``Lb6d6_weapon_fire`` range counter 5 x 2 cells per bullet update).
      Defined directly in cells, not via :func:`miles_to_cells`.
    - ``missile_range_cells``: the maximum firing range of a
      missile-equipped robot, in grid cells. Spectrum code value: 14 cells
      (range counter 7 x 2 cells per update).
    - ``phaser_range_cells``: the maximum firing range of a
      phaser-equipped robot, in grid cells. Spectrum code value: 10 cells
      (range counter 5 x 2 cells per update).
    - ``electronics_range_bonus_cells``: the additional range granted by an
      electronics module when fitted, in grid cells. Spectrum code value:
      2 cells (range counter +1 x 2 cells per update). Exact electronics
      accuracy and resistance mechanics remain research-owned.
    - Nuclear blast shape (CR001.2, issue #149, `_specs/open-questions.md`
      §20, from `Lb99f_fire_nuclear_bomb`), replacing the former uniform
      ``nuclear_radius_cells``:

      - ``nuclear_robot_window_row_widths``: row widths, top to bottom, of
        the carrier-centred robot window; default ``(5, 7, 9, 9, 9, 9, 9, 7,
        5)`` (the 9x9 ``ld bc, #0909`` window with trimmed corner rows).
        Every robot inside it, of either side, is destroyed.
      - ``nuclear_building_dy_offset`` (``1``, the code's ``inc a``) and
        ``nuclear_war_base_extra_dy_offset`` (``4``, ``add a, 4``): added to
        the carrier's y before measuring dy to a building anchor.
      - ``nuclear_war_base_axis_limit`` / ``nuclear_war_base_sum_limit``
        (``7`` / ``10``, ``ld de, #070a``) and ``nuclear_factory_axis_limit``
        / ``nuclear_factory_sum_limit`` (``5`` / ``7``, ``ld de, #0507``):
        a building is in range when ``dx < axis``, ``dy < axis`` and
        ``dx + dy < sum`` (all exclusive). At most one building is destroyed.
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
    - ``projectile_advance_ticks``: the number of simulation ticks between
      projectile-advance cadence updates (issue #73, M6.4, per
      `_specs/open-questions.md` §8 "Advance cadence"). The disassembly
      evidence found by issue #72's research shows bullets and robots are
      driven from the same per-game-cycle dispatcher
      (`Lb0ca_update_robots_bullets_and_ai`), with the same outer
      game-cycle boundary already mapped by issue #61 (M5.2) onto this
      project's locked 20 Hz tick rate as "1 cycle = 200 ms = 4 ticks" --
      unlike a robot's per-cycle movement (which is additionally throttled
      by its own chassis/terrain-speed skip counter), a bullet has no such
      throttle and advances on every single game cycle. Reusing that same
      "1 cycle = 4 ticks" conversion factor here is therefore an
      evidence-backed default (``4``), not an independent placeholder.
    - ``projectile_cells_per_advance``: how many cells a projectile moves
      along its firing axis on each advance (CR001, #150, per
      `_specs/open-questions.md` §8 resolution: ``Lb724_bullet_update_internal``
      moves a bullet 2 map cells per update on either axis). Default ``2``.
    - ``robot_fire_cycle_ticks``: the length, in ticks, of the game cycle in
      which a robot may fire at most one normal weapon (CR002.2 #169, owner
      decision 2026-09-21, `_specs/open-questions.md` §8). Fire cycles are
      the aligned windows ``tick // robot_fire_cycle_ticks``. Default ``4``
      (1 game cycle). On the Spectrum an AI robot fires only inside its
      update in ``Lb0ca_update_robots_bullets_and_ai`` and a combat-mode
      shot uses up one time step, so neither fires twice in a cycle.
    - ``robot_launch_exit_steps``: how many steps south a newly launched
      robot walks out of its war base before settling into Stop & Defend
      (CR002.3, owner decision 2026-09-21, `_specs/open-questions.md` §21).
      ``La6c8``, right after ``Lc849_robot_construction_if_possible``, sets
      ``ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING`` to 5 ("walk 5 steps
      after exiting the base, and stop"). Default ``5``; ``0`` disables it.
    """

    commander_min_altitude: int = 0
    commander_max_altitude: int = 48
    commander_vertical_update_ticks: int = 4
    commander_ascent_step: int = 2
    commander_descent_step: int = 2
    commander_height: int = 4
    commander_horizontal_move_ticks: int = 4
    commander_exit_elevate_updates: int = 5
    module_height_bipod: int = 11
    module_height_tracks: int = 7
    module_height_anti_grav: int = 8
    module_height_cannon: int = 6
    module_height_missile: int = 6
    module_height_phaser: int = 7
    module_height_nuclear: int = 7
    module_height_electronics: int = 7
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
    robot_move_ticks_bipod_normal: int = 24
    robot_move_ticks_bipod_rough: int = 32
    robot_move_ticks_tracks_normal: int = 16
    robot_move_ticks_tracks_rough: int = 24
    robot_move_ticks_tracks_mountain: int = 28
    robot_move_ticks_anti_grav_normal: int = 12
    robot_move_ticks_anti_grav_rough: int = 12
    robot_move_ticks_anti_grav_mountain: int = 16
    robot_move_ticks_anti_grav_ditch: int = 12
    capture_duration_ticks: int = 1440
    cannon_range_cells: int = 10
    missile_range_cells: int = 14
    phaser_range_cells: int = 10
    electronics_range_bonus_cells: int = 2
    nuclear_robot_window_row_widths: tuple[int, ...] = (5, 7, 9, 9, 9, 9, 9, 7, 5)
    nuclear_building_dy_offset: int = 1
    nuclear_war_base_extra_dy_offset: int = 4
    nuclear_war_base_axis_limit: int = 7
    nuclear_war_base_sum_limit: int = 10
    nuclear_factory_axis_limit: int = 5
    nuclear_factory_sum_limit: int = 7
    normal_projectile_altitude: int = 10
    cannon_damage_multiplier: int = 2
    missile_damage_multiplier: int = 3
    phaser_damage_multiplier: int = 4
    projectile_advance_ticks: int = 4
    projectile_cells_per_advance: int = 2
    #: Ticks a 90-degree robot turn costs (owner request, 2026-09-23). A
    #: robot that wants to step in a direction it is not facing spends an
    #: update rotating instead of moving (`Lb471`), so the cost is one
    #: Spectrum game cycle -- the same 4 ticks as
    #: ``robot_fire_cycle_ticks``, which is that cycle's length.
    robot_turn_ticks: int = 4
    robot_fire_cycle_ticks: int = 4
    robot_launch_exit_steps: int = 5

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
        if self.commander_exit_elevate_updates < 0:
            raise ValueError("commander_exit_elevate_updates must be non-negative")
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
            "robot_move_ticks_bipod_normal",
            "robot_move_ticks_bipod_rough",
            "robot_move_ticks_tracks_normal",
            "robot_move_ticks_tracks_rough",
            "robot_move_ticks_tracks_mountain",
            "robot_move_ticks_anti_grav_normal",
            "robot_move_ticks_anti_grav_rough",
            "robot_move_ticks_anti_grav_mountain",
            "robot_move_ticks_anti_grav_ditch",
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
            "nuclear_war_base_axis_limit",
            "nuclear_war_base_sum_limit",
            "nuclear_factory_axis_limit",
            "nuclear_factory_sum_limit",
            "normal_projectile_altitude",
            "cannon_damage_multiplier",
            "missile_damage_multiplier",
            "phaser_damage_multiplier",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.projectile_advance_ticks <= 0:
            raise ValueError("projectile_advance_ticks must be a positive integer")
        widths = self.nuclear_robot_window_row_widths
        if len(widths) % 2 == 0 or any(width <= 0 or width % 2 == 0 for width in widths):
            raise ValueError(
                "nuclear_robot_window_row_widths must be an odd number of positive odd widths"
            )
        if self.nuclear_building_dy_offset < 0 or self.nuclear_war_base_extra_dy_offset < 0:
            raise ValueError("nuclear building dy offsets must be non-negative")
        if self.projectile_cells_per_advance <= 0:
            raise ValueError("projectile_cells_per_advance must be a positive integer")
        if self.robot_turn_ticks <= 0:
            raise ValueError("robot_turn_ticks must be a positive integer")
        if self.robot_fire_cycle_ticks <= 0:
            raise ValueError("robot_fire_cycle_ticks must be a positive integer")
        if self.robot_launch_exit_steps < 0:
            raise ValueError("robot_launch_exit_steps must be non-negative")


#: Canonical, Spectrum-compatible default rule set. Calling code should
#: reference this shared instance rather than constructing ad hoc
#: ``EngineRules()`` copies, so a single object identity represents "the
#: default rules" wherever it is passed around.
DEFAULT_RULES = EngineRules()


def rules_content_hash(rules: EngineRules = DEFAULT_RULES) -> str:
    """Return the SHA-256 hex digest of ``rules`` as canonical JSON.

    Canonical form: every field, keys sorted, no whitespace. Deterministic
    across processes and platforms, so a replay can record it and a verifier
    can compare it against the running engine's rules.
    """
    canonical = json.dumps(asdict(rules), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
