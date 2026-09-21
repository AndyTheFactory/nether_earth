"""Milestone 4 integration scenario (issue #58, M4.8).

This is the final M4 ("Robots, Construction & Economy") integration gate
described in `_specs/milestones/04-robots-construction-economy.md` under
"Milestone integration scenario" and issue #58's acceptance criteria. It
composes the already-merged M4 primitives -- ``robot_build.py`` (#52),
``robot_stack.py`` (#53), ``construction_economy.py`` (#34),
``resource_pool.py``/``resource_production.py`` (#54), ``construction_session.py``
(#55), ``robot_launch.py``/``robot.py`` (#56), and ``engine.py``'s
``construction_commands.py`` wiring (#57) -- end to end, proving the whole
construction/economy subsystem works together, driven entirely through
``engine.new_game``/``engine.step`` and real player-issued ``Command``
instances (never the underlying pure functions directly -- those are
already unit-tested in isolation by #52-#57's own test modules, see
``test_robot_build.py``, ``test_robot_stack.py``, ``test_construction_economy.py``,
``test_resource_pool.py``, ``test_resource_production.py``,
``test_construction_session.py``, ``test_robot_launch.py``, and
``test_engine_construction_integration.py``).

No new production code is introduced by this task; every rule exercised
below is already unit-tested by #52-#57 in isolation. This module's job is
only to prove the pieces *compose* correctly end to end, following
`test_m3_integration.py`'s exact structure/style (fixture reuse, phase-by-
phase focused tests, one composed full-scenario driver replayed twice for
determinism).

Fixture reuse and the one permitted new fixture
--------------------------------------------------

Per the issue's explicit instruction to consume real M2 world/interaction
data rather than test-only alternate geometry, this module first checked
both existing shared YAML fixtures:

- ``fixtures/world_map_basic.yaml`` (used by M2/M3's own integration
  gates) places each war base's ``EXIT`` interaction point directly on top
  of one of its own physical components -- exactly the problem
  `test_robot_launch.py`'s own module docstring documents as the reason
  *that* module builds ``WorldMap`` fixtures directly in Python instead:
  a permanently-occupied exit cannot exercise "block then clear the exit"
  at all.
- ``fixtures/world_map_production.yaml`` (used by `test_resource_production.py`)
  declares owned factories/war bases but zero interaction points, so it
  cannot drive heli-pad landing or launch either.

Neither fixture alone covers this milestone's full scenario (production
*and* construction entry *and* launch), so this module adds one new,
minimal YAML fixture, ``fixtures/world_map_m4_integration.yaml``, combining
what both partially offer: owned factories for deterministic production,
plus a heli-pad and a *free* (non-component) exit cell per war base. See
that fixture file's own header comment for its exact declared geometry.

Commander placement: real M3 heli-pad landing contract, not a shortcut
------------------------------------------------------------------------

Per the M3 integration precedent (`test_m3_integration.py`'s own module
docstring, "Landing on the friendly war-base heli-pad is deliberately not
chained onto [a] flight" when a fixture's geometry cannot support flying a
commander all the way there), and per this task's own brief ("place it
there at tick 0 if the fixture/scenario setup makes that the more natural
M3-consistent approach"), this module places p1's commander directly on
its own heli-pad cell via ``GameState.with_commanders`` -- the same public,
canonical ``GameState`` transition ``engine.new_game`` itself uses
internally, not a construction-specific shortcut -- rather than driving a
multi-tick flight to get there (M3's own movement/collision rules are
already fully proven by `test_m3_integration.py`; this module's job is
construction/economy, not re-proving movement). Placement happens *after*
the one-game-day production phase completes (not at tick 0), specifically
so ``enter_construction``'s entry snapshot captures the player's actual,
already-produced resources -- landing before production would snapshot a
stale, pre-production buffer instead, an entirely different (and untested-
by-this-scenario) code path.

From that point on, every single rule exercised -- construction entry,
module selection/deselection, cancellation, launch, the 24-robot cap, and
exit blocking -- is driven exclusively through real
:class:`~nether_earth.construction_commands.SelectModuleCommand`/
:class:`~nether_earth.construction_commands.DeselectModuleCommand`/
:class:`~nether_earth.construction_commands.CancelConstructionCommand`/
:class:`~nether_earth.construction_commands.LaunchRobotCommand` instances
passed to ``engine.step`` -- never ``construction_session.py``/
``robot_launch.py``'s pure functions directly.
"""

