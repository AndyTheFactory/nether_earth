"""M5 snapshot/replay integration tests (issue #67, M5.8).

Proves the Milestone 5 authoritative state -- in-progress robot moves,
destination reservations, standing orders, engagement intent, and capture
progress/ownership -- is fully carried by the deterministic
snapshot/replay contracts established in M1.

The replay contract here is the codebase's existing one (see `replay.py`):
"the same fixture (map + scenario + seed + command stream + initial
entities) replayed through ``engine.new_game``/``engine.step`` produces an
identical final state, snapshot, and event sequence". There is deliberately
no ``from_snapshot`` restore path -- `snapshot.py` states that a generic
``GameState``-from-snapshot deserializer is out of scope, and replay
reconstructs state by re-simulating rather than by loading a snapshot.

Every scenario in this file is therefore run twice through ``run_fixture``
and compared on all three of: final ``GameState``, canonical snapshot JSON
string (the "final hash" of the acceptance criteria), and the full ordered
event sequence.
"""

from __future__ import annotations

import json

from nether_earth.capture import (
    CapturableStructureKind,
    StructureCapturedEvent,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commands import Command
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind, InteractionPoint
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.movement import RobotMoveStartedEvent
from nether_earth.occupancy import unit_footprint_cells
from nether_earth.orders import (
    Advance,
    RobotEngagementIntentEvent,
    RobotOrderChangedEvent,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    SetRobotOrderCommand,
    StopAndDefend,
)
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.reservations import DestinationContentionResolvedEvent, reservations_from_state
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import snapshot_to_json_string, to_snapshot
from nether_earth.state import GameState
from nether_earth.structures import Component, Factory, FactoryType, Footprint, WarBase
from nether_earth.terrain import TerrainGrid, TerrainType
from nether_earth.victory import VictoryEvent

MAP_ID = "test-m5-replay"
MAP_SIZE = 20

WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")
FACTORY_ONE = EntityId("factory-1")

#: The contested neutral-factory capture point: two cells, one per robot.
#: ``ROBOT_WEST``/``ROBOT_EAST`` start one step west/east of it; their 2×2
#: destination bodies (CR002.3) overlap, so they contend on the same tick.
#: (Two 2×2 robots can never claim the same anchor: a robot one step from it
#: already overlaps the other's body.)
FACTORY_CAPTURE_CELLS = ((5, 5), (6, 5))
#: PLAYER_ONE's war-base capture cell; PLAYER_TWO's ``ROBOT_CAPTOR`` stands
#: on it from tick 0.
WARBASE_CAPTURE_CELL = (2, 12)
#: A ditch cell no chassis but anti-grav may enter -- the deterministic
#: rejected-movement case.
DITCH_CELL = (16, 16)

ROBOT_WEST = EntityId("robot-a-west")
ROBOT_EAST = EntityId("robot-b-east")
ROBOT_CAPTOR = EntityId("robot-c-captor")
ROBOT_DITCH = EntityId("robot-d-ditch")

SEED = 20260919

#: One cell of ordinary-terrain movement for the tracks chassis every robot
#: in this module uses. A move started on tick ``t`` resolves on tick
#: ``t + TRACKS_MOVE_TICKS``.
TRACKS_MOVE_TICKS = DEFAULT_RULES.robot_move_ticks_tracks_normal


def _footprint(cell: tuple[int, int]) -> Footprint:
    return Footprint(cells=frozenset({cell}))


def _world() -> WorldMap:
    """Return the shared M5 test world.

    ``FACTORY_ONE`` is neutral (instant acquisition on qualifying
    occupation); both war bases are player-owned, so ``WAR_BASE_ONE``
    requires the full continuous-occupation capture duration.
    """
    return WorldMap(
        map_id=MAP_ID,
        version=1,
        width=MAP_SIZE,
        height=MAP_SIZE,
        terrain=TerrainGrid(
            width=MAP_SIZE, height=MAP_SIZE, cells={DITCH_CELL: TerrainType.DITCH}
        ),
        war_bases=(
            WarBase(id=WAR_BASE_ONE, components=(Component(x=0, y=0, height=3),), owner=PLAYER_ONE),
            WarBase(
                id=WAR_BASE_TWO,
                components=(Component(x=19, y=19, height=3),),
                owner=PLAYER_TWO,
            ),
        ),
        factories=(
            Factory(
                id=FACTORY_ONE,
                components=(Component(x=6, y=6, height=3),),
                factory_type=FactoryType.CHASSIS,
                owner=None,
            ),
        ),
        blockers=(),
        interaction_points=(
            InteractionPoint(
                id="factory-1-capture",
                kind=InteractionKind.FACTORY_CAPTURE,
                structure_id=FACTORY_ONE,
                footprint=Footprint(cells=frozenset(FACTORY_CAPTURE_CELLS)),
            ),
            InteractionPoint(
                id="warbase-p1-capture",
                kind=InteractionKind.WARBASE_CAPTURE,
                structure_id=WAR_BASE_ONE,
                footprint=_footprint(WARBASE_CAPTURE_CELL),
            ),
        ),
        spawn_positions={},
    )


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="m5-replay-scenario",
        map_id=MAP_ID,
        map_version=1,
        player_starting_warbases=1,
    )
    return scenario, BootstrapMap(map_id=MAP_ID, version=1, width=MAP_SIZE, height=MAP_SIZE)


