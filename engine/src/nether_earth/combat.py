"""Canonical combat metadata, fire requests, projectiles, and rejection reasons (issue #70, M6.1).

This module defines the stable engine-level types consumed by later combat
tasks: :class:`FireRequest`/:class:`FireResult` form the boundary for firing
eligibility and projectile creation, :class:`Projectile` represents an
in-flight projectile's authoritative state, and :class:`FireRejectionReason`
enumerates the stable rejection codes for an invalid fire attempt.

Construction-time validation enforces basic invariants (dx/dy direction,
projectile altitude and lifetime bounds) but defers all gameplay legality
logic (weapon fitted, channel availability, range checks, accuracy) to
later tasks, per `_specs/milestones/06-combat-damage-victory.md`. Task 2
implements :func:`validate_fire`, the single authoritative fire-validation
point; this task only defines the data shapes.

All combat rules (weapon ranges, damage multipliers, projectile altitude)
live in the centralized :class:`~nether_earth.rules.EngineRules` and are
never duplicated here, so a future rule change affects every task identically.

Fire execution and projectile advancement (issue #73, M6.4)
--------------------------------------------------------------
This task adds the fire-execution entry point (:func:`apply_fire`) and the
projectile-advancement executor (:func:`advance_projectiles`), mirroring
`movement.py`'s "validate, then execute" shape and
`commander_movement.py`'s cadence-gated advance pattern
(:func:`is_projectile_advance_tick` mirrors
:func:`~nether_earth.commander_movement.is_vertical_update_tick` exactly).

Like `docking.py` (M3.4) and `commander_movement.py` (M3.2) before it, these
functions were built as a pure, directly testable reference implementation
first; M6.10 has since threaded them into ``engine.step()``'s Step 2c2 (see
`engine.py`), alongside :class:`FireCommand`, the player-facing command type
that produces a :class:`FireRequest`. Tests still call :func:`apply_fire`/
:func:`advance_projectiles` directly for unit-level coverage.

Height-collision semantics (read this before touching termination logic)
-----------------------------------------------------------------------------
A projectile is a thin traveling point at a single fixed altitude
(``rules.normal_projectile_altitude``), not a solid body resting on
anything -- unlike a commander or a robot, it never needs a "touching is not
blocking" allowance (there is nothing for it to come to rest on top of).
Per `_specs/open-questions.md` §8's disassembly evidence
(``Lb5d6_map_altitude_2x2``'s ``cp (iy+BULLET_STRUCT_ALTITUDE)`` / ``jp nc``,
"jump if no-carry", i.e. jump when ``ground_altitude >= bullet_altitude``),
an obstacle or robot blocks a projectile whenever its height is
**greater than or equal to** the projectile's flight altitude. This is a
direct ``>=`` comparison against ``structures.Component.height`` /
``robot.Robot.height`` -- it deliberately does **not** go through
`collision.py`'s :class:`~nether_earth.collision.VerticalRange`/
:meth:`~nether_earth.collision.VerticalRange.overlaps`, whose exclusive
"touching is not blocking" semantics are correct for a solid body resting on
a surface but wrong for a projectile's collision rule. Do not "fix" this by
routing through ``VerticalRange`` -- that would silently reintroduce the
wrong (exclusive) semantics.

Collision footprint -- the Spectrum's 2×2 bullet (CR002.3 #170)
---------------------------------------------------------------
Owner decision (2026-09-21): projectiles follow the original's 2×2 map-area
checks. A projectile's ``x``/``y`` is the anchor of a 2×2 body, like a
robot's (`occupancy.py`, `_specs/open-questions.md` §21). Each advance moves
it ``projectile_cells_per_advance`` (2) cells and then tests only the landing
position, as ``Lb724_bullet_update_internal`` does:

1. the anchor must be on the map (the Spectrum only tests the narrow axis,
   ``cp MAP_WIDTH``; fences close the long axis);
2. the highest static surface under the four body cells
   (``Lb5d6_map_altitude_2x2``, :func:`~nether_earth.collision.unit_surface_height`:
   structures, scenery, and terrain pieces, CR002.21) stops it when ``>=``
   its altitude, so a projectile passing right beside a high box or fence
   stops. Terrain pieces are at most 6 high (mountains) and debris 3, all
   below the bullet altitude 10, so terrain never stops a bullet;
3. a robot whose 2×2 body overlaps the projectile's body is hit (the 3×3
   scan of robot anchors around the bullet); when several do, the first in
   the Spectrum's scan order -- by anchor row, then column -- is hit.

A 2-cell step with a 2-cell-wide body leaves no gap between two landing
positions, so no intermediate position is tested (the Spectrum tests none).

Grid symmetry -- why X and Y advance uniformly (§8's Y-axis-doubling question)
-------------------------------------------------------------------------------
`_specs/open-questions.md` §8 recommended a documented
``projectile_y_axis_doubled``-style policy decision for whether this
engine's Y axis should use the same step-size convention as X for
projectile movement, since the original disassembly's raw-pixel coordinate
system gives X (``MAP_LENGTH = 512``) and Y (``MAP_WIDTH = 16``) different
physical scales. This engine's grid already represents both X and Y as
uniform logical cells -- there is no raw-pixel/doubled-coordinate
distinction anywhere in this project's own coordinate representation -- so
:func:`advance_projectiles` advances a projectile the same
``projectile_cells_per_advance`` (2) logical cells per advance-tick,
symmetrically, regardless of which axis it travels along (CR001 confirmed
one raw coordinate unit is one map cell on both axes). This makes the original's X/Y raw-unit scale distinction moot for
this engine's own grid: there is no second, smaller-scale axis to reconcile
against. This is a deliberate policy decision, not an oversight, and per the
§8 reviewer's own note, no additional ``EngineRules`` field is needed to
express it -- this paragraph is the decision.

Fire direction resolution
---------------------------
:class:`FireRequest` carries a target cell, not a cardinal direction, and
:class:`~nether_earth.robot.Robot` has no facing field. The original
disassembly copies the firing robot's own facing direction when creating a
bullet; this engine has no equivalent state to copy, so
:func:`resolve_fire_direction` instead derives a direction from the target
cell via a documented, deliberate dominant-axis-with-x-tiebreak rule -- see
its own docstring for the exact rationale. This is a documented
simplification, not a fidelity claim.

Damage, strength, and destruction (issue #76, M6.6)
-------------------------------------------------------
This task adds the damage-calculation and damage-application half of
combat: :func:`calculate_base_damage`/:func:`calculate_weapon_damage`
implement the locked, evidence-backed formula
(`_specs/open-questions.md` §9), and :func:`apply_damage` is the single
point that reads a hit robot's current strength, applies computed damage,
and either updates it in place or routes to `destruction.py`'s
:func:`~nether_earth.destruction.destroy_robot` -- mirroring this module's
own "validate/compute, then execute" shape one level further.

``ground_height_at`` -- what "ground height" means in THIS engine
-----------------------------------------------------------------------
The locked formula is ``base_damage = (60 - (robot_height + ground_height))
/ 4``. `_specs/open-questions.md` §9's disassembly research traced the
original's ``ROBOT_STRUCT_ALTITUDE`` operand (read into this formula as
"ground_height") and found it is a **misleading name**: it is not a
robot-owned altitude/elevation field at all, but the terrain elevation
directly *underneath* the robot's current map position, refreshed every
time the robot moves (`Lb5d6_map_altitude_2x2`, "update the altitude of the
robot based on the terrain underneath").

This engine's :class:`~nether_earth.robot.Robot` has no equivalent field;
the value is derived from the robot's position instead. Since CR002.21
(#203) terrain pieces carry their ``Ld7bc_map_piece_heights`` height, so
:func:`ground_height_at` is :func:`~nether_earth.collision.unit_surface_height`
at the robot's anchor -- the same ``Lb5d6`` 2×2 reading the robot's
altitude comes from: 0 on normal ground, 2 or 3 on rough, 6 on mountains,
3 on nuclear debris. A robot on high ground takes less damage, as in the
original ("Stand on a mountain to make a robot more resistant!"). The
original refreshes the altitude only when the robot moves; a robot's
position changes only by moving, so reading it from the position agrees.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

from nether_earth.collision import unit_surface_height
from nether_earth.commands import Command
from nether_earth.destruction import destroy_robot
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.occupancy import unit_footprints_overlap
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import DEFAULT_RULES, EngineRules

if TYPE_CHECKING:
    from nether_earth.map import WorldMap
    from nether_earth.state import GameState

__all__ = [
    "FireCommand",
    "FireRejectionReason",
    "FireRequest",
    "FireResult",
    "Projectile",
    "ProjectileFiredEvent",
    "ProjectileTerminatedEvent",
    "ProjectileTerminationReason",
    "RobotDamagedEvent",
    "advance_projectiles",
    "apply_damage",
    "apply_fire",
    "calculate_base_damage",
    "calculate_weapon_damage",
    "ground_height_at",
    "is_projectile_advance_tick",
    "resolve_fire_direction",
    "validate_fire",
    "weapon_range_cells",
]


class FireRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected fire attempt.

    A dedicated enum (rather than reusing a generic rejection reason) because
    fire eligibility is its own gameplay concern -- weapon legality, channel
    occupancy, and range validation are distinct from command legality or
    movement legality. Task 2 implements the validation logic that produces
    these reasons; this task defines the codes only.
    """

    NO_SUCH_ROBOT = "no_such_robot"
    ROBOT_DESTROYED = "robot_destroyed"
    NOT_CONTROLLED_BY_PLAYER = "not_controlled_by_player"
    WEAPON_NOT_FITTED = "weapon_not_fitted"
    CHANNEL_OCCUPIED = "channel_occupied"
    TARGET_OUT_OF_RANGE = "target_out_of_range"
    INVALID_NUCLEAR_STATE = "invalid_nuclear_state"
    ALREADY_FIRED_THIS_CYCLE = "already_fired_this_cycle"


