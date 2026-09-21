"""M9.5 -- cross-system edge cases on the real map (issue #117), engine level.

Every case composes at least two subsystems (commander/robot/launch/capture/
combat/destruction/victory) on the canonical scenario world, driven purely
through ``engine.step``. Runtime cases (pause/reconnect/forfeit/no-contest)
live in ``backend/tests`` (M7 integration + ``tests/acceptance``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nether_earth import engine
from nether_earth.capture import StructureCapturedEvent
from nether_earth.combat import FireCommand, ProjectileTerminatedEvent, RobotDamagedEvent
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import CommanderSetVerticalIntentCommand
from nether_earth.commands import Command
from nether_earth.construction_commands import (
    LaunchRobotCommand,
    RobotLaunchedEvent,
    SelectModuleCommand,
)
from nether_earth.destruction import RobotDestroyedEvent, StructureDestroyedEvent
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.events import Event
from nether_earth.heli_pad import heli_pad_surface_altitude
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.movement import RobotMoveStartedEvent
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import create_initial_state, default_pvp_scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState
from nether_earth.victory import VictoryEvent

ORIGINAL_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
PROJECTILE_ALTITUDE = DEFAULT_RULES.normal_projectile_altitude
CAPTURE_TICKS = DEFAULT_RULES.capture_duration_ticks


@pytest.fixture(scope="module")
def world() -> WorldMap:
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def _initial(world: WorldMap, seed: int = 1) -> GameState:
    return create_initial_state(default_pvp_scenario(), world, seed=seed)


def _robot(
    entity_id: str,
    owner: PlayerId,
    x: int,
    y: int,
    *,
    chassis: ModuleIdentity = ModuleIdentity.TRACKS,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    electronics: ModuleIdentity | None = None,
    order: object | None = None,
) -> Robot:
    build = RobotBuild(chassis=chassis, weapons=weapons, electronics=electronics)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        order=order,  # type: ignore[arg-type]
    )


def _tall(entity_id: str, owner: PlayerId, x: int, y: int, **kwargs: object) -> Robot:
    """TRACKS + cannon + missile + phaser = height 10 = hittable."""
    return _robot(
        entity_id,
        owner,
        x,
        y,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        **kwargs,  # type: ignore[arg-type]
    )


def _docked(state: GameState, player: PlayerId, robot: Robot) -> GameState:
    commander = state.commander_for(player)
    assert commander is not None
    docked = replace(
        commander, mode=CommanderMode.DOCKED, x=robot.x, y=robot.y, altitude=robot.height, docked_robot_id=robot.entity_id
    )
    return state.with_commanders(tuple(docked if c.player_id == player else c for c in state.commanders))


def _step(
    state: GameState, world: WorldMap, commands: tuple[Command, ...] = (), ticks: int = 1
) -> tuple[GameState, list[Event]]:
    collected: list[Event] = []
    for _ in range(ticks):
        state, events = engine.step(state, commands, world=world)
        collected.extend(events)
        commands = ()
    return state, collected


def _events(events: list[Event], event_type: type) -> list:
    return [event for event in events if isinstance(event, event_type)]


def _cell(world: WorldMap, structure: str, kind: InteractionKind) -> tuple[int, int]:
    return min(world.interaction_points_for(EntityId(structure), kind=kind)[0].footprint.cells)


def _commander_on_pad_with_session(state: GameState, world: WorldMap, player: PlayerId, base: str) -> GameState:
    pad = _cell(world, base, InteractionKind.HELI_PAD)
    commander = state.commander_for(player)
    assert commander is not None
    moved = replace(commander, x=pad[0], y=pad[1], altitude=heli_pad_surface_altitude(world, *pad))
    state = state.with_commanders(tuple(moved if c.player_id == player else c for c in state.commanders))
    state, _ = _step(state, world)
    assert state.construction_session_for(player) is not None
    return state


# -- blocked war-base exit during launch, then unblock/retry --------------------------


def test_blocked_exit_rejects_launch_until_the_exit_clears(world: WorldMap) -> None:
    state = _commander_on_pad_with_session(_initial(world), world, PLAYER_ONE, "warbase-1")
    exit_cell = _cell(world, "warbase-1", InteractionKind.EXIT)
    blocker = _robot("blocker", PLAYER_TWO, *exit_cell)
    state = state.with_robots((blocker,))

    state, _ = _step(state, world, (SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD),))
    state, _ = _step(state, world, (SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON),))
    state, events = _step(state, world, (LaunchRobotCommand(player=PLAYER_ONE, sequence=2),))
    # Gameplay-level rejections are silent by engine contract: no launch event, no robot.
    assert not _events(events, RobotLaunchedEvent)
    assert state.robots_for(PLAYER_ONE) == ()
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None and session.build.chassis is ModuleIdentity.BIPOD  # session intact
    pool = state.resource_pool_for(PLAYER_ONE)
    assert pool is not None and pool.general == 20  # nothing committed

    # Unblock: move the blocker one cell away, retry the very same launch.
    state = state.with_robots((replace(blocker, x=exit_cell[0] + 1),))
    state, events = _step(state, world, (LaunchRobotCommand(player=PLAYER_ONE, sequence=3),))
    assert len(_events(events, RobotLaunchedEvent)) == 1
    launched = state.robots_for(PLAYER_ONE)
    assert len(launched) == 1 and (launched[0].x, launched[0].y) == exit_cell
    pool = state.resource_pool_for(PLAYER_ONE)
    assert pool is not None and pool.general == 15


# -- low commander obstructing robot movement ----------------------------------------


def test_grounded_commander_blocks_a_robot_until_it_rises_clear(world: WorldMap) -> None:
    state = _initial(world)
    robot = _robot("mover", PLAYER_ONE, 30, 15)
    state = state.with_robots((robot,))
    state = _docked(state, PLAYER_ONE, robot)
    # Player 2's commander squats, grounded, on the destination cell.
    c2 = state.commander_for(PLAYER_TWO)
    assert c2 is not None
    state = state.with_commanders(tuple(replace(c, x=31, y=15, altitude=0) if c.player_id == PLAYER_TWO else c for c in state.commanders))

    state, events = _step(state, world, (DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),))
    assert not _events(events, RobotMoveStartedEvent)
    assert state.robot_for(EntityId("mover")).movement is None  # type: ignore[union-attr]

    # Raise Player 2 above the robot's top: the same move now starts.
    state = state.with_commanders(
        tuple(replace(c, altitude=robot.height) if c.player_id == PLAYER_TWO else c for c in state.commanders)
    )
    state, events = _step(state, world, (DirectRobotMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0),))
    started = _events(events, RobotMoveStartedEvent)
    assert len(started) == 1 and (started[0].to_x, started[0].to_y) == (31, 15)


# -- commander-vs-commander vertical collision ----------------------------------------


def test_commander_cannot_descend_onto_the_other_commander(world: WorldMap) -> None:
    state = _initial(world)
    state = state.with_commanders(
        (
            Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=100, y=15, altitude=0),
            Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=100, y=15, altitude=12),
        )
    )
    state, _ = _step(state, world, ticks=200)  # gravity: -1 every 4 ticks
    c1, c2 = state.commander_for(PLAYER_ONE), state.commander_for(PLAYER_TWO)
    assert c1 is not None and c2 is not None
    assert c1.altitude == 0
    assert c2.altitude == DEFAULT_RULES.commander_height  # resting exactly on top, never inside
    # And Player 1 cannot rise into Player 2.
    state, _ = _step(state, world, (CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=0, rising=True),), ticks=40)
    assert state.commander_for(PLAYER_ONE).altitude == 0  # type: ignore[union-attr]


# -- simultaneous destination claims with seeded contention ------------------------


def _contention_state(world: WorldMap, seed: int) -> GameState:
    state = _initial(world, seed=seed)
    left = _robot("left", PLAYER_ONE, 200, 15)
    right = _robot("right", PLAYER_TWO, 202, 15)
    state = state.with_robots((left, right))
    state = _docked(state, PLAYER_ONE, left)
    state = _docked(state, PLAYER_TWO, right)
    return state


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_same_tick_destination_claims_are_exclusive_and_seed_deterministic(world: WorldMap, seed: int) -> None:
    commands = (
        DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),
        DirectRobotMoveCommand(player=PLAYER_TWO, sequence=0, dx=-1, dy=0),
    )
    first, events = _step(_contention_state(world, seed), world, commands)
    started = _events(events, RobotMoveStartedEvent)
    assert len(started) == 1, "exactly one contender may claim (201, 15)"
    assert (started[0].to_x, started[0].to_y) == (201, 15)
    second, _ = _step(_contention_state(world, seed), world, commands)
    assert to_snapshot(first) == to_snapshot(second)
    # The loser may retry once the winner's move completes.
    final, _ = _step(first, world, ticks=DEFAULT_RULES.robot_move_ticks_tracks_normal + 1)
    positions = {r.entity_id.value: (r.x, r.y) for r in final.robots}
    assert len(set(positions.values())) == 2 and (201, 15) in positions.values()


def test_contention_outcome_varies_with_seed(world: WorldMap) -> None:
    winners = set()
    for seed in range(1, 25):
        _, events = _step(
            _contention_state(world, seed),
            world,
            (
                DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),
                DirectRobotMoveCommand(player=PLAYER_TWO, sequence=0, dx=-1, dy=0),
            ),
        )
        winners.add(_events(events, RobotMoveStartedEvent)[0].entity_id.value)
    assert winners == {"left", "right"}


# -- capture interruption resets progress ------------------------------------------


def test_leaving_the_capture_cell_resets_progress_to_zero(world: WorldMap) -> None:
    cell = _cell(world, "warbase-2", InteractionKind.WARBASE_CAPTURE)
    state = _initial(world)
    robot = _robot("capturer", PLAYER_ONE, *cell)
    state = state.with_robots((robot,))
    state = _docked(state, PLAYER_ONE, robot)
    state, _ = _step(state, world, ticks=CAPTURE_TICKS // 2)
    progress = state.capture_progress_for(EntityId("warbase-2"))
    assert progress is not None and 0 < progress.elapsed_ticks < CAPTURE_TICKS

    # Interrupt: drive one cell off the footprint.
    state, events = _step(state, world, (DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0),))
    assert _events(events, RobotMoveStartedEvent)
    state, _ = _step(state, world, ticks=DEFAULT_RULES.robot_move_ticks_tracks_normal)
    assert state.capture_progress_for(EntityId("warbase-2")) is None
    assert state.structure_ownership_for(EntityId("warbase-2")) is None

    # Return: progress restarts from zero -- full duration again.
    state, _ = _step(state, world, (DirectRobotMoveCommand(player=PLAYER_ONE, sequence=1, dx=-1, dy=0),))
    state, _ = _step(state, world, ticks=DEFAULT_RULES.robot_move_ticks_tracks_normal)
    assert (state.robot_for(EntityId("capturer")).x, state.robot_for(EntityId("capturer")).y) == cell  # type: ignore[union-attr]
    state, events = _step(state, world, ticks=CAPTURE_TICKS // 2)
    assert not _events(events, StructureCapturedEvent)
    state, events = _step(state, world, ticks=CAPTURE_TICKS // 2 + 2)
    assert [e.structure_id.value for e in _events(events, StructureCapturedEvent)] == ["warbase-2"]
    assert state.structure_ownership_for(EntityId("warbase-2")).owner == PLAYER_ONE  # type: ignore[union-attr]


# -- ownership transfer and victory in the same authoritative step -------------------


def test_capturing_the_last_enemy_war_base_wins_in_the_same_tick(world: WorldMap) -> None:
    cell = _cell(world, "warbase-4", InteractionKind.WARBASE_CAPTURE)
    state = _initial(world).with_robots((_robot("raider", PLAYER_ONE, *cell),))
    state, events = _step(state, world, ticks=CAPTURE_TICKS + 2)
    captured = _events(events, StructureCapturedEvent)
    victories = _events(events, VictoryEvent)
    assert len(captured) == 1 and captured[0].structure_id.value == "warbase-4"
    assert captured[0].previous_owner == PLAYER_TWO and captured[0].new_owner == PLAYER_ONE
    assert len(victories) == 1 and victories[0].winner == PLAYER_ONE
    assert victories[0].tick == captured[0].tick
    owners = {r.structure_id.value: r.owner for r in state.structure_ownership}
    assert not any(owner == PLAYER_TWO for owner in owners.values())


# -- projectile collision against height-aware world/robot state ---------------------


def test_short_robot_is_flown_over_and_tall_robot_is_hit(world: WorldMap) -> None:
    shooter = _tall("shooter", PLAYER_TWO, 320, 15)
    short = _robot("short", PLAYER_ONE, 314, 15)  # height 6 < projectile altitude 10
    tall = _tall("tall", PLAYER_ONE, 310, 15)
    state = _initial(world).with_robots((shooter, short, tall))
    assert short.height < PROJECTILE_ALTITUDE <= tall.height
    fire = FireCommand(player=PLAYER_TWO, sequence=0, entity_id=EntityId("shooter"), weapon=ModuleIdentity.CANNON, target_x=300, target_y=15)
    state, events = _step(state, world, (fire,), ticks=60)
    damaged = _events(events, RobotDamagedEvent)
    assert [d.entity_id.value for d in damaged] == ["tall"]
    terminated = _events(events, ProjectileTerminatedEvent)
    assert len(terminated) == 1 and terminated[0].hit_robot_id == EntityId("tall")
    assert state.robot_for(EntityId("short")).strength == 100  # type: ignore[union-attr]
    assert state.robot_for(EntityId("tall")).strength == 100 - damaged[0].damage  # type: ignore[union-attr]


def test_projectile_stops_at_a_structure_wall_and_never_destroys_it(world: WorldMap) -> None:
    # warbase-2's capture cell (261, 8) sits directly under the base; the wall at (261, 7) is 15 high.
    cell = _cell(world, "warbase-2", InteractionKind.WARBASE_CAPTURE)
    shooter = _tall("shooter", PLAYER_ONE, cell[0], cell[1] + 4)
    state = _initial(world).with_robots((shooter,))
    fire = FireCommand(player=PLAYER_ONE, sequence=0, entity_id=EntityId("shooter"), weapon=ModuleIdentity.MISSILE, target_x=cell[0], target_y=0)
    state, events = _step(state, world, (fire,), ticks=80)
    terminated = _events(events, ProjectileTerminatedEvent)
    assert len(terminated) == 1
    assert terminated[0].hit_robot_id is None
    # Height-aware: the shot clears the base's 7-high outer parts and
    # terminates on the first component at or above the projectile altitude.
    stop = (terminated[0].x, terminated[0].y)
    base = world.structure_by_id(EntityId("warbase-2"))
    assert base is not None
    heights = {(c.x, c.y): c.height for c in base.components}
    assert heights.get(stop, 0) >= PROJECTILE_ALTITUDE
    assert heights.get((stop[0], stop[1] + 1), 0) < PROJECTILE_ALTITUDE
    assert stop[1] < cell[1]
    assert not _events(events, StructureDestroyedEvent)
    assert state.structure_destruction == ()
    assert state.projectiles == ()


# -- nuclear carrier + nearby robot/structure destruction, commander safety ----------


def test_detonation_destroys_carrier_neighbours_and_structures_but_never_commanders(world: WorldMap) -> None:
    cell = _cell(world, "warbase-2", InteractionKind.WARBASE_CAPTURE)
    carrier = _robot("carrier", PLAYER_ONE, cell[0], cell[1] + 2, weapons=(ModuleIdentity.NUCLEAR,))
    near = _robot("near", PLAYER_TWO, cell[0] + 3, cell[1] + 2)
    far = _robot("far", PLAYER_TWO, cell[0] + 40, cell[1] + 2)
    state = _initial(world).with_robots((carrier, near, far))
    # Player 1 rides the carrier; Player 2 hovers, free, right next to the epicentre.
    state = _docked(state, PLAYER_ONE, carrier)
    state = state.with_commanders(
        tuple(replace(c, x=cell[0] + 1, y=cell[1] + 2, altitude=20) if c.player_id == PLAYER_TWO else c for c in state.commanders)
    )
    # Start a capture of warbase-2 by the carrier's neighbour so a progress record references a doomed robot.
    state = state.with_robots(state.robots + (_robot("capturing", PLAYER_TWO, *cell),))
    state, _ = _step(state, world, ticks=10)
    assert state.capture_progress_for(EntityId("warbase-2")) is not None

    fire = FireCommand(player=PLAYER_ONE, sequence=0, entity_id=EntityId("carrier"), weapon=ModuleIdentity.NUCLEAR, target_x=cell[0], target_y=cell[1])
    state, events = _step(state, world, (fire,))

    destroyed = {e.entity_id.value for e in _events(events, RobotDestroyedEvent)}
    assert destroyed == {"carrier", "near", "capturing"}
    assert {r.entity_id.value for r in state.robots} == {"far"}
    assert [e.structure_id.value for e in _events(events, StructureDestroyedEvent)] == ["warbase-2"]
    assert EntityId("warbase-2") in state.structure_destruction
    assert not _events(events, VictoryEvent)  # a neutral base's loss decides nothing
    # No stale references: capture progress and docking cleared, both commanders alive.
    assert state.capture_progress == ()
    c1, c2 = state.commander_for(PLAYER_ONE), state.commander_for(PLAYER_TWO)
    assert c1 is not None and c2 is not None
    assert c1.mode is CommanderMode.FREE and c1.docked_robot_id is None
    assert (c1.x, c1.y) == (carrier.x, carrier.y)
    assert c2.mode is CommanderMode.FREE and (c2.x, c2.y) == (cell[0] + 1, cell[1] + 2)
    assert c2.altitude >= 16  # only gravity (-1 per 4 ticks) touched it, not the blast
    # The destroyed base can no longer be captured.
    state = state.with_robots(state.robots + (_robot("late", PLAYER_ONE, *cell),))
    state, events = _step(state, world, ticks=CAPTURE_TICKS + 2)
    assert not _events(events, StructureCapturedEvent)
    assert state.capture_progress == ()


# -- launch onto a shared pad/exit cell then commander/robot separation ---------------


def test_launched_robot_appears_at_the_anchor_exit_while_the_commander_stays_on_the_roof_pad(world: WorldMap) -> None:
    """Roof pad at (anchor.x, anchor.y - 4); the robot exits at the anchor (open-questions §18)."""
    state = _commander_on_pad_with_session(_initial(world), world, PLAYER_ONE, "warbase-1")
    pad = _cell(world, "warbase-1", InteractionKind.HELI_PAD)
    exit_cell = _cell(world, "warbase-1", InteractionKind.EXIT)
    assert exit_cell == (pad[0], pad[1] + 4)
    state, _ = _step(state, world, (SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD),))
    state, _ = _step(state, world, (SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON),))
    state, events = _step(state, world, (LaunchRobotCommand(player=PLAYER_ONE, sequence=2),))
    assert len(_events(events, RobotLaunchedEvent)) == 1
    robot = state.robots_for(PLAYER_ONE)[0]
    assert (robot.x, robot.y) == exit_cell
    commander = state.commander_for(PLAYER_ONE)
    assert commander is not None and (commander.x, commander.y, commander.altitude) == (*pad, 15)
    # The commander is not enclosed by the robot: it can take off from the roof.
    state, _ = _step(state, world, (CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=3, rising=True),), ticks=12)
    assert state.commander_for(PLAYER_ONE).altitude > 15  # type: ignore[union-attr]
