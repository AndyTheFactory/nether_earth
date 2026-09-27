"""A Search & Destroy (robots) hunt keeps its order and re-plans periodically (CR004.13, #299).

Owner decision (2026-09-27): an electronics hunter never falls back to Stop &
Defend just because navigation finds no route this tick. It steps greedily
toward the target instead, follows a cached route between re-plans, and
re-plans every ``EngineRules.robot_hunt_replan_ticks`` ticks, or early when the
route runs out, its next cell becomes unenterable, or the target changes.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from nether_earth import navigation
from nether_earth.combat import ProjectileFiredEvent
from nether_earth.commander import Commander, CommanderMode
from nether_earth.docking import CommanderDockedEvent
from nether_earth.engine import step
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.orders import (
    OrderStatus,
    Retreat,
    RobotOrderChangedEvent,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
    apply_order_evaluations,
    apply_set_robot_order,
    evaluate_order,
    evaluate_orders,
)
from nether_earth.robot import Robot, RobotFacing, RobotHuntRoute, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.snapshot import robot_hunt_route_from_snapshot, to_snapshot
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType

HUNT = SearchDestroy(SearchDestroyTarget.ROBOT)
HUNTER = EntityId("robot-hunter")
TARGET = EntityId("robot-target")


def _world(
    width: int, height: int, walls: tuple[tuple[int, int], ...] = ()
) -> WorldMap:
    blockers = (
        (
            Blocker(
                id=EntityId("wall"),
                components=tuple(Component(x=x, y=y, height=4) for x, y in walls),
            ),
        )
        if walls
        else ()
    )
    return WorldMap(
        map_id="hunt-route-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(width=width, height=height, cells={}, default=TerrainType.NORMAL),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _corridor(length: int) -> WorldMap:
    """A one-lane corridor: rows 0 and 3 are wall, a 2x2 body fits only anchored at y=2
    (a body anchored at ``(x, y)`` covers rows ``y - 1`` and ``y``)."""
    walls = tuple((x, y) for x in range(length) for y in (0, 3))
    return _world(length, 4, walls)


def _robot(
    entity_id: EntityId,
    owner: PlayerId,
    x: int,
    y: int,
    *,
    chassis: ModuleIdentity = ModuleIdentity.BIPOD,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.PHASER,),
    electronics: ModuleIdentity | None = ModuleIdentity.ELECTRONICS,
    order: object = None,
    movement: RobotMoveTransition | None = None,
    facing: RobotFacing = RobotFacing.EAST,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons, electronics=electronics)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=entity_id,
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        movement=movement,
        order=order,  # type: ignore[arg-type]
        facing=facing,
    )


def _state(robots: tuple[Robot, ...], tick: int = 0) -> GameState:
    return create_game_state(tick, (PLAYER_ONE, PLAYER_TWO), robots=list(robots))


def _target_moving_west(x: int, *, order: object = None) -> Robot:
    """A tracked enemy mid-step from ``(x, 2)`` to ``(x - 1, 2)`` in the corridor."""
    return _robot(
        TARGET,
        PLAYER_TWO,
        x,
        2,
        chassis=ModuleIdentity.TRACKS,
        weapons=(ModuleIdentity.CANNON,),
        electronics=None,
        order=order,
        movement=RobotMoveTransition(
            entity_id=TARGET,
            from_x=x,
            from_y=2,
            to_x=x - 1,
            to_y=2,
            started_tick=0,
            duration_ticks=DEFAULT_RULES.robot_move_ticks_tracks_normal,
        ),
        facing=RobotFacing.WEST,
    )


# --------------------------------------------------------------------------
# 1. Regression: the observed corridor case (#299)
# --------------------------------------------------------------------------


def test_a_target_plugging_a_corridor_does_not_end_the_hunt() -> None:
    """The target's in-flight footprint removes every near-side approach cell.

    Before CR004.13 electronic navigation proved the target UNREACHABLE and
    the order fell back to Stop & Defend. The hunt must now stay ACTIVE and
    step toward the target.
    """
    world = _corridor(24)
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 2, order=HUNT)
    target = _target_moving_west(10)

    evaluation = evaluate_order(hunter, _state((hunter, target)), world)

    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert evaluation.order == HUNT
    assert evaluation.request is not None
    assert (evaluation.request.dx, evaluation.request.dy) == (1, 0)


def _hunter_of(state: GameState) -> Robot:
    robot = state.robot_for(HUNTER)
    assert robot is not None
    return robot


def test_the_corridor_hunter_keeps_closing_in_until_it_engages() -> None:
    """Over the following ticks the hunter keeps its order, closes, and fires."""
    world = _corridor(40)
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 2, order=HUNT)
    target = _target_moving_west(30, order=Retreat(50))
    state = _state((hunter, target))

    events: list[Event] = []
    closest_x = hunter.x
    for _ in range(600):
        state, tick_events = step(state, (), world)
        events.extend(tick_events)
        robot = _hunter_of(state)
        assert robot.order == HUNT
        closest_x = max(closest_x, robot.x)
        if any(
            isinstance(event, ProjectileFiredEvent) and event.source_robot_id == HUNTER
            for event in tick_events
        ):
            break

    fallbacks = [
        event
        for event in events
        if isinstance(event, RobotOrderChangedEvent)
        and event.entity_id == HUNTER
        and event.status is OrderStatus.FALLBACK
    ]
    assert fallbacks == []
    assert closest_x > hunter.x
    assert any(
        isinstance(event, ProjectileFiredEvent) and event.source_robot_id == HUNTER
        for event in events
    )


# --------------------------------------------------------------------------
# 3. An unreachable target: greedy steps, never a fallback
# --------------------------------------------------------------------------


def _walled_in_target_world() -> WorldMap:
    """A 2x2 target at (20, 5) (body rows 4-5) boxed in by a one-cell wall ring on a 30x12 map."""
    ring = tuple(
        (x, y)
        for x in range(19, 23)
        for y in range(3, 7)
        if x in (19, 22) or y in (3, 6)
    )
    return _world(30, 12, ring)


def test_an_unreachable_target_is_approached_greedily_and_never_abandoned() -> None:
    world = _walled_in_target_world()
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT)
    target = _robot(
        TARGET, PLAYER_TWO, 20, 5, weapons=(ModuleIdentity.CANNON,), electronics=None
    )
    state = _state((hunter, target))

    first = evaluate_order(hunter, state, world)
    assert first is not None
    assert first.status is OrderStatus.ACTIVE
    assert first.request is not None
    assert (first.request.dx, first.request.dy) == (1, 0)

    closest_x = hunter.x
    for _ in range(400):
        state, _events = step(state, (), world)
        if state.robot_for(TARGET) is None:
            break  # shot through the wall: no candidate left is the one fallback
        robot = _hunter_of(state)
        assert robot.order == HUNT
        assert robot.hunt_route is not None and robot.hunt_route.steps is None
        closest_x = max(closest_x, robot.x)
    assert closest_x > hunter.x


# --------------------------------------------------------------------------
# 4. No enemies: the fallback is unchanged
# --------------------------------------------------------------------------


def test_a_hunt_with_no_enemy_robot_still_falls_back() -> None:
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT)
    evaluation = evaluate_order(hunter, _state((hunter,)), _world(20, 12))
    assert evaluation is not None
    assert evaluation.status is OrderStatus.FALLBACK


# --------------------------------------------------------------------------
# 2. Re-plan cadence
# --------------------------------------------------------------------------

REPLAN = DEFAULT_RULES.robot_hunt_replan_ticks


class _PlanCounter:
    """Counts route searches by wrapping ``navigation.plan_route_to_any``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        real = navigation.plan_route_to_any

        def counting(*args: object, **kwargs: object) -> object:
            self.calls += 1
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(navigation, "plan_route_to_any", counting)


