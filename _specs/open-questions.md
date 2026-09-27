# Nether Earth Clone — Open Questions

This file tracks gameplay/implementation details that are still unresolved after the current design and ZX Spectrum disassembly review. Resolved items remain listed so their locked outcome is easy to find.

After CR002, only the two research items under "Remaining research" are open. The deviations from the original that the owner chose to keep are listed under "Documented deviations".

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

Tests: `engine/tests/test_scenery_blockers.py`. Two related details from the same reading are not implemented here. The nuclear blast destroys scenery: resolved by CR002.18, see "Nuclear blast vs. scenery" below. The original checks collisions over 2×2 areas: resolved by §21.

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

**Detour fallback (owner request 2026-09-25, adopted from the disassembly).**
"Blocked" above means "has no legal step at all", not "its preferred step is
refused". The original's dumb robot is erratic, never immobile:
`Lb222_choose_direction_to_move` intersects the directions that point at the
target with the directions it may actually move in
(`Lb513_get_robot_movement_possibilities`) and picks one at random, and when
that intersection is empty `Lb326`/`Lb33e_pick_direction_at_random` pick at
random among *every* possible direction — sideways and backwards included. It
then commits to that direction for `rand & 3 + 3` = 3–6 game cycles
(`ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING`, `Lb1f5`) before reconsidering,
which is what carries it along an obstacle instead of oscillating against it.

The engine implements this as: the primary-axis step; then the direction the
robot is already walking (`robot.facing`), the *momentum* rule; then the two
steps perpendicular to the primary in an order drawn once per *window*
(`EngineRules.dumb_wander_commit_ticks`, default 16 ticks = 4 game cycles);
then whatever is still legal. The draw is derived from
`(match seed, tick // commit window, entity id)`, so it is replay-safe and
per-robot without storing a counter on the robot; the policy stays pure and
stateless. `BLOCKED` now means every cardinal step is illegal.

**Momentum (owner report 2026-09-27: "robots keep getting stuck in loops").**
The window draw alone was not enough. A robot would step aside, find the
primary step legal for one cell, be pulled straight back behind the obstacle,
and pace those two cells for ever — the corner trap. Continuing in the
direction it already faces is the memoryless stand-in for the Spectrum's
"keep walking" counter, and it is free: a step in the faced direction needs no
turn. Measured over four obstacle fixtures × 8 seeds, it took arrivals from
27/32 to 32/32 and roughly halved the time; on a comb of staggered walls it
took 0/8 to 8/8.