def _robot(entity_id: EntityId, owner: PlayerId, x: int, y: int) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.CANNON,))
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=entity_id, owner=owner, x=x, y=y, build=build, stack=stack, height=height
    )


def _fixture(
    *,
    tick_count: int,
    commands_by_tick: dict[int, tuple[Command, ...]] | None = None,
    initial_robots: tuple[Robot, ...] = (),
    commanders: tuple[Commander, ...] = (),
) -> ReplayFixture:
    scenario, map_data = _scenario_and_map()
    return ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=SEED,
        tick_count=tick_count,
        commands_by_tick=commands_by_tick or {},
        world=_world(),
        commanders=commanders,
        initial_robots=initial_robots,
    )


def _replay_twice(fixture: ReplayFixture) -> tuple[GameState, tuple[Event, ...]]:
    """Run ``fixture`` twice and assert full determinism, returning run A's result.

    Compares all three surfaces the acceptance criteria name: the final
    ``GameState`` itself, its canonical snapshot (both the structure and the
    single-string "hash" form), and the complete ordered event sequence.
    """
    state_a, events_a = run_fixture(fixture)
    state_b, events_b = run_fixture(fixture)

    assert state_a == state_b
    assert to_snapshot(state_a) == to_snapshot(state_b)
    assert snapshot_to_json_string(state_a) == snapshot_to_json_string(state_b)
    assert events_a == events_b
    return state_a, events_a


# --------------------------------------------------------------------------
# Contention / in-progress movement / order lifecycle
# --------------------------------------------------------------------------


def _contention_robots() -> tuple[Robot, ...]:
    west = FACTORY_CAPTURE_CELLS[0][0] - 1, FACTORY_CAPTURE_CELLS[0][1]
    east = FACTORY_CAPTURE_CELLS[1][0] + 1, FACTORY_CAPTURE_CELLS[1][1]
    return (
        _robot(ROBOT_WEST, PLAYER_ONE, *west),
        _robot(ROBOT_EAST, PLAYER_TWO, *east),
    )


def _contention_commands() -> dict[int, tuple[Command, ...]]:
    """Order both robots onto the same neutral-factory capture point at tick 1."""
    return {
        1: (
            SetRobotOrderCommand(
                player=PLAYER_ONE,
                sequence=1,
                entity_id=ROBOT_WEST,
                order=SearchCapture(target=SearchCaptureTarget.NEUTRAL_FACTORY),
            ),
            SetRobotOrderCommand(
                player=PLAYER_TWO,
                sequence=2,
                entity_id=ROBOT_EAST,
                order=SearchCapture(target=SearchCaptureTarget.NEUTRAL_FACTORY),
            ),
        )
    }


def test_same_tick_contention_replays_with_an_identical_winner() -> None:
    """Both robots claim overlapping bodies on one tick; the seeded winner is stable."""
    fixture = _fixture(
        tick_count=3,
        commands_by_tick=_contention_commands(),
        initial_robots=_contention_robots(),
    )

    state, events = _replay_twice(fixture)

    contentions = [e for e in events if isinstance(e, DestinationContentionResolvedEvent)]
    assert len(contentions) == 1
    started = [e for e in events if isinstance(e, RobotMoveStartedEvent)]
    assert len(started) == 1

    winner = state.robot_for(started[0].entity_id)
    assert winner is not None
    assert winner.movement is not None
    assert (winner.movement.to_x, winner.movement.to_y) in FACTORY_CAPTURE_CELLS


