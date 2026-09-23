"""Integration tests for capture/victory wiring into ``engine.step`` (issue #66, M5.7).

Exercises ``engine.step`` end to end (not the underlying pure ``capture.py``/
``victory.py`` functions directly): neutral factory acquisition, enemy
factory capture completion, capture interruption/reset, war-base capture
triggering victory evaluation in the same authoritative step, and replay
determinism across repeated runs with identical inputs.
"""

from __future__ import annotations

from nether_earth.capture import (
    CapturableStructureKind,
    StructureCapturedEvent,
)
from nether_earth.engine import new_game, step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.scenario import Scenario
from nether_earth.state import GameState
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid
from nether_earth.victory import VictoryEvent

WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")
FACTORY_ONE = EntityId("factory-1")
FACTORY_CAPTURE_CELL = (5, 5)
WARBASE_CAPTURE_CELL = (2, 2)


def _footprint(cell: tuple[int, int]) -> Footprint:
    return Footprint(cells=frozenset({cell}))


def _world(*, factory_owner: PlayerId | None) -> WorldMap:
    war_bases = (
        WarBase(id=WAR_BASE_ONE, components=(Component(x=0, y=0, height=3),), owner=PLAYER_ONE),
        WarBase(id=WAR_BASE_TWO, components=(Component(x=19, y=19, height=3),), owner=PLAYER_TWO),
    )
    factories = (
        Factory(
            id=FACTORY_ONE,
            components=(Component(x=6, y=6, height=3),),
            factory_type=FactoryType.CHASSIS,
            owner=factory_owner,
        ),
    )
    interaction_points = (
        InteractionPoint(
            id="factory-1-capture",
            kind=InteractionKind.FACTORY_CAPTURE,
            structure_id=FACTORY_ONE,
            footprint=_footprint(FACTORY_CAPTURE_CELL),
        ),
        InteractionPoint(
            id="warbase-p1-capture",
            kind=InteractionKind.WARBASE_CAPTURE,
            structure_id=WAR_BASE_ONE,
            footprint=_footprint(WARBASE_CAPTURE_CELL),
        ),
    )
    return WorldMap(
        map_id="test-capture-integration",
        version=1,
        width=20,
        height=20,
        terrain=TerrainGrid(width=20, height=20, cells={}),
        war_bases=war_bases,
        factories=factories,
        blockers=(),
        interaction_points=interaction_points,
        spawn_positions={},
    )


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="fixture-scenario",
        map_id="test-capture-integration",
        map_version=1,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id="test-capture-integration", version=1, width=20, height=20)
    return scenario, map_data


def _base_state() -> GameState:
    scenario, map_data = _scenario_and_map()
    return new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=1)


def _robot(entity_id: str, owner: PlayerId, x: int, y: int) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(entity_id=EntityId(entity_id), owner=owner, x=x, y=y, build=build, stack=stack, height=height)


def test_neutral_factory_needs_the_full_capture_duration_through_engine_step() -> None:
    # Owner decision, 2026-09-23 (`functional-spec.md` §9): a neutral factory
    # is no longer acquired on the first qualifying tick. It runs the same
    # continuous-occupation countdown as the enemy-owned case below, and
    # completes with an ordinary StructureCapturedEvent whose
    # ``previous_owner`` is ``None``.
    world = _world(factory_owner=None)
    robot = _robot("robot-p1-1", PLAYER_ONE, *FACTORY_CAPTURE_CELL)
    state = _base_state().with_robots((robot,))

    for _tick in range(1, DEFAULT_RULES.capture_duration_ticks):
        state, events = step(state, [], world=world)
        assert not any(isinstance(e, StructureCapturedEvent) for e in events)
        assert state.structure_ownership_for(FACTORY_ONE) is None

    state, events = step(state, [], world=world)

    captured = [e for e in events if isinstance(e, StructureCapturedEvent)]
    assert len(captured) == 1
    assert captured[0].structure_id == FACTORY_ONE
    assert captured[0].structure_kind is CapturableStructureKind.FACTORY
    assert captured[0].previous_owner is None
    assert captured[0].new_owner == PLAYER_ONE
    assert state.structure_ownership_for(FACTORY_ONE) is not None
    assert state.structure_ownership_for(FACTORY_ONE).owner == PLAYER_ONE  # type: ignore[union-attr]


