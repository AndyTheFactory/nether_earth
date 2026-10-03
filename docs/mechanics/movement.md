# Robot movement

## Purpose

Every robot move — from a standing order, a launch walk-out, the computer's orders or a player's direct control — goes through one validator and one batched start, so terrain, occupancy, commander blocking, reservations and same-tick contention are decided the same way for all of them.

## State involved

- `robot.Robot.movement`: `RobotMoveTransition(from_x, from_y, to_x, to_y, started_tick, duration_ticks)` while a move is in flight; the anchor `x`, `y` stays at the origin until it completes.
- `robot.Robot.turning`: `RobotTurnTransition(from_facing, to_facing, started_tick, duration_ticks)`; `robot.Robot.facing`: `RobotFacing` (`EAST`, `WEST`, `SOUTH`, `NORTH`). A robot holds at most one of `movement` / `turning`.
- `movement.RobotMoveRequest(entity_id, dx, dy)`: exactly one cardinal step.
- Interaction points (`interactions.InteractionKind`): `EXIT` gives a war base's launch cell; `HELI_PAD`, `WARBASE_CAPTURE`, `FACTORY_CAPTURE` are used by construction and capture.
- Derived, not stored: occupancy (`movement.folded_robot_occupancy`: static components plus every robot's current body) and reservations (`reservations.reservations_from_state`: every in-flight move's destination body).

## Algorithm

### Terrain and timing (`movement.py`)

- `CHASSIS_TERRAIN_PERMISSIONS`: bipod `NORMAL`, `ROUGH`; tracks also `MOUNTAIN`; anti-grav also `DITCH`. `unit_terrain_enterable` requires the chassis may enter all four destination body cells. Electronics never appears in this table.
- `unit_move_terrain` is the highest-ranked class under the destination body: `MOUNTAIN` > `ROUGH` > `DITCH` > `NORMAL`.
- `move_duration_ticks(chassis, terrain, rules)` returns `robot_move_ticks_<chassis>_<terrain>`.

### Validation (`movement.validate_robot_move`)

Checks in this fixed order, each with a stable reason:

1. the robot exists (`NO_SUCH_ROBOT`);
2. no move in flight (`MOVE_IN_PROGRESS`) and no turn in flight (`TURN_IN_PROGRESS`);
3. the destination body is on the map (`OUT_OF_BOUNDS`);
4. the chassis may enter every destination cell (`TERRAIN_IMPASSABLE`);
5. no structure, blocker or other robot body occupies a destination cell (`OCCUPIED`); the robot never blocks its own next body;
6. no commander blocks it (`COMMANDER_BLOCKED`): `commander_blocks_robot_cell` — a commander whose body overlaps and whose vertical range `[altitude, altitude + commander_height)` overlaps `[0, top)` of the moving robot (top at its current anchor). A commander docked on this robot never blocks it;
7. the destination availability hook — in play `reservations.destination_available`: no destination cell is reserved by another robot (`DESTINATION_UNAVAILABLE`).

Validation is run against the physical world (`destruction.scenery_world`), where debris is rough terrain.

### Starting one move (`movement.apply_robot_move`)

After validation: if the requested direction is not the robot's facing, a turn starts instead — `RobotTurnTransition` of `robot_turn_ticks` toward `facing.rotate_toward(wanted)` (one 90-degree step; a reversal goes through a perpendicular and costs two turns) and a `RobotTurnStartedEvent`. Otherwise a `RobotMoveTransition` starts with `duration_ticks = robot_move_duration_ticks(robot, unit_move_terrain(destination))` and a `RobotMoveStartedEvent`. The caller re-requests the step later; once the facing matches, the move starts.

### The tick's move batch (`reservations.apply_robot_move_batch`, Step 2c)

1. **Validate** every request against the same entry state, in entity-id order. A robot with a second request in the batch gets `MOVE_IN_PROGRESS`.
2. **Group** accepted claims in `(destination anchor, entity id)` order: the first open claim and every open claim whose destination body overlaps it form a group. A group of one wins. Otherwise the winner is `MatchRandom(derive_seed(match_seed, tick)).choice(contenders)` over the group in entity-id order — a 50/50 draw for two, uniform for more; every member whose destination overlaps the winner's loses (`DESTINATION_UNAVAILABLE`, no state change); members clear of the winner stay open for a later group. A `DestinationContentionResolvedEvent` records each contested group.
3. **Start** the winners through `apply_robot_move`, in batch order, re-validated against the evolving state.

The batch holds: launch walk-out steps and gated order steps from Step 2b2, and direct-control steps.

### Completion (`movement.advance_all_robot_transitions`, Step 2b)

