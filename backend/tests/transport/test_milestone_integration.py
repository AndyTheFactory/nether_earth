"""Milestone 7 end-to-end multiplayer runtime integration scenario (Task 10, issue #99).

This is the final M7 gate: it proves Tasks 1-9 work together as one real
stack, using two real WebSocket test clients against the real
``app.main.create_app`` composition root (connection registry, runtime
registry, replay writer, every ``on_tick``/``on_tick_commands``/
``on_match_start``/``on_match_finish`` hook) -- never a hand-assembled
subset, and never a direct ``engine.step``/``engine.new_game`` call from this
module (the engine is stepped only by the real ``MatchRuntime``).

Scenario coverage (issue #99's 13 steps), split across a small number of
focused tests rather than one single test function, because two of the
locked disconnect/reconnect outcomes (forfeit, no-contest) each terminate a
match (``FINISHED``) and cannot both be reached from the same match instance
as the pause/reconnect/resume path (steps 7-8) or the concurrent-match/
finish/replay path (steps 11-13):

- ``test_full_scenario_...`` covers steps 1-8 (create/join/ready/start/
  snapshot/representative-commands/invalid-rejection/disconnect-pause/
  reconnect-resume) on one match, runs a second match *concurrently* on the
  same app instance throughout to prove cross-match isolation (step 11),
  then drives the first match to forfeit (step 9) -- the natural, real
  termination path this stack already implements -- and finishes with an
  explicit ``dispose_match`` (step 12) and a replay-verification of the
  persisted command stream against the engine directly (step 13).
- ``test_both_players_disconnected_and_never_returning_is_a_no_contest``
  covers step 10 (the no-contest sub-case) on its own, separate match/app
  instance, since it is a second, mutually exclusive termination path.

Timing: every grace period is short (``reconnect_grace_seconds``) and the
tick rate is the real production 20 Hz default (``app.main.create_app``'s
own default) -- no arbitrary ``time.sleep`` anywhere; every wait is either a
blocking ``receive_json()`` on a real broadcast/message the stack itself
produces, or a deadline-watcher `asyncio.Task` whose deadline was shortened
at construction time.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap
from nether_earth.scenario import default_pvp_scenario
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.match.manager import MatchManager
from app.match.models import Match, MatchNotFoundError, MatchOutcome, MatchRuntimeState
from app.replay.verify import verify_replay

#: Short enough that no real test run waits anywhere near the locked
#: production 60s default, long enough that "reconnect within grace" (steps
#: 7-8) has comfortable room against normal test-process scheduling jitter.
_SHORT_GRACE_SECONDS = 0.2


def _match_manager(client: TestClient) -> MatchManager:
    manager = client.app.state.match_manager
    assert isinstance(manager, MatchManager)
    return manager


def _create(ws: WebSocketTestSession, nickname: str = "alice") -> dict[str, Any]:
    ws.send_text(json.dumps({"protocolVersion": 1, "type": "create", "nickname": nickname}))
    return dict(ws.receive_json())


def _join(ws: WebSocketTestSession, join_code: str, nickname: str = "bob") -> dict[str, Any]:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "join",
                "joinCode": join_code,
                "nickname": nickname,
            }
        )
    )
    return dict(ws.receive_json())


def _ready(
    ws: WebSocketTestSession, *, match_id: str, player_id: str, session_token: str, ready: bool = True
) -> None:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "ready",
                "matchId": match_id,
                "playerId": player_id,
                "sessionToken": session_token,
                "ready": ready,
            }
        )
    )


def _reconnect(ws: WebSocketTestSession, *, match_id: str, player_id: str, session_token: str) -> None:
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "reconnect",
                "matchId": match_id,
                "playerId": player_id,
                "sessionToken": session_token,
            }
        )
    )


def _start_active_match_keeping_sockets_open(
    ws_a: WebSocketTestSession, ws_b: WebSocketTestSession, *, nickname_a: str, nickname_b: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create+join+ready both slots to ACTIVE, returning (created, joined).

    Leaves both sockets open (unlike a ``with``-scoped helper) so callers
    can keep driving the match -- disconnecting one side deliberately while
    observing broadcasts on the other, exactly like
    ``test_ws.py``'s own ``_start_active_match_keeping_sockets_open``.
    """
    created = _create(ws_a, nickname_a)
    joined = _join(ws_b, created["joinCode"], nickname_b)
    ws_a.receive_json()  # ready_state (join broadcast)
    ws_b.receive_json()  # ready_state (own echo)

    _ready(ws_a, match_id=created["matchId"], player_id="p1", session_token=created["sessionToken"])
    ws_a.receive_json()
    ws_b.receive_json()

    _ready(ws_b, match_id=created["matchId"], player_id="p2", session_token=joined["sessionToken"])
    ws_a.receive_json()  # ready_state from p2 readying up
    ws_b.receive_json()
    assert ws_a.receive_json()["type"] == "started"
    assert ws_b.receive_json()["type"] == "started"
    snapshot_a = ws_a.receive_json()
    snapshot_b = ws_b.receive_json()
    assert snapshot_a["type"] == "snapshot"
    assert snapshot_a["tick"] == 0
    assert snapshot_b == snapshot_a

    return created, joined


