# Orders and capture

## Purpose

Robots act on standing orders without a player at the controls: hold and defend, advance or retreat a distance, capture structures one after another, or hunt enemy robots or structures. This page covers how an order is evaluated each tick, how targets are chosen, when a robot acts (its robot update), how an engagement becomes a shot, and how capture progress accumulates.

## State involved

- `robot.Robot.order`: one of `orders.StopAndDefend`, `Advance(distance_miles, target_x)`, `Retreat(distance_miles, target_x)`, `SearchCapture(target, structure_id)`, `SearchDestroy(target)`. `target_x` and `structure_id` are engine-bound (cleared on assignment).
- `robot.Robot.last_fire_tick`, `exit_steps_remaining`, `facing`, `turning`, `hunt_route`.
- `GameState.capture_progress`: `capture.CaptureProgress(structure_id, capturing_player, robot_id, elapsed_ticks)`; `GameState.structure_ownership`: `StructureOwnership(structure_id, owner)` overrides.
- Per tick, transient: `orders.OrderEvaluation(robot_id, order, previous, status, request, intent, hunt_route)` and `orders.EngagementIntent(robot_id, player, target_kind, target_id, target_x, target_y, distance_cells, weapons)`.

## Algorithm

### Assignment (Step 2b2)

`SetRobotOrderCommand` (any robot the player owns; the client sends it for the docked robot, the AI for its own robots) → `orders.apply_set_robot_order`. A structurally invalid order (`order_is_valid`: e.g. distance outside 0–`MAX_ORDER_DISTANCE_MILES`) is stored as `StopAndDefend` with status `FALLBACK`. Engine-bound fields are cleared; `Advance`/`Retreat` start `PENDING`. Any assignment zeroes `exit_steps_remaining` (ends a walk-out).

### Evaluation (`orders.evaluate_orders`, Step 2b2)

Every robot that has an order and no commander docked on it is evaluated in entity-id order against the same entry state (Search & Capture claims made earlier in the same pass are visible to later robots). `evaluate_order` per type:

- **`StopAndDefend`** — never completes. Request: the next walk-out step south if `exit_steps_remaining > 0` and it is legal now (`orders.walk_out_request`). Intent: the nearest hostile robot by Manhattan distance between anchors (ties by id), with every fitted weapon listed.
- **`Advance` / `Retreat`** — on first evaluation bind `target_x = x ± miles_to_cells(distance)`, clamped to `0..width − 2`. If clamping leaves the robot where it is and the distance was non-zero → `FALLBACK`. At `x == target_x` → `COMPLETED` (becomes `StopAndDefend`). Otherwise navigate toward `(target_x, robot.y)` — the row is the robot's *current* row, so detours do not pull it back. Electronic `UNREACHABLE` → `FALLBACK`. No intent while moving.
- **`SearchCapture`** (`_evaluate_capture`) — never completes or falls back:
  1. if the stored target still matches and the robot's anchor is on its capture cell: hold (`_face_out_or_hold`);
  2. otherwise `select_capture_target`: among structures of the target kind that match now and are not claimed by another same-owner robot with the same target type (`claimed_structures`), the one whose capture cell is nearest (ties by structure id). Matching: `NEUTRAL_FACTORY` — owner is none; `ENEMY_FACTORY` — owned by someone else; `ENEMY_WAR_BASE` — any war base not the robot's owner's, neutral included;
  3. nothing selected: keep walking to the stored target if it is still valid, else hold;
  4. on the goal cell: hold; else navigate (an electronic `UNREACHABLE` also holds).

  Holding emits the Stop & Defend intent. On the capture cell, `_face_out_or_hold` first requests a step in `capture.outward_facing(structure, cell)` while the facing is wrong — the dominant axis from the structure's component centroid to the cell (x on a tie), computed in integers — which the movement layer turns into a rotation, never a move off the cell.