At `started_tick + duration_ticks` the robot's anchor becomes the destination (releasing the reservation, which was only ever derived from the transition) and a `RobotMoveCompletedEvent` is emitted; a due turn sets `facing` and emits `RobotTurnCompletedEvent`. Completions run before the tick's new moves, so a robot can start its next step on the completion tick, and another robot can claim the cells the first one just left.

`cancel_robot_move` abandons a move and leaves the robot at its origin; nothing in normal play calls it today.

### Direct control (`direct_control.py`)

`DirectRobotMoveCommand(player, sequence, dx, dy)` names no robot: `validate_direct_robot_move` resolves the robot the player's commander is docked on and rejects `NO_COMMANDER` or `NOT_DOCKED`. `direct_robot_move_request` turns an accepted command into a `RobotMoveRequest` for the batch, so direct control has no legality, timing or contention rules of its own. Direct moves are not gated on the robot update; the in-flight checks still allow only one move or turn at a time.

## Constants

| Name | Value |
|---|---:|
| `robot_move_ticks_bipod_normal` / `robot_move_ticks_bipod_rough` | 24 / 32 |
| `robot_move_ticks_tracks_normal` / `robot_move_ticks_tracks_rough` / `robot_move_ticks_tracks_mountain` | 16 / 24 / 28 |
| `robot_move_ticks_anti_grav_normal` / `robot_move_ticks_anti_grav_rough` / `robot_move_ticks_anti_grav_mountain` / `robot_move_ticks_anti_grav_ditch` | 12 / 12 / 16 / 12 |
| `robot_turn_ticks` | 4 |
| `commander_height` | 4 |

## Determinism notes

Requests are evaluated in entity-id order, contention groups in destination order, and each contention draw comes from a fresh per-tick stream, so submission order never decides an outcome. Occupancy folds are memoized per `(world, state)` identity.

## Spectrum evidence

- `Lb61d_robot_movement_speed_table` (cycles per move: flat 6/4/3, rugged 8/6/3, mountains 9/7/4) and `Lb5f3_determine_speed_based_on_terrain`; one cycle = 4 ticks.
- `Lb513_get_robot_movement_possibilities` / `Lb5cd_robot_map_collision_internal` — chassis limits 8/12/15 on the element type; the two newly entered cells and three anchors per direction; a ship lower than the robot's top blocks it.
- `Lb471_move_robot_one_step_in_desired_direction` — rotate 90 degrees and return when not facing the desired direction; reversal through a perpendicular.
- `Lb495` — the robot's altitude is re-read after each advance.

## Deviations

None for the movement rules. The contention draw has no Spectrum counterpart (single-player, sequential robot update); see [resolved-questions.md](../../_specs/resolved-questions.md#simultaneous-destination-cell-claims).

## Tests that pin it

- `engine/tests/test_movement.py::test_chassis_terrain_permissions_match_locked_rules_exactly`
- `engine/tests/test_movement.py::test_move_duration_matches_locked_tick_table`
- `engine/tests/test_movement.py::test_rough_terrain_costs_bipod_and_tracks_the_same_eight_ticks`
- `engine/tests/test_movement.py::test_electronics_does_not_change_terrain_permissions`
- `engine/tests/test_movement.py::test_commander_overlapping_the_destination_blocks_the_move`
- `engine/tests/test_movement.py::test_transition_completes_exactly_at_the_configured_duration_boundary`
- `engine/tests/test_movement.py::test_a_perpendicular_step_turns_instead_of_moving`
- `engine/tests/test_movement.py::test_a_reversal_costs_two_turns_via_a_perpendicular_direction`
- `engine/tests/test_unit_footprint_2x2.py::test_the_move_duration_reads_the_slowest_terrain_under_the_body`
- `engine/tests/test_unit_footprint_2x2.py::test_a_robot_never_blocks_its_own_next_body`
- `engine/tests/test_unit_footprint_2x2.py::test_overlapping_same_tick_claims_to_different_anchors_contend`
- `engine/tests/test_reservations.py::test_two_way_contention_is_a_seeded_coin_flip_not_priority_by_id`
- `engine/tests/test_reservations.py::test_n_way_contention_is_a_uniform_seeded_choice`
- `engine/tests/test_reservations.py::test_submission_order_does_not_decide_the_winner`
- `engine/tests/test_reservations.py::test_a_group_member_clear_of_the_winner_still_moves`
- `engine/tests/test_reservations.py::test_no_two_robots_ever_hold_the_same_reservation_across_a_match`
- `engine/tests/test_direct_control.py::test_rejects_when_commander_is_free`
- `engine/tests/test_engine_direct_control_integration.py::test_direct_move_participates_in_same_tick_contention`
- `engine/tests/test_engine_direct_control_integration.py::test_direct_move_cannot_bypass_commander_blocking`
