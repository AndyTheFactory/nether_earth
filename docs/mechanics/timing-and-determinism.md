# Timing and determinism

## Purpose

Every gameplay outcome is a pure function of the initial state, the map, the rules, the match seed and the accepted command stream. This page describes the clock, the per-tick phase order of `engine.step`, how randomness is derived, and how a match is reproduced.

## State involved

- `GameState.tick` — the authoritative tick counter; `GameState.seed` — the immutable match seed.
- `GameState` is a frozen value; every phase returns a new one. Collections inside it (`robots`, `commanders`, `projectiles`, …) are kept in canonical id order by the `with_*` constructors in `state.py`.
- `Command` (`commands.py`): `player`, `sequence`, plus the concrete command's fields.
- `Event` (`events.py`): every event carries a `sequence` assigned by one `EventSequencer` per tick.

## Algorithm

### Clock (`clock.py`)

`TICK_RATE_HZ = 20`, `TICKS_PER_GAME_HOUR = 120`, `TICKS_PER_GAME_TWELVE_HOURS = 1440`, `TICKS_PER_GAME_DAY = 2880`. Boundaries are detected with integer floors (`ticks_to_game_hours_floor`, `ticks_to_game_days_floor`); the float helpers (`game_hours_elapsed`, `game_days_elapsed`) are for display only. Nothing in the engine reads wall-clock time.

Periodic engine work runs on *cadence ticks*: positive multiples of a constant (tick 0 never qualifies). Vertical commander physics (`commander_movement.is_vertical_update_tick`) and projectile advances (`combat.is_projectile_advance_tick`) use 4 ticks, one Spectrum game cycle.

### One tick (`engine.step`)

`engine.step(state, commands, world=world)` computes tick `state.tick + 1`. The steps are named in the code's comments; they run in this order:

