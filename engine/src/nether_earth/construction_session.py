"""Construction-session lifecycle and reversible build editing.

`docs/mechanics/construction.md` and
`_specs/functional-spec.md` §10.3/§11 describe the player-facing construction
flow: a commander lands on its own war base's heli-pad (the
``heli_pad.CommanderConstructionEntryEligible`` event, see ``heli_pad.py``),
enters construction, selects/deselects modules against a temporary resource
buffer, and either launches (`robot_launch.py`) or
cancels/scraps before launch with zero permanent resource impact.

This module implements the *session* layer on top of two primitives,
reusing both rather than reimplementing any of their logic:

- the build model (`robot_build.py`) -- :data:`~nether_earth.robot_build.CHASSIS_MODULES`/
  :data:`~nether_earth.robot_build.WEAPON_MODULES`/
  :data:`~nether_earth.robot_build.ELECTRONICS_MODULES` for incremental
  build-validity checks, and :class:`~nether_earth.robot_build.RobotBuild`
  itself once a build-in-progress is structurally complete;
- the spend/refund algorithm (`construction_economy.py`) --
  :func:`~nether_earth.construction_economy.spend_module`/
  :func:`~nether_earth.construction_economy.refund_module`, called against a
  ``ResourcePool`` snapshot derived from the authoritative
  :class:`~nether_earth.resource_pool.PlayerResourcePool` via
  :meth:`~nether_earth.resource_pool.PlayerResourcePool.to_resource_pool`/
  :meth:`~nether_earth.resource_pool.PlayerResourcePool.from_resource_pool`.

Why a separate "build-in-progress" representation
---------------------------------------------------

:class:`~nether_earth.robot_build.RobotBuild` enforces, in
``__post_init__``, that a build is *already* complete and valid: exactly one
chassis, one to three weapons, at most one electronics module. A
construction session spends most of its life in states that violate that --
zero modules selected yet, a chassis but no weapons yet, two weapons and
still deciding on a third -- so it cannot be represented as a
``RobotBuild`` while incomplete. :class:`BuildInProgress` is the
permissive, session-local representation: an optional chassis, a tuple of
zero to three weapons (order of selection preserved, not yet canonicalized
-- canonicalization is ``RobotBuild.from_modules``'s job, applied only at
the moment a complete build is asked for), and an optional electronics
module. :meth:`BuildInProgress.to_robot_build` converts to a real
:class:`~nether_earth.robot_build.RobotBuild` once (and only once) the
build-in-progress is structurally complete (one chassis, one to three
weapons); callers (the launch logic) are expected to check
:meth:`BuildInProgress.is_complete` first, or handle the
:class:`~nether_earth.robot_build.BuildValidationError` that an incomplete
conversion attempt raises.

Design choice: pure functions, not ``Command`` subclasses
-------------------------------------------------------------

This module's functions (:func:`enter_construction`, :func:`select_module`,
:func:`deselect_module`, :func:`cancel_construction`) are plain, pure,
directly testable functions of ``(state, ...) -> new state`` (or a
rejection outcome), like ``heli_pad.detect_heli_pad_landing`` and
``docking.py``. The ``Command`` subclasses that wrap them live in
`construction_commands.py`, and ``engine.step`` applies them.

Session state and ``GameState`` attachment
---------------------------------------------

:class:`ConstructionSession` attaches to ``GameState`` as
``construction_sessions``, following ``commanders``/``resource_pools``'s
exact "immutable tuple in canonical order (sorted by owning
``player_id.value``), never a ``dict``/``Mapping``-shaped field" convention
from `state.py` -- see that module's docstring and
:meth:`~nether_earth.state.GameState.with_commanders`/
:meth:`~nether_earth.state.GameState.with_resource_pools` for the exact
pattern replicated here as
:meth:`~nether_earth.state.GameState.with_construction_sessions`/
:meth:`~nether_earth.state.GameState.construction_session_for`. A player has
at most one construction session at a time (has an active session, or does
not appear in the tuple at all -- there is no separate "inactive session"
value; "no session for this player" *is* the inactive representation,
avoiding a redundant boolean flag that could disagree with tuple
membership).

Temporary buffer type and the entry-time refund baseline
-------------------------------------------------------------

The temporary resource buffer is stored as a
:class:`~nether_earth.construction_economy.ResourcePool` (the pure
operand type), not a second
:class:`~nether_earth.resource_pool.PlayerResourcePool` -- it is exactly the
type :func:`~nether_earth.construction_economy.spend_module`/
:func:`~nether_earth.construction_economy.refund_module` already operate on,
so no conversion is needed on every select/deselect call (only once, at
entry, converting the player's actual ``PlayerResourcePool`` in, and once,
at successful-launch time -- in `robot_launch.py` -- converting
back out). The entry-time baseline needed by
:func:`~nether_earth.construction_economy.refund_module`'s
``pre_construction_category_amount`` parameter is captured once, at session
entry, as a second, separate ``ResourcePool`` snapshot
(:attr:`ConstructionSession.entry_snapshot`) that is never mutated or
replaced for the life of the session -- every :func:`deselect_module` call
reads the *category amount* it needs out of this fixed snapshot, never out
of the live buffer or a running "amount at time of first spend" value, so
the baseline cannot drift across a sequence of selects/deselects (see
`construction_economy.py`'s own docstring on why it does not track this
value itself).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nether_earth.construction_economy import (
    ResourcePool,
    SpendRejectionReason,
    refund_module,
    spend_module,
)
from nether_earth.heli_pad import CommanderConstructionEntryEligible
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import WorldMap
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.robot_build import (
    CHASSIS_MODULES,
    ELECTRONICS_MODULES,
    WEAPON_MODULES,
    ModuleIdentity,
    RobotBuild,
    resource_category,
)
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "BuildInProgress",
    "ConstructionEntryRejectionReason",
    "ConstructionEntryResult",
    "ConstructionSession",
    "DeselectModuleRejectionReason",
    "DeselectModuleResult",
    "SelectModuleRejectionReason",
    "SelectModuleResult",
    "cancel_construction",
    "deselect_module",
    "enter_construction",
    "enter_construction_remotely",
    "exit_construction",
    "select_module",
]


@dataclass(frozen=True, slots=True)
class BuildInProgress:
    """A permissive, possibly-incomplete robot build, as edited during construction.

    Unlike :class:`~nether_earth.robot_build.RobotBuild`, every field may be
    absent/empty: ``chassis`` may be ``None``, ``weapons`` may hold zero to
    three entries, ``electronics`` may be ``None``. See the module docstring
    for why this separate representation exists. ``weapons`` preserves
    selection order (not canonical stack order) until converted to a real
    :class:`~nether_earth.robot_build.RobotBuild`.
    """

    chassis: ModuleIdentity | None = None
    weapons: tuple[ModuleIdentity, ...] = ()
    electronics: ModuleIdentity | None = None

    def __post_init__(self) -> None:
        if self.chassis is not None and self.chassis not in CHASSIS_MODULES:
            raise ValueError(f"invalid chassis module {self.chassis!r}")
        if len(self.weapons) > 3:
            raise ValueError(f"a build-in-progress may hold at most 3 weapons, got {len(self.weapons)}")
        for weapon in self.weapons:
            if weapon not in WEAPON_MODULES:
                raise ValueError(f"invalid weapon module {weapon!r}")
        if len(set(self.weapons)) != len(self.weapons):
            raise ValueError(f"duplicate weapon module in build-in-progress: {self.weapons!r}")
        if self.electronics is not None and self.electronics not in ELECTRONICS_MODULES:
            raise ValueError(f"invalid electronics module {self.electronics!r}")

    def contains(self, module: ModuleIdentity) -> bool:
        """Return whether ``module`` is currently selected in this build-in-progress."""
        return module == self.chassis or module == self.electronics or module in self.weapons

    def modules(self) -> tuple[ModuleIdentity, ...]:
        """Return every currently-selected module identity, in selection-relevant order."""
        modules: list[ModuleIdentity] = []
        if self.chassis is not None:
            modules.append(self.chassis)
        modules.extend(self.weapons)
        if self.electronics is not None:
            modules.append(self.electronics)
        return tuple(modules)

    def is_complete(self) -> bool:
        """Return whether this build-in-progress is structurally a complete build.

        Exactly one chassis and one to three weapons (electronics is always
        optional, per `_specs/functional-spec.md` §11) -- the same
        completeness bar :meth:`to_robot_build` enforces via
        :class:`~nether_earth.robot_build.RobotBuild`.
        """
        return self.chassis is not None and 1 <= len(self.weapons) <= 3

    def with_module_added(self, module: ModuleIdentity) -> BuildInProgress:
        """Return a copy with ``module`` added. Caller must have validated legality first."""
        if module in CHASSIS_MODULES:
            return BuildInProgress(chassis=module, weapons=self.weapons, electronics=self.electronics)
        if module in WEAPON_MODULES:
            return BuildInProgress(
                chassis=self.chassis, weapons=(*self.weapons, module), electronics=self.electronics
            )
        return BuildInProgress(chassis=self.chassis, weapons=self.weapons, electronics=module)

    def with_module_removed(self, module: ModuleIdentity) -> BuildInProgress:
        """Return a copy with ``module`` removed. Caller must have validated presence first."""
        if module in CHASSIS_MODULES:
            return BuildInProgress(chassis=None, weapons=self.weapons, electronics=self.electronics)
        if module in WEAPON_MODULES:
            return BuildInProgress(
                chassis=self.chassis,
                weapons=tuple(w for w in self.weapons if w != module),
                electronics=self.electronics,
            )
        return BuildInProgress(chassis=self.chassis, weapons=self.weapons, electronics=None)

    def to_robot_build(self) -> RobotBuild:
        """Convert to a complete :class:`~nether_earth.robot_build.RobotBuild`.

        Raises :class:`~nether_earth.robot_build.BuildValidationError` if
        this build-in-progress is not yet structurally complete (see
        :meth:`is_complete`) -- callers that need a non-raising check should
        call :meth:`is_complete` first.
        """
        return RobotBuild.from_modules(self.modules())


@dataclass(frozen=True, slots=True)
class ConstructionSession:
    """An active, in-progress construction session for one player.

    ``player_id`` is the owning player (mirrors ``Commander``/
    ``PlayerResourcePool``'s "the entity carries its own owning player id"
    convention). ``war_base_id`` is the id of the war base whose heli-pad
    landing (the triggering
    :class:`~nether_earth.heli_pad.CommanderConstructionEntryEligible` event)
    opened this session -- carried here so the launch logic has it
    without a second lookup. ``entry_tick`` records the tick the triggering
    event fired on (replay/debugging aid, mirrors the event's own ``tick``
    field). ``build`` is the current :class:`BuildInProgress`. ``buffer`` is
    the temporary, session-local :class:`~nether_earth.construction_economy.ResourcePool`
    every select/deselect spends/refunds against -- never the player's
    actual :class:`~nether_earth.resource_pool.PlayerResourcePool`.
    ``entry_snapshot`` is the fixed, never-mutated snapshot of the player's
    actual resource pool (converted) taken at entry time, used as
    :func:`~nether_earth.construction_economy.refund_module`'s
    per-category refund baseline for the entire life of the session -- see
    the module docstring for why this must never drift.

    Frozen/slotted and structurally equatable by value like every other
    ``GameState``-attached type in this codebase, so session state is
    snapshot/replay-safe by construction.
    """

    player_id: PlayerId
    war_base_id: EntityId
    entry_tick: int
    build: BuildInProgress
    buffer: ResourcePool
    entry_snapshot: ResourcePool


class ConstructionEntryRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected :func:`enter_construction` call."""

    WRONG_PLAYER = "wrong_player"
    NOT_OWN_WAR_BASE = "not_own_war_base"
    ALREADY_IN_SESSION = "already_in_session"
    UNKNOWN_PLAYER = "unknown_player"
    NOT_AI_SEAT = "not_ai_seat"