def test_enemy_factory_capture_completes_and_ownership_transfers() -> None:
    # ``engine.step`` uses ``DEFAULT_RULES`` internally (this milestone
    # centralizes the capture-duration rule in ``EngineRules``, not a
    # per-``step``-call override plumbing point) -- non-default durations
    # are exercised directly against ``capture.py`` in ``test_capture.py``;
    # this integration test exercises the real default end to end through
    # ``engine.step``.
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _base_state().with_robots((robot,))

    for tick in range(1, DEFAULT_RULES.capture_duration_ticks):
        state, events = step(state, [], world=world)
        assert not any(isinstance(e, StructureCapturedEvent) for e in events)

    state, events = step(state, [], world=world)

    captured = [e for e in events if isinstance(e, StructureCapturedEvent)]
    assert len(captured) == 1
    assert captured[0].structure_id == FACTORY_ONE
    assert captured[0].new_owner == PLAYER_TWO
    assert captured[0].previous_owner == PLAYER_ONE
    assert state.structure_ownership_for(FACTORY_ONE) is not None
    assert state.structure_ownership_for(FACTORY_ONE).owner == PLAYER_TWO  # type: ignore[union-attr]
    assert state.capture_progress == ()


def test_capture_interruption_resets_progress_through_engine_step() -> None:
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = _base_state().with_robots((robot,))

    state, _events = step(state, [], world=world)
    assert state.capture_progress_for(FACTORY_ONE) is not None
    assert state.capture_progress_for(FACTORY_ONE).elapsed_ticks == 1  # type: ignore[union-attr]

    # Robot leaves before completion.
    moved = state.robot_for(EntityId("robot-p2-1")).with_position(0, 5)  # type: ignore[union-attr]
    state = state.with_robots((moved,))

    state, events = step(state, [], world=world)

    assert state.capture_progress_for(FACTORY_ONE) is None
    assert not any(isinstance(e, StructureCapturedEvent) for e in events)


def test_war_base_capture_triggers_victory_in_same_step() -> None:
    # WAR_BASE_ONE starts owned by PLAYER_ONE, WAR_BASE_TWO by PLAYER_TWO
    # (see ``_world``); PLAYER_TWO's robot capturing WAR_BASE_ONE leaves
    # PLAYER_ONE with zero war bases -- the locked victory condition.
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *WARBASE_CAPTURE_CELL)
    state = _base_state().with_robots((robot,))

    for _tick in range(1, DEFAULT_RULES.capture_duration_ticks):
        state, events = step(state, [], world=world)
        assert not any(isinstance(e, VictoryEvent) for e in events)

    state, events = step(state, [], world=world)

    captured = [
        e
        for e in events
        if isinstance(e, StructureCapturedEvent) and e.structure_kind is CapturableStructureKind.WAR_BASE
    ]
    assert len(captured) == 1
    victories = [e for e in events if isinstance(e, VictoryEvent)]
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_TWO
    assert victories[0].tick == state.tick


def test_no_victory_event_when_war_bases_not_all_captured() -> None:
    world = _world(factory_owner=None)
    state = _base_state()

    state, events = step(state, [], world=world)

    assert not any(isinstance(e, VictoryEvent) for e in events)


def test_capture_replay_is_deterministic() -> None:
    world = _world(factory_owner=PLAYER_ONE)
    robot = _robot("robot-p2-1", PLAYER_TWO, *FACTORY_CAPTURE_CELL)

    def run() -> tuple[GameState, list[object]]:
        state = _base_state().with_robots((robot,))
        all_events: list[object] = []
        for _tick in range(DEFAULT_RULES.capture_duration_ticks + 2):
            state, events = step(state, [], world=world)
            all_events.extend(events)
        return state, all_events

    state_a, events_a = run()
    state_b, events_b = run()

    assert state_a == state_b
    assert events_a == events_b


def test_engine_rules_capture_duration_ticks_is_configurable() -> None:
    assert EngineRules(capture_duration_ticks=1).capture_duration_ticks == 1
    assert DEFAULT_RULES.capture_duration_ticks == 1440
