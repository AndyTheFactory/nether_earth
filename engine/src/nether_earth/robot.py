"""Authoritative robot entity type.

:class:`Robot` is the concrete, ``GameState``-attached robot entity
(`docs/mechanics/construction.md`). It defines the
entity's shape only; the rules that change each field live in their own
modules.

Identity fields:

- ``entity_id``: the robot's stable identifier, reusing
  :class:`~nether_earth.ids.EntityId` (not a second id type -- see
  `robot_launch.py`'s module docstring for the deterministic assignment
  scheme used to generate these ids at launch time).
- ``owner``: the owning :class:`~nether_earth.ids.PlayerId`, mirroring
  ``Commander``/``PlayerResourcePool``'s "the entity carries its own
  owning player id" convention.
- ``x``/``y``: the robot's current ground-cell position (integer grid
  coordinates, matching every other grid-positioned entity in this
  codebase -- ``Commander``, ``Component``, ``Footprint`` cells).
- ``build``: the concrete, already-validated
  :class:`~nether_earth.robot_build.RobotBuild` this robot was
  constructed from.
- ``stack``: the physical bottom-to-top component stack derived from
  ``build`` via :func:`~nether_earth.robot_stack.derive_stack`,
  carried directly on the entity rather than recomputed by every later
  consumer (rendering, collision, combat) that needs it.
- ``height``: the total physical height derived from ``build`` via
  :func:`~nether_earth.robot_stack.derive_height`, same
  "computed once at launch, carried on the entity" rationale as ``stack``.

``stack``/``height`` are not re-derived lazily on every access because
:class:`Robot` (like every other ``GameState``-attached type in this
codebase) is a frozen, slotted, structurally-equatable value: ``build``
never changes after launch (no upgrade/refit mechanic exists), so
``stack``/``height`` can never drift
out of sync with it, and callers that only have a ``Robot`` in hand (not
also the ``EngineRules`` that produced its stack) can still read its
physical identity directly. `robot_launch.py` is the only place these
three derived fields are computed together, via
:func:`~nether_earth.robot_stack.derive_stack_and_height`, so there is no
second place in the engine that could compute them differently.

``GameState`` attachment (``GameState.robots``) is `robot_launch.py`
and `state.py`'s concern, not this module's -- this module defines only
the entity shape.

Movement state: :class:`RobotMoveTransition`
and the ``Robot.movement`` field are the authoritative representation of
"this robot has an accepted cell-to-cell move in flight". They live here,
next to the entity they belong to, exactly as
:class:`~nether_earth.commander.GridTransition` lives next to
:class:`~nether_earth.commander.Commander` -- the movement *rules* that
create/resolve a transition live in `movement.py`, the way commander
movement rules live in `commander_movement.py`. (Keeping the type here
also avoids a `robot.py` <-> `movement.py` import cycle, since
`movement.py` needs :class:`Robot`.) Unlike the commander's transition,
this one carries its own ``entity_id``, matching
`_specs/technical-spec.md` §8's recommended robot ``GridTransition``
shape: robot moves contend for a shared destination-reservation table
(`reservations.py`), whose batching/release logic works with transitions detached from
the robots that own them. ``Robot.with_movement`` enforces that the
carried ``entity_id`` matches the robot it is attached to, so the
redundancy can never drift.

Order state: the ``Robot.order`` field carries
the robot's current autonomous order (`orders.py`'s :class:`Order` union:
``StopAndDefend``/``Advance``/``Retreat``/``SearchCapture``/
``SearchDestroy``), or ``None`` for a robot under no autonomous order at
all -- which is every robot at launch, and every robot under direct
control. It lives on the entity for the same reason ``movement`` does:
it is per-robot authoritative state, and keeping it here means
``state.robots``' single canonical ordering already orders order
evaluation too, with no second parallel ``GameState`` collection to keep
in sync. The order *rules* -- validation/fallback, movement-goal
derivation, target selection, engagement intent, and completion
transitions -- live in `orders.py`, exactly as the movement rules live in
`movement.py`; this module defines only the field. :class:`Order` is
imported under ``TYPE_CHECKING`` only, because `orders.py` itself imports
:class:`Robot` (and ``GameState``/``WorldMap``), so a runtime import here
would be circular -- the same pattern `state.py` already uses for
``Robot``/``ConstructionSession``/``CaptureProgress``.

Strength state: the ``Robot.strength`` field is
the robot's authoritative damage counter, defaulting to ``100`` -- the
evidence-backed starting value confirmed by `_specs/resolved-questions.md` "Damage, accuracy, and electronics effects"'s
disassembly research (both robot spawn sites in the original set
``ROBOT_STRUCT_STRENGTH`` to exactly ``100``, and that research pass found
no scale mismatch between this constant and the locked
``(60 - (robot_height + ground_height)) / 4`` damage formula's own ``60``
constant, so ``100`` is directly portable with no conversion). Damage
application (`combat.py`'s :func:`~nether_earth.combat.apply_damage`)
subtracts from this field; a robot whose strength would reach zero or below
is instead removed from ``state.robots`` entirely (`destruction.py`) rather
than lingering at a non-positive ``strength`` -- this codebase deliberately
does **not** add a ``Robot.destroyed`` flag, following the same "absence
from the collection is the terminal state" convention already used for
``capture_progress``/``construction_sessions`` (see `state.py`), so no other
subsystem ever needs an ``if not robot.destroyed`` guard. Because a
non-positive ``strength`` is therefore only ever a same-step, pre-removal
intermediate value (never observed by any other reader), ``__post_init__``
does not enforce ``strength > 0``.

Combat channel state: the
``Robot.active_projectile_id`` field is the authoritative per-robot gate
for "this robot already has a normal (cannon/missile/phaser) projectile
in flight" -- the milestone spec locks each robot to a single active
normal-weapon channel shared across all three normal weapon types, so a
robot with a cannon shot already airborne cannot also have a missile or
phaser shot airborne at the same time. It lives on the entity for the
same reason ``movement``/``order`` do: it is per-robot authoritative
state, and keeping it here means ``state.robots``' single canonical
ordering already orders combat evaluation too, with no second parallel
``GameState`` collection to keep in sync. Nuclear fire never touches this
field -- the nuke has no channel to occupy, since a robot can only ever
fire it once (`docs/mechanics/combat.md`).
`combat.py` sets it when a normal projectile is created and clears it back
to ``None`` when that projectile terminates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from nether_earth.ids import EntityId, PlayerId
from nether_earth.robot_build import ModuleIdentity, RobotBuild

if TYPE_CHECKING:
    from nether_earth.orders import Order

__all__ = [
    "HUNT_ROUTE_STEP_LETTERS",
    "Robot",
    "RobotFacing",
    "RobotHuntRoute",
    "RobotMoveTransition",
    "RobotTurnTransition",
]


class RobotFacing(str, Enum):
    """The cardinal direction a robot's body faces (owner request, 2026-09-23).

    The Spectrum keeps a one-hot ``ROBOT_STRUCT_DIRECTION`` per robot and
    ``Lcefd_draw_robot_piece_to_buffer`` indexes
    ``Ld6c8_piece_direction_graphic_indices`` at ``4 * piece + direction``
    to pick that piece's sprite, so a facing is exactly what the frontend
    needs to draw the other three sprites the disassembly already encodes
    (`_specs/resolved-questions.md`).

    Facing feeds the rules: a robot turns 90 degrees at a time
    (``RobotTurnTransition``), orders request rotations before steps, and
    autonomous and direct fire leave along the facing, so a shot hits only
    what its path meets (`docs/mechanics/movement.md`,
    `docs/mechanics/orders-and-capture.md`, `docs/mechanics/combat.md`).

    Axis conventions match the rest of the engine and the Spectrum's own
    step directions (``Lb4d5``: "down" is ``inc b``, i.e. ``y + 1``):
    ``EAST``/``WEST`` are ``+x``/``-x``, ``SOUTH``/``NORTH`` are
    ``+y``/``-y``.
    """

    EAST = "east"
    WEST = "west"
    SOUTH = "south"
    NORTH = "north"

    @property
    def step(self) -> tuple[int, int]:
        """Return this facing's ``(dx, dy)`` unit step."""
        return _FACING_STEPS[self]

    def rotate_toward(self, desired: RobotFacing) -> RobotFacing:
        """Return the facing one 90-degree rotation from this one toward ``desired``.

        Exactly ``Lb471_move_robot_one_step_in_desired_direction``'s rotation
        (owner request, 2026-09-23). The Spectrum stores direction one-hot as
        east 1, west 2, south 4, north 8 (``Lb724_bullet_update_internal``'s
        ``rrca`` chain: right, left, down, up), and turns like this:

        - already facing ``desired``: no rotation, the robot moves instead;
        - perpendicular: ``c = desired``, a single 90-degree turn straight
          onto it (``Lb471``'s fall-through to
          ``Lb48e_new_direction_calculated``);
        - opposite: ``desired | current`` is ``0x03`` (east/west) or ``0x0c``
          (south/north), and the code rotates the *desired* bit two positions
          (``rlc c`` twice, or ``rrc c`` twice) to land on a perpendicular
          direction first. A 180-degree turn therefore costs two rotations,
          not one.

        The two-position bit rotation is reproduced here as the explicit
        table it resolves to rather than as bit arithmetic on a one-hot
        value this engine does not otherwise carry.
        """
        if self is desired:
            return self
        if _FACING_BITS[self] | _FACING_BITS[desired] in (0x03, 0x0C):
            return _OPPOSITE_TURN_VIA[desired]
        return desired

    @classmethod
    def from_step(cls, dx: int, dy: int) -> RobotFacing | None:
        """Return the facing a one-cell step ``(dx, dy)`` turns a robot to.

        Returns ``None`` for a zero or non-cardinal step, which leaves the
        robot's current facing alone -- the original turns only when it
        actually moves in a direction (``Lb471`` returns at once for
        direction 0, so a firing update neither moves nor turns).
        """
        if dx and not dy:
            return cls.EAST if dx > 0 else cls.WEST
        if dy and not dx:
            return cls.SOUTH if dy > 0 else cls.NORTH
        return None



