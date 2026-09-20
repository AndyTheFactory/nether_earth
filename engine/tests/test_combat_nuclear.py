"""Tests for nuclear detonation and structure destruction (issue #78, M6.8)."""

from __future__ import annotations

from nether_earth.capture import CapturableStructureKind, CaptureProgress, StructureOwnership
from nether_earth.commander import Commander, CommanderMode
from nether_earth.destruction import (
    RobotDestroyedEvent,
    StructureDestroyedEvent,
    destroy_structure,
    effective_world,
    execute_nuclear_detonation,
)
from nether_earth.docking import CommanderUndockedEvent
from nether_earth.events import EventSequencer
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Component, Factory, FactoryType, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

# --------------------------------------------------------------------------
# Fixtures (mirroring test_combat_damage.py / test_combat_projectile.py)
# --------------------------------------------------------------------------

RADIUS = DEFAULT_RULES.nuclear_radius_cells


def _world(
    width: int = 60,
    height: int = 60,
    war_bases: tuple[WarBase, ...] = (),
    factories: tuple[Factory, ...] = (),
) -> WorldMap:
    return WorldMap(
        map_id="combat-nuclear-test-map",
        version=1,
        width=width,
        height=height,
        terrain=TerrainGrid(
            width=width,
            height=height,
            cells={},
            default=TerrainType.NORMAL,
        ),
        war_bases=war_bases,
        factories=factories,
        blockers=(),
        interaction_points=(),
        spawn_positions={},
    )


def _robot(
    entity_id: str = "robot-1",
    owner: PlayerId = PLAYER_ONE,
    x: int = 0,
    y: int = 0,
    weapons: tuple[ModuleIdentity, ...] = (ModuleIdentity.NUCLEAR,),
    height: int = 13,
    strength: int = 100,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=weapons, electronics=None)
    return Robot(
        entity_id=EntityId(entity_id),
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=(ModuleIdentity.BIPOD, *weapons),
        height=height,
        strength=strength,
        active_projectile_id=None,
    )


def _war_base(entity_id: str, x: int, y: int, owner: PlayerId | None = None) -> WarBase:
    return WarBase(
        id=EntityId(entity_id),
        components=(Component(x=x, y=y, height=13),),
        owner=owner,
    )


def _factory(
    entity_id: str, x: int, y: int, owner: PlayerId | None = None
) -> Factory:
    return Factory(
        id=EntityId(entity_id),
        components=(Component(x=x, y=y, height=13),),
        factory_type=FactoryType.CHASSIS,
        owner=owner,
    )


def _state(
    robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
    capture_progress: tuple[CaptureProgress, ...] = (),
    structure_ownership: tuple[StructureOwnership, ...] = (),
    structure_destruction: tuple[EntityId, ...] = (),
) -> GameState:
    return create_game_state(
        0,
        (PLAYER_ONE, PLAYER_TWO),
        robots=list(robots),
        commanders=list(commanders),
        capture_progress=list(capture_progress),
        structure_ownership=list(structure_ownership),
        structure_destruction=list(structure_destruction),
    )


# --------------------------------------------------------------------------
# execute_nuclear_detonation
# --------------------------------------------------------------------------


def test_carrier_only_detonation_destroys_only_carrier() -> None:
    carrier = _robot(entity_id="carrier", x=10, y=10)
    far_robot = _robot(entity_id="far-robot", x=10 + RADIUS + 1, y=10, weapons=(ModuleIdentity.CANNON,))
    state = _state((carrier, far_robot))
    world = _world()

    new_state, events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=4)

    assert new_state.robot_for(carrier.entity_id) is None
    assert new_state.robot_for(far_robot.entity_id) is not None
    destroyed_events = [e for e in events if isinstance(e, RobotDestroyedEvent)]
    assert len(destroyed_events) == 1
    assert destroyed_events[0].entity_id == carrier.entity_id


