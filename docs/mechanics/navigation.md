# Navigation

## Purpose

Decide which single step a robot should try next toward a goal. Robots without electronics use original-style local routing that may wander and stall; robots with electronics plan a shortest-time route. Navigation only proposes; `movement.py` decides whether the step is legal and `reservations.py` starts it.

## State involved

- Reads the robot (`x`, `y`, `facing`, `movement`, `turning`, `build.electronics`), the state (other robots, reservations, commanders) and the physical world.
- `robot.Robot.hunt_route`: `RobotHuntRoute(target_id, planned_tick, origin_x, origin_y, steps)` — the one piece of navigation state stored, only for Search & Destroy (robots) hunters with electronics. `steps` is one letter per step (`E`, `W`, `S`, `N`), `""` for "already on a goal", `None` for "no route".
- Output: `navigation.NavigationDecision` with a status (`STEP`, `ARRIVED`, `BLOCKED`, `UNREACHABLE`, `MOVE_IN_PROGRESS`, `TURN_IN_PROGRESS`) and, for `STEP`, a `RobotMoveRequest` the executor has already accepted against the same state.

## Algorithm

`navigation.navigation_policy_for(robot)` returns `ELECTRONIC_NAVIGATION` when the build has electronics, else `NON_ELECTRONIC_NAVIGATION`. Both policies first answer `MOVE_IN_PROGRESS` / `TURN_IN_PROGRESS` while a move or turn is in flight, and `ARRIVED` when the robot's anchor is the goal.

### Traversability (shared)

`navigation.cell_is_enterable(robot, x, y, …)` is the planner's view of "could this robot stand anchored here now": the body is on the map, the chassis may enter all four cells, no structure, no other robot's body or reserved destination overlaps it, and no commander blocks it (`movement.commander_blocks_robot_cell`). It mirrors `movement.validate_robot_move`'s cell-level checks by calling the same functions; every proposed step is still passed through `validate_robot_move` before it is returned (`_step_is_legal`).

### Without electronics (`NonElectronicNavigation.next_step`)

Candidate steps, first legal one wins:

1. **primary** — one step along the axis with the larger remaining delta (x on a tie);
2. **momentum** — the robot's current `facing`, when that is not the primary;
3. **the two perpendicular steps**, in an order shuffled by `MatchRandom(derive_wander_seed(match_seed, tick, robot_id, dumb_wander_commit_ticks))` — the seed uses `tick // dumb_wander_commit_ticks`, so the order holds for a 16-tick window and differs per robot;
4. **anything else** in `CARDINAL_DIRECTIONS` order (west, north, south, east), the reverse of the primary included.

No step legal → `BLOCKED`. The policy never searches and never reports `UNREACHABLE`, so an order that uses it never falls back for lack of a route. Momentum is what lets it slide along a wall instead of pacing two cells forever; a deep pocket or a diagonal staircase can still hold it indefinitely, by design.

Turning: a candidate in a direction the robot does not face is still a legal *step*; when the batch applies it, the robot turns first ([movement.md](movement.md)). Momentum costs no turn.

### With electronics (`ElectronicNavigation.next_step`)

`plan_route` → `plan_route_to_any(robot, goals, …)`: uniform-cost search (Dijkstra) over body anchors, with the edge cost of entering an anchor equal to `move_duration_ticks` for the chassis and the terrain under that body, and neighbours expanded in `CARDINAL_DIRECTIONS` order with a FIFO tie-break counter. It stops at the first goal popped. The first cell of the route becomes the proposed step:

- no route → `UNREACHABLE`;
- a route whose first step the executor still refuses → `BLOCKED` (retry next update).

It re-plans from scratch on every call; nothing is cached (except for hunts, below). A cheaper long route over normal ground beats a short one over rough, and a chassis never routes through terrain it cannot enter.

### Approaching another robot's body

A robot target's anchor is occupied by its body, so neither policy aims at it:

- `body_alignment_anchors(tx, ty)` — the four anchors two cells away on one axis and level on the other: the two bodies touch along a full edge and a shot along the facing connects;
- `body_contact_anchors(tx, ty)` — every anchor whose body touches the target's body along an edge (or overlaps it), excluding corner-only contact.

`NonElectronicNavigation.next_step_to_body` greedily heads for the nearest aligned anchor (ties by cell) and is `ARRIVED` on any aligned anchor. `ElectronicNavigation.next_step_to_body` plans to the aligned anchors, else to the contact anchors, and is `ARRIVED` when the route is empty.

### Search & Destroy hunts (`navigation.next_hunt_step`)

Used only by Search & Destroy (robots); it never reports `UNREACHABLE`.

