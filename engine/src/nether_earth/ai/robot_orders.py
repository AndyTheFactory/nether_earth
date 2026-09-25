"""AI robot-order sub-planner (CR004.5, #286).

Decides which order each of the AI seat's robots holds and when to change it.
It never picks a structure for a robot: the engine's own order semantics
(CR003.2, `orders.py`) do that -- orders persist, a Search & Capture retargets
after each capture, and capture targets are exclusive between same-owner
robots holding the same order. The planner *predicts* what the engine will pick
by calling the same public selection functions (:func:`select_capture_target`,
:func:`select_destroy_target`) and decides from those predictions which order
type each robot gets. Every decision is issued as an ordinary
:class:`SetRobotOrderCommand`.

One decision pass, in priority order:

1. **Defence.** An enemy robot inside :data:`THREAT_RADIUS_CELLS` of an owned
   war base's or factory's capture cell, and closing on it (nearer than at the
   last decision, or already within :data:`HOLD_RADIUS_CELLS`), is a threat.
   Each threat gets one defender, chosen by the weapon matchup against that
   intruder. A defender stays assigned until its intruder dies or leaves the
   threat radius. Standing assignments are the memory's ``defences``.
2. **Destroyers.** A robot carrying a nuclear module takes Search & Destroy
   against a war base or factory, but only when the engine would send it to an
   opponent-owned one. It is never sent to a neutral structure. This is the
   Spectrum's role split (`Lb95d`), ranked by value rather than a coin flip.
3. **Capture.** Every other robot that needs an order is allocated greedily,
   by value-per-distance score, to one of the three Search & Capture types.
   Neutral war bases count, since they decide victory, where the Spectrum
   ignored them. A type gets no more robots than it has unclaimed targets.
4. **Hunt.** An armed robot left without a capture target hunts enemy robots
   (Search & Destroy robots) when its matchup against the one the engine would
   chase is not unfavourable. This is the Spectrum's no-target fallback
   (`Lb289`), issued as a planner command and gated by composition.

Stability: an order is re-issued only when the desired order differs in kind
(:func:`same_order`). A robot productively holding a capture or destroy order
keeps it, so the planner re-plans on events (a target lost or gone, a threat,
a threat over), not every decision tick.

Visibility: the planner reads robot positions and builds, structure ownership,
and its own robots' orders -- all shown to a player in its seat. It never
reads the opponent's resources, orders, construction or commander.

The constants below are AI policy tuning, not gameplay rules. They change what
the AI chooses to order, never what an order does, so they live here rather
than in :class:`~nether_earth.rules.EngineRules`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nether_earth.capture import capture_footprint, effective_owner
from nether_earth.combat import calculate_weapon_damage, weapon_range_cells
from nether_earth.commands import Command
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.orders import (
    MAX_ORDER_DISTANCE_MILES,
    Advance,
    Order,
    Retreat,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
    _claimed_structures,
    select_capture_target,
    select_destroy_target,
)
from nether_earth.rng import MatchRandom
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import CELLS_PER_MILE, EngineRules
from nether_earth.state import (
    AiDefenceAssignment,
    AiMemory,
    AiOrderMemory,
    AiSighting,
    GameState,
)
from nether_earth.structures import Factory, FactoryType, WarBase

__all__ = [
    "CONTEST_RADIUS_CELLS",
    "HOLD_RADIUS_CELLS",
    "RECALL_RADIUS_CELLS",
    "THREAT_RADIUS_CELLS",
    "approach_order",
    "capture_score",
    "destroy_value",
    "hit_damage",
    "is_closing",
    "matchup",
    "plan",
    "same_order",
    "structure_value",
    "weapon_reach",
]

# --------------------------------------------------------------------------
# Policy tuning (AI choices, not gameplay rules)
# --------------------------------------------------------------------------

#: An enemy robot this close (Manhattan cells) to an owned structure's capture
#: cell, and closing, is a threat. Missile reach (14) plus a margin, so a
#: defender is sent before the intruder can fire on the capture cell.
THREAT_RADIUS_CELLS = 24
#: An enemy this close to an owned capture cell is a threat even when it is
#: not closing: it is standing on the capture cell or next to it.
HOLD_RADIUS_CELLS = 4
#: Only robots this close to a threatened capture cell are recalled to defend.
RECALL_RADIUS_CELLS = 80
#: Enemy robots this close to a capture target that out-match the robot make
#: the target contested.
CONTEST_RADIUS_CELLS = 16
#: Extra value of a war base for the victory rule (`victory.py` counts them).
VICTORY_VALUE = 10
#: General resources pay for any module (`construction_economy.py`), while a
#: factory's resource pays only for its own category.
GENERAL_RESOURCE_WEIGHT = 2
#: Every build needs exactly one chassis (`robot_build.py`), so chassis
#: resources are always spent. Other categories count once.
CHASSIS_RESOURCE_WEIGHT = 2
#: Added to every distance so a target on the robot's own cell does not
#: divide by zero and near targets are not infinitely preferred.
DISTANCE_OFFSET_CELLS = 10
#: Integer score scale; keeps the score in integers.
SCORE_SCALE = 1000

_NORMAL_WEAPONS = (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER)
_CAPTURE_TYPES = (
    SearchCaptureTarget.NEUTRAL_FACTORY,
    SearchCaptureTarget.ENEMY_FACTORY,
    SearchCaptureTarget.ENEMY_WAR_BASE,
)
_DESTROY_STRUCTURE_TYPES = (SearchDestroyTarget.WAR_BASE, SearchDestroyTarget.FACTORY)


# --------------------------------------------------------------------------
# Heuristics: small pure functions
# --------------------------------------------------------------------------


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def weapon_reach(robot: Robot, rules: EngineRules) -> int:
    """Return ``robot``'s longest normal-weapon range in cells, 0 when it has none.

    Electronics adds its bonus, as `combat.apply_fire` does. Nuclear is not a
    ranged weapon and is never fired at robots autonomously.
    """
    ranges = [weapon_range_cells(w, rules) for w in robot.build.weapons if w in _NORMAL_WEAPONS]
    if not ranges:
        return 0
    bonus = rules.electronics_range_bonus_cells if robot.build.electronics is not None else 0
    return max(ranges) + bonus


def hit_damage(attacker: Robot, target: Robot, rules: EngineRules) -> int:
    """Return the best per-hit damage ``attacker`` deals ``target`` on flat ground.

    Height is the defensive stat: a taller robot takes less damage
    (`combat.calculate_base_damage`), and one whose top is below the bullet
    altitude is flown over on flat ground (`combat._robot_hit_at`), so it
    takes none. Flat ground is used because it is the matchup of the two
    designs, not of the terrain they happen to stand on this tick.
    """
    if target.height < rules.normal_projectile_altitude:
        return 0
    damages = [
        calculate_weapon_damage(w, target.height, 0, rules)
        for w in attacker.build.weapons
        if w in _NORMAL_WEAPONS
    ]
    return max(damages, default=0)


def matchup(own: Robot, enemy: Robot, rules: EngineRules) -> int:
    """Return +1 when ``own`` beats ``enemy``, -1 when it loses, 0 when even.

    The composition response (CR004 improvement 3): reach decides who fires
    first, then per-hit damage (which already folds in both heights). There is
    no armour stat. A robot that cannot hurt the enemy always loses; one the
    enemy cannot hurt always wins.
    """
    dealt = hit_damage(own, enemy, rules)
    taken = hit_damage(enemy, own, rules)
    if dealt == 0:
        return -1
    if taken == 0:
        return 1
    own_reach, enemy_reach = weapon_reach(own, rules), weapon_reach(enemy, rules)
    if own_reach != enemy_reach:
        return 1 if own_reach > enemy_reach else -1
    return (dealt > taken) - (dealt < taken)


def structure_value(
    structure: WarBase | Factory, owner: PlayerId | None, player: PlayerId, rules: EngineRules
) -> int:
    """Return what capturing ``structure`` is worth to ``player``.

    Daily production, weighted by how freely the resource spends, plus
    :data:`VICTORY_VALUE` for a war base. Taking an opponent's structure is
    worth double, since the opponent loses what ``player`` gains. A structure
    ``player`` already owns is worth nothing.
    """
    if owner == player:
        return 0
    base = destroy_value(structure, rules)
    return base if owner is None else 2 * base


def destroy_value(structure: WarBase | Factory, rules: EngineRules) -> int:
    """Return what denying ``structure`` to its owner is worth (one side of a capture)."""
    if isinstance(structure, WarBase):
        return rules.war_base_production_amount * GENERAL_RESOURCE_WEIGHT + VICTORY_VALUE
    weight = CHASSIS_RESOURCE_WEIGHT if structure.factory_type is FactoryType.CHASSIS else 1
    return rules.factory_production_amount * weight


def capture_score(value: int, distance: int, contesters: int) -> int:
    """Return an integer score: value per distance, divided among contesting enemies."""
    return value * SCORE_SCALE // ((distance + DISTANCE_OFFSET_CELLS) * (1 + contesters))


def is_closing(distance: int, previous: int | None) -> bool:
    """Whether an enemy at ``distance`` is closing on an owned structure.

    ``previous`` is its distance at the last decision, ``None`` when it was not
    seen then (it has just entered, so it is closing). An enemy within
    :data:`HOLD_RADIUS_CELLS` counts whether or not it moved.
    """
    if distance > THREAT_RADIUS_CELLS:
        return False
    return distance <= HOLD_RADIUS_CELLS or previous is None or distance < previous


def approach_order(robot_x: int, goal_x: int) -> Order:
    """Return the order that moves a robot to ``goal_x``'s column, then holds there.

    Advance/Retreat end in Stop & Defend (`orders.py`), which engages the
    nearest enemy robot in range. Within :data:`HOLD_RADIUS_CELLS` the robot
    just holds. Distances are capped at the order's 50-mile limit.
    """
    delta = goal_x - robot_x
    if abs(delta) <= HOLD_RADIUS_CELLS:
        return StopAndDefend()
    miles = min(MAX_ORDER_DISTANCE_MILES, -(-abs(delta) // CELLS_PER_MILE))
    return Advance(miles) if delta > 0 else Retreat(miles)


def same_order(current: Order | None, desired: Order) -> bool:
    """Whether ``current`` already is ``desired``, ignoring engine-bound fields.

    Engine-bound state (a capture's stored target, an advance's bound column,
    its distance) is not the planner's choice, so a difference there is no
    reason to re-issue.
    """
    if current is None:
        return False
    if isinstance(desired, SearchCapture):
        return isinstance(current, SearchCapture) and current.target is desired.target
    if isinstance(desired, SearchDestroy):
        return isinstance(current, SearchDestroy) and current.target is desired.target
    return type(current) is type(desired)


# --------------------------------------------------------------------------
# Situation reading
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Site:
    """An owned structure and the cells an enemy must stand on to capture it."""

    structure: WarBase | Factory
    cells: tuple[tuple[int, int], ...]

    @property
    def rank(self) -> tuple[int, str]:
        """War bases first (they decide victory), then by id."""
        return (0 if isinstance(self.structure, WarBase) else 1, self.structure.id.value)


def _footprint(world: WorldMap, structure: WarBase | Factory) -> tuple[tuple[int, int], ...]:
    kind = (
        InteractionKind.WARBASE_CAPTURE
        if isinstance(structure, WarBase)
        else InteractionKind.FACTORY_CAPTURE
    )
    return tuple(sorted(capture_footprint(world, structure.id, kind)))


def _structures(world: WorldMap) -> tuple[WarBase | Factory, ...]:
    return (*world.war_bases, *world.factories)


def _owned_sites(state: GameState, world: WorldMap, player: PlayerId) -> tuple[_Site, ...]:
    sites = [
        _Site(structure, _footprint(world, structure))
        for structure in _structures(world)
        if effective_owner(world, state, structure) == player
    ]
    return tuple(sorted((site for site in sites if site.cells), key=lambda site: site.rank))


def _site_distance(robot: Robot, site: _Site) -> int:
    return min(_manhattan((robot.x, robot.y), cell) for cell in site.cells)


def _nearest_site(robot: Robot, sites: tuple[_Site, ...]) -> tuple[int, _Site] | None:
    best: tuple[int, _Site] | None = None
    for site in sites:
        distance = _site_distance(robot, site)
        if best is None or (distance, site.rank) < (best[0], best[1].rank):
            best = (distance, site)
    return best


def _structure(world: WorldMap, structure_id: EntityId) -> WarBase | Factory | None:
    for structure in _structures(world):
        if structure.id == structure_id:
            return structure
    return None


def _has_normal_weapon(robot: Robot) -> bool:
    return any(weapon in _NORMAL_WEAPONS for weapon in robot.build.weapons)


def _mid_capture(robot: Robot, state: GameState, world: WorldMap) -> bool:
    """Whether ``robot`` stands on its Search & Capture target's capture cell.

    Moving it off would reset the capture (`open-questions.md` §7).
    """
    order = robot.order
    if not isinstance(order, SearchCapture) or order.structure_id is None:
        return False
    structure = _structure(world, order.structure_id)
    if structure is None or effective_owner(world, state, structure) == robot.owner:
        return False
    return (robot.x, robot.y) in _footprint(world, structure)


def _contesters(
    robot: Robot, cell: tuple[int, int], enemies: tuple[Robot, ...], rules: EngineRules
) -> int:
    return sum(
        1
        for enemy in enemies
        if _manhattan((enemy.x, enemy.y), cell) <= CONTEST_RADIUS_CELLS
        and matchup(robot, enemy, rules) < 0
    )


# --------------------------------------------------------------------------
# Planning passes
# --------------------------------------------------------------------------


@dataclass
class _Plan:
    """Mutable scratch for one decision pass (never stored)."""

    desired: dict[EntityId, Order]
    committed: set[EntityId]


def _defence_order(
    defender: Robot,
    intruder: Robot,
    site: _Site,
    standing: AiDefenceAssignment | None,
    state: GameState,
    world: WorldMap,
) -> Order:
    """Hunt the intruder when the engine would chase it, else move to the site.

    Hysteresis for a ``standing`` assignment, so a defence never churns:

    - a defender already hunting robots keeps hunting while it is assigned,
      even if the nearest enemy alternates between the intruder and another;
    - an approach is issued once per assignment (``standing.approached``). Once issued,
      the defender keeps whatever the engine made of it: still walking,
      arrived, or fallen back to Stop & Defend because the goal cell proved
      unreachable. Re-issuing it would fall back again the same tick.
    """
    hunting = isinstance(defender.order, SearchDestroy) and (
        defender.order.target is SearchDestroyTarget.ROBOT
    )
    if standing is not None and hunting and defender.order is not None:
        return defender.order
    chased = select_destroy_target(defender, SearchDestroyTarget.ROBOT, state, world)
    if chased is not None and chased[0] == intruder.entity_id:
        return SearchDestroy(SearchDestroyTarget.ROBOT)
    if standing is not None and standing.approached:
        return defender.order if defender.order is not None else StopAndDefend()
    goal = min(site.cells, key=lambda cell: (_manhattan((defender.x, defender.y), cell), cell))
    return approach_order(defender.x, goal[0])


def _is_approach(order: Order) -> bool:
    return isinstance(order, (Advance, Retreat))


def _pick_defender(
    intruder: Robot,
    site: _Site,
    candidates: list[Robot],
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> Robot | None:
    """Choose the defender for one threat, or ``None``.

    Prefers a robot the engine would already chase the intruder with, then a
    favourable matchup, then distance, then id. A robot that would lose the
    matchup is sent only to save a war base.
    """
    ranked: list[tuple[tuple[int, int, int, str], Robot]] = []
    for robot in candidates:
        if _site_distance(robot, site) > RECALL_RADIUS_CELLS:
            continue
        edge = matchup(robot, intruder, rules)
        if edge < 0 and not isinstance(site.structure, WarBase):
            continue
        chased = select_destroy_target(robot, SearchDestroyTarget.ROBOT, state, world)
        key = (
            0 if chased is not None and chased[0] == intruder.entity_id else 1,
            0 if edge >= 0 else 1,
            _manhattan((robot.x, robot.y), (intruder.x, intruder.y)),
            robot.entity_id.value,
        )
        ranked.append((key, robot))
    return min(ranked, key=lambda entry: entry[0])[1] if ranked else None


def _plan_defence(
    own: tuple[Robot, ...],
    enemies: tuple[Robot, ...],
    sites: tuple[_Site, ...],
    memory: AiOrderMemory,
    scratch: _Plan,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> tuple[tuple[AiDefenceAssignment, ...], tuple[AiSighting, ...]]:
    previous = {entry.robot_id: entry.distance for entry in memory.sightings}
    own_by_id = {robot.entity_id: robot for robot in own}
    enemy_by_id = {robot.entity_id: robot for robot in enemies}
    site_by_id = {site.structure.id: site for site in sites}

    sightings: list[AiSighting] = []
    threats: list[tuple[tuple[int, int, str], Robot, _Site]] = []
    for enemy in enemies:
        nearest = _nearest_site(enemy, sites)
        if nearest is None:
            continue
        distance, nearest_site = nearest
        sightings.append(AiSighting(enemy.entity_id, distance))
        if is_closing(distance, previous.get(enemy.entity_id)):
            threats.append(
                ((nearest_site.rank[0], distance, enemy.entity_id.value), enemy, nearest_site)
            )

    # Standing assignments hold while the intruder is alive and still near the site.
    kept: list[AiDefenceAssignment] = []
    for entry in memory.defences:
        defender = own_by_id.get(entry.defender_id)
        intruder = enemy_by_id.get(entry.intruder_id)
        site = site_by_id.get(entry.structure_id)
        if (
            defender is None
            or intruder is None
            or site is None
            or _site_distance(intruder, site) > THREAT_RADIUS_CELLS
        ):
            continue
        order = _defence_order(defender, intruder, site, entry, state, world)
        kept.append(replace(entry, approached=entry.approached or _is_approach(order)))
        scratch.committed.add(defender.entity_id)
        scratch.desired[defender.entity_id] = order

    defended = {entry.intruder_id for entry in kept}
    for _key, intruder, threatened in sorted(threats, key=lambda threat: threat[0]):
        if intruder.entity_id in defended:
            continue
        candidates = [
            robot
            for robot in own
            if robot.entity_id not in scratch.committed
            and _has_normal_weapon(robot)
            and not _mid_capture(robot, state, world)
        ]
        defender = _pick_defender(intruder, threatened, candidates, state, world, rules)
        if defender is None:
            continue
        defended.add(intruder.entity_id)
        order = _defence_order(defender, intruder, threatened, None, state, world)
        kept.append(
            AiDefenceAssignment(
                defender.entity_id,
                intruder.entity_id,
                threatened.structure.id,
                approached=_is_approach(order),
            )
        )
        scratch.committed.add(defender.entity_id)
        scratch.desired[defender.entity_id] = order

    return (
        tuple(sorted(kept, key=lambda entry: entry.defender_id.value)),
        tuple(sightings),
    )


def _destroy_option(
    robot: Robot,
    target: SearchDestroyTarget,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> tuple[int, EntityId] | None:
    """Score Search & Destroy ``target`` for a nuclear carrier, or ``None``.

    Only an opponent-owned structure is worth a nuke: the engine would also
    send it to a neutral one, which the AI would rather capture.
    """
    chosen = select_destroy_target(robot, target, state, world)
    if chosen is None:
        return None
    structure = _structure(world, chosen[0])
    if structure is None:
        return None
    owner = effective_owner(world, state, structure)
    if owner is None or owner == robot.owner:
        return None
    distance = _manhattan((robot.x, robot.y), chosen[1])
    return capture_score(destroy_value(structure, rules), distance, 0), structure.id


def _plan_destroyers(
    own: tuple[Robot, ...],
    scratch: _Plan,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> set[EntityId]:
    """Give nuclear carriers destroy orders; return the structures now targeted."""
    carriers = [
        robot
        for robot in own
        if robot.entity_id not in scratch.committed
        and ModuleIdentity.NUCLEAR in robot.build.weapons
    ]
    options: list[tuple[int, int, str, int, Robot, SearchDestroyTarget, EntityId]] = []
    for robot in carriers:
        for index, target in enumerate(_DESTROY_STRUCTURE_TYPES):
            option = _destroy_option(robot, target, state, world, rules)
            if option is None:
                continue
            score, structure_id = option
            # An incumbent keeps its order ahead of any newcomer.
            incumbent = isinstance(robot.order, SearchDestroy) and robot.order.target is target
            rank = (0 if incumbent else 1, -score, robot.entity_id.value, index)
            options.append((*rank, robot, target, structure_id))
    targeted: set[EntityId] = set()
    for *_key, robot, target, structure_id in sorted(options, key=lambda option: option[:4]):
        if robot.entity_id in scratch.committed or structure_id in targeted:
            continue
        targeted.add(structure_id)
        scratch.committed.add(robot.entity_id)
        scratch.desired[robot.entity_id] = SearchDestroy(target)
    return targeted


def _plan_captures(
    own: tuple[Robot, ...],
    enemies: tuple[Robot, ...],
    nuked: set[EntityId],
    scratch: _Plan,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> None:
    """Keep productive capture orders, then allocate free robots by score.

    A capture order is productive while the engine still finds it a target.
    Its claim counts toward that type's exclusivity, as in the engine
    (`orders._claimed_structures`).
    """
    claimed: dict[SearchCaptureTarget, set[EntityId]] = {kind: set() for kind in _CAPTURE_TYPES}
    free: list[Robot] = []
    for robot in own:
        if robot.entity_id in scratch.committed:
            continue
        order = robot.order
        if isinstance(order, SearchCapture):
            exclude = _claimed_structures(robot, order, state)
            chosen = select_capture_target(robot, order.target, state, world, exclude=exclude)
            if chosen is not None or _mid_capture(robot, state, world):
                scratch.committed.add(robot.entity_id)
                scratch.desired[robot.entity_id] = SearchCapture(order.target)
                if order.structure_id is not None:
                    claimed[order.target].add(order.structure_id)
                elif chosen is not None:
                    claimed[order.target].add(chosen[0])
                continue
        free.append(robot)

    # Greedy: the best-scoring (robot, capture type) pair first, then repeat.
    cache: dict[tuple[EntityId, SearchCaptureTarget], tuple[int, EntityId] | None] = {}

    def option(robot: Robot, kind: SearchCaptureTarget) -> tuple[int, EntityId] | None:
        key = (robot.entity_id, kind)
        if key not in cache:
            chosen = select_capture_target(
                robot, kind, state, world, exclude=frozenset(claimed[kind])
            )
            cache[key] = None
            if chosen is not None and chosen[0] not in nuked:
                structure = _structure(world, chosen[0])
                if structure is not None:
                    value = structure_value(
                        structure, effective_owner(world, state, structure), robot.owner, rules
                    )
                    distance = _manhattan((robot.x, robot.y), chosen[1])
                    contesters = _contesters(robot, chosen[1], enemies, rules)
                    cache[key] = (capture_score(value, distance, contesters), chosen[0])
        return cache[key]

    while free:
        best: tuple[tuple[int, str, int], Robot, SearchCaptureTarget, EntityId] | None = None
        for robot in free:
            for index, kind in enumerate(_CAPTURE_TYPES):
                scored = option(robot, kind)
                if scored is None or scored[0] <= 0:
                    continue
                rank = (-scored[0], robot.entity_id.value, index)
                if best is None or rank < best[0]:
                    best = (rank, robot, kind, scored[1])
        if best is None:
            return
        _rank, robot, kind, structure_id = best
        free.remove(robot)
        claimed[kind].add(structure_id)
        scratch.committed.add(robot.entity_id)
        scratch.desired[robot.entity_id] = SearchCapture(kind)
        for key, cached in list(cache.items()):
            if key[1] is kind and cached is not None and cached[1] == structure_id:
                del cache[key]


def _plan_idle(
    own: tuple[Robot, ...],
    enemies: tuple[Robot, ...],
    scratch: _Plan,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> None:
    """Hunt with robots that win their matchup; hold the rest."""
    enemy_by_id = {robot.entity_id: robot for robot in enemies}
    for robot in own:
        if robot.entity_id in scratch.committed:
            continue
        chased = (
            select_destroy_target(robot, SearchDestroyTarget.ROBOT, state, world)
            if _has_normal_weapon(robot)
            else None
        )
        if chased is not None and matchup(robot, enemy_by_id[chased[0]], rules) >= 0:
            scratch.desired[robot.entity_id] = SearchDestroy(SearchDestroyTarget.ROBOT)
        elif robot.order is None or isinstance(robot.order, (SearchDestroy, Advance, Retreat)):
            # A hunt it would lose, a pointless nuke or a finished errand: hold.
            scratch.desired[robot.entity_id] = StopAndDefend()
        # Stop & Defend, or a capture order with nothing to take, already holds.


def plan(
    state: GameState,
    memory: AiMemory,
    world: WorldMap,
    rules: EngineRules,
    random: MatchRandom,
) -> tuple[tuple[Command, ...], AiMemory]:
    """Return this seat's robot-order commands and updated memory.

    Draws no random numbers: every choice is a deterministic ranking with
    ties broken by entity id, then by canonical order type.
    """
    del random
    player = memory.player_id
    own = tuple(robot for robot in state.robots if robot.owner == player)
    enemies = tuple(robot for robot in state.robots if robot.owner != player)
    sites = _owned_sites(state, world, player)
    scratch = _Plan(desired={}, committed=set())

    defences, sightings = _plan_defence(
        own, enemies, sites, memory.orders, scratch, state, world, rules
    )
    if own:
        nuked = _plan_destroyers(own, scratch, state, world, rules)
        _plan_captures(own, enemies, nuked, scratch, state, world, rules)
        _plan_idle(own, enemies, scratch, state, world, rules)

    commands: list[Command] = []
    for robot in own:
        desired = scratch.desired.get(robot.entity_id)
        if desired is not None and not same_order(robot.order, desired):
            commands.append(
                SetRobotOrderCommand(
                    player=player, sequence=len(commands), entity_id=robot.entity_id, order=desired
                )
            )
    orders = AiOrderMemory(defences=defences, sightings=sightings)
    return tuple(commands), replace(memory, orders=orders)