def _apply(state: GameState, world: WorldMap) -> GameState:
    """Evaluate every order once and write the results back (no tick advance)."""
    applied, _events = apply_order_evaluations(
        evaluate_orders(state, world), state, state.tick
    )
    return applied


def test_a_hunter_with_a_valid_route_plans_at_most_once_per_replan_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An anti-grav hunter steps every 12 ticks, faster than the 20-tick interval.

    Before CR004.13 every idle update re-planned, so a chassis quicker than
    the interval planned more often than once per interval; a bipod (24
    ticks per cell) would not tell the two rules apart.
    """
    assert DEFAULT_RULES.robot_move_ticks_anti_grav_normal < REPLAN
    world = _world(60, 12)
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, chassis=ModuleIdentity.ANTI_GRAV, order=HUNT)
    # 48 cells away: out of every weapon's range, so the hunter only walks.
    target = _robot(
        TARGET, PLAYER_TWO, 50, 5, weapons=(ModuleIdentity.CANNON,), electronics=None
    )
    state = _state((hunter, target))
    counter = _PlanCounter(monkeypatch)

    ticks = 5 * REPLAN
    plan_ticks: list[int] = []
    for _ in range(ticks):
        state, _events = step(state, (), world)
        route = _hunter_of(state).hunt_route
        if route is not None and (not plan_ticks or plan_ticks[-1] != route.planned_tick):
            plan_ticks.append(route.planned_tick)

    robot = _hunter_of(state)
    assert robot.x > hunter.x + 3  # it followed the route
    assert robot.order == HUNT
    # One plan at the first tick, then at most one per full interval. A due
    # re-plan waits for the move in flight to land, so gaps may exceed it.
    assert 1 <= counter.calls <= ticks // REPLAN
    assert plan_ticks[0] == 0
    assert len(plan_ticks) == counter.calls
    assert all(later - earlier >= REPLAN for earlier, later in pairwise(plan_ticks))


def test_a_new_target_forces_an_early_replan(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(60, 12)
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT)
    far = _robot(TARGET, PLAYER_TWO, 50, 5, weapons=(ModuleIdentity.CANNON,), electronics=None)
    state = _apply(_state((hunter, far)), world)
    planned = _hunter_of(state).hunt_route
    assert planned is not None and planned.target_id == TARGET

    counter = _PlanCounter(monkeypatch)
    later = state.with_tick(state.tick + 1)
    assert evaluate_order(_hunter_of(later), later, world) is not None
    assert counter.calls == 0  # same target, fresh route: no plan

    near_id = EntityId("robot-near")
    near = _robot(near_id, PLAYER_TWO, 30, 5, weapons=(ModuleIdentity.CANNON,), electronics=None)
    switched = later.with_robots((*later.robots, near))
    evaluation = evaluate_order(_hunter_of(switched), switched, world)
    assert evaluation is not None
    assert counter.calls == 1
    assert evaluation.hunt_route is not None
    assert evaluation.hunt_route.target_id == near_id
    assert evaluation.hunt_route.planned_tick == switched.tick


def test_an_unenterable_next_cell_forces_one_early_replan_around_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(60, 12)
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT)
    target = _robot(TARGET, PLAYER_TWO, 50, 5, weapons=(ModuleIdentity.CANNON,), electronics=None)
    state = _apply(_state((hunter, target)), world)
    cached = _hunter_of(state).hunt_route
    assert cached is not None and cached.steps
    next_cell = cached.cells()[0]

    # A friendly robot parks just beyond the hunter, across the route's next cell.
    blocker = _robot(
        EntityId("robot-friend"), PLAYER_ONE, next_cell[0] + 1, next_cell[1], electronics=None
    )
    blocked = state.with_tick(state.tick + 1).with_robots((*state.robots, blocker))
    counter = _PlanCounter(monkeypatch)
    evaluation = evaluate_order(_hunter_of(blocked), blocked, world)

    assert evaluation is not None
    assert evaluation.status is OrderStatus.ACTIVE
    assert counter.calls == 1
    assert evaluation.hunt_route is not None
    assert evaluation.hunt_route.planned_tick == blocked.tick
    assert evaluation.hunt_route.cells()[0] != next_cell
    assert evaluation.request is not None


def test_a_hunter_with_no_route_waits_for_the_next_replan_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked hunt does not re-plan every tick: once per interval."""
    world = _corridor(24)
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 2, order=HUNT)
    target = _target_moving_west(10)
    counter = _PlanCounter(monkeypatch)

    state = _apply(_state((hunter, target)), world)
    cached = _hunter_of(state).hunt_route
    assert cached is not None and cached.steps is None  # no route this tick
    first_plan = counter.calls
    assert first_plan >= 1

    for offset in range(1, REPLAN):
        later = state.with_tick(state.tick + offset)
        evaluation = evaluate_order(_hunter_of(later), later, world)
        assert evaluation is not None and evaluation.status is OrderStatus.ACTIVE
        assert evaluation.hunt_route == cached
    assert counter.calls == first_plan

    due = state.with_tick(state.tick + REPLAN)
    evaluation = evaluate_order(_hunter_of(due), due, world)
    assert evaluation is not None
    assert counter.calls > first_plan
    assert evaluation.hunt_route is not None
    assert evaluation.hunt_route.planned_tick == due.tick


