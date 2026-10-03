"""A destroyed robot blinks for four game cycles before it is removed (§3.6).

Owner decision 2026-10-03: as on the ZX Spectrum. `Lb7d7_robot_destroyed`
writes strength -4; `Lb0fa_robot_update` adds one per game cycle, toggling
the robot's map mark (bit 6), and removes it (`Lb116_robot_destroyed`) on
the cycle it finds 0. See `docs/mechanics/combat.md` "Destroyed robots".
"""

from __future__ import annotations

from nether_earth.capture import CaptureProgress, advance_capture
from nether_earth.combat import (
    FireCommand,
    Projectile,
    ProjectileFiredEvent,
    ProjectileTerminatedEvent,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.destruction import (
    RobotDestroyedEvent,
    advance_destroyed_robots,
    destroy_robot,
    execute_nuclear_detonation,
)
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.docking import CommanderUndockedEvent
from nether_earth.engine import new_game, step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    folded_robot_occupancy,
    validate_robot_move,
)
from nether_earth.orders import StopAndDefend
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.robot import Robot, RobotFacing
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import snapshot_to_json_string
from nether_earth.state import GameState
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid

MAP_ID = "test-blink"
SIZE = 40
X, Y = 20, 20
FACTORY = EntityId("factory-1")
CAPTURE_CELL = (10, 30)


def _world() -> WorldMap:
    return WorldMap(
        map_id=MAP_ID,
        version=1,
        width=SIZE,
        height=SIZE,
        terrain=TerrainGrid(width=SIZE, height=SIZE, cells={}),
        war_bases=(
            WarBase(id=EntityId("wb-1"), components=(Component(0, 0, 3),), owner=PLAYER_ONE),
            WarBase(id=EntityId("wb-2"), components=(Component(39, 0, 3),), owner=PLAYER_TWO),
        ),
        factories=(
            Factory(
                id=FACTORY,
                components=(Component(10, 31, 3),),
                factory_type=FactoryType.CHASSIS,
                owner=PLAYER_TWO,
            ),
        ),
        blockers=(),
        interaction_points=(
            InteractionPoint(
                id="factory-1-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=FACTORY,
                footprint=Footprint(cells=frozenset({CAPTURE_CELL})),
            ),
        ),
        spawn_positions={},
    )


def _state(*robots: Robot) -> GameState:
    scenario = Scenario(id="fixture", map_id=MAP_ID, map_version=1, player_starting_warbases=1)
    map_data = BootstrapMap(map_id=MAP_ID, version=1, width=SIZE, height=SIZE)
    return new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=3).with_robots(
        robots
    )


def _robot(
    ident: str,
    owner: PlayerId,
    x: int,
    y: int,
    *,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.CANNON,),
    strength: int = 100,
    order: StopAndDefend | None = None,
    facing: RobotFacing = RobotFacing.EAST,
) -> Robot:
    # Tracks + cannon: 13 high, above the projectile altitude of 10.
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=weapons)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=EntityId(ident),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        strength=strength,
        order=order,
        facing=facing,
    )


def _dying(robot: Robot, cycles: int = 4) -> Robot:
    return robot.with_strength(0).with_destroyed_cycles_remaining(cycles)


def _fire(robot: Robot, sequence: int = 0, weapon: ModuleIdentity = ModuleIdentity.CANNON) -> FireCommand:
    return FireCommand(player=robot.owner, sequence=sequence, entity_id=robot.entity_id, weapon=weapon)


def _kill_on_tick_one(world: WorldMap) -> tuple[GameState, EntityId]:
    """A lethal adjacent cannon shot on tick 1 (hit by the first advance)."""
    shooter = _robot("robot-a", PLAYER_ONE, X, Y)
    target = _robot("robot-z", PLAYER_TWO, X + 2, Y, strength=1)
    state, events = step(_state(shooter, target), [_fire(shooter)], world=world)
    assert state.tick == 1
    assert [e.entity_id for e in events if isinstance(e, RobotDestroyedEvent)] == [target.entity_id]
    return state, target.entity_id