from __future__ import annotations

from pathlib import Path

from nether_earth.commander import Commander, CommanderMode
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    ConstructionCancelledEvent,
    ConstructionEnteredEvent,
    DeselectModuleCommand,
    LaunchRobotCommand,
    ModuleDeselectedEvent,
    ModuleSelectedEvent,
    RobotLaunchedEvent,
    SelectModuleCommand,
)
from nether_earth.construction_economy import module_cost
from nether_earth.engine import new_game, step
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import BootstrapMap, WorldMap, load_world_map
from nether_earth.resource_pool import PlayerResourcePool, starting_player_resource_pool
from nether_earth.resource_production import DailyProductionApplied
from nether_earth.robot import Robot
from nether_earth.robot_build import ModuleIdentity, RobotBuild, resource_category
from nether_earth.robot_stack import derive_stack_and_height
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState
from nether_earth.structures import FactoryType

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "world_map_m4_integration.yaml"

RULES = DEFAULT_RULES
WAR_BASE_ONE = EntityId("warbase-p1")
P1_HELI_PAD_CELL = (4, 1)  # 2×2 pad anchor (CR002.4)
# The fixture's p1 heli-pad cell sits on warbase-p1's 3-high component: the
# commander lands at that component height (open-questions.md §18).
PAD_ROOF_ALTITUDE = 3
P1_EXIT_CELL = (7, 1)  # 2×2 robot body anchor (CR002.3)
TICKS_PER_DAY = 2880


def _scenario_and_map() -> tuple[Scenario, BootstrapMap]:
    scenario = Scenario(
        id="m4-integration-scenario",
        map_id="fixture-m4-integration",
        map_version=1,
        player_starting_warbases=1,
    )
    map_data = BootstrapMap(map_id="fixture-m4-integration", version=1, width=10, height=6)
    return scenario, map_data


def _world() -> WorldMap:
    return load_world_map(FIXTURE_PATH)


def _new_game(commanders: tuple[Commander, ...] = (), seed: int = 7) -> GameState:
    scenario, map_data = _scenario_and_map()
    return new_game(map_data, scenario, players=[PLAYER_ONE, PLAYER_TWO], seed=seed, commanders=commanders)


def _seeded_state(seed: int = 7) -> GameState:
    """Return a tick-0 state with both players' starting resource pools seeded.

    Mirrors `test_engine_construction_integration.py`'s own ``_base_state``
    precedent (and its documented rationale): ``engine.new_game``/
    ``create_game_state`` deliberately do not auto-seed resource pools --
    granting match-start resources is a scenario/caller responsibility, not
    an engine-internals one -- so this scenario's own setup does it
    explicitly via :func:`~nether_earth.resource_pool.starting_player_resource_pool`,
    the same function item 1 of the scenario checklist verifies below.
    """
    state = _new_game(seed=seed)
    return state.with_resource_pools(
        (
            starting_player_resource_pool(PLAYER_ONE, RULES),
            starting_player_resource_pool(PLAYER_TWO, RULES),
        )
    )


def _grounded_commander(player_id: PlayerId, cell: tuple[int, int]) -> Commander:
    x, y = cell
    return Commander(player_id=player_id, mode=CommanderMode.FREE, x=x, y=y, altitude=PAD_ROOF_ALTITUDE)


# ---------------------------------------------------------------------------
# 1. Player initialization: default 20 general resources + zeroed type pools
# ---------------------------------------------------------------------------


def test_player_initializes_with_default_general_resources_and_zeroed_type_pools() -> None:
    pool = starting_player_resource_pool(PLAYER_ONE, RULES)

    assert RULES.starting_general_resources == 20
    assert pool.general == 20
    for category in FactoryType:
        assert pool.amount(category) == 0

    state = _seeded_state()
    assert state.resource_pool_for(PLAYER_ONE) == pool
    assert state.resource_pool_for(PLAYER_TWO) == starting_player_resource_pool(PLAYER_TWO, RULES)


# ---------------------------------------------------------------------------
# 2. Daily production: exactly one day, correct per-structure amounts
# ---------------------------------------------------------------------------


