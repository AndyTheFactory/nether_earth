"""``engine.step`` integration for the Milestone 6 combat pass (M6.10, issue #80).

Every prior M6 task (fire validation, projectile simulation, damage/
destruction, nuclear detonation, autonomous engagement, victory) built pure,
directly-testable functions and deliberately left `engine.py` untouched, so
that one task could make a single coherent step-ordering decision. This
module is that decision's end-to-end verification: every assertion here is
driven through real ``engine.step``/``replay.run_fixture`` calls, never by
calling `combat.py`/`destruction.py`'s functions directly (those already
have their own unit suites -- duplicating them here would prove nothing
about the wiring).

Height arithmetic used throughout (all from ``DEFAULT_RULES``):
``normal_projectile_altitude`` is 10, and a projectile is blocked by a robot
whose top is ``>= 10``. With the Spectrum piece heights (CR003.3) a
``TRACKS`` chassis (7) plus cannon (6), missile (6) and phaser (7) is 26, so
:func:`_gunner` builds a robot that both fires and can be hit. A cannon hit
on it deals ``((60 - (26 + 0)) // 4) * 2 == 16`` damage on bare terrain.
"""

from __future__ import annotations

from nether_earth.capture import CapturableStructureKind, StructureCapturedEvent
from nether_earth.clock import TICKS_PER_GAME_DAY
from nether_earth.combat import (
    FireCommand,
    ProjectileFiredEvent,
    ProjectileTerminatedEvent,
    RobotDamagedEvent,
)
from nether_earth.destruction import RobotDestroyedEvent, StructureDestroyedEvent
from nether_earth.engine import new_game, step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.movement import robot_move_duration_ticks
from nether_earth.orders import SearchDestroy, SearchDestroyTarget
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.resource_production import DailyProductionApplied
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import snapshot_to_json_string
from nether_earth.state import GameState
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType
from nether_earth.victory import VictoryEvent

MAP_ID = "test-combat-integration"
SIZE = 60

WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_ONE_B = EntityId("warbase-p1-b")
WAR_BASE_TWO = EntityId("warbase-p2")
NEUTRAL_WAR_BASE = EntityId("warbase-neutral")
FACTORY_ONE = EntityId("factory-p1")

#: Far from every war base, the firefight zone for normal-weapon tests.
QUIET_X, QUIET_Y = 30, 30

#: The factory is deliberately off the ``y == QUIET_Y`` firing row: standing
#: on a structure raises the damage formula's ``ground_height`` term, which
#: would silently change every expected damage number in this module. Its
#: capture cell is also its nuclear-blast anchor (`_specs/open-questions.md`
#: §20).
FACTORY_CELL = (25, 25)
FACTORY_CAPTURE_CELL = (26, 25)

#: Nuclear detonation point near the factory: factory dx = |28 - 26| = 2,
#: dy = |26 + 1 - 25| = 2 (in range: < 5 each, sum < 7), and both war bases
#: are far outside their shape -- so a blast here destroys only the factory
#: and cannot trigger the victory condition.
NUKE_X, NUKE_Y = 28, 26

CANNON_DAMAGE = 16
ADVANCE = DEFAULT_RULES.projectile_advance_ticks


def _world(
    *,
    factory_owner: PlayerId | None = PLAYER_ONE,
    extra_war_bases: tuple[WarBase, ...] = (),
    extra_interaction_points: tuple[InteractionPoint, ...] = (),
) -> WorldMap:
    """Return the shared 60x60 flat test map.

    Both war bases are 3 units tall -- far below ``normal_projectile_altitude``
    (10) -- so no structure in this map ever blocks a projectile; collision
    tests here are purely about robots.
    """
    return WorldMap(
        map_id=MAP_ID,
        version=1,
        width=SIZE,
        height=SIZE,
        terrain=TerrainGrid(width=SIZE, height=SIZE, cells={}),
        war_bases=(
            WarBase(
                id=WAR_BASE_ONE,
                components=(Component(x=0, y=0, height=3),),
                owner=PLAYER_ONE,
            ),
            WarBase(
                id=WAR_BASE_TWO,
                components=(Component(x=SIZE - 1, y=SIZE - 1, height=3),),
                owner=PLAYER_TWO,
            ),
            *extra_war_bases,
        ),
        factories=(
            Factory(
                id=FACTORY_ONE,
                components=(Component(x=FACTORY_CELL[0], y=FACTORY_CELL[1], height=3),),
                factory_type=FactoryType.CHASSIS,
                owner=factory_owner,
            ),
        ),
        blockers=(),
        interaction_points=(
            # Each home war base's capture point sits on its own (occupied)
            # component cell: unreachable for capture, but it gives the base
            # its nuclear-blast anchor (`_specs/open-questions.md` §20).
            _war_base_anchor(WAR_BASE_ONE, (0, 0)),
            _war_base_anchor(WAR_BASE_TWO, (SIZE - 1, SIZE - 1)),
            InteractionPoint(
                id="factory-p1-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=FACTORY_ONE,
                footprint=Footprint(cells=frozenset({FACTORY_CAPTURE_CELL})),
            ),
            *extra_interaction_points,
        ),
        spawn_positions={},
    )