@dataclass(frozen=True, slots=True)
class ConstructionEntryResult:
    """Outcome of :func:`enter_construction`.

    Mirrors ``construction_economy.SpendResult``'s accept/reject shape:
    exactly one of "accepted, carrying the resulting state" or "a stable
    rejection reason" holds.
    """

    accepted: bool
    state: GameState | None = None
    reason: ConstructionEntryRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and (self.state is None or self.reason is not None):
            raise ValueError("an accepted ConstructionEntryResult must carry a state and no rejection reason")
        if not self.accepted and (self.state is not None or self.reason is None):
            raise ValueError("a rejected ConstructionEntryResult must carry a reason and no state")

    @classmethod
    def accept(cls, state: GameState) -> ConstructionEntryResult:
        return cls(accepted=True, state=state, reason=None)

    @classmethod
    def reject(cls, reason: ConstructionEntryRejectionReason) -> ConstructionEntryResult:
        return cls(accepted=False, state=None, reason=reason)


def enter_construction(
    state: GameState,
    event: CommanderConstructionEntryEligible,
    for_player: PlayerId,
) -> ConstructionEntryResult:
    """Enter a construction session for ``for_player`` in direct response to ``event``.

    This is the *only* way a :class:`ConstructionSession` may be created:
    the caller must supply the exact triggering
    :class:`~nether_earth.heli_pad.CommanderConstructionEntryEligible` event
    (from ``heli_pad.detect_heli_pad_landing``) rather than this function
    re-deriving landing eligibility itself -- see the module docstring.
    ``for_player`` must equal ``event.player``; this is a defensive
    re-check (not a re-derivation of heli-pad ownership -- the event's
    own invariant already guarantees ``event.player`` owns
    ``event.war_base_id``) against a forged/mismatched call where a caller
    passes another player's event, since trusting ``event.player`` alone
    without confirming it is who the caller actually intends to enter a
    session for would let a stale/mismatched event silently open a session
    for the wrong player.

    Rejects (no state change) if:

    - ``for_player`` does not match ``event.player``
      (:data:`ConstructionEntryRejectionReason.WRONG_PLAYER`);
    - ``for_player`` is not a participant in ``state``
      (:data:`ConstructionEntryRejectionReason.UNKNOWN_PLAYER`);
    - ``for_player`` already has an active construction session
      (:data:`ConstructionEntryRejectionReason.ALREADY_IN_SESSION`).

    On success, the player's actual
    :class:`~nether_earth.resource_pool.PlayerResourcePool` (looked up via
    :meth:`~nether_earth.state.GameState.resource_pool_for`, defaulting to a
    zeroed pool if the player has none recorded yet) is snapshotted once,
    converted to a :class:`~nether_earth.construction_economy.ResourcePool`,
    and used as both the fresh temporary buffer's starting point and the
    fixed refund baseline -- ``state.resource_pools`` itself is never
    written to by this function.
    """
    if for_player != event.player:
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.WRONG_PLAYER)
    if for_player not in state.players:
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.UNKNOWN_PLAYER)
    if state.construction_session_for(for_player) is not None:
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.ALREADY_IN_SESSION)

    return ConstructionEntryResult.accept(_open_session(state, for_player, event.war_base_id, event.tick))