# --------------------------------------------------------------------------
# Cache lifetime: cleared on any order change, fallback, or docking
# --------------------------------------------------------------------------


def _cached_route() -> RobotHuntRoute:
    return RobotHuntRoute(
        target_id=TARGET, planned_tick=0, origin_x=2, origin_y=5, steps="EEN"
    )


def test_any_new_order_clears_the_cached_route() -> None:
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT).with_hunt_route(_cached_route())
    target = _robot(TARGET, PLAYER_TWO, 20, 5, electronics=None)
    state = _state((hunter, target))
    for order in (StopAndDefend(), HUNT):
        updated, _event = apply_set_robot_order(
            SetRobotOrderCommand(player=PLAYER_ONE, sequence=1, entity_id=HUNTER, order=order),
            state,
            tick=1,
        )
        robot = updated.robot_for(HUNTER)
        assert robot is not None and robot.hunt_route is None


def test_a_fallback_clears_the_cached_route() -> None:
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT).with_hunt_route(_cached_route())
    state = _apply(_state((hunter,)), _world(20, 12))
    robot = _hunter_of(state)
    assert robot.order == StopAndDefend()
    assert robot.hunt_route is None


def test_docking_clears_the_cached_route() -> None:
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT).with_hunt_route(_cached_route())
    target = _robot(TARGET, PLAYER_TWO, 40, 5, electronics=None)
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.FREE,
        x=2,
        y=5,  # resting on the hunter's own anchor: it docks this tick
        altitude=hunter.height,
    )
    state = create_game_state(
        0, (PLAYER_ONE, PLAYER_TWO), robots=[hunter, target], commanders=[commander]
    )
    state, events = step(state, (), _world(60, 12))
    assert any(isinstance(event, CommanderDockedEvent) for event in events)
    assert _hunter_of(state).hunt_route is None