#: The Spectrum's one-hot ``ROBOT_STRUCT_DIRECTION`` values, needed only to
#: reproduce ``Lb471``'s opposite/perpendicular test exactly.
_FACING_BITS: dict[RobotFacing, int] = {
    RobotFacing.EAST: 0x01,
    RobotFacing.WEST: 0x02,
    RobotFacing.SOUTH: 0x04,
    RobotFacing.NORTH: 0x08,
}

_FACING_STEPS: dict[RobotFacing, tuple[int, int]] = {
    RobotFacing.EAST: (1, 0),
    RobotFacing.WEST: (-1, 0),
    RobotFacing.SOUTH: (0, 1),
    RobotFacing.NORTH: (0, -1),
}

#: Where a 180-degree turn goes first, keyed by the *desired* facing. This is
#: ``Lb471``'s ``rlc c``/``rrc c`` pair applied to the desired direction's
#: bit: east (0x01) rotates up two places to south (0x04), west (0x02) to
#: north (0x08), south (0x04) rotates down two places to east (0x01), and
#: north (0x08) to west (0x02).
_OPPOSITE_TURN_VIA: dict[RobotFacing, RobotFacing] = {
    RobotFacing.EAST: RobotFacing.SOUTH,
    RobotFacing.WEST: RobotFacing.NORTH,
    RobotFacing.SOUTH: RobotFacing.EAST,
    RobotFacing.NORTH: RobotFacing.WEST,
}


