# CR003 — Playtest Fixes

## Goal

Implement the owner change request of 2026-09-22, raised from playtesting CR002:

1. The commander descends too slowly.
2. Robots stay on a factory after capturing it. They should keep looking for neutral/enemy factories while any remain.
3. Commander movement is jerky, not smooth.
4. The robot menu belongs on the right side of the screen, as in the Spectrum (reference: [`cr003/robot-menu.png`](cr003/robot-menu.png)).
5. The left map-edge wall looks different from the right one. The right one is correct.
6. A Search & Destroy (robots) robot does nothing even when enemy robots exist.

## Owner decisions (2026-09-22)

| Topic | Decision |
|---|---|
| Descent speed | Deliberate change from the Spectrum. `commander_descent_step` goes from 1 to **2** per vertical update (cadence stays 4 ticks), so 48 → 0 takes 4.8 s, the same as the ascent. This replaces the +2/−1 lock in `open-questions.md` §13. |
| Robot heights | Adopt the Spectrum piece heights (`Ld7b4_piece_heights`): bipod 11, tracks 7, anti-grav 8, cannon 6, missile 6, phaser 7, nuclear 7, electronics 7. They replace the placeholder `module_height_*` values (chassis 4, others 2). |
| Capture orders | Full Spectrum behavior (see item 2 evidence): the order persists, the robot retargets after each capture, it idles under the same order when nothing is left, and targets are exclusive per order. |
| Commander movement | Frontend-only fix. The protocol and the engine's one-cell, cardinal-only move model stay as they are. |

## Evidence and root causes

References are to `netherearth-annotated.asm` (`santiontanon/netherearth-disassembly`).

**1. Descent.** `Laf11_player_ship_keyboard_control` runs once per game cycle (`La69a_game_loop`). `Lafb5_elevate` adds 2 and `Lafc3_gravity` subtracts 1. `MIN_INTERRUPTS_PER_GAME_CYCLE = 10` caps the game at 5 cycles/s. The engine matches that (4 ticks at 20 Hz). The change is an owner decision, not a fidelity fix.

**2. Capture.** `engine/src/nether_earth/orders.py` completes `SearchCapture` when the robot reaches the footprint and replaces the order with `StopAndDefend`. The robot therefore stays on the factory for good. It also falls back to `StopAndDefend` when no target exists. The Spectrum is different in three ways:
- `Lb289_choose_direction_orders_with_building_targets`: each update it checks whether the stored target (`ROBOT_STRUCT_ORDERS_ARGUMENT`) still matches the order's ownership flags. If not (for example, just captured), it calls `Lb34d_find_capture_or_destroy_target` for a new one. The order never changes.
- With no target found, a player robot keeps its order and does not move (`ld c, 0; ret`). An enemy AI robot switches to Destroy Enemy Robots. Our bot keeps whatever behavior it has today; this CR changes only the robot rule.
- `Lb36c_check_if_building_is_available_and_nearest_than_current_nearest` skips a building that another friendly robot with the same order already targets.

**3. Commander movement.** `frontend/src/input/keyboard.ts` sends a `commander_move` every 50 ms while a key is held. The engine rejects a move while one is in flight (`MOVE_IN_PROGRESS`), and a move takes `commander_horizontal_move_ticks = 4`. A new move starts only when a command happens to arrive on or after the completion tick. Network jitter therefore leaves idle ticks between cells, so the movement stops and starts. The camera lerp (`renderer.ts`, 0.15 per frame) follows that stop-start motion.

**4. Robot menu.** `#menus` (`frontend/src/style.css`) spans the bottom of the screen above the radar. In the Spectrum it is the right-hand HUD column: DAY/TIME at the top, then DIRECT CONTROL / GIVE ORDERS / COMBAT MODE / LEAVE ROBOT stacked, then ORDERS and the current order, then STRENGTH.

**5. Map-edge wall.** Both walls are the same data: 8 `fence` blockers each at columns 12–13 and 503–504 (`data/maps/zx-spectrum-original.yaml`), mapped to one asset. The difference is in rendering (sprite choice, facing, draw order or occlusion under the lower-left → upper-right view). The root cause is still to be found.