def _war_base_anchor(war_base_id: EntityId, cell: tuple[int, int]) -> InteractionPoint:
    return InteractionPoint(
        id=f"{war_base_id.value}-capture",
        kind=InteractionKind.WARBASE_CAPTURE,
        structure_id=war_base_id,
        footprint=Footprint(cells=frozenset({cell})),
    )


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(id="fixture", map_id=MAP_ID, map_version=1, player_starting_warbases=1)
    return scenario, BootstrapMap(map_id=MAP_ID, version=1, width=SIZE, height=SIZE)


def _state(robots: tuple[Robot, ...] = ()) -> GameState:
    scenario, map_data = _scenario_and_map()
    state = new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=7)
    return state.with_robots(robots) if robots else state


def _robot(
    entity_id: str,
    owner: PlayerId,
    x: int,
    y: int,
    weapons: tuple[ModuleIdentity, ...],
    *,
    order: object | None = None,
    strength: int = 100,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=weapons)
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
        strength=strength,
    )


def _gunner(entity_id: str, owner: PlayerId, x: int, y: int, **kwargs: object) -> Robot:
    """A height-26 robot: tall enough to block a projectile, armed with a cannon."""
    return _robot(
        entity_id,
        owner,
        x,
        y,
        (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        **kwargs,  # type: ignore[arg-type]
    )


def _nuke_carrier(entity_id: str, owner: PlayerId, x: int, y: int) -> Robot:
    return _robot(entity_id, owner, x, y, (ModuleIdentity.NUCLEAR,))


def _of(events: tuple[object, ...], kind: type) -> list[object]:
    return [event for event in events if isinstance(event, kind)]


# --------------------------------------------------------------------------
# Direct fire
# --------------------------------------------------------------------------


def test_fire_command_creates_an_in_flight_projectile_through_engine_step() -> None:
    world = _world()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    state = _state((shooter,))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=shooter.entity_id,
        weapon=ModuleIdentity.CANNON,
        target_x=QUIET_X + 10,
        target_y=QUIET_Y,
    )
    state, events = step(state, [command], world=world)

    fired = _of(events, ProjectileFiredEvent)
    assert len(fired) == 1
    assert len(state.projectiles) == 1
    projectile = state.projectiles[0]
    # CR002.2 (#169): the first 2-cell move is made on the fire tick.
    assert (projectile.x, projectile.y) == (QUIET_X + 2, QUIET_Y)
    assert (projectile.dx, projectile.dy) == (1, 0)
    assert state.robot_for(shooter.entity_id).active_projectile_id == projectile.id  # type: ignore[union-attr]


def test_fire_command_from_a_player_who_does_not_own_the_robot_is_a_no_op() -> None:
    # Ownership is enforced by `validate_fire`, not by FireCommand's shape --
    # see that command's docstring.
    world = _world()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    state = _state((shooter,))

    command = FireCommand(
        player=PLAYER_TWO,
        sequence=0,
        entity_id=shooter.entity_id,
        weapon=ModuleIdentity.CANNON,
        target_x=QUIET_X + 10,
        target_y=QUIET_Y,
    )
    state, events = step(state, [command], world=world)

    assert _of(events, ProjectileFiredEvent) == []
    assert state.projectiles == ()
    assert state.robot_for(shooter.entity_id).active_projectile_id is None  # type: ignore[union-attr]


