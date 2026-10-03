# Construction

## Purpose

A player builds a robot on the construction screen of a war base they own: they fit one chassis, one to three weapons and optional electronics, pay from a temporary buffer, and START ROBOT launches the robot through the war base's doorway. The same session code serves the computer seat, which opens it remotely.

## State involved

- `GameState.construction_sessions`: at most one `construction_session.ConstructionSession` per player — `war_base_id`, `entry_tick`, `build` (`BuildInProgress`: chassis, weapons, electronics), `buffer` (the temporary pool) and `entry_snapshot` (the pool when the screen opened).
- `GameState.robots`, `GameState.robot_launches` (`RobotLaunchCount`: launches per player, never decreasing).
- The new `robot.Robot`: `entity_id`, `owner`, anchor `x`, `y`, `build` (`robot_build.RobotBuild`), `stack`, `height`, `order`, `facing`, `strength`, `exit_steps_remaining`.

## Algorithm

### Opening the screen

- **Human (Step 7 of `engine.step`)**: `heli_pad.detect_heli_pad_landing` fires for a free commander with no exit lift running whose body is over its own war base's pad at the pad's surface altitude ([world-and-map.md](world-and-map.md#heli-pad-heli_padpy)); `construction_session.enter_construction` opens a session there. It is re-detected every tick the commander sits on the pad, and refused (`ALREADY_IN_SESSION`) while one is open.
- **AI seat (Step 8)**: `EnterConstructionRemotelyCommand` → `enter_construction_remotely`, accepted only for a seat that has an `AiMemory` (`NOT_AI_SEAT` otherwise) and owns the war base in the effective world (`NOT_OWN_WAR_BASE`).

Both call `_open_session`: the buffer and the entry snapshot are copies of the player's pool at that tick.

### Editing (Step 8, commands in canonical order)

`select_module` checks, in order: an open session; the module is not already fitted (`DUPLICATE_MODULE`); no second electronics; no fourth weapon (`WEAPON_CAP_REACHED`). Then:

- selecting a chassis while one is fitted first refunds and removes the fitted chassis (refund capped by the entry snapshot);
- the new module is paid with `construction_economy.spend_module` against the buffer ([economy.md](economy.md)); if unaffordable the selection is rejected (`INSUFFICIENT_RESOURCES`) — and in the chassis-swap case the old chassis stays removed and refunded, so the robot has no chassis.

`deselect_module` refunds into the buffer with `refund_module`, using the entry snapshot as the cap, and removes the module. Neither function touches `GameState.resource_pools`.

### EXIT MENU

`CancelConstructionCommand` with a session open → `exit_construction`: the session is dropped (no resource change) and the player's commander, if any, gets `commander_exit_elevate_updates` automatic-lift updates ([commander.md](commander.md#construction-exit-lift)).

### START ROBOT (`robot_launch.launch_robot`)

Checks, in order, each rejection leaving the state unchanged and the screen open:

1. a session is open (`NO_ACTIVE_SESSION`);
2. the build is complete — one chassis and at least one weapon (`INCOMPLETE_BUILD`);
3. the player has fewer than `max_robots_per_player` robots alive (`ROBOT_CAP_REACHED`);
4. the war base declares an exit (`NO_EXIT_DEFINED`); the exit cell is the smallest cell of its first `EXIT` point — the war base's anchor on the original map;
5. the exit is free (`EXIT_BLOCKED`, `resolve_launch_exit`): the new robot's 2×2 body there is on the map, overlaps no structure, blocker or robot body, no cell is reserved by an in-flight move, and no undocked commander's body overlaps it within `[0, top)`, where `top` is the surface under the exit plus the new robot's stack height.

On success, in one state transition: a robot is created at the exit with id `robot-<owner>-<n>` (`n` = launches so far + 1), `stack` and `height` from `robot_stack.derive_stack_and_height`, order `StopAndDefend`, facing south, strength 100 and `exit_steps_remaining = robot_launch_exit_steps`; the player's pool is replaced by the session buffer; the launch counter increments; and `exit_construction` closes the screen and starts the lift. A `RobotLaunchedEvent` is emitted.

### Build validity and stack (`robot_build.py`, `robot_stack.py`)

`RobotBuild` enforces exactly one chassis (bipod, tracks, anti-grav), 1–3 distinct weapons (cannon, missile, phaser, nuclear — nuclear may be alone), at most one electronics. Weapons are normalized to `CANONICAL_WEAPON_ORDER`. `derive_stack` is chassis, weapons in that order, then electronics; `derive_height` sums `module_height_*` over the stack. Heights range from 13 (tracks + cannon) to 38 (bipod + missile + phaser + nuclear + electronics).

### Walk-out

A launched robot walks out of the doorway: on each of its robot updates it requests one step south through the normal move batch until `exit_steps_remaining` reaches 0. `autonomous_combat.settle_walk_outs` decrements the counter when the step starts, and zeroes it when a commander is docked on the robot, or when the robot was at an update but its step did not start (blocked, lost a contention, or the update fired). Any order assignment also zeroes it (`orders.apply_set_robot_order`). See [orders-and-capture.md](orders-and-capture.md) for the robot update.

## Constants

| Name | Value |
|---|---:|
| `max_robots_per_player` | 24 |
| `robot_launch_exit_steps` | 5 |
| `commander_exit_elevate_updates` | 5 |
| `module_height_bipod` | 11 |
| `module_height_tracks` | 7 |
| `module_height_anti_grav` | 8 |
| `module_height_cannon` | 6 |
| `module_height_missile` | 6 |
| `module_height_phaser` | 7 |
| `module_height_nuclear` | 7 |
| `module_height_electronics` | 7 |

Costs are listed in [economy.md](economy.md#constants).

## Determinism notes

Construction commands apply in Step 8 in `(player, sequence)` order, so a player's select/launch sequence in one tick is processed in the order the client numbered it. Robot ids come from a monotonic per-player counter, so a dead robot's id is never reissued and ids never depend on how many robots are alive.

## Spectrum evidence

- `Lc85d_robot_construction`, `Lca0f_waiting_for_key_press_loop` — the modal screen; `Lca48`/`Lcac1` — chassis swap; `Lcb8e_construction_screen_exit` — EXIT MENU; `Lcb52_construction_screen_start_robot` — START ROBOT.
- `Lc849_robot_construction_if_possible`, `La6c8` — launch checks and walk-out (5 steps down, Stop & Defend); `La6c8` tests only robot marks at the exit.
- `Ld7b4_piece_heights`, `Lb904_robot_height_loop` — piece heights.
- `MAX_ROBOTS_PER_PLAYER: equ 24`.

## Deviations

- [Construction does not pause the match](../../_specs/deviations-from-original.md#construction-does-not-pause-the-match).
- [Commander in the doorway blocks a launch](../../_specs/deviations-from-original.md#commander-in-the-doorway-blocks-a-launch).

## Tests that pin it

- `engine/tests/test_construction_session.py::test_enter_construction_snapshots_actual_pool_as_buffer_and_baseline`
- `engine/tests/test_construction_session.py::test_select_module_rejects_fourth_weapon`
- `engine/tests/test_construction_session.py::test_select_other_chassis_swaps_with_exact_resource_accounting`
- `engine/tests/test_construction_session.py::test_unaffordable_chassis_swap_is_rejected_with_old_chassis_removed_and_refunded`
- `engine/tests/test_engine_construction_integration.py::test_full_construction_flow_select_and_launch_through_step`
- `engine/tests/test_engine_construction_integration.py::test_exit_menu_with_build_in_progress_closes_screen_and_lifts_commander`
- `engine/tests/test_engine_construction_integration.py::test_rejected_start_robot_keeps_the_screen_open_and_commander_on_pad`
- `engine/tests/test_engine_construction_integration.py::test_commander_cannot_leave_the_pad_while_the_screen_is_open`
- `engine/tests/test_robot_build.py::test_nuke_may_be_the_only_weapon`
- `engine/tests/test_robot_build.py::test_weapons_are_normalized_to_canonical_stack_order_regardless_of_input_order`
- `engine/tests/test_robot_stack.py::test_spectrum_piece_heights_bound_robot_height_13_to_38`
- `engine/tests/test_robot_stack.py::test_tallest_robot_on_a_mountain_stays_within_the_commander_ceiling`
- `engine/tests/test_robot_launch.py::test_launch_rejected_with_24_existing_robots`
- `engine/tests/test_robot_launch.py::test_launch_rejects_an_exit_cell_reserved_by_an_in_flight_move`
- `engine/tests/test_robot_launch.py::test_launch_rejects_an_exit_overlapped_by_a_commander`
- `engine/tests/test_robot_launch.py::test_launch_never_reuses_a_robot_id_after_a_robot_dies`
- `engine/tests/test_launch_walk_out.py::test_it_walks_five_steps_south_one_per_update_then_stops`
- `engine/tests/test_launch_walk_out.py::test_a_blocked_step_ends_the_walk_out`
- `engine/tests/test_launch_walk_out.py::test_an_update_that_fires_ends_the_walk_out`
- `engine/tests/test_ai_construction.py::test_a_human_seat_cannot_use_the_commanderless_entry`
