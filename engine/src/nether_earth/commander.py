"""Authoritative commander domain state (issue #37).

This module defines the commander's authoritative state shape described by
`_specs/technical-spec.md` §9 and `_specs/functional-spec.md` §8: an
indestructible, untargetable, physically collidable anti-grav unit with
integer X/Y, integer altitude, and a ``FREE``/``DOCKED`` mode.

Scope: this issue defines only the state model and its structural
invariants. It intentionally does not implement horizontal/vertical
movement, height-aware collision, docking/undocking transitions, enemy-robot
contact, or heli-pad interaction -- those are later M3 issues (#38-#41) that
build on top of this shape.

Invariant set (enforced by :meth:`Commander.__post_init__`):

- ``mode == DOCKED`` requires ``docked_robot_id is not None`` -- a docked
  commander must always name the robot it is docked to.
- ``mode == FREE`` requires ``docked_robot_id is None`` -- a free commander
  must never carry a stale docked-robot reference.

These are the only invariants ``Commander`` itself enforces, because they
are structural (mode/field consistency) and require no external
configuration. Altitude range legality is *not* enforced here: the legal
altitude envelope is a centralized, overridable rule (see ``rules.py``), not
a property of the ``Commander`` shape itself, so ``Commander`` does not
hardcode or embed a rules object. Callers that need to construct a
rules-validated commander should use :func:`create_commander`, which checks
the supplied (or default) :class:`~nether_earth.rules.EngineRules` altitude
bounds in addition to the structural invariants above. Horizontal (X/Y)
legality against map/world geometry is out of scope for this issue (no
robot/world entity model exists yet to validate against).
"""

from dataclasses import dataclass
from enum import Enum

from nether_earth.ids import EntityId, PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules

__all__ = ["Commander", "CommanderMode", "create_commander"]


class CommanderMode(str, Enum):
    """Commander control mode.

    Values are stable strings (not free-form text) so serialized commander
    state is reproducible and comparable across runs, matching the
    convention already used by :class:`nether_earth.commands.RejectionReason`.
    """

    FREE = "free"
    DOCKED = "docked"


@dataclass(frozen=True, slots=True)
class Commander:
    """Authoritative per-player commander state.

    ``player_id`` identifies the owning player. ``mode`` is ``FREE`` or
    ``DOCKED``. ``x``/``y`` are authoritative integer grid coordinates;
    ``altitude`` is an authoritative integer height. ``docked_robot_id`` is
    only meaningful (and must be set) while ``mode`` is ``DOCKED``; it must
    be ``None`` while ``FREE`` (see the module docstring for the exact
    invariant set enforced by :meth:`__post_init__`).
    """

    player_id: PlayerId
    mode: CommanderMode
    x: int
    y: int
    altitude: int
    docked_robot_id: EntityId | None = None

    def __post_init__(self) -> None:
        if self.mode is CommanderMode.DOCKED and self.docked_robot_id is None:
            raise ValueError("a DOCKED commander must carry a docked_robot_id")
        if self.mode is CommanderMode.FREE and self.docked_robot_id is not None:
            raise ValueError("a FREE commander must not carry a docked_robot_id")


def create_commander(
    player_id: PlayerId,
    mode: CommanderMode,
    x: int,
    y: int,
    altitude: int,
    docked_robot_id: EntityId | None = None,
    rules: EngineRules = DEFAULT_RULES,
) -> Commander:
    """Construct a :class:`Commander`, validated against ``rules``.

    In addition to the structural FREE/DOCKED invariants enforced by
    :meth:`Commander.__post_init__`, this factory checks ``altitude``
    against ``rules.commander_min_altitude``/``rules.commander_max_altitude``
    so callers that do care about rules-legal altitude have a single
    validated construction path, while ``Commander`` itself stays free of an
    embedded rules dependency (see the module docstring).
    """
    if altitude < rules.commander_min_altitude or altitude > rules.commander_max_altitude:
        raise ValueError(
            f"altitude {altitude} is outside the legal range "
            f"[{rules.commander_min_altitude}, {rules.commander_max_altitude}]"
        )
    return Commander(
        player_id=player_id,
        mode=mode,
        x=x,
        y=y,
        altitude=altitude,
        docked_robot_id=docked_robot_id,
    )