def enter_construction_remotely(
    state: GameState,
    world: WorldMap,
    for_player: PlayerId,
    war_base_id: EntityId,
    tick: int,
) -> ConstructionEntryResult:
    """Open a construction session at ``war_base_id`` for an AI seat, with no commander.

    The AI seat has no commander (owner decision 2026-09-25), so it can
    never raise :class:`~nether_earth.heli_pad.CommanderConstructionEntryEligible`.
    This is its entry instead, keyed on *owning* the war base rather than
    landing on it. It opens exactly the session :func:`enter_construction`
    opens, so select/deselect/cancel/launch, costs and legality are shared.

    ``world`` must be the effective world (captures and destruction applied),
    so a captured or destroyed war base is not the seat's. Rejects (no state
    change) if:

    - ``for_player`` is not a participant
      (:data:`ConstructionEntryRejectionReason.UNKNOWN_PLAYER`);
    - ``for_player`` is a human seat -- a human still has to land
      (:data:`ConstructionEntryRejectionReason.NOT_AI_SEAT`);
    - ``for_player`` does not own ``war_base_id`` in ``world``
      (:data:`ConstructionEntryRejectionReason.NOT_OWN_WAR_BASE`);
    - ``for_player`` already has an active session
      (:data:`ConstructionEntryRejectionReason.ALREADY_IN_SESSION`).
    """
    if for_player not in state.players:
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.UNKNOWN_PLAYER)
    if state.ai_memory_for(for_player) is None:
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.NOT_AI_SEAT)
    if not any(base.id == war_base_id and base.owner == for_player for base in world.war_bases):
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.NOT_OWN_WAR_BASE)
    if state.construction_session_for(for_player) is not None:
        return ConstructionEntryResult.reject(ConstructionEntryRejectionReason.ALREADY_IN_SESSION)
    return ConstructionEntryResult.accept(_open_session(state, for_player, war_base_id, tick))


