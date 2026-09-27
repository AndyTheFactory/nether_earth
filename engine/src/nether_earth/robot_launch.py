"""Robot launch: cap/exit/build validation, atomic commit, robot creation (issue #56, M4.6).

Per `_specs/functional-spec.md` §11 ("Construction cannot launch when:
player already has 24 robots; war-base exit is blocked; build is invalid;
resources are insufficient.") and `_specs/milestones/
04-robots-construction-economy.md` (M4.6), this module is the convergence
point of Tasks 1-5: it takes a player's active
:class:`~nether_earth.construction_session.ConstructionSession` (Task 5),
validates it against the 24-robot cap (`rules.py`'s
``max_robots_per_player``, issue #56) and the canonical M2 war-base ``EXIT``
interaction point (`interactions.py`/`occupancy.py`), and -- on success --
atomically commits the session's temporary resource buffer (Task 3/4) as
the player's new actual resources, creates exactly one authoritative
:class:`~nether_earth.robot.Robot` entity (this task's new type) at the
resolved exit cell, and clears the construction session (Task 5). This
module deliberately reuses every one of those primitives rather than
reimplementing any of them -- see the imports below.

Like `construction_session.py`'s own functions, :func:`launch_robot` is a
plain, pure function of ``(state, world, player_id, rules) -> LaunchResult``,
not a ``Command`` subclass: wiring this into ``engine.step``'s per-tick
command pipeline is explicitly Task 7 (M4.7)'s scope, not this task's (see
`construction_session.py`'s module docstring for the identical reasoning,
which applies here verbatim).

Validation order (mirrors `_specs/functional-spec.md` §11's listed
conditions, checked in the order most useful for early, cheap rejection --
session/build state first, since it requires no map/occupancy lookups, then
the cap, then the exit, which requires resolving map interaction metadata
and folding robot occupancy):

1. the player has an active construction session
   (:data:`LaunchRejectionReason.NO_ACTIVE_SESSION`);
2. the session's build-in-progress is structurally complete
   (:meth:`~nether_earth.construction_session.BuildInProgress.is_complete`;
   :data:`LaunchRejectionReason.INCOMPLETE_BUILD`) -- a complete
   build-in-progress is, by construction (see `robot_build.py`), already
   validated: no separate "is this build valid" check exists or is needed
   beyond completeness;
3. the player has fewer than ``rules.max_robots_per_player`` existing
   robots (:data:`LaunchRejectionReason.ROBOT_CAP_REACHED`);
4. the owning war base declares at least one ``EXIT`` interaction point
   (:data:`LaunchRejectionReason.NO_EXIT_DEFINED`) -- a map-authoring
   precondition, not a gameplay rule, but must be checked before resolving
   a cell;
5. the new robot's 2×2 body at the resolved exit cell is on the map and
   none of its cells is occupied or *reserved* as some
   in-flight move's destination (issue #62/M5.3; both surface as
   :data:`LaunchRejectionReason.EXIT_BLOCKED` -- see the check itself for
   why a reservation blocks an exit exactly like a standing robot does).

Every rejection path returns the original ``state`` completely unchanged
(:attr:`LaunchResult.state` is ``None`` on rejection, exactly like every
other ``*Result`` type in this codebase) -- see the module docstring below
on why this is structurally, not just behaviorally, guaranteed.

Exit-cell resolution (deterministic, single cell)
--------------------------------------------------

The resolved exit cell is the new robot's **anchor**: the robot is a 2×2
body (CR002.3, `_specs/open-questions.md` §21) covering the anchor, the
cell to its right and the two cells above them. The launch is refused as
:data:`LaunchRejectionReason.EXIT_BLOCKED` when that body would leave the
map, or when any of its cells is occupied or reserved. On the Spectrum the
robot starts at (pad.x, pad.y + 4) (`Lcb52_construction_screen_start_robot`)
and construction is only entered when no robot anchor lies where a robot
would overlap that body (the ``bit 6`` test of four cells before
``Lc849_robot_construction_if_possible``); the other five overlapping
anchors are impossible because war-base walls stand there.

A war base may in principle declare more than one ``EXIT`` interaction
point, and any one point's footprint may in principle span more than one
cell (`interactions.py` places no cardinality limit on either). This
module resolves to exactly *one* spawn cell, deterministically: the first
``EXIT`` interaction point in ``world.interaction_points_for``'s returned
order (which itself preserves the map's declared ``interaction_points``
order, per `map.py`), and within that point's footprint, the
lexicographically smallest ``(x, y)`` cell. This mirrors
``heli_pad.detect_heli_pad_landing``'s own "iterate in stable declared
order, deterministic first match" precedent for resolving canonical
interaction-point metadata to a concrete cell. A real map is expected to
declare exactly one single-cell ``EXIT`` point per war base (see the M2
fixture map), so this multi-point/multi-cell tie-break is a defensive
fallback, not the common case.

Occupancy-folding approach
-----------------------------

``OccupancyGrid`` is not attached to ``GameState`` (see `occupancy.py`'s
module docstring and this task's brief): it is computed on demand from a
``WorldMap``'s *static* structures only (``WorldMap.occupancy()``). No
robot has ever been foldable into it before this task, because no robot
entity existed. To check whether the resolved exit cell is blocked by an
existing robot (not just by static structures), this module builds a full
occupancy grid by starting from ``world.occupancy()`` and folding in every
one of ``state.robots``' current single-cell footprints via
``OccupancyGrid.with_added`` -- the existing dynamic-update API, reused
verbatim rather than inventing a second "is this cell taken" check. Robots
are folded in canonical ``state.robots`` order (already
``entity_id.value``-sorted, see `state.py`), so this fold is itself
deterministic. Since CR002.3 a robot occupies its 2×2 body (see
`occupancy.py`).

Issue #60 (M5.1) needs exactly this fold for robot movement's destination
occupancy check, so the fold itself now lives once, publicly, as
:func:`nether_earth.movement.folded_robot_occupancy`; this module's
``_folded_occupancy`` delegates to it rather than keeping a second copy
that could silently diverge.

Atomicity
------------

On success, exactly one new ``GameState`` is constructed carrying all three
changes together (committed resources, the new robot appended, the session
cleared) -- there is no intermediate state a caller could observe with only
one or two of the three changes applied, because :func:`launch_robot`
performs all three ``with_*`` calls internally and returns only the final
result; no partial state ever escapes this function. On any rejection, the
function returns before constructing any new ``GameState`` at all -- the
``state`` argument itself is returned unchanged (by reference, not even a
reconstructed equal copy), so ``state.resource_pools``, ``state.robots``,
and ``state.construction_sessions`` are trivially, provably identical
(``is``-identical, not just ``==``-equal) to their pre-call values.

Robot id assignment scheme (CR004.12, #295)
-------------------------------------------

A new robot is ``EntityId(f"robot-{owner.value}-{ordinal}")``, where
``ordinal`` is ``1 + GameState.robots_launched_by(owner)``: a per-player
monotonic launch counter (``GameState.robot_launches``) that each accepted
launch bumps and that a robot's death never lowers. Ids are therefore never
reused, not even a destroyed robot's. Until a player's first robot dies the
counter equals the number of that player's robots, so those ids are the
same as under the original M4.6 scheme (``1 + robots alive``), which
collided once robots could die.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nether_earth.collision import VerticalRange, commander_blocks_cell, unit_surface_height
from nether_earth.construction_session import ConstructionSession, exit_construction
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.movement import folded_robot_occupancy
from nether_earth.occupancy import OccupancyGrid, unit_footprint_cells, unit_footprint_in_bounds
from nether_earth.orders import StopAndDefend
from nether_earth.reservations import reservations_from_state
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.robot import Robot
from nether_earth.robot_build import BuildValidationError
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "LaunchRejectionReason",
    "LaunchResult",
    "launch_robot",
    "resolve_launch_exit",
]


class LaunchRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected :func:`launch_robot` call."""

    NO_ACTIVE_SESSION = "no_active_session"
    INCOMPLETE_BUILD = "incomplete_build"
    ROBOT_CAP_REACHED = "robot_cap_reached"
    NO_EXIT_DEFINED = "no_exit_defined"
    EXIT_BLOCKED = "exit_blocked"


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Outcome of :func:`launch_robot`.

    Accept/reject shape, mirroring ``ConstructionEntryResult``/
    ``SpendResult`` elsewhere in this codebase: exactly one of "accepted,
    carrying the resulting state and the newly created robot" or "a stable
    rejection reason" holds. ``robot`` is only ever populated alongside an
    accepted ``state`` -- it is the same entity that is present (and only
    present) in ``state.robots``, exposed directly so callers do not need
    to re-derive "which robot did this launch just create" from a diff.
    """

    accepted: bool
    state: GameState | None = None
    robot: Robot | None = None
    reason: LaunchRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and (self.state is None or self.robot is None or self.reason is not None):
            raise ValueError(
                "an accepted LaunchResult must carry a state and robot and no rejection reason"
            )
        if not self.accepted and (
            self.state is not None or self.robot is not None or self.reason is None
        ):
            raise ValueError("a rejected LaunchResult must carry a reason and no state/robot")

    @classmethod
    def accept(cls, state: GameState, robot: Robot) -> LaunchResult:
        return cls(accepted=True, state=state, robot=robot, reason=None)

    @classmethod
    def reject(cls, reason: LaunchRejectionReason) -> LaunchResult:
        return cls(accepted=False, state=None, robot=None, reason=reason)


def _folded_occupancy(world: WorldMap, state: GameState) -> OccupancyGrid:
    """Return ``world``'s static occupancy grid with every current robot folded in.

    Delegates to :func:`nether_earth.movement.folded_robot_occupancy`, the
    one shared implementation of this fold (see the module docstring's
    "occupancy-folding approach" section); kept as a named local alias so
    this module's call site reads unchanged.
    """
    return folded_robot_occupancy(world, state)


def resolve_launch_exit(
    world: WorldMap,
    state: GameState,
    war_base_id: EntityId,
    robot_height: int,
    rules: EngineRules = DEFAULT_RULES,
) -> tuple[int, int] | LaunchRejectionReason:
    """Return the exit cell a robot launched from ``war_base_id`` would take, or why it cannot.

    The exit half of :func:`launch_robot`'s validation (conditions 4 and 5
    in the module docstring): :data:`LaunchRejectionReason.NO_EXIT_DEFINED`
    or :data:`LaunchRejectionReason.EXIT_BLOCKED`, else the resolved anchor
    cell. Public so the AI construction planner (CR004.4) can skip a war base
    whose exit is blocked using the launch rule itself rather than a copy.
    ``robot_height`` is the new robot's stack height: a free commander below
    the robot's top at the exit blocks it (CR005.1).
    """
    exit_cell = _resolve_exit_cell(world, war_base_id)
    if exit_cell is None:
        return LaunchRejectionReason.NO_EXIT_DEFINED

    exit_x, exit_y = exit_cell
    if not unit_footprint_in_bounds(exit_x, exit_y, world.width, world.height):
        return LaunchRejectionReason.EXIT_BLOCKED
    occupancy = _folded_occupancy(world, state)
    if occupancy.blocks_unit(exit_x, exit_y):
        return LaunchRejectionReason.EXIT_BLOCKED

    # A robot with a move in flight authoritatively occupies its *origin*
    # cell, so the fold above cannot see the destination it is about to
    # land on -- that claim lives in M5.3's reservation contract (see
    # `movement.folded_robot_occupancy`'s docstring, which says exactly
    # this). Launching onto a reserved exit cell would therefore look legal
    # here and then stack two robots on one cell the moment that move
    # completes, since `movement.advance_robot_transition` writes the mover
    # onto its reserved destination unconditionally. A reservation blocks
    # the exit for the same reason a standing robot does, so it reuses
    # EXIT_BLOCKED rather than introducing a second "cell is taken" code
    # that callers would have to branch on identically.
    reservations = reservations_from_state(state)
    if any(reservations.is_reserved(x, y) for x, y in unit_footprint_cells(exit_x, exit_y)):
        return LaunchRejectionReason.EXIT_BLOCKED

    # A commander standing in the door (owner decision, CR005.1): the new
    # robot could not leave and would trap the commander, so no robot is
    # built. It blocks exactly as it would block the robot stepping there
    # (`movement.commander_blocks_robot_cell`): below the robot's top.
    top = unit_surface_height(world, exit_x, exit_y) + robot_height
    vertical_range = VerticalRange(bottom=0, top=top)
    if any(
        commander.docked_robot_id is None
        and commander_blocks_cell(
            state, commander, exit_x, exit_y, vertical_range, rules=rules
        )
        for commander in state.commanders
    ):
        return LaunchRejectionReason.EXIT_BLOCKED
    return exit_x, exit_y


def _resolve_exit_cell(world: WorldMap, war_base_id: EntityId) -> tuple[int, int] | None:
    """Return the deterministic exit cell for ``war_base_id``, or ``None`` if undeclared.

    See the module docstring's "exit-cell resolution" section for the exact
    deterministic tie-break rule.
    """
    exit_points = world.interaction_points_for(war_base_id, kind=InteractionKind.EXIT)
    if not exit_points:
        return None
    first_point = exit_points[0]
    return min(first_point.footprint.cells)


def _next_robot_id(state: GameState, owner: PlayerId) -> EntityId:
    """Return the next deterministic robot id for ``owner``.

    See the module docstring's "robot id assignment scheme" section.
    """
    ordinal = state.robots_launched_by(owner) + 1
    return EntityId(f"robot-{owner.value}-{ordinal}")


def launch_robot(
    state: GameState,
    world: WorldMap,
    player_id: PlayerId,
    rules: EngineRules = DEFAULT_RULES,
) -> LaunchResult:
    """Attempt to launch ``player_id``'s in-progress robot build from its active session.

    Reads the player's active
    :class:`~nether_earth.construction_session.ConstructionSession` off
    ``state`` (via ``state.construction_session_for``) rather than taking
    one as a separate parameter, matching
    `construction_session.py`'s own ``(state, player_id, ...)`` calling
    convention for ``select_module``/``deselect_module``.

    See the module docstring for the full validation order and the
    atomicity/occupancy-folding/id-assignment design notes. Returns a
    rejected :class:`LaunchResult` (with ``state`` left completely
    unchanged) for any of the conditions
    `_specs/functional-spec.md` §11 lists, or an accepted
    :class:`LaunchResult` carrying the new ``GameState`` (resources
    committed, robot added, session cleared) and the newly created
    :class:`~nether_earth.robot.Robot`.
    """
    session: ConstructionSession | None = state.construction_session_for(player_id)
    if session is None:
        return LaunchResult.reject(LaunchRejectionReason.NO_ACTIVE_SESSION)

    if not session.build.is_complete():
        return LaunchResult.reject(LaunchRejectionReason.INCOMPLETE_BUILD)

    try:
        robot_build = session.build.to_robot_build()
    except BuildValidationError:
        # Defensive: is_complete() already guarantees to_robot_build() succeeds
        # (see BuildInProgress's docstring), so this branch is unreachable in
        # practice, but is not assumed away silently.
        return LaunchResult.reject(LaunchRejectionReason.INCOMPLETE_BUILD)

    existing_robot_count = len(state.robots_for(player_id))
    if existing_robot_count >= rules.max_robots_per_player:
        return LaunchResult.reject(LaunchRejectionReason.ROBOT_CAP_REACHED)

    stack, height = derive_stack_and_height(robot_build, rules)
    exit_check = resolve_launch_exit(world, state, session.war_base_id, height, rules)
    if isinstance(exit_check, LaunchRejectionReason):
        return LaunchResult.reject(exit_check)
    exit_x, exit_y = exit_check

    entity_id = _next_robot_id(state, player_id)
    # The Spectrum starts every new robot on Stop & Defend with a 5-step walk
    # south out of the doorway (`La6c8` after `Lc849`; CR002.3, see
    # `orders.walk_out_request`).
    robot = Robot(
        entity_id=entity_id,
        owner=player_id,
        x=exit_x,
        y=exit_y,
        build=robot_build,
        stack=stack,
        height=height,
        order=StopAndDefend(),
        exit_steps_remaining=rules.robot_launch_exit_steps,
    )

    committed_pool = PlayerResourcePool.from_resource_pool(player_id, session.buffer)
    other_pools = tuple(pool for pool in state.resource_pools if pool.player_id != player_id)

    new_state = state.with_resource_pools((*other_pools, committed_pool))
    new_state = new_state.with_robots((*new_state.robots, robot))
    new_state = new_state.with_robots_launched(player_id, state.robots_launched_by(player_id) + 1)
    new_state = exit_construction(new_state, player_id, rules)

    return LaunchResult.accept(new_state, robot)
