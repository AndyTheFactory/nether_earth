"""Factory and war-base capture subsystem (issue #66, M5.7).

Per `_specs/milestones/05-orders-navigation-capture.md` ("Task 6: Factory
and war-base capture subsystem") and `_specs/open-questions.md` §§6-7
(RESOLVED), this module implements:

- **neutral factory acquisition**: a neutral factory (``owner is None``)
  becomes owned by the first qualifying robot instantly -- no
  continuous-occupation timer (`_specs/functional-spec.md` §9: "Neutral
  factories become owned by the first qualifying robot under the verified
  original behavior."). This is a *factory-only* exception;
- **enemy factory/war-base capture, and neutral war-base capture**:
  continuous qualifying occupation of the structure's canonical capture
  interaction location for ``rules.capture_duration_ticks`` (default
  ``1440``) authoritative ticks; any interruption resets progress to zero
  immediately, with no partial-credit resume (`_specs/open-questions.md`
  §7). Per `_specs/open-questions.md` §6 ("War-base capture uses the same
  continuous-occupation rule as factory capture by default"), a **neutral**
  war base is not an instant-acquisition case the way a neutral factory
  is -- it goes through the identical continuous-occupation countdown as
  an enemy-owned war base, just starting from ``owner is None`` instead of
  an opposing player;
- deterministic ownership transfer, exactly at the configured duration
  boundary, plus a deterministic capture event.

This module follows `docking.py`/`heli_pad.py`'s established "continuous
occupation of a canonical M2 interaction point drives a stateful process"
architectural shape, not a new one: qualifying occupation is checked purely
from already-authoritative positions (``Robot.x``/``Robot.y``, ``WorldMap``
interaction-point footprints), the same way `heli_pad.py` checks a
commander's position against a heli-pad footprint. Capture state
(:class:`CaptureProgress`) is a ``GameState``-attached record, following
`construction_session.py`'s "session/progress lives on ``GameState``,
mirrored per contested structure_id" convention rather than inventing a
third state-attachment style.

Why capture uses canonical interaction points, never inferred footprints
------------------------------------------------------------------------
`interactions.py` (M2) already exposes ``FACTORY_CAPTURE`` (required, one
per factory) and ``WARBASE_CAPTURE`` (optional, zero-or-more per war base)
interaction points precisely so later systems consume this one shared
representation instead of re-deriving "near the structure" from its
physical ``components``. A structure with zero declared capture points for
its kind (only possible for a war base, since ``WARBASE_CAPTURE`` is
optional -- see `interactions.py`) is simply not capturable on that map;
this is a valid map state, not an error, mirroring
``heli_pad.detect_heli_pad_landing``'s identical "zero declared points is a
valid, unmatched map state" precedent.

Runtime ownership: ``GameState``, not a mutated ``WorldMap``
--------------------------------------------------------------
``WorldMap.war_bases``/``WorldMap.factories`` carry each structure's
*starting* owner (scenario overlay data, applied once at match setup via
``map_overlay.apply_overlay`` -- see that module). ``WorldMap`` is a plain,
externally-held, never-mutated-in-place value that engine callers pass into
every ``engine.step`` call by reference (see `engine.py`'s own module
docstring); ``engine.step`` does not return an updated ``WorldMap``, so a
structure's *current* owner after any capture completes cannot live on
``WorldMap`` alone without changing that established API shape mid-milestone.
Runtime ownership changes are therefore recorded as
:class:`StructureOwnership` overrides on ``GameState`` -- a tuple in
canonical (sorted by ``structure_id.value``) order, mirroring every other
``GameState``-attached collection in this codebase -- and
:func:`effective_owner`/:func:`effective_world` resolve "the structure's
owner right now" by layering those overrides over the base ``WorldMap`` via
the exact same :func:`~nether_earth.map_overlay.apply_overlay` mechanism
`map_overlay.py` already uses for scenario-time ownership -- reused, not
reimplemented, per that module's own "layering...without mutating the
source map definition" contract. ``engine.py`` calls :func:`effective_world`
once per tick and threads the result into every world-dependent subsystem
that reads structure ownership (heli-pad/construction entry, daily
production, capture itself), so a captured structure's new owner is visible
everywhere in the very same tick it completes, matching
`_specs/functional-spec.md`'s "ownership transfers immediately on
completion".

Qualifying robot selection (this module's own documented judgment call)
--------------------------------------------------------------------------
The locked rules describe continuous occupation by "a qualifying robot" in
the singular, and `_specs/technical-spec.md` §10's recommended
``CaptureProgress`` shape carries exactly one ``robot_id``, but no locked
rule specifies a tie-break when a capture footprint spans multiple cells
and more than one qualifying robot occupies it in the same tick (only
possible on a multi-cell ``WARBASE_CAPTURE``/``FACTORY_CAPTURE`` footprint,
since ordinary ground occupancy already forbids two robots sharing one
cell). :func:`_qualifying_robot` picks the qualifying robot with the
lexicographically smallest ``entity_id.value`` deterministically -- this
mirrors the same "stable id order breaks otherwise-unspecified ties"
convention `map_overlay.default_pvp_overlay` already documents for its own
extreme-war-base tie-break. If the tie-break's answer changes from one tick
to the next (a different robot becomes the smallest-id qualifier), that is
treated as an interruption of the *previous* qualifier's continuous
occupation -- progress resets, per §7 -- rather than silently accruing
credit across different occupying robots, since `_specs/technical-spec.md`
§10's ``CaptureProgress.robot_id`` field ties progress to one specific
robot's occupation, not to "some qualifying robot of this player".

Tick-counting convention
--------------------------
:class:`CaptureProgress.elapsed_ticks` counts the qualifying occupation tick
it is set on as elapsed ``1`` (not ``0``): the first tick a qualifying
robot is detected creates a progress record with ``elapsed_ticks == 1``,
and each subsequent still-qualifying tick increments it by one. Completion
triggers when ``elapsed_ticks >= rules.capture_duration_ticks`` -- so with
the default ``1440``, ownership changes exactly on the 1440th continuously-
qualifying tick, not the 1441st, satisfying the "exactly at the configured
duration boundary, not off-by-one" acceptance criterion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.map_overlay import ScenarioOverlay, apply_overlay
from nether_earth.robot import Robot
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState
from nether_earth.structures import Factory, WarBase

__all__ = [
    "CapturableStructureKind",
    "CaptureProgress",
    "NeutralStructureAcquiredEvent",
    "StructureCapturedEvent",
    "StructureOwnership",
    "advance_capture",
    "capture_footprint",
    "effective_owner",
    "effective_world",
]


class CapturableStructureKind(str, Enum):
    """Which structure kind a capture event/progress record concerns.

    Distinct from :class:`~nether_earth.structures.FactoryType` (which
    names a factory's *production* category, not its structural kind) --
    callers (e.g. `engine.py`'s war-base-capture-triggers-victory-check
    hook) need to know "was this a war base?" without importing
    ``interactions.py``'s ``InteractionKind`` just to test event payloads.
    """

    FACTORY = "factory"
    WAR_BASE = "war_base"


@dataclass(frozen=True, slots=True)
class StructureOwnership:
    """A runtime ownership override for one structure, layered over ``WorldMap``.

    See the module docstring for why this lives on ``GameState`` rather
    than a mutated ``WorldMap``. ``structure_id`` must name a real war base
    or factory id (validated by :func:`effective_world` via
    ``map_overlay.apply_overlay``, not re-validated here -- this is a
    plain value type, like every other ``GameState``-attached record in
    this codebase).
    """

    structure_id: EntityId
    owner: PlayerId


@dataclass(frozen=True, slots=True)
class CaptureProgress:
    """An in-progress continuous-occupation capture attempt for one structure.

    Mirrors `_specs/technical-spec.md` §10's recommended ``CaptureProgress``
    shape (``structure_id``, ``capturing_player_id``, ``robot_id``,
    ``elapsed_ticks``, ``required_ticks``). Never stored on ``GameState``
    for a *completed* attempt -- see :func:`advance_capture`, which removes
    the record in the same tick ownership transfers. ``required_ticks`` is
    captured on the record (rather than re-read from ``EngineRules`` on
    every tick) so a snapshot/replay of an in-progress capture is
    self-describing without also carrying the full rule set (Task 8, M5.8,
    owns actually wiring this into `snapshot.py`).
    """

    structure_id: EntityId
    capturing_player: PlayerId
    robot_id: EntityId
    elapsed_ticks: int
    required_ticks: int

    def __post_init__(self) -> None:
        if self.required_ticks <= 0:
            raise ValueError("required_ticks must be a positive integer")
        if self.elapsed_ticks < 1:
            raise ValueError("elapsed_ticks must be at least 1 for a recorded attempt")
        if self.elapsed_ticks >= self.required_ticks:
            raise ValueError(
                "a CaptureProgress record must not represent a completed attempt "
                "(elapsed_ticks must be strictly less than required_ticks); "
                "advance_capture removes the record on completion instead"
            )


@dataclass(frozen=True, slots=True)
class NeutralStructureAcquiredEvent(Event):
    """A neutral factory was instantly acquired by the first qualifying robot.

    Per `_specs/functional-spec.md` §9, neutral acquisition has no
    continuous-occupation duration -- this event fires the same tick
    ``robot_id`` is first detected qualifying. ``structure_kind`` is always
    :attr:`CapturableStructureKind.FACTORY` in v1 (war bases start owned or
    neutral-but-continuous-capture per `_specs/open-questions.md` §2/§6;
    only factories use the instant-neutral-acquisition rule), but the field
    is carried explicitly (rather than assumed) so a consumer never needs a
    second event type if a future map ever declares a neutral, instantly-
    acquirable war base.
    """

    structure_id: EntityId
    structure_kind: CapturableStructureKind
    new_owner: PlayerId
    robot_id: EntityId
    tick: int


@dataclass(frozen=True, slots=True)
class StructureCapturedEvent(Event):
    """A structure finished continuous-occupation capture.

    Emitted exactly once, on the tick ``elapsed_ticks`` reaches
    ``required_ticks`` (see the module docstring's tick-counting
    convention), for: an enemy-owned factory, an enemy-owned war base, or a
    **neutral** war base (per `_specs/open-questions.md` §6, a neutral war
    base uses the same continuous-occupation rule as an enemy-owned one --
    unlike a neutral factory, which instead gets
    :class:`NeutralStructureAcquiredEvent`). ``previous_owner`` is ``None``
    for that neutral-war-base case, and the real previous owner otherwise.
    ``structure_kind`` lets `engine.py` decide whether to invoke the
    war-base-capture victory-evaluation hook without importing
    ``interactions.py`` itself.
    """

    structure_id: EntityId
    structure_kind: CapturableStructureKind
    previous_owner: PlayerId | None
    new_owner: PlayerId
    robot_id: EntityId
    tick: int


def effective_owner(
    world: WorldMap, state: GameState, structure: WarBase | Factory
) -> PlayerId | None:
    """Return ``structure``'s current owner: a runtime override, else its static ``WorldMap`` owner.

    ``structure`` must be one of ``world.war_bases``/``world.factories``
    (a caller-supplied object, not re-looked-up by id) so this stays a
    cheap, allocation-free field read in the common "no override yet" case.
    See the module docstring for why runtime overrides live on
    ``GameState`` rather than a mutated ``WorldMap``.
    """
    override = state.structure_ownership_for(structure.id)
    if override is not None:
        return override.owner
    return structure.owner


#: Memo for :func:`effective_world` (M10.6 performance). ``engine.step``
#: resolves the effective world several times per tick, almost always with
#: the same base world and unchanged ownership, and rebuilding the overlaid
#: ``WorldMap`` dominated tick cost. The function is pure, so returning the
#: cached (immutable) result cannot change any outcome. Entries hold the base
#: world itself, so its ``id`` cannot be recycled while cached; bounded by
#: clearing when full.
_EFFECTIVE_WORLD_MEMO: dict[
    tuple[int, tuple[StructureOwnership, ...]], tuple[WorldMap, WorldMap]
] = {}
_MEMO_MAX_ENTRIES = 256
#: Same memo discipline for :func:`capture_footprint`, which ``advance_capture``
#: evaluates for every capturable structure every tick.
_FOOTPRINT_MEMO: dict[
    tuple[int, EntityId, InteractionKind], tuple[WorldMap, frozenset[tuple[int, int]]]
] = {}


def effective_world(world: WorldMap, state: GameState) -> WorldMap:
    """Return ``world`` with every ``state.structure_ownership`` override layered on top.

    Reuses ``map_overlay.apply_overlay`` -- the exact mechanism
    `map_overlay.py` already uses to layer scenario-time starting ownership
    over a base map -- rather than re-implementing "replace a structure's
    owner field" a second time. `engine.py` calls this once per tick (after
    :func:`advance_capture` has applied any completions for that tick) and
    threads the result into every world-dependent subsystem that reads
    structure ownership, so a captured structure's new owner is visible
    everywhere else in the same tick it completes.

    Returns ``world`` itself, unchanged, when there are no overrides
    (``state.structure_ownership`` is empty) -- true for every match before
    its first capture completes, and for every existing call site that
    never touches this subsystem at all.
    """
    if not state.structure_ownership:
        return world
    key = (id(world), state.structure_ownership)
    cached = _EFFECTIVE_WORLD_MEMO.get(key)
    if cached is not None and cached[0] is world:
        return cached[1]
    overlay = ScenarioOverlay(
        id="capture-runtime-ownership",
        ownership={record.structure_id: record.owner for record in state.structure_ownership},
        spawn_positions={},
    )
    result = apply_overlay(world, overlay)
    if len(_EFFECTIVE_WORLD_MEMO) >= _MEMO_MAX_ENTRIES:
        _EFFECTIVE_WORLD_MEMO.clear()
    _EFFECTIVE_WORLD_MEMO[key] = (world, result)
    return result


def capture_footprint(
    world: WorldMap, structure_id: EntityId, kind: InteractionKind
) -> frozenset[tuple[int, int]]:
    """Return the union of ``kind``-typed interaction-point footprint cells for ``structure_id``.

    A structure may declare more than one interaction point of the same
    kind (`interactions.py` does not forbid it); the qualifying-occupation
    location is the union of all of them. Returns an empty ``frozenset``
    when none are declared -- a valid "not capturable on this map" state,
    never an error (see the module docstring).

    Public (rather than module-private) because `orders.py`'s Search &
    Capture target selection must send a robot to exactly the cells
    :func:`advance_capture` counts as qualifying occupation. Two
    independent spellings of "where must a robot stand to capture this"
    could drift apart and produce an order that walks a robot to a cell
    that never starts a capture, so both read this one function.
    """
    key = (id(world), structure_id, kind)
    cached = _FOOTPRINT_MEMO.get(key)
    if cached is not None and cached[0] is world:
        return cached[1]
    cells: set[tuple[int, int]] = set()
    for point in world.interaction_points_for(structure_id, kind=kind):
        cells |= point.footprint.cells
    result = frozenset(cells)
    if len(_FOOTPRINT_MEMO) >= _MEMO_MAX_ENTRIES * 16:
        _FOOTPRINT_MEMO.clear()
    _FOOTPRINT_MEMO[key] = (world, result)
    return result


def _qualifying_robot(
    robots: tuple[Robot, ...],
    footprint: frozenset[tuple[int, int]],
    current_owner: PlayerId | None,
) -> Robot | None:
    """Return the deterministic qualifying robot occupying ``footprint``, or ``None``.

    A robot qualifies when its authoritative ``(x, y)`` is a member of
    ``footprint`` and its ``owner`` differs from ``current_owner`` (a
    neutral structure's ``current_owner is None`` makes every robot
    qualify, matching "first qualifying robot" for neutral acquisition; an
    owned structure only admits an *enemy* robot). See the module docstring
    for the deterministic smallest-``entity_id`` tie-break when more than
    one qualifying robot occupies a multi-cell footprint in the same tick.
    """
    candidates = [
        robot for robot in robots if (robot.x, robot.y) in footprint and robot.owner != current_owner
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda robot: robot.entity_id.value)


def _capturable_structures(
    world: WorldMap,
) -> list[tuple[WarBase | Factory, InteractionKind, CapturableStructureKind]]:
    """Return every war base/factory paired with its capture-point kind, in canonical id order.

    Canonical (sorted by ``structure.id.value``) order so
    :func:`advance_capture`'s processing -- and therefore its event
    emission order -- never depends on ``world.war_bases``/``world.factories``
    declaration order.
    """
    entries: list[tuple[WarBase | Factory, InteractionKind, CapturableStructureKind]] = [
        (factory, InteractionKind.FACTORY_CAPTURE, CapturableStructureKind.FACTORY)
        for factory in world.factories
    ]
    entries.extend(
        (war_base, InteractionKind.WARBASE_CAPTURE, CapturableStructureKind.WAR_BASE)
        for war_base in world.war_bases
    )
    entries.sort(key=lambda entry: entry[0].id.value)
    return entries


def advance_capture(
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> tuple[GameState, tuple[Event, ...]]:
    """Advance neutral acquisition and continuous-occupation capture by one tick.

    Pure function: computes qualifying occupation for every capturable
    structure from ``state.robots``' current authoritative positions
    (post move-completion; callers should invoke this after
    :func:`~nether_earth.movement.advance_all_robot_transitions` for the
    same tick -- `engine.py` does), applies neutral-factory instant
    acquisition plus continuous-occupation progress/reset/completion for
    everything else -- enemy factories, enemy war bases, and neutral war
    bases alike (see the module docstring) -- and returns
    ``(new_state, events)``.

    ``events`` holds one :class:`NeutralStructureAcquiredEvent` per neutral
    factory acquired this tick and one :class:`StructureCapturedEvent` per
    structure (enemy-owned or neutral war base) whose continuous-occupation
    capture completed this tick, in canonical (structure-id) order.
    Interruptions and in-progress accrual never emit
    an event -- only the two ownership-changing outcomes do, per this
    issue's "emit a deterministic ownership/capture event [on completion]"
    acceptance criterion; a consumer that wants interruption/progress
    detail can already read it directly from
    ``state.capture_progress``/``new_state.capture_progress``.

    A structure with zero declared capture-kind interaction points is
    skipped entirely (not capturable on this map). Ownership is read via
    :func:`effective_owner` (state overrides layered over ``world``), so
    this function is itself safe to call with the raw ``world`` --
    callers do not need to pre-resolve :func:`effective_world` first.
    """
    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()
    events: list[Event] = []

    ownership_by_id: dict[EntityId, PlayerId] = {
        record.structure_id: record.owner for record in state.structure_ownership
    }
    progress_by_id: dict[EntityId, CaptureProgress] = {
        progress.structure_id: progress for progress in state.capture_progress
    }

    for structure, interaction_kind, structure_kind in _capturable_structures(world):
        footprint = capture_footprint(world, structure.id, interaction_kind)
        if not footprint:
            continue

        current_owner = effective_owner(world, state, structure)
        qualifying_robot = _qualifying_robot(state.robots, footprint, current_owner)

        if current_owner is None and structure_kind is CapturableStructureKind.FACTORY:
            # Neutral *factories* alone get instant acquisition
            # (`_specs/functional-spec.md` §9). A neutral *war base* falls
            # through to the continuous-occupation path below --
            # `_specs/open-questions.md` §6 is explicit that "war-base
            # capture uses the same continuous-occupation rule as factory
            # capture by default", which this module reads as applying
            # regardless of whether the war base's current owner is another
            # player or nobody (``None``); only a factory's neutral state is
            # carved out as the one instant-acquisition exception.
            if qualifying_robot is not None:
                ownership_by_id[structure.id] = qualifying_robot.owner
                events.append(
                    NeutralStructureAcquiredEvent(
                        sequence=resolved_sequencer.next_sequence(),
                        structure_id=structure.id,
                        structure_kind=structure_kind,
                        new_owner=qualifying_robot.owner,
                        robot_id=qualifying_robot.entity_id,
                        tick=tick,
                    )
                )
            continue

        # Continuous-occupation path: every enemy-owned structure, plus a
        # neutral war base (the one carve-out above is factories only).
        existing = progress_by_id.get(structure.id)
        if qualifying_robot is None:
            # No qualifying robot currently occupies the capture location:
            # any in-progress attempt is interrupted and resets to zero
            # immediately (drop the record).
            progress_by_id.pop(structure.id, None)
            continue

        continues_existing = (
            existing is not None
            and existing.capturing_player == qualifying_robot.owner
            and existing.robot_id == qualifying_robot.entity_id
        )
        elapsed_ticks = (
            existing.elapsed_ticks + 1 if continues_existing and existing is not None else 1
        )
        required_ticks = rules.capture_duration_ticks

        if elapsed_ticks >= required_ticks:
            ownership_by_id[structure.id] = qualifying_robot.owner
            progress_by_id.pop(structure.id, None)
            events.append(
                StructureCapturedEvent(
                    sequence=resolved_sequencer.next_sequence(),
                    structure_id=structure.id,
                    structure_kind=structure_kind,
                    previous_owner=current_owner,
                    new_owner=qualifying_robot.owner,
                    robot_id=qualifying_robot.entity_id,
                    tick=tick,
                )
            )
        else:
            progress_by_id[structure.id] = CaptureProgress(
                structure_id=structure.id,
                capturing_player=qualifying_robot.owner,
                robot_id=qualifying_robot.entity_id,
                elapsed_ticks=elapsed_ticks,
                required_ticks=required_ticks,
            )

    new_state = state.with_structure_ownership(
        tuple(StructureOwnership(structure_id=sid, owner=owner) for sid, owner in ownership_by_id.items())
    ).with_capture_progress(tuple(progress_by_id.values()))

    return new_state, tuple(events)