# -- (a) duration ----------------------------------------------------------------


def test_a_killed_robot_blinks_for_four_cycles_and_is_removed_on_the_fifth() -> None:
    world = _world()
    state, target = _kill_on_tick_one(world)
    timeline: dict[int, tuple[int | None, bool] | None] = {}
    while state.tick < 24:
        robot = state.robot_for(target)
        timeline[state.tick] = (
            (robot.destroyed_cycles_remaining, robot.present) if robot is not None else None
        )
        state, _ = step(state, [], world=world)

    # Shown for the rest of the hit cycle, then hidden/shown/hidden/shown on
    # the cycles starting at ticks 4, 8, 12 and 16; gone on tick 20.
    assert timeline[1] == (4, True)
    assert timeline[3] == (4, True)
    assert timeline[4] == (3, False)
    assert timeline[7] == (3, False)
    assert timeline[8] == (2, True)
    assert timeline[12] == (1, False)
    assert timeline[16] == (0, True)
    assert timeline[19] == (0, True)
    assert timeline[20] is None


def test_the_count_runs_on_cycle_ticks_not_on_the_hit_tick() -> None:
    # Killed on a cycle tick (after that cycle's robot update): the first
    # count-down is the next cycle, so removal is 5 cycles later.
    state = _state(_dying(_robot("robot-z", PLAYER_TWO, X, Y)))
    removed_at = None
    for tick in range(9, 40):
        state, _ = advance_destroyed_robots(state, None, tick)
        if not state.robots:
            removed_at = tick
            break
    assert removed_at == 28


def test_a_killing_hit_emits_one_destroyed_event_and_removal_emits_none() -> None:
    world = _world()
    state, target = _kill_on_tick_one(world)
    later: list[object] = []
    while state.robot_for(target) is not None:
        state, events = step(state, [], world=world)
        later.extend(events)
    assert not any(isinstance(e, RobotDestroyedEvent) for e in later)


# -- (b) what a blinking robot still does ---------------------------------------


def test_a_blinking_robot_blocks_other_robots_only_while_shown() -> None:
    world = _world()
    dying = _dying(_robot("robot-z", PLAYER_TWO, X, Y))
    shown = _state(dying)
    hidden = _state(dying.with_destroyed_cycles_remaining(3))
    assert folded_robot_occupancy(world, shown).is_occupied(X, Y)
    assert not folded_robot_occupancy(world, hidden).is_occupied(X, Y)

    mover = _robot("robot-a", PLAYER_ONE, X - 2, Y)
    request = RobotMoveRequest(entity_id=mover.entity_id, dx=1, dy=0)
    blocked = validate_robot_move(request, shown.with_robots((mover, dying)), world)
    assert blocked.reason is MovementRejectionReason.OCCUPIED
    free = validate_robot_move(
        request, hidden.with_robots((mover, dying.with_destroyed_cycles_remaining(3))), world
    )
    assert free.accepted


def test_a_blinking_robot_cannot_move_even_under_direct_control() -> None:
    world = _world()
    dying = _dying(_robot("robot-a", PLAYER_ONE, X, Y))
    request = RobotMoveRequest(entity_id=dying.entity_id, dx=1, dy=0)
    result = validate_robot_move(request, _state(dying), world)
    assert result.reason is MovementRejectionReason.ROBOT_DESTROYED

    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=X,
        y=Y,
        altitude=dying.height,
        docked_robot_id=dying.entity_id,
    )
    state = _state(dying).with_commanders((commander,))
    move = DirectRobotMoveCommand(player=PLAYER_ONE, sequence=0, dx=1, dy=0)
    state, _ = step(state, [move], world=world)
    robot = state.robot_for(dying.entity_id)
    assert robot is not None and robot.movement is None and robot.turning is None