def test_mixed_detonation_destroys_carrier_robot_factory_and_war_base_in_order() -> None:
    carrier = _robot(entity_id="carrier", x=10, y=10)
    other_robot = _robot(entity_id="other-robot", x=11, y=10, weapons=(ModuleIdentity.CANNON,))
    factory = _factory("factory-1", x=12, y=10, owner=PLAYER_TWO)
    war_base = _war_base("war-base-1", x=9, y=10, owner=PLAYER_TWO)
    state = _state((carrier, other_robot))
    world = _world(war_bases=(war_base,), factories=(factory,))

    new_state, events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=4)

    assert new_state.robot_for(carrier.entity_id) is None
    assert new_state.robot_for(other_robot.entity_id) is None
    assert new_state.structure_destroyed(factory.id)
    assert new_state.structure_destroyed(war_base.id)

    # Order: carrier -> robots (canonical id order) -> structures (canonical id order).
    robot_events = [
        e for e in events if isinstance(e, RobotDestroyedEvent)
    ]
    structure_events = [e for e in events if isinstance(e, StructureDestroyedEvent)]
    assert [e.entity_id.value for e in robot_events] == ["carrier", "other-robot"]
    assert [e.structure_id.value for e in structure_events] == ["factory-1", "war-base-1"]

    # Carrier event must appear before the other robot's event, which must
    # appear before any structure event, in the returned tuple.
    assert events.index(robot_events[0]) < events.index(robot_events[1])
    assert events.index(robot_events[1]) < events.index(structure_events[0])

    # The documented carrier -> robots -> structures order must also be
    # recoverable from event.sequence (strictly increasing), not merely from
    # tuple/list index -- this is what events.order_events actually sorts
    # by. A regression here (e.g. forwarding a possibly-None `sequencer`
    # straight into each destroy_robot/destroy_structure sub-call instead of
    # resolving it once up front) would silently produce sequence=0 for
    # every event while this ordered-by-index check above still passed.
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)  # strictly increasing, no duplicates


def test_radius_boundary_robot_at_exact_radius_is_destroyed_one_beyond_is_not() -> None:
    carrier = _robot(entity_id="carrier", x=0, y=0)
    at_boundary = _robot(
        entity_id="at-boundary", x=RADIUS, y=0, weapons=(ModuleIdentity.CANNON,)
    )
    beyond_boundary = _robot(
        entity_id="beyond-boundary", x=RADIUS + 1, y=0, weapons=(ModuleIdentity.CANNON,)
    )
    state = _state((carrier, at_boundary, beyond_boundary))
    world = _world()

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.robot_for(at_boundary.entity_id) is None
    assert new_state.robot_for(beyond_boundary.entity_id) is not None


def test_radius_boundary_structure_at_exact_radius_is_destroyed_one_beyond_is_not() -> None:
    carrier = _robot(entity_id="carrier", x=0, y=0)
    at_boundary = _factory("factory-at-boundary", x=RADIUS, y=0)
    beyond_boundary = _factory("factory-beyond-boundary", x=RADIUS + 1, y=0)
    state = _state((carrier,))
    world = _world(factories=(at_boundary, beyond_boundary))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(at_boundary.id)
    assert not new_state.structure_destroyed(beyond_boundary.id)


def test_commander_at_epicenter_survives_unchanged() -> None:
    carrier = _robot(entity_id="carrier", x=5, y=5)
    commander = Commander(
        player_id=PLAYER_TWO,
        mode=CommanderMode.FREE,
        x=5,
        y=5,
        altitude=0,
        docked_robot_id=None,
    )
    state = _state((carrier,), (commander,))
    world = _world()

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.commander_for(PLAYER_TWO) == commander