The owner's proposed remedy — more randomness over all four directions — was
tested and rejected on the evidence: drawing uniformly over all four
directions (25/32), and spending whole windows roaming at random (1-in-4:
29/32 and much slower), both did worse than the momentum rule, as did a
faithful port of the Spectrum's own 3–6-cycle random-direction counter (9/32).
At 24 ticks per step a robot cannot afford a random walk; coherence, not
entropy, is what gets it round an obstacle. Deep concave pockets and diagonal
staircases remain unsolvable for this policy by design (§5's "may become
blocked even if a longer route exists"); electronics clears all of them.

What electronics buys is unchanged in kind and still substantial: a planned
shortest route versus a greedy step with a random detour that can walk into a
pocket and back out of it. The engine's own M5 scenario shows the difference —
both robots clear the wall, the electronic one much sooner.

## 6. War-base capture mechanics — RESOLVED

- Enemy robots can capture war bases.
- War-base capture uses the same continuous-occupation rule as factory capture.
- Neutral structures use that same rule too: a neutral factory is **not** acquired instantly (owner decision, 2026-09-23 — see `functional-spec.md` §9; this supersedes the earlier "first qualifying robot under the verified original behavior" resolution, which the engine implemented until that date).
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

  Residual deviation: the engine tracks no update phase for a stationary robot that has not fired since it last moved, so it is treated as being at an update on every tick and fires as soon as it has a shot; in the Spectrum it would wait up to one period for its counter. Once it has fired, its updates follow the period exactly. Since CR002.3 (#170) the period uses the highest piece under the robot's 2×2 body, as the Spectrum does (`Lb5d6_map_altitude_2x2`, §21).

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

**Resolved (CR002.21, #203, owner decision 2026-09-21: model terrain piece heights map-wide).** ``ground_height_at`` is now the 2×2 surface height under the robot (`collision.unit_surface_height`, `Lb5d6_map_altitude_2x2`), terrain pieces included, so it is 2–3 on rough, 6 on mountains and 3 on debris. The formula keeps its ground term. See "Terrain piece heights" below.

Still open (not resolved by this research pass):

- none of the items originally listed above remain open; all resolved as
  documented in the "Resolved by issue #74 research" block, subject to the
  scale-reconciliation caveat on starting strength noted above.
- the follow-up note above on ``ground_height_at`` always being zero is
  resolved by CR002.21 (#203).
- the scale-reconciliation caveat is settled by CR003.3 (#218): robot
  heights are now the Spectrum's raw 13–38 piece-height sums; see "Robot
  piece heights" below.

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

Defaults at the locked 20 Hz engine rate (Spectrum-compatible except the descent step):

- minimum altitude: 0
- maximum altitude: 48
- vertical update cadence: every 4 simulation ticks (5 updates/sec)
- ascent step: +2
- descent/gravity step: -2 (owner deviation, see below)

**Descent step (CR003.1 #216, owner decision 2026-09-22).** The Spectrum's gravity is −1 per game cycle (`Lafc3_gravity`; `Lafb5_elevate` adds 2), so the original is asymmetric: 4.8 s from 0→48 and 9.6 s from 48→0. Playtesting found the descent too slow, and the owner changed it to −2 per vertical update, cadence unchanged, so 48→0 takes 24 updates (4.8 s), the same as the ascent. This is a deliberate deviation, not a fidelity fix. Gravity still stops on the surface under the ship: when a surface is at an odd altitude, the last step is shortened to land exactly on it (`commander_movement._gravity_landing_altitude`), so the ship never skips past or hovers above it, and docking on an odd-height friendly robot top still triggers.

Configuration keys/defaults:

- `commander_min_altitude = 0`
- `commander_max_altitude = 48`
- `commander_vertical_update_ticks = 4`
- `commander_ascent_step = 2`
- `commander_descent_step = 2`

Horizontal and vertical movement may occur simultaneously.

**Exit lift (CR002.12/CR002.13 #179/#180, CR002.24 #207; owner decisions 2026-09-21).** Leaving the construction screen (EXIT MENU or START ROBOT) and leaving a docked robot give the same lift: the commander ascends `commander_ascent_step` (+2) on each of the next `commander_exit_elevate_updates` (5) vertical updates, whatever its rise intent, then normal rise/gravity resumes, for a peak of 10 above the exit altitude. Evidence (`santiontanon/netherearth-disassembly`): `Lcb8e_construction_screen_exit` and the robot HUD's EXIT option (`#a7fd`–`#a80f`, falling through to `La812_exit_robot`) both set `Lfd30_player_elevate_timer` to 5; `Lafa2_player_ship_keyboard_control_altitude` climbs +2 per update while it runs. The original's `Laf11` also decrements the timer while up/down is pressed; by owner decision that shortening is not modelled. The engine triggers the robot exit with held rise intent (`docking.apply_undock`), which sets `Commander.elevate_updates_remaining` without moving the commander; the ascent runs on the following cadence ticks (`commander_movement.apply_vertical_physics`). While the lift runs, auto-dock and heli-pad landing are not checked (`docking.attempt_auto_dock`, `engine.step` Step 7): in `La69a` the ship's altitude update precedes the dock/landing tests, so the ship is already above the robot top or pad when they run. After the lift, a commander that falls back onto the same friendly robot's anchor docks again, as in `La69a`. The rule was `commander_construction_exit_elevate_updates` before CR002.24 (the rename changes `rules_content_hash`; `RULES_VERSION` is bumped by CR002.16). Tests: `engine/tests/test_docking.py`, `test_engine_commander_integration.py`, `test_engine_construction_integration.py`.

## 14. Landing on an enemy robot — RESOLVED

- Enemy robots are physical collision surfaces for the commander.
- Descending stops at the top of the enemy robot stack. A robot's top is the terrain altitude under it plus its stack height (CR002.25; see "Robots on terrain height").
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
- **Scenery:** destructible elements (types 17–20) in the window become rough debris. This was first judged a visual effect; it is not, because debris changes movement. It is implemented by CR002.18 (#196); see "Nuclear blast vs. scenery" below.

**Owner decision:** adopt the code's shapes. The shape parameters are `EngineRules` data.

## 21. 2×2 robots, commander, projectiles and heli-pad — RESOLVED (CR002.3 #170, CR002.4 #171, owner decisions 2026-09-21)

**Owner decisions:** robots, the commander and the heli-pad are 2×2, physically and visually; projectiles and the commander follow the original's 2×2 map-area checks, so a bullet passing right beside a high box or fence stops.

Conventions, verified by a direct reading of `netherearth-annotated.asm` (`santiontanon/netherearth-disassembly`). The map buffer is 512-byte rows, so `inc hl` is x + 1 and `inc h; inc h` is y + 1.

- **Anchor.** A unit's `(x, y)` is the anchor of a 2×2 body covering `x..x+1` and `y−1..y`: the min-x/max-y cell, the same corner scenery elements use (`Lbd91_add_element_to_map`, CR002.1). `Lb5d6_map_altitude_2x2` reads `(x, y)`, `(x+1, y)`, `(x+1, y−1)`, `(x, y−1)`. The radar flips the same four cells (`Ld65a_flip_2x2_radar_area`, CR002.22). Snapshots keep the anchor; the frontend derives the body (the only schema addition is `exit_steps_remaining` for the launch walk-out below).
- **Map marks and overlap.** Robots are marked on the map only at their anchor (`bit 6`). Every robot/ship test scans the 3×3 window of anchors around a unit (`Lb052_check_player_collision`, the bullet scan in `Lb724_bullet_update_internal`, the robot checks in `Lb557`–`Lb5b1`). That is exactly "the two 2×2 bodies overlap". Engine: `occupancy.unit_footprint_cells`, `unit_footprints_overlap`.
- **Bounds.** The whole body stays on the map: anchor `0 ≤ x ≤ width−2`, `1 ≤ y ≤ height−1`. Robots: `Lb58f`/`Lb5b1` refuse a step whose rows leave the map. Ship: `Laf90` refuses y = 0 (`and #0f`). The ship's x limits `MIN_PLAYER_X`/`MAX_PLAYER_X` (14/501) are scrolling limits and are not modelled (fences close the long axis).
- **Robot movement.** `Lb513_get_robot_movement_possibilities` tests, per direction, the two cells a step newly enters against the chassis limit (`Lb5cd`: map piece < 8/12/15) and the three anchors whose body would newly overlap (`and e`). The robot already stands legally on the rest of its body, so the engine checks the whole destination body: on the map, every cell enterable by the chassis, no structure/blocker cell, no other robot, commander or reserved destination overlapping it. A robot never blocks its own next body; a commander docked on it never blocks it (`Lb471` keeps the ship on the robot's top).
- **Robot speed.** `Lb495` sets the robot altitude from `Lb5d6_map_altitude_2x2` after each step, and `Lb5f3` picks the speed row from it: the highest piece under the body. Engine: `movement.unit_move_terrain` (mountain > rough > ditch > normal; ditch and normal have the same default speed, §4). The same terrain sets the autonomous update period (CR002.19, `autonomous_combat.autonomous_update_period_ticks`).
- **Reservations.** A move reserves its whole destination body. Same-tick claims contend when their destination bodies overlap; contention groups are resolved in canonical (destination anchor, entity id) order with §11's seeded draw, and losers stay put.
- **Capture.** `Ladb7_building_loop` counts only while a robot mark (an anchor) is on the building's own cell, so a robot qualifies when its anchor is the capture cell; a body merely covering the cell does not.
- **Projectiles.** A bullet is a 2×2 body anchored at its `(x, y)`. `Lb724` moves it 2 cells and tests only the landing position: y on the map (`cp MAP_WIDTH`), then the highest map piece under the body (`Lb5d6`) ≥ bullet altitude stops it, then the 3×3 anchor scan (row y−1, then y, then y+1, each west to east) hits the first robot found. A 2-cell step with a 2-cell body leaves no untested gap. The engine keeps its §8 height gate (a robot whose top, terrain altitude plus stack height since CR002.25, is below the bullet altitude is flown over) and ignores ship and bullet marks: a documented deviation the owner kept (2026-09-21), see "Documented deviations" item 1.
- **Nuclear window.** `Lba02` scans map marks, so a robot is in the §20 window when its anchor is.
- **War-base exit.** The new robot's anchor is the exit cell (anchor.x, anchor.y), i.e. (pad.x, pad.y + 4) (`Lcb52_construction_screen_start_robot`, §18); its body stands in the base's doorway. `La6c8` enters construction only when no robot anchor that would overlap that body is marked; the engine refuses the launch (`EXIT_BLOCKED`) when the body is off the map or overlapped by a robot or a reserved destination.
- **Launch walk-out (owner decision 2026-09-21: match the original).** Right after `Lc849_robot_construction_if_possible`, `La6c8` sets the new robot's desired direction to 4 (down), `ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING` to 5 ("walk 5 steps after exiting the base, and stop"), orders to Stop & Defend and its next update to the next cycle; the construction screen already faces it down (`ROBOT_STRUCT_DIRECTION` 4), so no turn step is spent. "Down" is `inc b` (`Lb4d5`): y + 1, south, out of the doorway. `Lb154_robot_ai_update` → `Lb1e9_no_enemy_robots_in_sight` decrements the steps and moves one step in the desired direction per robot update while that step is possible; five steps are taken. A blocked step drops to `Lb1f5_move_in_a_new_direction`, and `Lb222` gives Stop & Defend no direction, so the robot stays and defends. An enemy in sight takes `Lb154`'s combat branch, which overwrites the steps. The player can land on the robot at any time (`La69a`); a landed robot is not updated, and leaving its menu (after giving orders or direct control) zeroes its steps, so orders are neither ignored nor queued: they take over at once. Engine (`robot_launch.py`, `orders.walk_out_request`, `autonomous_combat.settle_walk_outs`): a launched robot holds `StopAndDefend` with `Robot.exit_steps_remaining = rules.robot_launch_exit_steps` (5, in the snapshot). On each of its own updates (CR002.19 cadence; the first is the tick after launch) it requests one step south through the normal move batch, with normal legality and terrain timing, and the counter drops by one when the step starts. The walk-out ends (counter 0) when the step south is illegal or loses a same-tick contention, when the update fires instead, when a commander docks on the robot, or when any order is assigned to it. The engine's own Stop & Defend does not turn toward enemies as `Lb1d7` does; that stays part of the §5/§8 autonomous-behaviour simplification.
- **Commander collision.** `Lb052_check_player_collision` takes the highest map piece under the ship's 2×2 body and the tops of robots in the 3×3 anchor window. Engine: `collision.commander_horizontal_move_allowed`/`commander_vertical_move_allowed` test the static surface under the four body cells (components and, since CR002.21, terrain pieces: `collision.unit_surface_height`) and robots/commanders whose bodies overlap. Commander-vs-commander (§12) uses the same overlap; it is an engine extension (the original has one ship).
- **Docking.** `La69a` docks only when a friendly robot's mark is on the ship's own anchor cell and `altitude == robot height + robot altitude`, so the anchors must coincide. A ship resting on a robot whose body only partly overlaps is held up by it but does not dock.
- **Heli-pad.** The pad is the 2×2 "H" decoration area anchored at the §18 roof cell (anchor.x, anchor.y − 4); the map lists its four cells, all on the 15-high roof. `La6c8` starts construction only when the ship's anchor is the decoration's cell at altitude 15, so the ship's body lies exactly over the pad. Engine: every body cell is a pad cell and the altitude equals the highest component under the body (15).

Tests: `engine/tests/test_unit_footprint_2x2.py`, `test_launch_walk_out.py`, `test_combat_projectile.py` (2×2 hits, bullet beside a high box), `test_heli_pad.py`, `test_robot_launch.py`, `test_reservations.py`, `test_autonomous_fire_update.py`.

## 22. Construction screen exit and modality — RESOLVED (CR002.12 #179, CR002.13 #180, owner decisions 2026-09-21)

Evidence (`netherearth-annotated.asm`): `Lc85d_robot_construction` / `Lca0f_waiting_for_key_press_loop` is a modal loop that reads only menu input. Fire on column 0 is EXIT MENU (`Lcb8e_construction_screen_exit`, the buffer is never copied, so the build is discarded). Fire on column 1 is START ROBOT (`Lcb52_construction_screen_start_robot`): with no chassis or no weapon it returns to the loop; otherwise it copies the resource buffer to the player, places the robot and falls through to the exit. The exit starts the automatic lift (§13).

Engine: `CancelConstructionCommand` (EXIT MENU) and a successful `launch_robot` (START ROBOT) call `construction_session.exit_construction`, which drops the session and starts the lift; a rejected launch changes nothing and the screen stays open. Picking another chassis swaps it (CR002.20 #198, `Lca48`/`Lcac1`): the fitted chassis is refunded and removed first; if the new one is then unaffordable it is rejected and the robot has no chassis, as in the Spectrum.

**Modality (owner decision 2026-09-21):** the Spectrum pauses the whole game during construction (it is single-player). In PvP only the building player's commander is frozen (its moves and vertical physics are no-ops while its session is open); the match, the opponent and all robots keep running. This is a locked PvP adaptation.

## 23. Idle tick between commander cells — RESOLVED (CR003.10 #232, owner decision 2026-09-22; found in CR003.5 #220)

CR003.5 asks for held-key commander moves with no idle tick, checked as "each move's `started_tick` equals the previous move's end tick", with no engine or protocol change. The engine cannot produce that. `engine.step` applies the tick's commands (Step 1) before it resolves due horizontal transitions (Step 2). On the completion tick `started_tick + commander_horizontal_move_ticks` the commander still has its transition when the command is applied, so the command is rejected (`MOVE_IN_PROGRESS`), and the transition is only then cleared. The earliest next start is therefore the end tick + 1: a 5-tick cadence (4 moving + 1 idle), even with a command queued on every tick (verified in the engine and on a live backend).

The frontend fix (CR003.5) schedules sends so every cell starts on that earliest tick (no extra idle ticks from input timing), and the camera follows the interpolated position. Removing the remaining idle tick needs an owner decision, for example: accept the 5-tick cadence; or change the engine so a move can start on the tick the previous one completes (resolve completions before commands, or accept a move queued for the completion tick), which is a gameplay-timing change and a rules-version bump.

**Resolution (owner decision 2026-09-22, CR003.10 #232).** Fix it in the engine. `engine.step` now resolves due commander horizontal transitions (Step 2) before it applies the tick's commander commands (Step 1). A `commander_move` that arrives on the completion tick starts at once, so a held key moves 4 ticks per cell with no idle tick (starts at ticks 1, 5, 9, …). Robots are unchanged: `engine.step` already resolved due robot moves (Step 2b) before it started the tick's robot moves (Step 2c), so an order-driven or direct-control robot could already start its next move on the completion tick, and its Spectrum-calibrated cadence (`movement.move_duration_ticks`, the §8 update gating) does not change. The change only reorders commander steps, so the rules data and the M9 full-match fixture are unchanged; `RULES_VERSION` is bumped with the rest of CR003 by CR003.8 (#223). The CR003.5 scheduler lead is now 3 ticks, because the target tick is one tick earlier.

## Remaining research

1. **Combat detail**: exact accuracy, rounding, strength, and electronics modifiers (§9).
2. **Autonomous fire-decision scan**: the 8/10/12-cell scan distances (§8), not yet decided.

Items resolved during CR002, kept for their history:

- **2×2 collision areas (found in CR002.1)**: resolved by §21 (owner decision 2026-09-21: projectiles and the commander use the original's 2×2 map-area checks).
- **Terrain heights for the ship and damage (found in CR002.18)**: resolved by CR002.21 (#203); see "Terrain piece heights" below. This also closes the §9 follow-up note on `ground_height_at` always being 0.
- **Launched robots stuck in the doorway (found in CR002.3)**: resolved (owner decision 2026-09-21: match the original). New robots walk 5 steps south out of the war base on Stop & Defend; see §21 "Launch walk-out".
- **Nuclear blast vs. scenery (found in CR002.1)**: resolved by CR002.18; see "Nuclear blast vs. scenery" below.
- **Robot altitude on terrain (found in CR002.21)**: resolved by CR002.25 (#214, owner decision 2026-09-21: match the original); see "Robots on terrain height" below.
- The two owner decisions found during CR001 (scenery blockers §4, first projectile move §8) were decided on 2026-09-21 and implemented by CR002 (`_specs/milestones/cr002-spectrum-fidelity-ui.md`).

## Search & Destroy approach position — RESOLVED (owner request 2026-09-25)

A Search & Destroy hunt closes on a position **lane-aligned** with its target:
the two 2×2 bodies face each other along a full edge, i.e. the hunter's anchor
is exactly two cells from the target's on one axis and level with it on the
other (`navigation.body_alignment_anchors`). A robot fires along its cardinal
facing and a bullet connects only while the two bodies overlap on the off axis
(`occupancy.unit_footprints_overlap`), so any staggered stop — one cell off the
lane, or corner-to-corner — leaves the hunter shooting past its target for
ever. That was the reported playtest bug.

When no aligned anchor is reachable (a target backed into a corner, every lane
blocked) the goal set falls back to the wider "touching" set, so the hunt
closes as far as it can rather than abandoning the order. Non-electronic
hunters use the same rule: their greedy goal is the nearest aligned anchor, and
arrival means alignment.

**Hunts keep their order when blocked (CR004.13 #299, owner decision
2026-09-27).** A solo playtest showed an electronics hunter dropping to Stop &
Defend while enemies were still alive: its target was moving along a one-lane
corridor toward it, the target's in-flight footprint removed every near-side
approach anchor, and electronic navigation reported `UNREACHABLE` for a block
that lasted one tick. For Search & Destroy (robots), by human and AI robots
alike:

- **Keep the order.** `UNREACHABLE` is no longer a fallback for this order. The
  fallbacks that stay are "no enemy robot at all" and "no weapon capable against
  robots".
- **Move toward the target anyway.** With no route, the robot takes the single
  greedy step down the larger remaining delta toward the target's nearest
  aligned anchor (the non-electronic primary step). The step goes through the
  normal move legality, and if it is illegal the robot waits this update. A
  robot already touching the target holds.
- **Don't re-plan every tick.** The route is cached on the robot
  (`Robot.hunt_route`: target id, planned tick, origin and steps) and followed
  between re-plans. It is re-planned every `EngineRules.robot_hunt_replan_ticks`
  ticks (default 20 = 1 s = 5 game cycles; the controller chose this default and
  the owner may retune it), and early when the route is exhausted, the robot is
  off its cached route, its next cell is no longer enterable (occupied, reserved
  or impassable terrain), or target selection picks a different robot. A next step that is enterable but still
  refused only waits, and a hunt with no route waits for the next periodic
  re-plan, so a blocked hunter does not plan every tick. Any order change,
  including a fallback, and docking clear the cache.

Search & Capture, Advance/Retreat and Search & Destroy against structures are
unchanged.

## Documented deviations

Deliberate differences from the original that the owner decided to keep. They are not open questions.

1. **Bullet scan (owner decision 2026-09-21: keep the engine behaviour).** The original's bullet hits the first `bit 6` mark in its 3×3 scan with no robot-height test, and a ship or bullet mark there ends the bullet with no damage. The engine keeps its §8 altitude gate (a robot whose top, terrain altitude plus stack height, is below the bullet altitude is flown over; owner decision 2026-09-22, see "Robots on terrain height") and only robots are hit; commanders and other projectiles never stop a bullet.
2. **Bullet slots per robot (owner decision 2026-09-21).** The Spectrum shares two bullet slots among each side's AI robots plus slot 0 for combat mode (`Lb6b8_find_new_bullet_ptr`); the engine keeps one channel per robot (§8).
3. **Construction modality (owner decision 2026-09-21).** Only the building player's commander is frozen; the match keeps running (§22).
4. **Lift shortened by up/down (owner decision 2026-09-21).** Ignored; moves never shorten the automatic exit lift (§13).
5. **Robot update phase.** A stationary robot that has not fired since it last moved counts as being at an update on every tick, so its first shot can come up to one period earlier than in the Spectrum (§8, CR002.19).
6. **Stop & Defend turning.** The engine's Stop & Defend does not turn toward enemies as `Lb1d7` does; this is part of the §5 "historical quirks of the dumb algorithm", not a product decision. The other half of this item — a non-electronic robot that could not step sideways where the original's `Lb326`/`Lb33e` falls back to a random possible direction — was adopted on 2026-09-25 and is no longer a deviation; see §5 "Detour fallback".
7. **Radar shows only the viewer's own commander (owner decision 2026-09-21),** as in the single-player original; enemy robots are shown. Marks are white only (the original flickers cyan/yellow on blue).
8. **Debris variant.** The Spectrum picks debris type 6 or 7 at random; both behave the same, so the engine consumes no RNG for it and the renderer always uses one sprite.
9. **Fence post centred on its footprint (owner decision 2026-09-22, CR003.7 #222).** The renderer draws the map-end fence sprite centred on its 2×2 footprint, not where the Spectrum draws it. Presentation only; see "Map-end fence placement" below.
10. **Robot/commander/structure Spectrum sprites replace procedural prisms (owner-directed, 2026-09-22).** See "Commander/robot/structure sprites" below. Presentation only. Robot facing was wired up on 2026-09-23 (owner request): the snapshot carries a `facing` field and all four cardinal-direction piece sprites the disassembly encodes are decoded and drawn. The commander still has a single frame with no facing (`Lcd83_render_player`).
11. **The AI opponent's fidelity to the Spectrum enemy is a reference, not a contract (owner decision 2026-09-25, CR004 #282).** The disassembly's enemy AI is used where it usefully explains the original, then deliberately gone beyond; the target is an opponent that plays better than the Spectrum's, not one that reproduces it. A later fidelity pass finding the AI does not match the original's behaviour is not, by itself, a bug.
12. **The AI seat has no commander, and none is shown (owner decision 2026-09-25, CR004 #282).** It orders robots and builds without a commander landing on its own heli-pad or occupying any cell on the map (`functional-spec.md` §3.1). This is a deliberate asymmetry with the human seat: the human pays a positioning cost to build and to drive a robot directly, and the AI does not, in exchange for having no unit that can be destroyed or take direct control. A build with no commander present is not a missing rule.
13. **AI scheduling: every decision tick evaluates the whole state, not a random slot (owner decision 2026-09-25, CR004 #282/#284, found in CR004.2).** The Spectrum draws one of 32 slots per game cycle and idles 1/4 of the time (`Lb7f4_update_enemy_ai`, `docs/cr004/spectrum-ai-notes.md` §1); the engine's planner runs on the same cadence (`ai_decision_interval_ticks`, one Spectrum cycle) but considers every owned war base and robot on every decision, never one dice-rolled slot, and never idles by construction.
14. **AI construction: an affordable, purposeful design chosen deterministically, not a random design byte dropped when it fails to fit (owner decision 2026-09-25, CR004 #282/#285, found in CR004.2).** `Lb81b_pick_random_warbase_loop` and the build code after it flip a coin per war base and roll a random chassis/weapon/electronics byte, discarding the whole attempt if the design is not legal or not affordable (`docs/cr004/spectrum-ai-notes.md` §2); `ai/construction.py`'s `choose_design` picks the highest-value legal design the current pool can actually pay for, and a session is opened only when the whole design is affordable, so no build stalls or is abandoned mid-attempt. The Spectrum's weapon-count floor that grows with army size (`Lb505`) and its general-pool spending cap (`Lb8c6`, half the pool) are kept in spirit but recomputed from the current army size and threat state (a threatened war base releases both), rather than the original's fixed step-of-8 and fixed half-pool cap.
15. **AI robot orders: value-ranked targets and a threat response, not nearest-by-x with no defence (owner decision 2026-09-25, CR004 #282/#286, found in CR004.2).** The Spectrum's target choice is nearest-by-x only, and nothing in its strategic layer reacts to an approaching enemy (`Lb34d_find_capture_or_destroy_target`; nothing in `Lb7f4_update_enemy_ai`; `docs/cr004/spectrum-ai-notes.md` §3–4). `ai/robot_orders.py` scores capture/destroy targets by production value, distance and how contested they already are, and detects an enemy robot closing on an owned war base or factory (within a threat radius derived from weapon range) to divert or hold a defender. Two Spectrum rules are kept as-is: exclusive targets per order type (`Lb36c_check_if_building_is_available_and_nearest_than_current_nearest`) and reading only information a player's own client would render — neither planner reads the opponent's resource pool, construction session or orders.
16. **AI never targets the map's neutral interior war bases; the Spectrum does (owner decision 2026-09-25, CR004 #282/#286, found in CR004.2).** `Lb3d5`'s flags exclude those bases from the enemy's capture candidates (`docs/cr004/spectrum-ai-notes.md` §3); `ai/robot_orders.py`'s enemy-war-base capture type includes them, because they decide victory (`victory.py`) and a planner that ignored them would be leaving material on the table, not being faithful.
17. **New AI robot receives a real order immediately, not ~6.4s of idle Stop & Defend (owner decision 2026-09-25, CR004 #282/#285/#286, found in CR004.2).** The Spectrum launches a new robot on Stop & Defend and only re-evaluates its order with probability 1/32 per cycle thereafter (`Lb920_enemy_ai_single_robot_control`, `docs/cr004/spectrum-ai-notes.md` §5, §8: the annotation's "1/32 re-roll" comment is inverted — orders are *kept* 31/32 of the time); the engine's order planner assigns a productive order in the same decision the robot launches, and re-plans only on events (a target gone, a new threat, a threat ended), not on a fixed re-roll probability.
18. **AI composition response has no armour stat to key on, unlike a conventional RTS (owner decision 2026-09-25, CR004 #282/#286, found in CR004.2).** The engine models combat with weapon range/damage and robot height, not an armour value; `docs/cr004/spectrum-ai-notes.md` §6 flags this because the Spectrum's own AI does not reason about matchups at all. `ai/robot_orders.py`'s `matchup` heuristic ranks a matchup on reach then damage (whether a robot can hit or be hit at all, then which one is favoured), which is new design with no Spectrum evidence behind it, not a reproduction of anything the original does.

## Nuclear blast vs. scenery — RESOLVED (CR002.18 #196, owner decision 2026-09-21)

Owner decision: model it in CR002. Verified by a direct reading of `netherearth-annotated.asm` (`santiontanon/netherearth-disassembly`):

- *Area.* The scenery scan is part of the robot scan in `Lba02_look_for_robots_in_range_of_nuclear_bomb`. It uses the same carrier-centred trimmed 9×9 window (§20; rows outside the map are skipped). At each window cell, after the robot check, `Lba44_robots_handled` skips the cell when map bit 5 is set. Bit 5 marks a cell that is not the bottom-left corner of a 2×2 element (`Lbd91_add_element_to_map`). So only an element whose bottom-left cell (lowest x, highest y) is in the window is affected, and the whole element is affected.
- *Types.* It skips type < 17 ("do not destroy terrain") and type ≥ 21 ("do not destroy the fences that mark the end of the map"). Types 17–20 are destroyed. Only 17 (`box_low`) and 18 (`box_high`) occur on the map; building pieces are types 15/16 and are untouched.
- *Result.* `call Ld358_random; and 1; add a, 6; call Lbd91_add_element_to_map` writes a 2×2 element of type 6 or 7 at the same anchor, over the same four cells. `Ld7bc_map_piece_heights` gives both types height 3, so the robot speed row is rugged (`Lb5f3`, altitude 1–3). Both are type < 8, so no chassis is blocked (`Lb513`). Types 6/7 are the map's native rough pieces (§4). The random choice between them is only visual, since both types behave the same in play.

Engine: map blockers carry `destructible: true` (the 149 boxes; the decoder emits it). `GameState.scenery_debris` holds the debris blocker ids, in canonical order, and is included in snapshots and the protocol `SnapshotState`. `destruction.effective_world` and `destruction.scenery_world` drop those blockers and make their cells `rough` terrain. Both are memoized; the base `WorldMap` stays immutable. `engine.step` uses `scenery_world` for robot move validation and commander collision, where it previously used the base map. The random 6/7 variant is not modelled: it has no gameplay effect, and the engine consumes no RNG for it. Tests: `engine/tests/test_nuclear_debris.py`.

Height 3 for the ship and bullets (CR002.21, #203): debris cells get the map's `terrain.debris_height` (3, the height of types 6/7), so debris behaves exactly like the native rough pieces of those types. The ship rests on it at altitude 3, and a robot on it takes damage with `ground_height` 3. Bullets fly over it, since 3 < bullet altitude 10. See "Terrain piece heights" below.

## Terrain piece heights — RESOLVED (CR002.21 #203, owner decision 2026-09-21)

Owner decision: model terrain piece heights map-wide in CR002 (found in CR002.18). `Ld7bc_map_piece_heights` gives rough types 2–5 height 2, rough types 6/7 height 3, mountains (8–11) height 6 and ditches 0. The map decoder writes each terrain cell's piece height (`terrain.cells[].height`) and the debris height (`terrain.debris_height: 3`, types 6/7) into `zx-spectrum-original.yaml` (still `version: 1`; provenance in `zx-spectrum-original.md`, "Terrain heights"). The engine has one surface height: `collision.surface_height_at` (the highest structure/scenery component or terrain piece on a cell) and `collision.unit_surface_height` (the 2×2 maximum, `Lb5d6_map_altitude_2x2`). It is used by:

- commander collision, landing and gravity (`Lb052_check_player_collision`, `Laf4c`, `Lafc3_gravity`): the ship cannot fly into a piece below its height and rests on top of it, at 2 or 3 on rough, 6 on mountains, 3 on debris;
- projectile termination (`Lb724_bullet_update_internal`, height ≥ altitude). Terrain is at most 6 high, below the bullet altitude 10, so terrain never stops a bullet, as in the original;
- the damage `ground_height` (`Lb495` stores `Lb5d6` as `ROBOT_STRUCT_ALTITUDE`; `Lb7a7`): a robot on rough or a mountain takes less damage. This also closes the §9 follow-up note on `ground_height_at` always being 0;
- the heli-pad rest altitude (unchanged: the pad is all roof).

Nuclear debris gets the debris height (`destruction.scenery_world`/`effective_world`). Frontend shadows (`surface.ts`) read the same map heights. The Spectrum draws terrain pieces as sprites at elevation 0 (`Lcd18_draw_map_cell`); their height is not a drawing parameter, so terrain drawing is unchanged. Tests: `engine/tests/test_terrain_heights.py`, `test_nuclear_debris.py`, `frontend/src/render/surface.test.ts`.

## Robots on terrain height — RESOLVED (CR002.25 #214, owner decisions 2026-09-21 and 2026-09-22)

Owner decision (2026-09-21): match the original (found in CR002.21). A robot stands on the terrain under it. `Lb495` (in `Lb471_move_robot_one_step_in_desired_direction`) stores `Lb5d6_map_altitude_2x2`, the highest map piece under the 2×2 body, in `ROBOT_STRUCT_ALTITUDE` right after `Lb4b9_robot_advance` moves the robot one cell, so the altitude changes only together with the robot's cell; a new robot starts at 0 (`La6c8`/`Lc849`). A robot's top is `height + altitude`: the ship rests and lands on it (`Lb099_get_robot_or_decoration_altitude` via `Lb052_check_player_collision`), docks there (`La69a`, `La720_land_on_robot`), rides it under direct control (`Lb495` sets the ship's altitude to it after each step), and is an obstacle to the robot while lower than it (`Lb513_get_robot_movement_possibilities`); the robot is drawn raised by the altitude (`Lcee8_draw_robot_to_buffer`).

Engine: the altitude is `collision.unit_surface_height` at the robot's authoritative anchor in the physical world (`destruction.scenery_world`), and `collision.robot_top` = altitude + stack height. The engine moves the authoritative anchor when a move completes (the robot stands on its origin cell during the move, `movement.py`), and the altitude follows the anchor, as in `Lb495` where cell and altitude change together. `RobotFixture.altitude`/`top` carry it into commander collision, landing, auto-dock and following (`engine._robot_fixtures`, `docking.py`); `movement.commander_blocks_robot_cell` uses the top at the robot's current anchor (`Lb513`); the undock lift starts from the top the docked commander rides; a commander ejected from a destroyed robot is left at its top (`destruction.destroy_robot`). Damage already used the altitude (CR002.21, "Terrain piece heights"). On the original map every war-base exit is flat, so a launched robot is at 0, as in the original. No snapshot change: the frontend derives the altitude from the map heights and the robot's anchor (`frontend/src/render/robot.ts` `robotGround`, over `surface.ts`), draws the robot raised by it (blended from origin to destination during a move, presentation only), and draws a docked commander on the drawn top. Tests: `engine/tests/test_robot_terrain_height.py` (docking on rough 2/3 and mountain 6, resting on an enemy, the ship blocked below a raised robot and blocking it, ejection, lift start, altitude timing), `frontend/src/render/robot.test.ts` (drawing offset).

**Bullet altitude gate (owner decision 2026-09-22, not evidence-derived).** The engine's §8 bullet gate compares the robot's top (terrain altitude under its 2×2 body + stack height, `collision.robot_top`) with the bullet altitude (10): `combat._robot_hit_at` hits a robot when `robot_top >= normal_projectile_altitude`. With the Spectrum piece heights (CR003.3, below) the shortest robot is 13 tall, so on the original map every robot's top is at or above 10 and every robot is hit; the gate only matters for a raised bullet altitude or overridden module heights. (Before CR003.3 the placeholder heights made a height-6 robot, flown over on flat ground and hit on a mountain.) The original has no robot-height test for bullets at all ("Documented deviations" item 1), so this is an owner decision on the engine's own gate, not a reading of the disassembly. Tests: `engine/tests/test_robot_terrain_height.py::test_bullet_gate_uses_the_robot_top`, `::test_bullet_hits_the_shortest_spectrum_robot_even_on_flat_ground`.

## Robot piece heights — RESOLVED (CR003.3 #218, owner decision 2026-09-22)

Owner decision: adopt the Spectrum piece heights. `Ld7b4_piece_heights` (summed per equipped piece by `Lb904_robot_height_loop` into `ROBOT_STRUCT_HEIGHT`) gives bipod 11, tracks 7, anti-grav 8, cannon 6, missile 6, phaser 7, nuclear 7, electronics 7. They replace the earlier placeholder `EngineRules.module_height_*` values (chassis 4, other modules 2), which had no evidence behind them. Robot heights now range over the disassembly header's 13 (tracks + cannon) to 38 (bipod + missile + phaser + nuclear + electronics), so the damage formula runs on its native scale: a phaser hit on a tracks + cannon robot at ground level deals `(60 − 13) / 4 × 4 = 44`, the header's worked example (this also settles the §9 strength note's caveat about the input range). The robot top (`collision.robot_top`), docking altitude and the ship's collision with robots follow from the stack height with no other change. The tallest robot on the highest walkable ground (mountain, 6) has its top at 44, below `commander_max_altitude` (48), so the ship can rest and dock on every robot. Tests: `engine/tests/test_rules.py::test_default_rules_match_spectrum_piece_heights`, `engine/tests/test_robot_stack.py` (13–38 range, the 44 ceiling check), `engine/tests/test_combat_damage.py::test_spectrum_worked_example_phaser_on_derived_tracks_cannon_robot_deals_44`.

## Capture order lifecycle — RESOLVED (CR003.2 #217, owner decision 2026-09-22)

Owner decision: full Spectrum behavior. Before CR003.2 a Search & Capture order completed when the robot reached the capture footprint and was replaced by Stop & Defend, and it fell back to Stop & Defend when no target existed, so a robot stayed on the first factory it captured. Evidence (`_specs/milestones/cr003-playtest-fixes.md`, item 2): `Lb289_choose_direction_orders_with_building_targets` re-checks the stored target (`ROBOT_STRUCT_ORDERS_ARGUMENT`) each update and calls `Lb34d_find_capture_or_destroy_target` when its ownership no longer matches; the order never changes. With no target a player robot keeps its order and does not move. `Lb36c_check_if_building_is_available_and_nearest_than_current_nearest` skips a building another friendly robot with the same order already targets.

Engine (`orders.py`): `SearchCapture.structure_id` stores the target. The order stays `ACTIVE` for as long as the player leaves it; it holds the capture cell (Stop & Defend intent) while the target is uncaptured, retargets after the capture, and idles under the same order when nothing matches. Capture progress itself is unchanged (§6, §7).

**Amended (owner decision, 2026-09-23): target selection re-opens on any ownership change.** Each evaluation re-runs the nearest-match selection instead of holding the stored target until it stops matching, so a structure that changes hands nearer to the robot than its current target pulls it in. This deliberately departs from `Lb289`, which only retargets once the stored target's own ownership stops matching: on the 512-cell map that let a robot walk hundreds of cells past structures that had become valid targets behind it (observed in a live match replay — a robot ordered to capture enemy factories at x≈494 targeted the only enemy factory, at x=227, and was still 25 cells short three thousand ticks later). The order is still never dropped or completed. One exception: a robot standing on its target's capture footprint keeps that target, because an interrupted capture resets to zero (§7), so re-aiming mid-capture would discard the elapsed occupation and could pull a robot off every target in turn without finishing one. The enemy AI's no-target switch to Destroy Enemy Robots is not adopted: the bot keeps its existing behavior (CR003 scope). Tests: `engine/tests/test_orders.py`, `engine/tests/test_engine_orders_integration.py`.

## Map-end fence placement — RESOLVED (CR003.7 #222, owner decision 2026-09-22)

Playtest report: the left map-end wall looked different from the right one. Screenshots of both walls at the same zoom (fixture and live two-client runs) show that the two fences draw identically. They use the same sprite, slicing, depth order and terrain. The difference shows only next to a unit. A commander at the left limit (x = 14) stands one empty-looking column away from the fence. At the right limit (x = 501, body 501–502) it is flush with the fence.

Evidence (`netherearth-annotated.asm`):

- *Data is symmetric.* Complex structure #86 (four type-21 elements) is placed at x = 12 (`#0c`) and x = 503 (`#f7`), y = 1 and 9. `Lbd91_add_element_to_map` stamps x..x+1, y−1..y from the anchor (min x, max y), so the fences cover columns 12–13 and 503–504. Both entries for type 21 in `Ld6e8_additional_isometric_graphic_pointers` are `La050_iso_additional_graphic_26`.
- *The post is off-centre.* The fence sprite is 16 px wide and its columns 0–4 are blank. The post is a thin, roughly one-cell box standing on the footprint's −x column (the anchor cell). Box and ship sprites cover their whole 2×2 footprint. So the post stands on column 12 at the left end and on 503 at the right end. The play area touches the right post but is separated from the left one by column 13.
- *The original has the same asymmetry.* `MIN_PLAYER_X`/`MAX_PLAYER_X` are 14/501, and the ship sprite (`graphic_0/1`) covers its 2×2 body. So the Spectrum also shows a gap at the left end and a flush fence at the right end.
- *4-px odd-parity offset (not applied).* `Lcf2d_draw_sprite_to_buffer` computes the x position in 4-px steps (2·x + y − 24). It draws at the byte below, and table entry 2·type + (step & 1) supplies a copy pre-shifted by 4 px for odd steps, so for odd y. Type 21 has no shifted copy, and every fence anchor has an odd y. So the Spectrum draws the fences 4 px left of the placement the renderer uses for all other sprites. Template matching `_specs/milestones/cr002/main-screen.png` confirms it: the fence anchored at (12, 13) is 40 px left of box blocker-9 (16, 14), against 36 px in the renderer.

Owner decision (2026-09-22): centre the fence post on its 2×2 footprint. This is presentation only: map data, collision and heights are unchanged, and the 4-px Spectrum shift is not applied. Implementation is generic, with no special case per column. A scenery asset may carry an optional `offset` (world pixels, [right, down]) in `frontend/public/assets/manifest.json`, and `spriteOrigin` applies it (so the per-cell slices follow). `scenery.fence` uses `[2, -3]`, which puts the post base within 1 px of the footprint centre (`frontend/src/render/scenery.test.ts`). Both walls now leave the same half-cell gap to a unit at the movement limit.

## Commander/robot/structure sprites — RESOLVED (owner-directed, 2026-09-22)

Owner-directed extension of the CR002.5 scenery pipeline: replace the
procedural placeholder prisms of the commander, robot modules and war-base/
factory blocks with decoded Spectrum sprites, the same way scenery blockers
already are. Requested directly by the owner, not inferred from a milestone.
Evidence (`santiontanon/netherearth-disassembly`, `netherearth-annotated.asm`
and `netherearth-annotated-data.asm`):

- **Commander.** `Lcd83_render_player` always draws graphic index 0 of
  `Ld6e8_additional_isometric_graphic_pointers` (`xor a` before the call):
  one frame (`L8e3a_iso_additional_graphic_0`), no directional facing, no
  selectable pieces. This is the on-foot commander figure, distinct from a
  robot.
- **Robot pieces.** `Lcefd_draw_robot_piece_to_buffer` looks up
  `Ld6c8_piece_direction_graphic_indices` at `4 * piece + direction` (piece
  0 = bipod .. 7 = electronics; direction is one of 4 cardinal directions
  decoded from the robot's one-hot `ROBOT_STRUCT_DIRECTION`), then draws the
  indexed sprite from `Ld740_isometric_graphic_pointers` (58 pointers,
  confirmed by its own per-piece inline comments: "tracks", "bipod",
  "antigrav", "cannon", "missiles", "phaser", "nuclear"). Every piece has 4
  direction-indexed table entries; some pieces reuse the same sprite for
  more than one direction (`anti_grav` and `nuclear` reuse one sprite for
  all 4; `bipod`/`tracks`/`missile` reuse one sprite per pair of directions;
  `cannon`/`phaser`/`electronics` have 4 distinct sprites) — read directly
  off `Ld6c8_piece_direction_graphic_indices`, not guessed.
- **War-base/factory blocks.** Both structures are built from map elements,
  not from the "robot/factory/warbase is here" object-draw path (that path
  only draws the flag/`"H"`/piece-on-top *decoration*, `Lce38_draw_decoration`
  — see below). `Lbcf9_add_warbases_and_factories_to_map` adds each as a
  "complex structure" (`Lbd61_add_complex_structure_to_map`) from
  `Lbfb2_warbase` / `Lbfe2_factory`: a list of (map-element type, x-offset,
  y-offset) triples. Both lists use only element types 15 and 16 (`Ld7bc_
  map_piece_heights` gives them height 7 and 15). `data/maps/zx-spectrum-
  original.yaml`'s `war_bases`/`factories` components already use exactly
  those two heights (7 and 15) for every cell, so `MapComponent.height`
  maps 1:1 to one of the two decoded wall sprites with no invented data.
- **Factory piece-on-top / war-base "H" decorations.** `Lce56_decoration_
  sprite_indexes` (9 entries) draws a flag (decorations 7/8, already sourced
  as `FLAG_SPRITES`/`flags.ts`, CR002.6), the war-base `"H"` (decoration 0,
  the one entry at a distinct height, `L87f0_iso_graphic_44`), and one icon
  per `FactoryType` (decorations 1–6, at the piece table's electronics/
  nuclear/phaser/missile/cannon/chassis(tracks) graphic indices — matching
  the engine's 6 `FactoryType` values exactly). **Not implemented**: the
  owner's request covered the four sprite categories above; the decoration
  overlay (flag excepted, already shipped) is a smaller follow-up left for a
  future pass, not a gap in the sourcing.

Owner decision: ship it, same terms and same caveat as the scenery exception
(`public/assets/README.md`, "Provenance and licensing" — the pixel data's
redistribution rights are unverified, flagged for public release, not a v1
blocker). Presentation only: no engine, collision or height change. New
generated files: `frontend/src/render/robot-sprites.ts`, `commander-sprites.ts`
(from `frontend/scripts/decode-unit-sprites.py`); the war-base/factory wall
sprites are decoded into `scenery-sprites.ts` itself (elements 15/16) since
they use the exact same per-map-element decode as scenery, and are wired
through `manifest.json`'s new `structures` section (`structureWallAsset` in
`renderer.ts`), matching the "unmapped falls back to a placeholder prism"
contract every other sprite category already has. `sprite-slice.ts` factors
the slicing/positioning/texture-caching algorithm scenery.ts used to own
alone, so robots, the commander and structure walls share it instead of each
reinventing it.

**Resolved (owner request, 2026-09-23):** robot facing. The snapshot now
carries a per-robot `facing` (`east`/`west`/`south`/`north`,
`nether_earth.robot.RobotFacing`), and all 4 direction sprites per piece are
decoded and drawn. A robot is launched facing south (the walk-out direction,
`La6c8`) and turns to the direction of each accepted step, matching `Lb471`,
which neither moves nor turns for direction 0.

**Extended (owner decision, 2026-09-23): facing drives combat, and turning
costs ticks.** A projectile travels in the firing robot's facing —
`Lb6d6_weapon_fire` copies `ROBOT_STRUCT_DIRECTION` straight into
`BULLET_STRUCT_DIRECTION` — so the fire command carries no target and there
is no aiming; the engine's old target-cell-to-direction rule is gone, along
with `targetX`/`targetY` on the protocol payload. A robot that wants to step
or shoot in a direction it does not face spends one update rotating 90
degrees toward it and does not move (`Lb471`, whose rotate branch sets the
new direction and returns, putting the walk-out step counter back "since
this was not a move"). `Lb471` rotates the desired bit two places when
`desired | current` is `0x03` or `0x0c`, so a reversal goes through a
perpendicular and costs two rotations. A robot mid-turn neither moves nor
fires. Duration: `rules.robot_turn_ticks`, one game cycle.

An autonomous robot turns toward its target before firing; a robot standing
on a capture cell never turns for combat, since an interrupted capture
resets to zero (§7).

**Extended (owner decision, 2026-09-24):** a robot holding a capture cell
turns to face *out* of the structure, away from its body
(`capture.outward_facing`), instead of staying pointed at the wall it walked
into. The direction comes from the structure's own components, so it does
not depend on the route taken, and is compared as integers rather than via a
floating-point centroid. The original has no equivalent --
`Ladb7_building_loop` never touches `ROBOT_STRUCT_DIRECTION` -- so this is a
deliberate departure, made necessary by shots travelling in the robot's
facing. The turn is requested as a move outward, which `Lb471` spends on a
rotation; because it is only requested while the facing is wrong, the step
that would carry the robot off the cell is never issued.

The facing-direction bonus to the fire-decision *scan* (10 cells ahead
rather than 8) is still unadopted — that remains the open research item in
§8, and the rules above do not decide it.

**Resolved by evidence (2026-09-23):** the one-hot
`ROBOT_STRUCT_DIRECTION` bit order is east 1, west 2, south 4, north 8, and
the sprite columns follow the set-bit position.
`Lb724_bullet_update_internal`'s `rrca` chain moves `+x`, `-x`, `+y`, `-y`
in that bit order, and `Lcefd_draw_robot_piece_to_buffer`'s
`Lcf08_direction_loop` shifts the one-hot value right until carry, counting
the bit position into `b`, then indexes
`Ld6c8_piece_direction_graphic_indices` at `4 * piece + b`. So the decoder's
east/west/south/north column order (`FACINGS`) is a reading of the code, not
an assumption.

## Resolution process

Use this fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

When one of the remaining items is verified, update the functional/technical specs and this file in the same change.