"""Tests for ``app.match.reconnect`` (M7 Task 7, issue #96).

Covers every scenario in the locked disconnect/reconnect policy:

- first disconnect pauses immediately, tick count frozen while paused;
- a second disconnect (both players gone) does not re-pause or re-notify;
- reconnect only resumes once BOTH players are connected;
- lone grace-deadline expiry forfeits the disconnected player;
- both-disconnected, one deadline still in grace when the other expires ->
  normal forfeit (not no-contest);
- both-disconnected, both deadlines expired with neither returning ->
  no-contest;
- deadline watcher tasks are cancelled on `cancel()`/`dispose()` (mirrors
  `MatchManager.finish_match`/`dispose_match`), leaving no orphan tasks;
- engine `GameState`/tick count is never touched by any of this.

Timing strategy (per the task brief's testing constraint: no real 60s
sleeps): the tricky forfeit-vs-no-contest *decision* logic is exercised by
calling the private `_resolve_expiry` directly with hand-placed deadline
values and a fake monotonic clock -- deterministic, no waiting at all,
mirroring `test_runtime.py`'s own precedent of testing private methods
directly for determinism. A couple of true end-to-end tests prove the
`asyncio.create_task` + sleep wiring itself works, using a tiny
(sub-100ms) real grace period -- the same scale of real sleep
`test_runtime.py` already uses for its own scheduling-dependent tests.
"""

from __future__ import annotations

import asyncio

import pytest
from nether_earth import engine as engine_module
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap
from nether_earth.scenario import default_pvp_scenario

from app.match.manager import MatchManager
from app.match.models import Match, MatchRuntimeState, PlayerSlot
from app.match.reconnect import (
    DisconnectEvent,
    ForfeitEvent,
    NoContestEvent,
    PausedEvent,
    ReconnectCoordinator,
    ResumedEvent,
)
from app.match.runtime import MatchRuntime


