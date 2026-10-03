# AI opponent

## Purpose

In a solo match the second seat is played by a deterministic planner inside the engine. It has no commander and issues the same commands a human client could, so it gains no rule a player does not have; a replay re-derives its decisions instead of recording them.

## State involved

- `GameState.ai_memories`: one `state.AiMemory(player_id, construction, orders)` per AI seat.
  - `AiConstructionMemory.last_war_base_id` — where it built last (round robin).
  - `AiOrderMemory.defences` — `AiDefenceAssignment(defender_id, intruder_id, structure_id, approached)`; `AiOrderMemory.sightings` — `AiSighting(robot_id, distance)` from the previous decision.
- Reads: its own resource pool and robots, every robot's position and build, structure ownership (effective world). Never the opponent's pool, orders or construction session.
- An AI seat has no entry in `GameState.commanders`.

## Algorithm

### Hook (`ai.seat.issue_ai_commands`, Step 0 of `engine.step`)

1. Runs only when the tick being simulated (`state.tick + 1`) is a multiple of `ai_decision_interval_ticks`, and only if the state has AI seats (a state with an AI seat must be stepped with a world).
2. For each AI seat in player order: `seed = derive_seed(match_seed, "ai", player, tick)`; `ai.planner.plan(state, memory, effective_world, rules, seed)` returns commands and new memory.
3. Commands are renumbered `max(existing sequence for that seat) + 1, …` (never below 0) and appended to the tick's batch; the memory is written back. They are then validated and applied exactly like a human's — a refused AI command has no effect and does not stop the planner.

`ai.planner.plan` runs the sub-planners in fixed order, construction then robot orders, each with its own `MatchRandom(derive_seed(seed, name))`. Neither currently draws a number.

### Construction (`ai.construction.plan`)