def test_detonation_clears_capture_progress_for_destroyed_structure() -> None:
    carrier = _robot(entity_id="carrier", x=0, y=0)
    factory = _factory("factory-1", x=0, y=0, owner=None)
    progress = CaptureProgress(
        structure_id=factory.id,
        capturing_player=PLAYER_ONE,
        robot_id=EntityId("some-capturer"),
        elapsed_ticks=10,
        required_ticks=1440,
    )
    state = _state((carrier,), capture_progress=(progress,))
    world = _world(factories=(factory,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(factory.id)
    assert new_state.capture_progress_for(factory.id) is None


def test_detonation_clears_structure_ownership_override_for_destroyed_structure() -> None:
    carrier = _robot(entity_id="carrier", x=0, y=0)
    war_base = _war_base("war-base-1", x=0, y=0, owner=PLAYER_TWO)
    override = StructureOwnership(structure_id=war_base.id, owner=PLAYER_ONE)
    state = _state((carrier,), structure_ownership=(override,))
    world = _world(war_bases=(war_base,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(war_base.id)
    assert new_state.structure_ownership_for(war_base.id) is None


def test_detonation_on_missing_carrier_is_noop() -> None:
    state = _state(())
    world = _world()

    new_state, events = execute_nuclear_detonation(
        state, world, EntityId("ghost"), tick=1
    )

    assert new_state is state
    assert events == ()


def test_detonation_relocates_commander_docked_to_a_robot_destroyed_by_the_blast() -> None:
    """A commander docked to a non-carrier robot caught in the blast radius.

    Exercises `destruction.py`'s docked-commander-safety branch through the
    nuclear-detonation path specifically (`execute_nuclear_detonation`'s
    sequencer-threading and carrier -> robots -> structures ordering),
    mirroring `test_combat_damage.py`'s
    ``test_destroy_robot_relocates_docked_commander_to_free`` assertion
    pattern for the single-``destroy_robot`` call path.
    """
    carrier = _robot(entity_id="carrier", x=10, y=10)
    docked_to = _robot(
        entity_id="docked-to", x=11, y=10, weapons=(ModuleIdentity.CANNON,), height=15
    )
    commander = Commander(
        player_id=PLAYER_TWO,
        mode=CommanderMode.DOCKED,
        x=11,
        y=10,
        altitude=15,
        docked_robot_id=docked_to.entity_id,
    )
    state = _state((carrier, docked_to), (commander,))
    world = _world()
    sequencer = EventSequencer()

    new_state, events = execute_nuclear_detonation(
        state, world, carrier.entity_id, tick=4, sequencer=sequencer
    )

    assert new_state.robot_for(docked_to.entity_id) is None

    updated_commander = new_state.commander_for(PLAYER_TWO)
    assert updated_commander is not None
    assert updated_commander.mode is CommanderMode.FREE
    assert updated_commander.docked_robot_id is None
    assert updated_commander.x == 11
    assert updated_commander.y == 10
    assert updated_commander.altitude == 15

    undock_events = [e for e in events if isinstance(e, CommanderUndockedEvent)]
    assert len(undock_events) == 1
    assert undock_events[0].robot_id == docked_to.entity_id
    assert undock_events[0].from_altitude == 15
    assert undock_events[0].to_altitude == 15

    # The undock event's sequence number must be consistent with the
    # detonation's overall carrier -> robots -> structures ordering: strictly
    # increasing and recoverable from event.sequence, not merely tuple index
    # (mirrors test_mixed_detonation_destroys_carrier_robot_factory_and_war_base_in_order's
    # sequencer-threading assertion pattern).
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_repeated_detonation_sequence_is_deterministic() -> None:
    def run() -> tuple[GameState, list[str]]:
        carrier = _robot(entity_id="carrier", x=10, y=10)
        other_robot = _robot(entity_id="other-robot", x=11, y=10, weapons=(ModuleIdentity.CANNON,))
        factory = _factory("factory-1", x=12, y=10, owner=PLAYER_TWO)
        war_base = _war_base("war-base-1", x=9, y=10, owner=PLAYER_TWO)
        state = _state((carrier, other_robot))
        world = _world(war_bases=(war_base,), factories=(factory,))
        sequencer = EventSequencer()

        state, events = execute_nuclear_detonation(
            state, world, carrier.entity_id, tick=4, sequencer=sequencer
        )
        return state, [repr(e) for e in events]

    state_a, log_a = run()
    state_b, log_b = run()

    assert state_a == state_b
    assert log_a == log_b


# --------------------------------------------------------------------------
# destroy_structure
# --------------------------------------------------------------------------


def test_destroy_structure_emits_event_and_marks_destroyed() -> None:
    state = _state(())

    new_state, event = destroy_structure(
        state, EntityId("factory-1"), CapturableStructureKind.FACTORY, tick=5
    )

    assert new_state.structure_destroyed(EntityId("factory-1"))
    assert isinstance(event, StructureDestroyedEvent)
    assert event.structure_id == EntityId("factory-1")
    assert event.structure_kind == CapturableStructureKind.FACTORY
    assert event.tick == 5


def test_destroy_structure_twice_is_identity_noop() -> None:
    state = _state(())

    first_state, first_event = destroy_structure(
        state, EntityId("factory-1"), CapturableStructureKind.FACTORY, tick=5
    )
    assert first_event is not None

    second_state, second_event = destroy_structure(
        first_state, EntityId("factory-1"), CapturableStructureKind.FACTORY, tick=6
    )

    assert second_state is first_state
    assert second_event is None


def test_destroy_structure_removes_only_matching_capture_progress() -> None:
    target_progress = CaptureProgress(
        structure_id=EntityId("factory-1"),
        capturing_player=PLAYER_ONE,
        robot_id=EntityId("robot-1"),
        elapsed_ticks=10,
        required_ticks=1440,
    )
    other_progress = CaptureProgress(
        structure_id=EntityId("factory-2"),
        capturing_player=PLAYER_ONE,
        robot_id=EntityId("robot-2"),
        elapsed_ticks=20,
        required_ticks=1440,
    )
    state = _state((), capture_progress=(target_progress, other_progress))

    new_state, _event = destroy_structure(
        state, EntityId("factory-1"), CapturableStructureKind.FACTORY, tick=5
    )

    assert new_state.capture_progress_for(EntityId("factory-1")) is None
    assert new_state.capture_progress_for(EntityId("factory-2")) is not None


def test_destroy_structure_removes_only_matching_ownership_override() -> None:
    target_override = StructureOwnership(structure_id=EntityId("war-base-1"), owner=PLAYER_ONE)
    other_override = StructureOwnership(structure_id=EntityId("war-base-2"), owner=PLAYER_TWO)
    state = _state((), structure_ownership=(target_override, other_override))

    new_state, _event = destroy_structure(
        state, EntityId("war-base-1"), CapturableStructureKind.WAR_BASE, tick=5
    )

    assert new_state.structure_ownership_for(EntityId("war-base-1")) is None
    assert new_state.structure_ownership_for(EntityId("war-base-2")) is not None


# --------------------------------------------------------------------------
# effective_world
# --------------------------------------------------------------------------


def test_effective_world_no_destruction_matches_capture_effective_world() -> None:
    from nether_earth.capture import effective_world as capture_effective_world

    factory = _factory("factory-1", x=1, y=1)
    war_base = _war_base("war-base-1", x=2, y=2)
    state = _state(())
    world = _world(war_bases=(war_base,), factories=(factory,))

    assert effective_world(world, state) == capture_effective_world(world, state)


def test_effective_world_excludes_destroyed_war_base_keeps_untouched_factory() -> None:
    factory = _factory("factory-1", x=1, y=1)
    war_base = _war_base("war-base-1", x=2, y=2)
    state = _state((), structure_destruction=(war_base.id,))
    world = _world(war_bases=(war_base,), factories=(factory,))

    result = effective_world(world, state)

    assert war_base not in result.war_bases
    assert war_base.id not in {wb.id for wb in result.war_bases}
    assert result.factories == (factory,)
