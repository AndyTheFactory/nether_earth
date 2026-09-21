"""Tests for damage calculation, application, and robot destruction (issue #76, M6.6)."""

from __future__ import annotations

from dataclasses import fields, replace

from nether_earth.combat import (
    RobotDamagedEvent,
    apply_damage,
    calculate_base_damage,
    calculate_weapon_damage,
    ground_height_at,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.destruction import RobotDestroyedEvent, destroy_robot
from nether_earth.docking import CommanderUndockedEvent
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.movement import folded_robot_occupancy
from nether_earth.robot import Robot, RobotMoveTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Blocker, Component
from nether_earth.terrain import TerrainGrid, TerrainType

# --------------------------------------------------------------------------
# Fixtures (mirroring test_combat_fire.py / test_combat_projectile.py)
# --------------------------------------------------------------------------


def _world(
    width: int = 20,
    height: int = 20,
    blockers: tuple[Blocker, ...] = (),
) -> WorldMap:
    return WorldMap(
        map_id="combat-damage-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width,
            height=height,
            cells={},
            default=TerrainType.NORMAL,
        ),
        war_bases=(),
        factories=(),
        blockers=blockers,
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str = "robot-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 5,
    y: int = 5,
    height: int = 13,
    strength: int = 100,
    active_projectile_id: EntityId | None = None,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.PHASER,), electronics=None)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=(ModuleIdentity.BIPOD, ModuleIdentity.PHASER),
        height=height,
        strength=strength,
        active_projectile_id=active_projectile_id,
    )


def _state(
    robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
) -> GameState:
    return create_game_state(
        0,
        (PLAYER_ONE, PLAYER_TWO),
        robots=list(robots),
        commanders=list(commanders),
    )


# --------------------------------------------------------------------------
# calculate_base_damage / calculate_weapon_damage
# --------------------------------------------------------------------------


def test_calculate_base_damage_formula() -> None:
    assert calculate_base_damage(13, 0) == 11  # (60 - 13) // 4 == 11
    assert calculate_base_damage(38, 0) == 5  # (60 - 38) // 4 == 5
    assert calculate_base_damage(20, 10) == 7  # (60 - 30) // 4 == 7


def test_calculate_weapon_damage_worked_example_phaser_weakest_robot() -> None:
    """Task 5's own worked example: phaser vs. weakest robot at ground level."""
    assert (
        calculate_weapon_damage(
            ModuleIdentity.PHASER, robot_height=13, ground_height=0, rules=DEFAULT_RULES
        )
        == 44
    )


def test_spectrum_worked_example_phaser_on_derived_tracks_cannon_robot_deals_44() -> None:
    """CR003.3 (#218): the disassembly header's example, from the real build.

    Tracks (7) + cannon (6) = 13 from ``Ld7b4_piece_heights``; a phaser hit
    at ground level deals ``(60 - (13 + 0)) // 4 * 4 == 44``.
    """
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    assert height == 13
    robot = replace(_robot(height=height), build=build, stack=stack)

    new_state, events = apply_damage(
        _state((robot,)), _world(), robot.entity_id, ModuleIdentity.PHASER, DEFAULT_RULES, tick=4
    )

    assert [(e.damage, e.remaining_strength) for e in events if isinstance(e, RobotDamagedEvent)] == [
        (44, 56)
    ]
    assert new_state.robot_for(robot.entity_id).strength == 56  # type: ignore[union-attr]


def test_calculate_weapon_damage_multipliers() -> None:
    base = calculate_base_damage(20, 0)  # (60-20)//4 == 10
    assert calculate_weapon_damage(ModuleIdentity.CANNON, 20, 0, DEFAULT_RULES) == base * 2
    assert calculate_weapon_damage(ModuleIdentity.MISSILE, 20, 0, DEFAULT_RULES) == base * 3
    assert calculate_weapon_damage(ModuleIdentity.PHASER, 20, 0, DEFAULT_RULES) == base * 4


def test_calculate_weapon_damage_rejects_nuclear() -> None:
    try:
        calculate_weapon_damage(ModuleIdentity.NUCLEAR, 13, 0, DEFAULT_RULES)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for nuclear")


def test_calculate_weapon_damage_rejects_non_weapon_identity() -> None:
    try:
        calculate_weapon_damage(ModuleIdentity.BIPOD, 13, 0, DEFAULT_RULES)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a non-weapon identity")


# --------------------------------------------------------------------------
# ground_height_at
# --------------------------------------------------------------------------


