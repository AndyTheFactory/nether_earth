# Commander

## Purpose

Each human player flies one indestructible anti-grav commander. It moves cell by cell, rises and falls on a fixed cadence, collides with everything solid by height, docks onto friendly robots to drive them, and lands on its war-base heli-pad to build. The AI seat has no commander.

## State involved

`commander.Commander` (one per human seat in `GameState.commanders`, sorted by player id):

| Field | Meaning |
|---|---|
| `mode` | `CommanderMode.FREE` or `CommanderMode.DOCKED` |
| `x`, `y` | anchor of the 2×2 body; changes only when a horizontal move completes |
| `altitude` | integer, `commander_min_altitude`..`commander_max_altitude` |
| `docked_robot_id` | set exactly when `DOCKED` |
| `rising` | held rise intent (the rise key) |
| `horizontal_transition` | `GridTransition(from, to, started_tick, duration_ticks)` while a move is in flight |
| `vertical_transition` | the last vertical step, for client interpolation |
| `elevate_updates_remaining` | automatic-lift updates left (0 when idle) |

The commander occupies the vertical range `[altitude, altitude + commander_height)` (`collision.commander_vertical_range`).

## Algorithm

All of this runs inside `engine.step` ([timing-and-determinism.md](timing-and-determinism.md)); the step numbers are the ones in the code.

### Horizontal movement (`commander_movement.py`)

1. **Step 2** — `advance_all_horizontal_transitions`: a transition with `started_tick + duration_ticks ≤ tick` completes; the anchor becomes the destination and the transition is cleared.
2. **Step 1** — `apply_commander_move` for each `CommanderMoveCommand` (a one-cell cardinal step), skipped while the commander is docked (`docking.docked_movement_allowed`) or its player has an open construction session. `validate_commander_move` rejects `NO_COMMANDER`, `NOT_FREE`, `MOVE_IN_PROGRESS`, or `BLOCKED` when `collision.commander_horizontal_move_allowed` refuses the destination. An accepted move starts a transition of `commander_horizontal_move_ticks`.

Because completions run before commands, a command that arrives on the completion tick starts at once: a held key moves one cell every 4 ticks (cells start on ticks 1, 5, 9, …).

### Vertical movement

1. `CommanderSetVerticalIntentCommand` sets `rising` (Step 1, in either mode).
2. **Step 4**, on vertical cadence ticks (`is_vertical_update_tick`: positive multiples of `commander_vertical_update_ticks`), for every free commander whose player has no open construction session, `apply_vertical_physics`:
   - if `elevate_updates_remaining > 0`: consume one and try `altitude + commander_ascent_step` (capped at the maximum), whatever `rising` says; the counter is consumed even when the ascent is clamped or blocked;
   - else if `rising`: try `altitude + commander_ascent_step`, clamped;
   - else gravity: target `altitude − commander_descent_step`, clamped, then `_gravity_landing_altitude` walks down one unit at a time and stops at the last altitude `commander_vertical_move_allowed` accepts. A step of 2 therefore never skips past, nor stops one unit above, a surface at an odd height.
   - The move is applied only if the final candidate differs from the current altitude and is allowed.

### Collision (`collision.py`)

`commander_horizontal_move_allowed(state, commander, dest_x, dest_y, world=…, robots=…)` and `commander_vertical_move_allowed` test the commander's vertical range — current altitude for a horizontal move, the target altitude for a vertical one — against:

- the static surface under the 2×2 body: one range `[0, unit_surface_height)` covering structures, scenery and terrain pieces;
- every robot whose body overlaps: `[0, top)`, with `top = altitude under its body + stack height` (`RobotFixture.top`, built by `engine._robot_fixtures` from the physical world);
- every other commander whose body overlaps: its own vertical range.

Ranges are half-open, so resting exactly on a surface is not a collision. The destination body must also be on the map. The checks run against `destruction.scenery_world`: debris is 3-high rough ground, not a wall.

### Docking (`docking.py`)

1. **Step 3** — `apply_undock`: a docked commander holding `rising` becomes free, `docked_robot_id` is cleared and `elevate_updates_remaining = commander_exit_elevate_updates`. It does not move this tick; the lift runs on the next cadence ticks.
2. **Step 5** — `attempt_auto_dock` (via `auto_dock_with_event`): a free commander with no lift running docks onto the first robot fixture at its exact anchor whose `top == altitude`, if that robot is friendly. An enemy robot there is a surface only. Docking clears the robot's cached hunt route and ends a launch walk-out ([construction.md](construction.md)).
3. **Step 6** — `follow_docked_robot` moves a docked commander to its robot's anchor and top every tick.

While docked, `orders.evaluate_orders` skips the robot, so its standing order is suspended; `DirectRobotMoveCommand` drives it ([movement.md](movement.md)). If the robot is destroyed, `destruction.destroy_robot` frees the commander at the robot's anchor and last top.

### Construction exit lift