def test_one_game_day_produces_factory_and_war_base_resources_exactly_once() -> None:
    world = _world()
    state = _seeded_state()

    production_events: list[DailyProductionApplied] = []
    for _ in range(TICKS_PER_DAY):
        state, tick_events = step(state, [], world=world)
        production_events.extend(
            e for e in tick_events if isinstance(e, DailyProductionApplied)
        )

    assert state.tick == TICKS_PER_DAY
    # Exactly one production application per player that owns something --
    # both players own a war base, so both produce exactly once.
    assert len(production_events) == 2
    by_player = {e.player: e for e in production_events}
    assert by_player[PLAYER_ONE].day_boundaries_crossed == 1
    assert by_player[PLAYER_TWO].day_boundaries_crossed == 1

    # p1 owns 2 chassis factories + 1 cannon factory + 1 missile factory +
    # its war base. The single missile factory is deliberate (see the
    # fixture's own header comment): it produces less than
    # module_cost_missile, so a later selection can exercise "partial
    # type-specific stock, general covers only the remaining shortfall"
    # (item 5) as distinct from "zero stock, whole cost from general".
    p1_pool = state.resource_pool_for(PLAYER_ONE)
    assert p1_pool is not None
    assert p1_pool.general == RULES.starting_general_resources + RULES.war_base_production_amount
    assert p1_pool.chassis == 2 * RULES.factory_production_amount
    assert p1_pool.cannon == 1 * RULES.factory_production_amount
    assert p1_pool.missile == 1 * RULES.factory_production_amount
    assert p1_pool.missile < module_cost(ModuleIdentity.MISSILE, RULES)
    assert p1_pool.phaser == 0
    assert p1_pool.nuclear == 0
    assert p1_pool.electronics == 0

    # p2 owns only its war base -- general resources only, no factories.
    p2_pool = state.resource_pool_for(PLAYER_TWO)
    assert p2_pool is not None
    assert p2_pool.general == RULES.starting_general_resources + RULES.war_base_production_amount
    for category in FactoryType:
        assert p2_pool.amount(category) == 0


# ---------------------------------------------------------------------------
# 7. Locked M4 component costs and resource-category mapping (Spectrum table)
# ---------------------------------------------------------------------------


def test_all_eight_module_costs_and_categories_match_the_locked_spectrum_table() -> None:
    expected = {
        ModuleIdentity.BIPOD: (3, FactoryType.CHASSIS),
        ModuleIdentity.TRACKS: (5, FactoryType.CHASSIS),
        ModuleIdentity.ANTI_GRAV: (10, FactoryType.CHASSIS),
        ModuleIdentity.CANNON: (2, FactoryType.CANNON),
        ModuleIdentity.MISSILE: (4, FactoryType.MISSILE),
        ModuleIdentity.PHASER: (4, FactoryType.PHASER),
        ModuleIdentity.NUCLEAR: (20, FactoryType.NUCLEAR),
        ModuleIdentity.ELECTRONICS: (3, FactoryType.ELECTRONICS),
    }
    assert set(expected) == set(ModuleIdentity)
    for identity, (cost, category) in expected.items():
        assert module_cost(identity, RULES) == cost
        assert resource_category(identity) == category


# ---------------------------------------------------------------------------
# 8b. Insufficient resources: a selection that can't be covered anywhere
# ---------------------------------------------------------------------------


def test_select_module_rejected_for_insufficient_resources_leaves_session_unchanged() -> None:
    world = _world()
    commander = _grounded_commander(PLAYER_ONE, P1_HELI_PAD_CELL)
    state = _new_game((commander,))
    # A deliberately poor player: 1 general resource, nothing else -- not
    # enough to cover even the cheapest module (cannon, cost 2), let alone
    # the nuclear weapon attempted below (cost 20).
    state = state.with_resource_pools((PlayerResourcePool(player_id=PLAYER_ONE, general=1),))

    state, entered_events = step(state, [], world=world)
    assert any(isinstance(e, ConstructionEnteredEvent) for e in entered_events)
    session_before = state.construction_session_for(PLAYER_ONE)
    assert session_before is not None

    select_nuclear = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.NUCLEAR)
    state, events = step(state, [select_nuclear], world=world)

    assert not any(isinstance(e, ModuleSelectedEvent) for e in events)
    session_after = state.construction_session_for(PLAYER_ONE)
    assert session_after == session_before
    assert session_after is not None
    assert not session_after.build.contains(ModuleIdentity.NUCLEAR)