def test_projectile_advances_over_ticks_and_damages_the_robot_it_hits() -> None:
    world = _world()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    # Five moves away (2 cells each: the fire-tick move plus four cadence
    # advances), so the projectile travels several cadence ticks first.
    target = _gunner("robot-z", PLAYER_TWO, QUIET_X + 10, QUIET_Y)
    state = _state((shooter, target))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=shooter.entity_id,
        weapon=ModuleIdentity.CANNON,
        target_x=QUIET_X + 10,
        target_y=QUIET_Y,
    )
    state, _events = step(state, [command], world=world)

    # Ticks 2..(5*ADVANCE - 1): the projectile is still travelling (a direct
    # shot is held for the rest of its fire cycle, CR002.2 #169).
    for _tick in range(2, 5 * ADVANCE):
        state, events = step(state, [], world=world)
        assert _of(events, RobotDamagedEvent) == []
    assert len(state.projectiles) == 1
    assert state.projectiles[0].x == QUIET_X + 8

    state, events = step(state, [], world=world)

    terminated = _of(events, ProjectileTerminatedEvent)
    assert len(terminated) == 1
    assert terminated[0].hit_robot_id == target.entity_id  # type: ignore[attr-defined]
    assert terminated[0].weapon is ModuleIdentity.CANNON  # type: ignore[attr-defined]

    damaged = _of(events, RobotDamagedEvent)
    assert len(damaged) == 1
    assert damaged[0].damage == CANNON_DAMAGE  # type: ignore[attr-defined]
    assert state.robot_for(target.entity_id).strength == 100 - CANNON_DAMAGE  # type: ignore[union-attr]
    # The projectile is gone and the shooter's combat channel is free again.
    assert state.projectiles == ()
    assert state.robot_for(shooter.entity_id).active_projectile_id is None  # type: ignore[union-attr]


def test_a_lethal_hit_destroys_the_target_robot_through_engine_step() -> None:
    world = _world()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    target = _gunner("robot-z", PLAYER_TWO, QUIET_X + 1, QUIET_Y, strength=CANNON_DAMAGE)
    state = _state((shooter, target))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=shooter.entity_id,
        weapon=ModuleIdentity.CANNON,
        target_x=QUIET_X + 10,
        target_y=QUIET_Y,
    )
    # Adjacent target: hit by the first move, on the fire tick (CR002.2 #169).
    state, events = step(state, [command], world=world)

    destroyed = _of(events, RobotDestroyedEvent)
    assert len(destroyed) == 1
    assert destroyed[0].entity_id == target.entity_id  # type: ignore[attr-defined]
    assert _of(events, RobotDamagedEvent) == []
    assert state.robot_for(target.entity_id) is None
    assert len(state.robots) == 1


# --------------------------------------------------------------------------
# Nuclear
# --------------------------------------------------------------------------


def test_nuclear_fire_command_detonates_within_the_single_step_that_processed_it() -> None:
    world = _world()
    carrier = _nuke_carrier("robot-a", PLAYER_ONE, NUKE_X, NUKE_Y)
    bystander = _gunner("robot-z", PLAYER_TWO, NUKE_X + 2, NUKE_Y)
    far_away = _gunner("robot-y", PLAYER_TWO, 0, 0)
    state = _state((carrier, bystander, far_away))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=carrier.entity_id,
        weapon=ModuleIdentity.NUCLEAR,
        target_x=NUKE_X + 2,
        target_y=NUKE_Y,
    )
    state, events = step(state, [command], world=world)

    destroyed_ids = {event.entity_id for event in _of(events, RobotDestroyedEvent)}  # type: ignore[attr-defined]
    assert destroyed_ids == {carrier.entity_id, bystander.entity_id}
    assert state.robot_for(carrier.entity_id) is None
    assert state.robot_for(bystander.entity_id) is None
    # Outside the 9x9 robot window, untouched.
    assert state.robot_for(far_away.entity_id) is not None

    structures = _of(events, StructureDestroyedEvent)
    assert [event.structure_id for event in structures] == [FACTORY_ONE]  # type: ignore[attr-defined]
    assert state.structure_destruction == (FACTORY_ONE,)
    # No war base was in range, so no victory was evaluated.
    assert _of(events, VictoryEvent) == []