def test_ground_height_at_bare_terrain_is_zero() -> None:
    world = _world()
    assert ground_height_at(world, 5, 5) == 0


def test_ground_height_at_reads_component_height() -> None:
    blocker = Blocker(id=EntityId("blocker-1"), components=(Component(x=5, y=5, height=7),))
    world = _world(blockers=(blocker,))
    assert ground_height_at(world, 5, 5) == 7
    # A different cell with no component is still bare.
    assert ground_height_at(world, 6, 6) == 0


# --------------------------------------------------------------------------
# apply_damage
# --------------------------------------------------------------------------


def test_apply_damage_survival_decreases_strength_and_emits_event() -> None:
    robot = _robot(strength=100, height=13)
    state = _state((robot,))
    world = _world()

    new_state, events = apply_damage(
        state, world, robot.entity_id, ModuleIdentity.CANNON, DEFAULT_RULES, tick=4
    )

    # cannon: base (60-13)//4=11 * 2 == 22
    updated = new_state.robot_for(robot.entity_id)
    assert updated is not None
    assert updated.strength == 78
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, RobotDamagedEvent)
    assert event.damage == 22
    assert event.remaining_strength == 78
    assert event.entity_id == robot.entity_id
    assert event.owner == robot.owner
    assert event.weapon == ModuleIdentity.CANNON
    assert event.tick == 4


def test_apply_damage_exact_threshold_destroys_robot() -> None:
    # phaser damage = 44; strength exactly 44 => new_strength == 0 => destroyed.
    robot = _robot(strength=44, height=13)
    state = _state((robot,))
    world = _world()

    new_state, events = apply_damage(
        state, world, robot.entity_id, ModuleIdentity.PHASER, DEFAULT_RULES, tick=4
    )

    assert new_state.robot_for(robot.entity_id) is None
    assert any(isinstance(e, RobotDestroyedEvent) for e in events)
    # No lingering strength=0 robot anywhere.
    assert all(r.entity_id != robot.entity_id for r in new_state.robots)


def test_apply_damage_overkill_destroys_robot_cleanly() -> None:
    robot = _robot(strength=10, height=13)
    state = _state((robot,))
    world = _world()

    new_state, events = apply_damage(
        state, world, robot.entity_id, ModuleIdentity.PHASER, DEFAULT_RULES, tick=4
    )

    assert new_state.robot_for(robot.entity_id) is None
    assert any(isinstance(e, RobotDestroyedEvent) for e in events)


def test_apply_damage_target_not_found_is_noop() -> None:
    state = _state(())
    world = _world()

    new_state, events = apply_damage(
        state, world, EntityId("no-such-robot"), ModuleIdentity.CANNON, DEFAULT_RULES, tick=4
    )

    assert new_state is state
    assert events == ()


def test_apply_damage_uses_sequencer() -> None:
    robot = _robot(strength=100, height=13)
    state = _state((robot,))
    world = _world()
    sequencer = EventSequencer(start=5)

    _new_state, events = apply_damage(
        state, world, robot.entity_id, ModuleIdentity.CANNON, DEFAULT_RULES, tick=4, sequencer=sequencer
    )

    assert events[0].sequence == 5


# --------------------------------------------------------------------------
# destroy_robot
# --------------------------------------------------------------------------


def test_destroy_robot_removes_robot_and_frees_occupancy() -> None:
    robot = _robot(x=5, y=5)
    state = _state((robot,))
    world = _world()

    occupancy_before = folded_robot_occupancy(world, state)
    assert occupancy_before.is_occupied(5, 5)

    new_state, events = destroy_robot(state, robot.entity_id, tick=10)

    assert new_state.robot_for(robot.entity_id) is None
    occupancy_after = folded_robot_occupancy(world, new_state)
    assert not occupancy_after.is_occupied(5, 5)
    assert len(events) == 1
    assert isinstance(events[0], RobotDestroyedEvent)
    assert events[0].entity_id == robot.entity_id
    assert events[0].owner == robot.owner
    assert events[0].x == 5
    assert events[0].y == 5
    assert events[0].tick == 10