@dataclass(frozen=True, slots=True)
class FireRequest:
    """A request to fire one weapon at one target location.

    ``target_x``/``target_y`` are the authoritative target grid cell
    coordinates; direction/vector computation is the validation layer's
    concern, not encoded here.
    """

    robot_id: EntityId
    player: PlayerId
    weapon: ModuleIdentity
    target_x: int
    target_y: int


@dataclass(frozen=True, slots=True)
class FireResult:
    """Outcome of validating a :class:`FireRequest`.

    Mirrors :class:`~nether_earth.movement.RobotMoveResult`'s
    accept/reject invariant: exactly one of "accepted" or "a stable
    rejection reason" holds.
    """

    request: FireRequest
    accepted: bool
    reason: FireRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError("an accepted FireResult must not carry a rejection reason")
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected FireResult must carry a rejection reason")

    @classmethod
    def accept(cls, request: FireRequest) -> FireResult:
        """Build an accepted result for ``request``."""
        return cls(request=request, accepted=True, reason=None)

    @classmethod
    def reject(cls, request: FireRequest, reason: FireRejectionReason) -> FireResult:
        """Build a rejected result for ``request`` with a stable ``reason``."""
        return cls(request=request, accepted=False, reason=reason)


@dataclass(frozen=True, slots=True)
class FireCommand(Command):
    """A player's request to fire ``weapon`` from one of their robots.

    The player-facing command type that produces a :class:`FireRequest`,
    exactly as `direct_control.py`'s
    :class:`~nether_earth.direct_control.DirectRobotMoveCommand` is the
    player-facing command that produces a
    :class:`~nether_earth.movement.RobotMoveRequest`. ``FireRequest`` itself
    is the internal currency :func:`validate_fire`/:func:`apply_fire`
    consume; nothing produced one from player input before this type
    existed.

    Unlike ``DirectRobotMoveCommand`` (which deliberately has no
    ``entity_id`` because exactly one robot is ever directly controllable),
    this command *does* name ``entity_id``, following `orders.py`'s
    :class:`~nether_earth.orders.SetRobotOrderCommand` precedent and its
    identical reasoning: firing is an action a player may direct at any of
    their own robots, not only the one their commander is docked to, and
    ownership is enforced downstream by :func:`validate_fire`'s
    ``request.player == robot.owner`` check rather than by artificially
    restricting the command's shape. Any presentation-level restriction
    (M7/M8) is a UI-availability concern, not an engine rule.

    No ``__post_init__`` validation is needed beyond what
    :func:`validate_fire` already checks structurally: unlike a move
    command's ``dx``/``dy``, a target cell has no structurally invalid
    shape -- an out-of-bounds or degenerate aim is a gameplay rejection
    (:attr:`FireRejectionReason.TARGET_OUT_OF_RANGE`), not a malformed
    command.

    ``target_x``/``target_y`` are ignored when ``weapon`` is
    :attr:`~nether_earth.robot_build.ModuleIdentity.NUCLEAR`: a nuclear
    detonation always centers on the carrier robot's own position, never on
    the aimed cell (see `engine.py`'s combat step for the code comment that
    already explains this at the call site).
    """

    entity_id: EntityId
    weapon: ModuleIdentity
    target_x: int
    target_y: int