def test_a_bullet_stops_on_a_shown_blinking_robot_and_passes_a_hidden_one() -> None:
    # `Lb7a7`: a marked robot found by the bullet scan with strength <= 0
    # ends the bullet without damage; an unmarked one is not seen at all.
    world = _world()
    shooter = _robot("robot-a", PLAYER_ONE, X - 10, Y)
    bullet = Projectile(
        id=EntityId("projectile-a-1"),
        owner=PLAYER_ONE,
        source_robot_id=shooter.entity_id,
        weapon=ModuleIdentity.CANNON,
        x=X - 2,
        y=Y,
        z=DEFAULT_RULES.normal_projectile_altitude,
        dx=1,
        dy=0,
        travelled_cells=2,
        max_range_cells=10,
        created_tick=1,
        first_advance_tick=4,
    )

    def advance(cycles: int) -> tuple[GameState, tuple[object, ...]]:
        # Tick 4 counts the blink down first, then moves the bullet onto it.
        dying = _dying(_robot("robot-z", PLAYER_TWO, X, Y), cycles)
        state = _state(shooter.with_active_projectile(bullet.id), dying)
        return step(state.with_projectiles((bullet,)).with_tick(3), [], world=world)

    shown, events = advance(3)  # 2 left on tick 4: shown
    hits = [e for e in events if isinstance(e, ProjectileTerminatedEvent)]
    assert [e.hit_robot_id for e in hits] == [EntityId("robot-z")]
    assert not shown.projectiles
    assert shown.robot_for(EntityId("robot-z")).strength == 0  # type: ignore[union-attr]
    assert not any(isinstance(e, RobotDestroyedEvent) for e in events)

    hidden, events = advance(4)  # 3 left on tick 4: hidden
    assert not any(isinstance(e, ProjectileTerminatedEvent) for e in events)
    assert [(p.x, p.y) for p in hidden.projectiles] == [(X, Y)]  # over it


def test_a_blinking_robot_never_fires_on_its_own() -> None:
    world = _world()
    dying = _dying(_robot("robot-a", PLAYER_ONE, X, Y, order=StopAndDefend()))
    enemy = _robot("robot-z", PLAYER_TWO, X + 4, Y)
    state = _state(dying, enemy)
    fired = []
    for _ in range(12):
        state, events = step(state, [], world=world)
        fired.extend(e for e in events if isinstance(e, ProjectileFiredEvent))
    assert fired == []


def test_a_blinking_robot_is_not_shot_at_but_a_live_enemy_behind_it_is() -> None:
    # `Lb68e_object_found`: a robot with strength <= 0 is not an enemy to
    # fire at.
    world = _world()
    defender = _robot("robot-a", PLAYER_ONE, X, Y, order=StopAndDefend())
    enemy = _robot("robot-y", PLAYER_TWO, X + 4, Y)

    def fires(*others: Robot) -> bool:
        state = _state(defender, *others)
        for _ in range(4):
            state, events = step(state, [], world=world)
            if any(isinstance(e, ProjectileFiredEvent) for e in events):
                return True
        return False

    assert fires(enemy)
    assert not fires(_dying(enemy))
    assert fires(_dying(enemy), _robot("robot-z", PLAYER_TWO, X + 8, Y))


def test_the_docked_player_can_still_fire_from_a_blinking_robot() -> None:
    # Combat mode (`Lac99`, `Lacb3_regular_weapon_fire`) checks no strength.
    world = _world()
    dying = _dying(_robot("robot-a", PLAYER_ONE, X, Y))
    _state_after, events = step(_state(dying), [_fire(dying)], world=world)
    assert any(isinstance(e, ProjectileFiredEvent) for e in events)


def test_the_docked_commander_stays_until_removal_then_is_set_down() -> None:
    world = _world()
    robot = _robot("robot-z", PLAYER_TWO, X + 2, Y, strength=1)
    commander = Commander(
        player_id=PLAYER_TWO,
        mode=CommanderMode.DOCKED,
        x=robot.x,
        y=robot.y,
        altitude=robot.height,
        docked_robot_id=robot.entity_id,
    )
    shooter = _robot("robot-a", PLAYER_ONE, X, Y)
    state = _state(shooter, robot).with_commanders((commander,))
    state, _ = step(state, [_fire(shooter)], world=world)
    undocked_at = None
    while undocked_at is None:
        docked = state.commander_for(PLAYER_TWO)
        assert docked is not None and docked.mode is CommanderMode.DOCKED
        state, events = step(state, [], world=world)
        if any(isinstance(e, CommanderUndockedEvent) for e in events):
            undocked_at = state.tick
    assert undocked_at == 20
    freed = state.commander_for(PLAYER_TWO)
    assert freed is not None and freed.mode is CommanderMode.FREE
    assert (freed.x, freed.y) == (robot.x, robot.y)


