"""Tests for ``app.match.runtime`` (M7 Task 4, issue #93).

Covers the acceptance criteria from the task brief: active matches call
``engine.step`` exactly once per authoritative tick; the same accepted
command sequence produces the same engine ordering regardless of coroutine
scheduling; duplicate/replayed ``client_sequence`` values are rejected;
no engine ticks advance while ``PAUSED_DISCONNECTED``; multiple matches run
independently in one process; cancellation/disposal leaves no orphan task.

Timing strategy: content-level behavior (engine.step call count per tick,
dedup, deterministic ordering) is verified by calling the loop's private
``_advance_one_tick``/``submit_command`` directly, with no real-time waits
at all. Only the handful of tests that must exercise the actual scheduled
loop (state-gating, pause no-op, cancellation) use a very high tick rate
(500-1000 Hz) with real sleeps capped at ~20-30ms total, well short of a
real 20 Hz tick interval.
"""

from __future__ import annotations

import asyncio

from nether_earth import engine as engine_module
from nether_earth.commands import Command
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap
from nether_earth.scenario import default_pvp_scenario

from app.match.manager import MatchManager
from app.match.models import Match, MatchRuntimeState
from app.match.runtime import MatchRuntime, MatchRuntimeRegistry


def _new_active_match(match_id: str = "m1", *, seed: int = 1) -> Match:
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    game_state = engine_module.new_game(
        map_data, scenario, players=(PLAYER_ONE, PLAYER_TWO), seed=seed
    )
    return Match(
        match_id=match_id,
        join_code="ABCDEF",
        seed=seed,
        state=MatchRuntimeState.ACTIVE,
        game_state=game_state,
    )


# -- direct tick-content tests (no real-time loop involved) ------------------


async def test_advance_one_tick_steps_engine_exactly_once() -> None:
    match = _new_active_match()
    runtime = MatchRuntime(match)
    starting_tick = match.game_state.tick  # type: ignore[union-attr]

    await runtime._advance_one_tick()

    assert match.game_state.tick == starting_tick + 1  # type: ignore[union-attr]
    assert runtime.tick_count == 1


async def test_advance_one_tick_drains_queue() -> None:
    match = _new_active_match()
    runtime = MatchRuntime(match)
    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=0)) is True

    await runtime._advance_one_tick()

    assert runtime._pending == []


async def test_loop_calls_engine_step_exactly_once_per_tick(monkeypatch: object) -> None:
    match = _new_active_match()
    calls: list[int] = []
    original_step = engine_module.step

    def counting_step(state: object, commands: object, **kwargs: object) -> object:
        calls.append(1)
        return original_step(state, commands, **kwargs)  # type: ignore[arg-type]

    import pytest

    mp = pytest.MonkeyPatch()
    mp.setattr(engine_module, "step", counting_step)
    try:
        runtime = MatchRuntime(match, tick_rate_hz=1000.0)
        runtime.start()
        await asyncio.sleep(0.02)
        runtime.request_cancel()
        await runtime.wait_stopped()
    finally:
        mp.undo()

    assert len(calls) == runtime.tick_count
    assert runtime.tick_count > 0
    assert match.game_state.tick == runtime.tick_count  # type: ignore[union-attr]


# -- duplicate / replayed client_sequence -------------------------------------


async def test_duplicate_client_sequence_rejected() -> None:
    match = _new_active_match()
    runtime = MatchRuntime(match)

    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=5)) is True
    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=5)) is False
    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=3)) is False
    assert await runtime.submit_command(Command(player=PLAYER_ONE, sequence=6)) is True
    # A different player's sequence numbering is tracked independently.
    assert await runtime.submit_command(Command(player=PLAYER_TWO, sequence=5)) is True


# -- deterministic ordering regardless of coroutine scheduling ----------------


async def test_deterministic_ordering_regardless_of_submission_order() -> None:
    match_a = _new_active_match("a", seed=42)
    match_b = _new_active_match("b", seed=42)
    runtime_a = MatchRuntime(match_a)
    runtime_b = MatchRuntime(match_b)

    # Each player's own sequences are increasing (as a real per-connection
    # transport guarantees), but the two players' commands interleave
    # differently across the two submission orders below -- only the
    # *player/sequence* identity of the accepted set matters for engine
    # ordering, never the order they happened to be submitted/scheduled in.
    commands_a_order = [
        Command(player=PLAYER_ONE, sequence=0),
        Command(player=PLAYER_TWO, sequence=0),
        Command(player=PLAYER_ONE, sequence=1),
        Command(player=PLAYER_TWO, sequence=1),
    ]
    commands_b_order = [
        Command(player=PLAYER_TWO, sequence=0),
        Command(player=PLAYER_TWO, sequence=1),
        Command(player=PLAYER_ONE, sequence=0),
        Command(player=PLAYER_ONE, sequence=1),
    ]

    for command in commands_a_order:
        assert await runtime_a.submit_command(command) is True

    async def _submit_with_yield(runtime: MatchRuntime, command: Command) -> None:
        await asyncio.sleep(0)
        await runtime.submit_command(command)

    # Submit the identical logical command set to runtime_b in a different
    # order via concurrently scheduled coroutines, so interleaving is up to
    # the event loop rather than a fixed submission order.
    await asyncio.gather(*(_submit_with_yield(runtime_b, command) for command in commands_b_order))

    await runtime_a._advance_one_tick()
    await runtime_b._advance_one_tick()

    assert runtime_a.last_events == runtime_b.last_events
    assert match_a.game_state.tick == match_b.game_state.tick  # type: ignore[union-attr]