def test_nuclear_destruction_of_the_last_war_base_produces_victory_in_the_same_tick() -> None:
    # WAR_BASE_TWO is PLAYER_TWO's only war base; detonating on top of it
    # leaves PLAYER_ONE as the sole war-base owner.
    world = _world()
    carrier = _nuke_carrier("robot-a", PLAYER_ONE, SIZE - 2, SIZE - 2)
    state = _state((carrier,))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=carrier.entity_id,
        weapon=ModuleIdentity.NUCLEAR,
        target_x=SIZE - 1,
        target_y=SIZE - 1,
    )
    state, events = step(state, [command], world=world)

    war_bases = [
        event
        for event in _of(events, StructureDestroyedEvent)
        if event.structure_kind is CapturableStructureKind.WAR_BASE  # type: ignore[attr-defined]
    ]
    assert [event.structure_id for event in war_bases] == [WAR_BASE_TWO]  # type: ignore[attr-defined]

    victories = _of(events, VictoryEvent)
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_ONE  # type: ignore[attr-defined]
    assert victories[0].tick == state.tick  # type: ignore[attr-defined]


def _p1_second_war_base() -> WarBase:
    """A second PLAYER_ONE war base, 20 cells from WAR_BASE_ONE at (0, 0).

    Far enough that one nuclear blast cannot take both (and a blast destroys
    at most one building anyway): the duplicate-victory
    scenarios below need PLAYER_ONE to still own *one* war base after the
    second detonation, so the victory condition stays satisfied at more than
    one check site in the same tick.
    """
    return WarBase(
        id=WAR_BASE_ONE_B,
        components=(Component(x=20, y=0, height=3),),
        owner=PLAYER_ONE,
    )


def test_two_nuclear_fire_commands_in_one_tick_emit_exactly_one_victory_event() -> None:
    # Regression: each accepted nuclear FireCommand runs its own victory
    # check. Here BOTH find the condition satisfied -- the first because
    # PLAYER_TWO's only war base is destroyed (p1 2, p2 0), the second
    # because destroying one of PLAYER_ONE's two war bases still leaves
    # exactly one owner (p1 1, p2 0). Without the shared `victory_emitted`
    # guard this tick announces the match result twice (issue #79:
    # "repeated/redundant evaluation does not emit duplicate match-result
    # events").
    world = _world(
        extra_war_bases=(_p1_second_war_base(),),
        extra_interaction_points=(_war_base_anchor(WAR_BASE_ONE_B, (20, 0)),),
    )
    first = _nuke_carrier("robot-a", PLAYER_ONE, SIZE - 2, SIZE - 2)
    second = _nuke_carrier("robot-b", PLAYER_ONE, 20, 1)
    state = _state((first, second))

    commands = [
        FireCommand(
            player=PLAYER_ONE,
            sequence=0,
            entity_id=first.entity_id,
            weapon=ModuleIdentity.NUCLEAR,
            target_x=SIZE - 1,
            target_y=SIZE - 1,
        ),
        FireCommand(
            player=PLAYER_ONE,
            sequence=1,
            entity_id=second.entity_id,
            weapon=ModuleIdentity.NUCLEAR,
            target_x=20,
            target_y=0,
        ),
    ]
    state, events = step(state, commands, world=world)

    # Both detonations really happened, each destroying a different war base,
    # and PLAYER_ONE really does still hold one afterwards -- so the victory
    # condition was genuinely satisfied at both check sites.
    destroyed_war_bases = {
        event.structure_id  # type: ignore[attr-defined]
        for event in _of(events, StructureDestroyedEvent)
        if event.structure_kind is CapturableStructureKind.WAR_BASE  # type: ignore[attr-defined]
    }
    assert destroyed_war_bases == {WAR_BASE_TWO, WAR_BASE_ONE_B}
    assert WAR_BASE_ONE not in state.structure_destruction

    # ...yet exactly one match result was announced.
    victories = _of(events, VictoryEvent)
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_ONE  # type: ignore[attr-defined]


