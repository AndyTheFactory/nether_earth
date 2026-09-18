"""Deterministic destination reservations and contention resolution (issue #62, M5.3).

`_specs/open-questions.md` §11 (RESOLVED) locks the rules this module owns:

- a destination cell is reserved when a robot move is accepted/started;
- a reserved destination is unavailable to other robots until the move
  completes or is cancelled;
- same-tick contention is resolved randomly using the match-local seeded
  deterministic RNG (`rng.py`);
- two contenders are a 50/50 coin flip, more contenders are chosen
  uniformly;
- losing contenders remain out of the destination and may retry/replan.

Never by robot id, submission order, or priority: the winner of a contested
cell is drawn from the seeded RNG, so it is unpredictable to players yet
reproducible for an identical seed + state + command stream.

Reservations are *derived*, not stored
---------------------------------------
There is no reservation registry on :class:`~nether_earth.state.GameState`.
A reservation is exactly "some robot has an in-flight
:class:`~nether_earth.robot.RobotMoveTransition` whose ``(to_x, to_y)`` is
this cell", so :class:`ReservationTable` is a pure projection of
``state.robots`` (:func:`reservations_from_state`), in the same spirit as
`movement.py`'s :func:`~nether_earth.movement.folded_robot_occupancy`
deriving live occupancy rather than caching it.

This is what makes the lifecycle requirements hold *by construction* rather
than by bookkeeping discipline:

- **taken on move start** -- `movement.py`'s
  :func:`~nether_earth.movement.apply_robot_move` is the single move-start
  point, and it is precisely what sets ``Robot.movement``;
- **released on completion** --
  :func:`~nether_earth.movement.advance_robot_transition` clears the
  transition in the same state transition that makes the destination the
  robot's authoritative cell (it becomes occupancy, not a reservation);
- **released on cancellation/failure** --
  :func:`~nether_earth.movement.cancel_robot_move` clears it, and a
  *rejected* move never started a transition at all, so there is nothing to
  leak;
- **no double release** -- releasing is "``movement`` is ``None``", an
  idempotent condition, not a counter to decrement.

Consequently no reservation can outlive the move that justifies it, no
reservation can be leaked by a code path that forgets to release one, and
snapshot/replay need no new field: any state that round-trips its robots
round-trips its reservations.

What plugs in where
--------------------
- :func:`destination_available` has exactly `movement.py`'s
  :data:`~nether_earth.movement.DestinationAvailabilityCheck` shape and is
  bound into :func:`~nether_earth.movement.validate_robot_move` /
  :func:`~nether_earth.movement.apply_robot_move` as the last legality gate;
  a reserved cell surfaces as
  :attr:`~nether_earth.movement.MovementRejectionReason.DESTINATION_UNAVAILABLE`.
- :func:`apply_robot_move_batch` is the batched entry point ``engine.step``
  uses: it validates every same-tick claim against one common entry state,
  groups the surviving claims by destination cell, resolves each contested
  cell with the seeded RNG, and only then starts the winners' moves. Any
  future robot control source (direct control M5.4, autonomous orders
  M5.7) must issue its moves through this function -- calling
  :func:`~nether_earth.movement.apply_robot_move` one request at a time
  would resolve a contested cell by submission order, which §11 forbids.

RNG stream
----------
`engine.py`'s module docstring locks how randomness enters a tick: a
milestone needing randomness "should construct ``rng.MatchRandom(state.seed)``
fresh from the state it is given, not thread a long-lived mutable RNG object
through ``GameState``". A single ``MatchRandom(state.seed)`` per tick would
be deterministic but *identical* every tick, so a recurring two-way
contention would always pick the same index -- reproducible, yet not the
locked coin flip. :func:`derive_contention_seed` therefore mixes the
immutable match seed with the authoritative tick into one per-tick seed
(splitmix64 finalizer: fixed integer arithmetic, no hashing, no platform or
run dependence), and :func:`contention_rng` builds the tick's stream from
it. Within a tick, contested cells draw successively from that one stream in
canonical ``(x, y)`` order, so each contested cell gets an independent draw.

Determinism: every function here is pure, iterates only canonically sorted
tuples (``state.robots`` is sorted by ``entity_id.value``; contested cells
are visited in sorted ``(x, y)`` order), reads no wall-clock time, and draws
randomness only from the match-local seeded stream described above. A
rejected claim returns the caller's ``state`` object unchanged, so a lost
contention provably mutates no occupancy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId
from nether_earth.map import WorldMap
from nether_earth.movement import (
    MovementRejectionReason,
    RobotMoveRequest,
    RobotMoveResult,
    RobotMoveStartedEvent,
    apply_robot_move,
    validate_robot_move,
)
from nether_earth.rng import MatchRandom
from nether_earth.robot import Robot
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "DestinationContentionResolvedEvent",
    "ReservationTable",
    "RobotMoveBatchResult",
    "apply_robot_move_batch",
    "contention_rng",
    "derive_contention_seed",
    "destination_available",
    "reservations_from_state",
]


# --------------------------------------------------------------------------
# Reservation table (derived projection of in-flight moves)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReservationTable:
    """Which destination cells are currently reserved, and by whom.

    An immutable, read-only view derived from a
    :class:`~nether_earth.state.GameState` by
    :func:`reservations_from_state` -- never constructed incrementally and
    never stored on the state (see the module docstring for why the derived
    form is what makes the reservation lifecycle leak-proof).

    ``holders`` maps ``(x, y)`` to the :class:`~nether_earth.ids.EntityId`
    of the robot whose in-flight move claims that cell. It is a
    ``MappingProxyType`` so a caller cannot mutate a table out from under
    the state it was derived from.
    """

    holders: Mapping[tuple[int, int], EntityId]

    def holder(self, x: int, y: int) -> EntityId | None:
        """Return the robot holding ``(x, y)``, or ``None`` if unreserved."""
        return self.holders.get((x, y))

    def is_reserved(self, x: int, y: int) -> bool:
        """Return whether ``(x, y)`` is reserved by any robot."""
        return (x, y) in self.holders

    def is_reserved_by_other(self, entity_id: EntityId, x: int, y: int) -> bool:
        """Return whether ``(x, y)`` is reserved by a robot other than ``entity_id``.

        A robot never contends with itself: re-asking about its own
        in-flight destination is not a conflict (though `movement.py`
        rejects such a request earlier, with
        :attr:`~nether_earth.movement.MovementRejectionReason.MOVE_IN_PROGRESS`).
        """
        holder = self.holders.get((x, y))
        return holder is not None and holder != entity_id

    def cells(self) -> tuple[tuple[int, int], ...]:
        """Return every reserved cell in canonical ``(x, y)`` sorted order."""
        return tuple(sorted(self.holders))


def reservations_from_state(state: GameState) -> ReservationTable:
    """Return the :class:`ReservationTable` implied by ``state``'s in-flight moves.

    Exactly one entry per robot with a non-``None``
    :attr:`~nether_earth.robot.Robot.movement`, keyed by that transition's
    destination. Robots are walked in ``state.robots``' canonical order
    (sorted by ``entity_id.value``, per `state.py`), so the projection never
    depends on incidental ordering.

    Two live transitions cannot legally target the same cell -- that is the
    invariant this module exists to preserve -- so a duplicate key would be
    an engine bug; :func:`destination_available` is what prevents one from
    ever being created.
    """
    holders: dict[tuple[int, int], EntityId] = {}
    for robot in state.robots:
        transition = robot.movement
        if transition is not None:
            holders[(transition.to_x, transition.to_y)] = robot.entity_id
    return ReservationTable(holders=MappingProxyType(holders))


def destination_available(state: GameState, robot: Robot, dest_x: int, dest_y: int) -> bool:
    """Return whether ``dest_x``/``dest_y`` is free of *another* robot's reservation.

    This is `movement.py`'s
    :data:`~nether_earth.movement.DestinationAvailabilityCheck` shape
    exactly, so it binds straight into
    :func:`~nether_earth.movement.validate_robot_move` /
    :func:`~nether_earth.movement.apply_robot_move` with no adapter. It
    answers only the reservation question: terrain, bounds, occupancy, and
    commander blocking stay `movement.py`'s, checked before this gate ever
    runs.
    """
    return not reservations_from_state(state).is_reserved_by_other(
        robot.entity_id, dest_x, dest_y
    )


# --------------------------------------------------------------------------
# Per-tick contention RNG stream
# --------------------------------------------------------------------------

_MASK64 = (1 << 64) - 1


def _mix64(value: int) -> int:
    """Return the splitmix64 finalizer of ``value`` (64-bit, wrapping).

    Fixed integer arithmetic only: no ``hash()`` (randomized per process for
    some types), no floats, no platform-width assumptions -- so the same
    input yields the same output in every process, on every machine, in
    every replay.
    """
    z = (value + 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return z ^ (z >> 31)


def derive_contention_seed(match_seed: int, tick: int) -> int:
    """Return the deterministic per-tick contention seed for a match.

    See the module docstring: the match seed alone would make every tick's
    contention draw identical, so the immutable match seed and the
    authoritative integer tick are mixed into one seed per tick. Two
    different ``(match_seed, tick)`` pairs give uncorrelated streams; the
    same pair always gives the same stream.
    """
    return _mix64((_mix64(match_seed & _MASK64) + tick) & _MASK64)


def contention_rng(match_seed: int, tick: int) -> MatchRandom:
    """Return this tick's match-local seeded RNG for contention resolution.

    A fresh :class:`~nether_earth.rng.MatchRandom` over
    :func:`derive_contention_seed`, honoring `engine.py`'s rule that a tick
    builds its RNG from the state it is given rather than threading a
    long-lived mutable RNG through ``GameState``.
    """
    return MatchRandom(seed=derive_contention_seed(match_seed, tick))


# --------------------------------------------------------------------------
# Batched claims, contention resolution, execution
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DestinationContentionResolvedEvent(Event):
    """Two or more robots claimed ``(x, y)`` on one tick; ``winner`` took it.

    Emitted once per *contested* destination (never for an uncontested
    claim), so replay logs record the seeded outcome itself rather than
    forcing a reader to re-derive it. ``contenders`` is in canonical
    ``entity_id.value`` order and always contains ``winner``; every other
    entry received a
    :attr:`~nether_earth.movement.MovementRejectionReason.DESTINATION_UNAVAILABLE`
    result and did not move.
    """

    x: int
    y: int
    contenders: tuple[EntityId, ...]
    winner: EntityId
    tick: int


@dataclass(frozen=True, slots=True)
class RobotMoveBatchResult:
    """Outcome of one tick's batch of robot move claims.

    ``results`` holds exactly one :class:`~nether_earth.movement.RobotMoveResult`
    per input request, in the canonical order the batch evaluated them
    (sorted by ``entity_id.value``), so a losing contender always gets a
    stable, inspectable reason its order/policy layer can retry or replan
    on. ``started``/``contentions`` are the events to merge into the tick's
    event stream.
    """

    state: GameState
    results: tuple[RobotMoveResult, ...]
    started: tuple[RobotMoveStartedEvent, ...]
    contentions: tuple[DestinationContentionResolvedEvent, ...]


def _canonical_requests(requests: Iterable[RobotMoveRequest]) -> tuple[RobotMoveRequest, ...]:
    """Return ``requests`` in canonical ``entity_id.value`` order.

    Submission order must never influence an outcome (`_specs/open-questions.md`
    §11), so the batch is evaluated in a canonical order instead. Ties
    within one entity (the same robot claiming twice in one tick) keep their
    relative input order, which is harmless: the second claim is rejected as
    :attr:`~nether_earth.movement.MovementRejectionReason.MOVE_IN_PROGRESS`
    either way.
    """
    return tuple(sorted(requests, key=lambda request: request.entity_id.value))


def _pick_winner(contenders: Sequence[EntityId], rng: MatchRandom) -> EntityId:
    """Return the seeded winner of a contested cell.

    A uniform draw over ``contenders`` -- which for two contenders *is* the
    locked 50/50 coin flip, and for N is the locked uniform choice. The
    draw is over positions, not identities, so no robot id, submission
    order, or priority can bias it (``contenders`` arrives canonically
    sorted purely so the draw is reproducible).
    """
    return rng.choice(contenders)


def apply_robot_move_batch(
    requests: Iterable[RobotMoveRequest],
    state: GameState,
    world: WorldMap,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> RobotMoveBatchResult:
    """Validate, deconflict, and start one tick's worth of robot moves.

    The single batched move-start path (see the module docstring). Phases:

    1. **Validate** every request against one common entry ``state`` via
       :func:`~nether_earth.movement.validate_robot_move`, with
       :func:`destination_available` bound as the reservation gate, walking
       requests in canonical ``entity_id.value`` order. A robot that
       already has a candidate claim in this batch cannot make a second one
       (:attr:`~nether_earth.movement.MovementRejectionReason.MOVE_IN_PROGRESS`),
       mirroring the one-move-at-a-time rule `movement.py` enforces across
       ticks.
    2. **Group** the surviving claims by destination cell. Visiting
       contested cells in canonical ``(x, y)`` order, each one draws its
       winner from this tick's seeded stream (:func:`contention_rng`); the
       losers get a
       :attr:`~nether_earth.movement.MovementRejectionReason.DESTINATION_UNAVAILABLE`
       result, stay exactly where they are, and mutate nothing.
    3. **Start** the winners' moves through
       :func:`~nether_earth.movement.apply_robot_move` -- the one move-start
       point -- in canonical entity order. Winners hold distinct
       destinations by construction, so their starts cannot conflict with
       each other; each is nonetheless re-validated against the evolving
       state, and a (defensive) late rejection is surfaced as that robot's
       result rather than silently dropped.

    Returns a :class:`RobotMoveBatchResult` whose ``state`` is the caller's
    own object when nothing started, so an all-rejected batch provably
    causes no partial state mutation.

    ``sequencer``, when supplied, assigns every emitted event's sequence
    number, exactly as in `movement.py`; callers orchestrating a whole tick
    (``engine.step``) must supply the tick's shared sequencer.
    """
    ordered = _canonical_requests(requests)

    results_by_request: dict[int, RobotMoveResult] = {}
    # Claims carry their batch index so a result can always be placed back
    # against the exact request that produced it, without relying on request
    # object identity or value equality (two requests can be equal).
    claims: dict[tuple[int, int], list[tuple[int, RobotMoveRequest]]] = {}
    claimed_entities: set[EntityId] = set()

    # --- Phase 1: validate every claim against the common entry state ------
    for index, request in enumerate(ordered):
        if request.entity_id in claimed_entities:
            results_by_request[index] = RobotMoveResult.reject(
                request, MovementRejectionReason.MOVE_IN_PROGRESS
            )
            continue
        result = validate_robot_move(request, state, world, rules, destination_available)
        if not result.accepted:
            results_by_request[index] = result
            continue
        robot = state.robot_for(request.entity_id)
        assert robot is not None  # guaranteed by validate_robot_move's NO_SUCH_ROBOT check
        destination = (robot.x + request.dx, robot.y + request.dy)
        claims.setdefault(destination, []).append((index, request))
        claimed_entities.add(request.entity_id)

    # --- Phase 2: resolve contested cells with the tick's seeded stream ----
    rng = contention_rng(state.seed, tick)
    winners: list[tuple[int, RobotMoveRequest]] = []
    contentions: list[DestinationContentionResolvedEvent] = []
    for destination in sorted(claims):
        contenders = claims[destination]
        if len(contenders) == 1:
            winners.append(contenders[0])
            continue
        contender_ids = tuple(request.entity_id for _index, request in contenders)
        winner_id = _pick_winner(contender_ids, rng)
        contentions.append(
            DestinationContentionResolvedEvent(
                sequence=sequencer.next_sequence() if sequencer is not None else 0,
                x=destination[0],
                y=destination[1],
                contenders=contender_ids,
                winner=winner_id,
                tick=tick,
            )
        )
        for index, request in contenders:
            if request.entity_id == winner_id:
                winners.append((index, request))
            else:
                results_by_request[index] = RobotMoveResult.reject(
                    request, MovementRejectionReason.DESTINATION_UNAVAILABLE
                )

    # --- Phase 3: start the winners' moves, canonical entity order ---------
    started: list[RobotMoveStartedEvent] = []
    for index, request in sorted(winners, key=lambda pair: pair[0]):
        state, result, event = apply_robot_move(
            request, state, world, tick, rules, destination_available, sequencer
        )
        results_by_request[index] = result
        if event is not None:
            started.append(event)

    results = tuple(results_by_request[index] for index in range(len(ordered)))
    return RobotMoveBatchResult(
        state=state,
        results=results,
        started=tuple(started),
        contentions=tuple(contentions),
    )
