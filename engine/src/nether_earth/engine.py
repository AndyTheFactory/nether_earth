"""Canonical engine integration API: ``new_game`` and ``step``.

This module is the integration point for issue #7, combining the four
prerequisite building blocks (``state.py``/``ids.py``, ``clock.py``,
``commands.py``/``events.py``, ``scenario.py``/``rng.py``) into the
conceptual contract from `_specs/technical-spec.md` §4.1:

```python
state = engine.new_game(map_data, scenario, players, seed)
state, events = engine.step(state, commands)
```

Scope (locked by the M1 milestone): this module owns deterministic tick
advancement, deterministic command validation/ordering, and deterministic
structural event emission. It intentionally does **not** implement any
concrete gameplay system (movement, combat, economy, construction, ...) —
none exist yet. Applying an accepted command in M1 is therefore a
pass-through: nothing on ``GameState`` exists for a command to mutate, so
"applying" a batch of commands has no observable effect beyond the single
authoritative tick advance every ``step`` call performs. The *contract*
(validate -> order -> apply -> advance tick -> emit ordered events) is real
and tested, not a stub, so later milestones can layer concrete gameplay
commands on top without changing this shape.

RNG ownership: ``GameState.seed`` (see ``state.py``) is the immutable seed a
match was created with. ``new_game`` records it; ``step`` does not draw any
random numbers in this milestone because no gameplay system consumes
randomness yet (`_specs/technical-spec.md` §5.3 requires a match-local seeded
RNG "if randomness is required" — none is required in M1). A future
milestone that needs randomness inside ``step`` should construct
``rng.MatchRandom(state.seed)`` fresh from the state it is given, not thread
a long-lived mutable RNG object through ``GameState``.

Wall-clock convention: nothing in this module reads wall-clock time (no
``time.time()``, no ``datetime.now()``); every notion of progress is the
authoritative integer tick counter (see ``clock.py``).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from dataclasses import dataclass

from nether_earth.collision import (
    RobotFixture,
    commander_horizontal_move_allowed,
    commander_vertical_move_allowed,
)
from nether_earth.commander import Commander, CommanderMode
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
    HorizontalMoveCheck,
    VerticalMoveCheck,
    advance_all_horizontal_transitions,
    apply_commander_move,
    apply_vertical_physics,
    is_vertical_update_tick,
    set_vertical_intent,
)
from nether_earth.commands import Command, RejectionReason, validate_command_batch
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
from nether_earth.construction_session import (
    cancel_construction,
    deselect_module,
    enter_construction,
    select_module,
)
from nether_earth.docking import (
    apply_undock,
    auto_dock_with_event,
    docked_movement_allowed,
    follow_docked_robot,
)
from nether_earth.events import Event, EventSequencer, order_events
from nether_earth.heli_pad import detect_heli_pad_landing
from nether_earth.ids import PlayerId
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.movement import advance_all_robot_transitions
from nether_earth.resource_production import apply_daily_production
from nether_earth.robot_launch import launch_robot
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario, initialize_players
from nether_earth.state import GameState, create_game_state

__all__ = [
    "CommandAccepted",
    "CommandRejected",
    "new_game",
    "step",
]


@dataclass(frozen=True, slots=True)
class CommandAccepted(Event):
    """Structural event: ``command`` was accepted and applied during a ``step``.

    M1 has no concrete gameplay command types, so this event only records
    which command was accepted and by whom; later milestones' gameplay
    events layer on top of (or alongside) this minimal contract.
    """

    command: Command


@dataclass(frozen=True, slots=True)
class CommandRejected(Event):
    """Structural event: ``command`` was rejected and had no effect on state."""

    command: Command
    reason: RejectionReason


def new_game(
    map_data: BootstrapMap,
    scenario: Scenario,
    players: Iterable[PlayerId] | None = None,
    seed: int = 0,
    commanders: tuple[Commander, ...] = (),
) -> GameState:
    """Construct the deterministic tick-0 ``GameState`` for a new match.

    Parameters mirror the conceptual contract in
    `_specs/technical-spec.md` §4.1: ``map_data`` (the loaded bootstrap map,
    see ``map.py``), ``scenario`` (see ``scenario.py``), an explicit
    ``players`` set (defaults to the canonical v1 PvP pair from
    :func:`nether_earth.scenario.initialize_players` when omitted), and an
    integer ``seed`` recorded on the resulting state (see the module
    docstring for how/when it is consumed).

    ``commanders`` (added by issue #43, M3.7, additive/backward-compatible
    following the exact same optional-parameter pattern as ``seed``/
    ``players``): already-constructed :class:`~nether_earth.commander.Commander`
    objects to seed onto the resulting tick-0 state, forwarded unchanged to
    :func:`nether_earth.state.create_game_state`'s own ``commanders``
    parameter (which validates ownership/uniqueness -- see ``state.py``).
    Defaults to ``()``, reproducing every prior call site's behavior exactly.
    This is intentionally *not* commander-spawning logic (it does not derive
    a starting position from ``map_data.spawn_positions`` or similar) -- that
    remains out of scope; callers wanting spawn-derived commanders must
    construct them explicitly before calling this function.

    ``map_data`` and ``scenario`` must describe the same map: this is
    validated structurally (``map_id``/``version`` must match
    ``scenario.map_id``/``scenario.map_version``) since a mismatch would make
    "same scenario => same initial state" ambiguous. No map/world geometry is
    otherwise consumed in this milestone (M2+ scope).

    This delegates player-set derivation to
    :func:`nether_earth.scenario.initialize_players` rather than
    reimplementing it, and canonical ordering/dedup to
    :func:`nether_earth.state.create_game_state`, so the same
    ``(map_data, scenario, players, seed, commanders)`` always yields a
    canonical-equivalent ``GameState`` at ``tick == 0``.
    """
    if map_data.map_id != scenario.map_id:
        raise ValueError(
            f"map_data.map_id {map_data.map_id!r} does not match "
            f"scenario.map_id {scenario.map_id!r}"
        )
    if map_data.version != scenario.map_version:
        raise ValueError(
            f"map_data.version {map_data.version!r} does not match "
            f"scenario.map_version {scenario.map_version!r}"
        )

    resolved_players = tuple(initialize_players(scenario)) if players is None else tuple(players)
    return create_game_state(0, resolved_players, seed=seed, commanders=commanders)


def _always_allow_horizontal(
    state: GameState, mover: Commander, dest_x: int, dest_y: int
) -> bool:
    """Permissive :data:`HorizontalMoveCheck` used when ``world is None``.

    Matches ``commander_movement.py``'s own default parameter behavior
    exactly (that module's functions already default to "always allow" when
    no check callable is supplied at all); this module passes it explicitly
    rather than omitting the argument so ``step`` can share one code path
    regardless of whether ``world`` was supplied.
    """
    return True


def _always_allow_vertical(state: GameState, mover: Commander, dest_altitude: int) -> bool:
    """Permissive :data:`VerticalMoveCheck` used when ``world is None``. See
    :func:`_always_allow_horizontal`."""
    return True


def _replace_commander(state: GameState, updated: Commander) -> GameState:
    """Return ``state`` with ``updated`` replacing the commander for its player.

    Local equivalent of ``commander_movement.py``'s private
    ``_replace_commander`` helper -- that module does not export it (it is
    an internal implementation detail of its own whole-state helpers), so
    this integration module defines its own rather than reaching into
    another module's private name (see issue #42's "Do NOT do" list).
    """
    return state.with_commanders(
        tuple(
            updated if commander.player_id == updated.player_id else commander
            for commander in state.commanders
        )
    )


def step(
    state: GameState,
    commands: Iterable[Command],
    world: WorldMap | None = None,
    robots: tuple[RobotFixture, ...] = (),
) -> tuple[GameState, tuple[Event, ...]]:
    """Advance ``state`` by exactly one authoritative tick.

    Contract (see module docstring, extended by issue #42/M3.6 to wire in
    the commander subsystem built by #37-#41):

    1. Validate the incoming ``commands`` batch deterministically via
       :func:`nether_earth.commands.validate_command_batch` (this also
       applies canonical ``(player, sequence)`` ordering and rejects
       colliding commands). Exactly one :class:`CommandAccepted`/
       :class:`CommandRejected` event is emitted per input command, in that
       canonical order -- this generic contract is unchanged by #42 and
       fires for commander commands exactly like any other structurally
       valid command (see the module's commander-integration notes below).
    2. Layered on top of that generic pass-through, every *structurally
       accepted* :class:`~nether_earth.commander_movement.CommanderMoveCommand`/
       :class:`~nether_earth.commander_movement.CommanderSetVerticalIntentCommand`
       is additionally applied at the gameplay level (movement/vertical
       physics/docking/heli-pad detection), in the fixed per-tick order
       documented on the private per-step helpers below. A gameplay-level
       rejection (e.g. blocked, not free, move already in progress) simply
       produces no additional event -- the generic ``CommandAccepted``
       still fired; see ``commander_movement.py``/``docking.py``'s own
       module docstrings for why no new rejection event type is introduced.
    3. Advance ``state.tick`` by exactly one via ``state.with_tick``.
    4. Every event emitted in this ``step`` call -- structural and
       gameplay alike -- shares one :class:`~nether_earth.events.EventSequencer`,
       so the final :func:`~nether_earth.events.order_events` pass reflects
       one globally consistent per-tick ordering.

    ``world``/``robots`` are optional and default to values that reproduce
    the exact M1/M2 behavior for every existing call site that does not use
    commanders: when ``world is None``, no collision check is applied to any
    commander in ``state.commanders`` (movement functions fall back to their
    own permissive "always allow" defaults; heli-pad detection, which
    requires a real ``WorldMap``, is skipped entirely for the tick). When
    ``world`` is supplied, it (together with ``robots``) is bound via
    ``functools.partial`` into the collision-check callables
    ``commander_movement.py``'s functions expect (see ``collision.py``'s
    module docstring for this exact binding contract).

    Extended by issue #57 (M4.7) to wire in the construction/economy
    subsystem built by #52-#56 (robot build identity, stack/height
    derivation, construction economy, resource pools, construction
    sessions, robot launch):

    5. Step 7 (heli-pad landing detection) additionally, in direct and
       immediate response to a detected
       :class:`~nether_earth.heli_pad.CommanderConstructionEntryEligible`
       event, calls
       :func:`~nether_earth.construction_session.enter_construction` for
       that player (mirrors Step 5's auto-dock "detect and immediately
       apply" style). ``enter_construction`` is itself safe to call
       repeatedly -- a player who already has an active session (e.g. the
       commander lingers on the pad across multiple ticks, so landing is
       re-detected every grounded tick, not just on the transition tick)
       simply gets an ``ALREADY_IN_SESSION`` rejection with ``state`` left
       unchanged, so no separate "already entered" guard is needed here.
    6. A new Step 8 applies every structurally accepted
       :class:`~nether_earth.construction_commands.SelectModuleCommand`/
       :class:`~nether_earth.construction_commands.DeselectModuleCommand`/
       :class:`~nether_earth.construction_commands.CancelConstructionCommand`/
       :class:`~nether_earth.construction_commands.LaunchRobotCommand`, in
       canonical command order, exactly mirroring Step 1's "structural
       accept already recorded a generic ``CommandAccepted``; a gameplay-
       level rejection produces no additional event" contract.
       ``LaunchRobotCommand`` additionally requires a real ``world`` (exit
       resolution/occupancy cannot be computed without one); when
       ``world is None`` it is a gameplay no-op, matching Step 7's own
       world-gating.
    Extended by issue #60 (M5.1) with Step 2b: any robot move transition
    started through the shared movement executor (`movement.py`) and due to
    complete by ``tick`` is resolved via
    :func:`~nether_earth.movement.advance_all_robot_transitions`, in
    ``state.robots``' canonical order. This is a no-op for any state with
    no in-flight robot move, so every existing call site is unaffected;
    starting robot moves is issued by later M5 tasks' own command types
    (direct control, autonomous orders), which route through
    :func:`~nether_earth.movement.apply_robot_move` -- this step is only
    the completion half, so a started move always resolves on the tick its
    centrally configured duration elapses.

    7. A new Step 9 applies
       :func:`~nether_earth.resource_production.apply_daily_production` for
       the tick range this ``step`` call advances through
       (``state.tick`` at entry -> the new ``tick``), using that module's
       own exact-integer day-boundary detection. Like Step 7/Step 8's
       launch handling, this requires a real ``world`` (factory/war-base
       ownership) and is skipped when ``world is None``.

    Never reads wall-clock time. Same ``(state, commands, world, robots)``
    always produces an identical ``(new_state, events)`` pair.
    """
    results = validate_command_batch(commands, state)

    sequencer = EventSequencer()
    events: list[Event] = []
    for result in results:
        sequence = sequencer.next_sequence()
        if result.accepted:
            # Structural pass-through: the generic contract does not know or
            # care about gameplay-specific command types (see module
            # docstring); gameplay application happens separately below.
            events.append(CommandAccepted(sequence=sequence, command=result.command))
        else:
            assert result.reason is not None  # invariant guaranteed by CommandResult
            events.append(
                CommandRejected(sequence=sequence, command=result.command, reason=result.reason)
            )

    starting_tick = state.tick
    tick = state.tick + 1
    rules = DEFAULT_RULES

    # When world is None, fall back to commander_movement.py's own
    # permissive ("always allow") defaults by simply not supplying a check
    # callable at all -- see that module's documented default parameter
    # values and the module docstring above for why calling the real
    # collision functions with world=None would crash.
    horizontal_check: HorizontalMoveCheck
    vertical_check: VerticalMoveCheck
    if world is not None:
        horizontal_check = functools.partial(
            commander_horizontal_move_allowed, world=world, robots=robots
        )
        vertical_check = functools.partial(
            commander_vertical_move_allowed, world=world, robots=robots
        )
    else:
        horizontal_check = _always_allow_horizontal
        vertical_check = _always_allow_vertical

    # --- Step 1: apply accepted commander commands, canonical order --------
    for result in results:
        if not result.accepted:
            continue
        command = result.command
        if isinstance(command, CommanderMoveCommand):
            commander = state.commander_for(command.player)
            if commander is not None and not docked_movement_allowed(commander):
                # Independent movement is disabled while docked (#40's own
                # integration point); a gameplay no-op beyond the already-
                # emitted generic CommandAccepted.
                continue
            state, _move_result, move_event = apply_commander_move(
                command, state, tick, rules, horizontal_check, sequencer
            )
            if move_event is not None:
                events.append(move_event)
        elif isinstance(command, CommanderSetVerticalIntentCommand):
            # Intent may be set regardless of FREE/DOCKED mode (see
            # set_vertical_intent's own docstring).
            state, intent_event = set_vertical_intent(command, state, tick, sequencer)
            if intent_event is not None:
                events.append(intent_event)

    # --- Step 2: resolve horizontal transitions due to complete ------------
    state, completed_events = advance_all_horizontal_transitions(state, tick, sequencer)
    events.extend(completed_events)

    # --- Step 2b: resolve robot move transitions due to complete -----------
    state, robot_move_events = advance_all_robot_transitions(state, tick, sequencer)
    events.extend(robot_move_events)

    # --- Step 3: undock any DOCKED commander holding rise intent ------------
    for commander in state.commanders:
        if commander.mode is not CommanderMode.DOCKED or not commander.rising:
            continue
        updated, undock_event = apply_undock(
            commander, state, tick, rules, vertical_check, sequencer
        )
        state = _replace_commander(state, updated)
        if undock_event is not None:
            events.append(undock_event)

    # --- Step 4: vertical-cadence physics for every FREE commander ---------
    if is_vertical_update_tick(tick, rules):
        for commander in state.commanders:
            if commander.mode is not CommanderMode.FREE:
                # apply_vertical_physics already no-ops defensively for a
                # non-FREE commander; skip explicitly for clarity.
                continue
            updated, vertical_event = apply_vertical_physics(
                commander, state, tick, rules, vertical_check, sequencer
            )
            state = _replace_commander(state, updated)
            if vertical_event is not None:
                events.append(vertical_event)

    # --- Step 5: friendly auto-dock check for every FREE commander ---------
    for commander in state.commanders:
        if commander.mode is not CommanderMode.FREE:
            continue
        updated, dock_event = auto_dock_with_event(commander, robots, tick, rules, sequencer)
        state = _replace_commander(state, updated)
        if dock_event is not None:
            events.append(dock_event)

    # --- Step 6: docked commanders follow their robot fixture --------------
    robots_by_id = {robot.id: robot for robot in robots}
    for commander in state.commanders:
        if commander.mode is not CommanderMode.DOCKED or commander.docked_robot_id is None:
            continue
        robot = robots_by_id.get(commander.docked_robot_id)
        if robot is None:
            # Stale/removed robot fixture: leave the commander unchanged
            # rather than crash. No real robot subsystem exists until M4/M5
            # (see issue #42's PR description for this forward-compat gap).
            continue
        state = _replace_commander(state, follow_docked_robot(commander, robot, rules))

    # --- Step 7: heli-pad landing detection + auto construction entry ------
    if world is not None:
        for commander in state.commanders:
            if commander.mode is not CommanderMode.FREE:
                continue
            landing_event = detect_heli_pad_landing(state, world, commander, tick, rules, sequencer)
            if landing_event is not None:
                events.append(landing_event)
                # Detect and immediately apply, mirroring Step 5's auto-dock
                # style. See this function's docstring: enter_construction
                # rejects (state unchanged, no event) rather than erroring
                # if the player already has an active session, so a
                # commander lingering on the pad across multiple ticks
                # (re-detected every grounded tick) is handled safely
                # without a separate guard here.
                entry_result = enter_construction(state, landing_event, commander.player_id)
                if entry_result.accepted:
                    assert entry_result.state is not None
                    state = entry_result.state
                    events.append(
                        ConstructionEnteredEvent(
                            sequence=sequencer.next_sequence(),
                            player=commander.player_id,
                            war_base_id=landing_event.war_base_id,
                            tick=tick,
                        )
                    )

    # --- Step 8: apply accepted construction/economy commands --------------
    for result in results:
        if not result.accepted:
            continue
        command = result.command
        if isinstance(command, SelectModuleCommand):
            select_result = select_module(state, command.player, command.module, rules)
            if select_result.accepted:
                assert select_result.state is not None
                state = select_result.state
                events.append(
                    ModuleSelectedEvent(
                        sequence=sequencer.next_sequence(),
                        player=command.player,
                        module=command.module,
                        tick=tick,
                    )
                )
        elif isinstance(command, DeselectModuleCommand):
            deselect_result = deselect_module(state, command.player, command.module, rules)
            if deselect_result.accepted:
                assert deselect_result.state is not None
                state = deselect_result.state
                events.append(
                    ModuleDeselectedEvent(
                        sequence=sequencer.next_sequence(),
                        player=command.player,
                        module=command.module,
                        tick=tick,
                    )
                )
        elif isinstance(command, CancelConstructionCommand):
            # Only emit an event (and only touch state) on an actual
            # transition -- cancel_construction() is itself a silent no-op
            # for a player with no active session; checking first here keeps
            # that no-op from producing a spurious ConstructionCancelledEvent.
            if state.construction_session_for(command.player) is not None:
                state = cancel_construction(state, command.player)
                events.append(
                    ConstructionCancelledEvent(
                        sequence=sequencer.next_sequence(),
                        player=command.player,
                        tick=tick,
                    )
                )
        elif isinstance(command, LaunchRobotCommand):
            if world is None:
                # Launch requires resolving the owning war base's EXIT
                # interaction point and folding robot occupancy against a
                # real WorldMap (see robot_launch.py); with no world
                # supplied this tick there is nothing to resolve against.
                # Gameplay-level no-op, exactly like every other unmet-
                # precondition rejection -- no additional event, matching
                # this module's own rejection convention.
                continue
            launch_result = launch_robot(state, world, command.player, rules)
            if launch_result.accepted:
                assert launch_result.state is not None
                assert launch_result.robot is not None
                state = launch_result.state
                events.append(
                    RobotLaunchedEvent(
                        sequence=sequencer.next_sequence(),
                        player=command.player,
                        robot_id=launch_result.robot.entity_id,
                        tick=tick,
                    )
                )

    # --- Step 9: daily production boundary check ----------------------------
    if world is not None:
        state, production_events = apply_daily_production(
            state, world, starting_tick, tick, rules, sequencer
        )
        events.extend(production_events)

    new_state = state.with_tick(tick)
    return new_state, order_events(events)
