# Match runtime (backend)

## Purpose

The backend hosts many in-memory matches in one process. It runs each match's 20 Hz loop, queues and orders client commands, manages the lobby and sessions, pauses and resolves disconnects, persists a replay of every match, and bounds what one connection can do. It makes no gameplay decision: every rule is the engine's.

## State involved

- `match.models.Match`: `match_id`, `join_code`, `state` (`MatchRuntimeState`: `WAITING`, `ACTIVE`, `PAUSED_DISCONNECTED`, `FINISHED`), `players` (`PlayerSlot`: `player_id`, `nickname`, `session_token`, `ready`), `ai_player_id` (solo only), `seed`, `game_state`, `result`, `created_at`, `finished_at`, `abandoned_at`.
- `match.runtime.MatchRuntime`: the queued commands, the last accepted client sequence per player, the tick task.
- `match.reconnect.ReconnectCoordinator`: per match, per disconnected player, a monotonic deadline and a watcher task.
- `transport.connections.ConnectionRegistry`: the one live socket per `(match_id, player_id)`.
- Replay artifacts on disk ([below](#replay-writer-and-retention)).

## Algorithm

### Lobby (`match.manager.MatchManager`)

1. **create** (`create_match`, or `create_solo_match` for "Play vs computer"): validate the nickname (`_validate_nickname`), refuse with `ServerBusyError` at `max_matches`, draw a seed (`secrets.randbits(63)`), a 6-character join code (`A–Z0–9`, PvP only) and a session token (`secrets.token_urlsafe(32)`); the creator takes `p1`. A solo match records `ai_player_id = p2` and no join code.
2. **join** (`join_match`): validate the nickname; reject one equal to the creator's after `_nickname_key` (NFKC + casefold) with `InvalidNicknameError`; the guest takes `p2`.
3. **ready** (`set_ready`): when every human slot is ready (the AI is always ready), the match goes `ACTIVE`: `scenario.create_initial_state(scenario, world, seed)` builds tick 0 (the scenario marks `p2` as `"ai"` in a solo match), the replay artifact starts, and the runtime starts.

Nickname validation strips whitespace and rejects: empty; any character in the `Cc`, `Cf`, `Cs`, `Co`, `Cn` categories (a ZWJ between two `So`/`Sk`/`Mn` characters is allowed); bidirectional controls (U+202A–U+202E, U+2066–U+2069); blank look-alikes (U+115F, U+1160, U+3164, U+FFA0, U+2800, U+034F); and names with no letter, number, punctuation or symbol. The protocol schema limits it to 32 characters.

### Sweep (`MatchManager.sweep`, every 15 s from `app.main`)

Disposes `FINISHED` matches after `finished_retention_s`; `WAITING` matches older than `waiting_timeout_s`; and `WAITING` matches whose `abandoned_at` (set by `mark_lobby_abandoned` when the last socket goes, cleared by `mark_lobby_occupied` when one attaches) is older than `abandoned_lobby_grace_s`. Sockets of a disposed lobby get an `error` with code `match_expired` and are closed. `ACTIVE` and `PAUSED_DISCONNECTED` matches are never swept.

### Tick loop (`MatchRuntime._run`)

1. Wait for the start announcement; then, while the match exists:
2. `FINISHED` → stop. Not `ACTIVE` (paused) → poll and re-base the schedule to now.
3. `_advance_one_tick`, under the step lock: drain the queue; `engine.step(state, commands, world=world)`; store the new state; then call the replay recorder (`on_tick_commands`) and the tick observers (`on_tick`: the snapshot broadcast, then the victory finalizer).
4. Next tick at `previous + 1/20 s`. If that time has already passed, yield once, log every `_OVERRUN_WARNING_EVERY_N_TICKS` consecutive overruns, and re-base to now — no catch-up burst.

Commands: `submit_command` rejects a command whose client sequence is not greater than the last one accepted for that player (duplicate or replayed); otherwise it is queued for the next tick. Ordering inside the tick is the engine's `(player, sequence)` sort. The AI's commands are added by the engine itself.

Broadcast: a full `snapshot` message to every connection of the match after every tick (`transport.snapshots.make_tick_broadcaster`). Engine events are not sent. The victory finalizer (`transport.victory`) calls `finish_match` and sends `finished` when the tick's events contain a `VictoryEvent`.

### Disconnect and reconnect (`match.reconnect.ReconnectCoordinator`)

1. A socket teardown (close, error, or `leave`) for the socket currently registered for its slot calls `mark_disconnected`. The first one pauses an `ACTIVE` match (`PAUSED_DISCONNECTED`, `paused` broadcast) and every disconnected human gets a deadline `now + grace_seconds` with a watcher task. A stale socket's late teardown is ignored.
2. `reconnect` with a valid token rebinds the socket (closing an older one with 4000), cancels that player's watcher and sends `resync` with the current snapshot; when no human is disconnected any more the match resumes (`resumed`).
3. A watcher that fires calls `_resolve_expiry`: if the opponent is connected or its deadline is later than the expiring one, the expiring player forfeits (`forfeit`); if the opponent's deadline is equal or earlier, the match is a no-contest (`no_contest`). Deadlines are compared as values, so scheduler jitter cannot flip the outcome. Exactly one outcome is produced; the other watcher is cancelled; `finish_match` ends the match.
4. The AI seat has no slot, is never disconnected and is always an eligible opponent.

While paused, no tick runs, so the engine state cannot change.

### Transport (`transport.ws`, `transport.limits`)

Per WebSocket:

1. refuse the handshake (1008) when an `Origin` is present and not the configured public origin;
2. until a session is bound, the socket must bind within `UNBOUND_SOCKET_TIMEOUT_S`, or gets `bind_timeout` and is closed;
3. every inbound frame spends a `TokenBucket` token (`MESSAGE_RATE_PER_S` sustained, `MESSAGE_BURST` burst); none left → `rate_limited`, close;
4. frames over `MAX_MESSAGE_BYTES` → `message_too_large`, close 1009;
5. schema-invalid frames → `invalid_message`, the socket stays open;
6. after `MAX_FAILED_JOINS` failed joins the socket is closed;
7. once bound, a message for another session → close 1008; `create`/`join` on a bound socket → `already_bound`, close 1008;
8. `attach` makes this socket the live one for its slot and closes a predecessor with `_REPLACED_CLOSE_CODE` (4000);
9. a send that cannot finish in `SEND_TIMEOUT_S` closes that socket, so the disconnect policy takes over instead of the tick loop blocking.

`transport.commands.payload_to_command` maps each schema payload to the engine `Command` (shape only; a diagonal `dx`/`dy` is a `CommandPayloadError`).

### Replay writer and retention

`replay.writer.ReplayWriter`, with a single-thread executor so I/O never runs on the event loop and each match's lines stay in order:

1. `start_match`: create `<replay_dir>/<match_id>/` and write `meta.json` (`status: "in_progress"`, rules version and hash, scenario, map, seed, nicknames, `seat_controllers`).
2. Every tick: one line in `commands.jsonl` with the drained human commands and an event summary. Pause, resume, forfeit and no-contest go to `lifecycle.jsonl` with wall-clock time.
3. `finish_match`: rewrite `meta.json` atomically (temp file + `os.replace`) with `status: "finished"`, the final tick, result and snapshot.

At most `MAX_PENDING_WRITES` jobs may be queued; beyond that a write is dropped and logged (`replay_write_failed`, action `backlog`). Session tokens are never written; match ids are checked against a safe pattern before use as a directory name.

At startup `replay.retention.mark_interrupted` flips every `in_progress` artifact to `interrupted`. When `replay_retention_days` is set, `prune_replays` runs every `REPLAY_PRUNE_INTERVAL_S` and deletes `finished`/`interrupted` artifacts whose `meta.json` is older than the limit; `in_progress` and unknown statuses are never deleted.

`replay.verify.verify_replay` checks the rules identity first (`ReplayRulesMismatchError`), rebuilds the initial state from the scenario, map, seed and `seat_controllers`, replays the human commands per tick through the engine, and compares the final snapshot with the stored one.

### Health

`/health` is liveness. `/ready` returns 503 while shutting down or when a temp file cannot be created in the replay directory (probed off the event loop); match, runtime and connection counts are added outside production, or for a loopback client.

## Constants

Deployment settings (`config.Settings`; environment variables in [technical-spec.md](../../_specs/technical-spec.md#24-deployment-and-supply-chain)):

| Setting | Default |
|---|---:|
| `max_matches` | 200 |
| `finished_retention_s` | 300 |
| `waiting_timeout_s` | 900 |
| `abandoned_lobby_grace_s` | 30 |
| `replay_retention_days` | unset (keep all) |
| reconnect grace (`DEFAULT_GRACE_SECONDS`) | 60 s |
| `TICK_RATE_HZ` | 20 |
| `SWEEP_INTERVAL_S` | 15 s |
| `REPLAY_PRUNE_INTERVAL_S` | 3600 s |

Transport limits (`transport/limits.py`, `transport/ws.py`):

| Constant | Value |
|---|---:|
| `MAX_MESSAGE_BYTES` | 16 KiB |
| `MESSAGE_RATE_PER_S` / `MESSAGE_BURST` | 40 / 80 |
| `MAX_FAILED_JOINS` | 5 |
| `SEND_TIMEOUT_S` | 5 s |
| `UNBOUND_SOCKET_TIMEOUT_S` | 30 s |
| `MAX_PENDING_WRITES` | 10,000 |

## Determinism notes

Only the engine is deterministic; the runtime is not, and does not need to be. What the runtime guarantees is that the engine sees exactly what is recorded: the drained command batch of each tick is written to `commands.jsonl` with the tick it was applied on, the seed and rules identity are in `meta.json`, and the AI's commands are re-derived. Wall-clock time is used only for scheduling, deadlines, sweeps and log timestamps.

## Spectrum evidence

None: the original is single-player. Everything here is a project rule.

## Deviations

None (network behaviour has no Spectrum counterpart).

## Tests that pin it

- `backend/tests/match/test_runtime.py::test_loop_calls_engine_step_exactly_once_per_tick`
- `backend/tests/match/test_runtime.py::test_duplicate_client_sequence_rejected`
- `backend/tests/match/test_runtime.py::test_deterministic_ordering_regardless_of_submission_order`
- `backend/tests/match/test_runtime.py::test_no_ticks_while_paused_disconnected`
- `backend/tests/match/test_runtime.py::test_overrun_tick_loop_still_yields_to_other_tasks`
- `backend/tests/match/test_manager.py::test_join_rejects_nickname_equal_to_creator`
- `backend/tests/match/test_manager.py::test_nickname_invisible_or_format_characters_rejected`
- `backend/tests/match/test_sweep.py::test_abandoned_lobby_is_disposed_after_grace`
- `backend/tests/match/test_sweep.py::test_expired_lobby_owner_is_told_and_disconnected`
- `backend/tests/match/test_sweep.py::test_live_matches_are_never_swept`
- `backend/tests/match/test_reconnect.py::test_one_expiry_while_opponent_still_within_grace_is_a_forfeit_not_no_contest`
- `backend/tests/match/test_reconnect.py::test_both_deadlines_expired_with_neither_returning_is_no_contest`
- `backend/tests/match/test_reconnect.py::test_near_simultaneous_both_disconnect_forfeits_correctly_despite_scheduler_jitter`
- `backend/tests/match/test_reconnect.py::test_tick_count_is_frozen_while_paused_and_resumes_after_reconnect`
- `backend/tests/match/test_solo.py::test_ai_seat_never_triggers_a_pause`
- `backend/tests/match/test_solo.py::test_human_grace_expiry_forfeits_a_solo_match_to_the_ai`
- `backend/tests/transport/test_ws.py::test_second_socket_with_same_session_closes_the_first`
- `backend/tests/transport/test_ws.py::test_reconnect_returns_resync_snapshot_and_rebinds_connection`
- `backend/tests/transport/test_hardening.py::test_foreign_origin_handshake_is_refused`
- `backend/tests/transport/test_hardening.py::test_message_flood_is_rate_limited_and_closed`
- `backend/tests/transport/test_hardening.py::test_unbound_socket_is_closed_at_the_deadline`
- `backend/tests/transport/test_hardening.py::test_join_code_guessing_is_capped_per_connection`
- `backend/tests/transport/test_hardening.py::test_stalled_peer_is_closed_instead_of_blocking`
- `backend/tests/transport/test_hardening.py::test_match_capacity_returns_server_busy`
- `backend/tests/replay/test_replay_log.py::test_replay_reproduces_final_engine_snapshot`
- `backend/tests/replay/test_replay_log.py::test_replay_with_other_rules_is_rejected_before_replaying`
- `backend/tests/replay/test_replay_log.py::test_no_session_tokens_in_any_persisted_file`
- `backend/tests/replay/test_replay_log.py::test_slow_disk_does_not_delay_ticks`
- `backend/tests/replay/test_retention.py::test_startup_marks_orphans_interrupted`
- `backend/tests/replay/test_retention.py::test_prune_deletes_old_finished_and_interrupted_only`
- `backend/tests/test_health.py::test_ready_hides_counts_in_production`
- `backend/tests/test_health.py::test_ready_reports_counts_in_production_to_loopback`