@dataclass(frozen=True, slots=True)
class RobotMoveTransition:
    """An in-progress, already-accepted cell-to-cell robot move.

    Field shape matches `_specs/technical-spec.md` §8's recommended robot
    ``GridTransition`` (``entity_id``, from/to cell, ``started_tick``,
    ``duration_ticks``), spelled with explicit ``from_x``/``from_y``/
    ``to_x``/``to_y`` integers to match every other grid-positioned value
    in this codebase.

    ``started_tick`` is the authoritative simulation tick the move began;
    ``duration_ticks`` is how many ticks it takes to resolve (see
    :func:`nether_earth.movement.move_duration_ticks`, which derives it
    from centralized :class:`~nether_earth.rules.EngineRules` data). The
    move is authoritative-complete once
    ``tick >= started_tick + duration_ticks`` (see :meth:`is_complete`);
    until then the robot's authoritative ``x``/``y`` remain at
    ``from_x``/``from_y`` and only rendering may interpolate towards the
    destination.
    """

    entity_id: EntityId
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    started_tick: int
    duration_ticks: int

    def __post_init__(self) -> None:
        if self.started_tick < 0:
            raise ValueError("started_tick must be non-negative")
        if self.duration_ticks <= 0:
            raise ValueError("duration_ticks must be a positive integer")

    def completes_at(self) -> int:
        """Return the tick at which this transition becomes authoritative-complete."""
        return self.started_tick + self.duration_ticks

    def is_complete(self, tick: int) -> bool:
        """Return whether this transition has resolved as of ``tick``."""
        return tick >= self.completes_at()