@dataclass(frozen=True, slots=True)
class Projectile:
    """An in-flight normal (cannon/missile/phaser) projectile.

    Holds the authoritative state of a single active projectile, including
    its position, direction of travel, distance travelled so far, and
    maximum lifetime. ``z`` is fixed at the configured
    :attr:`~nether_earth.rules.EngineRules.normal_projectile_altitude`
    for all three normal weapon types and does not depend on the firing
    robot's height.

    ``dx``/``dy`` form a cardinal direction: each is in ``{-1, 0, 1}``,
    exactly one is nonzero. Diagonal travel is rejected structurally in
    ``__post_init__`` rather than as a gameplay rejection reason.

    ``first_advance_tick`` (CR002.2 #169) is the earliest tick at which
    :func:`advance_projectiles` moves the projectile; cadence ticks before it
    leave it in place. :func:`apply_fire` sets it to the cadence tick that
    closes the fire cycle for an autonomous (AI) shot, and one cycle later
    for a direct (combat-mode) shot -- see :func:`apply_fire`. ``0`` (the
    default) means no hold.
    """

    id: EntityId
    owner: PlayerId
    source_robot_id: EntityId
    weapon: ModuleIdentity
    x: int
    y: int
    z: int
    dx: int
    dy: int
    travelled_cells: int
    max_range_cells: int
    created_tick: int
    first_advance_tick: int = 0

    def __post_init__(self) -> None:
        if self.z <= 0:
            raise ValueError("projectile z (altitude) must be a positive integer")
        if self.travelled_cells < 0:
            raise ValueError("travelled_cells must be non-negative")
        if self.max_range_cells <= 0:
            raise ValueError("max_range_cells must be a positive integer")
        if self.created_tick < 0:
            raise ValueError("created_tick must be non-negative")
        if self.dx not in (-1, 0, 1) or self.dy not in (-1, 0, 1):
            raise ValueError("dx and dy must each be in {-1, 0, 1}")
        if self.dx == 0 and self.dy == 0:
            raise ValueError("dx and dy cannot both be zero (projectile must have direction)")
        if self.dx != 0 and self.dy != 0:
            raise ValueError(
                "diagonal travel is not supported: exactly one of dx/dy must be nonzero"
            )


def validate_fire(request: FireRequest, state: GameState) -> FireResult:
    """Validate ``request`` against every fire-eligibility legality rule.

    Pure function: reads its arguments and returns a :class:`FireResult`,
    never mutating anything. Checks run in this fixed order, so the same
    illegal fire attempt always reports the same reason:

    1. the source robot exists in ``state``
       (:attr:`~FireRejectionReason.NO_SUCH_ROBOT` -- this also covers a
       destroyed robot, since a destroyed robot is represented by absence
       from ``state.robots`` rather than a flag, so there is no separate
       "robot destroyed" check here);
    2. the requesting player controls the robot
       (:attr:`~FireRejectionReason.NOT_CONTROLLED_BY_PLAYER`);
    3. the requested weapon is fitted to the robot's build
       (:attr:`~FireRejectionReason.WEAPON_NOT_FITTED` -- this single check
       covers both normal weapons and nuclear, since ``robot.build.weapons``
       is where every weapon lives, nuclear included);
    4. nuclear bypasses the combat channel entirely and is accepted
       immediately; a normal weapon (cannon/missile/phaser) is rejected
       when the robot's single shared channel is already occupied
       (:attr:`~FireRejectionReason.CHANNEL_OCCUPIED`), and accepted
       otherwise.
    """
    robot = state.robot_for(request.robot_id)
    if robot is None:
        return FireResult.reject(request, FireRejectionReason.NO_SUCH_ROBOT)

    if request.player != robot.owner:
        return FireResult.reject(request, FireRejectionReason.NOT_CONTROLLED_BY_PLAYER)

    if request.weapon not in robot.build.weapons:
        return FireResult.reject(request, FireRejectionReason.WEAPON_NOT_FITTED)

    if request.weapon is ModuleIdentity.NUCLEAR:
        return FireResult.accept(request)

    if robot.active_projectile_id is not None:
        return FireResult.reject(request, FireRejectionReason.CHANNEL_OCCUPIED)

    return FireResult.accept(request)


# --------------------------------------------------------------------------
# Fire direction resolution (issue #73, M6.4)
# --------------------------------------------------------------------------


def resolve_fire_direction(robot: Robot, request: FireRequest) -> tuple[int, int] | None:
    """Resolve ``request``'s target cell into a cardinal ``(dx, dy)`` direction.

    The original disassembly copies the firing robot's own facing direction
    when a bullet is created; this engine's :class:`~nether_earth.robot.Robot`
    carries no facing field, so this is a documented, deliberate
    simplification rather than a fidelity claim: direction is derived from
    ``request``'s target cell relative to ``robot``'s own position by a
    total, deterministic dominant-axis rule.

    ``raw_dx = request.target_x - robot.x``, ``raw_dy = request.target_y -
    robot.y``. If both are zero (the target names the robot's own cell --
    a degenerate aim with no direction to derive), returns ``None``.
    Otherwise, whichever axis has the larger magnitude wins
    (``abs(raw_dx) >= abs(raw_dy)`` favors X on a tie, an arbitrary but
    fixed and documented tiebreak so the same request always resolves the
    same direction): the winning axis' sign becomes the cardinal step on
    that axis, and the other axis is ``0``.
    """
    raw_dx = request.target_x - robot.x
    raw_dy = request.target_y - robot.y
    if raw_dx == 0 and raw_dy == 0:
        return None
    if abs(raw_dx) >= abs(raw_dy):
        return (1 if raw_dx > 0 else -1, 0)
    return (0, 1 if raw_dy > 0 else -1)


