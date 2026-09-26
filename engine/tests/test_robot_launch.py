"""Tests for robot launch constraints, atomic commit, and creation (issue #56, M4.6).

Builds small ``WorldMap`` fixtures directly in Python (rather than a YAML
fixture) so each test controls exactly where a war base's ``EXIT``
interaction point sits relative to its own physical components -- the
shared ``fixtures/world_map_basic.yaml`` used elsewhere happens to place a
war base's exit cell *on top of* one of its own physical components, which
would make the exit permanently "occupied" by the war base's own static
structure and unusable for a "free exit" scenario.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from nether_earth.construction_economy import ResourcePool
from nether_earth.construction_session import BuildInProgress, ConstructionSession
from nether_earth.destruction import destroy_robot
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap
from nether_earth.movement import (
    RobotMoveRequest,
    apply_robot_move,
    cancel_robot_move,
    folded_robot_occupancy,
)
from nether_earth.reservations import reservations_from_state
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.robot import Robot, RobotFacing
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_launch import LaunchRejectionReason, launch_robot
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState, RobotLaunchCount, create_game_state
from nether_earth.structures import Component, Footprint, WarBase
from nether_earth.terrain import TerrainGrid

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")
WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")

P1_EXIT_CELL = (5, 1)  # a robot anchor: its 2×2 body is rows 0..1 (CR002.3)
P2_EXIT_CELL = (5, 9)


def _world(
    *,
    war_bases: tuple[WarBase, ...] | None = None,
    interaction_points: tuple[InteractionPoint, ...] | None = None,
    width: int = 20,
    height: int = 20,
) -> WorldMap:
    if war_bases is None:
        war_bases = (
            WarBase(id=WAR_BASE_ONE, components=(Component(x=0, y=0, height=3),), owner=PLAYER_ONE),
            WarBase(id=WAR_BASE_TWO, components=(Component(x=0, y=9, height=3),), owner=PLAYER_TWO),
        )
    if interaction_points is None:
        interaction_points = (
            InteractionPoint(
                id="warbase-p1-exit",
                kind=InteractionKind.EXIT,
                structure_id=WAR_BASE_ONE,
                footprint=Footprint(cells=frozenset({P1_EXIT_CELL})),
            ),
            InteractionPoint(
                id="warbase-p2-exit",
                kind=InteractionKind.EXIT,
                structure_id=WAR_BASE_TWO,
                footprint=Footprint(cells=frozenset({P2_EXIT_CELL})),
            ),
        )
    return WorldMap(
        map_id="test-robot-launch",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(width=width, height=height, cells={}),
        war_bases=war_bases,
        factories=(),
        blockers=(),
        interaction_points=interaction_points,
        spawn_positions={},
    )


def _complete_build() -> BuildInProgress:
    return BuildInProgress(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))


def _session(
    player_id: PlayerId = PLAYER_ONE,
    war_base_id: EntityId = WAR_BASE_ONE,
    build: BuildInProgress | None = None,
    buffer: ResourcePool | None = None,
) -> ConstructionSession:
    return ConstructionSession(
        player_id=player_id,
        war_base_id=war_base_id,
        entry_tick=0,
        build=build if build is not None else _complete_build(),
        buffer=buffer if buffer is not None else ResourcePool(general=17, category={}),
        entry_snapshot=ResourcePool(general=20, category={}),
    )


def _dummy_robot(
    owner: PlayerId, ordinal: int, x: int, y: int, facing: RobotFacing = RobotFacing.EAST
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.MISSILE,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(f"robot-{owner.value}-{ordinal}"),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        facing=facing,
    )


def _state(
    *,
    session: ConstructionSession | None = None,
    robots: tuple[Robot, ...] = (),
    resource_pools: tuple[PlayerResourcePool, ...] | None = None,
) -> GameState:
    """Build a state; hand-placed robots count as already launched by their owner."""
    players = (PLAYER_ONE, PLAYER_TWO)
    if resource_pools is None:
        resource_pools = (
            PlayerResourcePool(player_id=PLAYER_ONE, general=20),
            PlayerResourcePool(player_id=PLAYER_TWO, general=20),
        )
    sessions = (session,) if session is not None else ()
    return create_game_state(
        0,
        players,
        resource_pools=resource_pools,
        construction_sessions=sessions,
        robots=robots,
        robot_launches=tuple(
            RobotLaunchCount(player, len([r for r in robots if r.owner == player]))
            for player in players
            if any(r.owner == player for r in robots)
        ),
    )


# --- Successful launch -------------------------------------------------------


def test_launch_succeeds_creates_robot_commits_resources_clears_session() -> None:
    world = _world()
    buffer = ResourcePool(general=15, category={})
    state = _state(session=_session(buffer=buffer))

    result = launch_robot(state, world, PLAYER_ONE)

    assert result.accepted
    assert result.reason is None
    assert result.state is not None
    assert result.robot is not None

    robot = result.robot
    assert robot.entity_id == EntityId("robot-p1-1")
    assert robot.owner == PLAYER_ONE
    assert (robot.x, robot.y) == P1_EXIT_CELL
    assert robot.build == RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    expected_stack, expected_height = derive_stack_and_height(robot.build, DEFAULT_RULES)
    assert robot.stack == expected_stack
    assert robot.height == expected_height

    new_state = result.state
    assert new_state.robots == (robot,)
    assert new_state.construction_session_for(PLAYER_ONE) is None
    committed = new_state.resource_pool_for(PLAYER_ONE)
    assert committed == PlayerResourcePool.from_resource_pool(PLAYER_ONE, buffer)
    # Player two's pool must be untouched.
    assert new_state.resource_pool_for(PLAYER_TWO) == state.resource_pool_for(PLAYER_TWO)


def test_launch_is_deterministic_and_replay_safe() -> None:
    world = _world()
    state = _state(session=_session())

    result_1 = launch_robot(state, world, PLAYER_ONE)
    result_2 = launch_robot(state, world, PLAYER_ONE)

    assert result_1.accepted and result_2.accepted
    assert result_1.robot == result_2.robot
    assert result_1.state == result_2.state


def test_launch_assigns_second_robot_next_ordinal() -> None:
    world = _world()
    existing = (_dummy_robot(PLAYER_ONE, 1, x=50, y=50),)
    state = _state(session=_session(), robots=existing)

    result = launch_robot(state, world, PLAYER_ONE)

    assert result.accepted
    assert result.robot is not None
    assert result.robot.entity_id == EntityId("robot-p1-2")
    assert result.state is not None
    assert {r.entity_id for r in result.state.robots} == {
        EntityId("robot-p1-1"),
        EntityId("robot-p1-2"),
    }


def _launch_and_walk_away(
    state: GameState, world: WorldMap, parking_x: int
) -> tuple[GameState, EntityId]:
    """Launch one p1 robot, then park it off the exit so the next launch is not blocked."""
    state = state.with_construction_sessions((_session(),))
    result = launch_robot(state, world, PLAYER_ONE)
    assert result.accepted and result.state is not None and result.robot is not None
    parked = dataclasses.replace(result.robot, x=parking_x, y=15)
    others = tuple(r for r in result.state.robots if r.entity_id != parked.entity_id)
    return result.state.with_robots((*others, parked)), parked.entity_id


def test_launch_never_reuses_a_robot_id_after_a_robot_dies() -> None:
    # Regression for #295: the ordinal was ``1 + robots alive``, so after a
    # death the next launch took a living robot's id (or a dead one's).
    world = _world()
    state = _state()
    issued: list[EntityId] = []
    for parking_x in (8, 11, 14):
        state, robot_id = _launch_and_walk_away(state, world, parking_x)
        issued.append(robot_id)
    assert issued == [EntityId(f"robot-p1-{n}") for n in (1, 2, 3)]

    state, _ = destroy_robot(state, EntityId("robot-p1-2"), tick=0)
    state, robot_id = _launch_and_walk_away(state, world, 11)
    issued.append(robot_id)

    state, _ = destroy_robot(state, robot_id, tick=0)  # the newest robot dies too
    state, robot_id = _launch_and_walk_away(state, world, 17)
    issued.append(robot_id)

    assert issued == [EntityId(f"robot-p1-{n}") for n in (1, 2, 3, 4, 5)]
    assert len({r.entity_id for r in state.robots}) == len(state.robots)


# --- No active session ---------------------------------------------------------


def test_launch_rejects_no_active_session() -> None:
    world = _world()
    state = _state()

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.NO_ACTIVE_SESSION
    assert result.state is None
    assert result.robot is None


# --- Invalid / incomplete build ----------------------------------------------


def test_launch_rejects_incomplete_build() -> None:
    world = _world()
    incomplete = BuildInProgress(chassis=ModuleIdentity.BIPOD)  # no weapons yet
    state = _state(session=_session(build=incomplete))

    pools_before = copy.deepcopy(state.resource_pools)
    robots_before = copy.deepcopy(state.robots)

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.INCOMPLETE_BUILD
    assert result.state is None
    assert result.robot is None
    # Atomicity: nothing changed.
    assert state.resource_pools == pools_before
    assert state.robots == robots_before


def test_launch_rejects_build_with_no_chassis() -> None:
    world = _world()
    incomplete = BuildInProgress(weapons=(ModuleIdentity.CANNON,))
    state = _state(session=_session(build=incomplete))

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.INCOMPLETE_BUILD


# --- Robot cap: boundary at 23 vs 24 ------------------------------------------


def test_launch_allowed_with_23_existing_robots() -> None:
    world = _world()
    existing = tuple(_dummy_robot(PLAYER_ONE, i, x=50 + 2 * i, y=50) for i in range(1, 24))
    assert len(existing) == 23
    state = _state(session=_session(), robots=existing)

    result = launch_robot(state, world, PLAYER_ONE)

    assert result.accepted
    assert result.state is not None
    assert len(result.state.robots_for(PLAYER_ONE)) == 24


def test_launch_rejected_with_24_existing_robots() -> None:
    world = _world()
    existing = tuple(_dummy_robot(PLAYER_ONE, i, x=50 + 2 * i, y=50) for i in range(1, 25))
    assert len(existing) == 24
    state = _state(session=_session(), robots=existing)

    pools_before = copy.deepcopy(state.resource_pools)
    robots_before = copy.deepcopy(state.robots)

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.ROBOT_CAP_REACHED
    assert result.state is None
    assert result.robot is None
    # Atomicity: nothing changed.
    assert state.resource_pools == pools_before
    assert state.robots == robots_before


def test_robot_cap_is_configurable_via_rules() -> None:
    world = _world()
    rules = EngineRules(max_robots_per_player=1)
    existing = (_dummy_robot(PLAYER_ONE, 1, x=50, y=50),)
    state = _state(session=_session(), robots=existing)

    result = launch_robot(state, world, PLAYER_ONE, rules=rules)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.ROBOT_CAP_REACHED


# --- Blocked vs free exit -----------------------------------------------------


@pytest.mark.parametrize("offset", [(1, 0), (-1, 0), (0, 1), (1, 1), (-1, 1)])
def test_launch_rejects_an_exit_overlapped_by_another_robots_body(offset: tuple[int, int]) -> None:
    """2×2 bodies (CR002.3): a robot anchored next to the exit still covers part of the new body.

    ``La6c8`` tests the ``bit 6`` robot marks of the anchors whose body would
    overlap the new robot's before ``Lc849_robot_construction_if_possible``.
    """
    world = _world()
    x, y = P1_EXIT_CELL[0] + offset[0], P1_EXIT_CELL[1] + offset[1]
    state = _state(session=_session(), robots=(_dummy_robot(PLAYER_TWO, 1, x=x, y=y),))

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.EXIT_BLOCKED


def test_launch_accepts_an_exit_with_a_robot_body_edge_to_edge() -> None:
    world = _world()
    beside = _dummy_robot(PLAYER_TWO, 1, x=P1_EXIT_CELL[0] + 2, y=P1_EXIT_CELL[1])
    state = _state(session=_session(), robots=(beside,))

    assert launch_robot(state, world, PLAYER_ONE).accepted


def test_launch_rejects_an_exit_whose_body_would_leave_the_map() -> None:
    """An exit anchor on row 0 would put the new body's upper row off the map."""
    point = InteractionPoint(
        id="warbase-p1-exit",
        kind=InteractionKind.EXIT,
        structure_id=WAR_BASE_ONE,
        footprint=Footprint(cells=frozenset({(5, 0)})),
    )
    world = _world(interaction_points=(point,))
    state = _state(session=_session())

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.EXIT_BLOCKED