def test_in_progress_move_is_serialized_mid_flight_and_replays_identically() -> None:
    """The final snapshot of a run cut mid-move carries the live transition."""
    assert TRACKS_MOVE_TICKS > 3  # the fixture below must end mid-move
    fixture = _fixture(
        tick_count=3,
        commands_by_tick=_contention_commands(),
        initial_robots=_contention_robots(),
    )

    state, _events = _replay_twice(fixture)

    in_flight = [robot for robot in state.robots if robot.movement is not None]
    assert len(in_flight) == 1
    robot = in_flight[0]
    assert robot.movement is not None
    assert robot.movement.started_tick == 1
    assert robot.movement.duration_ticks == TRACKS_MOVE_TICKS
    # Authoritative position is still the origin cell: only rendering may
    # interpolate towards the destination.
    assert (robot.x, robot.y) not in FACTORY_CAPTURE_CELLS
    destination = (robot.movement.to_x, robot.movement.to_y)
    assert destination in FACTORY_CAPTURE_CELLS

    snapshot = to_snapshot(state)
    serialized = {entry["entity_id"]: entry for entry in snapshot["robots"]}
    assert serialized[robot.entity_id.to_json()]["movement"] == {
        "entity_id": robot.entity_id.to_json(),
        "from_x": robot.x,
        "from_y": robot.y,
        "to_x": destination[0],
        "to_y": destination[1],
        "started_tick": 1,
        "duration_ticks": TRACKS_MOVE_TICKS,
    }


def test_reservations_are_fully_derivable_from_the_serialized_movement_state() -> None:
    """No separate ``reservations`` snapshot key is needed (or wanted).

    ``reservations_from_state`` is a pure projection of ``state.robots``'
    in-flight transitions, every one of which the snapshot serializes -- so
    the reservation table can be rebuilt from the snapshotted robots alone,
    and a second serialized copy could only drift from it.
    """
    fixture = _fixture(
        tick_count=3,
        commands_by_tick=_contention_commands(),
        initial_robots=_contention_robots(),
    )

    state, _events = _replay_twice(fixture)

    table = reservations_from_state(state)
    snapshot = to_snapshot(state)
    # Each in-flight move reserves its whole 2×2 destination body (CR002.3).
    rebuilt = {
        cell: entry["movement"]["entity_id"]
        for entry in snapshot["robots"]
        if entry["movement"] is not None
        for cell in unit_footprint_cells(entry["movement"]["to_x"], entry["movement"]["to_y"])
    }
    assert rebuilt
    assert rebuilt == {
        cell: holder.to_json() for cell, holder in table.holders.items()
    }
    assert "reservations" not in snapshot


def test_order_lifecycle_and_neutral_capture_replay_identically() -> None:
    """The winner arrives, captures the neutral factory, and both orders settle.

    Since the owner decision of 2026-09-23 the neutral factory costs a full
    ``capture_duration_ticks`` of continuous occupation like any other
    structure, so the fixture runs long enough for that countdown to finish.
    """
    fixture = _fixture(
        tick_count=TRACKS_MOVE_TICKS + 4 + DEFAULT_RULES.capture_duration_ticks,
        commands_by_tick=_contention_commands(),
        initial_robots=_contention_robots(),
    )

    state, events = _replay_twice(fixture)

    acquired = [e for e in events if isinstance(e, StructureCapturedEvent)]
    assert len(acquired) == 1
    assert acquired[0].structure_id == FACTORY_ONE
    assert acquired[0].previous_owner is None
    assert acquired[0].structure_kind is CapturableStructureKind.FACTORY

    ownership = state.structure_ownership_for(FACTORY_ONE)
    assert ownership is not None
    assert ownership.owner == acquired[0].new_owner

    # Ownership is serialized, so a snapshot alone reports who holds what.
    snapshot = to_snapshot(state)
    assert snapshot["structure_ownership"] == [
        {"structure_id": FACTORY_ONE.to_json(), "owner": ownership.owner.to_json()}
    ]

    # Final standing orders survive into the snapshot, stored target
    # included (CR003.2): Search & Capture never completes, so with no
    # neutral factory left a robot keeps the order and idles. Asserted over
    # whoever is still alive rather than over both contenders by name: the
    # fixture now runs the full capture countdown, and over that many ticks
    # the two adjacent contenders shoot each other (CR003.3 piece heights
    # make every robot tall enough to be hit), so one of them is gone.
    assert all(robot.order is not None for robot in state.robots)
    serialized_orders = {
        entry["entity_id"]: entry["order"] for entry in snapshot["robots"]
    }
    held = {
        "kind": "search_capture",
        "target": "neutral_factory",
        "structure_id": FACTORY_ONE.to_json(),
    }
    assert serialized_orders
    assert set(serialized_orders) <= {ROBOT_WEST.to_json(), ROBOT_EAST.to_json()}
    assert all(order == held for order in serialized_orders.values())
    assert any(isinstance(e, RobotOrderChangedEvent) for e in events)