**6. Search & Destroy.** Reproduced in the engine (tracks + cannon hunter against a tracks + cannon enemy on flat ground, 200 ticks):
- *Shots never hit.* `combat._robot_hit_at` hits a robot only when `robot_top >= normal_projectile_altitude` (10). With the placeholder heights, a tracks + cannon robot is 4 + 2 = 6 high, so every shot flies over it (`RANGE_EXHAUSTED`, strength stays 100). With the Spectrum heights, the smallest robot is 7 + 6 = 13, so every robot on any ground is hit, as in the original.
- *Electronic robots drop the order.* `select_destroy_target` gives the target robot's own cell as the goal. That cell is occupied, so `ElectronicNavigation.plan_route` returns `UNREACHABLE` and the order falls back to `StopAndDefend` on the first tick. Robots without electronics use greedy steps and just report `BLOCKED`.

## Dependencies

- Start: CR002 merged work on `main` (`RULES_VERSION = "cr002"`).
- CR003.1–CR003.4 and CR003.10 change gameplay rules. They share one rules-version bump (CR003.8), and old replays are rejected, not mis-verified.

## Tasks

Tracker: #225.

| ID | Task | Depends on |
|---|---|---|
| CR003.1 (#216) | Commander descent step 2 | — |
| CR003.2 (#217) | Capture orders persist, retarget after capture, exclusive targets | — |
| CR003.3 (#218) | Spectrum robot piece heights | — |
| CR003.4 (#219) | Search & Destroy (robots) engages: electronic navigation toward an occupied target | CR003.3 |
| CR003.5 (#220) | Smooth commander movement (frontend only) | — |
| CR003.6 (#221) | Robot menu in the right-hand HUD column | — |
| CR003.7 (#222) | Left map-edge wall renders like the right one | — |
| CR003.8 (#223) | Rules version bump, spec updates, fixture regeneration | CR003.1–CR003.4, CR003.10 |
| CR003.10 (#232) | Commander moves chain without an idle tick (engine step order) | — |
| CR003.11 (#235) | Camera centres the play view beside the menu column | CR003.6 |
| CR003.9 (#224) | CR003 acceptance gate | all |

Parallel groups: engine (CR003.1, CR003.2, CR003.3 → CR003.4) and frontend (CR003.5, CR003.6, CR003.7). CR003.5 and CR003.7 both touch `renderer.ts`, so merge one after the other.

### CR003.1 — Commander descent step 2

- `EngineRules.commander_descent_step = 2`. Gravity still stops at the surface under the ship (the landing clamp must not skip past a surface at an odd altitude; clamp to the surface, not below it).
- Tests: descent 48 → 0 in 24 updates; landing on a box of odd height rests exactly on it; docking on a robot top of odd height still triggers.
- Frontend: no code change expected (the vertical transition already interpolates). Verify visually.

### CR003.2 — Capture orders keep hunting

- `SearchCapture` stores its current target (the Spectrum's `ROBOT_STRUCT_ORDERS_ARGUMENT`). Each evaluation keeps that target while its ownership still matches the order. Otherwise it selects the nearest matching structure that no other same-owner robot with the same order already targets (`Lb36c`). Ties break deterministically in canonical order.
- Standing on the footprint of a target that is not yet captured: the order stays `ACTIVE`, the robot holds position with the defensive intent, and `capture.py` counts the occupation as today.
- Once captured, the next evaluation retargets and the robot leaves.
- No target: the order stays, the robot does not move and uses the defensive intent. It resumes when a matching structure appears (for example, a factory lost to the enemy).
- The target is serialized with the order, so snapshots and replays round-trip it. Do not change the protocol order-command payload.
- Tests: capture two neutral factories in sequence with one robot; two robots with the same order split two factories; no target → idle with the order kept, then resumes when a factory changes hands.

### CR003.3 — Spectrum robot piece heights

- Set `module_height_*` to the `Ld7b4_piece_heights` values and replace the "placeholder" rationale in `rules.py` with the evidence.
- Effects that follow automatically: robot top, damage `(60 − (height + ground)) / 4`, docking altitude, and the ship's collision with robots. Check that the tallest robot on the highest terrain (38 + 6 = 44) stays within `commander_max_altitude` (48).
- Frontend: robots are drawn from their modules' sprites. Check that the stack drawing and the commander dock position use the snapshot height consistently.
- Tests: the Spectrum's worked example (phaser on a tracks + cannon robot at ground level deals 44); a projectile hits a tracks + cannon robot on flat ground.

### CR003.4 — Search & Destroy (robots) engages

- Electronic navigation toward a robot target plans to the target's body, meaning any cell from which the hunter's 2×2 body touches or overlaps the target's cells. It must not report `UNREACHABLE` just because the goal cell is occupied. Keep the policy stateless, and keep robots without electronics on greedy steps.
- Regression tests (from the reproduction above): with and without electronics, a Search & Destroy hunter on flat ground closes on an enemy, damages it and destroys it; the order is still `SearchDestroy` while a hostile robot exists.

### CR003.5 — Smooth commander movement (frontend only)

- While a direction is held, the client sends the next `commander_move` so it reaches the server by the tick the current move completes. For example, send it when the local display tick reaches the in-flight transition's end, instead of on a blind 50 ms pulse. There must be no idle tick between consecutive cells under normal latency.
- The camera follows the interpolated position without lag or overshoot.
- No engine or protocol change, and no client-side legality checks.
- Check: a held key for 10 cells in a local run shows contiguous transitions (each move's `started_tick` equals the previous move's end tick) in the snapshot stream. Add a unit test for the scheduling function.

### CR003.6 — Robot menu on the right

- When docked, show the robot menu in a right-hand HUD column beside the play view, matching `cr003/robot-menu.png`: DAY/TIME at the top; DIRECT CONTROL / GIVE ORDERS / COMBAT MODE / LEAVE ROBOT as stacked blocks with the selected one highlighted; "ORDERS" and the current order text; "STRENGTH" and a percentage. Use the Spectrum fonts (CR002.10).
- The play view and radar stay on the left. The layout must still work at phone width.

### CR003.7 — Left map-edge wall

- Render both the left (columns 12–13) and the right (columns 503–504) fence the way the right one is drawn now. Find and fix the root cause (sprite choice, facing, depth order or occlusion); do not special-case the left column.
- Check: screenshots of both edges at the same zoom; a unit test on the resolved sprite/draw order if the cause is in code.
- Resolved (owner decision 2026-09-22): the fence sprite's post stands on the −x column of its footprint, so it touches the play area at the right end and leaves a column gap at the left end (the Spectrum has the same asymmetry). The renderer now centres the post through a per-asset `offset` in the scenery manifest. This is presentation only, and the Spectrum's 4-px odd-parity shift is not applied. Evidence: `open-questions.md`, "Map-end fence placement".

### CR003.10 — Commander moves chain without an idle tick

Found by CR003.5 (`open-questions.md` §23). Owner decision (2026-09-22): fix it in the engine.

- `engine.step` resolves due commander horizontal transitions before it applies the tick's commands, so a `commander_move` on the completion tick starts at once (4 ticks per cell, no idle tick).
- Robots keep their cadence: they already resolve due moves (Step 2b) before starting new ones (Step 2c).
- Tests: a `commander_move` held on every tick starts cells at ticks 1, 5, 9, …; the robot move tests pass unchanged. The CR003.5 scheduler model and lead follow the new order.
- `RULES_VERSION` is bumped by CR003.8.

### CR003.8 — Rules version and specs

- `RULES_VERSION = "cr003"`. Regenerate the M9 full-match fixture and any replay fixtures.
- Update `open-questions.md` §13 (descent step 2, an owner deviation from the Spectrum), the module-height notes (Spectrum values), and the order lifecycle (capture no longer completes). Update `functional-spec.md` §8.2 and `technical-spec.md` §9 where they state +2/−1.

### CR003.9 — Acceptance gate

- All engine, backend and frontend checks pass; the M9 scripted full match and the live two-client check pass.
- Two-browser gateway run (`docs/milestone-10/browser-smoke.mjs`) passes.
- Owner playtest of the six reported items against the reference image.