def test_a_blinking_robot_still_counts_for_its_owner() -> None:
    # `Lbb40_count_robots` counts it until `Lb116` frees its slot.
    dying = _dying(_robot("robot-z", PLAYER_TWO, X, Y))
    assert _state(dying).robots_for(PLAYER_TWO) == (dying,)


def test_capture_progress_counts_a_blinking_robot_only_while_shown() -> None:
    # `Ladb7_building_loop` advances the timer while bit 6 is set on the
    # capture cell and resets it otherwise.
    world = _world()
    dying = _dying(_robot("robot-a", PLAYER_ONE, *CAPTURE_CELL))
    progress = CaptureProgress(
        structure_id=FACTORY,
        capturing_player=PLAYER_ONE,
        robot_id=dying.entity_id,
        elapsed_ticks=5,
        required_ticks=DEFAULT_RULES.capture_duration_ticks,
    )
    state = _state(dying).with_capture_progress((progress,))
    shown, _ = advance_capture(state, world, 1)
    kept = shown.capture_progress_for(FACTORY)
    assert kept is not None and kept.elapsed_ticks == 6
    hidden, _ = advance_capture(
        state.with_robots((dying.with_destroyed_cycles_remaining(3),)), world, 1
    )
    assert hidden.capture_progress_for(FACTORY) is None


def test_a_nuclear_blast_removes_a_shown_blinking_robot_and_misses_a_hidden_one() -> None:
    # `Lba33` tests bit 6: only a marked robot is in the blast.
    world = _world()
    carrier = _robot("robot-a", PLAYER_ONE, X, Y, weapons=(ModuleIdentity.NUCLEAR,))
    shown = _dying(_robot("robot-y", PLAYER_TWO, X + 2, Y))
    hidden = _dying(_robot("robot-z", PLAYER_TWO, X - 2, Y), cycles=3)
    state, events = execute_nuclear_detonation(
        _state(carrier, shown, hidden), world, carrier.entity_id, 5
    )
    assert [r.entity_id for r in state.robots] == [hidden.entity_id]
    # The carrier was alive; the blinking robot already reported its death.
    assert [e.entity_id for e in events if isinstance(e, RobotDestroyedEvent)] == [
        carrier.entity_id
    ]


def test_a_second_kill_while_blinking_is_a_no_op() -> None:
    dying = _dying(_robot("robot-z", PLAYER_TWO, X, Y))
    state = _state(dying)
    again, events = destroy_robot(state, dying.entity_id, 5)
    assert again is state and events == ()


# -- determinism ----------------------------------------------------


def test_a_kill_and_its_blink_replay_identically() -> None:
    world = _world()
    shooter = _robot("robot-a", PLAYER_ONE, X, Y)
    target = _robot("robot-z", PLAYER_TWO, X + 2, Y, strength=1)
    fixture = ReplayFixture(
        scenario=Scenario(id="fixture", map_id=MAP_ID, map_version=1, player_starting_warbases=1),
        map_data=BootstrapMap(map_id=MAP_ID, version=1, width=SIZE, height=SIZE),
        seed=3,
        tick_count=30,
        commands_by_tick={1: (_fire(shooter),)},
        world=world,
        initial_robots=(shooter, target),
    )
    first_state, first_events = run_fixture(fixture)
    second_state, second_events = run_fixture(fixture)
    assert snapshot_to_json_string(first_state) == snapshot_to_json_string(second_state)
    assert first_events == second_events
    assert first_state.robot_for(target.entity_id) is None
    assert any(isinstance(e, RobotDestroyedEvent) for e in first_events)