1. Cancel a session left open from the last decision (no resource effect).
2. Stop if the seat has `max_robots_per_player` robots or owns no war base.
3. `ai.construction.choose_design(pool, robot_count, nuclear_count, threatened, rules)` over `ai.construction.DESIGNS` (every legal build: 3 chassis × 14 weapon sets × electronics or not):
   - **threatened** (an enemy robot within `ai.construction.threat_radius_cells` — longest weapon range + electronics bonus, Chebyshev, of any cell of an owned war base): best affordable non-nuclear design, no floor, no reserve;
   - **wants a nuke** (`ai.construction.wants_nuclear`: at least `NUCLEAR_MIN_ARMY` robots and none carries a nuke): best affordable nuclear design leaving the reserve, or wait;
   - **otherwise**: best affordable non-nuclear design with at least `ai.construction.min_weapons` weapons (1 + robots // `WEAPON_RAMP_ROBOTS_PER_STEP`, capped at 3) that leaves `ai.construction.defence_reserve` general resources (the cheapest legal design's cost once the army has `RESERVE_MIN_ARMY` robots).
   - "Affordable" uses the engine's spend rule (`ai.construction.pool_after`); "best" is the highest `ai.construction.design_value` (weapons: cannon 2, missile 4, phaser 4, nuclear 0; chassis: bipod 0, tracks 2, anti-grav 3; electronics 2), then the lowest cost, then `DESIGNS` order.
4. Try owned war bases threatened-first, then round robin after the last one built at (`ai.construction.war_base_order`); skip a base whose exit `robot_launch.resolve_launch_exit` refuses for the design's height.
5. For the first usable base, issue `EnterConstructionRemotelyCommand`, one `SelectModuleCommand` per module, and `LaunchRobotCommand` — all applied in Step 8 of the same tick — and remember the base.

### Robot orders (`ai.robot_orders.plan`)

Destroyed robots that are still blinking ([combat.md](combat.md#destroyed-robots-destructiondestroy_robot-destructionadvance_destroyed_robots)) are left out on both sides: they are never ordered and never count as threats. They do still count toward the robot cap in construction, and `threatened_war_bases` ignores them too.

One pass, in priority order; each robot is committed at most once:

1. **Defence.** For each enemy robot, its distance to the nearest owned capture cell (war bases first) is recorded as a sighting. It is a threat when that distance is ≤ `THREAT_RADIUS_CELLS` and it is closing (nearer than last decision, newly seen, or within `HOLD_RADIUS_CELLS`). Standing assignments are kept while the intruder lives and stays within the threat radius. Each new threat gets one defender (`ai.robot_orders._pick_defender`): own armed robots within `RECALL_RADIUS_CELLS` that are not mid-capture, preferring one the engine would already chase the intruder with, then a non-losing `ai.robot_orders.matchup`, then distance, then id; a losing robot is sent only to save a war base. The defender gets Search & Destroy (robots) when the engine would chase the intruder, else an `Advance`/`Retreat` toward the site's column (issued once per assignment).
2. **Destroyers.** Each nuclear carrier is scored for Search & Destroy against war bases and factories, using the engine's own `select_destroy_target`; only an opponent-owned target counts (never neutral), no two carriers on one building, incumbents first.
3. **Captures.** Robots whose Search & Capture still has a target (or that are mid-capture) keep it and their claims count. The rest are allocated greedily: for each free robot and each capture type, the engine's own `select_capture_target` (excluding claims) is scored with `ai.robot_orders.capture_score` = value × `SCORE_SCALE` // ((distance + `DISTANCE_OFFSET_CELLS`) × (1 + contesters)), where value is `ai.robot_orders.structure_value` (war base: `war_base_production_amount` × `GENERAL_RESOURCE_WEIGHT` + `VICTORY_VALUE`; factory: `factory_production_amount`, × `CHASSIS_RESOURCE_WEIGHT` for chassis; doubled for an opponent's structure) and contesters are enemies within `CONTEST_RADIUS_CELLS` that beat the robot. The best pair is assigned and the loop repeats. Neutral war bases are valid targets.
4. **Idle.** An armed robot left over hunts enemy robots when its matchup against the one the engine would chase is not losing; otherwise a robot holding Search & Destroy, Advance or Retreat (or no order) is set to Stop & Defend.

`ai.robot_orders.matchup(own, enemy)`: +1/−1/0. A robot that cannot hurt the enemy loses; one the enemy cannot hurt wins; otherwise longer reach (`ai.robot_orders.weapon_reach`) wins; otherwise higher per-hit damage on flat ground (`ai.robot_orders.hit_damage`, which folds in both heights) wins.

A `SetRobotOrderCommand` is issued only when the desired order differs in kind from the current one (`ai.robot_orders.same_order`), so orders change on events, not every decision.

## Constants

Engine rule: `ai_decision_interval_ticks` = 4.

AI strategy (module constants, not rules, outside `rules_content_hash`):

| Constant | Value | Module |
|---|---:|---|
| `WEAPON_RAMP_ROBOTS_PER_STEP` | 4 | `ai/construction.py` |
| `RESERVE_MIN_ARMY` | 2 | `ai/construction.py` |
| `NUCLEAR_MIN_ARMY` | 6 | `ai/construction.py` |
| `THREAT_RADIUS_CELLS` | 24 | `ai/robot_orders.py` |
| `HOLD_RADIUS_CELLS` | 4 | `ai/robot_orders.py` |
| `RECALL_RADIUS_CELLS` | 80 | `ai/robot_orders.py` |
| `CONTEST_RADIUS_CELLS` | 16 | `ai/robot_orders.py` |
| `VICTORY_VALUE` | 50 | `ai/robot_orders.py` |
| `GENERAL_RESOURCE_WEIGHT` / `CHASSIS_RESOURCE_WEIGHT` | 2 / 2 | `ai/robot_orders.py` |
| `DISTANCE_OFFSET_CELLS` / `SCORE_SCALE` | 10 / 1000 | `ai/robot_orders.py` |

## Determinism notes

The planner is a pure function of state, memory, world, rules and seed. Every choice is a ranking with ties broken by entity id and canonical order. Memory round-trips through snapshots and replays, so a replay — which records only human commands — re-derives every AI command. The strength harness (`engine/tests/ai_harness.py`, `scripts/ai_strength.py`) runs AI-vs-AI and AI-vs-baseline matches with no backend and checks identical snapshot sequences across processes.

## Spectrum evidence

`docs/cr004/spectrum-ai-notes.md` describes the original enemy: `Lb7f4_update_enemy_ai` (one of 32 slots per cycle, idle a quarter of the time), `Lb81b_pick_random_warbase_loop` and `Lb505`/`Lb8c6` (random designs, weapon floor, half-pool cap), `Lb34d_find_capture_or_destroy_target` and `Lb36c_check_if_building_is_available_and_nearest_than_current_nearest` (nearest-by-x targets, exclusivity), `Lb3d5_prepare_robot_order_building_target_search` (no neutral war bases), `Lb920_enemy_ai_single_robot_control` (orders kept 31/32 of the time), `Lb95d` (nuclear role split).

## Deviations

All AI entries in [deviations-from-original.md](../../_specs/deviations-from-original.md#ai-opponent): reference not contract, no commander, whole-state decisions, affordable designs, value-ranked targets and threat response, neutral war bases targeted, immediate orders for new robots, composition response without armour.

## Tests that pin it

- `engine/tests/test_ai_seat.py::test_planner_runs_only_on_decision_ticks`
- `engine/tests/test_ai_seat.py::test_ai_commands_join_the_tick_batch_with_deterministic_sequence_numbers`
- `engine/tests/test_ai_seat.py::test_an_illegal_ai_command_is_rejected_like_a_humans_and_does_not_stall_the_planner`
- `engine/tests/test_ai_seat.py::test_ai_match_snapshot_sequence_is_byte_identical_across_runs`
- `engine/tests/test_ai_seat.py::test_replay_fixture_reproduces_an_ai_match`
- `engine/tests/test_ai_construction.py::test_choose_design_picks_the_best_affordable_robot`
- `engine/tests/test_ai_construction.py::test_choose_design_keeps_the_defence_reserve`
- `engine/tests/test_ai_construction.py::test_a_threat_releases_the_reserve_and_the_floor`
- `engine/tests/test_ai_construction.py::test_a_blinking_enemy_robot_is_no_threat`
- `engine/tests/test_ai_construction.py::test_a_blinking_robot_still_counts_toward_the_robot_cap`
- `engine/tests/test_ai_robot_orders.py::test_a_blinking_robot_gets_no_order`
- `engine/tests/test_ai_construction.py::test_planner_skips_a_war_base_whose_exit_is_blocked`
- `engine/tests/test_ai_construction.py::test_ai_builds_and_launches_a_legal_robot_within_one_decision`
- `engine/tests/test_ai_construction.py::test_a_human_seat_cannot_use_the_commanderless_entry`
- `engine/tests/test_ai_robot_orders.py::test_matchup_reach_decides_then_damage`
- `engine/tests/test_ai_robot_orders.py::test_two_neutral_factories_are_split_one_robot_each`
- `engine/tests/test_ai_robot_orders.py::test_a_nuclear_carrier_is_not_sent_to_blow_up_a_neutral_war_base`
- `engine/tests/test_ai_robot_orders.py::test_an_enemy_closing_on_the_war_base_draws_a_defender`
- `engine/tests/test_ai_robot_orders.py::test_a_robot_mid_capture_is_not_pulled_off_to_defend`
- `engine/tests/test_ai_robot_orders.py::test_the_planner_does_not_read_the_opponents_resources_or_orders`
- `engine/tests/test_ai_robot_orders.py::test_orders_are_not_reissued_when_nothing_changed`
- `engine/tests/test_ai_seat_commanderless_audit.py::test_the_ai_seat_never_has_a_commander_over_a_long_multi_tick_run`
- `engine/tests/test_ai_harness.py::test_snapshot_sequence_is_identical_across_processes_and_hash_seeds`
- `engine/tests/test_ai_harness.py::test_ai_out_expands_the_scripted_baseline_on_war_bases`
