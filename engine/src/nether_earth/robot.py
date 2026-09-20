"""Authoritative robot entity type (issue #56, M4.6).

Milestones M5 (movement/orders/capture) and M6 (combat) both need a
concrete, `GameState`-attached robot entity to build on, but neither owns
defining its shape -- M4's own task breakdown assigns that to this task,
the convergence point of Tasks 1-5 (`_specs/milestones/
04-robots-construction-economy.md`, M4.6). :class:`Robot` is therefore
deliberately minimal: it carries exactly the identity a freshly launched
robot has and nothing movement/combat-specific (no health, no orders, no
facing) that would guess at M5/M6's own eventual field additions before
those milestones have actually decided them -- the same "define only what
this task's own scope needs, leave the rest to the task that owns it"
discipline `construction_session.py`'s module docstring follows for
``Command`` subclassing.

Fields:

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
  :class:`~nether_earth.robot_build.RobotBuild` (Task 1) this robot was
  constructed from.
- ``stack``: the physical bottom-to-top component stack derived from
  ``build`` via :func:`~nether_earth.robot_stack.derive_stack` (Task 2),
  carried directly on the entity rather than recomputed by every later
  consumer (rendering, collision, combat) that needs it.
- ``height``: the total physical height derived from ``build`` via
  :func:`~nether_earth.robot_stack.derive_height` (Task 2), same
  "computed once at launch, carried on the entity" rationale as ``stack``.

``stack``/``height`` are not re-derived lazily on every access because
:class:`Robot` (like every other ``GameState``-attached type in this
codebase) is a frozen, slotted, structurally-equatable value: ``build``
never changes after launch within this milestone's scope (no
upgrade/refit mechanic exists), so ``stack``/``height`` can never drift
out of sync with it, and callers that only have a ``Robot`` in hand (not
also the ``EngineRules`` that produced its stack) can still read its
physical identity directly. `robot_launch.py` is the only place these
three derived fields are computed together, via
:func:`~nether_earth.robot_stack.derive_stack_and_height`, so there is no
second place in the engine that could compute them differently.

``GameState`` attachment (``GameState.robots``) is `robot_launch.py`
and `state.py`'s concern, not this module's -- this module defines only
the entity shape.

Movement state (added by issue #60, M5.1): :class:`RobotMoveTransition`
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
(M5.3), whose batching/release logic works with transitions detached from
the robots that own them. ``Robot.with_movement`` enforces that the
carried ``entity_id`` matches the robot it is attached to, so the
redundancy can never drift.

Order state (added by issue #64, M5.5): the ``Robot.order`` field carries
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

Strength state (added by issue #76, M6.6): the ``Robot.strength`` field is
the robot's authoritative damage counter, defaulting to ``100`` -- the
evidence-backed starting value confirmed by `_specs/open-questions.md` §9's
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

Combat channel state (added by issue #71, M6.2): the
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
fire it once (`_specs/milestones/06-combat-damage-victory.md`). This
task (M6.2) only defines and validates against the field; setting it to a
real :class:`~nether_earth.ids.EntityId` when a normal projectile is
created, and clearing it back to ``None`` when that projectile
terminates, are later M6 tasks' jobs (projectile simulation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nether_earth.ids import EntityId, PlayerId
from nether_earth.robot_build import ModuleIdentity, RobotBuild

if TYPE_CHECKING:
    from nether_earth.orders import Order

__all__ = ["Robot", "RobotMoveTransition"]


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

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("height must be a positive integer")
        if not self.stack:
            raise ValueError("stack must not be empty")
        if self.movement is not None and self.movement.entity_id != self.entity_id:
            raise ValueError(
                f"movement transition entity_id {self.movement.entity_id.value!r} does not "
                f"match robot entity_id {self.entity_id.value!r}"
            )

    def with_movement(self, movement: RobotMoveTransition | None) -> Robot:
        """Return a copy of this robot with ``movement`` replaced.

        Passing ``None`` clears an in-progress move (completion,
        cancellation, or release); passing a transition must name this
        robot's own ``entity_id`` (enforced in ``__post_init__``).
        Authoritative ``x``/``y`` are untouched -- starting a move does not
        move the robot, resolving it does (see :meth:`with_position`).

        Every other field -- ``active_projectile_id``, ``strength``
        included -- is carried over unchanged (fixed alongside issue #76,
        M6.6: this method previously omitted ``active_projectile_id`` from
        the copy, silently resetting a robot's combat channel to ``None``
        on every move-transition update; that was a latent bug this task's
        own "extend every ``with_*`` copy method" work surfaced and fixed
        in the same pass, since a dropped ``strength`` here would have
        undone :meth:`with_strength`'s damage tracking the same way).
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
        )

    def with_order(self, order: Order | None) -> Robot:
        """Return a copy of this robot with ``order`` replaced.

        Passing ``None`` clears the order (e.g. when an order completes and
        no follow-on is assigned). The robot's authoritative ``x``/``y``,
        ``build``, ``movement``, ``active_projectile_id``, and ``strength``
        are carried over unchanged; see :meth:`with_movement`'s docstring
        for the same "previously dropped active_projectile_id" latent-bug
        fix applied here.
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
        )

    def with_strength(self, strength: int) -> Robot:
        """Return a copy of this robot with ``strength`` replaced.

        Added by issue #76 (M6.6): the authoritative per-hit update point
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
        )