def test_launch_rejects_blocked_exit() -> None:
    world = _world()
    blocker = _dummy_robot(PLAYER_TWO, 1, x=P1_EXIT_CELL[0], y=P1_EXIT_CELL[1])
    state = _state(session=_session(), robots=(blocker,))

    pools_before = copy.deepcopy(state.resource_pools)
    robots_before = copy.deepcopy(state.robots)

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.EXIT_BLOCKED
    assert result.state is None
    assert result.robot is None
    # Atomicity: nothing changed.
    assert state.resource_pools == pools_before
    assert state.robots == robots_before


def test_launch_rejects_an_exit_cell_reserved_by_an_in_flight_move() -> None:
    # Regression (issue #62/M5.3): a mover authoritatively occupies its
    # origin, so folded occupancy alone reports the exit cell as free while
    # a move is inbound to it; launching there would stack two robots on one
    # cell the moment that move completes.
    world = _world()
    mover = _dummy_robot(PLAYER_TWO, 1, x=P1_EXIT_CELL[0] - 2, y=P1_EXIT_CELL[1])
    state = _state(session=_session(), robots=(mover,))
    state, move_result, _event = apply_robot_move(
        RobotMoveRequest(entity_id=mover.entity_id, dx=1, dy=0), state, world, tick=0
    )
    assert move_result.accepted
    assert reservations_from_state(state).is_reserved(*P1_EXIT_CELL)
    assert not folded_robot_occupancy(world, state).is_occupied(*P1_EXIT_CELL)

    pools_before = copy.deepcopy(state.resource_pools)
    robots_before = copy.deepcopy(state.robots)
    # Sessions hold a read-only mapping (not deep-copyable); they are frozen
    # value objects, so capturing the tuple is enough to detect a mutation.
    sessions_before = state.construction_sessions

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.EXIT_BLOCKED
    assert result.state is None
    assert result.robot is None
    # Atomicity: nothing changed.
    assert state.resource_pools == pools_before
    assert state.robots == robots_before
    assert state.construction_sessions == sessions_before