# --------------------------------------------------------------------------
# 5. Snapshot round-trip and elision
# --------------------------------------------------------------------------


def test_a_robot_without_a_cached_route_has_no_hunt_route_key() -> None:
    hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT)
    entry = to_snapshot(_state((hunter,)))["robots"][0]
    assert "hunt_route" not in entry


def test_the_cached_route_round_trips_through_the_snapshot() -> None:
    for route in (
        _cached_route(),
        RobotHuntRoute(target_id=TARGET, planned_tick=7, origin_x=3, origin_y=4, steps=None),
        RobotHuntRoute(target_id=TARGET, planned_tick=9, origin_x=3, origin_y=4, steps=""),
    ):
        hunter = _robot(HUNTER, PLAYER_ONE, 2, 5, order=HUNT).with_hunt_route(route)
        entry = to_snapshot(_state((hunter,)))["robots"][0]
        assert list(entry)[-1] == "hunt_route"
        assert entry["hunt_route"] == {
            "target_id": "robot-target",
            "planned_tick": route.planned_tick,
            "origin_x": route.origin_x,
            "origin_y": route.origin_y,
            "steps": route.steps,
        }
        assert robot_hunt_route_from_snapshot(entry["hunt_route"]) == route


def test_route_cells_encode_and_decode_as_direction_letters() -> None:
    cells = ((3, 5), (4, 5), (4, 4), (4, 3), (3, 3), (3, 4))
    route = RobotHuntRoute.from_cells(TARGET, 0, (2, 5), cells)
    assert route.steps == "EENNWS"
    assert route.cells() == cells
    assert RobotHuntRoute.from_cells(TARGET, 0, (2, 5), None).steps is None
    with pytest.raises(ValueError):
        RobotHuntRoute.from_cells(TARGET, 0, (2, 5), ((4, 5),))
    with pytest.raises(ValueError):
        RobotHuntRoute(TARGET, 0, 2, 5, "EX")
