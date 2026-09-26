"""AI construction sub-planner (CR004.4, #285): what the AI builds, where and when.

The AI builds through the ordinary construction commands, never by creating a
robot directly. With no commander it cannot land on a heli-pad, so it opens a
session with :class:`~nether_earth.construction_commands.EnterConstructionRemotelyCommand`
(AI seats only, keyed on owning the war base), then issues the same
``SelectModuleCommand``\\ s and ``LaunchRobotCommand`` a human's construction
screen issues. All of them are validated and applied by ``engine.step``
(Step 8, in canonical order), so costs, legality, the robot cap and the exit
check are the engine's, not the planner's. A whole build goes out in one
batch; a session still open at the next decision (the launch was refused, e.g.
a robot stepped into the exit in the meantime) is cancelled, which has no
resource impact, and the planner decides afresh.

Build policy (improves on the Spectrum; see ``docs/cr004/spectrum-ai-notes.md``
§2-§3 for the verdicts it follows):

- **Purposeful, affordable loadout** (replaces the Spectrum's random design
  byte): every legal design is scored (:func:`design_value`) and the best one
  the current pool pays for, per the engine's own spend rule
  (:func:`pool_after`), is built. No design is rolled and then dropped.
- **Weapon ramp** (Spectrum ``Lb505``, taken and improved): a floor on the
  weapon count that rises with the army (:func:`min_weapons`), faster than the
  Spectrum's one step per 8 robots. Above the floor, more firepower wins
  whenever the pool affords it.
- **Defence reserve** (replaces the Spectrum's "at most half of general"
  cap): once the army is established, a build must leave enough general
  resources for one cheapest-possible defender (:func:`defence_reserve`).
  When an enemy robot closes on an owned war base (:func:`threatened_war_bases`)
  the reserve and the ramp are released and the best affordable robot is built
  at the threatened base.
- **Nuclear role** (Spectrum splits nuclear "destroy" robots from capturers):
  once the army is large enough and no nuclear robot is alive, the planner
  saves for and builds one (:func:`wants_nuclear`).
- **Every owned war base, skipping blocked exits** (Spectrum: coin flip per
  base, abort if the exit is blocked): owned war bases are tried round robin
  in map order (:func:`war_base_order`), and one whose exit the launch rule
  refuses (:func:`~nether_earth.robot_launch.resolve_launch_exit`) is skipped.

The AI reads only what a player in its seat can see: its own pool and robots,
structure ownership, and enemy robot positions (the radar shows every robot).
It never reads the opponent's resource pool.

The tunables below are AI strategy, not game rules, so they live here and not
in ``rules.py``: changing them changes how the AI plays, never what is legal,
and they must not enter ``rules_content_hash()`` (which pins replays to the
rule set). Every value that *is* a rule (costs, ranges, the robot cap) is read
from :class:`~nether_earth.rules.EngineRules`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from itertools import combinations

from nether_earth.commands import Command
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    EnterConstructionRemotelyCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.construction_economy import ResourcePool, module_cost, spend_module
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.rng import MatchRandom
from nether_earth.robot_build import CANONICAL_WEAPON_ORDER, ModuleIdentity
from nether_earth.robot_launch import LaunchRejectionReason, resolve_launch_exit
from nether_earth.rules import EngineRules
from nether_earth.state import AiConstructionMemory, AiMemory, GameState

__all__ = [
    "CHASSIS_VALUE",
    "DESIGNS",
    "ELECTRONICS_VALUE",
    "NUCLEAR_MIN_ARMY",
    "RESERVE_MIN_ARMY",
    "WEAPON_RAMP_ROBOTS_PER_STEP",
    "WEAPON_VALUE",
    "Design",
    "choose_design",
    "defence_reserve",
    "design_cost",
    "design_value",
    "min_weapons",
    "owned_war_bases",
    "plan",
    "pool_after",
    "threat_radius_cells",
    "threatened_war_bases",
    "wants_nuclear",
    "war_base_order",
]

# --- Tunables (AI strategy; see the module docstring) -----------------------

#: The weapon floor rises by one for every this many robots the AI owns:
#: robots 1-4 need one weapon, 5-8 two, 9 on three. (Spectrum: 8.)
WEAPON_RAMP_ROBOTS_PER_STEP = 4

#: The defence reserve applies once the AI owns this many robots. Before
#: that, spending everything on the opening robots grabs factories sooner.
RESERVE_MIN_ARMY = 2

#: The AI saves for a nuclear robot once it owns this many robots and none
#: of them carries a nuke.
NUCLEAR_MIN_ARMY = 6

#: Combat value per weapon. Phaser hits hardest (x4 damage); missile hits x3
#: but outranges the others (14 cells vs 10), which the AI rates as equal;
#: cannon is the cheap x2 filler. Nuclear adds no combat value: it is chosen
#: by role (:func:`wants_nuclear`), never for a fight.
WEAPON_VALUE: dict[ModuleIdentity, int] = {
    ModuleIdentity.CANNON: 2,
    ModuleIdentity.MISSILE: 4,
    ModuleIdentity.PHASER: 4,
    ModuleIdentity.NUCLEAR: 0,
}

#: Mobility value per chassis. Tracks also cross mountains, anti-grav crosses
#: everything and is fastest; bipod is slow and road-bound.
CHASSIS_VALUE: dict[ModuleIdentity, int] = {
    ModuleIdentity.BIPOD: 0,
    ModuleIdentity.TRACKS: 2,
    ModuleIdentity.ANTI_GRAV: 3,
}

#: Value of electronics (+2 cells of range on every weapon).
ELECTRONICS_VALUE = 2

# --- Designs -----------------------------------------------------------------

#: A robot design as the modules to select, in selection order: chassis,
#: weapons (canonical stack order), then electronics if fitted.
Design = tuple[ModuleIdentity, ...]


def _all_designs() -> tuple[Design, ...]:
    chassis_order = (ModuleIdentity.BIPOD, ModuleIdentity.TRACKS, ModuleIdentity.ANTI_GRAV)
    designs: list[Design] = []
    for chassis in chassis_order:
        for count in (1, 2, 3):
            for weapons in combinations(CANONICAL_WEAPON_ORDER, count):
                designs.append((chassis, *weapons))
                designs.append((chassis, *weapons, ModuleIdentity.ELECTRONICS))
    return tuple(designs)


#: Every legal build (3 chassis x 14 weapon sets x electronics or not), in a
#: fixed canonical order that also breaks scoring ties.
DESIGNS: tuple[Design, ...] = _all_designs()


def _weapons(design: Design) -> tuple[ModuleIdentity, ...]:
    return tuple(module for module in design if module in WEAPON_VALUE)


def design_value(design: Design) -> int:
    """Return ``design``'s combat value: weapons plus chassis plus electronics."""
    value = sum(WEAPON_VALUE[weapon] for weapon in _weapons(design))
    value += CHASSIS_VALUE[design[0]]
    if ModuleIdentity.ELECTRONICS in design:
        value += ELECTRONICS_VALUE
    return value


