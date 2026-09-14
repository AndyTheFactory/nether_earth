"""Authoritative commander domain state (issue #37; movement fields #38).

This module defines the commander's authoritative state shape described by
`_specs/technical-spec.md` §9 and `_specs/functional-spec.md` §8: an
indestructible, untargetable, physically collidable anti-grav unit with
integer X/Y, integer altitude, and a ``FREE``/``DOCKED`` mode.

Issue #37 scope (unchanged): the state model and its structural invariants.
It intentionally does not implement height-aware collision, docking/
undocking transitions, enemy-robot contact, or heli-pad interaction --
those are later M3 issues (#39-#41) that build on top of this shape.

Issue #38 scope (this revision): adds the fields and in-progress-transition
shapes needed to represent authoritative horizontal/vertical *movement*
without changing any of #37's structural invariants:

- ``rising``: persistent boolean vertical intent (see
  :mod:`nether_earth.commander_movement` for why intent is modeled as a
  persistent flag rather than a one-shot command -- the original game has
  no "hover": releasing rise always means descend/fall).
- ``horizontal_transition``: an in-progress :class:`GridTransition`, or
  ``None`` when the commander is not currently moving between cells.
  Per `_specs/technical-spec.md` §7.2/§8, authoritative X/Y only changes on
  transition *completion*; a commander mid-transition still authoritatively
  occupies its source cell until the transition resolves.
- ``vertical_transition``: the most recently committed :class:`
  VerticalTransition`, or ``None`` before any vertical update has occurred.
  Unlike horizontal movement, a vertical update's altitude change is
  authoritative *immediately* (vertical physics is a stepped, not gradual,
  update -- see the vertical cadence rules in ``rules.py``); this field
  exists purely so frontend rendering has the same ``(from, to,
  started_tick, duration_ticks)`` shape to interpolate against for the
  vertical axis as it does for the horizontal axis, per AGENTS.md's
  "any interpolation remains frontend-only" rule. It carries no legality
  meaning of its own.

All new fields default to values that reproduce #37's prior behavior
(``rising=False``, both transitions ``None``), so every existing #37
``Commander(...)`` call site and test keeps working unchanged.

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
legality against map/world geometry, and vertical legality against
collision, are out of scope for this module -- see
:mod:`nether_earth.commander_movement` for the duck-typed collision-check
callable contract that later milestones (starting with #39) implement
against.
"""

from dataclasses import dataclass
from enum import Enum

from nether_earth.ids import EntityId, PlayerId
from nether_earth.rules import DEFAULT_RULES, EngineRules

__all__ = [
    "Commander",
    "CommanderMode",
    "GridTransition",
    "VerticalTransition",
    "create_commander",
]


class CommanderMode(str, Enum):
    """Commander control mode.

    Values are stable strings (not free-form text) so serialized commander
    state is reproducible and comparable across runs, matching the
    convention already used by :class:`nether_earth.commands.RejectionReason`.
    """

    FREE = "free"
    DOCKED = "docked"


@dataclass(frozen=True, slots=True)
class GridTransition:
    """An in-progress cell-to-cell horizontal move.

    Field shape matches `_specs/technical-spec.md` §7.2's ``GridTransition``
    (recorded there for robot destination reservation), specialized here to
    a commander's own from/to X/Y rather than a generic ``entity_id`` +
    ``from_cell``/``to_cell`` pair, since a commander's transition is always
    owned by exactly one ``Commander`` and never contends for a shared
    reservation table the way robot moves do.

    ``started_tick`` is the authoritative simulation tick the move began;
    ``duration_ticks`` is how many ticks it takes to resolve (see
    ``EngineRules.commander_horizontal_move_ticks``). The move is
    authoritative-complete once ``tick >= started_tick + duration_ticks``
    (see :meth:`is_complete`) -- until then the commander's authoritative
    ``x``/``y`` remain at ``from_x``/``from_y``; only rendering may
    interpolate towards ``to_x``/``to_y`` in the meantime.
    """

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
class VerticalTransition:
    """A record of the most recently committed vertical altitude update.

    Field shape mirrors :class:`GridTransition` (``started_tick``/
    ``duration_ticks``) per `_specs/technical-spec.md` §7.2/§9, but
    represents an *already-committed* authoritative altitude change rather
    than an in-progress one: vertical physics updates altitude atomically
    on its cadence tick (see ``rules.commander_vertical_update_ticks``), so
    there is no "mid-flight" vertical state the way there is for horizontal
    movement. This dataclass exists so frontend rendering has the same
    ``(from, to, started_tick, duration_ticks)`` shape to interpolate the
    vertical axis smoothly across the update period that just elapsed.
    """

    from_altitude: int
    to_altitude: int
    started_tick: int
    duration_ticks: int

    def __post_init__(self) -> None:
        if self.started_tick < 0:
            raise ValueError("started_tick must be non-negative")
        if self.duration_ticks <= 0:
            raise ValueError("duration_ticks must be a positive integer")

    def completes_at(self) -> int:
        """Return the tick at which this update became authoritative."""
        return self.started_tick + self.duration_ticks


