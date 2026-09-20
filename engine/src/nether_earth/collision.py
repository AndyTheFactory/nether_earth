"""Height-aware commander collision (issue #39, M3.3).

Implements the authoritative collision rules the commander is subject to
against static world geometry, robots, and the opposing commander, per
`_specs/functional-spec.md` §8.3-8.5, `_specs/technical-spec.md` §9.1-9.2,
and the resolved decisions in `_specs/open-questions.md` §12 (commander-vs-
commander collision) and §14 (landing on an enemy robot).

Why a dedicated module
-----------------------
Collision is a pure, stateless geometric query: "does this vertical range,
at this X/Y, intersect anything that should block movement?" It has no
mutation, no ordering dependency, and no knowledge of *how* a commander
decides to move (that is #38's concern). Keeping it in its own module lets
#38's movement functions and later M5 robot movement share the exact same
collision math via the stable query contract documented below, rather than
each re-deriving vertical-range overlap semantics independently -- which
would risk them silently diverging (e.g. one treating touching surfaces as
blocking and the other not).

Vertical-range semantics (read this before changing any overlap logic)
------------------------------------------------------------------------
A :class:`VerticalRange` is a half-open interval ``[bottom, top)`` in
altitude units: it occupies every integer altitude ``a`` with
``bottom <= a < top``. Two ranges "touch" when one's ``top`` equals the
other's ``bottom`` -- that is deliberately *not* overlap. This is what makes
"stand on top of a surface" representable at all: a commander resting on a
component/robot of height ``h`` has vertical range ``[h, h + commander_height)``,
which touches the surface's ``[0, h)`` but does not overlap it, so descent
can legally stop there. Two ranges that share any interior altitude *do*
overlap and block each other. This choice is this module's own design
decision (the specs describe the blocking behavior but not the exact
interval algebra); it is applied uniformly everywhere in this module so
"touching is allowed, overlapping is blocked" is consistent for static
components, robots, and commander-vs-commander alike, per
`_specs/open-questions.md` §12 ("share X/Y only when vertical ranges do not
overlap") and §14 ("stops at the top of the ... stack").

Ground-rooted geometry
-----------------------
Static components and the (placeholder) robot fixture are modeled as
ground-rooted physical stacks: a component/robot of height ``h`` at a cell
occupies ``[0, h)`` in that cell's column. This matches
`_specs/functional-spec.md` §7 / §9.1 (structures/robots are objects
standing on the map) and is the only height reference available anywhere in
the locked specs or `structures.py`'s per-component ``height`` field.

The robot fixture placeholder
-------------------------------
No robot subsystem exists yet (M4/M5). `_specs/milestones/03-commander-movement-docking.md`
explicitly anticipates this: "Initially use test robots/height fixtures if
the full robot subsystem is not yet present." :class:`RobotFixture` here is
exactly that -- a minimal (id, owner, x, y, height) stand-in for "a robot's
top surface for collision purposes", scoped to collision math only. It is
deliberately *not* placed in a shared module: later milestones building the
real robot model should not be tempted to import or extend this type.

The #38 / #42 integration contract
------------------------------------
Issue #38 (M3.2, commander movement) is developed in parallel and does not
import this module. Its movement functions accept collision-check
callables of this exact duck-typed shape::

    HorizontalMoveCheck = Callable[[GameState, Commander, int, int], bool]
    VerticalMoveCheck = Callable[[GameState, Commander, int], bool]

:func:`commander_horizontal_move_allowed` and
:func:`commander_vertical_move_allowed` below take additional keyword-only
parameters (``world``, ``robots``) that this narrower shape does not have.
That is intentional: issue #42 (a later integration task) is expected to
bind ``world``/``robots`` with ``functools.partial`` (keyword-bound, so
parameter order does not matter), e.g.::

    horizontal_check = functools.partial(
        commander_horizontal_move_allowed, world=world, robots=robots
    )
    vertical_check = functools.partial(
        commander_vertical_move_allowed, world=world, robots=robots
    )

producing callables matching #38's ``HorizontalMoveCheck``/``VerticalMoveCheck``
contract. This module must not be changed in a way that breaks that binding
(i.e. ``state``, ``commander``/``mover``, ``dest_x``/``dest_y``/``dest_altitude``
must remain the leading positional parameters).

Robot-vs-commander forward compatibility (M5)
------------------------------------------------
:func:`commander_blocks_cell` is the stable, named query M5 robot movement
is expected to call to ask "does this commander block this cell at this
vertical range", without duplicating overlap math. It is intentionally the
narrowest possible query (one commander, one cell, one range) so robot
movement -- which will need to loop over *all* commanders and its own
collision sources -- composes it rather than this module trying to guess
robot movement's iteration shape ahead of time.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.commander import Commander
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.robot import Robot
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState
from nether_earth.structures import Blocker, Component, Factory, WarBase

__all__ = [
    "RobotFixture",
    "VerticalRange",
    "commander_blocks_cell",
    "commander_horizontal_move_allowed",
    "commander_vertical_move_allowed",
    "commander_vertical_range",
    "component_vertical_range",
    "components_at",
    "robot_vertical_range",
]


@dataclass(frozen=True, slots=True)
class VerticalRange:
    """A half-open vertical interval ``[bottom, top)`` in altitude units.

    See the module docstring for the exact touching-vs-overlapping
    semantics: two ranges that merely touch (one's ``top`` equals the
    other's ``bottom``) do *not* overlap, so "resting on top of a surface"
    is representable.
    """

    bottom: int
    top: int

    def __post_init__(self) -> None:
        if self.top <= self.bottom:
            raise ValueError("VerticalRange.top must exceed VerticalRange.bottom")

    def overlaps(self, other: VerticalRange) -> bool:
        """Return ``True`` iff ``self`` and ``other`` share an interior altitude.

        Standard half-open interval overlap test: ``self.bottom < other.top
        and other.bottom < self.top``. Touching ranges (equal boundary, no
        shared interior) return ``False``.
        """
        return self.bottom < other.top and other.bottom < self.top


@dataclass(frozen=True, slots=True)
class RobotFixture:
    """Minimal test-only stand-in for "a robot's top surface" (issue #39).

    This is **not** the real robot model -- no robot subsystem exists yet
    (M4/M5). It carries exactly what collision math needs: a position, an
    owner (so future callers can distinguish friendly/enemy if they choose
    to, though `_specs/open-questions.md` §14 treats both as physical
    surfaces identically for commander collision), and a ground-rooted
    physical ``height``. See the module docstring for why this placeholder
    lives here rather than in a shared location.
    """

    id: EntityId
    owner: PlayerId
    x: int
    y: int
    height: int

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("RobotFixture.height must be a positive integer")


def commander_vertical_range(
    altitude: int, rules: EngineRules = DEFAULT_RULES
) -> VerticalRange:
    """Return the vertical range a commander at ``altitude`` occupies.

    ``rules.commander_height`` supplies the commander's physical vertical
    extent (see ``rules.py`` for why this constant exists and its
    documented-default status). The result is ``[altitude, altitude +
    commander_height)``.
    """
    return VerticalRange(bottom=altitude, top=altitude + rules.commander_height)


def component_vertical_range(component: Component) -> VerticalRange:
    """Return the ground-rooted vertical range a static ``Component`` occupies.

    Static structures stand on the ground, so a component of height ``h``
    occupies ``[0, h)`` in its cell's column (see the module docstring).
    """
    return VerticalRange(bottom=0, top=component.height)


def robot_vertical_range(robot: RobotFixture | Robot) -> VerticalRange:
    """Return the ground-rooted vertical range ``robot``'s physical stack occupies.

    Same ground-rooted convention as :func:`component_vertical_range`:
    ``[0, robot.height)``.

    Accepts either this module's :class:`RobotFixture` placeholder or the
    real :class:`~nether_earth.robot.Robot` entity (M4.6), which carries
    the same derived ``height``. Widened by issue #60 (M5.1) so robot
    movement can ask this module for a robot's blocking range instead of
    re-deriving the ground-rooted convention itself -- the placeholder is
    still not extended, and callers holding a real robot never need to
    build a fixture to use this module.
    """
    return VerticalRange(bottom=0, top=robot.height)


def components_at(world: WorldMap, x: int, y: int) -> tuple[Component, ...]:
    """Return every static ``Component`` (war base/factory/blocker) at ``(x, y)``.

    Queries `structures.py`'s compositional per-component model directly
    (rather than assuming one scalar height per structure), per the issue's
    explicit requirement to use "M2 component/cell-aware static heights".
    Structures are walked in a stable order (war bases, then factories, then
    blockers, each in their tuple order) so results are deterministic; at
    most one component can legally occupy a given cell across all
    structures (enforced by `occupancy.py` at map-load time), but this
    function does not assume that invariant -- it simply returns whatever
    components are present.

    Public (issue #73, M6.4): `combat.py`'s projectile-vs-geometry collision
    check reuses this exact cell lookup rather than re-implementing the
    same war-bases/factories/blockers walk a second time, so the two
    modules' notion of "what static geometry occupies this cell" can never
    silently diverge.
    """
    all_structures: list[WarBase | Factory | Blocker] = [
        *world.war_bases,
        *world.factories,
        *world.blockers,
    ]
    matches: list[Component] = []
    for structure in all_structures:
        for component in structure.components:
            if component.x == x and component.y == y:
                matches.append(component)
    return tuple(matches)


def _robots_at(robots: tuple[RobotFixture, ...], x: int, y: int) -> tuple[RobotFixture, ...]:
    """Return every ``robots`` fixture at ``(x, y)``."""
    return tuple(robot for robot in robots if robot.x == x and robot.y == y)


def commander_blocks_cell(
    state: GameState,
    commander: Commander,
    x: int,
    y: int,
    vertical_range: VerticalRange,
    *,
    rules: EngineRules = DEFAULT_RULES,
) -> bool:
    """Return ``True`` iff ``commander`` blocks ``(x, y)`` at ``vertical_range``.

    This is the stable, forward-compatible query named by the issue's
    "expose the commander blocking query/contract needed later by robot
    movement" acceptance criterion (M5 robot movement will call this per
    commander it needs to check, without duplicating overlap math -- see
    the module docstring). ``commander`` blocks the cell iff it currently
    occupies ``(x, y)`` and its own vertical range (from ``rules``, default
    :data:`~nether_earth.rules.DEFAULT_RULES`) overlaps ``vertical_range``.

    ``state`` is accepted (and currently unused beyond documenting the
    contract) so the signature stays stable if a future caller needs to
    cross-check ``commander`` is still a live member of ``state.commanders``
    -- it is not validated here to keep this a pure, allocation-free query.
    """
    del state  # reserved for future contract stability; see docstring
    if commander.x != x or commander.y != y:
        return False
    return commander_vertical_range(commander.altitude, rules).overlaps(vertical_range)


def _blocking_ranges_at(
    world: WorldMap,
    x: int,
    y: int,
    robots: tuple[RobotFixture, ...],
) -> tuple[VerticalRange, ...]:
    """Return every static-geometry/robot vertical range occupying ``(x, y)``.

    Does not include commanders -- callers combine this with an explicit
    opposing-commander check so the commander-vs-commander rule (which
    additionally identifies *which* commander is blocking, for the
    "descend through it" rule) stays visible at the call site rather than
    being folded into an opaque range list.
    """
    ranges = [component_vertical_range(component) for component in components_at(world, x, y)]
    ranges.extend(robot_vertical_range(robot) for robot in _robots_at(robots, x, y))
    return tuple(ranges)


def _other_commanders(state: GameState, commander: Commander) -> tuple[Commander, ...]:
    """Return every commander in ``state`` other than ``commander`` itself.

    Compares by ``player_id`` (not object identity) so a commander value
    equal-but-not-identical to the one stored in ``state.commanders`` (e.g.
    a caller testing a hypothetical move) is still correctly excluded from
    "opposing" commanders.
    """
    return tuple(
        other for other in state.commanders if other.player_id != commander.player_id
    )


def commander_horizontal_move_allowed(
    state: GameState,
    commander: Commander,
    dest_x: int,
    dest_y: int,
    *,
    world: WorldMap,
    robots: tuple[RobotFixture, ...] = (),
    rules: EngineRules = DEFAULT_RULES,
) -> bool:
    """Return ``True`` iff ``commander`` may move to ``(dest_x, dest_y)``.

    Checks ``commander``'s *current* vertical range (horizontal movement
    alone does not change altitude) against:

    - static geometry components at the destination cell (`structures.py`,
      queried per-component so a tall component blocks a low commander
      while a sufficiently high commander clears it -- the acceptance
      criterion this directly implements);
    - ``robots`` (friendly and enemy alike -- both are physical top
      surfaces per `_specs/open-questions.md` §14; this function does not
      distinguish ownership because the collision rule does not);
    - every *other* commander in ``state.commanders`` at the destination
      cell, per `_specs/open-questions.md` §12 (opposing commanders block
      horizontal movement only when vertical ranges overlap; same-owner
      commanders cannot coexist in ``state.commanders`` since a player has
      at most one commander, so this is effectively "the opposing
      commander" in the locked 2-player v1 scope, expressed generally).

    ``world``/``robots`` are keyword-only so a later ``functools.partial``
    binding (see the module docstring's #38/#42 integration contract)
    leaves ``(state, commander, dest_x, dest_y)`` as the exact positional
    shape #38's ``HorizontalMoveCheck`` expects.
    """
    mover_range = commander_vertical_range(commander.altitude, rules)

    for blocking_range in _blocking_ranges_at(world, dest_x, dest_y, robots):
        if mover_range.overlaps(blocking_range):
            return False

    for other in _other_commanders(state, commander):
        if commander_blocks_cell(state, other, dest_x, dest_y, mover_range, rules=rules):
            return False

    return True


def commander_vertical_move_allowed(
    state: GameState,
    commander: Commander,
    dest_altitude: int,
    *,
    world: WorldMap,
    robots: tuple[RobotFixture, ...] = (),
    rules: EngineRules = DEFAULT_RULES,
) -> bool:
    """Return ``True`` iff ``commander`` may move to ``dest_altitude``.

    Checks the vertical range ``commander`` would occupy at
    ``dest_altitude`` (its X/Y does not change) against the same three
    sources as :func:`commander_horizontal_move_allowed`, evaluated at
    ``commander``'s current ``(x, y)`` column: static components, robots
    (friendly and enemy), and every other commander occupying that column.

    This is what stops descent on a static surface or a robot's top surface
    (the new range touches, but per the module docstring's half-open
    semantics does not overlap, the surface below it once the commander is
    resting on it) and what implements "a commander may prevent the other
    from descending through it" (an overlapping opposing commander at the
    destination range blocks the move outright, whether ascending or
    descending).

    ``world``/``robots`` are keyword-only for the same #38/#42
    ``functools.partial`` binding reason as
    :func:`commander_horizontal_move_allowed` -- see the module docstring.
    """
    dest_range = commander_vertical_range(dest_altitude, rules)

    for blocking_range in _blocking_ranges_at(world, commander.x, commander.y, robots):
        if dest_range.overlaps(blocking_range):
            return False

    for other in _other_commanders(state, commander):
        if commander_blocks_cell(
            state, other, commander.x, commander.y, dest_range, rules=rules
        ):
            return False

    return True