# -- pause / no-op while PAUSED_DISCONNECTED ----------------------------------


async def test_no_ticks_while_paused_disconnected() -> None:
    match = _new_active_match()
    match.state = MatchRuntimeState.PAUSED_DISCONNECTED
    runtime = MatchRuntime(match, tick_rate_hz=1000.0, pause_poll_interval_s=0.005)

    runtime.start()
    await asyncio.sleep(0.02)

    assert runtime.tick_count == 0

    runtime.request_cancel()
    await runtime.wait_stopped()


async def test_pausing_one_match_does_not_stall_another() -> None:
    match_a = _new_active_match("a")
    match_b = _new_active_match("b")
    registry = MatchRuntimeRegistry(tick_rate_hz=1000.0, pause_poll_interval_s=0.005)
    registry.start(match_a)
    registry.start(match_b)

    await asyncio.sleep(0.01)
    match_a.state = MatchRuntimeState.PAUSED_DISCONNECTED
    runtime_a = registry.get("a")
    runtime_b = registry.get("b")
    assert runtime_a is not None and runtime_b is not None
    ticks_a_at_pause = runtime_a.tick_count

    await asyncio.sleep(0.02)

    assert runtime_a.tick_count == ticks_a_at_pause
    assert runtime_b.tick_count > ticks_a_at_pause

    registry.dispose("a")
    registry.dispose("b")
    await runtime_a.wait_stopped()
    await runtime_b.wait_stopped()


# -- multiple independent matches ---------------------------------------------


async def test_multiple_matches_run_independently() -> None:
    match_a = _new_active_match("a")
    match_b = _new_active_match("b")
    registry = MatchRuntimeRegistry(tick_rate_hz=1000.0)
    registry.start(match_a)
    registry.start(match_b)

    await asyncio.sleep(0.02)

    runtime_a = registry.get("a")
    runtime_b = registry.get("b")
    assert runtime_a is not None and runtime_b is not None
    assert runtime_a.tick_count > 0
    assert runtime_b.tick_count > 0
    assert match_a.game_state.tick == runtime_a.tick_count  # type: ignore[union-attr]
    assert match_b.game_state.tick == runtime_b.tick_count  # type: ignore[union-attr]

    registry.dispose("a")
    registry.dispose("b")
    await runtime_a.wait_stopped()
    await runtime_b.wait_stopped()
    assert len(registry) == 0


# -- start idempotency ---------------------------------------------------------


async def test_start_is_idempotent() -> None:
    match = _new_active_match()
    runtime = MatchRuntime(match, tick_rate_hz=1000.0)

    runtime.start()
    task1 = runtime._task
    runtime.start()
    task2 = runtime._task

    assert task1 is task2

    runtime.request_cancel()
    await runtime.wait_stopped()


# -- cancellation / disposal leave no orphan task -----------------------------


async def test_cancellation_leaves_no_orphan_task() -> None:
    match = _new_active_match()
    runtime = MatchRuntime(match, tick_rate_hz=1000.0)
    runtime.start()
    await asyncio.sleep(0.01)

    assert runtime.is_running

    runtime.request_cancel()
    await runtime.wait_stopped()

    assert runtime._task is not None
    assert runtime._task.done()
    assert not runtime.is_running


async def test_registry_submit_command_unknown_match_returns_false() -> None:
    registry = MatchRuntimeRegistry()
    accepted = await registry.submit_command("nope", Command(player=PLAYER_ONE, sequence=0))
    assert accepted is False


# -- MatchManager wiring -------------------------------------------------------


async def test_manager_wiring_starts_and_stops_runtime_with_no_orphan_task() -> None:
    registry = MatchRuntimeRegistry(tick_rate_hz=1000.0)
    manager = MatchManager(runtime=registry)

    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")

    manager.set_ready(created.session_token)
    assert registry.get(created.match_id) is None  # not ACTIVE yet: only one side ready

    manager.set_ready(joined.session_token)
    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.ACTIVE

    runtime = registry.get(created.match_id)
    assert runtime is not None
    await asyncio.sleep(0.01)
    assert runtime.tick_count > 0

    manager.finish_match(created.match_id)
    await runtime.wait_stopped()
    assert runtime._task is not None
    assert runtime._task.cancelled()

    manager.dispose_match(created.match_id)
    assert len(registry) == 0


async def test_manager_without_runtime_hook_never_touches_asyncio() -> None:
    """No ``runtime`` supplied keeps MatchManager exactly as sync as before."""
    manager = MatchManager()
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")

    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)

    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.ACTIVE
    manager.finish_match(created.match_id)
    manager.dispose_match(created.match_id)
