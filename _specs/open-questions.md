# Nether Earth Clone — Open Questions

This file tracks gameplay/implementation details that are still unresolved after the current design and ZX Spectrum disassembly review. Resolved items remain listed so their locked outcome is easy to find.

## 1. Exact normal-weapon stack order — RESOLVED

Locked bottom-to-top order:

1. chassis
2. cannon, if fitted
3. missile, if fitted
4. phaser, if fitted
5. nuke, if fitted
6. electronics, if fitted
7. commander, when docked

Missing components are simply omitted; relative order is preserved.

## 2. PvP treatment of the remaining war bases — RESOLVED

- Original four-war-base map retained.
- Player 1 owns the extreme-left war base.
- Player 2 owns the extreme-right war base.
- Two interior war bases start neutral and capturable.
- Starting ownership belongs to scenario overlay data, not raw map geometry.
- Victory: opponent owns zero war bases.

## 3. Miles-to-grid-cell conversion — RESOLVED (weapon/nuke lines superseded by CR001)

**CR001 (2026-09-21):** the Spectrum code confirms 1 mile = 2 coordinate units (`Laab8_miles_selected`: `rlca ; multiply by 2: 1 mile == 2 coordinate units`), and one coordinate unit is one map cell on both axes (the map buffer is `MAP_LENGTH * MAP_WIDTH` = 512 × 16 bytes, one byte per cell). This still holds for Advance/Retreat. The weapon-range and nuke-radius lines below are superseded: weapon ranges come from the code in cells (§8), and the nuclear blast has per-kind shapes (§20).


- 1 mile = 2 map tiles/cells.
- 1 tile = 0.5 miles.
- Cannon 10 miles = 20 tiles.
- Missile 14 miles = 28 tiles.
- Phaser 10 miles = 20 tiles.
- Electronics +3 miles = +6 tiles.
- Nuclear radius 8 miles = 16 tiles.
- Advance/Retreat 0–50 miles = 0–100 tiles.

The conversion must exist once in shared game-rule/helper code.

## 4. Exact movement speeds and terrain penalties — RESOLVED (CR001, owner decision 2026-09-21)

**Resolution.** Verified by a direct reading of `netherearth-annotated.asm` (`santiontanon/netherearth-disassembly` @ `762e33e`):

- `Lb61d_robot_movement_speed_table` (cycles per move; 1 cycle = 4 ticks):
  flat 6/4/3, rugged 8/6/3, mountains 9/7/4 (bipod/tracks/anti-grav).
- `Lb5f3_determine_speed_based_on_terrain` picks the row from the robot's altitude: 0 → flat, 1–3 → rugged, ≥4 → mountains. The altitude is the highest piece height under the robot's 2×2 footprint (`Lb5d6_map_altitude_2x2`, set after every move at `Lb495`).
- `Lb513_get_robot_movement_possibilities` blocks a map element whose **type index** (`and #1f`) is ≥ 8 (bipod), 12 (tracks), or 15 (anti-grav).
- `Ld7bc_map_piece_heights`: types 0–1 have height 0; types 2–5 height 2; types 6–7 height 3; types 8–11 height 6; types 12–14 height 0; type 15+ are structures.

So the original has four terrain classes:

| Class | Element types | Bipod | Tracks | Anti-grav |
|---|---|---|---|---|
| normal | 0–1 | 24 | 16 | 12 |
| rough | 2–7 | 32 | 24 | 12 |
| mountain | 8–11 | blocked | 28 | 16 |
| ditch | 12–14 | blocked | blocked | 12 (height 0, so flat speed) |

**Owner decisions:** adopt the table as per-(chassis, terrain) tick values and add a `MOUNTAIN` terrain class. The earlier qualitative claim "tracks slow down less than bipod on rough" is revised to "tracks stay faster than bipod; both lose 8 ticks per cell on rough." Anti-grav's ditch speed equal to its flat speed is **correct**; it is not an under-estimate. The earlier mountains→ditch mapping below was wrong.