@dataclass(frozen=True, slots=True)
class RobotTurnTransition:
    """An in-progress 90-degree turn (owner request, 2026-09-23).

    A robot that wants to step in a direction it is not facing spends an
    update rotating instead of moving (``Lb471``: the rotate branch sets the
    new direction and returns, and even puts the walk-out step counter back
    because "this was not a move"). This engine spells that cost as a
    transition, mirroring :class:`RobotMoveTransition`, so it is visible in
    snapshots and blocks the robot the same way an in-flight move does.

    ``to_facing`` is the facing the robot lands on when the turn resolves --
    one 90-degree rotation toward what it wanted, not necessarily the
    direction it ultimately wants (see
    :meth:`RobotFacing.rotate_toward`; a 180-degree turn takes two of
    these). The robot's authoritative ``facing`` stays at ``from_facing``
    until the turn completes, so a half-finished turn never fires or moves
    in the new direction.
    """

    entity_id: EntityId
    from_facing: RobotFacing
    to_facing: RobotFacing
    started_tick: int
    duration_ticks: int

    def __post_init__(self) -> None:
        if self.started_tick < 0:
            raise ValueError("started_tick must be non-negative")
        if self.duration_ticks <= 0:
            raise ValueError("duration_ticks must be a positive integer")
        if self.from_facing is self.to_facing:
            raise ValueError("a turn must change the robot's facing")

    def completes_at(self) -> int:
        """Return the tick at which this turn becomes authoritative-complete."""
        return self.started_tick + self.duration_ticks

    def is_complete(self, tick: int) -> bool:
        """Return whether this turn has resolved as of ``tick``."""
        return tick >= self.completes_at()


#: One route step per letter in :attr:`RobotHuntRoute.steps`: the cardinal
#: direction of the cell entered, using :class:`RobotFacing`'s axis convention.
HUNT_ROUTE_STEP_LETTERS: dict[str, tuple[int, int]] = {
    "E": (1, 0),
    "W": (-1, 0),
    "S": (0, 1),
    "N": (0, -1),
}


@dataclass(frozen=True, slots=True)
class RobotHuntRoute:
    """A Search & Destroy (robots) hunter's cached route.

    Owner decision (2026-09-27): an electronics robot hunting robots plans a
    route to its target, follows it for up to
    :attr:`~nether_earth.rules.EngineRules.robot_hunt_replan_ticks` ticks and
    re-plans only then, or early when the route runs out, its next cell is no
    longer enterable, or the selected target changes. See
    :func:`~nether_earth.navigation.next_hunt_step`, which owns the rule.

    - ``target_id``: the target robot the route was planned for;
    - ``planned_tick``: the ``GameState.tick`` of the plan;
    - ``origin_x``/``origin_y``: the robot's anchor when it planned;
    - ``steps``: the route as one letter per cell entered (``E``/``W``/``S``/
      ``N``, see :data:`HUNT_ROUTE_STEP_LETTERS`) from the origin. ``""`` means
      the robot already stood on a goal anchor; ``None`` means no route
      existed at ``planned_tick``, and the robot steps greedily toward the
      target until the next periodic re-plan.

    The route is stored as it was planned and never consumed: the robot's
    progress is its own position on it. That keeps the cache unchanged, and
    the snapshot unchanged, between re-plans. Direction letters rather than
    cells keep a long route small on the wire.
    """

    target_id: EntityId
    planned_tick: int
    origin_x: int
    origin_y: int
    steps: str | None

    def __post_init__(self) -> None:
        if self.planned_tick < 0:
            raise ValueError("planned_tick must be non-negative")
        if self.steps is not None and any(
            letter not in HUNT_ROUTE_STEP_LETTERS for letter in self.steps
        ):
            raise ValueError("steps must only contain the letters E, W, S and N")

    @classmethod
    def from_cells(
        cls,
        target_id: EntityId,
        planned_tick: int,
        origin: tuple[int, int],
        cells: tuple[tuple[int, int], ...] | None,
    ) -> RobotHuntRoute:
        """Encode a planned route (cells after ``origin``, or ``None``) as a cache entry."""
        if cells is None:
            return cls(target_id, planned_tick, origin[0], origin[1], None)
        letters: list[str] = []
        previous = origin
        for cell in cells:
            delta = (cell[0] - previous[0], cell[1] - previous[1])
            letter = next(
                (key for key, step in HUNT_ROUTE_STEP_LETTERS.items() if step == delta), None
            )
            if letter is None:
                raise ValueError(f"route step {previous} -> {cell} is not one cardinal step")
            letters.append(letter)
            previous = cell
        return cls(target_id, planned_tick, origin[0], origin[1], "".join(letters))

    def cells(self) -> tuple[tuple[int, int], ...]:
        """Return the route's cells after the origin, in order (``()`` for no route)."""
        x, y = self.origin_x, self.origin_y
        route: list[tuple[int, int]] = []
        for letter in self.steps or "":
            dx, dy = HUNT_ROUTE_STEP_LETTERS[letter]
            x, y = x + dx, y + dy
            route.append((x, y))
        return tuple(route)


