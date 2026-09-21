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

from nether_earth.autonomous_combat import consume_engagement_intents
from nether_earth.capture import (
    CapturableStructureKind,
    NeutralStructureAcquiredEvent,
    StructureCapturedEvent,
    advance_capture,
)
from nether_earth.collision import (
    RobotFixture,
    commander_horizontal_move_allowed,
    commander_vertical_move_allowed,
)
from nether_earth.combat import (
    FireCommand,
    FireRequest,
    ProjectileTerminatedEvent,
    advance_projectiles,
    apply_damage,
    apply_fire,
    validate_fire,
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
    deselect_module,
    enter_construction,
    exit_construction,
    select_module,
)

# Aliased deliberately: `capture.py` also exports a function called
# ``effective_world`` (ownership overrides only). This module must read
# structure existence through `destruction.py`'s composed one (ownership
# overrides AND destruction), per that function's own documented hand-off
# note, and an unaliased import of both would silently shadow one with the
# other. `capture.effective_world` is no longer imported here at all --
# every one of this module's own call sites now resolves through the
# composed function below, so there is nothing left to shadow.
from nether_earth.destruction import effective_world as destruction_effective_world
from nether_earth.destruction import (
    evaluate_victory_after_nuclear_detonation,
    execute_nuclear_detonation,
)
from nether_earth.direct_control import DirectRobotMoveCommand, direct_robot_move_request
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
from nether_earth.movement import RobotMoveRequest, advance_all_robot_transitions
from nether_earth.orders import (
    OrderEvaluation,
    SetRobotOrderCommand,
    apply_order_evaluations,
    apply_set_robot_order,
    evaluate_orders,
)
from nether_earth.reservations import apply_robot_move_batch
from nether_earth.resource_production import apply_daily_production
from nether_earth.robot_build import ModuleIdentity
from nether_earth.robot_launch import launch_robot
from nether_earth.rules import DEFAULT_RULES
from nether_earth.scenario import Scenario, initialize_players
from nether_earth.state import GameState, create_game_state
from nether_earth.victory import evaluate_victory

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