def _submit_command(
    ws: WebSocketTestSession,
    *,
    match_id: str,
    player_id: str,
    session_token: str,
    sequence: int,
    payload: dict[str, Any],
) -> str:
    """Submit one gameplay command and classify its transport outcome.

    Returns ``"accepted"`` if the command produced no error of its own (the
    real protocol never acks a successfully-queued command -- see
    ``test_ws.py``'s
    ``test_valid_commander_move_command_reaches_submit_command_as_the_real_engine_command``),
    or the ``error.code`` string (``"invalid_command_payload"``/
    ``"command_rejected"``) if the transport rejected the frame outright.

    Uses the same "send a deliberately malformed probe frame right after"
    idiom the rest of ``test_ws.py`` relies on to force strict in-order,
    single-threaded-per-connection processing: receiving the probe's own
    ``invalid_message`` error is structural proof the command frame was
    already fully handled one way or the other, never a race.
    """
    ws.send_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "command",
                "matchId": match_id,
                "playerId": player_id,
                "sessionToken": session_token,
                "clientSequence": sequence,
                "payload": payload,
            }
        )
    )
    ws.send_text(json.dumps({"protocolVersion": 1, "type": "not_a_real_probe_type"}))

    # The match's own real 20Hz `MatchRuntime` may interleave fresh
    # `snapshot` broadcasts for `match_id` between the two frames just sent
    # and either response arriving -- legitimate ticking, not part of this
    # command's own outcome, so it is skipped rather than misread as an
    # error/ack for the command.
    for _ in range(1000):
        message = ws.receive_json()
        if message["type"] != "snapshot":
            break
        assert message["matchId"] == match_id
    else:
        raise AssertionError("no non-snapshot response ever arrived")

    assert message["type"] == "error"
    if message["error"]["code"] == "invalid_message":
        return "accepted"  # the probe's own error -- the command produced none.
    command_error_code = str(message["error"]["code"])

    for _ in range(1000):
        probe_response = ws.receive_json()
        if probe_response["type"] != "snapshot":
            break
        assert probe_response["matchId"] == match_id
    else:
        raise AssertionError("no non-snapshot probe response ever arrived")
    assert probe_response["type"] == "error"
    assert probe_response["error"]["code"] == "invalid_message"
    return command_error_code


