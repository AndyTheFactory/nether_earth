"""Height-aware commander collision.

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
decides to move (that is `commander_movement.py`'s concern). Keeping it in
its own module lets commander movement and robot movement share the exact
same collision math via the stable query contract documented below, rather than
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
2×2 bodies: a commander's and a robot's
``(x, y)`` is the anchor of a 2×2 body (`occupancy.py`,
`_specs/open-questions.md` §21). Every query below tests whole bodies:
static components under any of the four cells, and robots/commanders whose
bodies overlap.

Static components and the robot fixture are modeled as
ground-rooted physical stacks: a component/robot of height ``h`` at a cell
occupies ``[0, h)`` in that cell's column. This matches
`_specs/functional-spec.md` §7 / §9.1 (structures/robots are objects
standing on the map) and is the only height reference available anywhere in
the locked specs or `structures.py`'s per-component ``height`` field.
A robot stands on the terrain under it: its range is ``[0,
altitude + height)``, where ``altitude`` is the static surface under its
body (:func:`robot_top`, :attr:`RobotFixture.top`).

Terrain piece heights -- one surface-height function
----------------------------------------------------
Terrain pieces are solid too: the Spectrum's ``Lb052_check_player_collision``
and ``Lb5d6_map_altitude_2x2`` read every map piece's height from
``Ld7bc_map_piece_heights``, terrain (rough 2/3, mountain 6) as well as
buildings and scenery. :func:`surface_height_at` is this engine's one reading
of that table for a cell: the highest static component there or the terrain
piece height (`terrain.TerrainGrid.height_at`; nuclear debris gets the rough
piece height, `destruction.scenery_world`). :func:`unit_surface_height` is
``Lb5d6``: the highest of the four cells under a 2×2 body. Commander
collision/landing/gravity (below), projectile termination and the damage
``ground_height`` (`combat.py`) and the heli-pad rest altitude
(`heli_pad.py`) all read it, so static heights have one definition. Terrain
is ground-rooted like a component: a piece of height ``h`` occupies
``[0, h)``, so the ship rests on rough at altitude 3 and cannot fly into it
lower down, as ``Lb052``/``Lafc3_gravity`` keep ``altitude >= height``.

The robot fixture
-----------------
:class:`RobotFixture` is a minimal (id, owner, x, y, height, altitude)
projection of "a robot's top surface for collision purposes" (see
`docs/mechanics/commander.md`), scoped to collision
math only. It is deliberately *not* placed in a shared module: the real
robot model should not import or extend this type.

The commander-movement integration contract
-------------------------------------------
`commander_movement.py` does not import this module. Its movement functions
accept collision-check callables of this exact duck-typed shape::

    HorizontalMoveCheck = Callable[[GameState, Commander, int, int], bool]
    VerticalMoveCheck = Callable[[GameState, Commander, int], bool]

:func:`commander_horizontal_move_allowed` and
:func:`commander_vertical_move_allowed` below take additional keyword-only
parameters (``world``, ``robots``) that this narrower shape does not have.
That is intentional: `engine.py` binds ``world``/``robots`` with
``functools.partial`` (keyword-bound, so
parameter order does not matter), e.g.::

    horizontal_check = functools.partial(
        commander_horizontal_move_allowed, world=world, robots=robots
    )
    vertical_check = functools.partial(
        commander_vertical_move_allowed, world=world, robots=robots
    )

producing callables matching the ``HorizontalMoveCheck``/``VerticalMoveCheck``
contract. This module must not be changed in a way that breaks that binding
(i.e. ``state``, ``commander``/``mover``, ``dest_x``/``dest_y``/``dest_altitude``
must remain the leading positional parameters).

Robot-vs-commander blocking
---------------------------
:func:`commander_blocks_cell` is the stable, named query robot movement
calls to ask "does this commander block this cell at this
vertical range", without duplicating overlap math. It is intentionally the
narrowest possible query (one commander, one cell, one range) so robot
movement -- which loops over *all* commanders and its own
collision sources -- composes it rather than this module trying to guess
robot movement's iteration shape ahead of time.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.commander import Commander
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.occupancy import (
    unit_footprint_cells,
    unit_footprint_in_bounds,
    unit_footprints_overlap,
)
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
    "robot_top",
    "robot_vertical_range",
    "surface_height_at",
    "unit_surface_height",
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
    """Minimal collision projection of "a robot's top surface".

    This is **not** the real robot model. It carries exactly what collision
    math needs: a position, an
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
    #: Terrain altitude under the robot's 2×2 body: the Spectrum's
    #: ``ROBOT_STRUCT_ALTITUDE``. ``engine.py`` fills it with
    #: :func:`unit_surface_height` at the robot's authoritative anchor.
    altitude: int = 0

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError("RobotFixture.height must be a positive integer")
        if self.altitude < 0:
            raise ValueError("RobotFixture.altitude must not be negative")

    @property
    def top(self) -> int:
        """The robot's top surface: ``altitude + height`` (``Lb099``)."""
        return self.altitude + self.height


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


