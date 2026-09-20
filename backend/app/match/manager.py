"""``MatchManager``: in-memory match/session lifecycle owner.

Scope (issue #92 / M7 Task 2): create/join/ready lifecycle, guest session
tokens, and the ``WAITING`` -> ``ACTIVE`` transition that calls
``nether_earth.engine.new_game`` exactly once. No WebSocket I/O, no
fixed-tick stepping (``engine.step`` is never called from this module --
that is ``app.match.runtime.MatchRuntime``'s job, optionally wired in via
this class's ``runtime`` constructor parameter, M7 Task 4/issue #93), no
transport (Pydantic) models -- those are separate M7 tasks (3, 4, 5).

Architecture note (AGENTS.md, non-negotiable): this module never implements
or checks gameplay rules. It calls ``engine.new_game`` once, at match start,
and otherwise only manages bookkeeping (ids, tokens, readiness, runtime
state) that has nothing to do with gameplay legality.
"""

from __future__ import annotations

import secrets
import string
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from nether_earth import engine as engine_module
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.map import BootstrapMap
from nether_earth.scenario import Scenario, default_pvp_scenario

from app.match.models import (
    InvalidNicknameError,
    InvalidSessionTokenError,
    Match,
    MatchFullError,
    MatchNotFoundError,
    MatchRuntimeState,
    PlayerSlot,
)
from app.match.reconnect import ReconnectCoordinator
from app.match.runtime import MatchRuntimeRegistry, TickObserver

#: Deterministic v1 two-player seat order: the first guest to create/join a
#: match always takes PLAYER_ONE, the second always takes PLAYER_TWO. This
#: mirrors `nether_earth.scenario.initialize_players`'s own fixed v1 pairing
#: so the resulting player set matches exactly what `engine.new_game` would
#: derive on its own.
_SEAT_ORDER: tuple[PlayerId, ...] = (PLAYER_ONE, PLAYER_TWO)

_JOIN_CODE_ALPHABET = string.ascii_uppercase + string.digits
_JOIN_CODE_LENGTH = 6


def _default_bootstrap_map(scenario: Scenario) -> BootstrapMap:
    """Return a ``BootstrapMap`` structurally consistent with ``scenario``.

    Deliberately does **not** load a real map file from ``data/maps/`` here:
    this task only reaches ``engine.new_game``, whose sole use of
    ``map_data`` is the ``map_id``/``version`` cross-check against
    ``scenario`` (see ``engine.py``'s docstring) -- no terrain/geometry is
    consumed. Real map-file loading/deployment-path resolution (the
    ``data/maps/`` directory is not currently shipped into the backend's
    Docker image, and there is no existing backend config surface for it)
    is a wiring concern for whichever later task first needs a real
    ``WorldMap`` for ``engine.step`` (Task 4's fixed-tick runtime). Callers
    that already have a properly loaded ``BootstrapMap``/``WorldMap`` should
    pass one to ``MatchManager.__init__`` instead of relying on this
    placeholder.
    """
    return BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)


@dataclass(frozen=True, slots=True)
class CreateMatchResult:
    match_id: str
    join_code: str
    session_token: str
    player_id: PlayerId


@dataclass(frozen=True, slots=True)
class JoinMatchResult:
    match_id: str
    session_token: str
    player_id: PlayerId


