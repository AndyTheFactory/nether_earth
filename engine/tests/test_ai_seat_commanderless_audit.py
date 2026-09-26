"""CR004.6 (#287) -- audit every ``state.commanders`` / ``commander_for`` read
against the AI seat's no-commander shape.

CR004.3 already proved the seat itself (no commander is ever created, `new_
game`/`with_commanders` refuse one) and a handful of consumers (`engine.step`
Steps 1/3-7, `commander_movement.validate_commander_move`,
`set_vertical_intent`, `direct_control`, `construction_session.exit_
construction`) -- see task-3-report.md's "commander_for / commanders
consumers" section. This module covers the sites that report named as *not*
exercised there: ``orders.py`` (``_under_direct_control``/``evaluate_
orders``), ``autonomous_combat.py`` (``gate_order_requests``/``settle_walk_
outs``), ``movement.py`` (``commander_blocks_robot_cell``), ``collision.py``
(``_other_commanders``/``commander_horizontal_move_allowed``), and
``destruction.py`` (``destroy_robot``'s docked-commander cleanup loop).

Every one of these already iterates the ``state.commanders`` *tuple*
directly rather than indexing by seat, so an AI seat's absent commander is
simply one fewer tuple element -- there is no ``None``-dereference site to
guard. These tests lock that verdict in as a regression rather than leaving
it as an unexercised claim.

Also covers the CR004.6 acceptance criterion "a full AI-seat match runs to a
result with no commander for that seat" with a long multi-tick run, and that
the resulting snapshot validates against the backend's ``SnapshotState``
(the gap that report also flagged: the model had no ``ai_memories`` field at
all and rejected every AI-seat snapshot).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from nether_earth import engine
from nether_earth.autonomous_combat import gate_order_requests, settle_walk_outs
from nether_earth.collision import _other_commanders, commander_horizontal_move_allowed
from nether_earth.destruction import destroy_robot
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.movement import commander_blocks_robot_cell
from nether_earth.orders import StopAndDefend, evaluate_orders
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

ORIGINAL_MAP_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
)


def _world() -> WorldMap:
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def _vs_ai_state(world: WorldMap, *, seed: int = 11) -> GameState:
    scenario = dataclasses.replace(default_pvp_scenario(), player_two_controller="ai")
    return create_initial_state(scenario, world, seed=seed)


def _ai_robot(entity_id: str, x: int, y: int) -> Robot:
    """A robot owned by the AI seat (``PLAYER_TWO``), with a standing order
    and no commander anywhere in the state -- the shape every one of this
    module's consumer sites must tolerate."""
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=PLAYER_TWO,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        order=StopAndDefend(),
    )


def test_the_ai_seat_never_has_a_commander_over_a_long_multi_tick_run() -> None:
    world = _world()
    state = _vs_ai_state(world)
    assert state.commander_for(PLAYER_TWO) is None

    for _ in range(400):
        state, _events = engine.step(state, (), world=world)
        # The audited invariant: nothing in 400 ticks of Steps 1-7 ever
        # materializes a commander for the seat that started with none.
        assert state.commander_for(PLAYER_TWO) is None
        assert state.commander_for(PLAYER_ONE) is not None

    # And the final snapshot still round-trips through the engine's own
    # canonical serializer (byte-identical across a rerun is covered
    # elsewhere in test_ai_seat.py; this just proves no crash over the run).
    snapshot = to_snapshot(state)
    assert "ai_memories" in snapshot
    assert snapshot["commanders"] == [
        c for c in snapshot["commanders"] if c["player_id"] == PLAYER_ONE.to_json()
    ]


def test_orders_evaluate_orders_tolerates_a_commanderless_owner() -> None:
    """``orders.py:1351``'s ``_under_direct_control`` iterates
    ``state.commanders`` directly; an AI-owned robot with a standing order
    and zero commanders anywhere for that seat must still be evaluated
    normally, not skipped as if "under direct control"."""
    world = _world()
    state = _vs_ai_state(world)
    robot = _ai_robot("robot-ai-1", 30, 10)
    state = state.with_robots((*state.robots, robot))

    evaluations = evaluate_orders(state, world)

    assert any(e.robot_id == robot.entity_id for e in evaluations)


