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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nether_earth.ids import EntityId, PlayerId
from nether_earth.robot_build import ModuleIdentity

__all__ = [
    "FireRejectionReason",
    "FireRequest",
    "FireResult",
    "Projectile",
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