def _robot_fixtures(
    state: GameState, fixtures: tuple[RobotFixture, ...]
) -> tuple[RobotFixture, ...]:
    """Return ``fixtures`` plus one :class:`RobotFixture` per live robot in ``state``.

    ``Robot`` already carries the ground-rooted ``height`` the collision and
    docking math needs (see ``collision.robot_vertical_range``); a fixture is
    just its ``(id, owner, x, y, height)`` projection, taken from the
    authoritative cell (a robot mid-move still stands on its origin cell
    until the move completes -- `movement.py`). Caller-supplied fixtures
    come first so the M3 tests' explicit surfaces keep their precedence.
    """
    return fixtures + tuple(
        RobotFixture(id=robot.entity_id, owner=robot.owner, x=robot.x, y=robot.y, height=robot.height)
        for robot in state.robots
    )


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
    robot_moves: Iterable[RobotMoveRequest] = (),
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

    Extended by issue #62 (M5.3) with Step 2c: ``robot_moves`` -- this
    tick's :class:`~nether_earth.movement.RobotMoveRequest`\\ s -- are
    started as one deconflicted *batch* via
    :func:`~nether_earth.reservations.apply_robot_move_batch`, never one at
    a time, so that same-tick claims on one destination cell are collected
    before a winner is drawn from the match-local seeded RNG
    (`_specs/open-questions.md` §11; resolving them as they arrived would
    make submission order decide, which §11 forbids). The batch emits a
    :class:`~nether_earth.reservations.DestinationContentionResolvedEvent`
    per contested cell plus the winners'
    :class:`~nether_earth.movement.RobotMoveStartedEvent`\\ s; losers are
    reported only through the returned per-request results (not events),
    matching this module's existing "a gameplay-level rejection produces no
    additional event" convention. Like Step 8's launch handling this needs
    a real ``world`` and is skipped when ``world is None``. ``robot_moves``
    is a parameter (additive, defaulting to ``()``) for any caller that
    already has its own :class:`~nether_earth.movement.RobotMoveRequest`\\ s
    to submit directly.

    Extended by issue #63 (M5.4) with direct-control robot movement:
    structurally accepted
    :class:`~nether_earth.direct_control.DirectRobotMoveCommand`\\ s are,
    immediately before Step 2c's batch call, resolved via
    :func:`~nether_earth.direct_control.direct_robot_move_request` against
    the docked-commander interaction gate that module documents (a command
    from a player whose commander is not currently ``DOCKED`` to a robot
    yields no request at all -- zero side effects, no additional event,
    matching every other gameplay-level rejection in this function) and the
    resulting :class:`~nether_earth.movement.RobotMoveRequest`\\ s are
    appended to ``robot_moves`` before the single combined iterable is
    handed to :func:`~nether_earth.reservations.apply_robot_move_batch`.
    Direct control therefore always goes through the exact same batched
    legality/execution/contention path as any other robot move request --
    it cannot bypass terrain, occupancy, commander-blocking, or reservation
    rules, and it fully participates in the same tick's contention
    resolution as autonomous moves. Leaving direct control reuses the
    existing Step 3 undock transition below (no separate handling needed):
    once a docked commander undocks, its former robot is no longer that
    player's direct-control target, and a further
    ``DirectRobotMoveCommand`` from that player is rejected at the gate on
    the very next tick.

    7. A new Step 9 applies
       :func:`~nether_earth.resource_production.apply_daily_production` for
       the tick range this ``step`` call advances through
       (``state.tick`` at entry -> the new ``tick``), using that module's
       own exact-integer day-boundary detection. Like Step 7/Step 8's
       launch handling, this requires a real ``world`` (factory/war-base
       ownership) and is skipped when ``world is None``.

    Extended by issue #66 (M5.7) with Step 2d and the war-base-capture
    victory hook:

    8. A new Step 2d, immediately after Step 2c starts this tick's robot
       moves (capture reads robots' *post*-move authoritative positions,
       matching `capture.py`'s own documented ordering requirement -- a
       move *starting* this tick does not itself change a robot's
       authoritative position, so Step 2d's placement relative to Step 2c
       has no observable effect on capture outcomes), applies
       :func:`~nether_earth.capture.advance_capture`: neutral factory
       instant acquisition, and enemy factory/war-base continuous-
       occupation progress/interruption/completion. This is a no-op for
       any state with no capturable structures/qualifying robots, so every
       existing call site is unaffected. Skipped entirely when
       ``world is None`` (there is no structure/interaction-point data to
       evaluate capture against).
    9. Whenever Step 2c completes at least one war-base capture/neutral
       acquisition (a :class:`~nether_earth.capture.StructureCapturedEvent`
       or :class:`~nether_earth.capture.NeutralStructureAcquiredEvent` with
       ``structure_kind is CapturableStructureKind.WAR_BASE``),
       :func:`~nether_earth.victory.evaluate_victory` is invoked in this
       same step (`_specs/technical-spec.md` §6/§10's "victory is evaluated
       in the same authoritative simulation step" requirement), against the
       post-capture effective world. A resulting
       :class:`~nether_earth.victory.VictoryEvent` is appended like any
       other event; this module does not otherwise react to it (see
       `victory.py`'s module docstring for why -- match
       finalization/session lifecycle is the match/session layer's job).
    10. Steps 7-9 above (heli-pad/construction entry, launch, daily
        production) are evaluated against
        :func:`~nether_earth.destruction.effective_world` (M6.10; this was
        :func:`~nether_earth.capture.effective_world` through M5) -- ``world``
        with every runtime capture ownership override (including any that
        completed earlier in this same tick's Step 2c) layered on top, and
        every destroyed structure filtered out -- rather than the raw
        ``world`` argument, so a structure's new owner
        is immediately visible to every other ownership-aware subsystem in
        the same tick it changes hands, matching "ownership transfers
        immediately on completion".

    Extended by issue #64 (M5.5) with Step 2b2, the autonomous-order pass:

    11. Structurally accepted
        :class:`~nether_earth.orders.SetRobotOrderCommand`\\ s are applied
        via :func:`~nether_earth.orders.apply_set_robot_order` (a command
        naming a robot the issuing player does not own is a gameplay no-op,
        matching every other gameplay-level rejection here), and then every
        ordered robot's standing order is evaluated for the tick by
        :func:`~nether_earth.orders.evaluate_orders`. Order lifecycle
        changes are written back and their
        :class:`~nether_earth.orders.RobotOrderChangedEvent`/
        :class:`~nether_earth.orders.RobotEngagementIntentEvent` are
        appended by :func:`~nether_earth.orders.apply_order_evaluations`.
        Like Steps 2c/2d this needs a real ``world`` and is skipped when
        ``world is None``.
    12. Step 2b2 sits deliberately **before** Step 2c, not after Step 2d:
        the movement requests orders produce are appended to Step 2c's
        single ``apply_robot_move_batch`` call rather than executed in a
        second batch of their own. `_specs/open-questions.md` §11 requires
        same-tick claims on one cell to be collected before a seeded winner
        is drawn; evaluating orders in a separate later batch would instead
        let direct-control moves win every contested cell purely because
        their phase ran first, making submission order decide -- exactly
        what §11 forbids. Autonomous and direct-control moves therefore
        contend as equals in one batch.
    13. Order evaluation reads
        :func:`~nether_earth.destruction.effective_world` (M6.10; this was
        :func:`~nether_earth.capture.effective_world` through M5) --
        ownership as it stands at the start of the tick, including every
        previously completed capture, with every already-destroyed structure
        filtered out -- so a Search & Capture/Search & Destroy order
        re-resolves "neutral"/"enemy" against live ownership and never
        targets a structure that no longer exists. A capture completing
        later in this same tick's Step 2d is reflected on the next tick's
        evaluation, which is the same one-tick visibility every other
        pre-capture step in this function has.

    Extended by M6.10 with Step 2c2, the combat pass -- the single point at
    which every Milestone 6 combat function (built as pure, directly
    testable functions by M6.2/6.4/6.6/6.7/6.8/6.9, deliberately leaving
    this module untouched) is wired in:

    14. Step 2c2 sits immediately after Step 2c (this tick's robot moves
        start) and before Step 2d (capture), and runs three sub-phases in a
        fixed order:

        a. :func:`~nether_earth.combat.advance_projectiles` advances every
           in-flight projectile on its cadence tick, and each resulting
           :class:`~nether_earth.combat.ProjectileTerminatedEvent` carrying
           a ``hit_robot_id`` is immediately fed to
           :func:`~nether_earth.combat.apply_damage` (which itself routes a
           lethal hit to `destruction.py`). Advancement precedes firing so
           a projectile fired this tick is not advanced a second time on
           the tick it was created: its first move is made by
           :func:`~nether_earth.combat.apply_fire` itself on the fire tick
           (CR002.2 #169), including damage for a hit on that move.
        b. Every structurally accepted
           :class:`~nether_earth.combat.FireCommand` is applied, in
           canonical command order. A normal weapon routes through
           :func:`~nether_earth.combat.apply_fire`; nuclear clears
           :func:`~nether_earth.combat.validate_fire`'s accept boundary and
           then detonates via
           :func:`~nether_earth.destruction.execute_nuclear_detonation`,
           followed by
           :func:`~nether_earth.destruction.evaluate_victory_after_nuclear_detonation`
           in the same step (the same "victory is evaluated in the same
           authoritative simulation step" requirement Step 2d already
           satisfies for capture).
        c. Every engagement intent Step 2b2's ``evaluations`` produced is
           consumed by
           :func:`~nether_earth.autonomous_combat.consume_engagement_intents`,
           which re-validates and fires through the exact same
           ``FireRequest``/``validate_fire`` boundary direct fire uses --
           autonomous and direct fire are provably one code path. An
           autonomous nuclear shot gets the same victory check phase (b)
           applies to a direct one.

        Firing after movement means a robot that moved this tick fires from
        its authoritative (pre-move-completion) cell, exactly as capture
        reads authoritative positions in Step 2d -- a move *starting* this
        tick does not change a robot's position, so this ordering has no
        observable effect beyond being fixed and documented.
    15. Every one of this module's own world resolutions now reads
        `destruction.py`'s composed
        :func:`~nether_earth.destruction.effective_world` (capture ownership
        overrides *and* destruction) rather than `capture.py`'s
        ownership-only one, per that function's documented hand-off: a
        destroyed war base/factory must immediately stop being capturable,
        production-eligible, a valid heli-pad/launch target, or an order's
        Search & Capture/Search & Destroy target. This includes the world
        handed *into* :func:`~nether_earth.capture.advance_capture`, which
        therefore stays destruction-unaware itself.
    16. **At most one** :class:`~nether_earth.victory.VictoryEvent` is
        appended per ``step`` call, per issue #79's locked criteria
        ("repeated/redundant evaluation does not emit duplicate
        match-result events"). Three sites can each independently find the
        same condition in one tick -- Step 2c2's per-``FireCommand``
        nuclear branch (once per nuclear command in the batch), Step 2c2's
        autonomous-engagement branch, and Step 2d's capture-triggered check
        -- so a single ``victory_emitted`` flag, declared once at the top of
        this function, guards all three appends rather than each site
        reasoning about the others.

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

    # At most ONE VictoryEvent may be appended per `step` call, per issue
    # #79's locked acceptance criteria ("repeated/redundant evaluation does
    # not emit duplicate match-result events"; "match result is emitted once
    # and cannot oscillate/reopen"). Three independent sites below can each
    # find the same victory condition in one tick -- the per-`FireCommand`
    # nuclear branch (once per nuclear command in the batch), the autonomous
    # engagement branch, and Step 2d's capture-triggered check -- so the
    # single authoritative guard lives here rather than in any one of them.
    # The evaluation functions themselves are pure, so a later site may
    # still safely be CALLED once this flag is set; only the append is
    # suppressed, which keeps each site's own local reasoning unchanged.
    victory_emitted = False

    # When world is None, fall back to commander_movement.py's own
    # permissive ("always allow") defaults by simply not supplying a check
    # callable at all -- see that module's documented default parameter
    # values and the module docstring above for why calling the real
    # collision functions with world=None would crash.
    horizontal_check: HorizontalMoveCheck
    vertical_check: VerticalMoveCheck
    # Real robots (M4+) are physical surfaces for the commander exactly like
    # the M3 ``robots`` fixtures: fold ``state.robots`` in here for collision
    # and again below (post-move positions) for auto-dock/follow. Found by
    # the M9.1 audit: without this a live commander flew through robots and
    # could never dock on one, so direct control was unreachable in a match.
    fixture_robots = robots
    robots = _robot_fixtures(state, fixture_robots)
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
            if state.construction_session_for(command.player) is not None:
                # The construction screen is modal (CR002.13): the Spectrum
                # construction loop reads only menu input until EXIT MENU or
                # START ROBOT, so the commander cannot fly off the pad with
                # the screen still open. Same no-op convention as above.
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

    # --- Step 2b2: apply order assignments, then evaluate standing orders ---
    # Runs *before* Step 2c so every autonomous move request joins the exact
    # same deconflicted batch as this tick's direct-control requests -- see
    # the docstring's issue #64 notes for why a second batch would break
    # `_specs/open-questions.md` §11.
    order_requests: list[RobotMoveRequest] = []
    order_lifecycle_events: tuple[Event, ...] = ()
    # Bound before the guard so Step 2c2 (which consumes this tick's
    # engagement intents) can read it unconditionally without recomputing
    # `evaluate_orders` a second time -- `world is None` simply means no
    # orders were evaluated at all this tick, hence no intents to consume.
    evaluations: tuple[OrderEvaluation, ...] = ()
    if world is not None:
        for result in results:
            if not result.accepted:
                continue
            command = result.command
            if isinstance(command, SetRobotOrderCommand):
                state, order_changed = apply_set_robot_order(command, state, tick, sequencer)
                if order_changed is not None:
                    events.append(order_changed)

        evaluations = evaluate_orders(state, destruction_effective_world(world, state), rules)
        state, order_lifecycle_events = apply_order_evaluations(
            evaluations, state, tick, sequencer
        )
        order_requests = [
            evaluation.request for evaluation in evaluations if evaluation.request is not None
        ]
        events.extend(order_lifecycle_events)

    # --- Step 2c: start this tick's robot moves as one deconflicted batch ---
    # Deliberately after Step 2b: a move completing on this tick releases its
    # destination reservation (the transition is cleared) before this tick's
    # new claims are validated, so a robot may claim the cell a finishing
    # move is vacating without waiting an extra tick.
    if world is not None:
        direct_move_requests = []
        for result in results:
            if not result.accepted:
                continue
            command = result.command
            if isinstance(command, DirectRobotMoveCommand):
                _direct_result, direct_request = direct_robot_move_request(command, state)
                if direct_request is not None:
                    direct_move_requests.append(direct_request)
        batch = apply_robot_move_batch(
            (*robot_moves, *order_requests, *direct_move_requests),
            state,
            world,
            tick,
            rules,
            sequencer,
        )
        state = batch.state
        events.extend(batch.contentions)
        events.extend(batch.started)

    # --- Step 2c2: combat --------------------------------------------------
    # Projectile advancement + damage, direct fire, autonomous engagement,
    # nuclear detonation, and the nuclear victory check -- the single place
    # every M6 combat function is wired in (see the docstring's M6.10 notes
    # for why all of it lives in one step rather than four).
    #
    # `world_for_step` is NOT in scope here: it is first bound by Step 2d
    # below, which runs after this step. Every sub-phase therefore resolves
    # its own `destruction_effective_world(world, state)` from the LATEST
    # state at the moment it needs one. That is not merely defensive: a
    # nuclear detonation in phase (b) destroys structures, and phase (c)'s
    # autonomous target re-validation must not then read a world that still
    # contains them. It is a cheap pure recomputation over already-small
    # tuples; correctness before caching.
    if world is not None:
        # (a) Advance in-flight projectiles, then apply damage for every hit,
        # in the exact tuple order `advance_projectiles` returns its events
        # (already canonical per M6.4), so simultaneous hits resolve stably.
        combat_world = destruction_effective_world(world, state)
        state, projectile_events = advance_projectiles(
            state, combat_world, tick, rules, sequencer
        )
        events.extend(projectile_events)
        for event in projectile_events:
            if isinstance(event, ProjectileTerminatedEvent) and event.hit_robot_id is not None:
                state, damage_events = apply_damage(
                    state, combat_world, event.hit_robot_id, event.weapon, rules, tick, sequencer
                )
                events.extend(damage_events)

        # (b) Direct-control fire. Ownership is enforced inside
        # `validate_fire` (`request.player == robot.owner`), not here -- see
        # `combat.FireCommand`'s docstring. A gameplay-level rejection
        # produces no additional event, matching this module's convention.
        for result in results:
            if not result.accepted:
                continue
            command = result.command
            if not isinstance(command, FireCommand):
                continue
            fire_world = destruction_effective_world(world, state)
            request = FireRequest(
                robot_id=command.entity_id,
                player=command.player,
                weapon=command.weapon,
                target_x=command.target_x,
                target_y=command.target_y,
            )
            if command.weapon is ModuleIdentity.NUCLEAR:
                # Nuclear creates no projectile: it clears `validate_fire`'s
                # accept boundary and detonates immediately (see
                # `combat.apply_fire`, which deliberately passes nuclear
                # through untouched).
                if not validate_fire(request, state).accepted:
                    continue
                state, detonation_events = execute_nuclear_detonation(
                    state, fire_world, command.entity_id, tick, rules, sequencer
                )
                events.extend(detonation_events)
                victory_event = evaluate_victory_after_nuclear_detonation(
                    detonation_events, fire_world, state, tick, sequencer
                )
                if victory_event is not None and not victory_emitted:
                    events.append(victory_event)
                    victory_emitted = True
            else:
                state, _fire_result, fire_events = apply_fire(
                    request, state, fire_world, tick, rules, sequencer
                )
                events.extend(fire_events)

        # (c) Autonomous engagement: consume this tick's intents from the
        # SAME `evaluations` Step 2b2 already computed (never recomputed --
        # a second `evaluate_orders` call could disagree with the first now
        # that phases (a)/(b) have changed state).
        engagement_world = destruction_effective_world(world, state)
        state, engagement_events = consume_engagement_intents(
            evaluations, state, engagement_world, tick, rules, sequencer
        )
        events.extend(engagement_events)

        # `consume_engagement_intents` routes a nuclear intent straight to
        # `execute_nuclear_detonation` but deliberately does NOT run the
        # victory check itself (it owns the intent->fire bridge, not victory
        # evaluation), so the autonomous nuclear path gets exactly the same
        # treatment phase (b) gives the direct-fire one. The check is
        # internally gated on a war-base `StructureDestroyedEvent` and calls
        # `evaluate_victory` at most once, so passing the whole tuple is
        # correct even when several structures were destroyed.
        engagement_victory = evaluate_victory_after_nuclear_detonation(
            engagement_events,
            destruction_effective_world(world, state),
            state,
            tick,
            sequencer,
        )
        if engagement_victory is not None and not victory_emitted:
            events.append(engagement_victory)
            victory_emitted = True

    # --- Step 2d: neutral acquisition / enemy capture progress -------------
    # After Step 2c: capture reads robots' authoritative positions, which a
    # move *starting* this tick does not change (only a move *completing*,
    # already resolved in Step 2b, does) -- so this step's relative order
    # against Step 2c has no observable effect on capture outcomes, but it
    # must stay after Step 2b for the "post-move position" ordering capture.py
    # documents.
    world_for_step: WorldMap | None = world
    if world is not None:
        # Capture sees a destruction-filtered world: a war base/factory that
        # Step 2c2 just nuked must stop being capturable the instant it is
        # destroyed. `capture.py` itself stays destruction-unaware (it owns
        # ownership only) precisely because it is handed an already-filtered
        # world here.
        state, capture_events = advance_capture(
            state, destruction_effective_world(world, state), tick, rules, sequencer
        )
        events.extend(capture_events)

        war_base_ownership_changed = any(
            isinstance(event, (NeutralStructureAcquiredEvent, StructureCapturedEvent))
            and event.structure_kind is CapturableStructureKind.WAR_BASE
            for event in capture_events
        )

        # Every subsequent world-dependent step this tick sees ownership as
        # it stands *after* Step 2d -- including a completion that just
        # happened this very tick -- per the module docstring's "ownership
        # transfers immediately on completion" requirement. Recomputed here
        # from the LATEST state, so it also reflects every structure Step
        # 2c2's combat destroyed earlier in this same tick.
        world_for_step = destruction_effective_world(world, state)

        if war_base_ownership_changed:
            capture_victory = evaluate_victory(world_for_step, state, tick, sequencer)
            # Guarded by the same one-per-tick flag as Step 2c2's two nuclear
            # victory sites: a nuke removing the loser's last war base in
            # Step 2c2 and a war-base acquisition completing here can both
            # find the condition in a single tick.
            if capture_victory is not None and not victory_emitted:
                events.append(capture_victory)
                victory_emitted = True

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
            if state.construction_session_for(commander.player_id) is not None:
                # Modal construction screen (CR002.13, see Step 1): the
                # commander stays on the pad, whatever its rise intent, until
                # the player leaves via EXIT MENU or START ROBOT.
                continue
            updated, vertical_event = apply_vertical_physics(
                commander, state, tick, rules, vertical_check, sequencer
            )
            state = _replace_commander(state, updated)
            if vertical_event is not None:
                events.append(vertical_event)

    # --- Step 5: friendly auto-dock check for every FREE commander ---------
    robots = _robot_fixtures(state, fixture_robots)
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
    if world_for_step is not None:
        for commander in state.commanders:
            if commander.mode is not CommanderMode.FREE:
                continue
            if commander.elevate_updates_remaining > 0:
                # Just left the construction screen (CR002.12/CR002.13): the
                # exit ascent lifts the commander off the pad before the next
                # landing check can match (Spectrum: the elevate timer raises
                # the ship above altitude 15 before `cp 15` runs again).
                continue
            landing_event = detect_heli_pad_landing(
                state, world_for_step, commander, tick, rules, sequencer
            )
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
            # transition -- exit_construction() is itself a silent no-op
            # for a player with no active session; checking first here keeps
            # that no-op from producing a spurious ConstructionCancelledEvent.
            if state.construction_session_for(command.player) is not None:
                # EXIT MENU (CR002.13): discard the build and lift off the pad.
                state = exit_construction(state, command.player, rules)
                events.append(
                    ConstructionCancelledEvent(
                        sequence=sequencer.next_sequence(),
                        player=command.player,
                        tick=tick,
                    )
                )
        elif isinstance(command, LaunchRobotCommand):
            if world_for_step is None:
                # Launch requires resolving the owning war base's EXIT
                # interaction point and folding robot occupancy against a
                # real WorldMap (see robot_launch.py); with no world
                # supplied this tick there is nothing to resolve against.
                # Gameplay-level no-op, exactly like every other unmet-
                # precondition rejection -- no additional event, matching
                # this module's own rejection convention.
                continue
            launch_result = launch_robot(state, world_for_step, command.player, rules)
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
    if world_for_step is not None:
        state, production_events = apply_daily_production(
            state, world_for_step, starting_tick, tick, rules, sequencer
        )
        events.extend(production_events)

    new_state = state.with_tick(tick)
    return new_state, order_events(events)