def test_engagement_intent_is_re_derived_identically_on_every_replay() -> None:
    """Intent is transient (event-only), so determinism is what must hold.

    ``EngagementIntent`` is stored on neither ``Robot`` nor ``GameState``:
    ``evaluate_orders`` recomputes it every tick from the robot state the
    snapshot *does* carry. This test pins the consequence that matters for
    M6: the same fixture yields byte-identical intent events every run.
    """
    fixture = _fixture(
        tick_count=6,
        commands_by_tick={
            1: (
                SetRobotOrderCommand(
                    player=PLAYER_ONE,
                    sequence=1,
                    entity_id=ROBOT_WEST,
                    order=StopAndDefend(),
                ),
                SetRobotOrderCommand(
                    player=PLAYER_TWO,
                    sequence=2,
                    entity_id=ROBOT_EAST,
                    order=SearchDestroy(target=SearchDestroyTarget.ROBOT),
                ),
            )
        },
        initial_robots=_contention_robots(),
    )

    _state, events = _replay_twice(fixture)

    intents = [e for e in events if isinstance(e, RobotEngagementIntentEvent)]
    assert intents, "the two adjacent hostile robots must produce engagement intent"
    assert {intent.intent.robot_id for intent in intents} == {ROBOT_WEST, ROBOT_EAST}


# --------------------------------------------------------------------------
# Capture: completion (ownership + victory) and interruption
# --------------------------------------------------------------------------


def test_capture_completion_ownership_and_victory_replay_identically() -> None:
    """A full continuous-occupation war-base capture, twice, event for event."""
    captor = _robot(ROBOT_CAPTOR, PLAYER_TWO, *WARBASE_CAPTURE_CELL)
    fixture = _fixture(
        tick_count=DEFAULT_RULES.capture_duration_ticks + 1,
        initial_robots=(captor,),
    )

    state, events = _replay_twice(fixture)

    captured = [
        e
        for e in events
        if isinstance(e, StructureCapturedEvent)
        and e.structure_kind is CapturableStructureKind.WAR_BASE
    ]
    assert len(captured) == 1
    assert captured[0].structure_id == WAR_BASE_ONE
    assert captured[0].previous_owner == PLAYER_ONE
    assert captured[0].new_owner == PLAYER_TWO
    assert captured[0].tick == DEFAULT_RULES.capture_duration_ticks

    # PLAYER_ONE's only war base changed hands, so victory is evaluated in
    # the same authoritative step -- and replays identically.
    victories = [e for e in events if isinstance(e, VictoryEvent)]
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_TWO
    assert victories[0].tick == captured[0].tick

    ownership = state.structure_ownership_for(WAR_BASE_ONE)
    assert ownership is not None
    assert ownership.owner == PLAYER_TWO
    # The completed attempt leaves no progress record behind.
    assert state.capture_progress == ()
    assert to_snapshot(state)["capture_progress"] == []


def test_in_progress_capture_progress_is_serialized() -> None:
    captor = _robot(ROBOT_CAPTOR, PLAYER_TWO, *WARBASE_CAPTURE_CELL)
    fixture = _fixture(tick_count=7, initial_robots=(captor,))

    state, _events = _replay_twice(fixture)

    assert to_snapshot(state)["capture_progress"] == [
        {
            "structure_id": WAR_BASE_ONE.to_json(),
            "capturing_player": PLAYER_TWO.to_json(),
            "robot_id": ROBOT_CAPTOR.to_json(),
            "elapsed_ticks": 7,
            "required_ticks": DEFAULT_RULES.capture_duration_ticks,
        }
    ]


