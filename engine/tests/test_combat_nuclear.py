"""Tests for nuclear detonation and structure destruction (issue #78, M6.8).

Blast shapes follow the Spectrum code (CR001.2, issue #149,
`_specs/resolved-questions.md` "Nuclear blast shape", `Lb99f_fire_nuclear_bomb`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import WorldMap, load_world_map
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType

# --------------------------------------------------------------------------
# Fixtures (mirroring test_combat_damage.py / test_combat_projectile.py)
# --------------------------------------------------------------------------

ORIGINAL_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"


def _anchor_point(structure: WarBase | Factory, kind: InteractionKind) -> InteractionPoint:
    """The structure's capture point at its single component cell: its blast anchor."""
    (component,) = structure.components
    return InteractionPoint(
        id=f"{structure.id.value}-capture",
        kind=kind,
        structure_id=structure.id,
        footprint=Footprint(cells=frozenset({(component.x, component.y)})),
    )


def _world(
    width: int = 60,
    height: int = 60,
    war_bases: tuple[WarBase, ...] = (),
    factories: tuple[Factory, ...] = (),
    *,
    anchors: bool = True,
) -> WorldMap:
    points: tuple[InteractionPoint, ...] = ()
    if anchors:
        points = (
            *(_anchor_point(wb, InteractionKind.WARBASE_CAPTURE) for wb in war_bases),
            *(_anchor_point(f, InteractionKind.FACTORY_CAPTURE) for f in factories),
        )
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
        interaction_points=points,
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
    far_robot = _robot(entity_id="far-robot", x=15, y=10, weapons=(ModuleIdentity.CANNON,))
    state = _state((carrier, far_robot))
    world = _world()

    new_state, events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=4)

    assert new_state.robot_for(carrier.entity_id) is None
    assert new_state.robot_for(far_robot.entity_id) is not None
    destroyed_events = [e for e in events if isinstance(e, RobotDestroyedEvent)]
    assert len(destroyed_events) == 1
    assert destroyed_events[0].entity_id == carrier.entity_id


def test_mixed_detonation_destroys_carrier_robot_and_one_building_in_order() -> None:
    carrier = _robot(entity_id="carrier", x=10, y=10)
    other_robot = _robot(entity_id="other-robot", x=11, y=10, weapons=(ModuleIdentity.CANNON,))
    # Both in range: war base dx=0, dy=|10+5-15|=0; factory dx=2, dy=|10+1-11|=0.
    war_base = _war_base("war-base-1", x=10, y=15, owner=PLAYER_TWO)
    factory = _factory("factory-1", x=12, y=11, owner=PLAYER_TWO)
    state = _state((carrier, other_robot))
    world = _world(war_bases=(war_base,), factories=(factory,))

    new_state, events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=4)

    assert new_state.robot_for(carrier.entity_id) is None
    assert new_state.robot_for(other_robot.entity_id) is None
    # At most one building per detonation; war bases are scanned first.
    assert new_state.structure_destroyed(war_base.id)
    assert not new_state.structure_destroyed(factory.id)

    # Order: carrier -> robots (canonical id order) -> the one building.
    robot_events = [e for e in events if isinstance(e, RobotDestroyedEvent)]
    structure_events = [e for e in events if isinstance(e, StructureDestroyedEvent)]
    assert [e.entity_id.value for e in robot_events] == ["carrier", "other-robot"]
    assert [e.structure_id.value for e in structure_events] == ["war-base-1"]
    assert events.index(robot_events[0]) < events.index(robot_events[1])
    assert events.index(robot_events[1]) < events.index(structure_events[0])

    # The order must also be recoverable from event.sequence (what
    # events.order_events sorts by): one shared sequencer, strictly increasing.
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