@dataclass(frozen=True, slots=True)
class Commander:
    """Authoritative per-player commander state.

    ``player_id`` identifies the owning player. ``mode`` is ``FREE`` or
    ``DOCKED``. ``x``/``y`` are authoritative integer grid coordinates;
    ``altitude`` is an authoritative integer height. ``docked_robot_id`` is
    only meaningful (and must be set) while ``mode`` is ``DOCKED``; it must
    be ``None`` while ``FREE`` (see the module docstring for the exact
    invariant set enforced by :meth:`__post_init__`).

    ``rising``, ``horizontal_transition``, and ``vertical_transition`` are
    the movement fields added by issue #38 -- see the module docstring for
    what each represents. All three default to values that reproduce a
    stationary, non-rising, transition-free commander, so existing #37
    construction call sites are unaffected.
    """

    player_id: PlayerId
    mode: CommanderMode
    x: int
    y: int
    altitude: int
    docked_robot_id: EntityId | None = None
    rising: bool = False
    horizontal_transition: GridTransition | None = None
    vertical_transition: VerticalTransition | None = None

    def __post_init__(self) -> None:
        if self.mode is CommanderMode.DOCKED and self.docked_robot_id is None:
            raise ValueError("a DOCKED commander must carry a docked_robot_id")
        if self.mode is CommanderMode.FREE and self.docked_robot_id is not None:
            raise ValueError("a FREE commander must not carry a docked_robot_id")

    def with_rising(self, rising: bool) -> "Commander":
        """Return a new ``Commander`` with ``rising`` intent replaced.

        Only ``rising`` changes; every other field (including any
        in-progress ``horizontal_transition``) is carried over unchanged.
        """
        return Commander(
            player_id=self.player_id,
            mode=self.mode,
            x=self.x,
            y=self.y,
            altitude=self.altitude,
            docked_robot_id=self.docked_robot_id,
            rising=rising,
            horizontal_transition=self.horizontal_transition,
            vertical_transition=self.vertical_transition,
        )

    def with_horizontal_transition(self, transition: "GridTransition | None") -> "Commander":
        """Return a new ``Commander`` with ``horizontal_transition`` replaced.

        Used both to start a new in-progress move (``transition`` set) and,
        indirectly via :meth:`with_position`, to clear it on completion.
        ``x``/``y`` are intentionally left unchanged here -- authoritative
        position only changes via :meth:`with_position`, never as a side
        effect of starting a transition.
        """
        return Commander(
            player_id=self.player_id,
            mode=self.mode,
            x=self.x,
            y=self.y,
            altitude=self.altitude,
            docked_robot_id=self.docked_robot_id,
            rising=self.rising,
            horizontal_transition=transition,
            vertical_transition=self.vertical_transition,
        )

    def with_position(self, x: int, y: int) -> "Commander":
        """Return a new ``Commander`` at authoritative ``(x, y)``.

        This is the only path that changes authoritative ``x``/``y``; it
        always clears ``horizontal_transition`` (a commander that has just
        landed on its authoritative destination cell is, by definition, no
        longer mid-transition).
        """
        return Commander(
            player_id=self.player_id,
            mode=self.mode,
            x=x,
            y=y,
            altitude=self.altitude,
            docked_robot_id=self.docked_robot_id,
            rising=self.rising,
            horizontal_transition=None,
            vertical_transition=self.vertical_transition,
        )

    def with_altitude(
        self, altitude: int, transition: "VerticalTransition | None" = None
    ) -> "Commander":
        """Return a new ``Commander`` at authoritative ``altitude``.

        ``transition``, when supplied, replaces ``vertical_transition`` (see
        the module docstring for why this is a record of an already-
        committed change, not an in-progress one -- unlike
        :meth:`with_horizontal_transition` there is no separate "start" vs.
        "complete" step for altitude).
        """
        return Commander(
            player_id=self.player_id,
            mode=self.mode,
            x=self.x,
            y=self.y,
            altitude=altitude,
            docked_robot_id=self.docked_robot_id,
            rising=self.rising,
            horizontal_transition=self.horizontal_transition,
            vertical_transition=transition,
        )


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