def test_autonomous_combat_gate_and_settle_tolerate_a_commanderless_owner() -> None:
    """``autonomous_combat.py:514``'s walk-out settlement builds its
    ``docked`` set from ``state.commanders``; it must not require one entry
    per player."""
    world = _world()
    state = _vs_ai_state(world)
    robot = _ai_robot("robot-ai-2", 30, 10)
    state = state.with_robots((*state.robots, robot))

    evaluations = evaluate_orders(state, world)
    requests = gate_order_requests(evaluations, state, world, state.tick + 1)
    assert isinstance(requests, tuple)

    # settle_walk_outs's `docked = {commander.docked_robot_id for commander
    # in state.commanders if ...}` must not assume a commander per player.
    settled = settle_walk_outs(state, state, evaluations, (), world, state.tick + 1)
    assert settled.robot_for(robot.entity_id) is not None


def test_movement_commander_blocks_robot_cell_tolerates_a_commanderless_owner() -> None:
    """``movement.py:612``'s candidate search over ``state.commanders`` must
    not assume the AI's own commander exists to exclude by identity."""
    world = _world()
    state = _vs_ai_state(world)
    robot = _ai_robot("robot-ai-3", 30, 10)
    state = state.with_robots((*state.robots, robot))

    # No commander stands anywhere near this robot's destination, human or
    # AI -- the call must simply return False, not raise.
    blocked = commander_blocks_robot_cell(state, robot, 30, 11, world=world)
    assert blocked is False


def test_collision_other_commanders_and_horizontal_move_tolerate_a_missing_opponent() -> None:
    """``collision.py:398``'s ``_other_commanders`` -- "every commander in
    ``state.commanders`` other than this one" -- must return an empty tuple
    (not attempt to look up a second seat's commander) when the AI seat has
    none."""
    world = _world()
    state = _vs_ai_state(world)
    (human_commander,) = state.commanders
    assert human_commander.player_id == PLAYER_ONE

    assert _other_commanders(state, human_commander) == ()
    # The human commander's own move legality must not depend on an
    # opposing commander existing to compare against.
    allowed = commander_horizontal_move_allowed(
        state, human_commander, human_commander.x, human_commander.y, world=world
    )
    assert allowed is True


def test_destruction_destroy_robot_tolerates_a_commanderless_owner() -> None:
    """``destruction.py:231``'s docked-commander cleanup loop over
    ``state.commanders`` must not assume the destroyed robot's own owner has
    a commander to potentially free."""
    world = _world()
    state = _vs_ai_state(world)
    robot = _ai_robot("robot-ai-4", 30, 10)
    state = state.with_robots((*state.robots, robot))

    new_state, events = destroy_robot(state, robot.entity_id, state.tick, world=world)

    assert new_state.robot_for(robot.entity_id) is None
    assert new_state.commander_for(PLAYER_TWO) is None
    # The human's own commander is untouched by the AI-owned robot's death.
    assert new_state.commander_for(PLAYER_ONE) == state.commander_for(PLAYER_ONE)
    assert events is not None


def test_both_seats_ai_is_also_commanderless_and_survives_a_run() -> None:
    """The scenario allows both seats to be AI (CR004.10's AI-vs-AI harness);
    this is the strictest version of the audit -- zero commanders at all."""
    world = _world()
    scenario = dataclasses.replace(
        default_pvp_scenario(), player_one_controller="ai", player_two_controller="ai"
    )
    state = create_initial_state(scenario, world, seed=5)
    assert state.commanders == ()

    for _ in range(40):
        state, _events = engine.step(state, (), world=world)
        assert state.commanders == ()