def test_launch_succeeds_once_a_reserved_exit_cell_move_is_cancelled() -> None:
    world = _world()
    mover = _dummy_robot(PLAYER_TWO, 1, x=P1_EXIT_CELL[0] - 2, y=P1_EXIT_CELL[1])
    state = _state(session=_session(), robots=(mover,))
    state, _move_result, _event = apply_robot_move(
        RobotMoveRequest(entity_id=mover.entity_id, dx=1, dy=0), state, world, tick=0
    )
    state, _cancel_event = cancel_robot_move(state, mover.entity_id, tick=1)

    result = launch_robot(state, world, PLAYER_ONE)

    assert result.accepted
    assert result.robot is not None
    assert (result.robot.x, result.robot.y) == P1_EXIT_CELL


def test_launch_succeeds_with_free_exit_even_when_other_cells_occupied() -> None:
    world = _world()
    other_robot = _dummy_robot(PLAYER_TWO, 1, x=99, y=99)
    state = _state(session=_session(), robots=(other_robot,))

    result = launch_robot(state, world, PLAYER_ONE)

    assert result.accepted
    assert result.robot is not None
    assert (result.robot.x, result.robot.y) == P1_EXIT_CELL


def test_launch_rejects_when_exit_not_declared() -> None:
    war_bases = (
        WarBase(id=WAR_BASE_ONE, components=(Component(x=0, y=0, height=3),), owner=PLAYER_ONE),
    )
    world = _world(war_bases=war_bases, interaction_points=())
    state = _state(session=_session())

    result = launch_robot(state, world, PLAYER_ONE)

    assert not result.accepted
    assert result.reason is LaunchRejectionReason.NO_EXIT_DEFINED