def robot_vertical_range(robot: RobotFixture) -> VerticalRange:
    """Return the ground-rooted vertical range ``robot`` occupies: ``[0, robot.top)``.

    Same ground-rooted convention as :func:`component_vertical_range`. The
    top includes the terrain altitude under the robot (see
    :func:`robot_top`), so the ship rests on, docks on and is blocked by a
    robot standing on rough or a mountain at ``altitude + height``, as the
    Spectrum's ``Lb099_get_robot_or_decoration_altitude`` reads it.
    """
    return VerticalRange(bottom=0, top=robot.top)


def robot_top(world: WorldMap, robot: Robot) -> int:
    """Return the top of ``robot``: the terrain under its body plus its stack height.

    The Spectrum keeps ``ROBOT_STRUCT_ALTITUDE``, the highest map piece under
    the robot's 2×2 body (``Lb5d6_map_altitude_2x2``, stored by ``Lb495``
    each time the robot advances a cell), and every robot-top reader adds it
    to ``ROBOT_STRUCT_HEIGHT``: ship landing/collision (``Lb099``), docking
    (``La69a``), the ship as an obstacle to the robot (``Lb513``) and the
    drawing elevation (``Lcee8_draw_robot_to_buffer``). In this engine the
    altitude and the authoritative anchor change together, as they do in
    ``Lb495``: :func:`unit_surface_height` at ``(robot.x, robot.y)``, the
    origin cell while a move is in progress (`movement.py`). ``world`` is
    the physical world (`destruction.scenery_world`), so nuclear debris is
    3 high.
    """
    return unit_surface_height(world, robot.x, robot.y) + robot.height