def _open_session(state: GameState, player_id: PlayerId, war_base_id: EntityId, tick: int) -> GameState:
    """Return ``state`` with a fresh session for ``player_id``; callers have validated entry."""
    actual_pool = state.resource_pool_for(player_id)
    snapshot = (
        actual_pool.to_resource_pool()
        if actual_pool is not None
        else PlayerResourcePool(player_id=player_id).to_resource_pool()
    )

    session = ConstructionSession(
        player_id=player_id,
        war_base_id=war_base_id,
        entry_tick=tick,
        build=BuildInProgress(),
        buffer=snapshot,
        entry_snapshot=snapshot,
    )
    return state.with_construction_sessions((*state.construction_sessions, session))


class SelectModuleRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected :func:`select_module` call."""

    NO_ACTIVE_SESSION = "no_active_session"
    DUPLICATE_MODULE = "duplicate_module"
    ELECTRONICS_ALREADY_SELECTED = "electronics_already_selected"
    WEAPON_CAP_REACHED = "weapon_cap_reached"
    INSUFFICIENT_RESOURCES = "insufficient_resources"


@dataclass(frozen=True, slots=True)
class SelectModuleResult:
    """Outcome of :func:`select_module`. Accept/reject shape, mirrors :class:`ConstructionEntryResult`.

    ``removed_chassis`` is set when picking a chassis replaced a fitted one.
    The Spectrum removes and refunds the old chassis *before*
    trying to pay for the new one, so a swap whose new chassis is then
    unaffordable is a rejection that still carries a ``state``: the old
    chassis removed and refunded, the new one not fitted. That is the only
    rejection with a ``state``.
    """

    accepted: bool
    state: GameState | None = None
    reason: SelectModuleRejectionReason | None = None
    removed_chassis: ModuleIdentity | None = None

    def __post_init__(self) -> None:
        if self.accepted and (self.state is None or self.reason is not None):
            raise ValueError("an accepted SelectModuleResult must carry a state and no rejection reason")
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected SelectModuleResult must carry a reason")
        if not self.accepted and (self.state is None) != (self.removed_chassis is None):
            raise ValueError("a rejected SelectModuleResult carries a state only when it removed a chassis")

    @classmethod
    def accept(cls, state: GameState, removed_chassis: ModuleIdentity | None = None) -> SelectModuleResult:
        return cls(accepted=True, state=state, reason=None, removed_chassis=removed_chassis)

    @classmethod
    def reject(
        cls,
        reason: SelectModuleRejectionReason,
        state: GameState | None = None,
        removed_chassis: ModuleIdentity | None = None,
    ) -> SelectModuleResult:
        return cls(accepted=False, state=state, reason=reason, removed_chassis=removed_chassis)


def select_module(
    state: GameState,
    player_id: PlayerId,
    module: ModuleIdentity,
    rules: EngineRules = DEFAULT_RULES,
) -> SelectModuleResult:
    """Select ``module`` into ``player_id``'s active construction session.

    Validates incrementally against
    :class:`~nether_earth.robot_build.RobotBuild`'s eventual constraints
    (`_specs/functional-spec.md` §11) before spending anything: no
    duplicate module identity, no second electronics, no more than three
    weapons. Only once the selection is structurally legal is
    :func:`~nether_earth.construction_economy.spend_module` called against
    the session's temporary buffer; an
    :data:`~nether_earth.construction_economy.SpendRejectionReason.INSUFFICIENT_RESOURCES`
    result from that call is surfaced as
    :data:`SelectModuleRejectionReason.INSUFFICIENT_RESOURCES` here.

    Picking a chassis while another is fitted swaps it, exactly
    as the Spectrum's ``Lca0f_waiting_for_key_press_loop`` does: the fitted
    chassis is first refunded into the buffer
    (``Lcac1_update_resources_buffer_when_removing_a_piece``, i.e.
    :func:`~nether_earth.construction_economy.refund_module` against the
    entry snapshot) and removed from the build, then the new chassis is paid
    for as an ordinary add (``Lca57_construction_add_piece``). If the new
    chassis is unaffordable even after that refund, the Spectrum beeps
    (``Lcaac``) *without* restoring the old chassis, so the result is a
    rejection whose ``state`` has no chassis and the old one refunded (see
    :class:`SelectModuleResult`). Weapons and electronics are untouched.
    Every other rejection leaves ``state`` completely unchanged.

    Never touches ``state.resource_pools`` (the player's actual pool); only
    the session's ``buffer``/``build`` are updated.
    """
    session = state.construction_session_for(player_id)
    if session is None:
        return SelectModuleResult.reject(SelectModuleRejectionReason.NO_ACTIVE_SESSION)

    build = session.build
    if build.contains(module):
        return SelectModuleResult.reject(SelectModuleRejectionReason.DUPLICATE_MODULE)
    if module in ELECTRONICS_MODULES and build.electronics is not None:
        return SelectModuleResult.reject(SelectModuleRejectionReason.ELECTRONICS_ALREADY_SELECTED)
    if module in WEAPON_MODULES and len(build.weapons) >= 3:
        return SelectModuleResult.reject(SelectModuleRejectionReason.WEAPON_CAP_REACHED)

    buffer = session.buffer
    removed_chassis = build.chassis if module in CHASSIS_MODULES else None
    if removed_chassis is not None:
        pre_construction_amount = session.entry_snapshot.amount(resource_category(removed_chassis))
        buffer = refund_module(buffer, removed_chassis, rules, pre_construction_amount)
        build = build.with_module_removed(removed_chassis)

    spend_result = spend_module(buffer, module, rules)
    if not spend_result.accepted:
        assert spend_result.reason is SpendRejectionReason.INSUFFICIENT_RESOURCES
        if removed_chassis is None:
            return SelectModuleResult.reject(SelectModuleRejectionReason.INSUFFICIENT_RESOURCES)
        return SelectModuleResult.reject(
            SelectModuleRejectionReason.INSUFFICIENT_RESOURCES,
            state=_replace_session(state, _with_build(session, build, buffer)),
            removed_chassis=removed_chassis,
        )

    assert spend_result.pool is not None
    new_session = _with_build(session, build.with_module_added(module), spend_result.pool)
    return SelectModuleResult.accept(_replace_session(state, new_session), removed_chassis)


def _with_build(session: ConstructionSession, build: BuildInProgress, buffer: ResourcePool) -> ConstructionSession:
    """Return ``session`` with a new build-in-progress and temporary buffer."""
    return ConstructionSession(
        player_id=session.player_id,
        war_base_id=session.war_base_id,
        entry_tick=session.entry_tick,
        build=build,
        buffer=buffer,
        entry_snapshot=session.entry_snapshot,
    )


class DeselectModuleRejectionReason(str, Enum):
    """Stable, serializable reason codes for a rejected :func:`deselect_module` call."""

    NO_ACTIVE_SESSION = "no_active_session"
    MODULE_NOT_SELECTED = "module_not_selected"


@dataclass(frozen=True, slots=True)
class DeselectModuleResult:
    """Outcome of :func:`deselect_module`. Accept/reject shape, mirrors :class:`ConstructionEntryResult`."""

    accepted: bool
    state: GameState | None = None
    reason: DeselectModuleRejectionReason | None = None

    def __post_init__(self) -> None:
        if self.accepted and (self.state is None or self.reason is not None):
            raise ValueError("an accepted DeselectModuleResult must carry a state and no rejection reason")
        if not self.accepted and (self.state is not None or self.reason is None):
            raise ValueError("a rejected DeselectModuleResult must carry a reason and no state")

    @classmethod
    def accept(cls, state: GameState) -> DeselectModuleResult:
        return cls(accepted=True, state=state, reason=None)

    @classmethod
    def reject(cls, reason: DeselectModuleRejectionReason) -> DeselectModuleResult:
        return cls(accepted=False, state=None, reason=reason)


def deselect_module(
    state: GameState,
    player_id: PlayerId,
    module: ModuleIdentity,
    rules: EngineRules = DEFAULT_RULES,
) -> DeselectModuleResult:
    """Deselect ``module`` from ``player_id``'s active construction session.

    Rejects (no state change) if there is no active session for
    ``player_id``, or ``module`` is not currently selected in the session's
    build-in-progress. Otherwise calls
    :func:`~nether_earth.construction_economy.refund_module` against the
    session's temporary buffer, always passing the session's fixed
    ``entry_snapshot`` category amount (never the live buffer's current
    amount) as the ``pre_construction_category_amount`` baseline -- this is
    the exact reversal rule `construction_economy.py` locks, reused verbatim (see the module
    docstring for why the baseline must never drift across the session).

    Never touches ``state.resource_pools`` (the player's actual pool); only
    the session's ``buffer``/``build`` are updated.
    """
    session = state.construction_session_for(player_id)
    if session is None:
        return DeselectModuleResult.reject(DeselectModuleRejectionReason.NO_ACTIVE_SESSION)

    if not session.build.contains(module):
        return DeselectModuleResult.reject(DeselectModuleRejectionReason.MODULE_NOT_SELECTED)

    category = resource_category(module)
    pre_construction_category_amount = session.entry_snapshot.amount(category)

    refunded_pool = refund_module(session.buffer, module, rules, pre_construction_category_amount)

    new_session = ConstructionSession(
        player_id=session.player_id,
        war_base_id=session.war_base_id,
        entry_tick=session.entry_tick,
        build=session.build.with_module_removed(module),
        buffer=refunded_pool,
        entry_snapshot=session.entry_snapshot,
    )
    return DeselectModuleResult.accept(_replace_session(state, new_session))


def cancel_construction(state: GameState, player_id: PlayerId) -> GameState:
    """Discard ``player_id``'s active construction session, if any.

    Pure removal: the session (its build-in-progress and temporary buffer)
    is simply dropped from ``state.construction_sessions``.
    ``state.resource_pools`` -- the player's actual, permanent resource
    pool -- is never read or written by this function, so it is trivially
    left byte-for-byte unchanged (this module never writes to
    ``resource_pools`` anywhere, including during select/deselect -- see
    :func:`select_module`/:func:`deselect_module`'s docstrings). A no-op
    (still returns ``state`` with no session for ``player_id``, which is
    already true) if ``player_id`` has no active session.
    """
    remaining = tuple(s for s in state.construction_sessions if s.player_id != player_id)
    if remaining == state.construction_sessions:
        return state
    return state.with_construction_sessions(remaining)


def exit_construction(
    state: GameState, player_id: PlayerId, rules: EngineRules = DEFAULT_RULES
) -> GameState:
    """Leave ``player_id``'s construction screen: drop the session and start the exit ascent.

    Spectrum semantics: EXIT MENU
    (``Lcb8e_construction_screen_exit``) discards the build-in-progress
    (the resource buffer is only copied to the player on START ROBOT) and
    sets ``Lfd30_player_elevate_timer`` to 5, so the ship automatically
    ascends for that many vertical updates before gravity applies again.
    START ROBOT (``Lcb52_construction_screen_start_robot``) falls through to
    the same exit after committing the robot, which is why
    :func:`~nether_earth.robot_launch.launch_robot` calls this too.

    The session removal is :func:`cancel_construction` (``resource_pools``
    untouched). The player's commander gets
    ``rules.commander_exit_elevate_updates`` automatic-ascent
    updates; while any remain, the commander does not re-enter construction
    (``engine.step`` Step 7), so it lifts off the pad instead of re-opening
    the screen on the next tick. A no-op if ``player_id`` has no session.
    """
    if state.construction_session_for(player_id) is None:
        return state
    state = cancel_construction(state, player_id)
    commander = state.commander_for(player_id)
    if commander is None:
        return state
    lifted = commander.with_elevate_updates(rules.commander_exit_elevate_updates)
    return state.with_commanders(
        tuple(lifted if c.player_id == player_id else c for c in state.commanders)
    )


def _replace_session(state: GameState, session: ConstructionSession) -> GameState:
    """Return ``state`` with ``session`` replacing any existing session for its player."""
    remaining = tuple(s for s in state.construction_sessions if s.player_id != session.player_id)
    return state.with_construction_sessions((*remaining, session))