Leaving the construction screen (`construction_session.exit_construction`, used by EXIT MENU and by a successful launch) sets the same `elevate_updates_remaining`. While it is above 0, Step 7 skips heli-pad detection and Step 5 skips docking, so the commander lifts 10 units clear before either can trigger again.

### Modality

While a player has an open construction session, its `CommanderMoveCommand`s are no-ops and its vertical physics is skipped (rise intent is still recorded).

## Constants

| Name | Value |
|---|---:|
| `commander_min_altitude` | 0 |
| `commander_max_altitude` | 48 |
| `commander_vertical_update_ticks` | 4 |
| `commander_ascent_step` | 2 |
| `commander_descent_step` | 2 |
| `commander_height` | 4 |
| `commander_horizontal_move_ticks` | 4 |
| `commander_exit_elevate_updates` | 5 |

Full ascent 0 → 48 and full descent 48 → 0 each take 24 updates (4.8 s). The tallest robot on the highest walkable ground has its top at 44, below the ceiling.

## Determinism notes

Commanders are processed in player-id order in every step. Collision uses only integer ranges. The robot fixtures are rebuilt from the state twice per tick — before commander physics (for collision) and after it (for docking and following) — so a robot that moved this tick is seen where it now stands.

## Spectrum evidence

- `Laf11_player_ship_keyboard_control`, `Lafb5_elevate` (+2), `Lafc3_gravity` (−1 in the original).
- `Lfd30_player_elevate_timer` = 5, set by `Lcb8e_construction_screen_exit` and the robot HUD EXIT option (`#a7fd`–`#a80f`, `La812_exit_robot`); `Lafa2_player_ship_keyboard_control_altitude` climbs +2 while it runs.
- `Lb052_check_player_collision` — highest map piece under the 2×2 ship and robot tops in the 3×3 anchor window.
- `La69a` / `La720_land_on_robot` — dock only on the ship's own anchor at `altitude == robot height + robot altitude`.
- `Lb495` — the ship rides at the robot's top under direct control.

## Deviations

- [Descent speed](../../_specs/deviations-from-original.md#descent-speed) (−2, not −1).
- [Exit lift not shortened by movement](../../_specs/deviations-from-original.md#exit-lift-not-shortened-by-movement).
- [Construction does not pause the match](../../_specs/deviations-from-original.md#construction-does-not-pause-the-match) (only this commander is frozen).

## Tests that pin it

- `engine/tests/test_engine_commander_integration.py::test_held_move_chains_cells_without_an_idle_tick`
- `engine/tests/test_engine_commander_integration.py::test_second_move_command_while_in_progress_is_gameplay_rejected`
- `engine/tests/test_engine_commander_integration.py::test_docked_commander_move_command_is_a_gameplay_noop`
- `engine/tests/test_engine_commander_integration.py::test_undock_lift_profile_matches_construction_exit_and_lands_back_on_robot`
- `engine/tests/test_engine_commander_integration.py::test_rise_intent_during_the_undock_lift_does_not_change_it`
- `engine/tests/test_commander_descent_step.py::test_descent_from_max_altitude_reaches_ground_in_24_updates`
- `engine/tests/test_commander_descent_step.py::test_descent_lands_exactly_on_an_odd_height_box`
- `engine/tests/test_commander_descent_step.py::test_descent_docks_on_an_odd_height_friendly_robot_top`
- `engine/tests/test_commander_movement.py::test_elevate_counter_ascends_regardless_of_rise_intent_and_counts_down`
- `engine/tests/test_commander_movement.py::test_elevate_counter_is_consumed_even_when_the_ascent_is_blocked`
- `engine/tests/test_collision.py::test_touching_ranges_do_not_overlap`
- `engine/tests/test_collision.py::test_low_commander_blocked_by_tall_static_component`
- `engine/tests/test_collision.py::test_descent_stops_on_static_surface`
- `engine/tests/test_docking.py::test_friendly_dock_occurs_exactly_at_resting_altitude`
- `engine/tests/test_docking.py::test_enemy_robot_contact_never_docks`
- `engine/tests/test_docking.py::test_undock_transitions_to_free_and_starts_the_exit_lift`
- `engine/tests/test_docking.py::test_undocked_commander_does_not_redock_while_the_lift_runs`
- `engine/tests/test_unit_footprint_2x2.py::test_docking_needs_the_commander_anchored_on_the_robot_anchor`
- `engine/tests/test_terrain_heights.py::test_gravity_rests_the_commander_at_3_on_rough`
- `engine/tests/test_robot_terrain_height.py::test_commander_docks_on_a_raised_friendly_robot_at_its_top`
- `engine/tests/test_robot_terrain_height.py::test_commander_is_ejected_at_the_raised_top_when_its_robot_is_destroyed`
- `engine/tests/test_ai_seat_commanderless_audit.py::test_collision_other_commanders_and_horizontal_move_tolerate_a_missing_opponent`