- **`SearchDestroy`** — needs a weapon capable against the target kind (`_capable_weapons`: any weapon against robots, nuclear only against structures), else `FALLBACK`.
  - Robots: `select_destroy_target` picks the nearest hostile robot (ties by id); none → `FALLBACK`. Navigation via `navigation.next_hunt_step` ([navigation.md](navigation.md#search--destroy-hunts-navigationnext_hunt_step)); the order stays `ACTIVE` with an intent against the target every tick.
  - Factories / war bases: the nearest structure of that kind not owned by the robot's owner (neutral included); goal = its capture cell. On the cell → `COMPLETED` with a structure intent (the detonation, below). Otherwise navigate, no intent; electronic `UNREACHABLE` → `FALLBACK`.

`apply_order_evaluations` writes back changed orders and hunt caches and emits `RobotOrderChangedEvent`s, then `RobotEngagementIntentEvent`s.

### The robot update (`autonomous_combat.py`)

A robot is *at an update* (`autonomous_update_due`) when it has no move in flight and either it has never fired or `tick ≥ last_fire_tick + autonomous_update_period_ticks(robot)`, the move duration for the terrain under its own body. A move's completion tick is also an update, since the transition is cleared before orders run.

`gate_order_requests` keeps an evaluation's move request only if the robot is at an update and its intent would *not* fire this tick (a dry run of the same fire path). So an order-driven robot either fires or moves on each update, never both, and does nothing in between.

### From intent to shot (`consume_engagement_intents`, Step 2c2 phase c)

For each intent, in robot order, against the state as combat has left it so far:

1. the source robot still exists and is at an update;
2. `_select_fire_request`: the target still exists (structures: still in the effective world). Robot target: the first weapon in canonical order (cannon, missile, phaser), never nuclear, whose range plus `electronics_range_bonus_cells` (if electronics is fitted) is ≥ `distance_cells`. Structure target: nuclear, only at `distance_cells == 0`;
3. normal weapon, robot not facing the target (`_facing_toward`: dominant axis, x on a tie): start a 90-degree `RobotTurnTransition` instead (nothing if already turning);
4. nuclear: `combat.validate_fire`, then `destruction.execute_nuclear_detonation`;
5. otherwise `combat.apply_fire(..., autonomous=True)` — the same path as direct fire, which rejects a busy channel, a robot mid-turn, or a second shot in the fire cycle ([combat.md](combat.md)).

The range test is Manhattan distance to the target's anchor; the shot itself travels along the facing and hits only what its 2×2 body meets.

### Walk-out settlement

After the move batch, `settle_walk_outs` decrements `exit_steps_remaining` for a robot whose walk-out step started, and zeroes it when a commander is docked on the robot or when the robot was at an update but did not start the step.

### Capture (`capture.advance_capture`, Step 2d)

For every factory and war base in id order, with a capture point in the effective world:

1. qualifying robots: anchor on a capture cell and owner ≠ the structure's current owner (any robot for a neutral structure); the lowest entity id qualifies;
2. none → drop any progress;
3. same player *and* same robot as the stored progress → `elapsed_ticks + 1`, else start at 1;
4. `elapsed_ticks ≥ capture_duration_ticks` → write a `StructureOwnership` override, drop the progress, emit `StructureCapturedEvent`. A war base changing hands triggers `victory.evaluate_victory` in the same step.

Capture reads authoritative anchors, so a robot moving onto the cell starts counting on the tick its move completes, and one moving off stops counting on the tick its move completes. Destroying a structure or the capturing robot drops its progress.

## Constants

| Name | Value |
|---|---:|
| `capture_duration_ticks` | 1440 |
| `MAX_ORDER_DISTANCE_MILES` | 50 (`orders.py`) |
| `CELLS_PER_MILE` | 2 |
| `cannon_range_cells` / `missile_range_cells` / `phaser_range_cells` | 10 / 14 / 10 |
| `electronics_range_bonus_cells` | 2 |
| `robot_turn_ticks` | 4 |
| `robot_launch_exit_steps` | 5 |
| update period | `robot_move_ticks_*` for the terrain under the robot |

## Determinism notes

Evaluations are pure reads of one entry state, in entity-id order; the only cross-robot effect inside the pass is Search & Capture claims, applied in that same order. Targets tie-break by distance then id; facing tie-breaks toward x. No randomness.

## Spectrum evidence

- `Lb154_robot_ai_update` — act only when the per-robot cycle counter reaches 0; fire (then desired direction 0) or move; `Lb20d_move_robot` / `Lb5f3_determine_speed_based_on_terrain` reload the counter.
- `Lb626_check_directions_with_enemy_robots` — the original's fire-decision scan (8/10/12 cells along three lanes); not adopted, see [open-questions.md](../../_specs/open-questions.md).
- `Lb289_choose_direction_orders_with_building_targets`, `Lb34d_find_capture_or_destroy_target`, `Lb36c_check_if_building_is_available_and_nearest_than_current_nearest`, `Lb3d5_prepare_robot_order_building_target_search` — capture targets and exclusivity.
- `Lb2e8_target_directions_calculated` → `Lb99f_fire_nuclear_bomb` — detonate on arrival at the target building; `Labc8_capture_or_destroy_order_selected` — no nuke, no destroy-structure order.
- `Ladb7_building_loop` — capture counts while a robot mark (anchor) is on the building's cell.
- `La6c8`, `Lb1e9_no_enemy_robots_in_sight` — launch walk-out.

## Deviations

- [Neutral structures take the full capture time](../../_specs/deviations-from-original.md#neutral-structures-take-the-full-capture-time).
- [Search & Capture re-selects its target every evaluation](../../_specs/deviations-from-original.md#search--capture-re-selects-its-target-every-evaluation).
- [Search & Capture war-base target includes neutral war bases](../../_specs/deviations-from-original.md#search--capture-war-base-target-includes-neutral-war-bases).
- [A robot on a capture cell faces out of the structure](../../_specs/deviations-from-original.md#a-robot-on-a-capture-cell-faces-out-of-the-structure).
- [Robot update phase while standing still](../../_specs/deviations-from-original.md#robot-update-phase-while-standing-still).
- [Stop & Defend does not turn toward enemies](../../_specs/deviations-from-original.md#stop--defend-does-not-turn-toward-enemies).

## Tests that pin it

- `engine/tests/test_orders.py::test_invalid_order_falls_back_to_stop_and_defend`
- `engine/tests/test_orders.py::test_advance_from_the_eastern_edge_is_impossible_and_falls_back`
- `engine/tests/test_orders.py::test_advance_beyond_the_map_clamps_to_the_edge_rather_than_failing`
- `engine/tests/test_orders.py::test_defensive_target_selection_breaks_distance_ties_by_entity_id`
- `engine/tests/test_orders.py::test_search_capture_war_base_takes_a_neutral_war_base`
- `engine/tests/test_orders.py::test_search_capture_switches_to_a_nearer_target_that_just_changed_hands`
- `engine/tests/test_orders.py::test_search_capture_does_not_abandon_a_capture_already_under_way`
- `engine/tests/test_orders.py::test_search_capture_skips_a_target_another_robot_with_the_same_order_holds`
- `engine/tests/test_orders.py::test_a_robot_on_the_capture_cell_turns_to_face_away_from_the_structure`
- `engine/tests/test_orders.py::test_search_destroy_structure_targets_neutral_and_enemy_but_not_own`
- `engine/tests/test_engine_orders_integration.py::test_one_robot_captures_two_neutral_factories_in_sequence`
- `engine/tests/test_engine_orders_integration.py::test_two_robots_with_the_same_capture_order_split_two_factories`
- `engine/tests/test_engine_orders_integration.py::test_search_destroy_of_a_structure_detonates_exactly_on_arrival`
- `engine/tests/test_engine_orders_integration.py::test_stop_and_defend_nuclear_carrier_never_detonates`
- `engine/tests/test_engine_orders_integration.py::test_a_directly_driven_robot_ignores_its_own_standing_order`
- `engine/tests/test_autonomous_fire_update.py::test_a_robot_with_a_shot_on_its_update_fires_instead_of_moving`
- `engine/tests/test_autonomous_fire_update.py::test_fire_period_reads_the_highest_piece_under_the_2x2_body`
- `engine/tests/test_autonomous_fire_update.py::test_direct_fire_is_not_tied_to_the_robot_update`
- `engine/tests/test_combat_autonomous.py::test_robot_intent_never_selects_nuclear_even_adjacent`
- `engine/tests/test_combat_autonomous.py::test_electronics_range_bonus_extends_eligibility`
- `engine/tests/test_combat_autonomous.py::test_an_autonomous_robot_turns_toward_its_target_instead_of_firing`
- `engine/tests/test_capture.py::test_neutral_factory_counts_down_like_an_enemy_one_instead_of_instant_acquisition`
- `engine/tests/test_capture.py::test_enemy_capture_interruption_when_different_robot_takes_over`
- `engine/tests/test_capture.py::test_enemy_capture_completes_exactly_at_configured_duration_boundary`
- `engine/tests/test_capture.py::test_outward_facing_breaks_an_exact_tie_toward_x`
- `engine/tests/test_engine_capture_integration.py::test_war_base_capture_triggers_victory_in_same_step`
- `engine/tests/test_unit_footprint_2x2.py::test_a_body_covering_the_capture_cell_with_its_anchor_elsewhere_does_not`