async def _wait_for_match_finished(
    manager: MatchManager, match_id: str, *, timeout_s: float = 5.0
) -> Match:
    """Poll ``manager`` until ``match_id`` reaches ``FINISHED``, or raise after ``timeout_s``.

    Must run *inside* the app's own event loop (see this function's call
    site via ``client.portal.call``) since it awaits nothing owned by the
    calling thread -- it exists only to synchronize with a background
    deadline-watcher task this test cannot otherwise observe (no live
    connection to `receive_json()` a broadcast on). ``timeout_s`` is a
    generous safety net (25x ``_SHORT_GRACE_SECONDS``), not a value this
    function relies on for its own correctness -- a healthy resolution
    always finishes in close to ``_SHORT_GRACE_SECONDS``.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        match = manager.get_match(match_id)
        if match.state is MatchRuntimeState.FINISHED:
            return match
        if loop.time() >= deadline:
            raise AssertionError(
                f"match {match_id!r} never reached FINISHED within {timeout_s}s "
                f"(state is still {match.state})"
            )
        await asyncio.sleep(0.01)


def _next_non_snapshot(ws: WebSocketTestSession, *, own_match_id: str) -> dict[str, Any]:
    """Return the next message on ``ws`` that is not one of its own match's real tick broadcasts.

    The real 20Hz ``MatchRuntime`` can legitimately interleave a fresh
    ``snapshot`` for ``own_match_id`` between any two messages this test
    explicitly waits for (e.g. between a reconnect's own `resync` and the
    `resumed` broadcast it triggers) -- this is real, correct ticking, not
    something to special-case away structurally, so callers that need a
    *specific* non-snapshot message skip past any of it here rather than
    asserting on whatever happens to arrive first.
    """
    for _ in range(1000):
        message = ws.receive_json()
        if message["type"] != "snapshot":
            return dict(message)
        assert message["matchId"] == own_match_id, message
    raise AssertionError("only snapshot messages ever arrived")


def _assert_isolated_probe(ws: WebSocketTestSession, *, own_match_id: str) -> None:
    """Send a malformed probe and assert the socket's queue holds nothing but ``own_match_id``'s own traffic.

    The real ``create_app`` composition-root ticks the real 20Hz
    ``MatchRuntime`` in the background for every ``ACTIVE`` match, so a
    connection whose own match is still running legitimately keeps
    receiving fresh ``snapshot`` broadcasts for *its own* match between any
    two calls this test makes -- that is expected ticking, not leakage.
    This drains any such frames (asserting each one really is a `snapshot`
    tagged with `own_match_id`, never some other match's message) until the
    probe's own `invalid_message` error is reached, which is the actual
    cross-match-isolation assertion (issue #99 step 11): nothing belonging
    to a *different* match ever appears on this queue.
    """
    ws.send_text(json.dumps({"protocolVersion": 1, "type": "not_a_real_type"}))
    for _ in range(1000):
        message = ws.receive_json()
        if message["type"] == "error":
            assert message["error"]["code"] == "invalid_message"
            return
        assert message["type"] == "snapshot", message
        assert message["matchId"] == own_match_id, message
    raise AssertionError("probe response never arrived -- possible runaway tick flood")


def test_full_scenario_two_players_commands_disconnect_reconnect_forfeit_replay(
    tmp_path: Path,
) -> None:
    """Steps 1-9, 11-13 of the milestone integration scenario (issue #99).

    Runs a second, fully independent match concurrently throughout (step
    11) to prove cross-match isolation holds while match A goes through its
    full lifecycle, then drives match A to a real timeout-forfeit (step 9)
    and verifies the persisted replay against the engine directly (step
    13). Step 10 (both-disconnected no-contest) is a separate, mutually
    exclusive termination path -- see
    ``test_both_players_disconnected_and_never_returning_is_a_no_contest``.
    """
    scenario = default_pvp_scenario()
    map_data = BootstrapMap(map_id=scenario.map_id, version=scenario.map_version, width=1, height=1)
    replay_dir = tmp_path / "replays"
    app = create_app(replay_dir=replay_dir, reconnect_grace_seconds=_SHORT_GRACE_SECONDS)

    with TestClient(app) as client:
        manager = _match_manager(client)

        with (
            client.websocket_connect("/ws") as b1,
            client.websocket_connect("/ws") as b2,
        ):
            # -- Steps 1-4: match B (the concurrent match) starts first, so
            # it is already live and broadcasting its own snapshots for the
            # whole duration of match A's scenario below.
            created_b = _create(b1, "beth")
            joined_b = _join(b2, created_b["joinCode"], "ben")
            b1.receive_json()  # ready_state
            b2.receive_json()  # ready_state (own echo)
            _ready(b1, match_id=created_b["matchId"], player_id="p1", session_token=created_b["sessionToken"])
            b1.receive_json()
            b2.receive_json()
            _ready(b2, match_id=created_b["matchId"], player_id="p2", session_token=joined_b["sessionToken"])
            b1.receive_json()
            b2.receive_json()
            assert b1.receive_json()["type"] == "started"
            assert b2.receive_json()["type"] == "started"
            assert b1.receive_json()["type"] == "snapshot"
            assert b2.receive_json()["type"] == "snapshot"

            with client.websocket_connect("/ws") as a_persistent:
                with client.websocket_connect("/ws") as a_disconnecting:
                    # -- Steps 1-4: match A create/join/ready/start + snapshot --
                    created_a, _joined_a = _start_active_match_keeping_sockets_open(
                        a_disconnecting, a_persistent, nickname_a="alice", nickname_b="adam"
                    )
                    match_a_id = created_a["matchId"]
                    assert match_a_id != created_b["matchId"]

                    # -- Step 11 (mid-scenario probe): match B's connections must
                    # not have received anything from match A's create/join/
                    # ready/start/snapshot burst above. The very next frame on
                    # each of B's sockets must be the response to a fresh probe
                    # sent now, not a leaked match-A message queued earlier.
                    _assert_isolated_probe(b1, own_match_id=created_b["matchId"])
                    _assert_isolated_probe(b2, own_match_id=created_b["matchId"])

                    # -- Step 5: representative commands across every M7 Task 9
                    # adapter family: commander, M4 construction/economy, M5
                    # movement/orders, M6 combat. The transport's job is only to
                    # route each to the real engine without crashing the
                    # connection -- see this module's docstring and
                    # `_submit_command`'s own docstring for why "accepted" (no
                    # error) and an explicit rejection code are both valid,
                    # expected transport outcomes here.
                    outcomes: dict[str, str] = {}
                    outcomes["commander_move"] = _submit_command(
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=0,
                        payload={"kind": "commander_move", "dx": 1, "dy": 0},
                    )
                    outcomes["commander_set_vertical_intent"] = _submit_command(
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=1,
                        payload={"kind": "commander_set_vertical_intent", "rising": True},
                    )
                    outcomes["select_module"] = _submit_command(  # M4 construction/economy
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=2,
                        payload={"kind": "select_module", "module": "cannon"},
                    )
                    outcomes["cancel_construction"] = _submit_command(  # M4
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=3,
                        payload={"kind": "cancel_construction"},
                    )
                    outcomes["launch_robot"] = _submit_command(  # M4
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=4,
                        payload={"kind": "launch_robot"},
                    )
                    outcomes["direct_robot_move"] = _submit_command(  # M5 movement
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=5,
                        payload={"kind": "direct_robot_move", "dx": 1, "dy": 0},
                    )
                    outcomes["set_robot_order_advance"] = _submit_command(  # M5 orders
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=6,
                        payload={
                            "kind": "set_robot_order",
                            "entityId": "no-such-robot",
                            "order": {"kind": "advance", "distanceMiles": 10},
                        },
                    )
                    outcomes["set_robot_order_search_capture"] = _submit_command(  # M5 orders/capture
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=7,
                        payload={
                            "kind": "set_robot_order",
                            "entityId": "no-such-robot",
                            "order": {"kind": "search_capture", "target": "neutral_factory"},
                        },
                    )
                    outcomes["robot_fire"] = _submit_command(  # M6 combat
                        a_disconnecting,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                        sequence=8,
                        payload={
                            "kind": "robot_fire",
                            "entityId": "no-such-robot",
                            "weapon": "nuclear",
                            "targetX": 5,
                            "targetY": 5,
                        },
                    )
                    for name, outcome in outcomes.items():
                        assert outcome in ("accepted", "invalid_command_payload", "command_rejected"), (
                            name,
                            outcome,
                        )

                    # A deliberately structurally-malformed payload (diagonal
                    # move -- schema-valid per-axis, but the engine dataclass's
                    # own __post_init__ rejects it) must be classified
                    # explicitly as `invalid_command_payload`, never silently
                    # "accepted" -- pins down that the classifier above (and
                    # the real adapter/`CommandPayloadError` path) still works.
                    assert (
                        _submit_command(
                            a_disconnecting,
                            match_id=match_a_id,
                            player_id="p1",
                            session_token=created_a["sessionToken"],
                            sequence=9,
                            payload={"kind": "commander_move", "dx": 1, "dy": 1},
                        )
                        == "invalid_command_payload"
                    )

                    # -- Step 6: invalid/unauthorized message rejection --
                    # (a) a schema violation never closes/crashes the connection.
                    a_disconnecting.send_text(json.dumps({"protocolVersion": 1, "type": "bogus_type"}))
                    schema_error = _next_non_snapshot(a_disconnecting, own_match_id=match_a_id)
                    assert schema_error["type"] == "error"
                    assert schema_error["error"]["code"] == "invalid_message"

                    # (b) a command for a match this connection is not bound to
                    # (wrong session token entirely) is rejected and the
                    # connection is closed -- proven on a throwaway third
                    # socket so `a_disconnecting`/`a_persistent` stay usable.
                    with client.websocket_connect("/ws") as intruder:
                        intruder.send_text(
                            json.dumps(
                                {
                                    "protocolVersion": 1,
                                    "type": "command",
                                    "matchId": match_a_id,
                                    "playerId": "p1",
                                    "sessionToken": "totally-not-a-real-session-token",
                                    "clientSequence": 0,
                                    "payload": {"kind": "cancel_construction"},
                                }
                            )
                        )
                        unauthorized_error = intruder.receive_json()
                        assert unauthorized_error["type"] == "error"
                        assert unauthorized_error["error"]["code"] == "invalid_session"
                        with pytest.raises(WebSocketDisconnect):
                            intruder.receive_text()

                    # match B is still completely unaffected by any of the above.
                    _assert_isolated_probe(b1, own_match_id=created_b["matchId"])

                    # -- Step 7: disconnect one player, assert the engine tick
                    # freezes. Read the pre-disconnect tick count directly from
                    # the live `Match` (read-only introspection, never a step)
                    # right before closing the socket, and again immediately
                    # after observing the `paused` broadcast -- if the tick loop
                    # were still advancing, a 20Hz match would have almost
                    # certainly ticked at least once across the several
                    # `receive_json()`/assertion calls in between; instead
                    # `MatchRuntime._run` polls PAUSED_DISCONNECTED as a no-op
                    # (see `runtime.py`), so the two reads must match exactly.
                    match_a = manager.get_match(match_a_id)
                    assert match_a.game_state is not None
                    tick_before_disconnect = match_a.game_state.tick

                # `a_disconnecting`'s own `with` block just closed -> abrupt
                # disconnect for p1. `a_persistent` (p2) is still open.
                paused = _next_non_snapshot(a_persistent, own_match_id=match_a_id)
                assert paused["type"] == "paused"
                assert paused["matchId"] == match_a_id
                assert paused["disconnectedPlayerId"] == "p1"
                assert isinstance(paused["graceDeadlineMs"], int)
                assert manager.get_match(match_a_id).state is MatchRuntimeState.PAUSED_DISCONNECTED

                match_a_after_pause = manager.get_match(match_a_id)
                assert match_a_after_pause.game_state is not None
                tick_after_pause = match_a_after_pause.game_state.tick
                assert tick_after_pause == tick_before_disconnect, (
                    "engine tick advanced while the match was PAUSED_DISCONNECTED"
                )

                # match B is still ticking/unaffected -- one more cross-match
                # isolation probe, this time while match A is actually paused.
                _assert_isolated_probe(b2, own_match_id=created_b["matchId"])

                # -- Step 8: reconnect within grace, receive current snapshot,
                # resume. `_SHORT_GRACE_SECONDS` is comfortably longer than the
                # synchronous reconnect below takes, so this is not a race.
                with client.websocket_connect("/ws") as a_reconnected:
                    _reconnect(
                        a_reconnected,
                        match_id=match_a_id,
                        player_id="p1",
                        session_token=created_a["sessionToken"],
                    )
                    resync = a_reconnected.receive_json()
                    assert resync["type"] == "resync"
                    assert resync["matchId"] == match_a_id
                    assert resync["playerId"] == "p1"
                    assert resync["snapshot"]["type"] == "snapshot"
                    assert resync["snapshot"]["tick"] == tick_after_pause

                    resumed_on_reconnector = _next_non_snapshot(a_reconnected, own_match_id=match_a_id)
                    assert resumed_on_reconnector["type"] == "resumed"
                    resumed_on_peer = _next_non_snapshot(a_persistent, own_match_id=match_a_id)
                    assert resumed_on_peer["type"] == "resumed"
                    assert resumed_on_peer["matchId"] == match_a_id
                    assert manager.get_match(match_a_id).state is MatchRuntimeState.ACTIVE

                    # match B, again, is unaffected by match A's whole
                    # pause/reconnect/resume cycle.
                    _assert_isolated_probe(b1, own_match_id=created_b["matchId"])

                    # -- Step 9: timeout-forfeit. `a_reconnected` (p1) now
                    # disconnects for good (its own `with` block closes below,
                    # and this test never reconnects it again); `a_persistent`
                    # (p2) stays connected, well past its own eligibility, so
                    # the mandated outcome is a normal forfeit in p2's favor,
                    # never a no-contest.

                # a_reconnected closed -> p1 disconnects again.
                paused_again = _next_non_snapshot(a_persistent, own_match_id=match_a_id)
                assert paused_again["type"] == "paused"
                assert paused_again["disconnectedPlayerId"] == "p1"

                forfeit = _next_non_snapshot(a_persistent, own_match_id=match_a_id)
                assert forfeit["type"] == "forfeit"
                assert forfeit["matchId"] == match_a_id
                assert forfeit["forfeitingPlayerId"] == "p1"
                assert forfeit["winnerPlayerId"] == "p2"
                assert forfeit["reason"] == "disconnect_timeout"

                match_a_finished = manager.get_match(match_a_id)
                assert match_a_finished.state is MatchRuntimeState.FINISHED
                assert match_a_finished.result is not None
                assert match_a_finished.result.outcome is MatchOutcome.FORFEIT
                assert match_a_finished.result.winner_player_id == PLAYER_TWO
                assert match_a_finished.result.forfeiting_player_id == PLAYER_ONE
                final_tick = match_a_finished.game_state.tick if match_a_finished.game_state else None

                # match B is still completely unaffected by match A's forfeit.
                _assert_isolated_probe(b2, own_match_id=created_b["matchId"])
                assert manager.get_match(created_b["matchId"]).state is MatchRuntimeState.ACTIVE

        # -- Step 12: finish/dispose. `finish_match` already ran (via the
        # bound reconnect-coordinator finish hook, exactly like every other
        # `FINISHED` transition); `dispose_match` is the separate, explicit
        # bookkeeping-removal step `MatchManager` documents for that.
        #
        # `MatchRuntime.request_cancel`/`ReconnectCoordinator` internals
        # assert they are called from the event loop thread that owns their
        # tasks (issue #93 review) -- `TestClient`'s app runs on its own
        # background thread/loop, so `dispose_match` (unlike the plain
        # synchronous assertions elsewhere in this test) must be scheduled
        # onto that loop via the client's own blocking portal, exactly like
        # a real caller would if it ever invoked this from outside an async
        # request handler.
        assert client.portal is not None
        client.portal.call(manager.dispose_match, match_a_id)
        with pytest.raises(MatchNotFoundError):
            manager.get_match(match_a_id)

    # -- Step 13: replay the persisted authoritative command stream and
    # assert it reproduces an identical final engine state/result. This
    # calls `nether_earth.replay`/`nether_earth.engine` directly -- no
    # backend module is exercised by this call -- against the artifact
    # `ReplayWriter` wrote as a hook off the exact same `finish_match` call
    # that transitioned match A to FINISHED above.
    result = verify_replay(replay_dir, match_a_id, scenario=scenario, map_data=map_data)
    assert result.matches, (result.reproduced_snapshot, result.persisted_snapshot)

    meta = json.loads((replay_dir / match_a_id / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "finished"
    assert meta["final_tick"] == final_tick
    assert meta["result"]["outcome"] == "forfeit"
    assert meta["result"]["winner_player_id"] == "p2"
    assert meta["result"]["forfeiting_player_id"] == "p1"


def _create_app_with_frozen_reconnect_clock(replay_dir: Path) -> tuple[FastAPI, float]:
    """Build the exact same composition-root wiring as ``app.main.create_app``, except
    ``ReconnectCoordinator``'s ``monotonic_clock`` is frozen at a fixed value.

    Real, sequential ``WebSocketDisconnect``s for two players are always
    real wall-clock microseconds apart, so their two computed grace
    deadlines are (with the real ``time.monotonic`` clock) never *exactly*
    equal -- and per ``ReconnectCoordinator._resolve_expiry``'s own
    docstring, an unequal pair of deadlines always resolves to a normal
    forfeit for whichever disconnected first, never a no-contest (a
    genuinely equal pair of deadlines is the only case that resolves to
    no-contest). Freezing the clock both ``mark_disconnected`` calls read
    from makes both computed deadlines the exact same numeric value
    regardless of the real gap between the two disconnects, without any
    correctness-critical sleep -- exactly the "controllable time/deadlines"
    the milestone's own acceptance criterion calls for. ``asyncio.sleep``
    (the actual wait) is left real, so the two independent deadline-watcher
    tasks still each really wait ``_SHORT_GRACE_SECONDS`` before resolving.

    A hand-assembled fixture (mirroring every hook `create_app` wires) is
    used here rather than a `monotonic_clock` parameter on `create_app`
    itself: that injection point is inherently test-only (no real deployment
    ever wants a frozen clock), so it does not belong on the production
    factory's public signature the way `replay_dir`/`reconnect_grace_seconds`/
    `tick_rate_hz` legitimately do.

    Returns ``(app, frozen_time)`` -- ``frozen_time`` is only exposed for
    tests that want to assert against it directly; this test does not need
    to.
    """
    from app.match.reconnect import ReconnectCoordinator
    from app.match.runtime import MatchRuntimeRegistry
    from app.replay import ReplayWriter, make_replay_lifecycle_notifier, make_replay_tick_recorder
    from app.transport import ConnectionRegistry, create_websocket_router
    from app.transport.disconnects import make_disconnect_notifier
    from app.transport.snapshots import make_tick_broadcaster

    frozen_time = 1_000.0

    fastapi_app = FastAPI(title="Nether Earth (test: frozen reconnect clock)")
    connection_registry = ConnectionRegistry()
    runtime_registry = MatchRuntimeRegistry()
    replay_writer = ReplayWriter(base_dir=replay_dir)

    def _on_tick_factory(match: Any) -> Any:
        return make_tick_broadcaster(connection_registry, match.match_id)

    def _on_tick_commands_factory(match: Any) -> Any:
        return make_replay_tick_recorder(replay_writer, match.match_id)

    async def _combined_notify(event: Any) -> None:
        await make_disconnect_notifier(connection_registry)(event)
        await make_replay_lifecycle_notifier(replay_writer)(event)

    reconnect_coordinator = ReconnectCoordinator(
        notify=_combined_notify,
        grace_seconds=_SHORT_GRACE_SECONDS,
        runtime_registry=runtime_registry,
        monotonic_clock=lambda: frozen_time,
    )
    match_manager = MatchManager(
        runtime=runtime_registry,
        on_tick_factory=_on_tick_factory,
        reconnect=reconnect_coordinator,
        on_tick_commands_factory=_on_tick_commands_factory,
        on_match_start=replay_writer.start_match,
        on_match_finish=replay_writer.finish_match,
    )
    reconnect_coordinator.bind_finish_hook(match_manager.finish_match)

    fastapi_app.state.match_manager = match_manager
    fastapi_app.state.runtime_registry = runtime_registry
    fastapi_app.state.connection_registry = connection_registry
    fastapi_app.include_router(
        create_websocket_router(match_manager, runtime_registry, connection_registry)
    )
    return fastapi_app, frozen_time


def test_both_players_disconnected_and_never_returning_is_a_no_contest(tmp_path: Path) -> None:
    """Step 10: both players disconnect and neither returns -> no-contest.

    A separate match/app instance from the main scenario above: forfeit and
    no-contest are mutually exclusive terminal outcomes of the same
    disconnect/reconnect policy, so this is its own focused test rather than
    a continuation of a match that already reached ``FINISHED`` via forfeit.

    See ``_create_app_with_frozen_reconnect_clock``'s own docstring for why
    a frozen clock (not a real-time race) is what makes this outcome
    deterministic and repeatable rather than incidentally order-dependent.
    """
    replay_dir = tmp_path / "replays"
    app, _frozen_time = _create_app_with_frozen_reconnect_clock(replay_dir)

    with TestClient(app) as client:
        manager = _match_manager(client)

        with (
            client.websocket_connect("/ws") as ws_a,
            client.websocket_connect("/ws") as ws_b,
        ):
            created, _joined = _start_active_match_keeping_sockets_open(
                ws_a, ws_b, nickname_a="carol", nickname_b="dave"
            )
            match_id = created["matchId"]
            # Both sockets close here (end of this `with` block): real,
            # sequential disconnects, but the frozen `monotonic_clock` makes
            # both grace deadlines compute to the exact same value, so
            # neither player is ever treated as "still eligible" relative to
            # the other -- see `_resolve_expiry`'s own docstring.

        # Neither player has a live connection to receive the resulting
        # `no_contest` broadcast (both are gone -- that is the scenario),
        # so there is nothing to `receive_json()` on to synchronize with the
        # deadline-watcher tasks resolving server-side. Waiting is instead
        # done *inside* the app's own event loop (via the client's blocking
        # portal, like `dispose_match` above) as a short, bounded poll --
        # not an arbitrary sleep for correctness: the actual wait is
        # dominated by `_SHORT_GRACE_SECONDS`, and the resolution itself is
        # already fully deterministic (a genuine tie, thanks to the frozen
        # clock), never a race this poll is papering over.
        assert client.portal is not None
        match = client.portal.call(_wait_for_match_finished, manager, match_id)

    assert match.state is MatchRuntimeState.FINISHED
    assert match.result is not None
    assert match.result.outcome is MatchOutcome.NO_CONTEST
    assert match.result.reason == "disconnect_timeout_both"
    assert match.result.winner_player_id is None
    assert match.result.forfeiting_player_id is None

    meta = json.loads((replay_dir / match_id / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "finished"
    assert meta["result"]["outcome"] == "no_contest"
    assert meta["result"]["winner_player_id"] is None