def design_cost(design: Design, rules: EngineRules) -> int:
    """Return ``design``'s total module cost under ``rules``."""
    return sum(module_cost(module, rules) for module in design)


def pool_after(pool: ResourcePool, design: Design, rules: EngineRules) -> ResourcePool | None:
    """Return ``pool`` after paying for ``design`` module by module, or ``None`` if unaffordable.

    Uses the engine's own spend rule (own category first, shortfall from
    general), so a design this accepts is one the construction session will
    accept too.
    """
    for module in design:
        result = spend_module(pool, module, rules)
        if result.pool is None:
            return None
        pool = result.pool
    return pool


# --- Policy ------------------------------------------------------------------


def min_weapons(robot_count: int) -> int:
    """Return the weapon floor for the AI's next robot when it already owns ``robot_count``."""
    return min(3, 1 + robot_count // WEAPON_RAMP_ROBOTS_PER_STEP)


def defence_reserve(robot_count: int, rules: EngineRules) -> int:
    """Return the general resources a routine build must leave untouched.

    The price of the cheapest legal robot (one chassis, one weapon), so one
    defender can always be built when a threat appears; zero while the army
    is smaller than :data:`RESERVE_MIN_ARMY`.
    """
    if robot_count < RESERVE_MIN_ARMY:
        return 0
    return min(design_cost(design, rules) for design in DESIGNS)


def wants_nuclear(robot_count: int, nuclear_count: int) -> bool:
    """Return whether the AI's next robot should be a nuclear one."""
    return robot_count >= NUCLEAR_MIN_ARMY and nuclear_count == 0


def choose_design(
    pool: ResourcePool,
    robot_count: int,
    nuclear_count: int,
    threatened: bool,
    rules: EngineRules,
) -> Design | None:
    """Return the design to build now, or ``None`` to wait for resources.

    Under threat: the best affordable non-nuclear robot, with no reserve and
    no weapon floor, since any defender now beats a better one later.
    Otherwise, when :func:`wants_nuclear`: the best affordable nuclear design
    that leaves the reserve, or ``None`` (save for it). Otherwise: the best
    affordable non-nuclear design with at least :func:`min_weapons` weapons
    that leaves the reserve. "Best" is the highest :func:`design_value`, then
    the lowest cost, then :data:`DESIGNS` order.
    """
    if threatened:
        floor, reserve, nuclear = 1, 0, False
    else:
        floor = min_weapons(robot_count)
        reserve = defence_reserve(robot_count, rules)
        nuclear = wants_nuclear(robot_count, nuclear_count)
        if nuclear:
            floor = 1
    best: Design | None = None
    best_key: tuple[int, int] | None = None
    for design in DESIGNS:
        weapons = _weapons(design)
        if (ModuleIdentity.NUCLEAR in weapons) != nuclear or len(weapons) < floor:
            continue
        remaining = pool_after(pool, design, rules)
        if remaining is None or remaining.general < reserve:
            continue
        key = (design_value(design), -design_cost(design, rules))
        if best_key is None or key > best_key:
            best, best_key = design, key
    return best


def owned_war_bases(world: WorldMap, player: PlayerId) -> tuple[EntityId, ...]:
    """Return the ids of the war bases ``player`` owns in ``world``, in map order."""
    return tuple(base.id for base in world.war_bases if base.owner == player)


def threat_radius_cells(rules: EngineRules) -> int:
    """Return how close an enemy robot must be to a war base to count as a threat.

    The longest weapon range plus the electronics bonus: an enemy that close
    can fire on a robot leaving the base.
    """
    longest = max(rules.cannon_range_cells, rules.missile_range_cells, rules.phaser_range_cells)
    return longest + rules.electronics_range_bonus_cells


def threatened_war_bases(
    state: GameState, world: WorldMap, player: PlayerId, rules: EngineRules
) -> tuple[EntityId, ...]:
    """Return ``player``'s war bases with an enemy robot within :func:`threat_radius_cells`.

    Distance is Chebyshev, from the robot's anchor cell to the nearest cell of
    the war base. Map order.
    """
    radius = threat_radius_cells(rules)
    enemies = [robot for robot in state.robots if robot.owner != player]
    threatened: list[EntityId] = []
    for base in world.war_bases:
        if base.owner != player:
            continue
        if any(
            max(abs(robot.x - cell.x), abs(robot.y - cell.y)) <= radius
            for robot in enemies
            for cell in base.components
        ):
            threatened.append(base.id)
    return tuple(threatened)


def war_base_order(
    owned: tuple[EntityId, ...],
    last: EntityId | None,
    threatened: Iterable[EntityId] = (),
) -> tuple[EntityId, ...]:
    """Return ``owned`` in the order to try building at.

    Threatened bases first (map order), then the rest round robin: starting
    just after ``last`` (the base built at last time), wrapping around.
    """
    threatened_set = set(threatened)
    start = owned.index(last) + 1 if last in owned else 0
    rotated = owned[start:] + owned[:start]
    return tuple(base for base in owned if base in threatened_set) + tuple(
        base for base in rotated if base not in threatened_set
    )


def plan(
    state: GameState,
    memory: AiMemory,
    world: WorldMap,
    rules: EngineRules,
    random: MatchRandom,
) -> tuple[tuple[Command, ...], AiMemory]:
    """Return this seat's construction commands and updated memory.

    Deterministic; ``random`` is not used. Sequence numbers are placeholders
    (the engine hook numbers the commands); order is what matters.
    """
    del random
    player = memory.player_id
    commands: list[Command] = []
    if state.construction_session_for(player) is not None:
        # A session left over from a refused launch (or at a base since
        # captured or destroyed): drop it, no resource impact, and replan.
        commands.append(CancelConstructionCommand(player=player, sequence=0))

    robots = state.robots_for(player)
    if len(robots) >= rules.max_robots_per_player:
        return tuple(commands), memory
    owned = owned_war_bases(world, player)
    if not owned:
        return tuple(commands), memory

    actual = state.resource_pool_for(player) or PlayerResourcePool(player_id=player)
    threatened = threatened_war_bases(state, world, player, rules)
    nuclear_count = sum(1 for robot in robots if ModuleIdentity.NUCLEAR in robot.build.weapons)
    design = choose_design(
        actual.to_resource_pool(), len(robots), nuclear_count, bool(threatened), rules
    )
    if design is None:
        return tuple(commands), memory

    for base in war_base_order(owned, memory.construction.last_war_base_id, threatened):
        if isinstance(resolve_launch_exit(world, state, base), LaunchRejectionReason):
            continue
        commands.append(EnterConstructionRemotelyCommand(player=player, sequence=0, war_base_id=base))
        commands.extend(SelectModuleCommand(player=player, sequence=0, module=m) for m in design)
        commands.append(LaunchRobotCommand(player=player, sequence=0))
        memory = dataclasses.replace(
            memory, construction=AiConstructionMemory(last_war_base_id=base)
        )
        break
    return tuple(commands), memory