- No electronics: `NonElectronicNavigation.next_step_to_body`, no cache.
- Electronics:
  1. wait while a move or turn is in flight; `ARRIVED` on an aligned anchor;
  2. the cache is *fresh* when it names the same target and `0 ≤ tick − planned_tick < robot_hunt_replan_ticks`. A fresh "no route" cache → `_greedy_hunt_step`. A fresh `""` cache while still touching the target → `ARRIVED`. Otherwise take the cell after the robot's position on the cached route; drop it if the robot is off the route, the route is exhausted, or that cell is no longer enterable;
  3. with no usable next cell, re-plan (aligned anchors, else contact anchors) and store a new cache stamped with this tick; no route → store "no route" and take `_greedy_hunt_step`;
  4. the step is validated by the executor; a refused step is `BLOCKED` without re-planning.
- `_greedy_hunt_step`: `ARRIVED` when already touching the target; otherwise one primary step toward the nearest aligned anchor, or `BLOCKED` — no detour.

`orders.apply_order_evaluations` writes the returned cache back to the robot. Any order change, a fallback and docking clear it.

### Performance

All of the following are pure caches keyed by object identity, holding their inputs alive, and do not change results:

- `_traversal_view(state, world)`: per-anchor indices of static blocking, robot bodies and reservations, and commander-overlapped anchors — built once per state and shared by every robot planning that tick;
- `_static_blocked(world)`: anchors blocked by structures, per world;
- `_static_terrain(robot, world, rules)`: per chassis, a flat array of terrain-enterable anchors and step costs, per terrain grid and rules; the search runs over flat indices with a padding row so neighbours need no bounds checks;
- `movement.static_occupancy` and `movement.folded_robot_occupancy`.

## Constants

| Name | Value |
|---|---:|
| `dumb_wander_commit_ticks` | 16 (4 game cycles) |
| `robot_hunt_replan_ticks` | 20 (1 s) |
| move costs | `robot_move_ticks_*` ([movement.md](movement.md#constants)) |

## Determinism notes

The only randomness is the perpendicular order for robots without electronics, from a stream keyed by match seed, tick window and robot id. Route search uses fixed neighbour order and insertion-order tie-breaks; goal sets are used only for membership. Navigation draws nothing from the contention stream.

## Spectrum evidence

- `Lb222_choose_direction_to_move` — intersect directions toward the target with the possible directions (`Lb513_get_robot_movement_possibilities`), pick one at random.
- `Lb326`, `Lb33e_pick_direction_at_random` — when none points at the target, pick any possible direction.
- `Lb1f5`, `ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING` — keep walking for `rand & 3 + 3` cycles.
- The original has no electronics pathfinder; electronic routing is a project rule.

## Deviations

- [Dumb-robot momentum instead of the keep-walking counter](../../_specs/deviations-from-original.md#dumb-robot-momentum-instead-of-the-keep-walking-counter).

## Tests that pin it

- `engine/tests/test_navigation.py::test_navigation_policy_is_selected_by_the_electronics_module`
- `engine/tests/test_navigation.py::test_a_proposed_step_is_always_accepted_by_the_movement_executor`
- `engine/tests/test_navigation.py::test_non_electronic_breaks_an_equal_axis_delta_in_favor_of_x`
- `engine/tests/test_navigation.py::test_non_electronic_keeps_walking_the_way_it_faces_when_the_primary_is_blocked`
- `engine/tests/test_navigation.py::test_non_electronic_rounds_a_staggered_pair_of_walls_without_looping`
- `engine/tests/test_navigation.py::test_non_electronic_keeps_its_detour_direction_for_the_whole_window`
- `engine/tests/test_navigation.py::test_non_electronic_detours_are_per_robot_not_in_lockstep`
- `engine/tests/test_navigation.py::test_non_electronic_never_reports_unreachable_because_it_searches_nothing`
- `engine/tests/test_navigation.py::test_electronic_prefers_a_longer_ordinary_route_over_a_slower_rough_one`
- `engine/tests/test_navigation.py::test_electronics_never_routes_a_bipod_or_tracks_through_a_ditch`
- `engine/tests/test_navigation.py::test_electronic_reports_unreachable_when_a_wall_fully_separates_the_target`
- `engine/tests/test_navigation.py::test_body_alignment_anchors_are_the_four_full_edge_positions`
- `engine/tests/test_navigation.py::test_body_contact_anchors_exclude_diagonal_corner_contact`
- `engine/tests/test_navigation.py::test_body_approach_closes_the_stagger_instead_of_stopping_on_it`
- `engine/tests/test_navigation.py::test_symmetric_detour_tie_is_broken_canonically_not_incidentally`
- `engine/tests/test_hunt_route.py::test_a_target_plugging_a_corridor_does_not_end_the_hunt`
- `engine/tests/test_hunt_route.py::test_a_hunter_with_a_valid_route_plans_at_most_once_per_replan_interval`
- `engine/tests/test_hunt_route.py::test_an_unenterable_next_cell_forces_one_early_replan_around_it`
- `engine/tests/test_hunt_route.py::test_a_hunter_with_no_route_waits_for_the_next_replan_tick`
- `engine/tests/test_hunt_route.py::test_route_cells_encode_and_decode_as_direction_letters`