@dataclass(frozen=True, slots=True)
class Robot:
    """An authoritative, launched robot entity.

    Frozen/slotted and structurally equatable by value, like every other
    ``GameState``-attached type in this codebase, so robot state is
    snapshot/replay-safe by construction. See the module docstring for
    field rationale.
    """

    entity_id: EntityId
    owner: PlayerId
    x: int
    y: int
    build: RobotBuild
    stack: tuple[ModuleIdentity, ...]
    height: int
    movement: RobotMoveTransition | None = None
    order: Order | None = None
    active_projectile_id: EntityId | None = None
    strength: int = 100
    last_fire_tick: int | None = None
    #: Steps left in the launch walk-out (`_specs/resolved-questions.md`,
    #: Launched robots stuck in the doorway): a new robot walks south out of
    #: its war base's doorway before settling into Stop & Defend. See
    #: `robot_launch.py` and `orders.py`.
    exit_steps_remaining: int = 0
    #: The cardinal direction this robot's body faces (owner request,
    #: 2026-09-23). Feeds turning and fire direction -- see :class:`RobotFacing`. A robot
    #: is launched facing south, the direction it walks out of its war
    #: base's doorway (`La6c8` sets ``ROBOT_STRUCT_DIRECTION`` 4, "down",
    #: before the walk-out; see `robot_launch.py`).
    facing: RobotFacing = RobotFacing.SOUTH
    #: An in-progress turn, or ``None``. A robot that is turning is busy:
    #: it neither moves nor fires until the turn resolves (owner request,
    #: 2026-09-23). See :class:`RobotTurnTransition`.
    turning: RobotTurnTransition | None = None
    #: The cached route of a Search & Destroy (robots) hunt, or ``None``.
    #: See :class:`RobotHuntRoute`. Cleared whenever the
    #: order changes (:meth:`with_order`) and when a commander docks.
    hunt_route: RobotHuntRoute | None = None

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("height must be a positive integer")
        if self.exit_steps_remaining < 0:
            raise ValueError("exit_steps_remaining must be non-negative")
        if not self.stack:
            raise ValueError("stack must not be empty")
        if self.turning is not None and self.turning.entity_id != self.entity_id:
            raise ValueError(
                "a robot's turn transition must name that robot's own entity_id"
            )
        if self.turning is not None and self.turning.from_facing is not self.facing:
            raise ValueError(
                "a robot's turn transition must start from its current facing"
            )
        if self.movement is not None and self.movement.entity_id != self.entity_id:
            raise ValueError(
                f"movement transition entity_id {self.movement.entity_id.value!r} does not "
                f"match robot entity_id {self.entity_id.value!r}"
            )

    def with_turning(self, turning: RobotTurnTransition | None) -> Robot:
        """Return a copy of this robot with ``turning`` replaced.

        Passing ``None`` clears an in-progress turn without applying it;
        :meth:`with_facing` is what resolves one (it sets the new facing and
        clears the transition in the same step). Every other field is
        carried over unchanged -- see :meth:`with_movement`.
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=self.movement,
            order=self.order,
            active_projectile_id=self.active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=turning,
            hunt_route=self.hunt_route,
        )

    def with_facing(self, facing: RobotFacing) -> Robot:
        """Return a copy of this robot facing ``facing``, with any turn resolved.

        Every other field is carried over unchanged, like every other
        ``with_*`` method here (see :meth:`with_movement` for why this
        file spells each copy out rather than using ``dataclasses.replace``).
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=self.movement,
            order=self.order,
            active_projectile_id=self.active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=facing,
            turning=None,
            hunt_route=self.hunt_route,
        )

    def with_movement(self, movement: RobotMoveTransition | None) -> Robot:
        """Return a copy of this robot with ``movement`` replaced.

        Passing ``None`` clears an in-progress move (completion,
        cancellation, or release); passing a transition must name this
        robot's own ``entity_id`` (enforced in ``__post_init__``).
        Authoritative ``x``/``y`` are untouched -- starting a move does not
        move the robot, resolving it does (see :meth:`with_position`).

        Every other field -- ``active_projectile_id``, ``strength``
        included -- is carried over unchanged; dropping one would silently
        reset a robot's combat channel or damage on every move-transition
        update.
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=movement,
            order=self.order,
            active_projectile_id=self.active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=self.turning,
            hunt_route=self.hunt_route,
        )

    def with_position(self, x: int, y: int) -> Robot:
        """Return a copy of this robot at ``(x, y)`` with no in-progress move.

        Used when a move transition resolves: the destination becomes the
        robot's authoritative cell and the transition is cleared in the
        same single, auditable state transition (mirroring
        `commander.py`'s ``Commander.with_position``).

        Every other field -- ``active_projectile_id``, ``strength``
        included -- is carried over unchanged; see :meth:`with_movement`'s
        docstring for the same "previously dropped active_projectile_id"
        latent-bug fix applied here.
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=x,
            y=y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=None,
            order=self.order,
            active_projectile_id=self.active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=self.turning,
            hunt_route=self.hunt_route,
        )

    def with_order(self, order: Order | None) -> Robot:
        """Return a copy of this robot with ``order`` replaced.

        Passing ``None`` clears the order (e.g. when an order completes and
        no follow-on is assigned). The robot's authoritative ``x``/``y``,
        ``build``, ``movement``, ``active_projectile_id``, and ``strength``
        are carried over unchanged.

        The one field it does not carry over is ``hunt_route``:
        a cached hunt route belongs to the order that planned it, so any new
        order -- a player's, a completion, or a fallback -- clears it.
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=self.movement,
            order=order,
            active_projectile_id=self.active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=self.turning,
            hunt_route=None,
        )

    def with_hunt_route(self, hunt_route: RobotHuntRoute | None) -> Robot:
        """Return a copy of this robot with its cached hunt route replaced."""
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=self.movement,
            order=self.order,
            active_projectile_id=self.active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=self.turning,
            hunt_route=hunt_route,
        )

    def with_active_projectile(self, active_projectile_id: EntityId | None) -> Robot:
        """Return a copy of this robot with ``active_projectile_id`` replaced.

        Passing ``None`` clears the combat channel (no in-flight normal
        projectile); passing an :class:`~nether_earth.ids.EntityId` occupies
        it. Every other field -- authoritative ``x``/``y``, ``build``,
        ``movement``, ``order``, ``strength`` -- is carried over unchanged.
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=self.movement,
            order=self.order,
            active_projectile_id=active_projectile_id,
            strength=self.strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=self.turning,
            hunt_route=self.hunt_route,
        )

    def with_strength(self, strength: int) -> Robot:
        """Return a copy of this robot with ``strength`` replaced.

        The authoritative per-hit update point
        for `combat.py`'s :func:`~nether_earth.combat.apply_damage`. Every
        other field -- authoritative ``x``/``y``, ``build``, ``movement``,
        ``order``, ``active_projectile_id`` -- is carried over unchanged,
        mirroring every other ``with_*`` method's "one field changes, the
        rest survive" contract. Callers that compute a non-positive
        ``strength`` should route to `destruction.py`'s
        :func:`~nether_earth.destruction.destroy_robot` instead of calling
        this method -- see the module docstring's "Strength state" section
        for why ``strength <= 0`` is never actually observed here.
        """
        return Robot(
            entity_id=self.entity_id,
            owner=self.owner,
            x=self.x,
            y=self.y,
            build=self.build,
            stack=self.stack,
            height=self.height,
            movement=self.movement,
            order=self.order,
            active_projectile_id=self.active_projectile_id,
            strength=strength,
            last_fire_tick=self.last_fire_tick,
            exit_steps_remaining=self.exit_steps_remaining,
            facing=self.facing,
            turning=self.turning,
            hunt_route=self.hunt_route,
        )
