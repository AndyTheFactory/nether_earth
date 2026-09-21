"""M9.4 -- the canonical scripted full PvP match (issue #116).

One deterministic, reactive script that drives both players from the
canonical tick-0 state (`scenario.create_initial_state` on the standard
world) to authoritative victory, traversing every step the milestone
requires: commander movement/interaction, construction and launch, direct
and autonomous robot movement, factory production, factory and war-base
capture, direct and autonomous combat, area destruction of the enemy war
base, and the victory rule.

The script is written as generators over a tiny :class:`Api` so it can be
driven either directly through ``engine.step`` (:func:`run_engine`) or
through the real ``MatchRuntime`` (:func:`drive_runtime`); both produce the
same accepted command stream because the engine is deterministic and the
script only reacts to authoritative state. Nothing in here implements a
gameplay rule -- every decision is "wait until the engine says X, then
submit command Y".
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass, field

from nether_earth import engine
from nether_earth.combat import FireCommand
from nether_earth.commander import CommanderMode
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.commands import Command
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.events import Event, order_events
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.movement import chassis_can_enter
from nether_earth.occupancy import unit_footprint_cells
from nether_earth.orders import (
    Advance,
    SearchCapture,
    SearchCaptureTarget,
    SetRobotOrderCommand,
    StopAndDefend,
)
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState

SCRIPT_SEED = 20260920
#: Flight altitude that clears the 15-high war-base roof the heli-pad sits on
#: (open-questions §18); the script only uses it to route, never to decide landing.
ROOF_CLEARANCE = 16
#: Hard stop so a regression that stalls the script fails instead of hanging.
MAX_TICKS = 40_000

Predicate = Callable[[GameState], bool]
Actor = Generator[Predicate, None, None]


@dataclass
class Api:
    """What the actor generators see: the latest authoritative state plus an outbox."""

    world: WorldMap
    state: GameState
    outbox: list[Command] = field(default_factory=list)
    sequences: dict[PlayerId, int] = field(default_factory=dict)
    #: Milestones the script reached (tick -> label), for localized failures.
    milestones: list[tuple[int, str]] = field(default_factory=list)

    def cmd(self, command_type: type[Command], player: PlayerId, **fields: object) -> None:
        self.sequences[player] = self.sequences.get(player, -1) + 1
        self.outbox.append(command_type(player=player, sequence=self.sequences[player], **fields))  # type: ignore[arg-type]

    def mark(self, label: str) -> None:
        self.milestones.append((self.state.tick, label))

    # -- reusable waits --------------------------------------------------------

    @staticmethod
    def ticks(n: int) -> Predicate:
        target: list[int] = []

        def _pred(state: GameState) -> bool:
            if not target:
                target.append(state.tick + n)
            return state.tick >= target[0]

        return _pred

    def move_commander_to(self, player: PlayerId, x: int, y: int) -> Iterator[Predicate]:
        """Issue one 4-directional move at a time until the commander stands on ``(x, y)``."""
        while True:
            commander = self.state.commander_for(player)
            assert commander is not None
            if (commander.x, commander.y) == (x, y):
                return
            if commander.horizontal_transition is None:
                dx = (x > commander.x) - (x < commander.x)
                dy = 0 if dx else (y > commander.y) - (y < commander.y)
                self.cmd(CommanderMoveCommand, player, dx=dx, dy=dy)
            yield self.ticks(1)

    def fly_commander_to(self, player: PlayerId, x: int, y: int, altitude: int) -> Iterator[Predicate]:
        """Hold rise until at least ``altitude``, fly to ``(x, y)`` still rising, then release."""
        if self.state.construction_session_for(player) is not None:
            # The construction screen is modal (CR002.13): a commander left on
            # its pad re-enters it, and can only take off via EXIT MENU.
            self.cmd(CancelConstructionCommand, player)
            yield lambda s: s.construction_session_for(player) is None
        commander = self.state.commander_for(player)
        assert commander is not None
        enclosing = next(
            (r for r in self.state.robots if (r.x, r.y) == (commander.x, commander.y) and commander.altitude < r.height),
            None,
        )
        if enclosing is not None and commander.mode is CommanderMode.FREE:
            # A robot standing on the grounded commander's cell encloses it;
            # step aside on the ground before rising.
            yield from self.move_commander_to(player, commander.x - 1, commander.y)
        self.cmd(CommanderSetVerticalIntentCommand, player, rising=True)
        yield lambda s: (c := s.commander_for(player)) is not None and c.altitude >= altitude
        yield from self.move_commander_to(player, x, y)
        self.cmd(CommanderSetVerticalIntentCommand, player, rising=False)
        yield self.ticks(1)

    def land_on_heli_pad(self, player: PlayerId, structure_id: str) -> Iterator[Predicate]:
        """Fly above the roof-top heli-pad, release rise, and let gravity settle the commander on it.

        The pad is on the war-base roof (open-questions §18); the engine
        enters construction once the commander rests at the roof height.
        """
        pad = self.heli_pad(structure_id)
        yield from self.fly_commander_to(player, pad[0], pad[1], ROOF_CLEARANCE)

    def land_on_robot(self, player: PlayerId, robot_id: EntityId) -> Iterator[Predicate]:
        """Fly above the robot, release rise, and let gravity dock the commander onto it."""
        robot = self.state.robot_for(robot_id)
        assert robot is not None
        yield from self.fly_commander_to(player, robot.x, robot.y, robot.height + 4)
        yield lambda s: (c := s.commander_for(player)) is not None and c.mode is CommanderMode.DOCKED

    def undock(self, player: PlayerId) -> Iterator[Predicate]:
        """Hold rise until undocked and clear of the robot; the caller keeps flying (rise stays held)."""
        commander = self.state.commander_for(player)
        assert commander is not None and commander.docked_robot_id is not None
        robot = self.state.robot_for(commander.docked_robot_id)
        assert robot is not None
        clearance = robot.height + 4
        self.cmd(CommanderSetVerticalIntentCommand, player, rising=True)
        yield lambda s: (c := s.commander_for(player)) is not None and c.mode is CommanderMode.FREE and c.altitude >= clearance

    def build_and_launch(
        self, player: PlayerId, modules: tuple[ModuleIdentity, ...]
    ) -> Iterator[Predicate]:
        """Assumes an open construction session; selects ``modules`` and launches."""
        for module in modules:
            self.cmd(SelectModuleCommand, player, module=module)
            yield self.ticks(1)
        before = {robot.entity_id for robot in self.state.robots_for(player)}
        self.cmd(LaunchRobotCommand, player)
        yield lambda s: len(s.robots_for(player)) > len(before)

    def direct_move(self, player: PlayerId, dx: int, dy: int, cells: int) -> Iterator[Predicate]:
        commander = self.state.commander_for(player)
        assert commander is not None and commander.docked_robot_id is not None
        robot_id = commander.docked_robot_id
        for _ in range(cells):
            robot = self.state.robot_for(robot_id)
            assert robot is not None
            goal = (robot.x + dx, robot.y + dy)
            self.cmd(DirectRobotMoveCommand, player, dx=dx, dy=dy)
            yield lambda s, g=goal: (r := s.robot_for(robot_id)) is not None and (r.x, r.y) == g

    def drive_to(self, player: PlayerId, x: int, y: int) -> Iterator[Predicate]:
        """Direct-control the docked robot along the row first, then the column, one cell per move."""
        commander = self.state.commander_for(player)
        assert commander is not None and commander.docked_robot_id is not None
        robot = self.state.robot_for(commander.docked_robot_id)
        assert robot is not None
        dy = (y > robot.y) - (y < robot.y)
        yield from self.direct_move(player, 0, dy, abs(y - robot.y))
        dx = (x > robot.x) - (x < robot.x)
        yield from self.direct_move(player, dx, 0, abs(x - robot.x))

    def drive_into_doorway(self, player: PlayerId, x: int, y: int) -> Iterator[Predicate]:
        """Direct-control the docked robot onto a war-base capture cell ``(x, y)``.

        The capture cell is the anchor of a 2×2 body standing in the base's
        south-facing doorway (CR002.3), whose walls block a sideways entry:
        drive to the cell just below it, then step up into the doorway.
        """
        yield from self.drive_to(player, x, y + 1)
        yield from self.direct_move(player, 0, -1, 1)

    def advance_to_column(self, player: PlayerId, robot_id: EntityId, column: int) -> Iterator[Predicate]:
        """Chain ``Advance`` orders (max 50 miles = 100 cells each) until the robot reaches ``column``."""
        while True:
            robot = self.state.robot_for(robot_id)
            assert robot is not None, f"{robot_id.value} was destroyed before reaching column {column}"
            remaining = column - robot.x
            if remaining <= 0:
                return
            # An Advance goal is the cell (goal column, robot's *current* row);
            # a detour around terrain can change the row mid-leg, and a goal
            # inside a structure or impassable terrain is legitimately
            # unreachable, so prefer the longest leg whose goal column is
            # enterable ground on every row, else on the current row.
            longest = min(50, (remaining + 1) // 2)
            miles = next(
                (
                    m
                    for rows in (range(1, self.world.height), (robot.y,))
                    for m in range(longest, 0, -1)
                    if self._column_is_open(robot, min(robot.x + m * 2, self.world.width - 2), rows)
                ),
                0,
            )
            assert miles > 0, f"no free Advance goal east of {(robot.x, robot.y)}"
            self.cmd(SetRobotOrderCommand, player, entity_id=robot_id, order=Advance(distance_miles=miles))
            target = min(robot.x + miles * 2, column)
            yield lambda s, t=target: (r := s.robot_for(robot_id)) is None or r.x >= t

    def _column_is_open(self, robot: Robot, column: int, rows: Iterable[int]) -> bool:
        """Whether a 2×2 body anchored at ``column`` fits on every anchor row in ``rows`` (CR002.3)."""
        occupancy = self.world.occupancy()
        return all(
            not occupancy.blocks_unit(column, y)
            and all(
                chassis_can_enter(robot.build.chassis, self.world.terrain.terrain_at(x, cell_y))
                for x, cell_y in unit_footprint_cells(column, y)
            )
            for y in rows
        )

    def war_base(self, structure_id: str) -> tuple[int, int]:
        points = self.world.interaction_points_for(EntityId(structure_id), kind=InteractionKind.WARBASE_CAPTURE)
        return min(points[0].footprint.cells)

    def heli_pad(self, structure_id: str) -> tuple[int, int]:
        """The anchor of the 2×2 pad: its min-x/max-y cell (open-questions §18, CR002.4)."""
        points = self.world.interaction_points_for(EntityId(structure_id), kind=InteractionKind.HELI_PAD)
        return min(points[0].footprint.cells, key=lambda cell: (cell[0], -cell[1]))


# -- the two players ----------------------------------------------------------

#: Four modules -> height 10 = the normal projectile altitude, so the striker
#: is hittable (a shorter robot is legitimately flown over; OQ §8).
STRIKER_MODULES = (
    ModuleIdentity.TRACKS,
    ModuleIdentity.NUCLEAR,
    ModuleIdentity.CANNON,
    ModuleIdentity.ELECTRONICS,
)
STRIKER_COST = 30
#: The striker's autonomous Advance stops this many columns west of the
#: warbase-4 anchor, clear of the scenery walls in front of the base.
STRIKER_STAGING_OFFSET = 24

P1_SCOUT = EntityId("robot-p1-1")
P1_STRIKER = EntityId("robot-p1-2")
P2_GUARD = EntityId("robot-p2-1")


def player_one(api: Api) -> Actor:
    """Player 1: scout captures a factory and the neutral warbase-2; a striker destroys warbase-4."""
    pad = api.heli_pad("warbase-1")
    yield from api.land_on_heli_pad(PLAYER_ONE, "warbase-1")
    yield lambda s: s.construction_session_for(PLAYER_ONE) is not None
    api.mark("p1 construction entered")

    yield from api.build_and_launch(PLAYER_ONE, (ModuleIdentity.BIPOD, ModuleIdentity.CANNON, ModuleIdentity.ELECTRONICS))
    api.mark("p1 scout launched")
    api.cmd(
        SetRobotOrderCommand,
        PLAYER_ONE,
        entity_id=P1_SCOUT,
        order=SearchCapture(target=SearchCaptureTarget.NEUTRAL_FACTORY),
    )
    yield lambda s: any(r.owner == PLAYER_ONE for r in s.structure_ownership if r.structure_id.value.startswith("factory"))
    api.mark("p1 captured a neutral factory")

    # Direct fire at the adjacent factory wall: a normal weapon never destroys a structure.
    scout = api.state.robot_for(P1_SCOUT)
    assert scout is not None
    api.cmd(FireCommand, PLAYER_ONE, entity_id=P1_SCOUT, weapon=ModuleIdentity.CANNON, target_x=scout.x - 1, target_y=scout.y)
    yield lambda s: (r := s.robot_for(P1_SCOUT)) is not None and r.active_projectile_id is not None
    yield lambda s: (r := s.robot_for(P1_SCOUT)) is not None and r.active_projectile_id is None
    api.mark("p1 scout fired at a structure")

    # Autonomous long-range movement: chained Advance orders to a staging column.
    capture_cell = api.war_base("warbase-2")
    staging_column = capture_cell[0] - 11
    yield from api.advance_to_column(PLAYER_ONE, P1_SCOUT, staging_column)
    yield lambda s: (r := s.robot_for(P1_SCOUT)) is not None and isinstance(r.order, StopAndDefend)
    api.mark("p1 scout staged near warbase-2")

    # Direct control: dock onto the scout and drive it onto the capture cell.
    yield from api.land_on_robot(PLAYER_ONE, P1_SCOUT)
    api.mark("p1 docked on scout")
    yield from api.drive_into_doorway(PLAYER_ONE, *capture_cell)
    api.mark("p1 direct-controlled scout onto warbase-2 capture cell")
    yield from api.undock(PLAYER_ONE)
    yield from api.land_on_heli_pad(PLAYER_ONE, "warbase-1")
    yield lambda s: s.construction_session_for(PLAYER_ONE) is not None
    yield lambda s: (o := s.structure_ownership_for(EntityId("warbase-2"))) is not None and o.owner == PLAYER_ONE
    api.mark("p1 captured warbase-2")

    # Wait for enough general resources (daily war-base production) for the
    # striker. A session's buffer is snapshotted at entry (M4 rule), so the
    # income that arrived while the menu was open is only spendable after
    # leaving and re-entering -- EXIT MENU lifts the commander off the pad
    # (CR002.12/13) and, left alone, gravity lands it back on the pad, which
    # re-opens the screen with a fresh buffer.
    yield lambda s: (p := s.resource_pool_for(PLAYER_ONE)) is not None and p.general >= STRIKER_COST
    api.cmd(CancelConstructionCommand, PLAYER_ONE)
    yield lambda s: (
        (session := s.construction_session_for(PLAYER_ONE)) is not None and session.buffer.general >= STRIKER_COST
    )
    api.mark("p1 affords striker")
    yield from api.build_and_launch(PLAYER_ONE, STRIKER_MODULES)
    api.mark("p1 striker launched")

    # Direct-drive the striker down to the free bottom row, then let it advance east on its own.
    yield from api.land_on_robot(PLAYER_ONE, P1_STRIKER)
    yield from api.drive_to(PLAYER_ONE, pad[0], api.world.height - 1)
    yield from api.undock(PLAYER_ONE)
    yield from api.land_on_heli_pad(PLAYER_ONE, "warbase-1")
    enemy = api.war_base("warbase-4")
    yield from api.advance_to_column(PLAYER_ONE, P1_STRIKER, enemy[0] - STRIKER_STAGING_OFFSET)
    # The blast reaches a war base only from near its anchor (dy measured from
    # carrier.y + 5 must stay < 7; open-questions §20), so direct-drive the
    # striker up to the row just below the anchor, level with the guard.
    # The scenery walls west of warbase-4 (CR002.1 blockers) leave one open
    # gap on the row above the anchor, so drive through it and come down
    # beside the base instead of crossing the capture anchor itself.
    yield from api.land_on_robot(PLAYER_ONE, P1_STRIKER)
    gap_row = enemy[1] - 1
    # A 2×2 body anchored on the gap row reaches four columns west of the
    # anchor before the base wall (CR002.3).
    yield from api.drive_to(PLAYER_ONE, enemy[0] - 4, gap_row)
    yield from api.drive_to(PLAYER_ONE, enemy[0], enemy[1] + 1)
    # Let the guard's aligned shot land before detonating. Coming down from
    # the gap row, the striker is already level with the guard (and in its
    # range) a few cells west of the anchor, so the shot may have landed
    # before the striker got here.
    yield lambda s: s.robot_for(P2_GUARD) is None or any(
        label == "p2 guard fired directly" for _, label in api.milestones
    )
    yield lambda s: (g := s.robot_for(P2_GUARD)) is None or g.active_projectile_id is None
    api.mark("p1 striker in position")
    # The completed Advance became Stop & Defend, which never detonates
    # (OQ §19): the striker must still be alive, and the nuclear module is
    # fired directly.
    assert api.state.robot_for(P1_STRIKER) is not None, "striker detonated autonomously (OQ §19)"
    api.cmd(FireCommand, PLAYER_ONE, entity_id=P1_STRIKER, weapon=ModuleIdentity.NUCLEAR, target_x=enemy[0], target_y=enemy[1])
    yield lambda s: EntityId("warbase-4") in s.structure_destruction
    api.mark("p1 destroyed warbase-4")


def player_two(api: Api) -> Actor:
    """Player 2: builds a guard, parks it just below and east of the base, and fires one aligned shot."""
    base = api.war_base("warbase-4")
    yield from api.land_on_heli_pad(PLAYER_TWO, "warbase-4")
    yield lambda s: s.construction_session_for(PLAYER_TWO) is not None
    api.mark("p2 construction entered")
    yield from api.build_and_launch(PLAYER_TWO, (ModuleIdentity.BIPOD, ModuleIdentity.CANNON))
    api.mark("p2 guard launched")

    # Direct control: dock and drive the guard one row below the base anchor,
    # three cells east of the base column -- inside the striker's blast window.
    yield from api.land_on_robot(PLAYER_TWO, P2_GUARD)
    api.mark("p2 docked on guard")
    yield from api.drive_to(PLAYER_TWO, base[0] + 3, base[1] + 1)
    api.mark("p2 guard in position")
    yield from api.undock(PLAYER_TWO)
    yield from api.fly_commander_to(PLAYER_TWO, base[0] + 3, base[1] + 3, 12)
    yield lambda s: (c := s.commander_for(PLAYER_TWO)) is not None and c.altitude == 0
    api.mark("p2 commander parked")

    # One direct cannon shot along the row once the striker is in range.
    yield lambda s: (
        (r := s.robot_for(P1_STRIKER)) is not None
        and (g := s.robot_for(P2_GUARD)) is not None
        and r.y == g.y
        and 0 < g.x - r.x <= DEFAULT_RULES.cannon_range_cells
    )
    guard = api.state.robot_for(P2_GUARD)
    striker = api.state.robot_for(P1_STRIKER)
    assert guard is not None and striker is not None
    api.cmd(FireCommand, PLAYER_TWO, entity_id=P2_GUARD, weapon=ModuleIdentity.CANNON, target_x=striker.x, target_y=striker.y)
    api.mark("p2 guard fired directly")


# -- drivers ------------------------------------------------------------------


@dataclass
class Scheduler:
    """Advances every actor whose current wait predicate holds, filling ``api.outbox``."""

    api: Api
    actors: list[tuple[str, Actor, Predicate | None]]
    finished: bool = False

    @classmethod
    def standard(cls, api: Api) -> Scheduler:
        return cls(api, [("p1", player_one(api), None), ("p2", player_two(api), None)])

    def collect_commands(self) -> tuple[Command, ...]:
        next_actors: list[tuple[str, Actor, Predicate | None]] = []
        for name, actor, waiting in self.actors:
            pred = waiting
            while pred is None or pred(self.api.state):
                try:
                    pred = next(actor)
                except StopIteration:
                    pred = None
                    break
            if pred is not None:
                next_actors.append((name, actor, pred))
        self.actors = next_actors
        commands = tuple(self.api.outbox)
        self.api.outbox.clear()
        return commands


def victory_reached(events: tuple[Event, ...]) -> bool:
    from nether_earth.victory import VictoryEvent

    return any(isinstance(event, VictoryEvent) for event in events)


@dataclass(frozen=True)
class EngineRun:
    api: Api
    commands_by_tick: dict[int, tuple[Command, ...]]
    events_by_tick: dict[int, tuple[Event, ...]]

    @property
    def final_state(self) -> GameState:
        return self.api.state

    def events(self, event_type: type) -> list[tuple[int, Event]]:
        return [
            (tick, event)
            for tick, events in self.events_by_tick.items()
            for event in events
            if isinstance(event, event_type)
        ]


def run_engine(world: WorldMap, initial: GameState) -> EngineRun:
    """Drive the script straight through ``engine.step`` until the engine announces victory."""
    api = Api(world=world, state=initial)
    scheduler = Scheduler.standard(api)
    commands_by_tick: dict[int, tuple[Command, ...]] = {}
    events_by_tick: dict[int, tuple[Event, ...]] = {}
    while api.state.tick < MAX_TICKS:
        commands = scheduler.collect_commands()
        tick = api.state.tick + 1
        if commands:
            commands_by_tick[tick] = commands
        api.state, events = engine.step(api.state, commands, world=world)
        ordered = tuple(order_events(events))
        if ordered:
            events_by_tick[tick] = ordered
        if victory_reached(events):
            return EngineRun(api, commands_by_tick, events_by_tick)
    raise AssertionError(f"script did not reach victory within {MAX_TICKS} ticks; milestones: {api.milestones}")


# -- real runtime driver ------------------------------------------------------


@dataclass
class RuntimeRun:
    """Everything the backend-level drive produced, for assertions."""

    match_id: str
    match: object
    final_state: GameState
    milestones: list[tuple[int, str]]
    commands_by_tick: dict[int, tuple[Command, ...]]
    snapshot_count: int
    #: Every non-snapshot frame broadcast to the two fake sockets, in order.
    frames: list[dict[str, object]]
    #: The last broadcast snapshot frame (JSON-decoded).
    last_snapshot: dict[str, object] | None
    replay_dir: object


async def drive_runtime(world: WorldMap, replay_dir: object) -> RuntimeRun:
    """Drive the script through the real backend stack, one deterministic tick at a time.

    Composition mirrors ``app.main.create_app`` (``MatchManager`` +
    ``MatchRuntime`` + snapshot broadcaster + victory finalizer + replay
    writer) except that the tick loop is stepped explicitly with
    ``MatchRuntime._advance_one_tick`` instead of being scheduled at 20 Hz,
    so the accepted command stream is a pure function of the script.
    """
    import json
    from pathlib import Path

    from app.match.manager import MatchManager
    from app.match.models import MatchRuntimeState
    from app.match.runtime import MatchRuntime
    from app.protocol import serialize_server_message
    from app.replay import ReplayWriter, make_replay_tick_recorder
    from app.transport.connections import ConnectionRegistry
    from app.transport.snapshots import build_snapshot_message
    from app.transport.victory import make_victory_finalizer

    assert isinstance(replay_dir, Path)
    writer = ReplayWriter(base_dir=replay_dir)
    manager = MatchManager(
        world=world, on_match_start=writer.start_match, on_match_finish=writer.finish_match
    )
    created = manager.create_match("alice", seed=SCRIPT_SEED)
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token, True)
    manager.set_ready(joined.session_token, True)
    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.ACTIVE and match.game_state is not None

    frames: list[dict[str, object]] = []
    last_snapshot: list[dict[str, object]] = []
    snapshot_count = [0]

    class _Socket:
        async def send_text(self, text: str) -> None:
            frame = json.loads(text)
            if frame["type"] == "snapshot":
                snapshot_count[0] += 1
                last_snapshot[:] = [frame]
            else:
                frames.append(frame)

    registry = ConnectionRegistry()
    registry.register(match.match_id, "p1", _Socket())  # type: ignore[arg-type]
    registry.register(match.match_id, "p2", _Socket())  # type: ignore[arg-type]

    async def _broadcast_snapshot(state: GameState, events: tuple[Event, ...]) -> None:
        del events
        # The exact browser-facing contract: build (validates the strict
        # SnapshotState model) and serialize, then deliver to both players.
        message = build_snapshot_message(match.match_id, state)
        text = serialize_server_message(message)
        for socket in registry.connections_for(match.match_id):
            await socket.send_text(text)

    finalize = make_victory_finalizer(manager, registry, match)

    async def _on_tick(state: GameState, events: tuple[Event, ...]) -> None:
        await _broadcast_snapshot(state, events)
        await finalize(state, events)

    runtime = MatchRuntime(
        match,
        world=world,
        on_tick=_on_tick,
        on_tick_commands=make_replay_tick_recorder(writer, match.match_id),
    )
    api = Api(world=world, state=match.game_state)
    scheduler = Scheduler.standard(api)
    commands_by_tick: dict[int, tuple[Command, ...]] = {}
    while match.state is MatchRuntimeState.ACTIVE and match.game_state.tick < MAX_TICKS:
        commands = scheduler.collect_commands()
        for command in commands:
            assert await runtime.submit_command(command)
        if commands:
            commands_by_tick[match.game_state.tick + 1] = commands
        await runtime._advance_one_tick()
        assert match.game_state is not None
        api.state = match.game_state
    return RuntimeRun(
        match_id=match.match_id,
        match=match,
        final_state=api.state,
        milestones=api.milestones,
        commands_by_tick=commands_by_tick,
        snapshot_count=snapshot_count[0],
        frames=frames,
        last_snapshot=last_snapshot[0] if last_snapshot else None,
        replay_dir=replay_dir,
    )
