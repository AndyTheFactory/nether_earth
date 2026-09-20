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

Timing: every grace period is short (``reconnect_grace_seconds``), and the
main scenario runs at a faster-than-production tick rate
(``_FAST_TICK_RATE_HZ``, via ``app.main.create_app``'s own ``tick_rate_hz``
parameter) so the step-7 freeze proof has a meaningfully short, bounded
window to observe several genuine tick intervals in (see
``_FAST_TICK_RATE_HZ``'s own docstring) -- no arbitrary ``time.sleep``
anywhere for *correctness*; every wait that a test's own pass/fail depends
on is either a blocking ``receive_json()`` on a real broadcast/message the
stack itself produces, or a deadline-watcher `asyncio.Task` whose deadline
was shortened at construction time. The one exception (a short, explicit,
bounded ``asyncio.sleep`` in the step-7 freeze check) exists purely to
strengthen a *negative* assertion's detection power, not because the test
needs it to pass -- see that call site's own comment.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nether_earth.combat import FireCommand
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.commands import Command
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.orders import SetRobotOrderCommand
from nether_earth.scenario import default_pvp_scenario
from pydantic import TypeAdapter
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.match.manager import MatchManager
from app.match.models import Match, MatchNotFoundError, MatchOutcome, MatchRuntimeState
from app.match.world import load_standard_world
from app.protocol.common import CommandPayload
from app.replay.verify import load_commands_by_tick, verify_replay
from tests.transport._helpers import (
    _create,
    _join,
    _match_manager,
    _next_non_snapshot,
    _ready,
    _start_active_match_keeping_sockets_open,
)

#: Short enough that no real test run waits anywhere near the locked
#: production 60s default, long enough that "reconnect within grace" (steps
#: 7-8) has comfortable room against normal test-process scheduling jitter.
_SHORT_GRACE_SECONDS = 0.2

#: Faster than the real 20Hz production default (M7 Task 10 review, Important
#: I1): a broken/never-actually-pausing runtime needs several genuinely
#: elapsed tick intervals inside a short, bounded wait to be reliably caught
#: (at 20Hz a single tick interval is 50ms -- comparable to the wall-clock
#: jitter of the handful of synchronous WebSocket round trips this test
#: already does between its "before" and "after" tick reads, so a real
#: freeze regression could easily go undetected). At 100Hz, five tick
#: intervals is only 50ms of real wait -- still short, but long enough that
#: a genuinely-still-ticking runtime would almost certainly have ticked
#: several times, while a correctly-paused one ticks zero times.
_FAST_TICK_RATE_HZ = 40.0
_FAST_TICK_INTERVAL_S = 1.0 / _FAST_TICK_RATE_HZ

#: Validates a representative-command payload against the exact same
#: `CommandPayload` discriminated union the real WebSocket handler validates
#: incoming `command` frames against (M7 Task 10 review, Important I2).
#: `_submit_command` uses this to *prove*, not merely assume, that its
#: caller's payload cannot itself produce the same `invalid_message` error
#: code its own probe frame relies on -- see that function's docstring for
#: why this ambiguity would otherwise be a landmine.
_COMMAND_PAYLOAD_ADAPTER: TypeAdapter[Any] = TypeAdapter(CommandPayload)

#: Step 5's representative commands: (name, wire payload, expected persisted
#: engine `Command` subclass) -- one per M7 Task 9 adapter family (commander,
#: M4 construction/economy, M5 movement/orders/capture, M6 combat). The
#: expected-class column is what C1/C2's artifact assertions (M7 Task 10
#: review, Critical) check the persisted `commands.jsonl` batch against,
#: proving each command genuinely reached `engine.step` rather than merely
#: producing *some* transport-level outcome.
_REPRESENTATIVE_COMMANDS: tuple[tuple[str, dict[str, Any], type[Command]], ...] = (
    ("commander_move", {"kind": "commander_move", "dx": 1, "dy": 0}, CommanderMoveCommand),
    (
        "commander_set_vertical_intent",
        {"kind": "commander_set_vertical_intent", "rising": True},
        CommanderSetVerticalIntentCommand,
    ),
    (  # M4 construction/economy
        "select_module",
        {"kind": "select_module", "module": "cannon"},
        SelectModuleCommand,
    ),
    ("cancel_construction", {"kind": "cancel_construction"}, CancelConstructionCommand),  # M4
    ("launch_robot", {"kind": "launch_robot"}, LaunchRobotCommand),  # M4
    (  # M5 movement
        "direct_robot_move",
        {"kind": "direct_robot_move", "dx": 1, "dy": 0},
        DirectRobotMoveCommand,
    ),
    (  # M5 orders
        "set_robot_order_advance",
        {
            "kind": "set_robot_order",
            "entityId": "no-such-robot",
            "order": {"kind": "advance", "distanceMiles": 10},
        },
        SetRobotOrderCommand,
    ),
    (  # M5 orders/capture
        "set_robot_order_search_capture",
        {
            "kind": "set_robot_order",
            "entityId": "no-such-robot",
            "order": {"kind": "search_capture", "target": "neutral_factory"},
        },
        SetRobotOrderCommand,
    ),
    (  # M6 combat
        "robot_fire",
        {
            "kind": "robot_fire",
            "entityId": "no-such-robot",
            "weapon": "nuclear",
            "targetX": 5,
            "targetY": 5,
        },
        FireCommand,
    ),
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

    That structural proof relies on assuming the probe's own response --
    not the command's -- is the one carrying ``invalid_message``. This
    function proves that assumption instead of merely hoping it holds: it
    validates ``payload`` against the exact same ``CommandPayload``
    discriminated union the real transport validates incoming ``command``
    frames against (M7 Task 10 review, Important I2). A schema-invalid
    payload would itself be rejected at the envelope layer with
    ``invalid_message`` -- the same code the probe below always produces --
    which would silently misclassify the command's own rejection as
    "accepted" and leave the probe's real response corrupting every later
    read on this connection. Validating up front turns that landmine into
    a loud, immediate ``pydantic.ValidationError`` at the call site instead.
    """
    _COMMAND_PAYLOAD_ADAPTER.validate_python(payload)
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


def _wait_for_snapshot_tick_beyond(
    ws: WebSocketTestSession, *, own_match_id: str, min_tick: int
) -> int:
    """Block on real ``snapshot`` broadcasts on ``ws`` until one reports ``tick > min_tick``.

    This is the C1 fix (M7 Task 10 review, Critical): a command queued into
    the real ``MatchRuntime``'s pending batch is only actually fed to
    ``engine.step`` -- and therefore only actually appended to the
    persisted replay artifact -- at the *next* tick boundary. Disconnecting
    (or otherwise tearing the match down) before that boundary fires
    discards the pending batch unseen, so a caller that queued commands and
    wants to assert on their *persisted, engine-validated* outcome must
    first prove a tick has genuinely elapsed since submission -- via a real
    broadcast this test itself blocks on, never a sleep. Returns the tick
    number of the first qualifying snapshot observed.
    """
    for _ in range(1000):
        message = ws.receive_json()
        assert message["type"] == "snapshot", message
        assert message["matchId"] == own_match_id, message
        if message["tick"] > min_tick:
            return int(message["tick"])
    raise AssertionError(f"no snapshot with tick > {min_tick} ever arrived")


def _wait_for_persisted_batch(
    ws: WebSocketTestSession,
    *,
    own_match_id: str,
    replay_dir: Path,
    match_id: str,
    after_tick: int,
    expected_count: int,
    max_extra_ticks: int = 20,
) -> tuple[int, dict[int, tuple[Any, ...]]]:
    """Wait for a persisted tick after ``after_tick`` holding at least ``expected_count`` commands.

    A queued command batch is only fed to ``engine.step`` (and therefore
    only persisted) at the *next* tick boundary after submission (see
    ``_wait_for_snapshot_tick_beyond``'s own docstring) -- but "the next
    tick boundary" is not necessarily the very first one observed after
    submission if the submitting round trips themselves happened to
    straddle a tick boundary. This polls forward tick by tick (each step
    blocking on a real broadcast, never a sleep) up to ``max_extra_ticks``
    times, which stays short and bounded while tolerating that timing
    variance rather than assuming the batch always lands in exactly the
    very next tick (M7 Task 10 review, Critical C1).
    """
    current_tick = after_tick
    for _ in range(max_extra_ticks):
        current_tick = _wait_for_snapshot_tick_beyond(
            ws, own_match_id=own_match_id, min_tick=current_tick
        )
        commands_by_tick = load_commands_by_tick(replay_dir, match_id)
        candidates = [
            tick
            for tick in commands_by_tick
            if after_tick < tick <= current_tick and len(commands_by_tick[tick]) >= expected_count
        ]
        if candidates:
            return candidates[0], commands_by_tick
    raise AssertionError(
        f"no persisted tick with >= {expected_count} commands appeared within "
        f"{max_extra_ticks} ticks after {after_tick} -- persisted stream: "
        f"{sorted(load_commands_by_tick(replay_dir, match_id))}"
    )


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
    # The app plays every match on the scenario-overlaid standard world
    # (M9.1 audit gap G1), so replay verification must rebuild the same
    # tick-0 state from it rather than from a 1x1 placeholder map.
    world = load_standard_world(scenario)
    replay_dir = tmp_path / "replays"
    app = create_app(
        replay_dir=replay_dir,
        reconnect_grace_seconds=_SHORT_GRACE_SECONDS,
        tick_rate_hz=_FAST_TICK_RATE_HZ,
    )

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
                    created_a, _joined_a, _snapshot_a = _start_active_match_keeping_sockets_open(
                        a_disconnecting, a_persistent, nickname_a="alice", nickname_b="adam"
                    )
                    match_a_id = created_a["matchId"]
                    match_a_at_start = manager.get_match(match_a_id)
                    assert match_a_at_start.game_state is not None
                    tick_before_commands = match_a_at_start.game_state.tick
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
                    # expected transport outcomes here. `_REPRESENTATIVE_COMMANDS`
                    # is a table (M7 Task 10 review, Minor M3) of (name,
                    # payload, expected persisted engine `Command` subclass) --
                    # the expected-class column feeds the C1/C2 artifact
                    # assertions below (M7 Task 10 review, Critical C1/C2).
                    outcomes = {
                        name: _submit_command(
                            a_disconnecting,
                            match_id=match_a_id,
                            player_id="p1",
                            session_token=created_a["sessionToken"],
                            sequence=sequence,
                            payload=payload,
                        )
                        for sequence, (name, payload, _expected_class) in enumerate(
                            _REPRESENTATIVE_COMMANDS
                        )
                    }
                    for name, outcome in outcomes.items():
                        assert outcome in ("accepted", "invalid_command_payload", "command_rejected"), (
                            name,
                            outcome,
                        )

                    # -- Steps 5/13 (Critical C1/C2, M7 Task 10 review): prove
                    # the 9 representative commands above actually reached
                    # `engine.step` and were persisted, rather than being
                    # silently discarded by an unconsumed pending batch. Block
                    # on a real broadcast (never a sleep) on `a_persistent`
                    # until a tick strictly after submission has genuinely
                    # been observed, which is only possible once the real
                    # `MatchRuntime` has drained its pending batch into
                    # `engine.step` and this hook has appended the result to
                    # `commands.jsonl`.
                    representative_tick, commands_by_tick = _wait_for_persisted_batch(
                        a_persistent,
                        own_match_id=match_a_id,
                        replay_dir=replay_dir,
                        match_id=match_a_id,
                        after_tick=tick_before_commands,
                        expected_count=len(_REPRESENTATIVE_COMMANDS),
                    )
                    tick_after_commands = representative_tick
                    recorded_commands = commands_by_tick[representative_tick]
                    assert len(recorded_commands) == len(_REPRESENTATIVE_COMMANDS), recorded_commands
                    recorded_classes = sorted(type(command).__name__ for command in recorded_commands)
                    expected_classes = sorted(
                        expected_class.__name__ for _name, _payload, expected_class in _REPRESENTATIVE_COMMANDS
                    )
                    assert recorded_classes == expected_classes, (recorded_classes, expected_classes)

                    # Cross-check the debug event summary `ReplayWriter` also
                    # persisted for `representative_tick`: `engine.step` emits
                    # exactly one `CommandAccepted`/`CommandRejected` event per
                    # input command (see `nether_earth.engine.step`'s own
                    # docstring), so this independently confirms all 9 really
                    # reached the engine's per-command validation, not just
                    # that 9 opaque records exist on disk.
                    raw_lines = (
                        (replay_dir / match_a_id / "commands.jsonl").read_text(encoding="utf-8").splitlines()
                    )
                    representative_line = next(
                        json.loads(line)
                        for line in raw_lines
                        if line.strip() and json.loads(line)["tick"] == representative_tick
                    )
                    # On the real world (M9.1 gap G1) the same tick also
                    # emits gameplay events (e.g. a commander move starting),
                    # so count only the per-command structural verdicts.
                    recorded_events = [
                        event
                        for event in representative_line["events"]
                        if event["type"] in ("CommandAccepted", "CommandRejected")
                    ]
                    assert len(recorded_events) == len(_REPRESENTATIVE_COMMANDS), recorded_events

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
                    # right before closing the socket. This read is itself
                    # racy against the real, concurrently-running tick loop
                    # (a tick may complete in the instant between this read
                    # and the socket actually closing) -- see the tolerance
                    # built into the post-pause assertion below (M7 Task 10
                    # review, Important I1) for why that race is handled
                    # explicitly rather than assumed away.
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

                # M7 Task 10 review, Important I1: the `paused` broadcast
                # above arrives essentially immediately, so reading the tick
                # right after it is not, on its own, strong evidence of a
                # freeze -- a 50ms (20Hz default) tick interval is comparable
                # to the wall-clock cost of the handful of synchronous
                # `receive_json()`/assertion calls involved, so a genuinely
                # broken (never-actually-pausing) runtime could easily tick
                # zero times in that window purely by luck. This app was
                # built with `tick_rate_hz=_FAST_TICK_RATE_HZ` specifically so
                # a short, explicit, bounded real wait here (scheduled onto
                # the app's own event loop, not a plain `time.sleep`) spans
                # several genuine tick intervals: a still-ticking runtime
                # would almost certainly advance multiple times in that
                # window, while a correctly-paused one advances zero times.
                assert client.portal is not None
                client.portal.call(asyncio.sleep, 5 * _FAST_TICK_INTERVAL_S)

                match_a_after_pause = manager.get_match(match_a_id)
                assert match_a_after_pause.game_state is not None
                tick_after_pause = match_a_after_pause.game_state.tick
                # Tolerates at most the one benign tick that may have been
                # already in flight the instant `tick_before_disconnect` was
                # read (the race noted above) -- anything beyond that, after
                # a multi-tick-interval bounded wait, is a genuine freeze
                # regression, not scheduling jitter.
                assert tick_after_pause <= tick_before_disconnect + 1, (
                    "engine tick advanced while the match was PAUSED_DISCONNECTED "
                    f"(before={tick_before_disconnect}, after={tick_after_pause})"
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

        # M7 Task 10 review, Minor M1: match B is still ACTIVE (its own
        # sockets closed with the `with (b1, b2)` block above, which -- like
        # match A's own disconnects -- pauses rather than finishes it), so
        # disposing it too and asserting neither match's `MatchRuntime`
        # remains registered makes "runtime tasks/connections are cleaned up
        # after tests" an explicit assertion here, not just an incidental
        # property of `TestClient`'s own teardown.
        client.portal.call(manager.dispose_match, created_b["matchId"])
        with pytest.raises(MatchNotFoundError):
            manager.get_match(created_b["matchId"])
        runtime_registry = client.app.state.runtime_registry
        assert runtime_registry.get(match_a_id) is None
        assert runtime_registry.get(created_b["matchId"]) is None

    # -- Step 13: replay the persisted authoritative command stream and
    # assert it reproduces an identical final engine state/result. This
    # calls `nether_earth.replay`/`nether_earth.engine` directly -- no
    # backend module is exercised by this call -- against the artifact
    # `ReplayWriter` wrote as a hook off the exact same `finish_match` call
    # that transitioned match A to FINISHED above.
    result = verify_replay(replay_dir, match_a_id, scenario=scenario, world=world)
    assert result.matches, (result.reproduced_snapshot, result.persisted_snapshot)

    meta = json.loads((replay_dir / match_a_id / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "finished"
    assert meta["final_tick"] == final_tick
    assert meta["result"]["outcome"] == "forfeit"
    assert meta["result"]["winner_player_id"] == "p2"
    assert meta["result"]["forfeiting_player_id"] == "p1"

    # -- Critical C2 (M7 Task 10 review): without these, the whole scenario
    # above would pass identically even if `MatchRuntime` had never ticked
    # at all -- `verify_replay`'s `result.matches` alone proves the persisted
    # stream and a *fresh* engine run agree with each other, not that either
    # one is non-trivial. These pin down, independently of `verify_replay`,
    # that the tick loop genuinely advanced past tick 0 during the active
    # phase and that the persisted stream is not empty.
    assert final_tick is not None
    # `tick_after_commands` is, by `_wait_for_snapshot_tick_beyond`'s own
    # contract, strictly greater than `tick_before_commands` (>= 0) -- i.e.
    # the tick counter genuinely advanced past 0 during the active phase --
    # and the match only finished (forfeit) well after that point, so
    # `final_tick` must be at least as large.
    assert tick_after_commands > 0
    assert final_tick >= tick_after_commands
    final_commands_by_tick = load_commands_by_tick(replay_dir, match_a_id)
    assert final_commands_by_tick, "commands.jsonl persisted no ticks at all"
    assert representative_tick in final_commands_by_tick
    assert len(final_commands_by_tick[representative_tick]) == len(_REPRESENTATIVE_COMMANDS)


def test_both_players_disconnected_and_never_returning_is_a_no_contest(tmp_path: Path) -> None:
    """Step 10: both players disconnect and neither returns -> no-contest.

    A separate match/app instance from the main scenario above: forfeit and
    no-contest are mutually exclusive terminal outcomes of the same
    disconnect/reconnect policy, so this is its own focused test rather than
    a continuation of a match that already reached ``FINISHED`` via forfeit.

    Real, sequential ``WebSocketDisconnect``s for two players are always
    real wall-clock microseconds apart, so their two computed grace
    deadlines are (with the real ``time.monotonic`` clock) never *exactly*
    equal -- and per ``ReconnectCoordinator._resolve_expiry``'s own
    docstring, an unequal pair of deadlines always resolves to a normal
    forfeit for whichever disconnected first, never a no-contest (a
    genuinely equal pair of deadlines is the only case that resolves to
    no-contest). ``create_app``'s internal ``_reconnect_monotonic_clock``
    seam (M7 Task 10 review, Important I3 -- freezing the clock both
    ``mark_disconnected`` calls read from) makes both computed deadlines the
    exact same numeric value regardless of the real gap between the two
    disconnects, without any correctness-critical sleep -- exactly the
    "controllable time/deadlines" the milestone's own acceptance criterion
    calls for, and without hand-assembling a second copy of ``create_app``'s
    wiring the way this test previously did. ``asyncio.sleep`` (the actual
    wait) is left real, so the two independent deadline-watcher tasks still
    each really wait ``_SHORT_GRACE_SECONDS`` before resolving.
    """
    replay_dir = tmp_path / "replays"
    frozen_time = 1_000.0
    app = create_app(
        replay_dir=replay_dir,
        reconnect_grace_seconds=_SHORT_GRACE_SECONDS,
        _reconnect_monotonic_clock=lambda: frozen_time,
    )

    with TestClient(app) as client:
        manager = _match_manager(client)

        with (
            client.websocket_connect("/ws") as ws_a,
            client.websocket_connect("/ws") as ws_b,
        ):
            created, _joined, _snapshot = _start_active_match_keeping_sockets_open(
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