def test_destroy_robot_removes_capture_progress_naming_it() -> None:
    from nether_earth.capture import CaptureProgress

    robot = _robot(entity_id="robot-capturer")
    other_robot = _robot(entity_id="robot-other", x=1, y=1)
    progress = CaptureProgress(
        structure_id=EntityId("factory-1"),
        capturing_player=PLAYER_ONE,
        robot_id=robot.entity_id,
        elapsed_ticks=10,
        required_ticks=1440,
    )
    other_progress = CaptureProgress(
        structure_id=EntityId("factory-2"),
        capturing_player=PLAYER_ONE,
        robot_id=other_robot.entity_id,
        elapsed_ticks=20,
        required_ticks=1440,
    )
    state = create_game_state(
        0,
        (PLAYER_ONE, PLAYER_TWO),
        robots=[robot, other_robot],
        capture_progress=[progress, other_progress],
    )

    new_state, _events = destroy_robot(state, robot.entity_id, tick=10)

    assert new_state.capture_progress_for(EntityId("factory-1")) is None
    remaining = new_state.capture_progress_for(EntityId("factory-2"))
    assert remaining is not None
    assert remaining.robot_id == other_robot.entity_id


def test_destroy_robot_relocates_docked_commander_to_free() -> None:
    robot = _robot(entity_id="robot-1", x=3, y=4, height=15)
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=3,
        y=4,
        altitude=15,
        docked_robot_id=robot.entity_id,
    )
    state = _state((robot,), (commander,))

    new_state, events = destroy_robot(state, robot.entity_id, tick=7)

    updated_commander = new_state.commander_for(PLAYER_ONE)
    assert updated_commander is not None
    assert updated_commander.mode is CommanderMode.FREE
    assert updated_commander.docked_robot_id is None
    assert updated_commander.x == 3
    assert updated_commander.y == 4
    assert updated_commander.altitude == 15

    undock_events = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert len(undock_events) == 1
    assert undock_events[0].robot_id == robot.entity_id
    assert undock_events[0].from_altitude == 15
    assert undock_events[0].to_altitude == 15

    destroyed_events = [e for e in events if isinstance(e, RobotDestroyedEvent)]
    assert len(destroyed_events) == 1


def test_destroy_robot_leaves_undocked_commander_untouched() -> None:
    robot = _robot(entity_id="robot-1", x=3, y=4)
    other_robot = _robot(entity_id="robot-other", x=9, y=9)
    commander = Commander(
        player_id=PLAYER_TWO,
        mode=CommanderMode.DOCKED,
        x=9,
        y=9,
        altitude=13,
        docked_robot_id=other_robot.entity_id,
    )
    state = _state((robot, other_robot), (commander,))

    new_state, events = destroy_robot(state, robot.entity_id, tick=7)

    updated_commander = new_state.commander_for(PLAYER_TWO)
    assert updated_commander == commander
    assert not any(isinstance(e, CommanderUndockedEvent) for e in events)


def test_destroy_robot_twice_is_identity_noop() -> None:
    robot = _robot()
    state = _state((robot,))

    first_state, first_events = destroy_robot(state, robot.entity_id, tick=1)
    assert first_events != ()

    second_state, second_events = destroy_robot(first_state, robot.entity_id, tick=2)

    assert second_state is first_state
    assert second_events == ()


def test_destroy_robot_on_unknown_id_is_noop() -> None:
    state = _state(())
    new_state, events = destroy_robot(state, EntityId("ghost"), tick=1)
    assert new_state is state
    assert events == ()


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_repeated_damage_sequence_is_deterministic() -> None:
    def run() -> tuple[GameState, list[str]]:
        robot = _robot(entity_id="robot-1", strength=100, height=13)
        state = _state((robot,))
        world = _world()
        sequencer = EventSequencer()
        log: list[str] = []

        state, events = apply_damage(
            state, world, robot.entity_id, ModuleIdentity.CANNON, DEFAULT_RULES, tick=4, sequencer=sequencer
        )
        log.extend(repr(e) for e in events)

        state, events = apply_damage(
            state, world, robot.entity_id, ModuleIdentity.PHASER, DEFAULT_RULES, tick=8, sequencer=sequencer
        )
        log.extend(repr(e) for e in events)

        return state, log

    state_a, log_a = run()
    state_b, log_b = run()

    assert state_a == state_b
    assert log_a == log_b


def test_repeated_destroy_sequence_is_deterministic() -> None:
    def run() -> tuple[GameState, list[str]]:
        robot = _robot(entity_id="robot-1")
        commander = Commander(
            player_id=PLAYER_ONE,
            mode=CommanderMode.DOCKED,
            x=robot.x,
            y=robot.y,
            altitude=robot.height,
            docked_robot_id=robot.entity_id,
        )
        state = _state((robot,), (commander,))
        sequencer = EventSequencer()

        state, events = destroy_robot(state, robot.entity_id, tick=10, sequencer=sequencer)
        log = [repr(e) for e in events]
        return state, log

    state_a, log_a = run()
    state_b, log_b = run()

    assert state_a == state_b
    assert log_a == log_b


