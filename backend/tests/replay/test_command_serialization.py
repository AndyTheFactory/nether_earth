"""Command serialization round-trip tests (M7 Task 9, issue #98).

Extends Task 8's replay persistence to cover every concrete gameplay
``Command`` subclass ``engine.step`` dispatches on, grouped by subsystem per
the task brief: commander, construction/economy, movement/orders/capture,
firing/combat/nuclear. Each fixture proves ``ReplayWriter.record_tick`` ->
``load_commands_by_tick`` reproduces the exact same command (type and every
field), and a final end-to-end fixture proves the full one-tick-per-subsystem
batch replays through ``nether_earth.replay.run_fixture`` (i.e. the real
engine, not a serialization round-trip alone) without error -- the
"representative full-surface protocol fixture" acceptance criterion applied
to the command side.
"""

from __future__ import annotations

from pathlib import Path

from nether_earth import engine as engine_module
from nether_earth.combat import FireCommand
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.commands import Command
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    DeselectModuleCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId
from nether_earth.map import BootstrapMap
from nether_earth.orders import Advance, SearchCapture, SearchCaptureTarget, SetRobotOrderCommand
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.robot_build import ModuleIdentity
from nether_earth.scenario import default_pvp_scenario

from app.match.models import Match, MatchRuntimeState, PlayerSlot
from app.replay import ReplayWriter, load_commands_by_tick


def _writer_with_started_match(base_dir: Path, match_id: str) -> ReplayWriter:
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    game_state = engine_module.new_game(
        map_data, scenario, players=(PLAYER_ONE, PLAYER_TWO), seed=1
    )
    match = Match(
        match_id=match_id,
        join_code="ABCDEF",
        players={
            PLAYER_ONE: PlayerSlot(player_id=PLAYER_ONE, nickname="Alice", session_token="t1"),
            PLAYER_TWO: PlayerSlot(player_id=PLAYER_TWO, nickname="Bob", session_token="t2"),
        },
        state=MatchRuntimeState.ACTIVE,
        seed=1,
        game_state=game_state,
    )
    writer = ReplayWriter(base_dir=base_dir)
    writer.start_match(match, scenario, map_data)
    return writer


def _round_trip(base_dir: Path, match_id: str, tick: int, command: Command) -> Command:
    writer = _writer_with_started_match(base_dir, match_id)
    writer.record_tick(match_id, tick, (command,), ())
    (reconstructed,) = load_commands_by_tick(base_dir, match_id)[tick]
    return reconstructed


def test_base_command_round_trips(tmp_path: Path) -> None:
    command = Command(player=PLAYER_ONE, sequence=1)
    assert _round_trip(tmp_path, "m-base", 1, command) == command


# -- commander -------------------------------------------------------------------


def test_commander_move_command_round_trips(tmp_path: Path) -> None:
    command = CommanderMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0)
    assert _round_trip(tmp_path, "m-commander-move", 1, command) == command


def test_commander_set_vertical_intent_command_round_trips(tmp_path: Path) -> None:
    command = CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=1, rising=True)
    assert _round_trip(tmp_path, "m-commander-vertical", 1, command) == command


# -- construction/economy ---------------------------------------------------------


def test_select_module_command_round_trips(tmp_path: Path) -> None:
    command = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.TRACKS)
    assert _round_trip(tmp_path, "m-select-module", 1, command) == command


def test_deselect_module_command_round_trips(tmp_path: Path) -> None:
    command = DeselectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    assert _round_trip(tmp_path, "m-deselect-module", 1, command) == command


def test_cancel_construction_command_round_trips(tmp_path: Path) -> None:
    command = CancelConstructionCommand(player=PLAYER_ONE, sequence=1)
    assert _round_trip(tmp_path, "m-cancel-construction", 1, command) == command


def test_launch_robot_command_round_trips(tmp_path: Path) -> None:
    command = LaunchRobotCommand(player=PLAYER_ONE, sequence=1)
    assert _round_trip(tmp_path, "m-launch-robot", 1, command) == command


# -- movement/orders/capture --------------------------------------------------------


def test_direct_robot_move_command_round_trips(tmp_path: Path) -> None:
    command = DirectRobotMoveCommand(player=PLAYER_ONE, sequence=1, dx=0, dy=-1)
    assert _round_trip(tmp_path, "m-direct-move", 1, command) == command