def test_launch_resolves_first_declared_exit_point_and_smallest_cell() -> None:
    war_bases = (
        WarBase(id=WAR_BASE_ONE, components=(Component(x=0, y=0, height=3),), owner=PLAYER_ONE),
    )
    interaction_points = (
        InteractionPoint(
            id="warbase-p1-exit-a",
            kind=InteractionKind.EXIT,
            structure_id=WAR_BASE_ONE,
            footprint=Footprint(cells=frozenset({(3, 1), (2, 1)})),
        ),
        InteractionPoint(
            id="warbase-p1-exit-b",
            kind=InteractionKind.EXIT,
            structure_id=WAR_BASE_ONE,
            footprint=Footprint(cells=frozenset({(9, 9)})),
        ),
    )
    world = _world(war_bases=war_bases, interaction_points=interaction_points)
    state = _state(session=_session())

    result = launch_robot(state, world, PLAYER_ONE)

    assert result.accepted
    assert result.robot is not None
    # First declared EXIT point ("...-a"), smallest (x, y) cell within it.
    assert (result.robot.x, result.robot.y) == (2, 1)


# --- Result shape invariants ---------------------------------------------------


def test_launch_result_rejects_accepted_without_state_or_robot() -> None:
    from nether_earth.robot_launch import LaunchResult

    with pytest.raises(ValueError):
        LaunchResult(accepted=True)


def test_launch_result_rejects_rejected_with_state_or_robot() -> None:
    from nether_earth.robot_launch import LaunchResult

    with pytest.raises(ValueError):
        LaunchResult(accepted=False, reason=LaunchRejectionReason.NO_ACTIVE_SESSION, state=_state())