class MatchManager:
    """Owns every in-memory ``Match``, keyed by match id, join code, and session token.

    Not async-aware and not thread-hostile-safe beyond a single coarse lock
    guarding its own bookkeeping dictionaries -- callers issuing concurrent
    create/join/ready calls across *different* matches do not block each
    other's gameplay (there is none here yet), only the bookkeeping mutation
    itself is serialized. This is a plain, synchronous, deterministic
    lifecycle layer; nothing here awaits or does network I/O.

    ``runtime``, if supplied, is an optional hook (M7 Task 4, issue #93) into
    the async fixed-tick layer: the ``WAITING`` -> ``ACTIVE`` transition
    starts a ``MatchRuntime`` for the match, and ``finish_match``/
    ``dispose_match`` cancel it. This class never awaits anything itself --
    ``MatchRuntimeRegistry``'s start/cancel/dispose methods are synchronous
    and non-blocking (see ``runtime.py``'s module docstring for why), so
    passing ``runtime=None`` (the default) keeps this class exactly as
    synchronous/event-loop-free as it was before Task 4 existed.

    ``on_tick_factory``, if supplied (M7 Task 6, issue #95), is called once
    with the ``Match`` being started, at the moment its runtime starts; its
    return value (a ``TickObserver`` or ``None``) is passed to
    ``MatchRuntimeRegistry.start``. A factory rather than one shared
    ``TickObserver`` so the broadcast callback can bind ``match.match_id``
    via closure -- this class still never imports ``app.transport``: the
    factory's return type is `runtime.py`'s own ``TickObserver``, and its
    concrete body is supplied by whoever constructs this class
    (``app.main``).

    ``reconnect``, if supplied (M7 Task 7, issue #96), is the async
    disconnect/reconnect-grace policy layer: ``mark_disconnected``/
    ``mark_reconnected`` delegate to it, and ``finish_match``/
    ``dispose_match`` cancel its pending deadline-watcher tasks for the
    match, mirroring the ``runtime`` parameter's own start/cancel/dispose
    wiring. Like ``runtime``, this class never awaits anything itself --
    ``ReconnectCoordinator``'s public methods are synchronous facades over
    asyncio internals (see its module docstring), so passing
    ``reconnect=None`` (the default) keeps this class exactly as
    synchronous/event-loop-free as before Task 7 existed.
    """

    def __init__(
        self,
        *,
        scenario: Scenario | None = None,
        map_data: BootstrapMap | None = None,
        runtime: MatchRuntimeRegistry | None = None,
        on_tick_factory: Callable[[Match], TickObserver | None] | None = None,
        reconnect: ReconnectCoordinator | None = None,
    ) -> None:
        self._scenario = scenario if scenario is not None else default_pvp_scenario()
        self._map_data = map_data if map_data is not None else _default_bootstrap_map(self._scenario)
        self._runtime = runtime
        self._on_tick_factory = on_tick_factory
        self._reconnect = reconnect
        self._lock = threading.Lock()
        self._matches: dict[str, Match] = {}
        self._match_id_by_join_code: dict[str, str] = {}
        self._match_id_by_session_token: dict[str, str] = {}

    # -- creation / join --------------------------------------------------

    def create_match(self, nickname: str, *, seed: int | None = None) -> CreateMatchResult:
        """Create a new ``WAITING`` match with ``nickname`` as its first (PLAYER_ONE) slot."""
        nickname = _validate_nickname(nickname)
        with self._lock:
            match_id = uuid.uuid4().hex
            join_code = self._generate_unique_join_code()
            match_seed = seed if seed is not None else secrets.randbits(63)
            session_token = _generate_session_token()

            match = Match(match_id=match_id, join_code=join_code, seed=match_seed)
            match.players[PLAYER_ONE] = PlayerSlot(
                player_id=PLAYER_ONE, nickname=nickname, session_token=session_token
            )

            self._matches[match_id] = match
            self._match_id_by_join_code[join_code] = match_id
            self._match_id_by_session_token[session_token] = match_id

        return CreateMatchResult(
            match_id=match_id,
            join_code=join_code,
            session_token=session_token,
            player_id=PLAYER_ONE,
        )

    def join_match(self, join_code: str, nickname: str) -> JoinMatchResult:
        """Join the second (PLAYER_TWO) slot of the match identified by ``join_code``.

        Raises ``MatchNotFoundError`` for an unknown code and ``MatchFullError``
        if the match already has two players (v1 caps every match at exactly
        two guest slots).
        """
        nickname = _validate_nickname(nickname)
        with self._lock:
            match_id = self._match_id_by_join_code.get(join_code)
            if match_id is None:
                raise MatchNotFoundError(f"no match for join code {join_code!r}")
            match = self._matches[match_id]
            if match.is_full:
                raise MatchFullError(f"match {match_id!r} already has two players")

            session_token = _generate_session_token()
            match.players[PLAYER_TWO] = PlayerSlot(
                player_id=PLAYER_TWO, nickname=nickname, session_token=session_token
            )
            self._match_id_by_session_token[session_token] = match_id

        return JoinMatchResult(
            match_id=match_id, session_token=session_token, player_id=PLAYER_TWO
        )

    # -- readiness / start --------------------------------------------------

    def set_ready(self, session_token: str, ready: bool = True) -> Match:
        """Set the ready flag for the player owning ``session_token``.

        When this call causes both slots to be ready (and the match is still
        ``WAITING``), the match transitions to ``ACTIVE`` and
        ``engine.new_game`` is called exactly once to produce the match's
        authoritative tick-0 ``GameState``. Toggling readiness back off after
        the match has already gone ``ACTIVE`` has no effect on the already-
        started engine state (there is no "un-start" in v1).
        """
        with self._lock:
            match, player_id = self._resolve_session_locked(session_token)
            match.players[player_id].ready = ready

            if match.state is MatchRuntimeState.WAITING and match.all_ready:
                self._start_match_locked(match)

        return match

    def _start_match_locked(self, match: Match) -> None:
        """Run the WAITING -> ACTIVE transition exactly once for ``match``.

        Caller must hold ``self._lock``. Guarded by ``match.state`` (rather
        than e.g. a separate boolean) so this can never run twice for the
        same match even under concurrent ``set_ready`` calls: the state
        change to ``ACTIVE`` happens before releasing the lock, and every
        caller checks ``match.state is WAITING`` before invoking this.
        """
        players = tuple(_SEAT_ORDER)
        match.game_state = engine_module.new_game(
            self._map_data, self._scenario, players=players, seed=match.seed
        )
        match.state = MatchRuntimeState.ACTIVE
        if self._runtime is not None:
            # Whenever `on_tick_factory` actually produced an observer,
            # require an explicit `announce_started()` (see `runtime.py`)
            # instead of a timer -- the caller that supplied it (the
            # transport layer) is expected to call
            # `MatchRuntimeRegistry.announce_started(match.match_id)` itself
            # once its own "match started" messaging is sent, structurally
            # ruling out that observer's first call racing that messaging.
            on_tick = self._on_tick_factory(match) if self._on_tick_factory is not None else None
            self._runtime.start(
                match, on_tick=on_tick, require_announcement=on_tick is not None
            )

    # -- lifecycle end / disposal --------------------------------------------

    def finish_match(self, match_id: str) -> Match:
        """Mark ``match_id`` as ``FINISHED``. Does not remove it from the manager.

        Idempotent: finishing an already-``FINISHED`` match is a no-op rather
        than an error, matching the "match result is emitted once and cannot
        oscillate/reopen" spirit already locked at the engine level.
        """
        with self._lock:
            match = self._get_match_locked(match_id)
            match.state = MatchRuntimeState.FINISHED
            if self._runtime is not None:
                self._runtime.cancel(match_id)
            if self._reconnect is not None:
                self._reconnect.cancel(match_id)
        return match

    def dispose_match(self, match_id: str) -> None:
        """Explicit disposal hook: remove ``match_id`` and all its indices.

        No persistence happens here or anywhere else in this module (replay
        persistence is Task 8's filesystem writer, invoked by the runtime
        layer *before* disposal, not by ``MatchManager`` itself). Raises
        ``MatchNotFoundError`` for an unknown id so a caller cannot silently
        double-dispose.
        """
        with self._lock:
            match = self._get_match_locked(match_id)
            del self._matches[match_id]
            self._match_id_by_join_code.pop(match.join_code, None)
            for slot in match.players.values():
                self._match_id_by_session_token.pop(slot.session_token, None)
            if self._runtime is not None:
                self._runtime.dispose(match_id)
            if self._reconnect is not None:
                self._reconnect.dispose(match_id)

    # -- lookup ---------------------------------------------------------------

    def get_match(self, match_id: str) -> Match:
        """Return the match for ``match_id``, or raise ``MatchNotFoundError``."""
        with self._lock:
            return self._get_match_locked(match_id)

    def get_match_by_join_code(self, join_code: str) -> Match:
        """Return the match for ``join_code``, or raise ``MatchNotFoundError``."""
        with self._lock:
            match_id = self._match_id_by_join_code.get(join_code)
            if match_id is None:
                raise MatchNotFoundError(f"no match for join code {join_code!r}")
            return self._matches[match_id]

    def resolve_session(self, session_token: str) -> tuple[Match, PlayerId]:
        """Resolve ``session_token`` to its owning ``(match, player_id)`` pair.

        Raises ``InvalidSessionTokenError`` for a token that does not (or no
        longer, e.g. after ``dispose_match``) resolve to any match/slot. A
        token issued for one match/slot can never resolve to a different
        match or the other player's slot -- the mapping is fixed at
        create/join time and never reassigned.
        """
        with self._lock:
            return self._resolve_session_locked(session_token)

    def mark_disconnected(self, session_token: str) -> None:
        """Record that the connection owning ``session_token`` has closed.

        Delegates to the ``reconnect`` policy layer (M7 Task 7, issue #96),
        if one was supplied: it pauses the match on the first currently-
        disconnected player and starts that player's reconnect grace timer
        (see ``ReconnectCoordinator.mark_disconnected``). With
        ``reconnect=None`` this remains the pre-Task-7 no-op bookkeeping
        call (e.g. tests that only need session/lifecycle bookkeeping and no
        asyncio at all). An unknown token (e.g. notification for an
        already-disposed match) is silently ignored rather than raising,
        since "the match is already gone" is an expected, non-exceptional
        outcome for a disconnect notification.
        """
        with self._lock:
            match, player_id = self._lookup_session_locked(session_token)
            if match is None or player_id is None:
                return
            if self._reconnect is not None:
                self._reconnect.mark_disconnected(match, player_id)

    def mark_reconnected(self, session_token: str) -> None:
        """Record that the connection owning ``session_token`` has reattached.

        Delegates to the ``reconnect`` policy layer (M7 Task 7, issue #96),
        if one was supplied: it cancels that player's reconnect grace timer
        and, once both players are connected again, resumes the match (see
        ``ReconnectCoordinator.mark_reconnected``). A no-op (like
        ``mark_disconnected``) for an unknown token or ``reconnect=None``.

        Callers (``app.transport.ws``'s ``ClientReconnect`` handling) are
        expected to call this once per successful reconnect, in addition to
        -- not instead of -- registering the new socket with
        ``ConnectionRegistry`` and sending the resync snapshot; this method
        touches only lifecycle/pause bookkeeping, never the connection
        table or any wire message itself.
        """
        with self._lock:
            match, player_id = self._lookup_session_locked(session_token)
            if match is None or player_id is None:
                return
            if self._reconnect is not None:
                self._reconnect.mark_reconnected(match, player_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._matches)

    # -- internal helpers (caller must hold self._lock) ------------------------

    def _get_match_locked(self, match_id: str) -> Match:
        match = self._matches.get(match_id)
        if match is None:
            raise MatchNotFoundError(f"no match with id {match_id!r}")
        return match

    def _resolve_session_locked(self, session_token: str) -> tuple[Match, PlayerId]:
        match_id = self._match_id_by_session_token.get(session_token)
        if match_id is None:
            raise InvalidSessionTokenError("session token does not resolve to any match")
        match = self._matches[match_id]
        slot = match.slot_for_token(session_token)
        if slot is None:
            # Defensive: the reverse index should never point at a match
            # whose slot no longer carries this token. Not reachable through
            # this class's own public API today, but guarded rather than
            # silently returning a wrong slot if that ever changes.
            raise InvalidSessionTokenError("session token does not resolve to any match")
        return match, slot.player_id

    def _lookup_session_locked(self, session_token: str) -> tuple[Match | None, PlayerId | None]:
        """Best-effort, non-raising counterpart to ``_resolve_session_locked``.

        Used only by ``mark_disconnected``/``mark_reconnected``: an unknown
        or stale token is an expected, silently-ignored outcome for a
        connection-lifecycle notification (unlike ``resolve_session``, which
        is used for message *authorization* and must raise loudly on a bad
        token).
        """
        match_id = self._match_id_by_session_token.get(session_token)
        if match_id is None:
            return None, None
        match = self._matches.get(match_id)
        if match is None:
            return None, None
        slot = match.slot_for_token(session_token)
        if slot is None:
            return None, None
        return match, slot.player_id

    def _generate_unique_join_code(self) -> str:
        for _ in range(100):
            code = "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(_JOIN_CODE_LENGTH))
            if code not in self._match_id_by_join_code:
                return code
        raise RuntimeError("failed to generate a unique join code after 100 attempts")


def _generate_session_token() -> str:
    """Return an opaque, unguessable per-player session/reconnect token."""
    return secrets.token_urlsafe(32)


def _validate_nickname(nickname: str) -> str:
    nickname = nickname.strip()
    if not nickname:
        raise InvalidNicknameError("nickname must be a non-empty string")
    return nickname