def test_victory_is_announced_once_when_a_nuke_and_a_capture_land_in_one_tick() -> None:
    # The third independent append site: Step 2d's capture-triggered check.
    # PLAYER_ONE completes a neutral-war-base capture on exactly the tick a
    # PLAYER_ONE nuke removes PLAYER_TWO's last war base, so Step 2c2's
    # nuclear check and Step 2d's capture check both find the same result.
    neutral = WarBase(
        id=NEUTRAL_WAR_BASE,
        components=(Component(x=5, y=5, height=3),),
        owner=None,
    )
    capture_cell = (6, 5)
    world = _world(
        extra_war_bases=(neutral,),
        extra_interaction_points=(
            InteractionPoint(
                id="warbase-neutral-capture",
                kind=InteractionKind.WARBASE_CAPTURE,
                structure_id=NEUTRAL_WAR_BASE,
                footprint=Footprint(cells=frozenset({capture_cell})),
            ),
        ),
    )
    capturer = _gunner("robot-c", PLAYER_ONE, *capture_cell)
    carrier = _nuke_carrier("robot-a", PLAYER_ONE, SIZE - 2, SIZE - 2)
    state = _state((carrier, capturer))

    # Occupy the neutral war base until one tick short of completion.
    for _tick in range(1, DEFAULT_RULES.capture_duration_ticks):
        state, events = step(state, [], world=world)
        assert _of(events, VictoryEvent) == []

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=carrier.entity_id,
        weapon=ModuleIdentity.NUCLEAR,
        target_x=SIZE - 1,
        target_y=SIZE - 1,
    )
    state, events = step(state, [command], world=world)

    # Both triggers really fired in this one tick.
    assert WAR_BASE_TWO in state.structure_destruction
    captured = [
        event
        for event in _of(events, StructureCapturedEvent)
        if event.structure_kind is CapturableStructureKind.WAR_BASE  # type: ignore[attr-defined]
    ]
    assert [event.structure_id for event in captured] == [NEUTRAL_WAR_BASE]  # type: ignore[attr-defined]

    victories = _of(events, VictoryEvent)
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_ONE  # type: ignore[attr-defined]


def test_a_destroyed_factory_stops_being_capturable_on_the_following_tick() -> None:
    # Absent Step 2d's switch from `capture.effective_world` to
    # `destruction.effective_world`, ``advance_capture`` would still see the
    # nuked factory and keep accruing capture progress against it.
    world = _world(factory_owner=PLAYER_ONE)
    invader = _gunner("robot-z", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    carrier = _nuke_carrier("robot-a", PLAYER_ONE, NUKE_X, NUKE_Y)
    state = _state((carrier, invader))

    state, _events = step(state, [], world=world)
    assert state.capture_progress_for(FACTORY_ONE) is not None

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=carrier.entity_id,
        weapon=ModuleIdentity.NUCLEAR,
        target_x=NUKE_X,
        target_y=NUKE_Y + 1,
    )
    state, _events = step(state, [command], world=world)

    assert FACTORY_ONE in state.structure_destruction
    # `destroy_structure` cleared the in-flight attempt (the invader was
    # inside the robot window too and is gone).
    assert state.capture_progress_for(FACTORY_ONE) is None

    # A *fresh* invader walks onto the very same capture cell. Only a
    # destruction-filtered world stops `advance_capture` accruing against a
    # structure that no longer exists.
    replacement = _gunner("robot-y", PLAYER_TWO, *FACTORY_CAPTURE_CELL)
    state = state.with_robots((*state.robots, replacement))

    state, _events = step(state, [], world=world)

    assert state.robot_for(replacement.entity_id) is not None
    assert state.capture_progress_for(FACTORY_ONE) is None
    assert state.capture_progress == ()


def _run_to_day_boundary(state: GameState, world: WorldMap) -> tuple[GameState, list[object]]:
    """Step until the next ``TICKS_PER_GAME_DAY`` boundary, collecting production."""
    produced: list[object] = []
    while state.tick % TICKS_PER_GAME_DAY != 0 or state.tick == 0:
        state, events = step(state, [], world=world)
        produced.extend(_of(events, DailyProductionApplied))
    return state, produced


def test_a_live_factory_produces_its_category_at_a_day_boundary() -> None:
    # The control for the test below: establish that this exact world/owner
    # really does pay out, so the "stops producing" assertion means something.
    world = _world(factory_owner=PLAYER_ONE)
    state = _state()

    state, produced = _run_to_day_boundary(state, world)

    assert state.tick == TICKS_PER_GAME_DAY
    by_player = {event.player: event for event in produced}  # type: ignore[attr-defined]
    assert dict(by_player[PLAYER_ONE].category_amounts)[FactoryType.CHASSIS] == (
        DEFAULT_RULES.factory_production_amount
    )
    pool = state.resource_pool_for(PLAYER_ONE)
    assert pool is not None
    assert pool.chassis == DEFAULT_RULES.factory_production_amount