def weapon_range_cells(weapon: ModuleIdentity, rules: EngineRules) -> int:
    """Return ``rules``' configured maximum range, in cells, for ``weapon``.

    One dict, one place, mirroring ``robot_build.MODULE_RESOURCE_CATEGORY``'s
    "one dict, one place" convention -- see :func:`apply_fire`. Nuclear is
    deliberately absent: it does not create a :class:`Projectile` at all (a
    later task's scope), so it never reaches this lookup.

    Public (rather than module-private) because `autonomous_combat.py`
    (issue #77, M6.7) needs the exact same weapon-range lookup to
    re-validate an autonomous engagement intent's range eligibility, and two
    independent spellings of "how far can this weapon reach" could silently
    drift apart -- see that module's own docstring.
    """
    ranges: dict[ModuleIdentity, int] = {
        ModuleIdentity.CANNON: rules.cannon_range_cells,
        ModuleIdentity.MISSILE: rules.missile_range_cells,
        ModuleIdentity.PHASER: rules.phaser_range_cells,
    }
    return ranges[weapon]


# --------------------------------------------------------------------------
# Fire execution (issue #73, M6.4)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectileFiredEvent(Event):
    """A normal-weapon projectile was created and began travelling.

    Mirrors :class:`~nether_earth.movement.RobotMoveStartedEvent`'s shape:
    the projectile's full initial state is carried on the event so
    consumers (replay, rendering) never need to re-derive it from a
    separate state read.
    """

    entity_id: EntityId
    source_robot_id: EntityId
    owner: PlayerId
    weapon: ModuleIdentity
    x: int
    y: int
    dx: int
    dy: int
    tick: int


class ProjectileTerminationReason(str, Enum):
    """Stable, serializable reason codes for why a projectile stopped travelling.

    A dedicated enum (rather than a bare string) for the same reason
    :class:`FireRejectionReason`/:class:`~nether_earth.movement.MovementRejectionReason`
    are dedicated enums: termination reason is part of this module's public,
    replay-relevant contract, and later tasks (Task 6's damage application)
    branch on ``"robot_hit"`` specifically to know whether ``hit_robot_id``
    is meaningful.
    """

    RANGE_EXHAUSTED = "range_exhausted"
    OUT_OF_BOUNDS = "out_of_bounds"
    STATIC_COLLISION = "static_collision"
    ROBOT_HIT = "robot_hit"


@dataclass(frozen=True, slots=True)
class ProjectileTerminatedEvent(Event):
    """A projectile stopped travelling (range exhausted, collision, or a hit).

    ``hit_robot_id`` is populated only when ``reason`` is
    :attr:`~ProjectileTerminationReason.ROBOT_HIT`; it is ``None`` for every
    other termination reason. This event does not itself apply any damage --
    it only identifies the collision and releases the firing robot's combat
    channel; `engine.py`'s combat step (M6.10) consumes ``hit_robot_id``
    plus ``weapon`` from this event and calls :func:`apply_damage`.

    ``weapon`` (added by M6.10) is the terminated projectile's own
    :attr:`Projectile.weapon`. It is carried here because
    :func:`apply_damage` needs it to select the right damage multiplier, and
    the projectile itself is removed from ``state.projectiles`` on the very
    tick this event is emitted -- so a consumer could not look it back up.
    This completes the "enough info for the damage layer" intent M6.4 stated
    for this event but did not finish.

    For a :attr:`~ProjectileTerminationReason.OUT_OF_BOUNDS` termination,
    the reported ``x``/``y`` is the off-map coordinate the projectile would
    have entered, not a valid in-map cell -- a future consumer (renderer,
    replay UI) should not assume this event's coordinates always fall
    within the map's bounds.
    """

    entity_id: EntityId
    owner: PlayerId
    source_robot_id: EntityId
    weapon: ModuleIdentity
    x: int
    y: int
    tick: int
    hit_robot_id: EntityId | None
    reason: ProjectileTerminationReason