# ---------------------------------------------------------------------------
# 10. 24-robot cap rejects a 25th launch
# ---------------------------------------------------------------------------


def _dummy_build() -> RobotBuild:
    return RobotBuild(chassis=ModuleIdentity.TRACKS, weapons=(ModuleIdentity.MISSILE,))


def test_robot_cap_rejects_a_25th_launch() -> None:
    world = _world()
    commander = _grounded_commander(PLAYER_ONE, P1_HELI_PAD_CELL)
    state = _new_game((commander,))
    # A rich player: general resources alone comfortably cover a build,
    # since only the cap (not affordability) is under test here.
    state = state.with_resource_pools((PlayerResourcePool(player_id=PLAYER_ONE, general=100),))
    state, _events = step(state, [], world=world)  # auto-enter construction

    select_chassis = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_weapon = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, _events = step(state, [select_chassis, select_weapon], world=world)
    assert state.construction_session_for(PLAYER_ONE) is not None

    # Directly seed 24 already-existing robots (issue #58's own brief
    # explicitly allows this over 24 real launches, "if 24 real launches
    # would be impractically slow" -- 24 full construct/launch cycles would
    # add nothing this scenario doesn't already prove once via a real
    # launch below) at cells far outside this fixture's own geometry so
    # they cannot incidentally interact with anything else in this module.
    build = _dummy_build()
    stack, height = derive_stack_and_height(build, RULES)
    dummy_robots = tuple(
        Robot(
            entity_id=EntityId(f"seed-robot-{i}"),
            owner=PLAYER_ONE,
            x=100 + 2 * i,
            y=100,
            build=build,
            stack=stack,
            height=height,
        )
        for i in range(RULES.max_robots_per_player)
    )
    state = state.with_robots(dummy_robots)
    assert len(state.robots_for(PLAYER_ONE)) == RULES.max_robots_per_player

    pre_launch_state = state
    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=0)
    result_state, events = step(pre_launch_state, [launch], world=world)

    assert not any(isinstance(e, RobotLaunchedEvent) for e in events)
    assert len(result_state.robots_for(PLAYER_ONE)) == RULES.max_robots_per_player
    assert result_state.construction_session_for(PLAYER_ONE) is not None
    assert result_state.resource_pools == pre_launch_state.resource_pools


# ---------------------------------------------------------------------------
# 3-6, 9, 11-12: full phase-by-phase milestone scenario + replay determinism
# ---------------------------------------------------------------------------