**Follow-up (done, CR001.5 #152):** `data/maps/zx-spectrum-original.yaml` now carries the decoded terrain (344 rough, 436 mountain, 204 ditch cells; provenance and spot checks in `data/maps/zx-spectrum-original.md`, "Terrain"). No element type in the map was ambiguous under the classes above.

**Decided (owner, 2026-09-21; implementation CR002.1 #168, assets CR002.5 #172): blockers exist; asset per blocker kind is configurable, Spectrum assets only in v1.**

**Scenery blockers — RESOLVED (CR002.1, #168).** Verified by a direct reading of `netherearth-annotated.asm` (`santiontanon/netherearth-disassembly` @ `762e33e`):

- *Data.* `data/maps/zx-spectrum-original.yaml` now has a `blockers` section: 165 entries, one per 2×2 scenery element stamped by `Lbd91_add_element_to_map` (660 cells; no element is partly overwritten). Each entry has a data `kind` per element type and the height from `Ld7bc_map_piece_heights`: type 17 → `box_low` (7), 18 → `box_high` (15), 21 → `fence` (99). Only "fence" is a name from the code (`Lba44_robots_handled`: "the fences that mark the end of the map in each end"; the 16 fence elements close columns 12–13 and 503–504). `box_low`/`box_high` are descriptive labels. The engine never branches on `kind`; the frontend maps it to an asset (CR002.5). Provenance: `data/maps/zx-spectrum-original.md`, "Blockers/scenery".
- *Robots.* `Lb513_get_robot_movement_possibilities` sets the chassis limit to 8/12/15 and `Lb5cd_robot_map_collision_internal` refuses any element type ≥ the limit, so types 17/18/21 stop every chassis. The engine already treats every static component as an occupied cell (`movement.validate_robot_move` → `OCCUPIED`), and navigation routes around them.
- *Commander.* `Lb052_check_player_collision` takes the highest piece height under the ship and reports a collision when `player_altitude < height` (`cp c`, carry). A horizontal move (`Laf4c_move_player_if_no_collision`, and the y move after it) is refused on collision, and gravity (`Lafc3_gravity`) does not descend below that height. So the ship crosses a box only at altitude ≥ its height and lands on top of it (rests at altitude = height). A fence (99) is above `MAX_PLAYER_ALTITUDE` (48), so the ship can never cross it. This is exactly the engine's existing static-component rule in `collision.py` (touching is allowed, overlapping is blocked); no engine change was needed.
- *Projectiles.* `Lb724_bullet_update_internal` destroys a bullet when `Lb5d6_map_altitude_2x2 >= BULLET_STRUCT_ALTITUDE` (`cp` / `jp nc`), and `Lb6d6_weapon_fire` sets that altitude to 10. So bullets fly over `box_low` (7) and are stopped by `box_high` (15) and `fence` (99). This is the engine's existing `height >= normal_projectile_altitude` rule in `combat.py`; no engine change was needed.

Tests: `engine/tests/test_scenery_blockers.py`. Two related details from the same reading are not implemented here. Both are listed under "Remaining research" below: the nuclear blast destroys scenery, and the original checks collisions over 2×2 areas.

Original note — scenery blockers: the same decode places 660 cells of element types 17, 18 and 21 (boxes and walls, heights 7/15/99). `Lb513` blocks types ≥ 15 for every chassis, so in the original no robot can enter these cells. They are not terrain classes, and the map has no `blockers` section, so this clone currently lets robots walk through them. CR001 did not scope this, although the CR001.5 issue text assumed "15+ are … scenery already modeled". The decision needed is whether to encode these cells as map `blockers` and in which change request. Two things need settling first: how they interact with the commander's flight and landing (heights 7/15/99), and how they interact with projectiles.

The research history below is kept for provenance.


Locked:

- bipod is slowest, tracks faster, anti-grav fastest on ordinary terrain;
- bipod crosses rough terrain with severe slowdown;
- tracks cross rough terrain with a smaller slowdown;
- bipod and tracks cannot cross ditches/ravines;
- anti-grav can traverse every terrain type including ditches/ravines;
- authoritative movement timing is integer ticks-per-tile at 20 Hz;
- values live in centralized game-rule configuration.

Issue #61 (M5.2) research findings, encoded in `engine/src/nether_earth/rules.py`'s
`EngineRules.robot_move_ticks_*` / `robot_rough_multiplier_*` /
`robot_ditch_multiplier_anti_grav` fields (the movement-timing home issue
#60/M5.1 established; evidence trail lives in that module's docstring and
in each field's own docstring entry) and consumed by
`engine/src/nether_earth/movement.py`'s `move_duration_ticks`:

**Newly resolved (disassembly-evidence-backed):**

- default ordinary-terrain ticks per cell, derived from
  `santiontanon/netherearth-disassembly`'s `netherearth-annotated.asm`
  `Lb61d_robot_movement_speed_table` (bipod/tracks/anti-grav = 6/4/3
  "cycles" on flat terrain) and the game's own documented cadence
  (`MIN_INTERRUPTS_PER_GAME_CYCLE: equ 10 ; game maximum speed is 5 frames
  per second`, i.e. 1 cycle = 200 ms = 4 ticks at the locked 20 Hz rate):
  bipod = 24 ticks/cell, tracks = 16 ticks/cell, anti-grav = 12 ticks/cell
  (`robot_move_ticks_bipod` / `robot_move_ticks_tracks` /
  `robot_move_ticks_anti_grav`). This evidence was retrieved via an
  automated fetch (not a first-hand raw read) but is corroborated: the same
  fetch independently reproduced two other already-locked constants from
  this file (`INITIAL_PLAYER_RESOURCES: equ 20`, `MAX_ROBOTS_PER_PLAYER:
  equ 24`), matching §10 and `rules.py`.
- anti-grav's rough-terrain multiplier (`robot_rough_multiplier_anti_grav
  = 1`, i.e. no penalty) is now evidence-backed rather than a placeholder:
  the same disassembly table shows anti-grav identical on flat/rugged (3
  cycles both).
- anti-grav is **not** uniform across every traversable terrain type once
  ditch is included: the disassembly table shows anti-grav slower on the
  most extreme ("mountains") terrain tier (4 cycles vs. 3 on flat/rugged,
  a ~1.33x ratio). See "still open" below for why this could not be
  encoded as an exact `robot_ditch_multiplier_anti_grav` value.

**Still open / explicitly NOT resolved by this research pass:**

- exact bipod rough-terrain penalty and exact tracked rough-terrain
  penalty: the disassembly table itself (bipod 6->8 cycles, tracks 4->6
  cycles on rugged terrain) does **not** unambiguously support the locked
  "tracks penalized less severely than bipod" ordering under either an
  absolute-increase reading (tied, +2 cycles each) or a proportional
  reading (tracks' 1.5x proportional slowdown is actually *larger* than
  bipod's 1.33x) — a genuine conflict between hard disassembly evidence and
  the previously locked qualitative claim above, not a rounding artifact.
  Neither raw ratio (1.33x, 1.5x) is representable as a clean integer
  `robot_rough_multiplier_*` without inventing precision the evidence does
  not support, so `robot_rough_multiplier_bipod = 3` /
  `robot_rough_multiplier_tracks = 2` remain the pre-existing, explicitly
  unverified placeholder values (chosen only to satisfy the locked
  qualitative ordering, which they do: 3 > 2 > 1). **This conflict needs a
  human decision**: either accept the disassembly numbers and revise the
  locked qualitative claim (and possibly relax the multiplier field to a
  non-integer/rational type), or keep the qualitative claim and accept that
  the exact rough-penalty magnitude is permanently a configurable,
  non-Spectrum-exact default.
- exact anti-grav ditch multiplier: the disassembly-evidenced flat-to-
  mountains ratio (~1.33x) is likewise not representable as a clean integer
  multiplier on the `EngineRules` schema. `robot_ditch_multiplier_anti_grav`
  therefore remains at its pre-existing placeholder value (`1`, i.e. no
  penalty) — this is a known **under-estimate** relative to the disassembly
  evidence above (which suggests anti-grav should be measurably slower,
  not equally fast, on ditch terrain), left unchanged rather than silently
  replaced with an invented integer (e.g. `2`, which would overstate the
  penalty). Flagged here as open rather than corrected in place.
- exact terrain-tier correspondence: the disassembly's speed table is keyed
  by a continuous per-cell altitude tier (flat / rugged / mountains) gated
  by a separate per-chassis altitude ceiling (bipod 8, tracks 12, anti-grav
  15), not by this project's discrete `NORMAL`/`ROUGH`/`DITCH` categories
  with a categorical bipod/tracks ditch ban. Mapping flat->`NORMAL` and
  rugged->`ROUGH` is direct; mapping "mountains"->`DITCH` (the closest
  available evidence for anti-grav's non-ordinary-terrain speed, since
  ditch remains categorically forbidden for bipod/tracks) is an
  interpretive judgment, not a verified one-to-one correspondence.
- whether anti-grav speed is identical across *every* traversable terrain
  type: disassembly evidence suggests **no** (see above), but the current
  shipped `robot_ditch_multiplier_anti_grav = 1` default does not yet
  reflect that finding (see the ditch-multiplier item above) — this is an
  intentionally recorded gap, not a resolved "yes."

## 5. Dumb vs electronic navigation — RESOLVED

- Non-electronic robots use deliberately limited/original-style local routing and may become blocked even if a longer route exists.
- Electronic robots use proper deterministic pathfinding/replanning around obstacles.
- Electronics improves routing only; it does not change chassis terrain permissions.
- Navigation strategies must be isolated behind an engine policy/interface.

Exact historical quirks of the dumb algorithm remain research detail, not a product decision.

## 6. War-base capture mechanics — RESOLVED

- Enemy robots can capture war bases.
- War-base capture uses the same continuous-occupation rule as factory capture by default.
- Default duration: 12 in-game hours = 1,440 simulation ticks = 72 real seconds at 20 Hz.
- Ownership changes immediately when the duration completes.
- Victory is evaluated in the same authoritative simulation step.
- Capture duration is configurable game-rule/scenario data.

## 7. Capture interruption semantics — RESOLVED

If qualifying occupation breaks before capture completes, progress resets immediately to zero. Partial progress is not retained.

## 8. Exact projectile mechanics — RESOLVED (CR001, owner decision 2026-09-21)

**Resolution.** One raw coordinate unit is one map cell on **both** axes (map buffer = 512 × 16 bytes). The "coordinate doubling" premise below was wrong: bullets move **2 cells per update** on either axis (`Lb724_bullet_update_internal`), one update per game cycle (4 ticks).

Range: `Lb6d6_weapon_fire` sets the counter to 5 (cannon/phaser) or 7 (missiles), +1 with electronics, and makes the first move at fire time. `Lb70d_bullet_update` decrements the counter before each later move and removes the bullet at 0. The bullet therefore travels 2 × counter cells:

- cannon 10 cells, phaser 10 cells, missile 14 cells, electronics +2 cells.

The manual's "10/14 miles" weapon ranges equal these cell counts, which shows the manual used cells for weapons.

**Owner decision:** adopt the code values: `projectile_cells_per_advance = 2`, ranges 10/14/10 cells, electronics +2. They replace the mile-derived 20/28/20/+6.

**Settled with no change needed:** there is no separate building collision rule; the generic altitude collision covers buildings.

**RESOLVED (owner decisions 2026-09-21; implemented by CR002.2 #169): fire-cycle timing.** Evidence (`netherearth-annotated.asm` @ `762e33e`):

- *First move at fire time.* `Lb6d6_weapon_fire` calls `Lb724_bullet_update_internal` before returning, so a bullet makes its first 2-cell move when it is fired. `Lb70d_bullet_update` then decrements the range counter before each later move and removes the bullet when the counter reaches 0.
- *One game cycle.* The game loops (`La69a_game_loop`, `La94c_direct_control_loop`, `Laa70_select_miles_loop`, `Lacd7_right_hud_menu_control`) each run `Lb0ca_update_robots_bullets_and_ai` once and then `Lad62_increase_time` once. That is one game cycle (4 engine ticks). Inside `Lb0ca_update_robots_bullets_and_ai`, the robot loop (`Lb0fa_robot_update` → `Lb154_robot_ai_update`) runs first, and then the bullet loop runs `Lb70d_bullet_update` on every live bullet.
- *AI shots.* An AI robot fires from `Lb154_robot_ai_update` (`call Lb6d6_weapon_fire`) inside the robot loop. The bullet loop that follows in the same cycle updates the new bullet again, so an AI shot moves 4 cells in its fire cycle.
- *Combat-mode shots.* A player's combat-mode shot (`Lacb3_regular_weapon_fire`: `Lb6d6_weapon_fire`, then `Lccbd_redraw_game_area`, then `Lad62_increase_time`) is fired outside `Lb0ca_update_robots_bullets_and_ai`. Its fire step uses up one time step with no bullet update, so it moves 2 cells in its fire cycle, and the next `Lb0ca_update_robots_bullets_and_ai` call moves it again. Each combat-mode shot uses up one time step, so it fires at most once per cycle. It uses bullet slot 0 only, which is reserved for the player's robot. In direct-control driving mode (`La941_direct_control_internal`), pressing fire exits instead of firing.

Engine rules (the owner decided each one; the code follows the evidence above):

- `combat.apply_fire` creates the projectile and immediately applies one advance, with the same range, bounds, static-collision and robot-hit checks as a normal advance. A target 1–2 cells away is hit on the fire tick. A shot ended by its first move never occupies the firing robot's combat channel.
- Fire cycles are the windows `tick // robot_fire_cycle_ticks` (`EngineRules.robot_fire_cycle_ticks = 4`, one game cycle). A robot fires at most one normal weapon per fire cycle, whether autonomously or by `FireCommand`. `Robot.last_fire_tick` enforces this, and a second attempt is rejected with `already_fired_this_cycle`.
- The cadence tick that closes the fire cycle, `(t // 4 + 1) * 4`, stands for that cycle's bullet loop. An autonomous (AI) shot moves again there. A direct (`FireCommand`, combat-mode) shot is held until one cycle later. `Projectile.first_advance_tick` records this, and the snapshot carries it so clients can interpolate. Every later move keeps the 4-tick cadence, and the total range is unchanged (10/14/10 cells, +2 electronics).
- Resulting timing for a cannon fired on tick 5. Autonomous: +2 on tick 5, then +4/+6/+8/+10 on ticks 8/12/16/20, and it expires on tick 24. Direct: +2 on tick 5, no move on tick 8, then +4/+6/+8/+10 on ticks 12/16/20/24, and it expires on tick 28.

Previous note, kept for provenance: the engine used to create the projectile at fire time and make its first move on the next advance tick.

**Follow-ups found while implementing #169:**

- *AI fire happens only on the robot's own update — RESOLVED (owner decision 2026-09-21; implemented by CR002.19 #197).* Evidence (`netherearth-annotated.asm` @ `762e33e`):
  - `Lb154_robot_ai_update` runs `dec (iy + ROBOT_STRICT_CYCLES_TO_NEXT_UPDATE)` / `ret nz`, so a robot does nothing (no fire, no move) until its own counter reaches 0.
  - On the update, an enemy ahead in the facing direction makes it call `Lb6d6_weapon_fire`, then set `ROBOT_STRUCT_DESIRED_MOVE_DIRECTION` to 0 and `jr Lb20d_move_robot`. `Lb471_move_robot_one_step_in_desired_direction` returns at once for direction 0 (`or a` / `ret z`), so a firing update does not move or turn.
  - Every update, firing or not, ends in `Lb20d_move_robot` → `Lb5f3_determine_speed_based_on_terrain`, which reloads the counter from `Lb61d_robot_movement_speed_table` using `ROBOT_STRUCT_ALTITUDE`. That altitude is only rewritten after an actual advance (`Lb495`: `Lb5d6_map_altitude_2x2`), so after a firing update the next period is the speed of the cell the robot stands on; after a move it is the speed of the cell it moved into. The reset is the same in both cases.
  - If no bullet slot is free (`Lb6b8_find_new_bullet_ptr`, `jr nz, Lb1cd_do_not_fire`), the robot does not fire and moves that update instead.

  Engine rules: an order-driven robot is at an update when it has no move in flight and at least one period has passed since its `last_fire_tick`; the period is `movement.move_duration_ticks(chassis, terrain of its own cell)`, the same §4 per-(chassis, terrain) table the move duration uses (bipod on flat: 24 ticks = 6 cycles). A move's update is the tick it completes. Autonomous fire is consumed only on an update (`autonomous_combat.autonomous_update_due`). An order's move request is dropped when the robot is not at an update, or when its intent will fire on this update (`autonomous_combat.gate_order_requests`; the check is a dry run through the same `apply_fire`/`validate_fire` path, so the robot moves when the shot would be refused, e.g. with its channel busy). Direct (`FireCommand`, combat-mode) fire is unchanged and keeps the once-per-cycle rule. No new rule constant and no `RULES_VERSION` change (CR002.16 bumps it). Tests: `engine/tests/test_autonomous_fire_update.py`.

  Residual deviation: the engine tracks no update phase for a stationary robot that has not fired since it last moved, so it is treated as being at an update on every tick and fires as soon as it has a shot; in the Spectrum it would wait up to one period for its counter. Once it has fired, its updates follow the period exactly. The period uses the robot's own cell; the Spectrum uses the highest piece under its 2×2 footprint (`Lb5d6_map_altitude_2x2`), which follows the footprint work (#170/#171).

  Also fixed here: `Robot.with_movement`/`with_position`/`with_order`/`with_active_projectile`/`with_strength` dropped `last_fire_tick`, so a shot that stayed in flight cleared it at once (`apply_fire` calls `with_active_projectile`). The M9 fixture's final snapshot now shows the surviving robot's `last_fire_tick` (666) instead of `null`; its command stream and final tick are unchanged.
- *Bullet slots are shared per side — documented deviation (owner decision 2026-09-21).* `Lb6b8_find_new_bullet_ptr` gives all of the player's AI robots bullets 1–2 and all enemy AI robots bullets 3–4, and bullet 0 is reserved for combat mode. So at most two AI bullets per side are in flight at once. The engine deliberately keeps one combat channel per robot instead.

**Still open (research only; not blocking):** the autonomous fire-decision scan (`Lb626_check_directions_with_enemy_robots`) looks 8 cells in each direction, 10 in the facing direction, and 12 facing with electronics, along the robot's lane and the lanes on either side. The engine uses weapon range for engagement. Whether to adopt the scan distances is not decided.

The research history below is kept for provenance.


Locked:

- a robot cannot fire another normal weapon while its current normal projectile is active;
- cannon, missile, and phaser projectile flight altitude defaults to the Spectrum value 10;
- projectile altitude is independent of robot height;
- projectile gameplay is authoritative world/game logic, not browser viewport logic.

Resolved by issue #72 research (`santiontanon/netherearth-disassembly`,
`netherearth-annotated.asm`), each cited to its exact label:

- **Advance cadence**: bullets update exactly once per invocation of
  `Lb0ca_update_robots_bullets_and_ai` (the single per-game-cycle dispatcher —
  confirmed by its `Lb0e9_bullet_update_loop`, a `MAX_BULLETS`-iteration
  `djnz` loop that calls `Lb70d_bullet_update` once for every active bullet,
  unconditionally, every cycle). This differs structurally from robot
  movement: robots gate their own per-cycle update behind a per-robot skip
  counter (`ROBOT_STRUCT_CYCLES_TO_NEXT_UPDATE`, decremented and checked at
  `Lb154_robot_ai_update` line "`dec (iy + ROBOT_STRICT_CYCLES_TO_NEXT_UPDATE)` /
  `ret nz`"), so a robot may take several game cycles per movement step
  depending on chassis/terrain speed. `BULLET_STRUCT` (9 bytes,
  `BULLET_STRUCT_SIZE: equ 9`) has no equivalent timer field, so bullets have
  no such throttle — every active bullet advances on every single game
  cycle. `Lb0ca_update_robots_bullets_and_ai` is itself called exactly once
  per iteration of the main game loop (`La69a_game_loop`, single call site at
  that address used during normal play), and that loop iteration is
  throttled by the disassembly's own documented cadence
  (`MIN_INTERRUPTS_PER_GAME_CYCLE: equ 10 ; game maximum speed is 5 frames
  per second`) — the same cadence issue #61 (M5.2) already mapped onto this
  project's locked 20 Hz tick rate as "1 cycle = 200 ms = 4 ticks". Because
  both robots and bullets are driven from the same dispatcher and the same
  outer game-cycle boundary, reusing "1 cycle = 4 ticks" as the tick
  conversion factor for bullet advancement is architecturally correct to
  reuse (same cycle unit), but it produces a *different effective speed* for
  bullets than for robots, since bullets never skip cycles: a bullet moving
  in a straight line advances every 4-tick cycle, i.e. once every 200 ms,
  unconditionally.
- **Cells advanced per update**: `Lb724_bullet_update_internal` moves the
  bullet by exactly 2 raw coordinate units per update, on one axis only per
  update (direction is one-hot: right = `inc hl` twice, left = `dec hl`
  twice, down = `inc b` twice, up = `dec b` twice — see the `rrca`/`jr nc`
  chain at `Lb724_bullet_update_internal` through `Lb73c_not_down`). A +2
  raw-X step matches the coordinate-doubling convention this project's
  robot-X handling already assumes (2 raw units = 1 logical cell in the wide
  512-unit `MAP_LENGTH` X axis); the Y axis is bounded by the much smaller
  `MAP_WIDTH: equ 16` and is not independently re-verified as "doubled" by
  this research pass — flagged below as still open.
- **Shared speed across weapon types**: confirmed. `Lb6d6_weapon_fire` only
  varies `BULLET_STRUCT_RANGE` (`WEAPON_RANGE_DEFAULT: equ 5` for cannon/
  phaser, `WEAPON_RANGE_MISSILES: equ 7` for missiles, plus `inc c` for
  electronics via `bit 7, (ix + ROBOT_STRUCT_PIECES)`); `Lb724_bullet_update_internal`'s
  movement code path is identical for every `BULLET_STRUCT_TYPE` value (1:
  cannon, 2: missiles, 3: phasers) — there is no type-conditional branch in
  the movement code, only in range assignment at fire time.
- **Termination rules**: confirmed three independent termination paths, all
  jumping to `Lb7ea_bullet_disappear`:
  1. Range exhaustion: `Lb70d_bullet_update` does
     `dec (iy + BULLET_STRUCT_RANGE)` then `jp z, Lb7ea_bullet_disappear`,
     executed exactly once per invocation (i.e. once per game cycle the
     bullet is updated in).
  2. Y-out-of-bounds: after moving, `Lb741_check_out_of_map_in_y` does
     `ld a, b` / `cp MAP_WIDTH` / `jp nc, Lb7ea_bullet_disappear`. **No
     equivalent explicit X-bounds check exists in the traced code** — the
     disassembly's own comment at `Lb741_check_out_of_map_in_y` states this
     directly: "we only need to check out of bounds in the y axis, as, in
     the x axis, there's always obstacles at the ends of the map, so, we
     don't need to check." This project should record X-axis containment as
     "relying on physical fence/obstacle objects at the map edges per the
     disassembly's own stated architecture," not as an independently
     code-verified explicit bounds check.
  3. Altitude/height collision: `Lb747_movement_complete` calls
     `Lb5d6_map_altitude_2x2` (which itself calls `Lb08a_get_map_altitude`
     four times over a 2x2 neighborhood around the bullet's new position and
     returns the max altitude found) and compares the result against
     `BULLET_STRUCT_ALTITUDE` (fixed at 10, set in `Lb6d6_weapon_fire`):
     `cp (iy + BULLET_STRUCT_ALTITUDE)` / `jp nc, Lb7ea_bullet_disappear` —
     terrain/building/robot/decoration altitude at or above the bullet's
     flight altitude terminates it.
  4. Robot-strike collision (not a "disappear" path but also removes the
     bullet): `Lb7a7_potentially_hit_a_robot` sets
     `(iy + BULLET_STRUCT_MAP_PTR + 1), 0` directly rather than routing
     through `Lb7ea_bullet_disappear` (both zero the same liveness byte —
     functionally equivalent termination).
- **Collision-detection footprint/order** (`Lb7a7_potentially_hit_a_robot`'s
  preceding scan in `Lb724_bullet_update_internal`/`Lb77c_continue`): the
  code checks a specific ordered sequence of up to **9 map cells** around
  the bullet's new position (a 3x3 neighborhood, not a flat "2x2" or generic
  "eight-cell" shape as loosely described in earlier drafts) via
  `bit 6, (hl)` tests, each guarded by a map-bounds check on the pointer's
  high byte (`cp #dd` / `cp #fd`) that can skip a whole row's worth of
  checks near the map edges: row "y-1" (3 cells, x-1/x/x+1, skippable via
  the `cp #dd`/`jr c, Lb77c_continue` bounds check), row "y" (3 cells,
  x-1/x/x+1, always checked, labeled `Lb77c_continue`), and row "y+1" (3
  cells, x-1/x/x+1, skippable via the `cp #fd`/`jr nc, Lb7a3_no_robot_collision`
  bounds check). The scan short-circuits (`jr nz, Lb7a7_potentially_hit_a_robot`)
  on the first cell found with bit 6 set, so cells are checked in a fixed
  left-to-right, row-by-row order and the check terminates at the first hit,
  not an exhaustive scan.

Still open (not resolved by this research pass — needs an explicit engine
policy decision, see "Recommended engine policy surface" below):

- exact Y-axis coordinate-doubling status (whether Y also uses a 2-raw-units-
  per-logical-cell convention, or whether the ±2 Y step is a different
  effective fraction of a logical cell given `MAP_WIDTH = 16`'s much smaller
  scale than `MAP_LENGTH = 512`);
- interaction with buildings/static objects as distinct from generic map
  altitude (the traced code folds robots, decorations, and terrain into one
  altitude figure via `Lb08a_get_map_altitude`'s callers; it does not
  distinguish "collided with a building" from "collided with terrain" as a
  separate rule);
- exact real-world distance meaning of the raw disassembly range constants
  --- see the "raw range vs. locked mile-derived range" note immediately
  below, which is explicitly *not* resolved by this pass and must not be
  silently reconciled.

### Raw disassembly range figures vs. this project's locked mile-derived ranges — explicitly NOT reconciled

The disassembly's `BULLET_STRUCT_RANGE` is a raw per-cycle decrement counter,
not a mile or tile distance stat: `WEAPON_RANGE_DEFAULT: equ 5` (cannon/
phaser) and `WEAPON_RANGE_MISSILES: equ 7` (missiles), each incremented by 1
for electronics (`Lb6e1_not_missiles`/`Lb6e8_not_electronics`: 5→6, 7→8).
Since one raw-X step is +2 raw units == 1 logical cell (per the
coordinate-doubling convention noted above), a cannon/phaser bullet that
travels in a straight line before its range counter reaches zero covers on
the order of 5 (or 6 with electronics) logical cells, and a missile 7 (or 8)
cells, in the executable's own actual runtime behavior.

This project's already-locked `rules.py` defaults (`cannon_range_cells =
miles_to_cells(10) = 20`, `missile_range_cells = miles_to_cells(14) = 28`,
`phaser_range_cells = miles_to_cells(10) = 20`,
`electronics_range_bonus_cells = miles_to_cells(3) = 6`) instead derive from
the game's *instruction manual's* stated mile ranges (§3's "1 mile = 2
tiles" resolution), not from the `BULLET_STRUCT_RANGE` runtime counter. This
research pass finds these to be **two distinct, non-interchangeable
numbers** describing weapon range in the source material: the manual's
advertised mile figures (10/14/10 miles, this project's canonical/locked
scale) versus the executable's actual raw per-shot travel-cycle limit
(5/7 raw units, a ~4x smaller figure — coincidentally exactly 4x for both
cannon (5*4=20) and missile (7*4=28), though this pass did not find
disassembly evidence that a literal "×4" conversion constant exists in the
executable; it may be coincidental or may reflect an unresolved scale
difference between documentation and implementation that this project has
no basis to adjudicate).

**Disposition**: this project's locked mile-derived cell ranges
(20/28/20/+6) take precedence architecturally and are NOT to be changed by
this note. This is a documentation-only flag: a future research pass with
additional evidence (e.g. confirming the exact pixel/tile scale the
executable's map coordinates represent) could revisit whether the raw
`BULLET_STRUCT_RANGE` figures should instead inform a *different*,
non-canonical field (e.g. an independent projectile-travel-distance limit
separate from weapon nominal range) rather than being reconciled against
the locked range constants.

### Recommended engine policy surface (for Task 4, non-canonical/configurable)

For everything this pass could not resolve to a single canonical value,
Task 4 should add these as explicit, documented-as-non-canonical
`EngineRules` fields rather than inventing silent behavior:

- `projectile_y_axis_doubled: bool` (or equivalent) — whether the engine's Y
  axis uses the same 2-raw-units-per-cell convention as X for projectile
  step size, or a separate divisor. Purpose: resolves the "exact Y-axis
  coordinate-doubling status" open item above. Marked non-canonical because
  this pass found no disassembly evidence isolating Y's scale independent
  of `MAP_WIDTH = 16`'s much smaller extent than `MAP_LENGTH = 512`.
- `projectile_collision_neighborhood`: a named/enum policy (already
  effectively fixed by evidence as the ordered 3x3, first-hit-wins scan
  documented above under "Collision-detection footprint/order") — Task 4
  should implement this as the default, but expose it as a policy point
  only if a later milestone needs to vary it (e.g. for nuclear/area weapons
  distinct from normal projectiles); it is not marked non-canonical since
  the 3x3 first-hit scan is directly evidenced.
- `projectile_max_range_cells` (already added as a placeholder in `rules.py`
  by issue #70/M6.1, currently defaulted to the longest locked weapon range)
  — this research pass did NOT find grounds to change that placeholder's
  value or its "explicitly unverified" status; it remains Task 4's to
  resolve or to keep as documented policy, per the discrepancy noted above
  between the raw disassembly range-in-cycles figures and the locked
  mile-derived range figures.
- A distinct `building_collision_altitude_bonus`-style field (name TBD by
  Task 4) is NOT recommended: this pass found no disassembly evidence that
  buildings are collided with as a category separate from generic map
  altitude (`Lb08a_get_map_altitude`/`Lb5d6_map_altitude_2x2` treat terrain,
  robots, and decorations uniformly as "altitude at this cell"). Task 4
  should not invent a building-specific collision rule; the generic
  altitude-collision policy already covers buildings via their map
  altitude/decoration values (see `Lb0c1_decoration_altitudes` for the
  concrete per-decoration altitude table, e.g. warbase "H" pad = `#0f`).

## 9. Damage, accuracy, and electronics effects — RESOLVED (starting-strength scale caveat noted)

Locked normal-weapon damage:

`base_damage = (60 - (robot_height + ground_height)) / 4`

Default multipliers:

- cannon = 2
- missile = 3
- phaser = 4

The damage formula is isolated behind one engine function; multipliers are centralized game-rule configuration.

Resolved by issue #74 research (`santiontanon/netherearth-disassembly`,
`netherearth-annotated.asm`), each cited to its exact label:

- **Integer truncation/rounding path**: confirmed. `Lb7a7_potentially_hit_a_robot`
  computes `ld a, 60` / `sub (iy + ROBOT_STRUCT_HEIGHT)` /
  `sub (iy + ROBOT_STRUCT_ALTITUDE)` / `srl a` / `srl a` / `ld d, a`. Two
  consecutive `srl` (shift-right-logical) instructions are an unsigned,
  bit-level divide-by-4 — since `a` is non-negative at this point (robot
  height + ground height is bounded well under 60 per the header comment's
  worked min/max, 13–38), this is exactly integer floor-division by 4 with
  no separate rounding step: `base = (60 - robot_height - ground_height) >> 2`.
  This confirms the locked formula's `/ 4` should be read as integer
  floor-division, not real-division-then-round.
- **Order of multiplier application**: confirmed via the accumulation loop
  immediately following, `Lb7c8_damage_calculation_loop`: `ld b, e` (`e` was
  loaded from `BULLET_STRUCT_TYPE`, 1 = cannon, 2 = missile, 3 = phaser) then
  `add a, d` / `djnz Lb7c8_damage_calculation_loop`. Since `a` already equals
  `d` (the base) going into the loop, and the loop runs `b` times adding `d`
  again each time, the total is `base + base * b = base * (b + 1)`: cannon
  (`b=1`) → `base * 2`, missile (`b=2`) → `base * 3`, phaser (`b=3`) →
  `base * 4`. This is repeated-addition, not a multiply instruction, but is
  arithmetically identical to `damage = base * multiplier` with no further
  rounding — directly confirming the already-locked multipliers 2/3/4 as
  evidence-backed, not just plausible. The disassembly's own module-header
  comment cross-checks this independently: "phasers against the weakest
  robot (at ground level) deal: ((60 - 13)/4)*4 = 44 damage" — this pass
  verified that worked example against the actual code path rather than
  trusting the prose alone.
- **`ROBOT_STRUCT_HEIGHT` vs. `ROBOT_STRUCT_ALTITUDE` semantics — naming
  caution**: both operands were traced to their write sites, and the names
  are misleading relative to what they hold. `ROBOT_STRUCT_HEIGHT` (struct
  offset 9) is a fixed, chassis/piece-derived value computed once at robot
  construction (`Lb904_robot_height_loop`, summing `Ld7b4_piece_heights` for
  each equipped piece) — this is the robot's own physical height, matching
  this project's `robot_height`. `ROBOT_STRUCT_ALTITUDE` (struct offset 13),
  despite its name suggesting the robot's own elevation/jump-height, is
  actually written by `Lb495`'s `call Lb5d6_map_altitude_2x2` /
  `ld (iy + ROBOT_STRUCT_ALTITUDE), a` with the comment "update the altitude
  of the robot based on the terrain underneath" — i.e. it is the **terrain
  elevation at the robot's current map position**, refreshed every time the
  robot moves. This confirms it maps to this project's `ground_height`
  operand, not a separate "robot's own altitude off the ground" concept —
  the disassembly's field name is a false cognate here and should not be
  read literally when cross-referencing future disassembly passes.
- **Strength representation and starting value**: confirmed. Both robot
  spawn sites — `ld (iy + ROBOT_STRUCT_STRENGTH), 100` at line ~368
  (warbase-exit reset) and again at line ~3306 (`Lb890`-area robot
  construction) — set strength to exactly `100` (a single signed byte field,
  struct offset 12). No other initial value was found anywhere in the file.
- **Damage application / destruction threshold**: confirmed via
  `Lb7a7_potentially_hit_a_robot`'s tail: `ld a, (iy + ROBOT_STRUCT_STRENGTH)`
  / `sub b` (b = computed damage) / `jr z, Lb7d7_robot_destroyed` (exact
  zero → destroyed) / `jp p, Lb7db_robot_hit` (still positive → survives,
  new strength stored as-is). If neither branch is taken (result negative),
  execution falls through into `Lb7d7_robot_destroyed` directly — so **any
  result ≤ 0 leads to destruction**, matching the brief's "<= 0" hypothesis
  exactly; there is no separate "overkill" branch. `Lb7d7_robot_destroyed`
  does **not** store the actual computed negative remainder — it discards it
  and writes a fixed sentinel: `ld a, -4` / `ld (iy + ROBOT_STRUCT_STRENGTH), a`.
  This `-4` is a specific countdown/blink value, not "any negative number":
  `Lb0fa_robot_update`'s per-cycle update increments a negative strength
  toward zero (`inc (iy + ROBOT_STRUCT_STRENGTH)`) and toggles a "blink"
  bit each cycle (`and 1` / `res 6, (hl)` or `set 6, (hl)`) until strength
  reaches exactly 0, at which point `Lb116_robot_destroyed` performs the
  actual removal from the map. So destruction is a two-stage process: (1)
  strength reaches ≤0 → set to sentinel -4 and begin a fixed 4-cycle visible
  "blink" grace period, (2) strength counts back up to exactly 0 → robot is
  actually removed. The gate at the top of `Lb7a7_potentially_hit_a_robot`
  (`ld a, (iy + ROBOT_STRUCT_STRENGTH)` / `dec a` / `jp m,
  Lb7de_collision_handled`) treats any strength ≤0 (including mid-blink
  sentinel values) as "already destroyed," preventing further damage
  processing on a robot that is already in its blink-out grace period.
- **Hit/miss accuracy roll**: confirmed absent. The entire
  `Lb7a7_potentially_hit_a_robot` routine (from its label through
  `Lb7de_collision_handled`'s `ret`) was read line-by-line and contains no
  call to `Ld358_random` or any other RNG routine. A projectile that
  geometrically collides with a robot (per the collision-footprint scan
  already documented in §8) always deals damage; there is no separate
  probability gate anywhere in this code path. This confirms the brief's
  hypothesis.
- **Component-level damage**: confirmed absent. The full `ROBOT_STRUCT_*`
  field list (struct size 16 bytes: `MAP_PTR`, `X`, `Y`,
  `DESIRED_MOVE_DIRECTION`, `NUMBER_OF_STEPS_TO_KEEP_WALKING`, `PIECES`,
  `DIRECTION`, `HEIGHT`, `CONTROL`, `ORDERS`, `STRENGTH`, `ALTITUDE`,
  `ORDERS_ARGUMENT`, `CYCLES_TO_NEXT_UPDATE`) has exactly one strength/health
  field (`ROBOT_STRUCT_STRENGTH`, offset 12) and no per-piece or per-weapon
  health/durability field. Damage is always applied to this single aggregate
  value; there is no evidence anywhere in the traced struct layout or the
  damage routine of individual component/weapon-slot damage.
- **Electronics' damage-related effect**: confirmed to be range-only, with
  no separate effect in the damage-calculation path. Cross-referencing §8's
  already-verified `Lb6d6_weapon_fire` finding (`bit 7, (ix + ROBOT_STRUCT_PIECES)`
  / `inc c` = +1 to `BULLET_STRUCT_RANGE` only), this pass additionally
  searched `Lb7a7_potentially_hit_a_robot`'s entire damage-calculation branch
  specifically for any second electronics check (e.g. a `bit 7,
  (iy + ROBOT_STRUCT_PIECES)` on the *defending* robot) and found none — the
  damage formula reads only `ROBOT_STRUCT_HEIGHT`, `ROBOT_STRUCT_ALTITUDE`,
  and the bullet type; a defending robot's own electronics piece has no
  bearing on damage taken. No damage-resistance modifier exists.

Scale-reconciliation note (starting strength, mirroring §8's mile/cell
caution): the disassembly's `100` is a raw signed-byte counter consumed by
damage values on the same raw scale as the `(60 - h - a) / 4 * multiplier`
formula traced above (e.g. a phaser hit on the weakest robot at ground level
deals 44 raw points per the header comment's worked example, meaning as few
as 2–3 solid phaser hits can destroy a fresh robot at 100 strength). Because
this project's `robot_height`/`ground_height` inputs to the locked formula
are expected to be on this same raw disassembly scale (per the already-locked
formula text `(60 - (robot_height + ground_height)) / 4`, which reuses the
disassembly's literal constant `60` unmodified), `100` is very likely
directly portable as the starting-strength default with **no scale
conversion needed** — unlike the mile-vs-raw-unit range discrepancy in §8,
this pass found no evidence of two different unit systems in play for
strength/damage (both sides of the equation — the `60` constant and the
`100` strength constant — are the same disassembly-native raw scale). This
is recorded as a resolved-with-caveat item, not silently adopted: Task 6
should treat `100` as evidence-backed but confirm the same conclusion holds
once `robot_height`/`ground_height` are wired to real per-chassis/terrain
data (i.e. that those inputs are populated on the disassembly's raw 13–38
height scale and not on some other project-specific unit), since this
research pass only traced the arithmetic/constants, not the numeric ranges
that will flow through them at runtime.

Follow-up note (M6 final review, engine-wiring observation, not new
disassembly research): `combat.py`'s ``ground_height_at`` -- the function
that supplies this formula's ``ground_height`` operand -- returns the
tallest static ``structures.Component`` height at a robot's cell, but
`WorldMap.occupancy()` marks every structure cell occupied and
`movement.py`'s ``validate_robot_move`` rejects any move into an occupied
cell. A live robot can therefore never legally stand on a structure cell in
this engine, which means ``ground_height_at`` always returns ``0`` for any
robot reached through normal movement, and the locked formula
``(60 - (robot_height + ground_height)) // 4`` collapses in practice to
``(60 - robot_height) // 4`` for the entire engine as currently wired. The
function itself is correctly implemented per this section's evidence; this
is an open question about whether that is the intended end state, not a bug
report. Left for a future milestone/owner decision: is ``ground_height``
meant to ever be non-zero given this engine's occupancy model (e.g. via a
future terrain-elevation model decoupled from movement-blocking structure
occupancy), or should the damage formula itself be revisited to drop the
now-always-zero term? Not resolved here -- do not silently pick an answer.

Still open (not resolved by this research pass):

- none of the items originally listed above remain open; all resolved as
  documented in the "Resolved by issue #74 research" block, subject to the
  scale-reconciliation caveat on starting strength noted above.
- see the follow-up note immediately above regarding ``ground_height_at``'s
  structural always-zero behavior under this engine's current occupancy
  model.

## 10. Resource spending rules — RESOLVED

Preserve the original Spectrum construction economy, with all tunable numeric values in centralized game configuration.

Canonical defaults from the disassembly:

- starting general resources: 20
- bipod cost: 3
- tracks cost: 5
- anti-grav cost: 10
- cannon cost: 2
- missile cost: 4
- phaser cost: 4
- nuclear cost: 20
- electronics cost: 3

Resource categories:

- general
- chassis
- electronics
- cannon
- missile
- phaser
- nuclear

Spending behavior:

- spend the relevant type-specific resource pool first;
- general resources pay only the shortfall;
- reject the selection if specific + general resources cannot cover the cost;
- construction editing uses a temporary resource buffer;
- deselecting a component reverses the original mixed specific/general spending semantics;
- actual player resources are committed atomically only when `Start Robot` succeeds;
- leaving/canceling unlaunched construction consumes no permanent resources.

## 11. Simultaneous destination-cell claims — RESOLVED

- A destination cell is reserved when a robot move is accepted/started.
- A reserved destination is unavailable to other robots until completion/cancellation.
- Same-tick contention is resolved randomly using the match-local seeded deterministic RNG.
- Two contenders are a 50/50 coin flip; more contenders are chosen uniformly.
- Losing contenders remain out of the destination and may retry/replan.

This is random to players but replay-safe for identical seed + commands + state.

## 12. Commander-versus-commander collision — RESOLVED

- Commanders physically collide with each other.
- They may share X/Y only if their vertical collision ranges do not overlap.
- If vertical ranges overlap, they block horizontal and vertical movement, including descent.
- Commanders remain indestructible, untargetable, and immune to damage.

## 13. Commander vertical limits and speed — RESOLVED

Spectrum-compatible defaults at the locked 20 Hz engine rate:

- minimum altitude: 0
- maximum altitude: 48
- vertical update cadence: every 4 simulation ticks (5 updates/sec)
- ascent step: +2
- descent/gravity step: -1

Ascent/descent are intentionally asymmetric. Approximate unobstructed times are 4.8 s from 0→48 and 9.6 s from 48→0.

Configuration keys/defaults:

- `commander_min_altitude = 0`
- `commander_max_altitude = 48`
- `commander_vertical_update_ticks = 4`
- `commander_ascent_step = 2`
- `commander_descent_step = 1`

Horizontal and vertical movement may occur simultaneously. Automatic elevation after exiting a robot/war base uses the same +2 elevation semantics.

## 14. Landing on an enemy robot — RESOLVED

- Enemy robots are physical collision surfaces for the commander.
- Descending stops at the top of the enemy robot stack.
- The commander may rest there while collision geometry permits it.
- No docking, control transfer, or contact damage occurs.
- Docking/control remains restricted to friendly robots.

## 15. Static-object composition and footprints — RESOLVED

- Static geometry is explicit occupied map cells/components, not one universal building rectangle.
- War bases and factories are separate semantic entity types with their own canonical compositions.
- War-base metadata includes heli-pad, exit, capture zone, ownership/resource behavior.
- Factory metadata includes production type and capture zone.
- Physical height may vary by component/cell.
- Interaction zones are explicit semantic metadata.
- Exact original layouts are reconstructed from Spectrum evidence during map ingestion; uncertain cells/heights must not be guessed.
- Robot footprint is a separate robot-model concern.

## 16. Disconnect and reconnect rules — RESOLVED

- Match pauses immediately when either player disconnects.
- Simulation ticks and gameplay timers stop while paused.
- Default reconnect grace period: 60 seconds, configurable at match/server runtime level.
- Reconnect receives the current authoritative snapshot.
- Match resumes only when both players are connected.
- Grace expiry causes the disconnected player to forfeit when an opponent remains eligible to win.
- If both are disconnected, each has an independent grace deadline; if both expire without either returning, end as abandoned/no-contest rather than inventing a gameplay winner.
- No manual pause in v1.
- Reconnect/deadline state belongs to the runtime layer and must not mutate deterministic engine state while paused.

## 17. Commander starting positions — RESOLVED (CR001, owner decision 2026-09-21)

**Resolution:** Player 1 = extreme-left war-base anchor + (−5, +1) → (17, 10), from the Spectrum code. Player 2 = extreme-right war-base anchor + (+5, +1) → (499, 9): the owner confirmed this mirror as a locked PvP adaptation. Both are overlay data.


Neither spec nor map data declared where the two commanders begin. Evidence
(tier 2, `netherearth-annotated.asm` `La600_start`):

```
ld hl, 17 ; ld (Lfd0e_player_x), hl   ; set player start x
ld a, 10  ; ld (Lfd0d_player_y), a    ; set player start y
xor a     ; ld (Lfd10_player_altitude), a
```

so Player 1's ship starts at cell (17, 10), altitude 0, i.e. offset (−5, +1)
from war base 0's capture anchor (22, 9), just outside the base on the side
facing away from the map interior. The original is single-player, so there is
no evidence for Player 2.

Locked data (`map_overlay.default_pvp_overlay`, pinned by
`engine/tests/test_map_overlay.py`):

- `p1_commander` = extreme-left war-base anchor + (−5, +1) → (17, 10) on the original map (evidence-backed);
- `p2_commander` = extreme-right war-base anchor + (+5, +1) → (499, 9) (mirror of Player 1; locked PvP adaptation confirmed by the owner, not Spectrum evidence).

Both commanders start at altitude 0. Spawns are overlay data, not engine
rules, and live in one place.

## 18. War-base heli-pad location and landing height — RESOLVED (CR001, owner decision 2026-09-21)

**Resolution:** option 2. The pad is on the roof, at (anchor.x, anchor.y − 4). The M3 landing rule becomes "altitude equals the pad cell's component height" (15 on the original war base). The robot exits at the anchor cell (unchanged).


Evidence (tier 2): `Lbb86_assign_warbase_to_player` places the war-base "H"
decoration at (anchor.x, anchor.y − 4), and the game loop enters construction
only when the ship is over that decoration at altitude exactly 15 (`cp 15`),
i.e. on the roof of the 15-high war-base block. The robot then exits at
(pad.x, pad.y + 4) = the anchor cell (`Lcb52_construction_screen_start_robot`,
"robot starts 4 positions off the player in the y axis"), which confirms the
current `*-exit` interaction points.

Before CR001, the M3 landing rule (`heli_pad.py`: altitude ==
`commander_min_altitude` on a heli-pad footprint cell) could not express a
roof-top pad, and the M2 data kept the `*-helipad` points as ground-level
placeholders at the anchor cell. Options that were put to the owner:

1. keep the ground-level pad at the anchor cell (the pre-CR001 data; playable, but deviates from the original);
2. move the pad to (anchor.x, anchor.y − 4) and extend the M3 landing rule to "altitude equals the pad cell's component height" (fidelity-correct; M3 rule + M2 data change).

The owner chose option 2 (see Resolution above). CR001.6 (#153) implemented
it; the M9 acceptance script and the live two-client check land on the roof pad.

## 19. Autonomous use of the nuclear weapon — RESOLVED (CR001, owner decision 2026-09-21)

**Resolution: match the original.** `Lb99f_fire_nuclear_bomb` is reached from exactly two places:

1. manual fire from the player's direct-control menu (line ~932);
2. `Lb2e8_target_directions_calculated` (line ~1984): a robot whose order is Destroy enemy factories or Destroy enemy war bases, standing exactly on its target building's coordinates, which are the same coordinates Capture orders navigate to.

Stop & Defend, Destroy robots, Advance, Retreat, and Capture never detonate. `Labc8_capture_or_destroy_order_selected` also turns a Destroy factory/war-base order into Stop & Defend when the robot has no nuclear weapon.

Engine consequence: nuclear must be removed from the generic autonomous weapon walk (`autonomous_combat.py`). Detonation becomes an order-completion effect of Search & Destroy against a structure.


Research history. Found by the M9.4 scripted match. The spec defined what a
detonation does (`functional-spec.md` §17.3) but not when an autonomous order
uses it. Before CR001.1 (#148), the M5/M6 engine policy composed two rules:

- Stop & Defend targets the nearest hostile robot at **any** distance (`orders._defensive_intent`);
- autonomous fire walks weapons in canonical order (cannon, missile, phaser, nuclear) and nuclear has **no range gate** (`autonomous_combat.py`).

Consequence: a nuclear carrier on Stop & Defend detonates on the first tick
any enemy robot exists anywhere on the map, or whenever its normal weapon is
out of range or its projectile channel is busy. Every completed Advance or
Retreat and every fallback order becomes Stop & Defend, so this is reachable
in a normal match and destroys the carrier plus everything within 16 cells.

Options that were put to the owner:

1. autonomous orders never detonate; nuclear is a direct-control decision only;
2. autonomous detonation only when the target (robot or structure) is within the nuclear radius;
3. keep the current policy (not recommended: effectively a self-destruct).

The owner chose to match the original (see Resolution above). CR001.1 (#148)
implemented it. The M9 acceptance script no longer tolerates an autonomous
detonation: it asserts the striker is alive before its direct nuclear fire, and
the fixture replay asserts that the only robot losses happen in that one blast.

## 20. Nuclear blast shape — RESOLVED (CR001, owner decision 2026-09-21)

Found while researching §19. The earlier locked "8 miles = 16 cells, destroys every eligible robot/factory/war base in radius" rule does not match the code (`Lb99f_fire_nuclear_bomb`):

- **Buildings:** war bases are checked first, then factories, in index order, skipping destroyed ones. dy = |robot.y + 1 − b.y| (war bases add 4 to robot.y first) and dx = |robot.x − b.x|. A war base is in range when dx < 7, dy < 7, and dx + dy < 10 (`ld de, #070a`). A factory is in range when dx < 5, dy < 5, and dx + dy < 7 (`ld de, #0507`). The **first** match is destroyed and the scan stops ("A nuclear bomb will only destroy at most one building"). Ownership is not checked.
- **Robots:** a 9×9 window around the carrier (`ld de, -(4*MAP_LENGTH + 4)`, `ld bc, #0909`), with corner rows trimmed to widths 5, 7, 9, 9, 9, 9, 9, 7, 5 and clipped at the map edges. Every robot in it is destroyed, of either side.
- **Carrier:** destroyed.
- **Scenery:** destructible elements (types 17–20) in the window become debris. This is a visual effect and out of scope for v1.

**Owner decision:** adopt the code's shapes. The shape parameters are `EngineRules` data.

## Remaining research

1. **Combat detail**: exact accuracy, rounding, strength, and electronics modifiers (#9).
2. **Autonomous fire-decision scan**: the 8/10/12-cell scan distances (§8), not yet decided.
3. **Nuclear blast vs. scenery (owner decision needed; found in CR002.1)**: in the nuclear blast's robot-destruction window (`Lba02_look_for_robots_in_range_of_nuclear_bomb`, the 9×9 window of §20), `Lba44_robots_handled` replaces every scenery element of type 17–20 whose bottom-left cell is in the window with random debris (type 6 or 7, rough, height 3) via `Lbd91_add_element_to_map`. Fences (type 21) are kept. The engine map is static and a blast leaves blockers in place. Decide whether to model this, which needs mutable map state, and in which change request.
4. **2×2 collision areas (found in CR002.1)**: the original tests the ship (`Lb052_check_player_collision`, 2×2 for map pieces) and bullets (`Lb5d6_map_altitude_2x2` at the landing cell) against 2×2 areas of the map. The engine tests single cells. For the commander this belongs to CR002.4 (2×2 commander). For projectiles, `combat.py` documents the single-cell check as a policy choice. Now that the map has blockers, a bullet that passes right beside a high box is stopped in the original but not in the engine. Decide whether the projectile footprint should follow.

Items 1–2 are research items and items 3–4 need owner decisions. The two owner decisions found during CR001 (scenery blockers §4, first projectile move §8) were decided on 2026-09-21 and are implemented by CR002 (`_specs/milestones/cr002-spectrum-fidelity-ui.md`).

## Resolution process

Use this fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

When one of the remaining items is verified, update the functional/technical specs and this file in the same change.