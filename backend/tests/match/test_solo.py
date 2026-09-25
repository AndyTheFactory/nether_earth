"""Solo (human vs engine AI) match lifecycle (CR004.7, issue #288).

A solo match has one human ``PlayerSlot`` and an explicit AI seat
(``Match.ai_player_id``): it fills the match and is always ready, has no
socket or token, is never disconnected, and its commands are re-derived by
the engine on replay rather than recorded.
"""

from __future__ import annotations

import asyncio
import dataclasses
from functools import cache
from pathlib import Path

import pytest
from nether_earth.commands import Command
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import WorldMap
from nether_earth.scenario import Scenario, default_pvp_scenario
from nether_earth.state import GameState

from app.match.manager import SOLO_AI_SEAT, MatchManager
from app.match.models import (
    InvalidSessionTokenError,
    Match,
    MatchNotFoundError,
    MatchOutcome,
    MatchRuntimeState,
)
from app.match.reconnect import (
    DisconnectEvent,
    ForfeitEvent,
    PausedEvent,
    ReconnectCoordinator,
    ResumedEvent,
)
from app.match.runtime import MatchRuntime, MatchRuntimeRegistry, TickObserver
from app.match.world import load_standard_world
from app.replay import (
    ReplayWriter,
    load_commands_by_tick,
    load_meta,
    make_replay_tick_recorder,
    verify_replay,
)


@cache
def _world() -> WorldMap:
    return load_standard_world()


def _solo_scenario() -> Scenario:
    return dataclasses.replace(default_pvp_scenario(), player_two_controller="ai")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _recording_coordinator(
    grace_seconds: float = 60.0,
) -> tuple[list[DisconnectEvent], ReconnectCoordinator]:
    events: list[DisconnectEvent] = []

    async def notify(event: DisconnectEvent) -> None:
        events.append(event)

    return events, ReconnectCoordinator(notify=notify, grace_seconds=grace_seconds)


def _game_state(match: Match) -> GameState:
    assert match.game_state is not None
    return match.game_state


# -- creation / start -----------------------------------------------------------


def test_solo_match_is_full_with_one_human_and_has_no_join_code() -> None:
    manager = MatchManager(world=_world())
    created = manager.create_solo_match("alice", seed=7)

    assert created.player_id == PLAYER_ONE
    assert created.ai_player_id == SOLO_AI_SEAT == PLAYER_TWO
    assert not hasattr(created, "join_code")

    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.WAITING
    assert match.join_code is None
    assert match.is_solo and match.is_full
    # The AI seat is not a PlayerSlot: no nickname, token or socket to reach.
    assert list(match.players) == [PLAYER_ONE]
    assert match.seat_ids == (PLAYER_ONE, PLAYER_TWO)
    assert manager.resolve_session(created.session_token) == (match, PLAYER_ONE)


def test_solo_match_reaches_active_on_the_humans_ready_alone() -> None:
    manager = MatchManager(world=_world())
    created = manager.create_solo_match("alice", seed=7)

    match = manager.set_ready(created.session_token)

    assert match.state is MatchRuntimeState.ACTIVE
    state = _game_state(match)
    assert [memory.player_id for memory in state.ai_memories] == [PLAYER_TWO]
    assert [commander.player_id for commander in state.commanders] == [PLAYER_ONE]


def test_solo_match_needs_a_real_world() -> None:
    with pytest.raises(ValueError, match="world"):
        MatchManager().create_solo_match("alice")


def test_pvp_create_match_is_unchanged() -> None:
    manager = MatchManager(world=_world())
    created = manager.create_match("alice", seed=7)
    match = manager.get_match(created.match_id)

    assert match.join_code == created.join_code
    assert match.ai_player_id is None and not match.is_solo and not match.is_full
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token)
    assert match.state is MatchRuntimeState.WAITING  # still waits for the second human
    manager.set_ready(joined.session_token)
    assert match.state is MatchRuntimeState.ACTIVE
    assert _game_state(match).ai_memories == ()


def test_solo_match_cannot_be_joined_by_code() -> None:
    manager = MatchManager(world=_world())
    created = manager.create_solo_match("alice")
    pvp = manager.create_match("bob")

    # The only join codes indexed belong to PvP matches.
    assert manager.get_match_by_join_code(pvp.join_code).match_id == pvp.match_id
    for code in ("", "None"):
        with pytest.raises(MatchNotFoundError):
            manager.join_match(code, "mallory")
    assert list(manager.get_match(created.match_id).players) == [PLAYER_ONE]


# -- tick loop ----------------------------------------------------------------------


async def test_solo_match_ticks_with_the_ai_seat_and_records_only_human_commands() -> None:
    registry = MatchRuntimeRegistry()
    recorded: list[tuple[int, tuple[Command, ...]]] = []

    async def record(tick: int, commands: tuple[Command, ...], *_: object) -> None:
        recorded.append((tick, commands))

    async def on_tick(*_: object) -> None:
        return None

    def on_tick_factory(_match: Match) -> TickObserver:
        # Any observer gates the runtime on `announce_started`, so this test
        # drives ticks itself and nothing ticks in the background.
        return on_tick

    manager = MatchManager(
        world=_world(),
        runtime=registry,
        on_tick_factory=on_tick_factory,
        on_tick_commands_factory=lambda _match: record,
    )
    created = manager.create_solo_match("alice", seed=11)
    match = manager.set_ready(created.session_token)
    runtime = registry.get(match.match_id)
    assert runtime is not None

    assert await registry.submit_command(match.match_id, Command(player=PLAYER_ONE, sequence=0))
    for _ in range(12):  # spans several AI decision ticks
        await runtime._advance_one_tick()

    assert _game_state(match).tick == 12
    assert [tick for tick, _ in recorded] == list(range(1, 13))
    assert all(command.player == PLAYER_ONE for _, batch in recorded for command in batch)

    manager.finish_match(match.match_id)
    await registry.wait_stopped(match.match_id)
    assert not runtime.is_running