def test_a_destroyed_factory_stops_producing_resources() -> None:
    # Step 9 (daily production) reads `world_for_step`, which Step 2d
    # recomputes from the latest state -- so a factory nuked earlier in the
    # match cannot pay out at the next day boundary. Mirrors the shape of
    # the capture test above: establish the live behavior (the control test
    # above), destroy, then prove the behavior stops.
    world = _world(factory_owner=PLAYER_ONE)
    carrier = _nuke_carrier("robot-a", PLAYER_ONE, FACTORY_CELL[0], FACTORY_CELL[1] + 1)
    state = _state((carrier,))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=carrier.entity_id,
        weapon=ModuleIdentity.NUCLEAR,
        target_x=FACTORY_CELL[0],
        target_y=FACTORY_CELL[1],
    )
    state, _events = step(state, [command], world=world)
    assert FACTORY_ONE in state.structure_destruction

    chassis_before = state.resource_pool_for(PLAYER_ONE)
    assert chassis_before is None or chassis_before.chassis == 0

    state, produced = _run_to_day_boundary(state, world)

    assert state.tick == TICKS_PER_GAME_DAY
    # The war base survived, so PLAYER_ONE still produces *general*
    # resources -- proving production ran at all, and that only the
    # destroyed factory's contribution is missing.
    by_player = {event.player: event for event in produced}  # type: ignore[attr-defined]
    assert PLAYER_ONE in by_player
    assert by_player[PLAYER_ONE].general_amount == DEFAULT_RULES.war_base_production_amount
    assert dict(by_player[PLAYER_ONE].category_amounts)[FactoryType.CHASSIS] == 0

    pool = state.resource_pool_for(PLAYER_ONE)
    assert pool is not None
    assert pool.chassis == 0


# --------------------------------------------------------------------------
# Autonomous engagement
# --------------------------------------------------------------------------


def test_search_destroy_order_damages_an_enemy_with_no_fire_command_at_all() -> None:
    world = _world()
    hunter = _gunner(
        "robot-a",
        PLAYER_ONE,
        QUIET_X,
        QUIET_Y,
        order=SearchDestroy(SearchDestroyTarget.ROBOT),
    )
    prey = _gunner("robot-z", PLAYER_TWO, QUIET_X + 3, QUIET_Y)
    state = _state((hunter, prey))

    damaged_events: list[object] = []
    for _tick in range(4 * ADVANCE):
        state, events = step(state, [], world=world)
        damaged_events.extend(_of(events, RobotDamagedEvent))
        damaged_events.extend(_of(events, RobotDestroyedEvent))
        if damaged_events:
            break

    assert damaged_events, "an autonomous Search & Destroy order never fired a damaging shot"
    survivor = state.robot_for(prey.entity_id)
    assert survivor is None or survivor.strength < 100


# --------------------------------------------------------------------------
# Determinism through the real engine pipeline
# --------------------------------------------------------------------------


def _combat_fixture() -> ReplayFixture:
    scenario, map_data = _scenario_and_map()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    target = _gunner(
        "robot-z",
        PLAYER_TWO,
        QUIET_X + 4,
        QUIET_Y,
        order=SearchDestroy(SearchDestroyTarget.ROBOT),
    )
    # Parked next to PLAYER_TWO's war base, far from the QUIET firefight, so
    # one fixture exercises projectile flight, mutual autonomous fire,
    # nuclear structure destruction and the victory check together.
    carrier = _nuke_carrier("robot-n", PLAYER_ONE, SIZE - 1, SIZE - 3)
    return ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=7,
        tick_count=6 * ADVANCE,
        commands_by_tick={
            1: (
                FireCommand(
                    player=PLAYER_ONE,
                    sequence=0,
                    entity_id=shooter.entity_id,
                    weapon=ModuleIdentity.CANNON,
                    target_x=QUIET_X + 10,
                    target_y=QUIET_Y,
                ),
            ),
            3 * ADVANCE: (
                FireCommand(
                    player=PLAYER_ONE,
                    sequence=0,
                    entity_id=carrier.entity_id,
                    weapon=ModuleIdentity.NUCLEAR,
                    target_x=SIZE - 1,
                    target_y=SIZE - 1,
                ),
            ),
        },
        world=_world(),
        initial_robots=(shooter, target, carrier),
    )