class _FakeClock:
    """A fully controllable monotonic clock for deterministic timing tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


def _new_active_match(match_id: str = "m1", *, seed: int = 1) -> Match:
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    game_state = engine_module.new_game(
        map_data, scenario, players=(PLAYER_ONE, PLAYER_TWO), seed=seed
    )
    match = Match(
        match_id=match_id,
        join_code="ABCDEF",
        seed=seed,
        state=MatchRuntimeState.ACTIVE,
        game_state=game_state,
    )
    # `ReconnectCoordinator._resolve_expiry` derives "the opponent" from
    # `match.players` (populated for every real match by `MatchManager`
    # before it can go ACTIVE) -- populate it here too so this fixture
    # matches production shape, unlike `test_runtime.py`'s own
    # `_new_active_match` (which does not need an opponent lookup).
    match.players[PLAYER_ONE] = PlayerSlot(player_id=PLAYER_ONE, nickname="alice", session_token="t1")
    match.players[PLAYER_TWO] = PlayerSlot(player_id=PLAYER_TWO, nickname="bob", session_token="t2")
    return match


def _recording_notifier() -> tuple[list[DisconnectEvent], ReconnectCoordinator]:
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    return events, ReconnectCoordinator(notify=notify, grace_seconds=60.0, monotonic_clock=_FakeClock())


# -- pause on first disconnect ------------------------------------------------


async def test_first_disconnect_pauses_match_and_emits_paused() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)  # let the fire-and-forget PausedEvent notify task run

    assert match.state is MatchRuntimeState.PAUSED_DISCONNECTED
    assert len(events) == 1
    assert isinstance(events[0], PausedEvent)
    assert events[0].disconnected_player_id == PLAYER_ONE
    assert events[0].match is match


async def test_second_disconnect_does_not_repause_or_renotify() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)
    coordinator.mark_disconnected(match, PLAYER_TWO)
    await asyncio.sleep(0)

    assert match.state is MatchRuntimeState.PAUSED_DISCONNECTED
    # Only the first disconnect produces a `PausedEvent`.
    assert [type(e) for e in events] == [PausedEvent]


async def test_duplicate_disconnect_notification_for_same_player_is_idempotent() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    coordinator.mark_disconnected(match, PLAYER_ONE)  # e.g. a stray duplicate notification
    await asyncio.sleep(0)

    assert [type(e) for e in events] == [PausedEvent]
    assert len(coordinator._watchers[match.match_id]) == 1

    coordinator.cancel(match.match_id)


async def test_disconnect_on_a_waiting_match_is_a_no_op() -> None:
    match = _new_active_match()
    match.state = MatchRuntimeState.WAITING
    events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)

    assert match.state is MatchRuntimeState.WAITING
    assert events == []


# -- resume only once both players are connected -----------------------------


async def test_reconnect_of_the_only_disconnected_player_resumes() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)
    coordinator.mark_reconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)

    assert match.state is MatchRuntimeState.ACTIVE
    assert [type(e) for e in events] == [PausedEvent, ResumedEvent]


async def test_reconnect_stays_paused_until_both_players_are_back() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    coordinator.mark_disconnected(match, PLAYER_TWO)
    await asyncio.sleep(0)

    coordinator.mark_reconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)
    # Read into a fresh local rather than re-checking `match.state` by name:
    # mypy's attribute narrowing otherwise treats this and the later check as
    # contradictory (it does not know `coordinator.mark_reconnected` mutates
    # `match.state` in between two checks of the same attribute expression).
    state_after_first_reconnect = match.state
    assert state_after_first_reconnect == MatchRuntimeState.PAUSED_DISCONNECTED
    assert [type(e) for e in events] == [PausedEvent]  # no resume yet

    coordinator.mark_reconnected(match, PLAYER_TWO)
    await asyncio.sleep(0)
    state_after_second_reconnect = match.state
    assert state_after_second_reconnect == MatchRuntimeState.ACTIVE
    assert [type(e) for e in events] == [PausedEvent, ResumedEvent]


async def test_reconnect_of_a_player_never_marked_disconnected_is_a_no_op() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    coordinator.mark_reconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)

    assert match.state is MatchRuntimeState.ACTIVE
    assert events == []


# -- deadline expiry: forfeit / no-contest decision logic --------------------
#
# These call `_resolve_expiry` directly against hand-placed deadline state,
# which pins down the decision rule with no timing dependency at all -- see
# the module docstring.


async def test_lone_deadline_expiry_forfeits_the_disconnected_player() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()
    clock = coordinator._monotonic_clock
    assert isinstance(clock, _FakeClock)

    coordinator.mark_disconnected(match, PLAYER_ONE)  # opponent (P2) stays connected
    await asyncio.sleep(0)
    clock.value = 61.0  # past PLAYER_ONE's 60s deadline

    await coordinator._resolve_expiry(match, PLAYER_ONE)

    assert match.state is MatchRuntimeState.FINISHED
    assert [type(e) for e in events] == [PausedEvent, ForfeitEvent]
    forfeit = events[1]
    assert isinstance(forfeit, ForfeitEvent)
    assert forfeit.forfeiting_player_id == PLAYER_ONE
    assert forfeit.winner_player_id == PLAYER_TWO


async def test_one_expiry_while_opponent_still_within_grace_is_a_forfeit_not_no_contest() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    # Both players disconnected with independent deadlines: P1 at t=5,
    # P2 at t=6 -- P2 is still eligible (within grace) when P1's expires.
    coordinator._deadlines[match.match_id] = {PLAYER_ONE: 5.0, PLAYER_TWO: 6.0}
    match.state = MatchRuntimeState.PAUSED_DISCONNECTED
    clock = coordinator._monotonic_clock
    assert isinstance(clock, _FakeClock)
    clock.value = 5.0

    await coordinator._resolve_expiry(match, PLAYER_ONE)

    assert match.state is MatchRuntimeState.FINISHED
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ForfeitEvent)
    assert event.forfeiting_player_id == PLAYER_ONE
    assert event.winner_player_id == PLAYER_TWO


async def test_both_deadlines_expired_with_neither_returning_is_no_contest() -> None:
    match = _new_active_match()
    events, coordinator = _recording_notifier()

    # Both already past their own independent deadlines by the time this
    # resolves -- e.g. a near-simultaneous double disconnect with equal
    # grace, or simply this watcher running late. Neither returned.
    coordinator._deadlines[match.match_id] = {PLAYER_ONE: 5.0, PLAYER_TWO: 5.0}
    match.state = MatchRuntimeState.PAUSED_DISCONNECTED
    clock = coordinator._monotonic_clock
    assert isinstance(clock, _FakeClock)
    clock.value = 5.0

    await coordinator._resolve_expiry(match, PLAYER_ONE)

    assert match.state is MatchRuntimeState.FINISHED
    assert len(events) == 1
    assert isinstance(events[0], NoContestEvent)


async def test_resolution_is_exactly_once_even_if_both_watchers_would_fire() -> None:
    """Resolving player A's expiry must also retire player B's pending watcher/deadline
    so a later, independently-scheduled resolution for B can never fire a second
    outcome for the same match."""
    match = _new_active_match()
    events, coordinator = _recording_notifier()
    coordinator._deadlines[match.match_id] = {PLAYER_ONE: 5.0, PLAYER_TWO: 6.0}
    match.state = MatchRuntimeState.PAUSED_DISCONNECTED
    clock = coordinator._monotonic_clock
    assert isinstance(clock, _FakeClock)
    clock.value = 5.0

    await coordinator._resolve_expiry(match, PLAYER_ONE)
    # A second resolution attempt for the (already-finished) match/opponent
    # must be a no-op, not a second event.
    await coordinator._resolve_expiry(match, PLAYER_TWO)

    assert len(events) == 1
    assert match_id_has_no_tracking(coordinator, match.match_id)


def match_id_has_no_tracking(coordinator: ReconnectCoordinator, match_id: str) -> bool:
    return match_id not in coordinator._deadlines and match_id not in coordinator._watchers


# -- end-to-end wiring: real asyncio scheduling, tiny real grace -------------


async def test_end_to_end_lone_expiry_forfeits_via_real_scheduling() -> None:
    """Proves the actual `asyncio.create_task` + sleep wiring resolves a lone
    expiry, not just the decision logic in isolation. Uses a ~20ms real grace
    period (same order of magnitude as `test_runtime.py`'s own real-time
    tests) -- nowhere near a real 60s wait."""
    match = _new_active_match()
    events, _ = _recording_notifier()
    events = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=0.02)

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0.06)

    assert match.state is MatchRuntimeState.FINISHED
    assert [type(e) for e in events] == [PausedEvent, ForfeitEvent]


async def test_end_to_end_reconnect_within_grace_cancels_the_watcher() -> None:
    match = _new_active_match()
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=0.02)

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0.005)
    coordinator.mark_reconnected(match, PLAYER_ONE)
    await asyncio.sleep(0.06)  # well past what would have been the deadline

    assert match.state is MatchRuntimeState.ACTIVE
    assert [type(e) for e in events] == [PausedEvent, ResumedEvent]


# -- tick count frozen while paused, engine state untouched ------------------


async def test_tick_count_is_frozen_while_paused_and_resumes_after_reconnect() -> None:
    match = _new_active_match()
    # A short pause-poll interval so resuming is observable within this
    # test's own short real sleeps (production default is 50ms; see
    # `runtime.py`'s `DEFAULT_PAUSE_POLL_INTERVAL_S`).
    runtime = MatchRuntime(match, tick_rate_hz=1000.0, pause_poll_interval_s=0.005)

    async def notify(event: DisconnectEvent) -> None:
        return None

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=60.0)

    runtime.start()
    await asyncio.sleep(0.02)
    ticks_before_pause = runtime.tick_count
    assert ticks_before_pause > 0

    coordinator.mark_disconnected(match, PLAYER_ONE)
    assert match.state is MatchRuntimeState.PAUSED_DISCONNECTED

    await asyncio.sleep(0.02)
    assert runtime.tick_count == ticks_before_pause  # frozen: no ticks while paused
    assert match.game_state is not None
    assert match.game_state.tick == ticks_before_pause

    coordinator.mark_reconnected(match, PLAYER_ONE)
    await asyncio.sleep(0.03)
    assert runtime.tick_count > ticks_before_pause  # ticking resumed

    runtime.request_cancel()
    await runtime.wait_stopped()


# -- cancellation on finish/disposal: no orphan watcher tasks ----------------


async def test_cancel_stops_pending_watchers_and_forgets_the_match() -> None:
    match = _new_active_match()
    _events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)
    task = coordinator._watchers[match.match_id][PLAYER_ONE]
    assert not task.done()

    coordinator.cancel(match.match_id)
    await asyncio.sleep(0)

    assert task.cancelled()
    assert match.match_id not in coordinator._deadlines
    assert match.match_id not in coordinator._watchers


async def test_dispose_is_an_alias_of_cancel() -> None:
    match = _new_active_match()
    _events, coordinator = _recording_notifier()

    coordinator.mark_disconnected(match, PLAYER_ONE)
    await asyncio.sleep(0)

    coordinator.dispose(match.match_id)
    await asyncio.sleep(0)

    assert match.match_id not in coordinator._deadlines
    assert match.match_id not in coordinator._watchers


async def test_cancel_on_a_match_with_nothing_tracked_is_a_no_op() -> None:
    _events, coordinator = _recording_notifier()
    coordinator.cancel("no-such-match")  # must not raise


# -- MatchManager wiring: finish_match/dispose_match cancel the coordinator --


async def test_finish_match_cancels_reconnect_watchers() -> None:
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=60.0)
    manager = MatchManager(reconnect=coordinator)
    result = manager.create_match("alice")
    join = manager.join_match(result.join_code, "bob")
    manager.set_ready(result.session_token, ready=True)
    manager.set_ready(join.session_token, ready=True)

    manager.mark_disconnected(result.session_token)
    await asyncio.sleep(0)
    assert result.match_id in coordinator._watchers

    manager.finish_match(result.match_id)

    assert result.match_id not in coordinator._watchers
    assert result.match_id not in coordinator._deadlines


async def test_dispose_match_cancels_reconnect_watchers() -> None:
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=60.0)
    manager = MatchManager(reconnect=coordinator)
    result = manager.create_match("alice")
    join = manager.join_match(result.join_code, "bob")
    manager.set_ready(result.session_token, ready=True)
    manager.set_ready(join.session_token, ready=True)

    manager.mark_disconnected(result.session_token)
    await asyncio.sleep(0)
    assert result.match_id in coordinator._watchers

    manager.dispose_match(result.match_id)

    assert result.match_id not in coordinator._watchers
    assert result.match_id not in coordinator._deadlines


async def test_manager_mark_disconnected_and_mark_reconnected_delegate_to_coordinator() -> None:
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=60.0)
    manager = MatchManager(reconnect=coordinator)
    result = manager.create_match("alice")
    join = manager.join_match(result.join_code, "bob")
    manager.set_ready(result.session_token, ready=True)
    manager.set_ready(join.session_token, ready=True)

    manager.mark_disconnected(result.session_token)
    await asyncio.sleep(0)
    match = manager.get_match(result.match_id)
    state_after_disconnect = match.state
    assert state_after_disconnect == MatchRuntimeState.PAUSED_DISCONNECTED

    manager.mark_reconnected(result.session_token)
    await asyncio.sleep(0)
    state_after_reconnect = match.state
    assert state_after_reconnect == MatchRuntimeState.ACTIVE
    assert [type(e) for e in events] == [PausedEvent, ResumedEvent]


async def test_manager_mark_disconnected_with_no_reconnect_configured_is_a_no_op() -> None:
    # Pre-Task-7 behavior: `reconnect=None` must remain a harmless no-op.
    manager = MatchManager()
    result = manager.create_match("alice")
    manager.join_match(result.join_code, "bob")

    manager.mark_disconnected(result.session_token)  # must not raise
    manager.mark_reconnected(result.session_token)  # must not raise


async def test_manager_mark_disconnected_for_unknown_token_is_a_no_op() -> None:
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    coordinator = ReconnectCoordinator(notify=notify, grace_seconds=60.0)
    manager = MatchManager(reconnect=coordinator)

    manager.mark_disconnected("no-such-token")
    manager.mark_reconnected("no-such-token")
    await asyncio.sleep(0)

    assert events == []


# -- construction guards ------------------------------------------------------


def test_grace_seconds_must_be_positive() -> None:
    async def notify(event: DisconnectEvent) -> None:
        return None

    with pytest.raises(ValueError):
        ReconnectCoordinator(notify=notify, grace_seconds=0.0)
    with pytest.raises(ValueError):
        ReconnectCoordinator(notify=notify, grace_seconds=-1.0)


def test_default_grace_seconds_is_sixty() -> None:
    from app.match.reconnect import DEFAULT_GRACE_SECONDS

    assert DEFAULT_GRACE_SECONDS == 60.0
