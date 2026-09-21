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

## 3. Miles-to-grid-cell conversion — RESOLVED

- 1 mile = 2 map tiles/cells.
- 1 tile = 0.5 miles.
- Cannon 10 miles = 20 tiles.
- Missile 14 miles = 28 tiles.
- Phaser 10 miles = 20 tiles.
- Electronics +3 miles = +6 tiles.
- Nuclear radius 8 miles = 16 tiles.
- Advance/Retreat 0–50 miles = 0–100 tiles.

The conversion must exist once in shared game-rule/helper code.

## 4. Exact movement speeds and terrain penalties — PARTIALLY RESOLVED

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

## 8. Exact projectile mechanics — PARTIALLY RESOLVED

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

## 17. Commander starting positions — PROVISIONAL (owner review required)

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

Provisional data (M9, `map_overlay.default_pvp_overlay`):

- `p1_commander` = extreme-left war-base anchor + (−5, +1) → (17, 10) on the original map (evidence-backed);
- `p2_commander` = extreme-right war-base anchor + (+5, +1) → (499, 9) (mirrored convention, **not evidence**).

Spawns are overlay data, not engine rules; changing the convention is a data
edit in one place. Owner must confirm or replace the Player 2 convention.

## 18. War-base heli-pad location and landing height — OPEN (owner review required)

Evidence (tier 2): `Lbb86_assign_warbase_to_player` places the war-base "H"
decoration at (anchor.x, anchor.y − 4), and the game loop enters construction
only when the ship is over that decoration at altitude exactly 15 (`cp 15`),
i.e. on the roof of the 15-high war-base block. The robot then exits at
(pad.x, pad.y + 4) = the anchor cell (`Lcb52_construction_screen_start_robot`,
"robot starts 4 positions off the player in the y axis"), which confirms the
current `*-exit` interaction points.

The engine's locked M3 landing rule (`heli_pad.py`: altitude ==
`commander_min_altitude` on a heli-pad footprint cell) cannot express a
roof-top pad, and the M2 data keeps the `*-helipad` points as ground-level
placeholders at the anchor cell. Options for the owner:

1. keep the ground-level pad at the anchor cell (current, playable; deviates from the original);
2. move the pad to (anchor.x, anchor.y − 4) and extend the M3 landing rule to "altitude equals the pad cell's component height" (fidelity-correct; M3 rule + M2 data change).

Until decided, M9 acceptance uses option 1 as-is and does not treat pad
placement as verified.

## 19. Autonomous use of the nuclear weapon — OPEN (owner review required)

Found by the M9.4 scripted match. The spec defines what a detonation does
(`functional-spec.md` §17.3) but not when an autonomous order uses it. The
current M5/M6 engine policy composes two rules:

- Stop & Defend targets the nearest hostile robot at **any** distance (`orders._defensive_intent`);
- autonomous fire walks weapons in canonical order (cannon, missile, phaser, nuclear) and nuclear has **no range gate** (`autonomous_combat.py`).

Consequence: a nuclear carrier on Stop & Defend detonates on the first tick
any enemy robot exists anywhere on the map, or whenever its normal weapon is
out of range or its projectile channel is busy. Every completed Advance or
Retreat and every fallback order becomes Stop & Defend, so this is reachable
in a normal match and destroys the carrier plus everything within 16 cells.

Options for the owner:

1. autonomous orders never detonate; nuclear is a direct-control decision only;
2. autonomous detonation only when the target (robot or structure) is within the nuclear radius;
3. keep the current policy (not recommended: effectively a self-destruct).

M9 does not change the rule. The acceptance script keeps its striker under
direct control or tolerates the autonomous detonation.

## Remaining research

Only three substantive fidelity areas remain:

1. **Movement timing** — exact Spectrum chassis ticks-per-tile and rough-terrain penalties (#4).
2. **Projectile mechanics** — exact speed/cadence/collision/lifetime behavior (#8).
3. **Combat detail** — exact accuracy, rounding, strength, and electronics modifiers (#9).

## Resolution process

Use this fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

When one of the remaining items is verified, update the functional/technical specs and this file in the same change.