1. **Step 0 — AI seats.** When the state has AI seats, `ai.seat.issue_ai_commands` runs each seat's planner on decision ticks and appends its commands to the batch ([ai.md](ai.md)).
2. **Validation.** `commands.validate_command_batch` checks each command structurally (known player, non-negative `sequence`), rejects *every* command that shares a `(player, sequence)` key with another (`DUPLICATE_SEQUENCE`), and returns the batch sorted by `(player.value, sequence)` (then `repr` as a final tie-break). A `CommandAccepted` or `CommandRejected` event is emitted per command. Gameplay-level rejections later in the tick are silent: the command simply has no effect.
3. **Step 2 — commander moves complete** (`advance_all_horizontal_transitions`), so that a move command arriving on the completion tick can start.
4. **Step 1 — commander commands**: `CommanderMoveCommand` (no-op while docked or while the player's construction screen is open) and `CommanderSetVerticalIntentCommand`.
5. **Step 2b — robot moves and turns complete** (`movement.advance_all_robot_transitions`).
6. **Step 2b2 — orders.** `SetRobotOrderCommand`s apply; `orders.evaluate_orders` evaluates every standing order against one entry state; `apply_order_evaluations` writes back order changes; `autonomous_combat.gate_order_requests` keeps only the move requests of robots at their update that will not fire.
7. **Step 2c — robot moves start**: walk-out, order and `DirectRobotMoveCommand` requests go through one `reservations.apply_robot_move_batch` against the physical world; then `autonomous_combat.settle_walk_outs`.
8. **Step 2c2 — combat**: (a) `combat.advance_projectiles` and `apply_damage` for each hit; (b) `FireCommand`s — nuclear through `destruction.execute_nuclear_detonation`, normal weapons through `combat.apply_fire`; (c) `autonomous_combat.consume_engagement_intents` for the intents computed in Step 2b2. A victory check follows each nuclear path.
9. **Step 2d — capture**: `capture.advance_capture`; a war-base capture triggers `victory.evaluate_victory`. From here on the step uses the world as it stands after captures and destruction.
10. **Step 3 — undock** a docked commander holding rise (`docking.apply_undock`).
11. **Step 4 — vertical physics** on cadence ticks for every free commander whose player has no open construction screen.
12. **Step 5 — auto-dock** (`docking.auto_dock_with_event`); docking clears the robot's cached hunt route.
13. **Step 6 — docked commanders follow** their robot's anchor and top.
14. **Step 7 — heli-pad landing** (`heli_pad.detect_heli_pad_landing`) opens construction, skipped while the exit lift runs.
15. **Step 8 — construction commands** in canonical order: remote entry (AI only), select, deselect, cancel (EXIT MENU), launch (START ROBOT).
16. **Step 9 — daily production** (`resource_production.apply_daily_production`) when the tick crosses a day boundary.
17. The state's tick becomes `state.tick + 1`; the events are returned sorted by sequence (`events.order_events`).

At most one `VictoryEvent` is emitted per tick, however many of the three victory sites find the condition.

### Randomness (`rng.py`)

There is no stored RNG. `rng.derive_seed(*parts)` mixes integers directly and strings through CRC-32 into one 64-bit seed (SplitMix-style `mix64`); `rng.MatchRandom(seed)` wraps a private `random.Random`. Each consumer builds a fresh stream from the match seed and its own coordinates:

| Consumer | Seed parts | Draw |
|---|---|---|
| destination contention (`reservations.contention_rng`) | match seed, tick | `choice` over contenders |
| non-electronic detour (`navigation.derive_wander_seed`) | match seed, `tick // dumb_wander_commit_ticks`, robot id | `shuffle` of two perpendicular steps |
| AI planner (`ai.seat`) | match seed, `"ai"`, player id, tick; then the sub-planner name | currently no draws |

Because each stream is keyed by what it is for, adding a draw in one system never shifts another system's outcomes.

### Replays

Engine side, `replay.ReplayFixture` holds a map, scenario, seed, initial commanders/robots and `commands_by_tick`; `replay.run_fixture` drives `engine.new_game` and `engine.step` tick by tick, and `run_from_state` continues from an existing state. The backend's persisted artifacts and their verification are described in [match-runtime.md](match-runtime.md#replay-writer-and-retention).

## Constants

| Name | Value | Where |
|---|---:|---|
| `TICK_RATE_HZ` | 20 | `clock.py` |
| `TICKS_PER_GAME_HOUR` | 120 | `clock.py` |
| `TICKS_PER_GAME_DAY` | 2880 | `clock.py` |
| `commander_vertical_update_ticks` | 4 | `rules.py` |
| `projectile_advance_ticks` | 4 | `rules.py` |
| `ai_decision_interval_ticks` | 4 | `rules.py` |
| `dumb_wander_commit_ticks` | 16 | `rules.py` |

`RULES_VERSION` (`"cr005"`) and `rules_content_hash` (SHA-256 of the canonical JSON of every `EngineRules` field) identify the rule set in every replay.

## Determinism notes

- Phases read and write immutable states; a phase that changes nothing returns the same object.
- Commands are applied in `(player, sequence)` order; robots, commanders, projectiles and structures are iterated in id order; events are numbered in emission order.
- Navigation, capture, contention and the AI never iterate a `set` or `dict` where order could leak into an outcome.
- Memo caches (`capture_footprint`, `effective_world`, `scenery_world`, navigation views) are keyed by object identity and hold their inputs alive; they are pure caches and cannot change results.

## Spectrum evidence

- `MIN_INTERRUPTS_PER_GAME_CYCLE: equ 10` ("game maximum speed is 5 frames per second"): one game cycle = 200 ms = 4 ticks at 20 Hz.
- `La69a_game_loop` runs `Lb0ca_update_robots_bullets_and_ai` (robots, then bullets) and `Lad62_increase_time` once per cycle.

## Deviations

None at this level. The commander step order (completions before commands) has no Spectrum counterpart; see [resolved-questions.md](../../_specs/resolved-questions.md#idle-tick-between-commander-cells).

## Tests that pin it

- `engine/tests/test_clock.py::test_constants_match_locked_spec_values`
- `engine/tests/test_clock.py::test_module_does_not_reference_wall_clock_apis`
- `engine/tests/test_rng.py::test_same_seed_produces_identical_sequences`
- `engine/tests/test_rng.py::test_match_random_does_not_touch_global_random_state`
- `engine/tests/test_commands.py::test_order_commands_sorts_by_player_then_sequence`
- `engine/tests/test_commands.py::test_validate_command_batch_rejects_colliding_sequence_deterministically`
- `engine/tests/test_engine.py::test_step_events_are_ordered_by_canonical_command_order_not_input_order`
- `engine/tests/test_engine.py::test_five_hundred_ticks_is_deterministic_and_reproducible`
- `engine/tests/test_engine.py::test_module_does_not_reference_wall_clock_apis`
- `engine/tests/test_events.py::test_order_events_is_deterministic_regardless_of_collection_order`
- `engine/tests/test_engine_commander_integration.py::test_held_move_chains_cells_without_an_idle_tick`
- `engine/tests/test_replay.py::test_run_fixture_is_repeatable_across_many_ticks`
- `engine/tests/test_ai_seat.py::test_planner_random_stream_is_seeded_from_match_seed_seat_and_tick`
- `engine/tests/test_rules.py::test_rules_content_hash_is_a_deterministic_sha256_of_the_rules`
- `backend/tests/acceptance/test_m9_full_match.py::test_committed_fixture_replays_deterministically_without_the_script`