def apply_fire(
    request: FireRequest,
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
    *,
    autonomous: bool = False,
) -> tuple[GameState, FireResult, tuple[Event, ...]]:
    """Validate and, if legal, execute ``request``. The one fire-execution point.

    Returns ``(new_state, result, events)``. When rejected, ``new_state is
    state`` (the caller's own object, not a rebuilt copy) and ``events`` is
    empty, mirroring :func:`~nether_earth.movement.apply_robot_move`'s "a
    failed request provably causes no partial state mutation" convention.

    Three distinct accepted outcomes:

    1. A nuclear fire request is accepted by :func:`validate_fire` and
       passed through unchanged here -- ``(state, result, None)`` -- since
       nuclear detonation execution is a later task's (M6.8's) scope; this
       function only lets it clear the accept boundary without creating a
       :class:`Projectile` or touching the combat channel.
    2. A normal-weapon (cannon/missile/phaser) request whose target equals
       the firing robot's own cell (a degenerate aim
       :func:`resolve_fire_direction` cannot turn into a direction) is
       rejected here, with :attr:`FireRejectionReason.TARGET_OUT_OF_RANGE`
       -- a fire-time-only check :func:`validate_fire` deliberately does
       not perform (see that function's docstring: its scope is narrower),
       so it is not added there. Likewise a robot that already fired in this
       fire cycle (``tick // rules.robot_fire_cycle_ticks`` equals that of
       its ``last_fire_tick``) is rejected with
       :attr:`FireRejectionReason.ALREADY_FIRED_THIS_CYCLE`: at most one
       normal-weapon shot per robot per game cycle (CR002.2 #169, owner
       decision; §8).
    3. Otherwise, a new :class:`Projectile` is created at the firing
       robot's own cell, travelling in the resolved direction, with
       ``max_range_cells`` from ``rules`` for ``request.weapon`` plus
       ``rules.electronics_range_bonus_cells`` if the robot's build has
       electronics fitted (``robot.build.electronics is
       ModuleIdentity.ELECTRONICS``), and a :class:`ProjectileFiredEvent`
       (at the firing cell) is emitted. The projectile then makes its first
       advance immediately, on the fire tick (CR002.2 #169, matching the
       Spectrum's ``Lb6d6_weapon_fire``, which calls
       ``Lb724_bullet_update_internal`` before returning), with the same
       checks as every later advance (:func:`_advance_one`). Later advances
       keep the cadence of :func:`advance_projectiles`, so the total range
       is unchanged. If the projectile survives its first move, it is added
       to ``state.projectiles`` at its new cell and the robot's
       ``active_projectile_id`` is set to it (occupying its combat channel).
       If the first move terminates it, it is never added, the channel stays
       free, a :class:`ProjectileTerminatedEvent` follows the fired event,
       and a robot hit is damaged here via :func:`apply_damage` (whose
       damage/destruction events follow). Either way the robot's
       ``last_fire_tick`` is set to ``tick``.

    ``autonomous`` selects the fire cycle's second move (§8). On the
    Spectrum an AI robot fires inside ``Lb0ca_update_robots_bullets_and_ai``'s
    robot loop, and the bullet loop that follows in the same cycle updates
    the new bullet again, so an AI shot moves 4 cells in its fire cycle. A
    combat-mode shot (``Lacb3_regular_weapon_fire``) is fired outside that
    routine and its fire step runs no bullet update, so it moves 2 cells in
    its fire cycle. So an autonomous shot's ``first_advance_tick`` is the
    cadence tick that closes the fire cycle, and a direct shot's is one
    cycle later.
    """
    result = validate_fire(request, state)
    if not result.accepted:
        return state, result, ()

    if request.weapon is ModuleIdentity.NUCLEAR:
        return state, result, ()

    robot = state.robot_for(request.robot_id)
    assert robot is not None  # guaranteed by validate_fire's NO_SUCH_ROBOT check

    direction = resolve_fire_direction(robot, request)
    if direction is None:
        rejected = FireResult.reject(request, FireRejectionReason.TARGET_OUT_OF_RANGE)
        return state, rejected, ()
    dx, dy = direction

    cycle = rules.robot_fire_cycle_ticks
    if robot.last_fire_tick is not None and robot.last_fire_tick // cycle == tick // cycle:
        rejected = FireResult.reject(request, FireRejectionReason.ALREADY_FIRED_THIS_CYCLE)
        return state, rejected, ()

    max_range = weapon_range_cells(request.weapon, rules)
    if robot.build.electronics is ModuleIdentity.ELECTRONICS:
        max_range += rules.electronics_range_bonus_cells

    projectile = Projectile(
        id=EntityId(f"projectile-{request.robot_id.value}-{tick}"),
        owner=request.player,
        source_robot_id=request.robot_id,
        weapon=request.weapon,
        x=robot.x,
        y=robot.y,
        z=rules.normal_projectile_altitude,
        dx=dx,
        dy=dy,
        travelled_cells=0,
        max_range_cells=max_range,
        created_tick=tick,
        first_advance_tick=_first_advance_tick(tick, autonomous, rules),
    )

    sequence = sequencer.next_sequence() if sequencer is not None else 0
    fired = ProjectileFiredEvent(
        sequence=sequence,
        entity_id=projectile.id,
        source_robot_id=projectile.source_robot_id,
        owner=projectile.owner,
        weapon=projectile.weapon,
        x=projectile.x,
        y=projectile.y,
        dx=projectile.dx,
        dy=projectile.dy,
        tick=tick,
    )

    # First move on the fire tick (CR002.2 #169; `Lb6d6_weapon_fire` calls
    # `Lb724_bullet_update_internal` before returning).
    moved, termination = _advance_one(projectile, state, world, rules)
    fired_robot = replace(robot, last_fire_tick=tick)
    if moved is not None:
        fired_robot = fired_robot.with_active_projectile(moved.id)
    state_after_fire = state.with_robots(
        tuple(fired_robot if r.entity_id == robot.entity_id else r for r in state.robots)
    )
    if moved is not None:
        new_state = state_after_fire.with_projectiles((*state.projectiles, moved))
        return new_state, result, (fired,)

    # Terminated by its first move: never enters `state.projectiles` and never
    # occupies the combat channel. A hit is damaged here, like the engine's
    # advance phase does for later hits.
    assert termination is not None
    reason, hit_robot_id, at_x, at_y = termination
    _cleared, terminated = _terminate_projectile(
        state, projectile, tick, reason, hit_robot_id, at_x, at_y, sequencer
    )
    if hit_robot_id is None:
        return state_after_fire, result, (fired, terminated)
    new_state, damage_events = apply_damage(
        state_after_fire, world, hit_robot_id, projectile.weapon, rules, tick, sequencer
    )
    return new_state, result, (fired, terminated, *damage_events)