def test_combat_replay_is_deterministic_through_the_real_engine_pipeline() -> None:
    fixture = _combat_fixture()

    state_a, events_a = run_fixture(fixture)
    state_b, events_b = run_fixture(fixture)

    assert snapshot_to_json_string(state_a) == snapshot_to_json_string(state_b)
    assert events_a == events_b
    # The fixture must actually exercise combat, or determinism proves nothing.
    assert _of(events_a, ProjectileFiredEvent) != []
    assert _of(events_a, ProjectileTerminatedEvent) != []
    assert _of(events_a, RobotDamagedEvent) != []
    assert _of(events_a, StructureDestroyedEvent) != []
    assert _of(events_a, RobotDestroyedEvent) != []
    assert _of(events_a, VictoryEvent) != []


def test_combat_state_round_trips_into_the_snapshot() -> None:
    world = _world()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    target = _gunner("robot-z", PLAYER_TWO, QUIET_X + 4, QUIET_Y)
    state = _state((shooter, target))

    command = FireCommand(
        player=PLAYER_ONE,
        sequence=0,
        entity_id=shooter.entity_id,
        weapon=ModuleIdentity.CANNON,
        target_x=QUIET_X + 10,
        target_y=QUIET_Y,
    )
    state, _events = step(state, [command], world=world)

    snapshot = snapshot_to_json_string(state)
    assert '"projectiles": [' in snapshot
    assert '"active_projectile_id": "projectile-robot-a-1"' in snapshot
    assert '"strength": 100' in snapshot

    for _tick in range(2, 2 * ADVANCE + 1):
        state, _events = step(state, [], world=world)

    assert state.robot_for(target.entity_id).strength == 100 - CANNON_DAMAGE  # type: ignore[union-attr]
    assert f'"strength": {100 - CANNON_DAMAGE}' in snapshot_to_json_string(state)


# --------------------------------------------------------------------------
# CR002.2 (#169): at most one shot per robot per game cycle
# --------------------------------------------------------------------------


def test_autonomous_robot_next_to_its_target_fires_once_per_robot_update() -> None:
    """CR002.19 (#197) supersedes the once-per-cycle cadence for autonomous fire."""
    world = _world()
    hunter = _gunner(
        "robot-a", PLAYER_ONE, QUIET_X, QUIET_Y, order=SearchDestroy(target=SearchDestroyTarget.ROBOT)
    )
    prey = _gunner("robot-z", PLAYER_TWO, QUIET_X + 2, QUIET_Y, strength=10_000)  # 2×2 bodies side by side
    state = _state((hunter, prey))

    fire_ticks = []
    for _tick in range(1, 41):
        state, events = step(state, [], world=world)
        fire_ticks += [
            e.tick for e in _of(events, ProjectileFiredEvent) if e.source_robot_id == hunter.entity_id  # type: ignore[attr-defined]
        ]

    period = robot_move_duration_ticks(hunter, TerrainType.NORMAL, DEFAULT_RULES)
    assert fire_ticks == list(range(1, 41, period))
    assert period > DEFAULT_RULES.robot_fire_cycle_ticks


def test_direct_fire_every_tick_at_an_adjacent_target_fires_once_per_game_cycle() -> None:
    world = _world()
    shooter = _gunner("robot-a", PLAYER_ONE, QUIET_X, QUIET_Y)
    target = _gunner("robot-z", PLAYER_TWO, QUIET_X + 2, QUIET_Y, strength=10_000)  # 2×2 bodies side by side
    state = _state((shooter, target))

    fire_ticks = []
    for tick in range(1, 17):
        command = FireCommand(
            player=PLAYER_ONE,
            sequence=0,
            entity_id=shooter.entity_id,
            weapon=ModuleIdentity.CANNON,
            target_x=QUIET_X + 2,
            target_y=QUIET_Y,
        )
        state, events = step(state, [command], world=world)
        fire_ticks += [e.tick for e in _of(events, ProjectileFiredEvent)]  # type: ignore[attr-defined]
        assert state.tick == tick

    assert fire_ticks == [1, 4, 8, 12, 16]