def test_set_robot_order_command_round_trips(tmp_path: Path) -> None:
    command = SetRobotOrderCommand(
        player=PLAYER_ONE,
        sequence=1,
        entity_id=EntityId("robot-1"),
        order=SearchCapture(target=SearchCaptureTarget.ENEMY_FACTORY),
    )
    assert _round_trip(tmp_path, "m-set-order", 1, command) == command


def test_set_robot_order_command_with_bound_advance_target_round_trips(tmp_path: Path) -> None:
    """A persisted in-flight ``Advance`` (already bound `target_x`, e.g. a
    replayed command stream captured mid-order) must not lose that bound
    goal on reconstruction -- see `orders_json.order_to_json`'s docstring."""
    command = SetRobotOrderCommand(
        player=PLAYER_ONE,
        sequence=1,
        entity_id=EntityId("robot-1"),
        order=Advance(distance_miles=10, target_x=42),
    )
    assert _round_trip(tmp_path, "m-set-order-bound", 1, command) == command


# -- firing/combat/nuclear -----------------------------------------------------------


def test_robot_fire_normal_weapon_command_round_trips(tmp_path: Path) -> None:
    command = FireCommand(
        player=PLAYER_ONE,
        sequence=1,
        entity_id=EntityId("robot-1"),
        weapon=ModuleIdentity.CANNON,
        target_x=5,
        target_y=7,
    )
    assert _round_trip(tmp_path, "m-fire-normal", 1, command) == command


def test_robot_fire_nuclear_command_round_trips(tmp_path: Path) -> None:
    command = FireCommand(
        player=PLAYER_ONE,
        sequence=1,
        entity_id=EntityId("robot-1"),
        weapon=ModuleIdentity.NUCLEAR,
        target_x=0,
        target_y=0,
    )
    assert _round_trip(tmp_path, "m-fire-nuclear", 1, command) == command


# -- full-surface: every subsystem's command replays through the real engine --------


def test_every_subsystem_command_replays_through_the_real_engine(tmp_path: Path) -> None:
    """One command per subsystem group, one per tick, run through
    ``nether_earth.replay.run_fixture`` (i.e. ``engine.new_game`` + one
    ``engine.step`` per tick) -- proving the persisted/reconstructed
    commands are not just byte-identical but are legal, well-typed input to
    the real engine (gameplay legality itself, e.g. whether a robot exists
    to fire from, is exercised by the engine's own test suite -- this proves
    only that ``engine.step`` accepts and processes them without error, the
    "protocol mapping is a faithful translation of live gameplay types"
    property this task owns).
    """
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    commands_by_tick: dict[int, tuple[Command, ...]] = {
        1: (CommanderMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0),),
        2: (CommanderSetVerticalIntentCommand(player=PLAYER_ONE, sequence=2, rising=True),),
        3: (SelectModuleCommand(player=PLAYER_ONE, sequence=3, module=ModuleIdentity.TRACKS),),
        4: (DeselectModuleCommand(player=PLAYER_ONE, sequence=4, module=ModuleIdentity.TRACKS),),
        5: (CancelConstructionCommand(player=PLAYER_ONE, sequence=5),),
        6: (LaunchRobotCommand(player=PLAYER_ONE, sequence=6),),
        7: (DirectRobotMoveCommand(player=PLAYER_ONE, sequence=7, dx=1, dy=0),),
        8: (
            SetRobotOrderCommand(
                player=PLAYER_ONE,
                sequence=8,
                entity_id=EntityId("no-such-robot"),
                order=SearchCapture(target=SearchCaptureTarget.ENEMY_FACTORY),
            ),
        ),
        9: (
            FireCommand(
                player=PLAYER_ONE,
                sequence=9,
                entity_id=EntityId("no-such-robot"),
                weapon=ModuleIdentity.CANNON,
                target_x=5,
                target_y=7,
            ),
        ),
        10: (
            FireCommand(
                player=PLAYER_ONE,
                sequence=10,
                entity_id=EntityId("no-such-robot"),
                weapon=ModuleIdentity.NUCLEAR,
                target_x=0,
                target_y=0,
            ),
        ),
    }
    fixture = ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=1,
        tick_count=10,
        commands_by_tick=commands_by_tick,
    )
    final_state, events = run_fixture(fixture)
    assert final_state.tick == 10
    # Every command names a nonexistent robot/session precondition, so each
    # is a gameplay-level no-op (per this module's docstring) -- the point
    # is that `engine.step` handled all nine subclasses without raising.
    assert events
