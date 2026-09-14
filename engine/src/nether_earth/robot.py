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
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.ids import EntityId, PlayerId
from nether_earth.robot_build import ModuleIdentity, RobotBuild

__all__ = ["Robot"]


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

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("height must be a positive integer")
        if not self.stack:
            raise ValueError("stack must not be empty")