def _first_advance_tick(tick: int, autonomous: bool, rules: EngineRules) -> int:
    """Return the first cadence tick that may move a projectile fired on ``tick``.

    The cadence tick after ``tick`` closes the fire cycle (the Spectrum's
    bullet loop). An autonomous shot moves there; a direct shot waits one
    more cycle (see :func:`apply_fire`).
    """
    step = rules.projectile_advance_ticks
    closing = (tick // step + 1) * step
    return closing if autonomous else closing + step


# --------------------------------------------------------------------------
# Projectile advancement (issue #73, M6.4)
# --------------------------------------------------------------------------


def is_projectile_advance_tick(tick: int, rules: EngineRules = DEFAULT_RULES) -> bool:
    """Return whether ``tick`` is a projectile-advance cadence tick.

    Structurally identical to
    :func:`~nether_earth.commander_movement.is_vertical_update_tick`: a
    cadence tick is any positive multiple of
    ``rules.projectile_advance_ticks`` (tick ``0`` is never itself an
    advance tick, since no time has elapsed yet). With the default
    ``projectile_advance_ticks=4`` this is exactly ticks 4, 8, 12, ...
    """
    return tick > 0 and tick % rules.projectile_advance_ticks == 0


def _components_at_inclusive_blocking(
    world: WorldMap, x: int, y: int, rules: EngineRules
) -> bool:
    """Return whether the static surface under the 2×2 body at ``(x, y)`` blocks a projectile.

    ``(x, y)`` is the projectile's anchor; the four body cells are read as
    ``Lb5d6_map_altitude_2x2`` reads them (CR002.3), through `collision.py`'s
    :func:`~nether_earth.collision.unit_surface_height` (structures, scenery
    and terrain pieces, CR002.21) so this module's static geometry can never
    diverge from the commander's. It blocks when ``>=
    rules.normal_projectile_altitude`` -- see the module docstring's "Height-
    collision semantics" section for why this is a direct ``>=`` comparison
    and not `collision.py`'s ``VerticalRange.overlaps()``.
    """
    return unit_surface_height(world, x, y) >= rules.normal_projectile_altitude


def _robot_hit_at(
    state: GameState,
    x: int,
    y: int,
    source_robot_id: EntityId,
    rules: EngineRules,
) -> EntityId | None:
    """Return the ``entity_id`` of the first robot hit by a projectile body anchored at ``(x, y)``.

    A robot is a candidate when its 2×2 body overlaps the projectile's 2×2
    body (CR002.3; the Spectrum's 3×3 scan of robot anchors around the
    bullet), ``robot.height >= rules.normal_projectile_altitude`` (see the
    module docstring's "Height-collision semantics" section), and it is not
    the projectile's own firer (defensive: a landing position never
    overlaps the firer's body). Candidates are taken in the Spectrum's scan
    order -- anchor row, then anchor column -- so the result is
    deterministic; two robots never share an anchor. Returns ``None`` if no
    robot qualifies.
    """
    candidates = sorted(
        (
            robot
            for robot in state.robots
            if unit_footprints_overlap(robot.x, robot.y, x, y)
            and robot.entity_id != source_robot_id
            and robot.height >= rules.normal_projectile_altitude
        ),
        key=lambda robot: (robot.y, robot.x),
    )
    return candidates[0].entity_id if candidates else None


def _range_exhausted(projectile: Projectile) -> bool:
    """Return whether ``projectile`` has already reached its maximum range.

    Checked against the projectile's CURRENT (not-yet-incremented)
    ``travelled_cells`` -- see :func:`_projectile_terminal_reason`'s
    docstring for why this must be evaluated before any new position is
    computed for this step, not after.
    """
    return projectile.travelled_cells >= projectile.max_range_cells


def _projectile_terminal_reason(
    projectile: Projectile,
    new_x: int,
    new_y: int,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> tuple[ProjectileTerminationReason, EntityId | None] | None:
    """Return ``(reason, hit_robot_id)`` if ``projectile`` terminates this step, else ``None``.

    Range exhaustion is checked by the caller (:func:`_advance_one`)
    BEFORE this function is invoked, against the projectile's current
    (not-yet-incremented) ``travelled_cells`` -- not here, and not against
    the candidate ``new_x``/``new_y``. This function only evaluates the
    remaining checks, in this fixed order, so the same terminal outcome
    always reports the same reason (mirroring
    :func:`~nether_earth.movement.validate_robot_move`'s "same illegal case
    always same reason" discipline):

    1. out of map bounds;
    2. static-geometry collision (inclusive ``>=`` height comparison);
    3. robot collision (inclusive ``>=`` height comparison, excluding the
       projectile's own firer).

    Why range exhaustion must be checked separately, against the OLD
    ``travelled_cells``, at the OLD position: a projectile with
    ``max_range_cells = N`` must remain reachable/hittable at its Nth cell
    (``travelled_cells`` becoming exactly ``N`` on the tick it moves there)
    before it expires -- collision at that final cell is still checked on
    the same tick it arrives there. Checking
    ``new_travelled >= max_range_cells`` (the post-increment value) instead
    would terminate the projectile one tick early, at ``travelled_cells ==
    N - 1``, reporting a resting cell one short of the weapon's actual
    configured range and making the Nth cell permanently unreachable. The
    correct sequence is: the projectile moves into its Nth cell and is
    collision-checked there on arrival; only on the *following* advance
    call (finding ``travelled_cells`` already ``>= max_range_cells``, with
    no move having been attempted this step) does it expire, at that exact
    resting cell.
    """
    if not (0 <= new_x < world.width and 0 <= new_y < world.height):
        return ProjectileTerminationReason.OUT_OF_BOUNDS, None

    if _components_at_inclusive_blocking(world, new_x, new_y, rules):
        return ProjectileTerminationReason.STATIC_COLLISION, None

    hit_robot_id = _robot_hit_at(state, new_x, new_y, projectile.source_robot_id, rules)
    if hit_robot_id is not None:
        return ProjectileTerminationReason.ROBOT_HIT, hit_robot_id

    return None


def _terminate_projectile(
    state: GameState,
    projectile: Projectile,
    tick: int,
    reason: ProjectileTerminationReason,
    hit_robot_id: EntityId | None,
    at_x: int,
    at_y: int,
    sequencer: EventSequencer | None,
) -> tuple[Robot | None, ProjectileTerminatedEvent]:
    """Compute this termination's robot-channel update and event.

    The single place termination logic is expressed: returns the firing
    robot with its combat channel cleared (via
    :meth:`~nether_earth.robot.Robot.with_active_projectile`), or ``None``
    if the source robot no longer exists in ``state`` (destroyed mid-flight
    by a later task's logic -- treated as "channel already released", per
    the task brief, not an error), plus the terminated event. Callers are
    responsible for actually removing ``projectile`` from
    ``state.projectiles`` and applying the returned robot update -- this
    function does not mutate ``state`` itself, so :func:`advance_projectiles`
    can batch every projectile's outcome into one final ``GameState``.
    """
    source_robot = state.robot_for(projectile.source_robot_id)
    cleared_robot = (
        source_robot.with_active_projectile(None) if source_robot is not None else None
    )

    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = ProjectileTerminatedEvent(
        sequence=sequence,
        entity_id=projectile.id,
        owner=projectile.owner,
        source_robot_id=projectile.source_robot_id,
        weapon=projectile.weapon,
        x=at_x,
        y=at_y,
        tick=tick,
        hit_robot_id=hit_robot_id,
        reason=reason,
    )
    return cleared_robot, event


_Termination = tuple[ProjectileTerminationReason, EntityId | None, int, int]


def _advance_one(
    projectile: Projectile,
    state: GameState,
    world: WorldMap,
    rules: EngineRules,
) -> tuple[Projectile | None, _Termination | None]:
    """Apply ONE advance to ``projectile``: ``(moved, None)`` or ``(None, termination)``.

    The single advance rule, shared by :func:`advance_projectiles` (every
    cadence tick) and :func:`apply_fire` (the first move on the fire tick,
    CR002.2 #169). ``termination`` is ``(reason, hit_robot_id, x, y)``.

    1. Range exhaustion is checked FIRST, against the projectile's current
       (not-yet-incremented) ``travelled_cells`` via :func:`_range_exhausted`
       -- if it has already reached ``max_range_cells``, it terminates at its
       CURRENT ``x``/``y`` (no move is attempted). See
       :func:`_projectile_terminal_reason`'s docstring for why.
    2. Otherwise, it moves along its firing axis by
       ``rules.projectile_cells_per_advance`` cells (default 2, CR001 /
       `_specs/open-questions.md` §8), capped so ``travelled_cells`` never
       exceeds ``max_range_cells``, and its 2×2 body is checked at the
       landing position only via :func:`_projectile_terminal_reason`
       (bounds, static collision, robot collision, in that fixed order), as
       ``Lb724_bullet_update_internal`` does (see the module docstring's
       "Collision footprint" section).
    """
    if _range_exhausted(projectile):
        return None, (
            ProjectileTerminationReason.RANGE_EXHAUSTED,
            None,
            projectile.x,
            projectile.y,
        )

    steps = min(
        rules.projectile_cells_per_advance,
        projectile.max_range_cells - projectile.travelled_cells,
    )
    new_x = projectile.x + projectile.dx * steps
    new_y = projectile.y + projectile.dy * steps
    outcome = _projectile_terminal_reason(projectile, new_x, new_y, state, world, rules)
    if outcome is not None:
        reason, hit_robot_id = outcome
        return None, (reason, hit_robot_id, new_x, new_y)
    moved = replace(
        projectile, x=new_x, y=new_y, travelled_cells=projectile.travelled_cells + steps
    )
    return moved, None


def advance_projectiles(
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Advance every in-flight projectile, if ``tick`` is a cadence tick.

    Returns ``(state, ())`` unchanged immediately when
    :func:`is_projectile_advance_tick` is ``False`` for ``tick`` -- no
    advancement happens on ticks that are not a cadence boundary.

    Otherwise, every projectile in ``state.projectiles`` (already in
    canonical ``id.value`` order, per `state.py`) gets one advance via
    :func:`_advance_one` (range exhaustion first, then a move of up to
    ``rules.projectile_cells_per_advance`` cells checked at its landing
    position). A projectile's
    FIRST advance is not made here but by :func:`apply_fire` on its fire
    tick (CR002.2 #169); ``engine.step()`` runs this function before firing,
    so a projectile never advances twice on its fire tick. A projectile is
    left in place while ``tick < first_advance_tick`` (a direct shot for the
    rest of its fire cycle, §8).

    A terminated projectile is dropped from the result and its firing
    robot's combat channel is cleared (unless that robot no longer exists
    -- see :func:`_terminate_projectile`); a projectile that does not
    terminate this step is kept, at its new position. This task applies no
    damage -- see :class:`ProjectileTerminatedEvent`'s docstring.

    All updates are collected into exactly one new ``GameState`` (one
    :meth:`~nether_earth.state.GameState.with_projectiles` call, one
    :meth:`~nether_earth.state.GameState.with_robots` call if any channel
    was cleared), and returned alongside every emitted event in canonical
    order via the shared ``sequencer``.
    """
    if not is_projectile_advance_tick(tick, rules):
        return state, ()

    events: list[Event] = []
    surviving_projectiles: list[Projectile] = []
    updated_robots: dict[EntityId, Robot] = {}

    for projectile in state.projectiles:
        if tick < projectile.first_advance_tick:
            surviving_projectiles.append(projectile)  # direct shot's fire cycle (§8)
            continue
        moved, termination = _advance_one(projectile, state, world, rules)
        if moved is not None:
            surviving_projectiles.append(moved)
            continue
        assert termination is not None
        reason, hit_robot_id, at_x, at_y = termination
        cleared_robot, event = _terminate_projectile(
            state, projectile, tick, reason, hit_robot_id, at_x, at_y, sequencer
        )
        events.append(event)
        if cleared_robot is not None:
            updated_robots[cleared_robot.entity_id] = cleared_robot

    new_state = state.with_projectiles(tuple(surviving_projectiles))
    if updated_robots:
        new_state = new_state.with_robots(
            tuple(
                updated_robots.get(robot.entity_id, robot) for robot in new_state.robots
            )
        )
    return new_state, tuple(events)


# --------------------------------------------------------------------------
# Damage calculation, application, and destruction (issue #76, M6.6)
# --------------------------------------------------------------------------


def ground_height_at(world: WorldMap, x: int, y: int) -> int:
    """Return the "ground height" the damage formula reads for a robot anchored at ``(x, y)``.

    See the module docstring's "``ground_height_at``" section: the highest
    static surface under the robot's 2×2 body
    (:func:`~nether_earth.collision.unit_surface_height`, ``Lb5d6``),
    including terrain piece heights (CR002.21).
    """
    return unit_surface_height(world, x, y)


def calculate_base_damage(robot_height: int, ground_height: int) -> int:
    """Return the locked base-damage value for a hit at this height/ground pair.

    Implements `_specs/open-questions.md` §9's confirmed formula exactly:
    ``(60 - (robot_height + ground_height)) // 4``. Python's ``//`` is
    floor division, matching the disassembly's confirmed two-``srl``
    (shift-right-logical) unsigned bit-level divide-by-4 on a non-negative
    operand (``Lb7a7_potentially_hit_a_robot``: ``ld a,60`` / ``sub
    HEIGHT`` / ``sub ALTITUDE`` / ``srl a`` / ``srl a``) -- there is no
    separate rounding step, and no additional clamping beyond what that
    evidence supports (a negative ``a`` was never observed in the traced
    height ranges, so this function does not defensively clamp to zero).
    """
    return (60 - (robot_height + ground_height)) // 4


_WEAPON_DAMAGE_MULTIPLIER_FIELDS: dict[ModuleIdentity, str] = {
    ModuleIdentity.CANNON: "cannon_damage_multiplier",
    ModuleIdentity.MISSILE: "missile_damage_multiplier",
    ModuleIdentity.PHASER: "phaser_damage_multiplier",
}


def calculate_weapon_damage(
    weapon: ModuleIdentity, robot_height: int, ground_height: int, rules: EngineRules
) -> int:
    """Return ``calculate_base_damage(...)`` scaled by ``weapon``'s damage multiplier.

    One dict, one place, mirroring ``robot_build.MODULE_RESOURCE_CATEGORY``'s
    "one dict, one place" convention: :data:`_WEAPON_DAMAGE_MULTIPLIER_FIELDS`
    names, per normal weapon, which ``EngineRules`` attribute holds its
    multiplier (default 2/3/4 for cannon/missile/phaser respectively,
    confirmed by `_specs/open-questions.md` §9's disassembly trace of
    ``Lb7c8_damage_calculation_loop``'s repeated-addition accumulation,
    arithmetically identical to ``base * multiplier``).

    Raises ``ValueError`` for :attr:`~nether_earth.robot_build.ModuleIdentity.NUCLEAR`
    or any non-weapon identity -- nuclear damage is an area-destruction
    event with no per-hit formula (a later task's scope, #78), so a caller
    passing it here is a caller bug, not a gameplay rejection this function
    should model as a normal return value.
    """
    field = _WEAPON_DAMAGE_MULTIPLIER_FIELDS.get(weapon)
    if field is None:
        raise ValueError(
            f"calculate_weapon_damage does not support weapon {weapon!r}: only "
            "normal weapons (cannon/missile/phaser) have a per-hit damage formula"
        )
    multiplier: int = getattr(rules, field)
    return calculate_base_damage(robot_height, ground_height) * multiplier


@dataclass(frozen=True, slots=True)
class RobotDamagedEvent(Event):
    """A robot took normal-weapon damage and survived (see :func:`apply_damage`).

    Emitted only on the survival path -- a hit that destroys the robot
    instead emits `destruction.py`'s
    :class:`~nether_earth.destruction.RobotDestroyedEvent` (and possibly a
    commander-undock event), not this one, mirroring
    :class:`ProjectileTerminatedEvent`'s "this event only covers one
    outcome" shape. ``remaining_strength`` is the robot's new,
    already-applied ``Robot.strength`` (always ``> 0`` here), so a consumer
    never needs to re-read ``state`` to know the post-hit value.
    """

    entity_id: EntityId
    owner: PlayerId
    weapon: ModuleIdentity
    damage: int
    remaining_strength: int
    tick: int


def apply_damage(
    state: GameState,
    world: WorldMap,
    target_robot_id: EntityId,
    weapon: ModuleIdentity,
    rules: EngineRules,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Apply one weapon hit to ``target_robot_id``: damage, or destruction.

    Returns ``(state, ())`` unchanged -- no event -- if ``target_robot_id``
    no longer names a live robot in ``state.robots``. This guards against a
    projectile identifying a hit on a robot that something else already
    destroyed earlier in the same tick (single-threaded tick processing
    should normally prevent this, but the guard costs nothing and mirrors
    `destruction.py`'s own :func:`~nether_earth.destruction.destroy_robot`
    idempotency guard and this codebase's "a rejected/no-op action returns
    state unchanged" convention).

    Otherwise: computes ``ground_height_at(world, robot.x, robot.y)``, then
    ``calculate_weapon_damage(weapon, robot.height, ground_height, rules)``,
    then ``new_strength = robot.strength - damage``.

    - ``new_strength <= 0``: destruction supersedes a strength update --
      this function does *not* also write the (never-observed,
      non-positive) intermediate strength value first. It calls and
      returns `destruction.py`'s :func:`~nether_earth.destruction.destroy_robot`
      directly, so destruction's own event(s) (and any docked-commander
      safety relocation) are exactly what this call returns.
    - Otherwise: the robot's ``strength`` is updated via
      :meth:`~nether_earth.robot.Robot.with_strength`, replaced in
      ``state.robots``, and a single :class:`RobotDamagedEvent` is emitted.

    The return type is a tuple of events (not a single optional event)
    because the destruction path may itself emit more than one event
    (destruction, plus a possible commander-undock event) -- this plural
    shape is deliberate from the start rather than retrofitted later.
    """
    robot = state.robot_for(target_robot_id)
    if robot is None:
        return state, ()

    ground_height = ground_height_at(world, robot.x, robot.y)
    damage = calculate_weapon_damage(weapon, robot.height, ground_height, rules)
    new_strength = robot.strength - damage

    if new_strength <= 0:
        return destroy_robot(state, target_robot_id, tick, rules, sequencer)

    updated_robot = robot.with_strength(new_strength)
    new_state = state.with_robots(
        tuple(
            updated_robot if r.entity_id == updated_robot.entity_id else r
            for r in state.robots
        )
    )
    sequence = sequencer.next_sequence() if sequencer is not None else 0
    event = RobotDamagedEvent(
        sequence=sequence,
        entity_id=updated_robot.entity_id,
        owner=updated_robot.owner,
        weapon=weapon,
        damage=damage,
        remaining_strength=new_strength,
        tick=tick,
    )
    return new_state, (event,)