def components_at(world: WorldMap, x: int, y: int) -> tuple[Component, ...]:
    """Return every static ``Component`` (war base/factory/blocker) at ``(x, y)``.

    Queries `structures.py`'s compositional per-component model directly
    (rather than assuming one scalar height per structure), so static
    heights are component/cell-aware.
    Structures are walked in a stable order (war bases, then factories, then
    blockers, each in their tuple order) so results are deterministic; at
    most one component can legally occupy a given cell across all
    structures (enforced by `occupancy.py` at map-load time), but this
    function does not assume that invariant -- it simply returns whatever
    components are present.

    Public: `combat.py`'s projectile-vs-geometry collision
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


def surface_height_at(world: WorldMap, x: int, y: int) -> int:
    """Return the height of the static surface on cell ``(x, y)`` (``Ld7bc_map_piece_heights``).

    The highest static component there, or the terrain piece height when
    that is higher (see the module docstring); 0 off the map.
    """
    if not (0 <= x < world.width and 0 <= y < world.height):
        return 0
    terrain_height = world.terrain.height_at(x, y)
    component_height = _component_heights(world).get((x, y))
    return terrain_height if component_height is None else max(terrain_height, component_height)


#: The highest static component on each cell of a world: exactly what
#: :func:`components_at` would report, folded once per world instead of
#: walking every structure per queried cell (the per-tick robot-altitude
#: fold asks for every robot's four cells). Keyed by identity with the world
#: kept alive; every derived world (`capture`/`destruction` overlays) is
#: itself memoized, so a world object is stable across ticks.
_COMPONENT_HEIGHTS_MEMO: dict[int, tuple[WorldMap, dict[tuple[int, int], int]]] = {}
_COMPONENT_HEIGHTS_MEMO_MAX = 32


def _component_heights(world: WorldMap) -> dict[tuple[int, int], int]:
    cached = _COMPONENT_HEIGHTS_MEMO.get(id(world))
    if cached is not None and cached[0] is world:
        return cached[1]
    heights: dict[tuple[int, int], int] = {}
    structures: list[WarBase | Factory | Blocker] = [
        *world.war_bases,
        *world.factories,
        *world.blockers,
    ]
    for structure in structures:
        for component in structure.components:
            cell = (component.x, component.y)
            previous = heights.get(cell)
            heights[cell] = (
                component.height if previous is None else max(previous, component.height)
            )
    if len(_COMPONENT_HEIGHTS_MEMO) >= _COMPONENT_HEIGHTS_MEMO_MAX:
        _COMPONENT_HEIGHTS_MEMO.clear()
    _COMPONENT_HEIGHTS_MEMO[id(world)] = (world, heights)
    return heights


def unit_surface_height(world: WorldMap, x: int, y: int) -> int:
    """Return the highest static surface under the 2×2 body anchored at ``(x, y)``.

    The engine's ``Lb5d6_map_altitude_2x2``: the maximum of
    :func:`surface_height_at` over the body's four cells (off-map cells are 0).
    """
    return max(surface_height_at(world, cx, cy) for cx, cy in unit_footprint_cells(x, y))


def _robots_overlapping(
    robots: tuple[RobotFixture, ...], x: int, y: int
) -> tuple[RobotFixture, ...]:
    """Return every ``robots`` fixture whose 2×2 body overlaps the body anchored at ``(x, y)``."""
    return tuple(robot for robot in robots if unit_footprints_overlap(robot.x, robot.y, x, y))


def commander_blocks_cell(
    state: GameState,
    commander: Commander,
    x: int,
    y: int,
    vertical_range: VerticalRange,
    *,
    rules: EngineRules = DEFAULT_RULES,
) -> bool:
    """Return ``True`` iff ``commander`` blocks a 2×2 body anchored at ``(x, y)``.

    This is the stable commander-blocking query robot movement calls per
    commander it needs to check, without duplicating overlap math -- see
    the module docstring. ``(x, y)`` is the anchor of the other unit's 2×2
    body (`_specs/open-questions.md` §21): ``commander``
    blocks it iff the commander's own 2×2 body overlaps that body and its
    vertical range (from ``rules``, default
    :data:`~nether_earth.rules.DEFAULT_RULES`) overlaps ``vertical_range``.

    ``state`` is accepted (and currently unused beyond documenting the
    contract) so the signature stays stable if a future caller needs to
    cross-check ``commander`` is still a live member of ``state.commanders``
    -- it is not validated here to keep this a pure, allocation-free query.
    """
    del state  # reserved for future contract stability; see docstring
    if not unit_footprints_overlap(commander.x, commander.y, x, y):
        return False
    return commander_vertical_range(commander.altitude, rules).overlaps(vertical_range)


def _blocking_ranges_at(
    world: WorldMap,
    x: int,
    y: int,
    robots: tuple[RobotFixture, ...],
) -> tuple[VerticalRange, ...]:
    """Return every static-geometry/robot vertical range under the 2×2 body at ``(x, y)``.

    ``(x, y)`` is a commander anchor (`_specs/open-questions.md`
    §21). Static geometry -- components and terrain pieces -- is one
    ground-rooted range up to :func:`unit_surface_height`, as the Spectrum's
    ``Lb052_check_player_collision`` takes the highest map piece of its 2×2
    area; robots count when their own 2×2 body overlaps (``Lb052``'s 3×3
    window of robot anchors). Cells off the map hold nothing.

    Does not include commanders -- callers combine this with an explicit
    opposing-commander check so the commander-vs-commander rule (which
    additionally identifies *which* commander is blocking, for the
    "descend through it" rule) stays visible at the call site rather than
    being folded into an opaque range list.
    """
    static_top = unit_surface_height(world, x, y)
    ranges = [VerticalRange(bottom=0, top=static_top)] if static_top > 0 else []
    ranges.extend(robot_vertical_range(robot) for robot in _robots_overlapping(robots, x, y))
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
      while a sufficiently high commander clears it);
    - ``robots`` (friendly and enemy alike -- both are physical top
      surfaces per `_specs/open-questions.md` §14; this function does not
      distinguish ownership because the collision rule does not);
    - every *other* commander in ``state.commanders`` at the destination
      cell, per `_specs/open-questions.md` §12 (opposing commanders block
      horizontal movement only when vertical ranges overlap; same-owner
      commanders cannot coexist in ``state.commanders`` since a player has
      at most one commander, so this is effectively "the opposing
      commander" in the locked 2-player v1 scope, expressed generally).

    ``(dest_x, dest_y)`` is the anchor of the commander's 2×2 body:
    every source is tested against the whole body (see
    :func:`_blocking_ranges_at`), and a body that would leave the map is
    refused (the Spectrum keeps the ship's rows inside the map the same
    way, ``Laf90``).

    ``world``/``robots`` are keyword-only so a ``functools.partial``
    binding (see the module docstring's integration contract)
    leaves ``(state, commander, dest_x, dest_y)`` as the exact positional
    shape ``HorizontalMoveCheck`` expects.
    """
    if not unit_footprint_in_bounds(dest_x, dest_y, world.width, world.height):
        return False
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

    ``world``/``robots`` are keyword-only for the same
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