def _drive_full_scenario(world: WorldMap) -> tuple[GameState, tuple[object, ...]]:
    """Drive one coherent multi-phase scenario exercising nearly every M4 rule.

    Phases (all against the shared fixture map/world):

    1. One full game day of production for both players (item 2).
    2. p1's commander lands on its own heli-pad -- placed there directly
       (per the module docstring) *after* production, so construction
       entry snapshots the already-produced resources -- opening a
       construction session (item 3).
    3. A first, fully legal build (bipod + cannon) is selected using
       *only* type-specific resources -- chassis/cannon production exactly
       covers both costs, so general resources are untouched (item 4, and
       item 7's costs are exercised structurally here).
    4. Cannon is deselected then immediately reselected, proving reversible
       temporary accounting: the buffer after the round trip is byte-for-
       byte identical to the buffer immediately before it (item 6).
    5. Two more weapons are added: missile, whose type-specific pool holds
       *some* stock (one factory's worth) but strictly less than the
       module's own cost, draining that partial stock to exactly 0 and
       drawing only the remaining shortfall from general resources -- the
       literal "spend more of a type than its starting pool held" case
       item 5 asks for; then phaser, whose category has zero production at
       all, spending its entire cost from general -- the simpler "zero
       stock" variant, kept alongside missile's partial-stock case for
       contrast.
    6. A fourth weapon is attempted and rejected (invalid build shape)
       without mutating the session (item 8, first half). A different
       chassis is then picked, which swaps it (CR002.20), and picking the
       original back restores the exact same session buffer.
    7. The whole session is cancelled unlaunched; the player's actual
       resource pool is asserted unchanged from its post-production value
       (item 9).
    8. The commander, still grounded on the heli-pad, is auto-re-entered
       into a fresh session on the very next tick; a second, real launch-
       bound build (bipod + cannon again) is selected.
    9. The war base's own EXIT cell is blocked by another robot; launch is
       rejected with no resource/robot/session mutation (item 11).
    10. The exit is cleared and launch succeeds: exactly one robot is
        created, with the correct owner/build/stack/height/spawn cell, and
        the session's buffer is committed as the player's new actual
        resource pool atomically and exactly once (item 12).

    Returns the final state and the full ordered event stream, so callers
    can replay this twice and diff the results for determinism (item 13).
    """
    state = _seeded_state()
    all_events: list[object] = []

    # --- Phase 1: one full game day of production -----------------------
    for _ in range(TICKS_PER_DAY):
        state, events = step(state, [], world=world)
        all_events.extend(events)

    p1_pool_after_production = state.resource_pool_for(PLAYER_ONE)
    assert p1_pool_after_production is not None
    assert p1_pool_after_production.general == 25
    assert p1_pool_after_production.chassis == 4
    assert p1_pool_after_production.cannon == 2
    assert p1_pool_after_production.missile == 2

    # --- Phase 2: land on the heli-pad, auto-enter construction ----------
    commander = _grounded_commander(PLAYER_ONE, P1_HELI_PAD_CELL)
    state = state.with_commanders((commander,))
    state, events = step(state, [], world=world)
    all_events.extend(events)
    entered = [e for e in events if isinstance(e, ConstructionEnteredEvent)]
    assert len(entered) == 1
    assert entered[0].war_base_id == WAR_BASE_ONE
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.buffer.general == 25
    assert session.buffer.amount(FactoryType.CHASSIS) == 4
    assert session.buffer.amount(FactoryType.CANNON) == 2
    assert session.buffer.amount(FactoryType.MISSILE) == 2

    # --- Phase 3: first build, pure type-specific resources ---------------
    select_bipod = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_cannon = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, events = step(state, [select_bipod, select_cannon], world=world)
    all_events.extend(events)
    selected = {e.module for e in events if isinstance(e, ModuleSelectedEvent)}
    assert selected == {ModuleIdentity.BIPOD, ModuleIdentity.CANNON}
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis == ModuleIdentity.BIPOD
    assert session.build.weapons == (ModuleIdentity.CANNON,)
    assert session.build.is_complete()
    # No general-pool shortfall: type-specific pools exactly covered both.
    assert session.buffer.general == 25
    assert session.buffer.amount(FactoryType.CHASSIS) == 4 - module_cost(ModuleIdentity.BIPOD, RULES)
    assert session.buffer.amount(FactoryType.CANNON) == 2 - module_cost(ModuleIdentity.CANNON, RULES)

    # --- Phase 4: remove and re-add mid-build, exactly reversible --------
    buffer_before_deselect = session.buffer
    deselect_cannon = DeselectModuleCommand(player=PLAYER_ONE, sequence=2, module=ModuleIdentity.CANNON)
    state, events = step(state, [deselect_cannon], world=world)
    all_events.extend(events)
    assert any(isinstance(e, ModuleDeselectedEvent) for e in events)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert not session.build.contains(ModuleIdentity.CANNON)
    # Cannon's type-specific pool is fully restored (2 <= pre-construction
    # snapshot amount of 2); general is untouched by the refund.
    assert session.buffer.amount(FactoryType.CANNON) == 2
    assert session.buffer.general == buffer_before_deselect.general

    reselect_cannon = SelectModuleCommand(player=PLAYER_ONE, sequence=3, module=ModuleIdentity.CANNON)
    state, events = step(state, [reselect_cannon], world=world)
    all_events.extend(events)
    assert any(isinstance(e, ModuleSelectedEvent) for e in events)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    # Exactly reversible: back to the identical buffer the deselect started
    # from -- not merely "close", byte-for-byte equal.
    assert session.buffer == buffer_before_deselect

    # --- Phase 5: two more weapons, forced general-resource shortfall ----
    # Missile: the buffer holds a *partial* type-specific stock (2, from
    # one factory's day of production) that is strictly less than
    # module_cost_missile (4) -- the literal "spend more of a type than
    # its starting pool held" case. spend_module drains the partial stock
    # to exactly 0 and draws only the remaining shortfall (cost - stock)
    # from general, never the whole cost.
    missile_stock_before = session.buffer.amount(FactoryType.MISSILE)
    missile_cost = module_cost(ModuleIdentity.MISSILE, RULES)
    assert 0 < missile_stock_before < missile_cost  # precondition for this exact case
    general_before_missile = session.buffer.general

    select_missile = SelectModuleCommand(player=PLAYER_ONE, sequence=4, module=ModuleIdentity.MISSILE)
    state, events = step(state, [select_missile], world=world)
    all_events.extend(events)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    # (a) the category pool is drained to exactly 0 ...
    assert session.buffer.amount(FactoryType.MISSILE) == 0
    # ... and (b) general absorbs exactly the remainder (cost - pre-existing
    # category amount), not the module's whole cost.
    missile_shortfall = missile_cost - missile_stock_before
    assert missile_shortfall < missile_cost
    assert session.buffer.general == general_before_missile - missile_shortfall

    # Phaser: zero type-specific stock at all -- the simpler "whole cost
    # from general" variant, kept alongside missile's partial-stock case
    # above for contrast (both are documented general-resource
    # substitution paths, but only missile's is the exact "partial stock"
    # shape item 5 asks for).
    select_phaser = SelectModuleCommand(player=PLAYER_ONE, sequence=5, module=ModuleIdentity.PHASER)
    state, events = step(state, [select_phaser], world=world)
    all_events.extend(events)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.buffer.amount(FactoryType.PHASER) == 0
    general_after_weapons = (
        general_before_missile - missile_shortfall - module_cost(ModuleIdentity.PHASER, RULES)
    )
    assert session.buffer.general == general_after_weapons
    assert session.build.weapons == (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER)

    # --- Phase 6: invalid selections are rejected, session untouched -----
    session_before_invalid = state.construction_session_for(PLAYER_ONE)
    fourth_weapon = SelectModuleCommand(player=PLAYER_ONE, sequence=6, module=ModuleIdentity.NUCLEAR)
    state, events = step(state, [fourth_weapon], world=world)
    all_events.extend(events)
    assert not any(isinstance(e, ModuleSelectedEvent) for e in events)
    session_after_invalid = state.construction_session_for(PLAYER_ONE)
    assert session_after_invalid == session_before_invalid

    # Picking another chassis swaps it (CR002.20); swapping back is exact.
    swap_to_tracks = SelectModuleCommand(player=PLAYER_ONE, sequence=7, module=ModuleIdentity.TRACKS)
    state, events = step(state, [swap_to_tracks], world=world)
    all_events.extend(events)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis == ModuleIdentity.TRACKS
    assert session.build.weapons == session_before_invalid.build.weapons
    swap_back = SelectModuleCommand(player=PLAYER_ONE, sequence=8, module=ModuleIdentity.BIPOD)
    state, events = step(state, [swap_back], world=world)
    all_events.extend(events)
    assert state.construction_session_for(PLAYER_ONE) == session_before_invalid

    # --- Phase 7: cancel unlaunched, actual resources byte-for-byte same -
    actual_pool_before_cancel = state.resource_pool_for(PLAYER_ONE)
    assert actual_pool_before_cancel == p1_pool_after_production
    cancel = CancelConstructionCommand(player=PLAYER_ONE, sequence=9)
    state, events = step(state, [cancel], world=world)
    all_events.extend(events)
    cancelled = [e for e in events if isinstance(e, ConstructionCancelledEvent)]
    assert len(cancelled) == 1
    assert state.construction_session_for(PLAYER_ONE) is None
    actual_pool_after_cancel = state.resource_pool_for(PLAYER_ONE)
    assert actual_pool_after_cancel == actual_pool_before_cancel == p1_pool_after_production

    # --- Phase 8: exit ascent, fall back onto the pad, auto-re-enter -----
    # EXIT MENU lifts the commander off the pad (CR002.12/13); left alone,
    # gravity lands it on the pad again and construction re-opens.
    pad_altitude = state.commander_for(PLAYER_ONE).altitude  # type: ignore[union-attr]
    peak = pad_altitude
    re_entered = []
    for _ in range(200):
        state, events = step(state, [], world=world)
        all_events.extend(events)
        peak = max(peak, state.commander_for(PLAYER_ONE).altitude)  # type: ignore[union-attr]
        re_entered = [e for e in events if isinstance(e, ConstructionEnteredEvent)]
        if re_entered:
            break
    assert len(re_entered) == 1
    assert peak == pad_altitude + 5 * RULES.commander_ascent_step
    assert state.commander_for(PLAYER_ONE).altitude == pad_altitude  # type: ignore[union-attr]
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.buffer == p1_pool_after_production.to_resource_pool()

    select_bipod_2 = SelectModuleCommand(player=PLAYER_ONE, sequence=0, module=ModuleIdentity.BIPOD)
    select_cannon_2 = SelectModuleCommand(player=PLAYER_ONE, sequence=1, module=ModuleIdentity.CANNON)
    state, events = step(state, [select_bipod_2, select_cannon_2], world=world)
    all_events.extend(events)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.is_complete()

    # --- Phase 9: blocked exit rejects launch, nothing mutated -----------
    blocker_build = _dummy_build()
    blocker_stack, blocker_height = derive_stack_and_height(blocker_build, RULES)
    blocker = Robot(
        entity_id=EntityId("blocker-robot"),
        owner=PLAYER_TWO,
        x=P1_EXIT_CELL[0],
        y=P1_EXIT_CELL[1],
        build=blocker_build,
        stack=blocker_stack,
        height=blocker_height,
    )
    state = state.with_robots((*state.robots, blocker))
    pre_blocked_launch_state = state
    launch = LaunchRobotCommand(player=PLAYER_ONE, sequence=2)
    state, events = step(state, [launch], world=world)
    all_events.extend(events)
    assert not any(isinstance(e, RobotLaunchedEvent) for e in events)
    assert state.robots == pre_blocked_launch_state.robots
    assert state.resource_pools == pre_blocked_launch_state.resource_pools
    assert state.construction_session_for(PLAYER_ONE) == pre_blocked_launch_state.construction_session_for(
        PLAYER_ONE
    )

    # --- Phase 10: clear the exit, launch succeeds, atomic commit --------
    state = state.with_robots(tuple(r for r in state.robots if r.entity_id != blocker.entity_id))
    launch_again = LaunchRobotCommand(player=PLAYER_ONE, sequence=3)
    state, events = step(state, [launch_again], world=world)
    all_events.extend(events)
    launched = [e for e in events if isinstance(e, RobotLaunchedEvent)]
    assert len(launched) == 1
    assert launched[0].player == PLAYER_ONE

    assert state.construction_session_for(PLAYER_ONE) is None
    p1_robots = state.robots_for(PLAYER_ONE)
    assert len(p1_robots) == 1
    robot = p1_robots[0]
    assert robot.entity_id == launched[0].robot_id
    assert robot.owner == PLAYER_ONE
    assert robot.build == RobotBuild(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    expected_stack, expected_height = derive_stack_and_height(robot.build, RULES)
    assert robot.stack == expected_stack
    assert robot.height == expected_height
    assert (robot.x, robot.y) == P1_EXIT_CELL

    # Atomic commit exactly once: the actual pool now equals exactly the
    # session buffer that was in effect at launch time (bipod+cannon spent,
    # fully from type-specific resources -- general untouched again).
    final_pool = state.resource_pool_for(PLAYER_ONE)
    assert final_pool is not None
    assert final_pool.general == 25
    assert final_pool.chassis == 4 - module_cost(ModuleIdentity.BIPOD, RULES)
    assert final_pool.cannon == 2 - module_cost(ModuleIdentity.CANNON, RULES)

    return state, tuple(all_events)


def test_full_milestone_scenario_composes_all_m4_rules() -> None:
    world = _world()
    final_state, events = _drive_full_scenario(world)

    # Every locked M4 rule left an observable trace in the event stream.
    assert any(isinstance(e, DailyProductionApplied) for e in events)
    assert any(isinstance(e, ConstructionEnteredEvent) for e in events)
    assert any(isinstance(e, ModuleSelectedEvent) for e in events)
    assert any(isinstance(e, ModuleDeselectedEvent) for e in events)
    assert any(isinstance(e, ConstructionCancelledEvent) for e in events)
    assert any(isinstance(e, RobotLaunchedEvent) for e in events)

    assert len(final_state.robots) == 1
    assert final_state.robots[0].owner == PLAYER_ONE


def test_full_milestone_scenario_replays_identically() -> None:
    world = _world()

    final_a, events_a = _drive_full_scenario(world)
    final_b, events_b = _drive_full_scenario(world)

    assert final_a == final_b
    assert events_a == events_b
    assert to_snapshot(final_a) == to_snapshot(final_b)