# -- disconnect / reconnect -----------------------------------------------------------


async def test_ai_seat_never_triggers_a_pause() -> None:
    manager = MatchManager(world=_world())
    match = manager.set_ready(manager.create_solo_match("alice").session_token)
    events, coordinator = _recording_coordinator()

    coordinator.mark_disconnected(match, SOLO_AI_SEAT)
    coordinator.mark_reconnected(match, SOLO_AI_SEAT)
    await asyncio.sleep(0)

    assert match.state is MatchRuntimeState.ACTIVE
    assert events == []


async def test_human_disconnect_pauses_a_solo_match_and_reconnect_resumes_it() -> None:
    events, coordinator = _recording_coordinator()
    manager = MatchManager(world=_world(), reconnect=coordinator)
    created = manager.create_solo_match("alice")
    match = manager.set_ready(created.session_token)

    manager.mark_disconnected(created.session_token)
    await asyncio.sleep(0)
    assert match.state is MatchRuntimeState.PAUSED_DISCONNECTED
    assert [type(event) for event in events] == [PausedEvent]
    assert isinstance(events[0], PausedEvent)
    assert events[0].disconnected_player_id == PLAYER_ONE

    # The human alone reconnecting resumes: the AI seat is never waited on.
    manager.mark_reconnected(created.session_token)
    await asyncio.sleep(0)
    assert match.state is MatchRuntimeState.ACTIVE
    assert [type(event) for event in events] == [PausedEvent, ResumedEvent]


async def test_human_grace_expiry_forfeits_a_solo_match_to_the_ai() -> None:
    events, coordinator = _recording_coordinator(grace_seconds=0.01)
    manager = MatchManager(world=_world(), reconnect=coordinator)
    coordinator.bind_finish_hook(manager.finish_match)
    created = manager.create_solo_match("alice")
    match = manager.set_ready(created.session_token)

    manager.mark_disconnected(created.session_token)
    # Even a stray notification naming the AI seat cannot make this a
    # both-disconnected no-contest.
    coordinator.mark_disconnected(match, SOLO_AI_SEAT)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if match.state is MatchRuntimeState.FINISHED:
            break

    assert match.state is MatchRuntimeState.FINISHED
    assert match.result is not None
    assert match.result.outcome is MatchOutcome.FORFEIT
    assert match.result.forfeiting_player_id == PLAYER_ONE
    assert match.result.winner_player_id == SOLO_AI_SEAT
    forfeit = events[-1]
    assert isinstance(forfeit, ForfeitEvent)
    assert forfeit.winner_player_id == SOLO_AI_SEAT


# -- sweep ----------------------------------------------------------------------------


def test_solo_match_is_swept_and_finished_like_any_other() -> None:
    clock = _Clock()
    manager = MatchManager(
        world=_world(), finished_retention_s=300, waiting_timeout_s=900, clock=clock
    )
    lobby = manager.create_solo_match("alice")
    played = manager.create_solo_match("bob")
    match = manager.set_ready(played.session_token)
    assert match.state is MatchRuntimeState.ACTIVE

    clock.now += 10
    manager.finish_match(played.match_id)
    assert match.state is MatchRuntimeState.FINISHED

    clock.now += 300
    assert [m.match_id for m in manager.sweep()] == [played.match_id]
    clock.now += 600
    assert [m.match_id for m in manager.sweep()] == [lobby.match_id]
    assert len(manager) == 0
    with pytest.raises(InvalidSessionTokenError):
        manager.resolve_session(played.session_token)


# -- replay -----------------------------------------------------------------------------


async def test_recorded_solo_match_replay_verifies(tmp_path: Path) -> None:
    world = _world()
    writer = ReplayWriter(base_dir=tmp_path)
    manager = MatchManager(
        world=world, on_match_start=writer.start_match, on_match_finish=writer.finish_match
    )
    created = manager.create_solo_match("alice", seed=1234)
    match = manager.set_ready(created.session_token)
    runtime = MatchRuntime(
        match, world=world, on_tick_commands=make_replay_tick_recorder(writer, match.match_id)
    )

    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=0))
    for _ in range(20):
        await runtime._advance_one_tick()
    manager.finish_match(match.match_id)

    meta = load_meta(tmp_path, match.match_id)
    assert meta["status"] == "finished" and meta["final_tick"] == 20
    assert meta["seat_controllers"] == {"p1": "human", "p2": "ai"}
    assert meta["players"] == {"p1": "alice"}
    assert "ai_memories" in meta["final_snapshot"]
    recorded = load_commands_by_tick(tmp_path, match.match_id)
    assert all(c.player == PLAYER_ONE for batch in recorded.values() for c in batch)

    result = verify_replay(tmp_path, match.match_id, scenario=_solo_scenario(), world=world)
    assert result.matches, (result.reproduced_snapshot, result.persisted_snapshot)

    # The PvP scenario cannot silently stand in for the AI-seat one.
    with pytest.raises(ValueError, match="seat controllers"):
        verify_replay(tmp_path, match.match_id, scenario=default_pvp_scenario(), world=world)