def test_interrupted_capture_resets_progress_to_zero_identically() -> None:
    """The captor is ordered away mid-capture; progress resets, both runs."""
    captor = _robot(ROBOT_CAPTOR, PLAYER_TWO, *WARBASE_CAPTURE_CELL)
    fixture = _fixture(
        # Long enough for the departure move (started on tick 2) to resolve
        # and for the interruption to be observed for several further ticks.
        tick_count=TRACKS_MOVE_TICKS + 8,
        commands_by_tick={
            2: (
                SetRobotOrderCommand(
                    player=PLAYER_TWO,
                    sequence=1,
                    entity_id=ROBOT_CAPTOR,
                    order=Advance(distance_miles=2),
                ),
            )
        },
        initial_robots=(captor,),
    )

    state, events = _replay_twice(fixture)

    robot = state.robot_for(ROBOT_CAPTOR)
    assert robot is not None
    assert (robot.x, robot.y) != WARBASE_CAPTURE_CELL, "the captor must have left the footprint"

    # Interruption resets progress to zero immediately -- represented as the
    # absence of a record, never a stale one (see capture.py).
    assert state.capture_progress_for(WAR_BASE_ONE) is None
    assert to_snapshot(state)["capture_progress"] == []
    assert state.structure_ownership_for(WAR_BASE_ONE) is None
    assert not any(isinstance(e, StructureCapturedEvent) for e in events)


def test_interrupted_capture_leaves_no_hidden_state_behind() -> None:
    """A run that captures-then-interrupts ends exactly like one that never captured.

    Guards against an interrupted capture leaving a hidden side effect (a
    partial record, an ownership override, a phantom reservation) that a
    never-started capture would not have: with the captor's in-flight state
    and order held equal, the two snapshots must agree on every
    capture-related key.
    """
    interrupted = _fixture(
        tick_count=TRACKS_MOVE_TICKS + 8,
        commands_by_tick={
            2: (
                SetRobotOrderCommand(
                    player=PLAYER_TWO,
                    sequence=1,
                    entity_id=ROBOT_CAPTOR,
                    order=Advance(distance_miles=2),
                ),
            )
        },
        initial_robots=(_robot(ROBOT_CAPTOR, PLAYER_TWO, *WARBASE_CAPTURE_CELL),),
    )

    state, _events = _replay_twice(interrupted)
    snapshot = to_snapshot(state)

    assert snapshot["capture_progress"] == []
    assert snapshot["structure_ownership"] == []


# --------------------------------------------------------------------------
# Rejected / failed movement
# --------------------------------------------------------------------------


def test_rejected_movement_replays_identically_with_no_state_divergence() -> None:
    """A direct move into a ditch is rejected the same way on every replay.

    A tracks chassis cannot enter ditch terrain, so the move is rejected by
    the shared movement executor: no transition starts, no reservation is
    taken, and the robot's authoritative cell is untouched -- exactly the
    "rejected movements replay without hidden side effects" criterion.
    """
    west_of_ditch = DITCH_CELL[0] - 1, DITCH_CELL[1]
    robot = _robot(ROBOT_DITCH, PLAYER_ONE, *west_of_ditch)
    commander = Commander(
        player_id=PLAYER_ONE,
        mode=CommanderMode.DOCKED,
        x=west_of_ditch[0],
        y=west_of_ditch[1],
        altitude=0,
        docked_robot_id=ROBOT_DITCH,
    )
    fixture = _fixture(
        tick_count=4,
        commands_by_tick={
            2: (DirectRobotMoveCommand(player=PLAYER_ONE, sequence=1, dx=1, dy=0),),
        },
        initial_robots=(robot,),
        commanders=(commander,),
    )

    state, events = _replay_twice(fixture)

    assert not any(isinstance(e, RobotMoveStartedEvent) for e in events)
    final = state.robot_for(ROBOT_DITCH)
    assert final is not None
    assert (final.x, final.y) == west_of_ditch
    assert final.movement is None
    assert reservations_from_state(state).holders == {}


# --------------------------------------------------------------------------
# Snapshot-wide guarantees
# --------------------------------------------------------------------------


def test_final_snapshot_of_an_m5_run_is_json_safe() -> None:
    fixture = _fixture(
        tick_count=TRACKS_MOVE_TICKS + 4,
        commands_by_tick=_contention_commands(),
        initial_robots=_contention_robots(),
    )

    state, _events = _replay_twice(fixture)
    snapshot = to_snapshot(state)

    assert json.loads(json.dumps(snapshot)) == snapshot
    assert json.loads(snapshot_to_json_string(state)) == snapshot