# --------------------------------------------------------------------------
# Regression tests: Robot.with_movement/with_position/with_order must not
# drop active_projectile_id/strength (the latent bug found and fixed while
# implementing Robot.with_strength for this task -- see task-6-report.md).
# --------------------------------------------------------------------------


def test_with_movement_preserves_active_projectile_id_and_strength() -> None:
    robot = _robot(entity_id="robot-1", strength=57, active_projectile_id=EntityId("projectile-1"))
    transition = RobotMoveTransition(
        entity_id=robot.entity_id,
        from_x=robot.x,
        from_y=robot.y,
        to_x=robot.x + 1,
        to_y=robot.y,
        started_tick=0,
        duration_ticks=5,
    )

    updated = robot.with_movement(transition)

    assert updated.movement == transition
    assert updated.active_projectile_id == EntityId("projectile-1")
    assert updated.strength == 57


def test_with_position_preserves_active_projectile_id_and_strength() -> None:
    robot = _robot(entity_id="robot-1", strength=57, active_projectile_id=EntityId("projectile-1"))

    updated = robot.with_position(robot.x + 1, robot.y)

    assert (updated.x, updated.y) == (robot.x + 1, robot.y)
    assert updated.active_projectile_id == EntityId("projectile-1")
    assert updated.strength == 57


def test_with_order_preserves_active_projectile_id_and_strength() -> None:
    from nether_earth.orders import StopAndDefend

    robot = _robot(entity_id="robot-1", strength=57, active_projectile_id=EntityId("projectile-1"))

    updated = robot.with_order(StopAndDefend())

    assert updated.order == StopAndDefend()
    assert updated.active_projectile_id == EntityId("projectile-1")
    assert updated.strength == 57


def test_every_with_method_changes_only_its_own_field() -> None:
    """Strongest form of the regression protection: for every ``Robot.with_*``
    method, assert it changes exactly its own targeted field(s) and leaves
    every other :func:`dataclasses.fields` value untouched. This is the
    exact property that silently broke for ``active_projectile_id`` before
    this task's fix, encoded so the *next* field addition to ``Robot``
    cannot reintroduce the same class of bug without a test failing here.
    """
    from nether_earth.orders import StopAndDefend

    transition = RobotMoveTransition(
        entity_id=EntityId("robot-1"),
        from_x=5,
        from_y=5,
        to_x=6,
        to_y=5,
        started_tick=0,
        duration_ticks=5,
    )

    base = _robot(
        entity_id="robot-1",
        x=5,
        y=5,
        strength=57,
        active_projectile_id=EntityId("projectile-1"),
    )
    base = base.with_order(StopAndDefend())

    field_names = {f.name for f in fields(Robot)}

    cases: tuple[tuple[Robot, set[str]], ...] = (
        (base.with_movement(transition), {"movement"}),
        (base.with_position(9, 9), {"x", "y", "movement"}),
        (base.with_order(None), {"order"}),
        (base.with_active_projectile(EntityId("projectile-2")), {"active_projectile_id"}),
        (base.with_strength(1), {"strength"}),
    )

    for updated, expected_changed in cases:
        unchanged_fields = field_names - expected_changed
        for name in unchanged_fields:
            assert getattr(updated, name) == getattr(base, name), (
                f"{name!r} unexpectedly changed"
            )
        # Sanity check the targeted field(s) actually did change, so this
        # test cannot pass vacuously if a with_* method became a no-op.
        assert any(
            getattr(updated, name) != getattr(base, name) for name in expected_changed
        )


def test_with_strength_preserves_every_other_field() -> None:
    transition = RobotMoveTransition(
        entity_id=EntityId("robot-1"),
        from_x=5,
        from_y=5,
        to_x=6,
        to_y=5,
        started_tick=0,
        duration_ticks=5,
    )
    robot = replace(
        _robot(entity_id="robot-1", strength=57, active_projectile_id=EntityId("projectile-1")),
        movement=transition,
    )

    updated = robot.with_strength(1)

    assert updated.strength == 1
    assert updated.movement == transition
    assert updated.active_projectile_id == EntityId("projectile-1")
    assert updated.order == robot.order
