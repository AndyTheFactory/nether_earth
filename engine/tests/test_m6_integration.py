"""M6 milestone integration scenario (issue #81, M6.11).

The final Milestone-6 acceptance gate: one coherent, deterministic
battlefield exercising the composition of every M6 combat system rather
than each in isolation -- weapon-fit validation, the shared combat channel,
projectile altitude/range/collision (static and robot, clear-path and
obstructed), damage/destruction, direct-control fire, autonomous Search &
Destroy engagement (proven to share the exact same firing path as direct
control), commander untargetability, structural immunity of factories/war
bases to normal weapons, nuclear detonation (blast-shape boundary, deterministic
carrier -> robots -> structures ordering, structure cleanup), and nuclear
destruction of a player's last war base triggering same-step victory --
per `_specs/milestones/06-combat-damage-victory.md`'s own "Milestone
integration scenario" requirement.

Everything here runs through the real ``engine.new_game``/``engine.step``
pipeline (via ``replay.run_fixture`` and a local recording variant of the
same loop, mirroring `test_m5_integration.py`'s own ``_drive`` helper) --
no alternate combat semantics are invented in the test, and no assertion
calls `combat.py`/`destruction.py`'s functions directly (those already have
their own unit suites -- see `test_engine_combat_integration.py`). See
``tests/fixtures/world_map_m6_integration.yaml`` for the battlefield layout
and its "lanes" rationale.

Fidelity disclosures (this scenario deliberately exercises, and does not
overclaim, every M6 policy choice still flagged non-canonical by Task 3
(#72) or Task 5 (#74) -- see the milestone's own "Fidelity gate" and
`_specs/open-questions.md` §8/§9):

- Nuclear blast eligibility follows the Spectrum code's per-kind shapes
  (``destruction.py``'s ``execute_nuclear_detonation``, CR001.2 / issue
  #149, `_specs/open-questions.md` §20): a trimmed 9x9 robot window and a
  per-kind building range test destroying at most one building.
- Fire direction is resolved by a dominant-axis-with-x-tiebreak rule
  (``combat.resolve_fire_direction``) because this engine's ``Robot`` has
  no facing field to copy, unlike the original disassembly -- a documented
  simplification, not a fidelity claim.
- ``ground_height_at`` reinterprets the disassembly's ``ROBOT_STRUCT_ALTITUDE``
  as the static ``Component`` height under a robot's current cell (this
  engine has no per-robot elevation field) -- see ``combat.py``'s module
  docstring for the full reasoning.
- Destruction in this engine is immediate: Task 5's research
  (`_specs/open-questions.md` §9) found the disassembly stages destruction
  behind a negative-strength "blink" grace state before removal
  (``Lb0fa_robot_update``/``Lb116_robot_destroyed``), which this milestone
  deliberately does not implement (`destroy_robot`'s own docstring) -- a
  documented simplification, not a fidelity claim.
- This scenario's range-exhaustion assertions (Lanes C/D3, 10/14 cells)
  rest on the Spectrum code-derived ranges and the 2-cells-per-advance
  projectile speed adopted by CR001 (#150, `_specs/open-questions.md` §8
  resolution).
- This scenario hardcodes ``strength=100`` and asserts exact damage values
  off it; `_specs/open-questions.md` §9 records the starting-strength
  figure as resolved only "subject to the scale-reconciliation caveat" that
  ``robot_height``/``ground_height`` be confirmed on the same disassembly-
  native raw scale once wired to real data -- a noted caveat, not a
  silently-assumed fact.
- Projectile collision checks each cell entered, in travel order (single-
  cell point collision per cell), not `_specs/open-questions.md` §8's
  evidenced ordered 3x3 first-hit-wins scan
  (``Lb7a7_potentially_hit_a_robot``'s preceding neighborhood check) -- see
  ``combat.py``'s module docstring for the full disclosure and rationale;
  this is a documented simplification, not a fidelity claim.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from nether_earth import engine
from nether_earth.capture import CapturableStructureKind
from nether_earth.combat import (
    FireCommand,
    ProjectileFiredEvent,
    ProjectileTerminatedEvent,
    ProjectileTerminationReason,
    RobotDamagedEvent,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commands import Command
from nether_earth.destruction import RobotDestroyedEvent, StructureDestroyedEvent
from nether_earth.engine import CommandAccepted
from nether_earth.events import Event, order_events
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap, load_world_map
from nether_earth.movement import folded_robot_occupancy
from nether_earth.orders import SearchDestroy, SearchDestroyTarget
from nether_earth.replay import ReplayFixture, run_fixture
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import snapshot_to_json_string
from nether_earth.state import GameState
from nether_earth.victory import VictoryEvent

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "world_map_m6_integration.yaml"
MAP_ID = "fixture-m6-integration"
MAP_VERSION = 1
MAP_WIDTH = 200
MAP_HEIGHT = 460

SEED = 60680919

# --------------------------------------------------------------------------
# Tick-scheduling constants, derived from `rules.py` -- never bare literals.
# --------------------------------------------------------------------------

ADVANCE = DEFAULT_RULES.projectile_advance_ticks  # 4
CELLS_PER_ADVANCE = DEFAULT_RULES.projectile_cells_per_advance  # 2
CANNON_RANGE = DEFAULT_RULES.cannon_range_cells  # 10
MISSILE_RANGE = DEFAULT_RULES.missile_range_cells  # 14
PHASER_RANGE = DEFAULT_RULES.phaser_range_cells  # 10
ELECTRONICS_BONUS = DEFAULT_RULES.electronics_range_bonus_cells  # 2
#: Half-width of the carrier's own row of the nuclear robot window (9 -> 4).
NUCLEAR_ROW_REACH = DEFAULT_RULES.nuclear_robot_window_row_widths[
    len(DEFAULT_RULES.nuclear_robot_window_row_widths) // 2
] // 2  # 4
ALTITUDE = DEFAULT_RULES.normal_projectile_altitude  # 10
CANNON_MULT = DEFAULT_RULES.cannon_damage_multiplier  # 2

# A small, on-map-relative offset used only to steer
# `combat.resolve_fire_direction`'s dominant-axis rule east: the exact
# magnitude is irrelevant to direction resolution (only the sign of
# `target_x - robot.x` matters), and no projectile in this fixture ever
# travels far enough for the offset's actual value to matter. Kept small
# and relative to each shooter's own x (rather than a fixed far-away
# constant) so every lane's target stays within the map's actual width
# (`MAP_WIDTH = 200`) -- `apply_fire` performs no target-bounds validation
# today, so an out-of-bounds target would silently work by accident rather
# than by design.
FAR_EAST = 50


def east_of(x: int) -> int:
    return x + FAR_EAST


# `apply_fire`'s projectile-lifecycle documentation (see `combat.py`): a
# projectile makes its first move on the fire tick itself (CR002.2 #169).
# Every shot below is a direct (combat-mode) FireCommand, which is then held
# for the rest of its fire cycle (§8), so with `fire_tick < ADVANCE` its
# next move is at cadence tick `2 * ADVANCE`, and cadence `k >= 2` occurs at
# tick `ADVANCE * k`, giving `travelled_cells == k * CELLS_PER_ADVANCE` at
# that tick (capped at the range). Range exhaustion is detected one cadence
# tick *after* `travelled_cells` first reaches `max_range_cells` (see
# `combat._range_exhausted`'s docstring: the final in-range cell is still
# collision-checked on arrival, so expiry is deferred one more cadence).
# Every fire command below is issued at FIRE_TICK, chosen `< ADVANCE` so
# this exact arithmetic applies uniformly.
FIRE_TICK = 1
assert FIRE_TICK < ADVANCE


def _moves_to_cover(distance_cells: int) -> int:
    return -(-distance_cells // CELLS_PER_ADVANCE)


def _move_tick(move: int) -> int:
    """Return the tick of a direct ``FIRE_TICK`` shot's 1-based ``move``.

    The first move is on the fire tick; the fire cycle's closing cadence
    tick is skipped for a direct shot, so move ``m >= 2`` is at ``ADVANCE * m``.
    """
    return FIRE_TICK if move == 1 else ADVANCE * move


def range_exhaustion_tick(max_range_cells: int) -> int:
    """Return the tick a direct shot fired at ``FIRE_TICK`` exhausts ``max_range_cells``."""
    return _move_tick(_moves_to_cover(max_range_cells)) + ADVANCE


def static_collision_tick(distance_cells: int) -> int:
    """Return the tick a projectile fired at ``FIRE_TICK`` reaches a blocker ``distance_cells`` away.

    Unlike range exhaustion, a static/robot collision is detected the
    moment the projectile lands where it touches the obstacle -- no extra
    deferred cadence tick (see `combat._projectile_terminal_reason`'s
    docstring). The projectile is a 2×2 body (CR002.3, `combat.py`'s
    "Collision footprint"), so it touches an obstacle one column before its
    anchor would reach it: the first landing at ``distance_cells - 1`` or
    further.
    """
    return _move_tick(_moves_to_cover(distance_cells - 1))


TOTAL_TICKS = 136  # ample margin past every lane (Lane D3 ends at tick 32)
SNAPSHOT_TICK = 12  # inside Lane C's clear flight (< its tick-24 termination)
LANE_B_REFIRE_TICK = 20  # after Lane B's first shot hits at tick 8 (see below)

# --------------------------------------------------------------------------
# Entity ids and lane rows
# --------------------------------------------------------------------------

A_Y = 0
B_Y = 40
C_Y = 80
D2_Y = 120
D3_Y = 160
E_Y = 200
F_Y = 240
G_Y = 280
H_Y = 320
I_Y = 360
J_Y = 400

ROBOT_A = EntityId("robot-a-cannon-only")

ROBOT_B_SHOOTER = EntityId("robot-b-shooter")
ROBOT_B_TARGET = EntityId("robot-b-target")

ROBOT_C_SHOOTER = EntityId("robot-c-shooter")

ROBOT_D2_SHOOTER = EntityId("robot-d2-shooter")
ROBOT_D3_SHOOTER = EntityId("robot-d3-shooter")

ROBOT_E = EntityId("robot-e-direct")

ROBOT_F_HUNTER = EntityId("robot-f-hunter")
ROBOT_F_PREY = EntityId("robot-f-prey")

ROBOT_G_HUNTER = EntityId("robot-g-hunter")
ROBOT_G_PREY = EntityId("robot-g-prey")

ROBOT_H_SHOOTER = EntityId("robot-h-shooter")
ROBOT_H2_SHOOTER = EntityId("robot-h2-shooter")

ROBOT_I_CARRIER = EntityId("robot-i-carrier")
ROBOT_I_IN = EntityId("robot-i-in")
ROBOT_I_OUT = EntityId("robot-i-out")

ROBOT_J_CARRIER = EntityId("robot-j-carrier")

FACTORY_H = EntityId("factory-h")
WARBASE_H = EntityId("warbase-h")
FACTORY_I_IN = EntityId("factory-i-in")
FACTORY_I_OUT = EntityId("factory-i-out")
WARBASE_J_FINAL = EntityId("warbase-j-final")
WARBASE_HOME = EntityId("warbase-p1-home")

# Lane D2/D3 blocker x (5 cells east of their shooters, see fixture).
D2_SHOOTER_X = 10
D2_BLOCKER_X = 15
D3_SHOOTER_X = 10
D3_BLOCKER_X = 15

# Lane I geometry: exactly at the blast-shape boundary in each direction
# (`_specs/open-questions.md` §20). The window tests robot anchors (CR002.3).
# The two robots' 2×2 bodies must not overlap, so the excluded one stands two
# rows above the carrier, where the window row is just as wide (9).
I_CARRIER_X = 100
I_ROBOT_IN_X = I_CARRIER_X + NUCLEAR_ROW_REACH  # 104, included
I_ROBOT_OUT_X = I_CARRIER_X + NUCLEAR_ROW_REACH + 1  # 105, excluded
I_ROBOT_OUT_Y = I_Y - 2
assert DEFAULT_RULES.nuclear_robot_window_row_widths[
    len(DEFAULT_RULES.nuclear_robot_window_row_widths) // 2 - 2
] // 2 == NUCLEAR_ROW_REACH
# Factory dy = |I_Y + 1 - I_Y| = 1; dx must stay < 5 (sum then 5 < 7).
I_FACTORY_IN_X = I_CARRIER_X - 4  # 96, included
I_FACTORY_OUT_X = I_CARRIER_X - 5  # 95, excluded (dx not < 5)

J_CARRIER_X = 100
# War-base dy = |J_Y + 1 + 4 - J_Y| = 5, dx = 4: sum 9 < 10, in range.
J_WARBASE_X = 104


def _robot(
    entity_id: EntityId,
    owner: PlayerId,
    x: int,
    y: int,
    *,
    weapons: tuple[ModuleIdentity, ...],
    electronics: ModuleIdentity | None = None,
    order: object | None = None,
    strength: int = 100,
) -> Robot:
    build = RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=weapons, electronics=electronics)
    stack, height = derive_stack_and_height(build, DEFAULT_RULES)
    return Robot(
        entity_id=entity_id,
        owner=owner,
        x=x,
        y=y,
        build=build,
        stack=stack,
        height=height,
        order=order,  # type: ignore[arg-type]
        strength=strength,
    )


def _hittable(entity_id: EntityId, owner: PlayerId, x: int, y: int, **kwargs: object) -> Robot:
    """A TRACKS(4) + cannon+missile+phaser(2 each) robot: height 10, exactly
    at `ALTITUDE`, so it both blocks/is-hit-by a normal projectile and can
    itself fire any of the three normal weapons (used for Lanes B/F/G/I's
    targets and combatants)."""
    return _robot(
        entity_id,
        owner,
        x,
        y,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER),
        **kwargs,  # type: ignore[arg-type]
    )


def _initial_robots() -> tuple[Robot, ...]:
    return (
        # Lane A: only a cannon fitted.
        _robot(ROBOT_A, PLAYER_ONE, 10, A_Y, weapons=(ModuleIdentity.CANNON,)),
        # Lane B: channel occupancy.
        _robot(ROBOT_B_SHOOTER, PLAYER_ONE, 10, B_Y, weapons=(ModuleIdentity.CANNON,)),
        _hittable(ROBOT_B_TARGET, PLAYER_TWO, 14, B_Y),
        # Lane C: altitude + exact range-exhaustion tick (phaser).
        _robot(ROBOT_C_SHOOTER, PLAYER_ONE, 10, C_Y, weapons=(ModuleIdentity.PHASER,)),
        # Lane D2: obstructed path (cannon).
        _robot(ROBOT_D2_SHOOTER, PLAYER_ONE, D2_SHOOTER_X, D2_Y, weapons=(ModuleIdentity.CANNON,)),
        # Lane D3: pass-over + missile coverage.
        _robot(ROBOT_D3_SHOOTER, PLAYER_ONE, D3_SHOOTER_X, D3_Y, weapons=(ModuleIdentity.MISSILE,)),
        # Lane E: direct fire, electronics-equipped (range bonus).
        _robot(
            ROBOT_E,
            PLAYER_ONE,
            10,
            E_Y,
            weapons=(ModuleIdentity.CANNON,),
            electronics=ModuleIdentity.ELECTRONICS,
        ),
        # Lane F: autonomous Search & Destroy, no FireCommand ever issued.
        _hittable(
            ROBOT_F_HUNTER,
            PLAYER_ONE,
            10,
            F_Y,
            order=SearchDestroy(target=SearchDestroyTarget.ROBOT),
        ),
        _hittable(ROBOT_F_PREY, PLAYER_TWO, 15, F_Y),
        # Lane G: autonomous engagement with an untargetable commander
        # parked directly between hunter and prey.
        _hittable(
            ROBOT_G_HUNTER,
            PLAYER_ONE,
            10,
            G_Y,
            order=SearchDestroy(target=SearchDestroyTarget.ROBOT),
        ),
        _hittable(ROBOT_G_PREY, PLAYER_TWO, 16, G_Y),
        # Lane H: structural immunity.
        _robot(ROBOT_H_SHOOTER, PLAYER_ONE, 10, H_Y, weapons=(ModuleIdentity.CANNON,)),
        _robot(ROBOT_H2_SHOOTER, PLAYER_ONE, 60, H_Y, weapons=(ModuleIdentity.CANNON,)),
        # Lane I: nuclear blast-shape boundary.
        _robot(ROBOT_I_CARRIER, PLAYER_ONE, I_CARRIER_X, I_Y, weapons=(ModuleIdentity.NUCLEAR,)),
        _hittable(ROBOT_I_IN, PLAYER_TWO, I_ROBOT_IN_X, I_Y),
        _hittable(ROBOT_I_OUT, PLAYER_TWO, I_ROBOT_OUT_X, I_ROBOT_OUT_Y),
        # Lane J: victory-triggering nuclear detonation.
        _robot(ROBOT_J_CARRIER, PLAYER_ONE, J_CARRIER_X, J_Y, weapons=(ModuleIdentity.NUCLEAR,)),
    )


def _commanders() -> tuple[Commander, ...]:
    return (
        # Lane E: docked from tick 0.
        Commander(
            player_id=PLAYER_ONE,
            mode=CommanderMode.DOCKED,
            x=10,
            y=E_Y,
            altitude=0,
            docked_robot_id=ROBOT_E,
        ),
        # Lane G: free, parked directly on the hunter->prey flight path
        # (hunter at x=10, prey at x=16), never moved.
        Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=13, y=G_Y, altitude=0),
    )


def _commands_by_tick() -> dict[int, tuple[Command, ...]]:
    commands: dict[int, list[Command]] = {}

    def add(tick: int, command: Command) -> None:
        commands.setdefault(tick, []).append(command)

    seq = 0

    def fire(entity_id: EntityId, weapon: ModuleIdentity, target_x: int, target_y: int) -> None:
        nonlocal seq
        add(
            FIRE_TICK,
            FireCommand(
                player=PLAYER_ONE,
                sequence=seq,
                entity_id=entity_id,
                weapon=weapon,
                target_x=target_x,
                target_y=target_y,
            ),
        )
        seq += 1

    # Lane A: a weapon NOT fitted (rejected), then the one that IS.
    fire(ROBOT_A, ModuleIdentity.MISSILE, east_of(10), A_Y)
    fire(ROBOT_A, ModuleIdentity.CANNON, east_of(10), A_Y)

    # Lane B: fire, then immediately re-fire the same robot in the SAME
    # tick (processed in sequence order within one `engine.step` call) --
    # the second must be rejected while the channel is occupied.
    fire(ROBOT_B_SHOOTER, ModuleIdentity.CANNON, east_of(10), B_Y)
    fire(ROBOT_B_SHOOTER, ModuleIdentity.CANNON, east_of(10), B_Y)

    # Lane C: phaser, clear runway.
    fire(ROBOT_C_SHOOTER, ModuleIdentity.PHASER, east_of(10), C_Y)

    # Lane D2: cannon into the height-15 blocker.
    fire(ROBOT_D2_SHOOTER, ModuleIdentity.CANNON, east_of(D2_SHOOTER_X), D2_Y)

    # Lane D3: missile over the height-9 blocker.
    fire(ROBOT_D3_SHOOTER, ModuleIdentity.MISSILE, east_of(D3_SHOOTER_X), D3_Y)

    # Lane E: direct-control cannon fire from the docked robot.
    fire(ROBOT_E, ModuleIdentity.CANNON, east_of(10), E_Y)

    # Lane H: normal-weapon shots aimed straight at a factory, then a war
    # base -- both pass over (height 3 < ALTITUDE) untouched.
    fire(ROBOT_H_SHOOTER, ModuleIdentity.CANNON, 15, H_Y)  # FACTORY_H's cell
    fire(ROBOT_H2_SHOOTER, ModuleIdentity.CANNON, 65, H_Y)  # WARBASE_H's cell

    # Lane I: nuclear detonation testing the blast-shape boundary.
    fire(ROBOT_I_CARRIER, ModuleIdentity.NUCLEAR, I_CARRIER_X, I_Y)

    # Lane J: nuclear detonation destroying p2's last war base.
    fire(ROBOT_J_CARRIER, ModuleIdentity.NUCLEAR, J_CARRIER_X, J_Y)

    # Lane B: re-fire after the first shot's tick-8 hit freed the channel.
    add(
        LANE_B_REFIRE_TICK,
        FireCommand(
            player=PLAYER_ONE,
            sequence=0,
            entity_id=ROBOT_B_SHOOTER,
            weapon=ModuleIdentity.CANNON,
            target_x=east_of(10),
            target_y=B_Y,
        ),
    )

    return {tick: tuple(cmds) for tick, cmds in commands.items()}


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="m6-integration-scenario",
        map_id=MAP_ID,
        map_version=MAP_VERSION,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id=MAP_ID, version=MAP_VERSION, width=MAP_WIDTH, height=MAP_HEIGHT)
    return scenario, map_data


def _fixture() -> ReplayFixture:
    scenario, map_data = _scenario_and_map()
    world = load_world_map(FIXTURE_PATH)
    return ReplayFixture(
        scenario=scenario,
        map_data=map_data,
        seed=SEED,
        tick_count=TOTAL_TICKS,
        commands_by_tick=_commands_by_tick(),
        world=world,
        commanders=_commanders(),
        initial_robots=_initial_robots(),
    )


@dataclass(frozen=True, slots=True)
class Drive:
    states: tuple[GameState, ...]  # states[t] is the state after t ticks
    events: tuple[Event, ...]
    world: object


def _drive(fixture: ReplayFixture) -> Drive:
    """Run ``fixture`` tick by tick, recording the state after every tick.

    Mirrors `test_m5_integration.py`'s own ``_drive`` helper exactly: this
    scenario's assertions need to observe intermediate states (e.g. "still
    in flight at tick 40", "a commander never moved across every tick"),
    which ``replay.run_fixture`` alone does not expose.
    """
    state = engine.new_game(
        fixture.map_data, fixture.scenario, seed=fixture.seed, commanders=fixture.commanders
    )
    if fixture.initial_robots:
        state = state.with_robots(fixture.initial_robots)

    states = [state]
    all_events: list[Event] = []
    for tick in range(1, fixture.tick_count + 1):
        cmds = fixture.commands_by_tick.get(tick, ())
        state, tick_events = engine.step(state, cmds, world=fixture.world, robots=fixture.robots)
        states.append(state)
        all_events.extend(order_events(tick_events))

    return Drive(states=tuple(states), events=tuple(all_events), world=fixture.world)


def _events_of(events: tuple[Event, ...], cls: type) -> list:
    return [e for e in events if isinstance(e, cls)]


def _fired_by(events: list, source_robot_id: EntityId) -> list:
    return [e for e in events if e.source_robot_id == source_robot_id]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# The scenario, driven once and shared by every assertion in this module.
# --------------------------------------------------------------------------


def test_full_milestone_scenario_composes_all_m6_rules() -> None:
    drive = _drive(_fixture())
    states = drive.states
    events = drive.events
    final = states[-1]

    fired = _events_of(events, ProjectileFiredEvent)
    terminated = _events_of(events, ProjectileTerminatedEvent)
    damaged = _events_of(events, RobotDamagedEvent)
    robots_destroyed = _events_of(events, RobotDestroyedEvent)
    structures_destroyed = _events_of(events, StructureDestroyedEvent)
    victories = _events_of(events, VictoryEvent)
    accepted_commands = _events_of(events, CommandAccepted)

    # ---------------------------------------------------------------
    # Lane A: weapon-fit validation.
    # ---------------------------------------------------------------
    a_fire_commands = [
        e.command  # type: ignore[attr-defined]
        for e in accepted_commands
        if isinstance(e.command, FireCommand) and e.command.entity_id == ROBOT_A  # type: ignore[attr-defined]
    ]
    # Both the illegal (missile) and the legal (cannon) FireCommand are
    # structurally accepted -- a gameplay-level rejection produces no
    # additional event, matching `engine.py`'s Step 2c2(b) comment ("A
    # gameplay-level rejection produces no additional event").
    assert {c.weapon for c in a_fire_commands} == {ModuleIdentity.MISSILE, ModuleIdentity.CANNON}
    a_fired = _fired_by(fired, ROBOT_A)
    assert [e.weapon for e in a_fired] == [ModuleIdentity.CANNON]  # missile never fired

    # ---------------------------------------------------------------
    # Lane B: shared combat channel.
    # ---------------------------------------------------------------
    b_fired = _fired_by(fired, ROBOT_B_SHOOTER)
    # Exactly two shots ever leave the channel: the first (tick 1) and the
    # refire after the channel freed (tick 20) -- the same-tick second
    # attempt at tick 1 never produced a projectile.
    assert len(b_fired) == 2
    assert b_fired[0].tick == FIRE_TICK
    assert b_fired[1].tick == LANE_B_REFIRE_TICK

    b_distance = 14 - 10  # ROBOT_B_TARGET.x - ROBOT_B_SHOOTER.x
    b_hit_tick = static_collision_tick(b_distance)
    b_terminated = _fired_by(terminated, ROBOT_B_SHOOTER)
    assert b_terminated[0].reason is ProjectileTerminationReason.ROBOT_HIT
    assert b_terminated[0].hit_robot_id == ROBOT_B_TARGET
    assert b_terminated[0].tick == b_hit_tick == 8
    b_damaged = [e for e in damaged if e.entity_id == ROBOT_B_TARGET and e.tick == b_hit_tick]
    assert len(b_damaged) == 1
    b_target_height = states[FIRE_TICK].robot_for(ROBOT_B_TARGET).height  # type: ignore[union-attr]
    assert b_damaged[0].damage == (60 - (b_target_height + 0)) // 4 * CANNON_MULT  # bare-terrain formula
    target_after_first_hit = states[b_hit_tick].robot_for(ROBOT_B_TARGET)
    assert target_after_first_hit is not None
    assert target_after_first_hit.strength == 100 - b_damaged[0].damage

    # ---------------------------------------------------------------
    # Lane C: projectile altitude + exact range-exhaustion tick.
    # ---------------------------------------------------------------
    c_fired = _fired_by(fired, ROBOT_C_SHOOTER)
    assert len(c_fired) == 1
    c_projectile_id = c_fired[0].entity_id
    # Read the projectile straight off state immediately after creation:
    # z is always the configured normal-projectile altitude, independent of
    # the firing robot's own height.
    projectile_right_after_fire = states[FIRE_TICK].projectile_for(c_projectile_id)
    assert projectile_right_after_fire is not None
    assert projectile_right_after_fire.z == ALTITUDE == 10
    c_expected_tick = range_exhaustion_tick(PHASER_RANGE)
    assert c_expected_tick == 24  # 5th move at 4 * 5, expiry one cadence later
    c_terminated = _fired_by(terminated, ROBOT_C_SHOOTER)
    assert len(c_terminated) == 1
    assert c_terminated[0].tick == c_expected_tick
    assert c_terminated[0].reason is ProjectileTerminationReason.RANGE_EXHAUSTED
    assert c_terminated[0].hit_robot_id is None  # clear path: no collision at all

    # ---------------------------------------------------------------
    # Lane D2: obstructed path, exactly AT the inclusive boundary --
    # `blocker-d2`'s height equals `ALTITUDE` exactly (10 == 10), proving
    # the `>=` comparison in `combat._components_at_inclusive_blocking`
    # really blocks a projectile at the boundary value itself, not just
    # comfortably above it.
    # ---------------------------------------------------------------
    d2_distance = D2_BLOCKER_X - D2_SHOOTER_X  # 5
    d2_expected_tick = static_collision_tick(d2_distance)
    # 2nd move (fire tick, 8): landing at +4, the 2×2 body covers +4..+5.
    assert d2_expected_tick == 8
    d2_terminated = _fired_by(terminated, ROBOT_D2_SHOOTER)
    assert len(d2_terminated) == 1
    assert d2_terminated[0].tick == d2_expected_tick
    assert d2_terminated[0].reason is ProjectileTerminationReason.STATIC_COLLISION
    assert (d2_terminated[0].x, d2_terminated[0].y) == (D2_BLOCKER_X - 1, D2_Y)
    # It never reached cannon's full 10-cell range.
    assert d2_distance < CANNON_RANGE

    # ---------------------------------------------------------------
    # Lane D3: pass-over (height < ALTITUDE) + missile weapon coverage.
    # ---------------------------------------------------------------
    d3_distance = D3_BLOCKER_X - D3_SHOOTER_X  # 5
    d3_pass_tick = static_collision_tick(d3_distance)  # the tick it WOULD have
    # collided at, if height 9 blocked -- it does not, proving the boundary
    # from the opposite side to Lane D2: one unit below `ALTITUDE` (9 < 10)
    # does not block, while exactly `ALTITUDE` (Lane D2's height-10 blocker)
    # does.
    projectile_at_blocker_tick = None
    for state in states[: d3_pass_tick + 1]:
        for projectile in state.projectiles:
            if projectile.source_robot_id == ROBOT_D3_SHOOTER:
                projectile_at_blocker_tick = projectile
    assert projectile_at_blocker_tick is not None
    # Its 2×2 body already covers (or is past) the low blocker.
    assert projectile_at_blocker_tick.x + 1 >= D3_BLOCKER_X
    d3_expected_tick = range_exhaustion_tick(MISSILE_RANGE)
    assert d3_expected_tick == 32  # 7th move at 4 * 7, expiry one cadence later
    d3_terminated = _fired_by(terminated, ROBOT_D3_SHOOTER)
    assert len(d3_terminated) == 1
    assert d3_terminated[0].tick == d3_expected_tick
    assert d3_terminated[0].reason is ProjectileTerminationReason.RANGE_EXHAUSTED
    assert d3_terminated[0].x == D3_SHOOTER_X + MISSILE_RANGE

    # ---------------------------------------------------------------
    # Lane E vs Lane F: direct control and autonomous engagement share the
    # exact same firing path (Task 7's "one fire path" proven again here at
    # full-integration scope).
    # ---------------------------------------------------------------
    e_fired = _fired_by(fired, ROBOT_E)
    assert len(e_fired) == 1
    e_projectile = states[FIRE_TICK].projectile_for(e_fired[0].entity_id)
    assert e_projectile is not None
    # Electronics bonus applied: cannon's base range plus the bonus.
    assert e_projectile.max_range_cells == CANNON_RANGE + ELECTRONICS_BONUS

    f_fired = _fired_by(fired, ROBOT_F_HUNTER)
    assert f_fired, "Search & Destroy must fire with no FireCommand ever submitted for it"
    # No FireCommand was ever issued naming ROBOT_F_HUNTER -- the only
    # commands in this fixture are listed in `_commands_by_tick`, and none
    # names this entity.
    assert all(
        not (isinstance(cmd, FireCommand) and cmd.entity_id == ROBOT_F_HUNTER)
        for cmds in _commands_by_tick().values()
        for cmd in cmds
    )
    # Same structural shape: same event type, same weapon, same resolved
    # cardinal direction (both fire due east).
    assert type(e_fired[0]) is type(f_fired[0]) is ProjectileFiredEvent
    assert e_fired[0].weapon is f_fired[0].weapon is ModuleIdentity.CANNON
    assert (e_fired[0].dx, e_fired[0].dy) == (f_fired[0].dx, f_fired[0].dy) == (1, 0)
    f_projectile_shape = states[f_fired[0].tick].projectile_for(f_fired[0].entity_id)
    assert f_projectile_shape is not None
    assert f_projectile_shape.z == e_projectile.z == ALTITUDE

    f_prey_final = final.robot_for(ROBOT_F_PREY)
    # Normal damage genuinely destroys the prey robot by the fixture's final
    # tick (not merely "damaged or destroyed, whichever" -- both this
    # lookup and the occupancy/roster checks below must show it gone).
    assert f_prey_final is None
    assert ROBOT_F_PREY not in {robot.entity_id for robot in final.robots}
    f_prey_occupancy = folded_robot_occupancy(drive.world, final)  # type: ignore[arg-type]
    assert not f_prey_occupancy.is_occupied(15, F_Y)  # ROBOT_F_PREY's former cell
    # ROBOT_F_PREY was never mid-capture and carried no order in this lane's
    # design, so there is no capture-progress reference for `destroy_robot`
    # to clean up here -- that cleanup path already has its own dedicated
    # coverage in `test_combat_damage.py`'s
    # `test_destroy_robot_removes_capture_progress_naming_it`.

    # ---------------------------------------------------------------
    # Lane G: a commander on active combat geometry is untargetable.
    # ---------------------------------------------------------------
    g_fired = _fired_by(fired, ROBOT_G_HUNTER)
    assert g_fired, "Lane G's autonomous engagement must actually fire"
    commander_g_initial = next(c for c in _commanders() if c.player_id == PLAYER_TWO)
    for state in states:
        commander_g_now = next(c for c in state.commanders if c.player_id == PLAYER_TWO)
        assert commander_g_now == commander_g_initial, (
            "no combat system may ever move/alter a commander -- it is not a "
            "Robot and not a structures.Component, so it is structurally "
            "invisible to both `combat._robot_hit_at` and "
            "`combat._components_at_inclusive_blocking`"
        )
    # The projectile really did fly through the commander's cell (x=13) on
    # its way from the hunter (x=10) to the prey (x=16).
    g_terminated = _fired_by(terminated, ROBOT_G_HUNTER)
    assert g_terminated
    assert g_terminated[0].hit_robot_id == ROBOT_G_PREY

    # ---------------------------------------------------------------
    # Lane H: factories/war bases are structurally immune to normal weapons
    # -- proven both without and, now, WITH a real geometric collision.
    # ---------------------------------------------------------------
    h_factory_terminated = _fired_by(terminated, ROBOT_H_SHOOTER)
    h_warbase_terminated = _fired_by(terminated, ROBOT_H2_SHOOTER)
    assert len(h_factory_terminated) == 1
    assert len(h_warbase_terminated) == 1
    # `factory-h` stays below `ALTITUDE` (height 3): the shot passes
    # straight over it and expires by range exhaustion, never colliding.
    assert h_factory_terminated[0].reason is ProjectileTerminationReason.RANGE_EXHAUSTED
    assert h_factory_terminated[0].hit_robot_id is None
    # `warbase-h` is at height 12 (>= ALTITUDE): the shot DOES geometrically
    # collide with it, the same STATIC_COLLISION outcome a blocker would
    # produce (`combat._components_at_inclusive_blocking` makes no
    # distinction between a blocker's and a structure's component). The
    # real proof of structural immunity is what happens next: a normal
    # weapon hits a structure head-on and nothing is destroyed, because no
    # normal-weapon code path ever calls `destruction.destroy_structure`
    # (see `destruction.py`'s own "Structure-only-via-nuclear is a
    # structural, not a runtime, invariant" docstring section).
    h_warbase_distance = 65 - 60  # WARBASE_H's cell - ROBOT_H2_SHOOTER.x
    assert h_warbase_terminated[0].reason is ProjectileTerminationReason.STATIC_COLLISION
    assert h_warbase_terminated[0].tick == static_collision_tick(h_warbase_distance) == 8
    # Landing at x=64: the 2×2 body covers the war base's cell at x=65.
    assert (h_warbase_terminated[0].x, h_warbase_terminated[0].y) == (64, H_Y)
    assert h_warbase_terminated[0].hit_robot_id is None
    assert FACTORY_H not in final.structure_destruction
    assert WARBASE_H not in final.structure_destruction  # survives its real collision
    assert structures_destroyed  # the suite as a whole does exercise destruction (Lanes I/J)
    assert all(e.structure_id not in (FACTORY_H, WARBASE_H) for e in structures_destroyed)

    # ---------------------------------------------------------------
    # Lane I: blast-shape boundary + deterministic ordering.
    # ---------------------------------------------------------------
    assert final.robot_for(ROBOT_I_CARRIER) is None  # carrier always destroyed
    assert final.robot_for(ROBOT_I_IN) is None  # window edge: included
    assert final.robot_for(ROBOT_I_OUT) is not None  # one cell further: excluded
    assert FACTORY_I_IN in final.structure_destruction
    assert FACTORY_I_OUT not in final.structure_destruction

    lane_i_tick_events = [e for e in events if getattr(e, "tick", None) == FIRE_TICK]
    lane_i_destroy_events = [
        e
        for e in lane_i_tick_events
        if (isinstance(e, RobotDestroyedEvent) and e.entity_id in (ROBOT_I_CARRIER, ROBOT_I_IN))
        or (isinstance(e, StructureDestroyedEvent) and e.structure_id == FACTORY_I_IN)
    ]
    # Carrier -> robots -> structures, in that fixed order (Task 8's
    # documented `execute_nuclear_detonation` sequence). Note `events` is
    # already `order_events`-sorted by `sequence` per tick (see `_drive`),
    # so `ordered_kinds`' order below reflects real event-sequence order,
    # not incidental list-construction order; the `sequence` numbers
    # themselves are only checked here for uniqueness (no ties), not for
    # sortedness (which `order_events` already guarantees upstream and
    # this test does not re-verify).
    ordered_kinds = [type(e).__name__ for e in lane_i_destroy_events]
    assert ordered_kinds == ["RobotDestroyedEvent", "RobotDestroyedEvent", "StructureDestroyedEvent"]
    assert lane_i_destroy_events[0].entity_id == ROBOT_I_CARRIER  # type: ignore[attr-defined]
    assert lane_i_destroy_events[1].entity_id == ROBOT_I_IN  # type: ignore[attr-defined]
    sequences = [e.sequence for e in lane_i_destroy_events]
    assert len(set(sequences)) == len(sequences)  # no ties among the three

    # ---------------------------------------------------------------
    # Lane J: nuclear destruction of the opponent's final war base
    # triggers victory in that exact authoritative step.
    # ---------------------------------------------------------------
    assert WARBASE_J_FINAL in final.structure_destruction
    j_structure_events = [
        e
        for e in structures_destroyed
        if e.structure_id == WARBASE_J_FINAL and e.structure_kind is CapturableStructureKind.WAR_BASE
    ]
    assert len(j_structure_events) == 1
    assert len(victories) == 1
    assert victories[0].winner == PLAYER_ONE
    assert victories[0].tick == FIRE_TICK == j_structure_events[0].tick
    # p1's own home war base, and Lane H's p1-owned war base, both survive
    # untouched -- p1 never loses war-base ownership during this scenario.
    assert WARBASE_HOME not in final.structure_destruction
    assert WARBASE_H not in final.structure_destruction

    # ---------------------------------------------------------------
    # Snapshot content check (honest naming per Task 11's correction: this
    # engine has no deserializer -- see the module docstring / the
    # dedicated test below for the full reasoning).
    # ---------------------------------------------------------------
    mid_flight_snapshot = json.loads(snapshot_to_json_string(states[SNAPSHOT_TICK]))
    projectile_entries = {p["id"]: p for p in mid_flight_snapshot["projectiles"]}
    c_entry = projectile_entries[c_projectile_id.to_json()]
    # The fire-tick move, then one move per cadence tick from 2 * ADVANCE on.
    expected_travelled = SNAPSHOT_TICK // ADVANCE * CELLS_PER_ADVANCE
    assert c_entry["weapon"] == ModuleIdentity.PHASER.value
    assert c_entry["x"] == 10 + expected_travelled
    assert c_entry["y"] == C_Y
    assert c_entry["travelled_cells"] == expected_travelled

    # The overall event stream really did exercise every M6 outcome kind at
    # least once -- the acceptance criterion this whole module exists for.
    assert fired and terminated and damaged and robots_destroyed and structures_destroyed
    assert victories


def test_snapshot_content_captures_in_flight_projectile_state() -> None:
    """The snapshot's own content is complete for an in-flight projectile.

    This engine has no deserializer (``snapshot.py`` is serialize-only --
    there is no ``from_snapshot``/``restore_game_state`` anywhere in this
    codebase). So this test does NOT claim a snapshot/restore round trip:
    it takes a snapshot mid-flight and continues the SAME already-in-memory
    ``GameState`` object forward, then confirms the snapshot string taken
    at that moment correctly and completely reflects the projectile's
    position/lifecycle fields at that exact tick -- proving a hypothetical
    future deserializer would have enough information to reproduce this
    moment, without claiming deserialization itself works. Mirrors
    `test_engine_combat_integration.py`'s own
    ``test_combat_state_round_trips_into_the_snapshot`` naming discipline,
    which Task 10's review explicitly praised for not overclaiming.
    """
    fixture = _fixture()
    drive = _drive(fixture)

    mid_state = drive.states[SNAPSHOT_TICK]
    c_fired = _fired_by(_events_of(drive.events, ProjectileFiredEvent), ROBOT_C_SHOOTER)
    assert len(c_fired) == 1
    projectile_id = c_fired[0].entity_id

    live_projectile = mid_state.projectile_for(projectile_id)
    assert live_projectile is not None  # genuinely still in flight at this tick

    snapshot_str = snapshot_to_json_string(mid_state)
    snapshot = json.loads(snapshot_str)
    entry = next(p for p in snapshot["projectiles"] if p["id"] == projectile_id.to_json())

    assert entry["x"] == live_projectile.x
    assert entry["y"] == live_projectile.y
    assert entry["z"] == live_projectile.z
    assert entry["dx"] == live_projectile.dx
    assert entry["dy"] == live_projectile.dy
    assert entry["owner"] == live_projectile.owner.to_json()
    assert entry["source_robot_id"] == live_projectile.source_robot_id.to_json()
    assert entry["created_tick"] == live_projectile.created_tick
    assert entry["travelled_cells"] == live_projectile.travelled_cells
    assert entry["weapon"] == live_projectile.weapon.value
    assert entry["max_range_cells"] == live_projectile.max_range_cells

    # Continue the SAME in-memory state forward (not a rebuilt-from-snapshot
    # state) and confirm the scenario's documented final outcome still
    # follows deterministically from this exact mid-flight moment.
    state = mid_state
    for tick in range(SNAPSHOT_TICK + 1, TOTAL_TICKS + 1):
        cmds = fixture.commands_by_tick.get(tick, ())
        state, _events = engine.step(state, cmds, world=fixture.world)

    # Continuing the same in-memory state to the fixture's own final tick
    # reproduces exactly the same documented outcome as the full drive:
    # Lane C's projectile has, by then, terminated by range exhaustion.
    assert state.projectile_for(projectile_id) is None
    assert state == drive.states[-1]


def test_full_milestone_scenario_replays_identically() -> None:
    fixture = _fixture()
    state_a, events_a = run_fixture(fixture)
    state_b, events_b = run_fixture(fixture)

    assert state_a == state_b
    assert snapshot_to_json_string(state_a) == snapshot_to_json_string(state_b)
    assert events_a == events_b

    # The fixture must actually exercise every M6 outcome, or determinism
    # proves nothing.
    assert _events_of(events_a, ProjectileFiredEvent) != []
    assert _events_of(events_a, ProjectileTerminatedEvent) != []
    assert _events_of(events_a, RobotDamagedEvent) != []
    assert _events_of(events_a, RobotDestroyedEvent) != []
    assert _events_of(events_a, StructureDestroyedEvent) != []
    assert _events_of(events_a, VictoryEvent) != []

    # Cross-check against the recording drive used by the main scenario
    # test, so both code paths agree on the final outcome.
    drive = _drive(fixture)
    assert drive.states[-1] == state_a
    assert drive.events == events_a
