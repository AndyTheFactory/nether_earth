"""Top-level authoritative game state shell.

Mutation convention: ``GameState`` is immutable (``frozen=True, slots=True``,
matching ``map.BootstrapMap``). State never changes in place; a new
``GameState`` is produced via an explicit, named transition method (e.g.
``with_tick``) rather than ad hoc attribute assignment or ``dataclasses.replace``
calls scattered across callers. This keeps every state transition a single,
auditable, deterministic step, which later issues (#4 tick clock, #7
simulation step) build on.

Ordering convention: ``players`` is exposed as a tuple in canonical order
(sorted by ``PlayerId.value``), never as a plain ``set``/``dict`` whose
iteration order could depend on insertion history. This guarantees that
serialization and any future derived output (events, snapshots) are
independent of the order callers happen to supply players in. ``commanders``
(added by issue #37), ``resource_pools`` (added by issue #54), and
``construction_sessions`` (added by issue #55) follow the exact same
convention: a tuple sorted by the owning player's ``PlayerId.value``, never
a plain ``dict``/``set``.

Scope: this milestone (M1.1) intentionally excludes map/world/entities/
robots/economy/combat. Issue #37 (M3.1) adds the first entity-shaped field,
``commanders``; issue #54 (M4.4) adds ``resource_pools``; issue #55 (M4.5)
adds ``construction_sessions`` -- see below -- but robots and map/world
integration remain out of scope for later issues.

Seed field (added by issue #7, the simulation-step integration task): the
engine's match-local RNG ownership contract (``rng.py``) requires "same seed
=> same output sequence", but ``rng.MatchRandom`` is a *mutable*, stateful
wrapper around ``random.Random`` — each draw advances it. Embedding a live
``MatchRandom`` instance directly on this frozen ``GameState`` would make
state equality/serialization depend on private RNG internals and would be
awkward to reason about across ticks. Instead, ``GameState`` carries only the
immutable integer ``seed`` it was created with; any code that actually needs
to draw random numbers (no such gameplay system exists yet in M1) is expected
to construct a fresh ``rng.MatchRandom(state.seed)`` on demand. This keeps
``GameState`` a clean, comparable, serializable-in-spirit value type (relevant
to issue #8's upcoming canonical-snapshot work) while still making "same seed
=> same behavior" structurally checkable today.

Commanders field (added by issue #37): ``commanders`` attaches the
authoritative per-player :class:`~nether_earth.commander.Commander` state
(see ``commander.py``) directly onto ``GameState``, following the same
"immutable tuple in canonical order" convention as ``players`` rather than
introducing a first ``dict``/``Mapping``-shaped field -- this keeps
``GameState`` equality, serialization, and iteration order independent of
insertion history for exactly the reasons documented above. A commander's
``player_id`` must be one of ``players`` (an orphaned commander with no
matching player would make "which players have commanders" ambiguous), and
no two commanders may share a ``player_id`` (a player has at most one
commander). Use :func:`create_game_state` or :meth:`with_commanders` rather
than constructing this dataclass with a pre-sorted/pre-validated tuple by
convention alone.

Resource pools field (added by issue #54, M4.4): ``resource_pools`` attaches
the authoritative per-player
:class:`~nether_earth.resource_pool.PlayerResourcePool` state directly onto
``GameState``, mirroring ``commanders`` exactly for the same reasons: a
tuple in canonical (sorted by ``PlayerResourcePool.player_id.value``) order,
never a ``dict``/``Mapping``-shaped field; every pool's ``player_id`` must
be a participant in ``players``; no two pools may share a ``player_id`` (a
player has at most one resource pool). Use :func:`create_game_state` or
:meth:`with_resource_pools` rather than constructing this dataclass with a
pre-sorted/pre-validated tuple by convention alone.

Construction sessions field (added by issue #55, M4.5):
``construction_sessions`` attaches the authoritative per-player
:class:`~nether_earth.construction_session.ConstructionSession` state
directly onto ``GameState``, mirroring ``commanders``/``resource_pools``
exactly: a tuple in canonical (sorted by
``ConstructionSession.player_id.value``) order, never a ``dict``/
``Mapping``-shaped field; every session's ``player_id`` must be a
participant in ``players``; no two sessions may share a ``player_id`` (a
player has at most one active construction session -- "no session for this
player" is represented by absence from the tuple, not a separate inactive
value). Use :func:`create_game_state` or :meth:`with_construction_sessions`
rather than constructing this dataclass with a pre-sorted/pre-validated
tuple by convention alone. ``construction_session.py`` is only imported
under ``TYPE_CHECKING`` here to avoid a circular import (that module itself
imports ``GameState`` to type its own session-returning functions).

Robots field (added by issue #56, M4.6): ``robots`` attaches the
authoritative :class:`~nether_earth.robot.Robot` entity collection directly
onto ``GameState``, following the same "immutable tuple, never a ``dict``/
``Mapping``-shaped field" convention as ``commanders``/``resource_pools``/
``construction_sessions`` -- but ordered differently. Unlike those three
fields, a player may own *any number* of robots (there is no "at most one
per player" constraint, so sorting by owning-player id alone would not
produce a total order), so ``robots`` is instead sorted by
``Robot.entity_id.value`` -- each robot's own stable identifier -- which is
guaranteed unique (enforced below) and therefore always yields one
canonical order regardless of launch order. Every robot's ``owner`` must be
a participant in ``players``; no two robots may share an ``entity_id``. Use
:func:`create_game_state` or :meth:`with_robots` rather than constructing
this dataclass with a pre-sorted/pre-validated tuple by convention alone.
``robot.py`` is only imported under ``TYPE_CHECKING`` here to avoid a
circular import (that module itself imports ``ids``/``robot_build``, not
``GameState``, but is kept consistent with the ``construction_session``
precedent above for symmetry and to keep this module's own import graph
shallow).

Projectiles field (added by issue #73, M6.4): ``projectiles`` attaches the
authoritative in-flight :class:`~nether_earth.combat.Projectile` collection
directly onto ``GameState``, following the exact same "immutable tuple,
never a ``dict``/``Mapping``-shaped field" convention established above.
Like ``robots`` -- and unlike ``commanders``/``resource_pools``/
``construction_sessions`` -- a projectile has no "at most one per player"
constraint (a player may have several robots each with their own in-flight
projectile at once), so sorting by owning-player id alone would not
produce a total order; ``projectiles`` is instead sorted by its own
``Projectile.id.value``, mirroring ``robots``' rationale exactly. No
``players``-membership check is performed on a projectile's ``owner`` here
because ``combat.py`` (which constructs every ``Projectile``) already
derives ``owner`` from a validated ``FireRequest``/``Robot`` pair; unlike
``robots``/``commanders``/etc., ``GameState.with_projectiles`` only
enforces the one invariant it can check locally (unique ``id``).
``combat.py`` is only imported under ``TYPE_CHECKING`` here to avoid a
circular import (that module itself will need ``GameState`` for
``apply_fire``/``advance_projectiles``), mirroring the ``robot.py``/
``construction_session.py`` precedent above.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nether_earth.commander import Commander
from nether_earth.ids import EntityId, PlayerId
from nether_earth.resource_pool import PlayerResourcePool

if TYPE_CHECKING:
    from nether_earth.capture import CaptureProgress, StructureOwnership
    from nether_earth.combat import Projectile
    from nether_earth.construction_session import ConstructionSession
    from nether_earth.robot import Robot


def _canonical_commanders(commanders: tuple[Commander, ...]) -> tuple[Commander, ...]:
    """Return ``commanders`` sorted canonically, after validating them.

    Shared by :func:`create_game_state` and :meth:`GameState.with_commanders`
    so both entry points enforce the identical ordering/uniqueness contract.
    """
    seen_players: set[PlayerId] = set()
    for commander in commanders:
        if commander.player_id in seen_players:
            raise ValueError(
                f"duplicate commander for player {commander.player_id.value!r}: "
                "a player may have at most one commander"
            )
        seen_players.add(commander.player_id)
    return tuple(sorted(commanders, key=lambda commander: commander.player_id.value))


def _canonical_resource_pools(
    resource_pools: tuple[PlayerResourcePool, ...],
) -> tuple[PlayerResourcePool, ...]:
    """Return ``resource_pools`` sorted canonically, after validating them.

    Shared by :func:`create_game_state` and
    :meth:`GameState.with_resource_pools`, mirroring
    :func:`_canonical_commanders` exactly (see issue #54, M4.4).
    """
    seen_players: set[PlayerId] = set()
    for pool in resource_pools:
        if pool.player_id in seen_players:
            raise ValueError(
                f"duplicate resource pool for player {pool.player_id.value!r}: "
                "a player may have at most one resource pool"
            )
        seen_players.add(pool.player_id)
    return tuple(sorted(resource_pools, key=lambda pool: pool.player_id.value))


def _canonical_construction_sessions(
    construction_sessions: tuple[ConstructionSession, ...],
) -> tuple[ConstructionSession, ...]:
    """Return ``construction_sessions`` sorted canonically, after validating them.

    Shared by :func:`create_game_state` and
    :meth:`GameState.with_construction_sessions`, mirroring
    :func:`_canonical_commanders`/:func:`_canonical_resource_pools` exactly
    (see issue #55, M4.5).
    """
    seen_players: set[PlayerId] = set()
    for session in construction_sessions:
        if session.player_id in seen_players:
            raise ValueError(
                f"duplicate construction session for player {session.player_id.value!r}: "
                "a player may have at most one active construction session"
            )
        seen_players.add(session.player_id)
    return tuple(sorted(construction_sessions, key=lambda session: session.player_id.value))


def _canonical_robots(robots: tuple[Robot, ...]) -> tuple[Robot, ...]:
    """Return ``robots`` sorted canonically, after validating them.

    Shared by :func:`create_game_state` and :meth:`GameState.with_robots`.
    Unlike :func:`_canonical_commanders`/:func:`_canonical_resource_pools`/
    :func:`_canonical_construction_sessions`, robots sort by their own
    ``entity_id.value`` (see the module docstring for why "one per player"
    ordering does not apply here) and are validated for a unique
    ``entity_id``, not a unique owning player -- multiple robots may share
    an ``owner``.
    """
    seen_ids: set[EntityId] = set()
    for robot in robots:
        if robot.entity_id in seen_ids:
            raise ValueError(
                f"duplicate robot entity_id {robot.entity_id.value!r}: "
                "every robot must have a unique entity_id"
            )
        seen_ids.add(robot.entity_id)
    return tuple(sorted(robots, key=lambda robot: robot.entity_id.value))


def _canonical_projectiles(projectiles: tuple[Projectile, ...]) -> tuple[Projectile, ...]:
    """Return ``projectiles`` sorted canonically, after validating them.

    Shared by :func:`create_game_state` and :meth:`GameState.with_projectiles`.
    Mirrors :func:`_canonical_robots`'s "sort/validate by the record's own
    id" shape exactly, for the same reason: a projectile has no "one per
    player" constraint, so it sorts by its own ``id.value`` rather than an
    owning player's id, and is validated for a unique ``id``.
    """
    seen_ids: set[EntityId] = set()
    for projectile in projectiles:
        if projectile.id in seen_ids:
            raise ValueError(
                f"duplicate projectile id {projectile.id.value!r}: "
                "every projectile must have a unique id"
            )
        seen_ids.add(projectile.id)
    return tuple(sorted(projectiles, key=lambda projectile: projectile.id.value))


def _canonical_structure_ownership(
    structure_ownership: tuple[StructureOwnership, ...],
) -> tuple[StructureOwnership, ...]:
    """Return ``structure_ownership`` sorted canonically, after validating it.

    Shared by :func:`create_game_state` and
    :meth:`GameState.with_structure_ownership` (see issue #66, M5.7). Mirrors
    :func:`_canonical_robots`'s "sort/validate by the record's own id"
    shape: a structure has at most one runtime ownership override recorded
    at a time (absence means "still whatever ``WorldMap`` says", never a
    second, stale override), so uniqueness is enforced on
    ``structure_id``, not on the owning player (unlike
    ``commanders``/``resource_pools``/``construction_sessions``, many
    structures may share the same current owner).
    """
    seen_structures: set[EntityId] = set()
    for record in structure_ownership:
        if record.structure_id in seen_structures:
            raise ValueError(
                f"duplicate structure ownership override for structure "
                f"{record.structure_id.value!r}: a structure may have at most one "
                "runtime ownership override"
            )
        seen_structures.add(record.structure_id)
    return tuple(sorted(structure_ownership, key=lambda record: record.structure_id.value))


def _canonical_capture_progress(
    capture_progress: tuple[CaptureProgress, ...],
) -> tuple[CaptureProgress, ...]:
    """Return ``capture_progress`` sorted canonically, after validating it.

    Shared by :func:`create_game_state` and
    :meth:`GameState.with_capture_progress` (see issue #66, M5.7). A
    structure has at most one active capture attempt in progress at a time
    -- per `_specs/open-questions.md` §7, any interruption resets progress
    to zero immediately rather than retaining a second, stale attempt --
    so uniqueness is enforced on ``structure_id``, mirroring
    :func:`_canonical_structure_ownership`.
    """
    seen_structures: set[EntityId] = set()
    for progress in capture_progress:
        if progress.structure_id in seen_structures:
            raise ValueError(
                f"duplicate capture progress for structure "
                f"{progress.structure_id.value!r}: a structure may have at most one "
                "active capture attempt in progress"
            )
        seen_structures.add(progress.structure_id)
    return tuple(sorted(capture_progress, key=lambda progress: progress.structure_id.value))


@dataclass(frozen=True, slots=True)
class GameState:
    """Minimal authoritative engine state: tick, players, commanders, resource pools, and construction sessions.

    ``players`` is always stored in canonical (sorted-by-id) order; use
    :func:`create_game_state` or :meth:`with_tick` rather than constructing
    this dataclass with a pre-sorted tuple by convention alone.

    ``seed`` is the immutable match seed this state (and any future state
    derived from it via :meth:`with_tick`) was initialized with. It defaults
    to ``0`` so existing callers that construct ``GameState`` without a seed
    keep working unchanged. See the module docstring for why this is a plain
    ``int`` rather than a live ``rng.MatchRandom`` instance.

    ``commanders`` is always stored in canonical (sorted by
    ``Commander.player_id.value``) order, matching ``players``; it defaults
    to ``()`` so existing callers that construct ``GameState`` without
    commander state keep working unchanged. See the module docstring for the
    ownership invariants ``create_game_state``/``with_commanders`` enforce.

    ``resource_pools`` is always stored in canonical (sorted by
    ``PlayerResourcePool.player_id.value``) order, matching ``players``; it
    defaults to ``()`` so existing callers that construct ``GameState``
    without resource-pool state keep working unchanged. See the module
    docstring for the ownership invariants
    ``create_game_state``/``with_resource_pools`` enforce.

    ``construction_sessions`` is always stored in canonical (sorted by
    ``ConstructionSession.player_id.value``) order, matching ``players``; it
    defaults to ``()`` so existing callers that construct ``GameState``
    without construction-session state keep working unchanged. See the
    module docstring for the ownership invariants
    ``create_game_state``/``with_construction_sessions`` enforce.

    ``robots`` is always stored in canonical (sorted by
    ``Robot.entity_id.value``) order; it defaults to ``()`` so existing
    callers that construct ``GameState`` without robot state keep working
    unchanged. See the module docstring for why robots sort by their own
    entity id rather than owning-player id, and for the ownership/
    uniqueness invariants ``create_game_state``/``with_robots`` enforce.

    ``structure_ownership`` (added by issue #66, M5.7) carries runtime
    ownership overrides for war bases/factories captured since match start,
    layered over ``WorldMap``'s own static/scenario-starting ``owner``
    fields (see ``capture.py``'s ``effective_world``) -- ``WorldMap`` is a
    plain loaded map value external to ``GameState`` and is never mutated
    in place, so a structure's *current* owner after any capture completes
    is authoritative only on ``GameState``. It is always stored in
    canonical (sorted by ``StructureOwnership.structure_id.value``) order,
    at most one override per structure; it defaults to ``()`` so existing
    callers keep working unchanged.

    ``capture_progress`` (added by issue #66, M5.7) carries the in-progress
    continuous-occupation capture attempt, if any, for each contested
    structure (`_specs/technical-spec.md` §10's ``CaptureProgress``). It is
    always stored in canonical (sorted by
    ``CaptureProgress.structure_id.value``) order, at most one active
    attempt per structure; it defaults to ``()`` so existing callers keep
    working unchanged.

    ``projectiles`` (added by issue #73, M6.4) carries every in-flight
    :class:`~nether_earth.combat.Projectile`. It is always stored in
    canonical (sorted by ``Projectile.id.value``) order, matching
    ``robots``' own-id sort rather than an owning-player sort (see the
    module docstring); it defaults to ``()`` so existing callers keep
    working unchanged.
    """

    tick: int
    players: tuple[PlayerId, ...]
    seed: int = 0
    commanders: tuple[Commander, ...] = ()
    resource_pools: tuple[PlayerResourcePool, ...] = ()
    construction_sessions: tuple[ConstructionSession, ...] = ()
    robots: tuple[Robot, ...] = ()
    structure_ownership: tuple[StructureOwnership, ...] = ()
    capture_progress: tuple[CaptureProgress, ...] = ()
    projectiles: tuple[Projectile, ...] = ()

    def with_tick(self, tick: int) -> GameState:
        """Return a new ``GameState`` with ``tick`` replaced.

        This is the explicit, controlled transition for advancing the
        authoritative tick counter; callers must not mutate ``tick`` in
        place. ``players``, ``seed``, ``commanders``, ``resource_pools``,
        ``construction_sessions``, ``robots``, ``structure_ownership``,
        ``capture_progress``, and ``projectiles`` are carried over
        unchanged.
        """
        return GameState(
            tick=tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=self.resource_pools,
            construction_sessions=self.construction_sessions,
            robots=self.robots,
            structure_ownership=self.structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=self.projectiles,
        )

    def with_commanders(self, commanders: tuple[Commander, ...]) -> GameState:
        """Return a new ``GameState`` with ``commanders`` replaced.

        ``commanders`` may be supplied in any order; the result is
        normalized to canonical (sorted by ``player_id.value``) order. Every
        commander's ``player_id`` must already be a participant in
        ``self.players`` and no two commanders may share a ``player_id`` --
        see the module docstring for why these invariants live here rather
        than being left to caller discipline. ``tick``, ``players``,
        ``seed``, ``resource_pools``, and ``construction_sessions`` are
        carried over unchanged.
        """
        for commander in commanders:
            if commander.player_id not in self.players:
                raise ValueError(
                    f"commander player_id {commander.player_id.value!r} is not a "
                    "participant in this GameState's players"
                )
        canonical_commanders = _canonical_commanders(tuple(commanders))
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=canonical_commanders,
            resource_pools=self.resource_pools,
            construction_sessions=self.construction_sessions,
            robots=self.robots,
            structure_ownership=self.structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=self.projectiles,
        )

    def commander_for(self, player_id: PlayerId) -> Commander | None:
        """Return ``player_id``'s commander, or ``None`` if it has none."""
        for commander in self.commanders:
            if commander.player_id == player_id:
                return commander
        return None

    def with_resource_pools(
        self, resource_pools: tuple[PlayerResourcePool, ...]
    ) -> GameState:
        """Return a new ``GameState`` with ``resource_pools`` replaced.

        ``resource_pools`` may be supplied in any order; the result is
        normalized to canonical (sorted by ``player_id.value``) order. Every
        pool's ``player_id`` must already be a participant in
        ``self.players`` and no two pools may share a ``player_id`` -- see
        the module docstring for why these invariants live here rather than
        being left to caller discipline. ``tick``, ``players``, ``seed``,
        ``commanders``, and ``construction_sessions`` are carried over
        unchanged.
        """
        for pool in resource_pools:
            if pool.player_id not in self.players:
                raise ValueError(
                    f"resource pool player_id {pool.player_id.value!r} is not a "
                    "participant in this GameState's players"
                )
        canonical_resource_pools = _canonical_resource_pools(tuple(resource_pools))
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=canonical_resource_pools,
            construction_sessions=self.construction_sessions,
            robots=self.robots,
            structure_ownership=self.structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=self.projectiles,
        )

    def resource_pool_for(self, player_id: PlayerId) -> PlayerResourcePool | None:
        """Return ``player_id``'s resource pool, or ``None`` if it has none."""
        for pool in self.resource_pools:
            if pool.player_id == player_id:
                return pool
        return None

    def with_construction_sessions(
        self, construction_sessions: tuple[ConstructionSession, ...]
    ) -> GameState:
        """Return a new ``GameState`` with ``construction_sessions`` replaced.

        ``construction_sessions`` may be supplied in any order; the result
        is normalized to canonical (sorted by ``player_id.value``) order.
        Every session's ``player_id`` must already be a participant in
        ``self.players`` and no two sessions may share a ``player_id`` --
        see the module docstring for why these invariants live here rather
        than being left to caller discipline. ``tick``, ``players``,
        ``seed``, ``commanders``, and ``resource_pools`` are carried over
        unchanged.
        """
        for session in construction_sessions:
            if session.player_id not in self.players:
                raise ValueError(
                    f"construction session player_id {session.player_id.value!r} is not "
                    "a participant in this GameState's players"
                )
        canonical_construction_sessions = _canonical_construction_sessions(
            tuple(construction_sessions)
        )
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=self.resource_pools,
            construction_sessions=canonical_construction_sessions,
            robots=self.robots,
            structure_ownership=self.structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=self.projectiles,
        )

    def construction_session_for(self, player_id: PlayerId) -> ConstructionSession | None:
        """Return ``player_id``'s active construction session, or ``None`` if it has none."""
        for session in self.construction_sessions:
            if session.player_id == player_id:
                return session
        return None

    def with_robots(self, robots: tuple[Robot, ...]) -> GameState:
        """Return a new ``GameState`` with ``robots`` replaced.

        ``robots`` may be supplied in any order; the result is normalized
        to canonical (sorted by ``entity_id.value``) order. Every robot's
        ``owner`` must already be a participant in ``self.players`` and no
        two robots may share an ``entity_id`` -- see the module docstring
        for why these invariants live here rather than being left to
        caller discipline. ``tick``, ``players``, ``seed``, ``commanders``,
        ``resource_pools``, and ``construction_sessions`` are carried over
        unchanged.
        """
        for robot in robots:
            if robot.owner not in self.players:
                raise ValueError(
                    f"robot owner {robot.owner.value!r} is not a participant in "
                    "this GameState's players"
                )
        canonical_robots = _canonical_robots(tuple(robots))
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=self.resource_pools,
            construction_sessions=self.construction_sessions,
            robots=canonical_robots,
            structure_ownership=self.structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=self.projectiles,
        )

    def robot_for(self, entity_id: EntityId) -> Robot | None:
        """Return the robot with ``entity_id``, or ``None`` if it has no such robot."""
        for robot in self.robots:
            if robot.entity_id == entity_id:
                return robot
        return None

    def robots_for(self, player_id: PlayerId) -> tuple[Robot, ...]:
        """Return every robot owned by ``player_id``, in canonical entity-id order."""
        return tuple(robot for robot in self.robots if robot.owner == player_id)

    def with_structure_ownership(
        self, structure_ownership: tuple[StructureOwnership, ...]
    ) -> GameState:
        """Return a new ``GameState`` with ``structure_ownership`` replaced.

        ``structure_ownership`` may be supplied in any order; the result is
        normalized to canonical (sorted by ``structure_id.value``) order.
        No two overrides may share a ``structure_id`` -- see
        :func:`_canonical_structure_ownership`. Unlike
        ``commanders``/``resource_pools``/``robots``, there is no
        ``self.players``-membership check here: a structure id is not a
        ``PlayerId`` and this module has no map/world reference to validate
        it against (``capture.py`` validates structure ids against the
        ``WorldMap`` it is given before ever constructing a
        :class:`~nether_earth.capture.StructureOwnership`). ``tick``,
        ``players``, ``seed``, ``commanders``, ``resource_pools``,
        ``construction_sessions``, ``robots``, and ``capture_progress`` are
        carried over unchanged.
        """
        canonical_structure_ownership = _canonical_structure_ownership(
            tuple(structure_ownership)
        )
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=self.resource_pools,
            construction_sessions=self.construction_sessions,
            robots=self.robots,
            structure_ownership=canonical_structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=self.projectiles,
        )

    def structure_ownership_for(self, structure_id: EntityId) -> StructureOwnership | None:
        """Return the runtime ownership override for ``structure_id``, or ``None`` if it has none."""
        for record in self.structure_ownership:
            if record.structure_id == structure_id:
                return record
        return None

    def with_capture_progress(self, capture_progress: tuple[CaptureProgress, ...]) -> GameState:
        """Return a new ``GameState`` with ``capture_progress`` replaced.

        ``capture_progress`` may be supplied in any order; the result is
        normalized to canonical (sorted by ``structure_id.value``) order.
        No two entries may share a ``structure_id`` -- see
        :func:`_canonical_capture_progress`. ``tick``, ``players``,
        ``seed``, ``commanders``, ``resource_pools``,
        ``construction_sessions``, ``robots``, and ``structure_ownership``
        are carried over unchanged.
        """
        canonical_capture_progress = _canonical_capture_progress(tuple(capture_progress))
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=self.resource_pools,
            construction_sessions=self.construction_sessions,
            robots=self.robots,
            structure_ownership=self.structure_ownership,
            capture_progress=canonical_capture_progress,
            projectiles=self.projectiles,
        )

    def capture_progress_for(self, structure_id: EntityId) -> CaptureProgress | None:
        """Return the in-progress capture attempt for ``structure_id``, or ``None`` if it has none."""
        for progress in self.capture_progress:
            if progress.structure_id == structure_id:
                return progress
        return None

    def with_projectiles(self, projectiles: tuple[Projectile, ...]) -> GameState:
        """Return a new ``GameState`` with ``projectiles`` replaced.

        ``projectiles`` may be supplied in any order; the result is
        normalized to canonical (sorted by ``id.value``) order, mirroring
        :meth:`with_robots` exactly (see the module docstring for why
        projectiles sort by their own id rather than owning-player id). No
        two projectiles may share an ``id`` -- see
        :func:`_canonical_projectiles`. ``tick``, ``players``, ``seed``,
        ``commanders``, ``resource_pools``, ``construction_sessions``,
        ``robots``, ``structure_ownership``, and ``capture_progress`` are
        carried over unchanged.
        """
        canonical_projectiles = _canonical_projectiles(tuple(projectiles))
        return GameState(
            tick=self.tick,
            players=self.players,
            seed=self.seed,
            commanders=self.commanders,
            resource_pools=self.resource_pools,
            construction_sessions=self.construction_sessions,
            robots=self.robots,
            structure_ownership=self.structure_ownership,
            capture_progress=self.capture_progress,
            projectiles=canonical_projectiles,
        )

    def projectile_for(self, entity_id: EntityId) -> Projectile | None:
        """Return the projectile with ``entity_id``, or ``None`` if it has no such projectile."""
        for projectile in self.projectiles:
            if projectile.id == entity_id:
                return projectile
        return None


def create_game_state(
    tick: int,
    players: tuple[PlayerId, ...] | set[PlayerId] | list[PlayerId],
    seed: int = 0,
    commanders: tuple[Commander, ...] | list[Commander] | None = None,
    resource_pools: tuple[PlayerResourcePool, ...] | list[PlayerResourcePool] | None = None,
    construction_sessions: tuple[ConstructionSession, ...] | list[ConstructionSession] | None = None,
    robots: tuple[Robot, ...] | list[Robot] | None = None,
    structure_ownership: tuple[StructureOwnership, ...] | list[StructureOwnership] | None = None,
    capture_progress: tuple[CaptureProgress, ...] | list[CaptureProgress] | None = None,
    projectiles: tuple[Projectile, ...] | list[Projectile] | None = None,
) -> GameState:
    """Construct a ``GameState`` with ``players``/``commanders``/``resource_pools``/``construction_sessions``/``robots``/``structure_ownership``/``capture_progress``/``projectiles`` normalized.

    ``players`` may be supplied in any order or as any iterable of unique
    ``PlayerId`` values; the resulting ``GameState.players`` is always the
    deduplicated tuple sorted by ``PlayerId.value``, so two calls describing
    the same logical set of players always produce an identical state.

    ``seed`` is recorded on the resulting state unchanged (see the module
    docstring); it defaults to ``0`` for callers that do not care about
    seeded randomness.

    ``commanders`` defaults to no commanders (``()``); when supplied, every
    commander's ``player_id`` must be a member of the resolved ``players``
    set and no two commanders may share a ``player_id`` (see the module
    docstring). The resulting ``GameState.commanders`` is always sorted by
    ``player_id.value``.

    ``resource_pools`` defaults to no resource pools (``()``); when
    supplied, every pool's ``player_id`` must be a member of the resolved
    ``players`` set and no two pools may share a ``player_id`` (see the
    module docstring). The resulting ``GameState.resource_pools`` is always
    sorted by ``player_id.value``.

    ``construction_sessions`` defaults to no sessions (``()``); when
    supplied, every session's ``player_id`` must be a member of the
    resolved ``players`` set and no two sessions may share a ``player_id``
    (see the module docstring). The resulting
    ``GameState.construction_sessions`` is always sorted by
    ``player_id.value``.

    ``robots`` defaults to no robots (``()``); when supplied, every robot's
    ``owner`` must be a member of the resolved ``players`` set and no two
    robots may share an ``entity_id`` (see the module docstring for why
    "one per player" ordering does not apply here). The resulting
    ``GameState.robots`` is always sorted by ``entity_id.value``.

    ``structure_ownership`` defaults to no overrides (``()``); at most one
    override per ``structure_id`` (see the module docstring). The resulting
    ``GameState.structure_ownership`` is always sorted by
    ``structure_id.value``.

    ``capture_progress`` defaults to no in-progress attempts (``()``); at
    most one attempt per ``structure_id`` (see the module docstring). The
    resulting ``GameState.capture_progress`` is always sorted by
    ``structure_id.value``.

    ``projectiles`` defaults to no in-flight projectiles (``()``); no two
    projectiles may share an ``id`` (see the module docstring). The
    resulting ``GameState.projectiles`` is always sorted by ``id.value``.
    """
    if tick < 0:
        raise ValueError("tick must be non-negative")
    canonical_players = tuple(sorted(set(players), key=lambda player_id: player_id.value))
    resolved_commanders = () if commanders is None else tuple(commanders)
    for commander in resolved_commanders:
        if commander.player_id not in canonical_players:
            raise ValueError(
                f"commander player_id {commander.player_id.value!r} is not a "
                "participant in players"
            )
    canonical_commanders = _canonical_commanders(resolved_commanders)

    resolved_resource_pools = () if resource_pools is None else tuple(resource_pools)
    for pool in resolved_resource_pools:
        if pool.player_id not in canonical_players:
            raise ValueError(
                f"resource pool player_id {pool.player_id.value!r} is not a "
                "participant in players"
            )
    canonical_resource_pools = _canonical_resource_pools(resolved_resource_pools)

    resolved_construction_sessions = (
        () if construction_sessions is None else tuple(construction_sessions)
    )
    for session in resolved_construction_sessions:
        if session.player_id not in canonical_players:
            raise ValueError(
                f"construction session player_id {session.player_id.value!r} is not a "
                "participant in players"
            )
    canonical_construction_sessions = _canonical_construction_sessions(
        resolved_construction_sessions
    )

    resolved_robots = () if robots is None else tuple(robots)
    for robot in resolved_robots:
        if robot.owner not in canonical_players:
            raise ValueError(
                f"robot owner {robot.owner.value!r} is not a participant in players"
            )
    canonical_robots = _canonical_robots(resolved_robots)

    resolved_structure_ownership = (
        () if structure_ownership is None else tuple(structure_ownership)
    )
    canonical_structure_ownership = _canonical_structure_ownership(resolved_structure_ownership)

    resolved_capture_progress = () if capture_progress is None else tuple(capture_progress)
    canonical_capture_progress = _canonical_capture_progress(resolved_capture_progress)

    resolved_projectiles = () if projectiles is None else tuple(projectiles)
    canonical_projectiles = _canonical_projectiles(resolved_projectiles)

    return GameState(
        tick=tick,
        players=canonical_players,
        seed=seed,
        commanders=canonical_commanders,
        resource_pools=canonical_resource_pools,
        construction_sessions=canonical_construction_sessions,
        robots=canonical_robots,
        structure_ownership=canonical_structure_ownership,
        capture_progress=canonical_capture_progress,
        projectiles=canonical_projectiles,
    )