# Carrier at (20, 20). War-base dy is measured from carrier.y + 1 + 4 = 25,
# factory dy from carrier.y + 1 = 21 (`_specs/resolved-questions.md` "Nuclear blast shape").
@pytest.mark.parametrize(
    ("dx", "dy", "hit"),
    [
        (6, 3, True),  # axis 6 < 7, sum 9 < 10
        (7, 0, False),  # axis 7 not < 7
        (0, 7, False),
        (0, 6, True),
        (0, -6, True),  # |dy|: anchor above the measuring row
        (0, -7, False),
        (5, 5, False),  # sum 10 not < 10
        (4, 5, True),
        (-6, 3, True),
        (-7, 0, False),
    ],
)
def test_war_base_blast_range_boundaries(dx: int, dy: int, hit: bool) -> None:
    carrier = _robot(entity_id="carrier", x=20, y=20)
    war_base = _war_base("war-base-1", x=20 + dx, y=25 + dy)
    state = _state((carrier,))
    world = _world(war_bases=(war_base,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(war_base.id) is hit


@pytest.mark.parametrize(
    ("dx", "dy", "hit"),
    [
        (4, 2, True),  # axis 4 < 5, sum 6 < 7
        (5, 0, False),  # axis 5 not < 5
        (0, 5, False),
        (0, 4, True),
        (0, -4, True),
        (0, -5, False),
        (3, 4, False),  # sum 7 not < 7
        (3, 3, True),
        (-4, 2, True),
        (-5, 0, False),
    ],
)
def test_factory_blast_range_boundaries(dx: int, dy: int, hit: bool) -> None:
    carrier = _robot(entity_id="carrier", x=20, y=20)
    factory = _factory("factory-1", x=20 + dx, y=21 + dy)
    state = _state((carrier,))
    world = _world(factories=(factory,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(factory.id) is hit


def test_building_dy_is_measured_from_the_offset_carrier_row() -> None:
    """Anchors are measured from carrier.y + 1 (+4 more for war bases), not carrier.y."""
    carrier = _robot(entity_id="carrier", x=20, y=20)
    war_base = _war_base("war-base-1", x=23, y=20)  # dx 3, dy |25-20| = 5, sum 8: hit
    factory = _factory("factory-1", x=24, y=17)  # dx 4, dy |21-17| = 4, sum 8: miss
    state = _state((carrier,))
    world = _world(war_bases=(war_base,), factories=(factory,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(war_base.id)
    assert not new_state.structure_destroyed(factory.id)


@pytest.mark.parametrize(
    ("dx", "dy", "hit"),
    [
        # Row widths 5, 7, 9, 9, 9, 9, 9, 7, 5 for dy = -4 .. +4.
        (2, -4, True),
        (3, -4, False),
        (-2, -4, True),
        (-3, -4, False),
        (3, -3, True),
        (4, -3, False),
        (4, -2, True),
        (5, -2, False),
        (4, 0, True),
        (-4, 0, True),
        (5, 0, False),
        (4, 2, True),
        (-3, 3, True),
        (-4, 3, False),
        (2, 4, True),
        (-2, 4, True),
        (3, 4, False),
        (0, 5, False),
        (0, -5, False),
    ],
)
def test_robot_window_corners(dx: int, dy: int, hit: bool) -> None:
    carrier = _robot(entity_id="carrier", x=20, y=20)
    target = _robot(
        entity_id="target", owner=PLAYER_TWO, x=20 + dx, y=20 + dy, weapons=(ModuleIdentity.CANNON,)
    )
    state = _state((carrier, target))
    world = _world()

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert (new_state.robot_for(target.entity_id) is None) is hit


def test_robot_window_is_clipped_at_the_map_edge() -> None:
    carrier = _robot(entity_id="carrier", x=1, y=0)
    corner = _robot(entity_id="corner", x=0, y=0, weapons=(ModuleIdentity.CANNON,))
    lower = _robot(entity_id="lower", x=3, y=4, weapons=(ModuleIdentity.CANNON,))
    outside = _robot(entity_id="outside", x=4, y=4, weapons=(ModuleIdentity.CANNON,))
    state = _state((carrier, corner, lower, outside))
    world = _world(width=10, height=10)

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.robot_for(corner.entity_id) is None
    assert new_state.robot_for(lower.entity_id) is None
    assert new_state.robot_for(outside.entity_id) is not None


def test_friendly_robots_in_the_window_are_destroyed() -> None:
    carrier = _robot(entity_id="carrier", owner=PLAYER_ONE, x=20, y=20)
    friend = _robot(entity_id="friend", owner=PLAYER_ONE, x=21, y=21, weapons=(ModuleIdentity.CANNON,))
    enemy = _robot(entity_id="enemy", owner=PLAYER_TWO, x=19, y=18, weapons=(ModuleIdentity.CANNON,))
    state = _state((carrier, friend, enemy))

    new_state, _events = execute_nuclear_detonation(state, _world(), carrier.entity_id, tick=1)

    assert new_state.robot_for(friend.entity_id) is None
    assert new_state.robot_for(enemy.entity_id) is None


def test_two_factories_in_range_only_the_first_in_map_order_is_destroyed() -> None:
    """Map declaration order is the Spectrum building-index order, not id-string order."""
    carrier = _robot(entity_id="carrier", x=20, y=20)
    first = _factory("factory-2", x=21, y=21)
    second = _factory("factory-10", x=20, y=21)
    state = _state((carrier,))
    world = _world(factories=(first, second))

    new_state, events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(first.id)
    assert not new_state.structure_destroyed(second.id)
    assert [e.structure_id for e in events if isinstance(e, StructureDestroyedEvent)] == [first.id]


def test_two_war_bases_in_range_only_the_first_is_destroyed() -> None:
    carrier = _robot(entity_id="carrier", x=20, y=20)
    first = _war_base("war-base-1", x=22, y=25, owner=PLAYER_ONE)
    second = _war_base("war-base-2", x=20, y=25, owner=PLAYER_TWO)
    state = _state((carrier,))
    world = _world(war_bases=(first, second))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(first.id)
    assert not new_state.structure_destroyed(second.id)


def test_already_destroyed_building_is_skipped_for_the_next_in_range() -> None:
    carrier = _robot(entity_id="carrier", x=20, y=20)
    war_base = _war_base("war-base-1", x=20, y=25)
    factory = _factory("factory-1", x=20, y=21)
    state = _state((carrier,), structure_destruction=(war_base.id,))
    world = _world(war_bases=(war_base,), factories=(factory,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(factory.id)


def test_building_ownership_is_not_checked() -> None:
    carrier = _robot(entity_id="carrier", owner=PLAYER_ONE, x=20, y=20)
    own_base = _war_base("war-base-1", x=20, y=25, owner=PLAYER_ONE)
    state = _state((carrier,))
    world = _world(war_bases=(own_base,))

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert new_state.structure_destroyed(own_base.id)


def test_building_without_a_capture_anchor_is_never_in_range() -> None:
    carrier = _robot(entity_id="carrier", x=20, y=20)
    war_base = _war_base("war-base-1", x=20, y=25)
    state = _state((carrier,))
    world = _world(war_bases=(war_base,), anchors=False)

    new_state, _events = execute_nuclear_detonation(state, world, carrier.entity_id, tick=1)

    assert not new_state.structure_destroyed(war_base.id)


def test_original_map_anchors_match_the_spectrum_building_table() -> None:
    """The engine anchor is the Spectrum building-struct (x, y), in building-index order.

    `Lbf46_warbases_factories_part1` / `Lbf6e_warbases_factories_part2`
    (x >= 256 stored as x - 256), filtered by type 0 (war base) / 1-6 (factory).
    """
    world = load_world_map(ORIGINAL_MAP_PATH)
    spectrum_war_bases = [(22, 9), (261, 8), (369, 8), (494, 8)]
    spectrum_factories = [
        (39, 6), (53, 3), (62, 10), (79, 3), (97, 3), (125, 3), (140, 11), (160, 5),
        (180, 3), (217, 3), (227, 9), (241, 3), (282, 3), (288, 13), (296, 3), (311, 7),
        (322, 3), (386, 3), (401, 7), (416, 3), (436, 5), (446, 3), (456, 10), (466, 3),
    ]

    def anchor(structure_id: EntityId, kind: InteractionKind) -> tuple[int, int]:
        (point,) = world.interaction_points_for(structure_id, kind=kind)
        (cell,) = point.footprint.cells
        return cell

    assert [
        anchor(wb.id, InteractionKind.WARBASE_CAPTURE) for wb in world.war_bases
    ] == spectrum_war_bases
    assert [
        anchor(f.id, InteractionKind.FACTORY_CAPTURE) for f in world.factories
    ] == spectrum_factories


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
    """A commander docked to a non-carrier robot caught in the blast window.

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


def test_carrier_on_the_capture_anchor_destroys_that_building() -> None:
    """S&D structure arrival detonates on the capture cell (OQ §19); §20 must reach it.

    War base: dx 0, dy |y + 1 + 4 - y| = 5 (< 7, sum 5 < 10). Factory: dx 0, dy 1.
    """
    carrier = _robot(entity_id="carrier", x=20, y=20)
    war_base = _war_base("war-base-1", x=20, y=20)
    factory = _factory("factory-1", x=20, y=20)

    wb_state, _ = execute_nuclear_detonation(
        _state((carrier,)), _world(war_bases=(war_base,)), carrier.entity_id, tick=1
    )
    f_state, _ = execute_nuclear_detonation(
        _state((carrier,)), _world(factories=(factory,)), carrier.entity_id, tick=1
    )

    assert wb_state.structure_destroyed(war_base.id)
    assert f_state.structure_destroyed(factory.id)


def test_original_map_detonation_on_each_capture_anchor_destroys_its_own_building() -> None:
    """On the original map no other building wins the scan from any capture anchor."""
    world = load_world_map(ORIGINAL_MAP_PATH)
    structures: list[tuple[EntityId, InteractionKind]] = [
        *((wb.id, InteractionKind.WARBASE_CAPTURE) for wb in world.war_bases),
        *((f.id, InteractionKind.FACTORY_CAPTURE) for f in world.factories),
    ]
    for structure_id, kind in structures:
        (point,) = world.interaction_points_for(structure_id, kind=kind)
        (x, y) = min(point.footprint.cells)
        carrier = _robot(entity_id="carrier", x=x, y=y)

        new_state, _events = execute_nuclear_detonation(_state((carrier,)), world, carrier.entity_id, tick=1)

        assert new_state.structure_destruction == (structure_id,), structure_id
