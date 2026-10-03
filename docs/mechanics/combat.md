# Combat, destruction and victory

## Purpose

Robots fire cannon, missile and phaser projectiles that fly along their facing, hit the first tall-enough robot in their path and deal height-dependent damage. A nuclear weapon destroys a shaped area at once, including at most one building. A player whose last war base is captured or destroyed loses.

## State involved

- `GameState.projectiles`: `combat.Projectile(id, owner, source_robot_id, weapon, x, y, z, dx, dy, travelled_cells, max_range_cells, created_tick, first_advance_tick)` — a 2×2 body anchored at `(x, y)`, flying at `z`.
- `robot.Robot`: `active_projectile_id` (the one normal channel), `last_fire_tick`, `turning`, `facing`, `strength` (starts at 100), `height`.
- `GameState.structure_destruction` (destroyed building ids), `scenery_debris` (destroyed box ids), `robot_debris` (anchors of combat kills that left debris).

## Algorithm

All in Step 2c2 of `engine.step`, against the effective world as it stands at each phase: (a) advance projectiles, (b) direct fire, (c) autonomous fire.

### Fire validation and creation (`combat.validate_fire`, `combat.apply_fire`)

`validate_fire`: the robot exists (`NO_SUCH_ROBOT`), the requester owns it (`NOT_CONTROLLED_BY_PLAYER`), the weapon is fitted (`WEAPON_NOT_FITTED`); nuclear is then accepted at once; a normal weapon also needs a free channel (`CHANNEL_OCCUPIED`).

`apply_fire` for a normal weapon additionally rejects a robot mid-turn (`TURNING`) and a second shot in the same fire cycle — `last_fire_tick // robot_fire_cycle_ticks == tick // robot_fire_cycle_ticks` (`ALREADY_FIRED_THIS_CYCLE`). Then:

1. direction = the robot's facing (`resolve_fire_direction`);
2. `max_range_cells` = the weapon's range, + `electronics_range_bonus_cells` when electronics is fitted;
3. the projectile starts at the robot's anchor at `normal_projectile_altitude`, `first_advance_tick` = the cadence tick closing this fire cycle, `(tick // 4 + 1) × 4`, for an autonomous shot, or one cycle later for a direct shot;
4. it makes its **first advance immediately** (`_advance_one`); `last_fire_tick = tick`. If it survives it is stored and occupies the channel; if the first move ends it, it never occupies the channel, and a hit is damaged on the spot.

`FireCommand` (direct fire) is accepted for any robot the player owns; the client sends it only for the docked robot. Direct fire is not gated on the robot update.

### Projectile advance (`combat.advance_projectiles`)

Only on cadence ticks (`is_projectile_advance_tick`: positive multiples of `projectile_advance_ticks`). Projectiles with `tick < first_advance_tick` wait. Each other projectile, in id order, gets one `_advance_one`:

1. if `travelled_cells ≥ max_range_cells`: terminate at the current position (`RANGE_EXHAUSTED`);
2. move `min(projectile_cells_per_advance, remaining range)` cells along `(dx, dy)` and test **only the landing position**:
   - anchor off the map → `OUT_OF_BOUNDS`;
   - `unit_surface_height` under the 2×2 body ≥ the projectile altitude → `STATIC_COLLISION` (high boxes 15, fences 99, buildings; terrain is at most 6 and never stops a bullet);
   - the first robot, ordered by anchor row then column, whose body overlaps the projectile's body, which is not the firer, and whose `robot_top` ≥ the projectile altitude → `ROBOT_HIT`.

A 2-cell step with a 2-cell body leaves no untested gap. Termination clears the firing robot's channel (if it still exists) and emits `ProjectileTerminatedEvent`; the engine then calls `apply_damage` for each hit in event order. A projectile whose firer was destroyed keeps flying. Commanders and other projectiles are never hit.

With the defaults a cannon shot fired by an autonomous robot on tick 5 is at +2 cells on tick 5, then +4/+6/+8/+10 on ticks 8/12/16/20 and expires on tick 24; a direct shot skips tick 8 and expires on tick 28.

### Damage (`combat.apply_damage`)

1. `ground_height = unit_surface_height` under the target's body (`ground_height_at`);
2. `damage = calculate_base_damage(height, ground) × multiplier`, with `calculate_base_damage = (60 − (height + ground)) // 4` and the weapon's `*_damage_multiplier`;
3. `strength − damage > 0` → store it, emit `RobotDamagedEvent`;
4. otherwise record combat debris when `destruction.robot_debris_anchor` finds four plain `NORMAL` cells with no component, then `destruction.destroy_robot`.

There is no hit roll, no component damage and no defensive modifier. Example: a phaser hit on a tracks + cannon robot (height 13) on flat ground deals `(60 − 13) // 4 × 4 = 44`.

### Robot destruction (`destruction.destroy_robot`)

Drops capture progress the robot was earning; frees a commander docked on it (placed at the robot's anchor and last top, `CommanderUndockedEvent`); removes the robot (occupancy and reservations are derived, so they vanish with it); emits `RobotDestroyedEvent`. Destruction is immediate. Calling it twice is a no-op.

### Nuclear detonation (`destruction.execute_nuclear_detonation`)

Triggered by a nuclear `FireCommand` (Step 2c2 b) or by a Search & Destroy structure intent at distance 0 (Step 2c2 c). Measured from the carrier's anchor before anything is destroyed:

1. **robots** — every other robot whose anchor lies in the window: row `r = y − carrier.y + 4` in `0..8`, and `|x − carrier.x| ≤ nuclear_robot_window_row_widths[r] // 2`; both sides; canonical id order;
2. **building** — `_first_building_in_blast` over the effective world's war bases then factories, in map order: the anchor is the smallest capture cell; `dx = |carrier.x − ax|`, `dy = |carrier.y + nuclear_building_dy_offset (+ nuclear_war_base_extra_dy_offset for war bases) − ay|`; in range when `dx < axis`, `dy < axis`, `dx + dy < sum`. The first match only; ownership is ignored;
3. **scenery** — every `destructible` blocker whose anchor (lowest x, highest y) lies in the robot window (`_blockers_in_blast`);
4. destroy the carrier, then the robots, then the building (`destroy_structure`: drop its capture progress and ownership override, add it to `structure_destruction`, emit `StructureDestroyedEvent`); append the blockers to `scenery_debris`.

Robots killed by the blast leave no debris. The derived worlds turn the destroyed building's cells and the destroyed boxes into rough terrain at `terrain.debris_height` ([world-and-map.md](world-and-map.md#derived-worlds-capturepy-destructionpy)). A commander is never harmed.

### Victory (`victory.evaluate_victory`)

Counts war bases per player in the effective world (destroyed ones are absent). Returns a `VictoryEvent` when exactly one player owns at least one war base and every other player owns none; no event when nobody owns any. It is called after a war-base capture completes (Step 2d) and, through `evaluate_victory_after_nuclear_detonation`, after a detonation that destroyed a war base. `engine.step` keeps at most one `VictoryEvent` per tick. The backend finishes the match on it ([match-runtime.md](match-runtime.md)).

## Constants

| Name | Value |
|---|---:|
| `normal_projectile_altitude` | 10 |
| `projectile_advance_ticks` | 4 |
| `projectile_cells_per_advance` | 2 |
| `robot_fire_cycle_ticks` | 4 |
| `cannon_range_cells` | 10 |
| `missile_range_cells` | 14 |
| `phaser_range_cells` | 10 |
| `electronics_range_bonus_cells` | 2 |
| `cannon_damage_multiplier` | 2 |
| `missile_damage_multiplier` | 3 |
| `phaser_damage_multiplier` | 4 |
| `nuclear_robot_window_row_widths` | (5, 7, 9, 9, 9, 9, 9, 7, 5) |
| `nuclear_building_dy_offset` | 1 |
| `nuclear_war_base_extra_dy_offset` | 4 |
| `nuclear_war_base_axis_limit` / `nuclear_war_base_sum_limit` | 7 / 10 |
| `nuclear_factory_axis_limit` / `nuclear_factory_sum_limit` | 5 / 7 |
| `robot_turn_ticks` | 4 |
| starting strength | 100 (`Robot.strength` default) |

## Determinism notes

Projectiles advance in id order and their hits are damaged in event order; hit candidates are ordered by anchor row then column; blast victims by id; buildings by map order. Projectile ids are `projectile-<robot>-<tick>`. No randomness (debris type is not drawn).

## Spectrum evidence

- `Lb6d6_weapon_fire` — range counter 5 (cannon/phaser) or 7 (missiles), +1 with electronics (`bit 7, (ix + ROBOT_STRUCT_PIECES)`), direction copied from `ROBOT_STRUCT_DIRECTION`, first move before returning.
- `Lb70d_bullet_update` / `Lb724_bullet_update_internal` — 2 cells per update, y-bounds test, `Lb5d6_map_altitude_2x2 ≥ BULLET_STRUCT_ALTITUDE` stops it, then the 3×3 robot-anchor scan.
- `Lb0ca_update_robots_bullets_and_ai` — robot loop then bullet loop each cycle (an AI shot moves twice in its cycle); `Lacb3_regular_weapon_fire` — combat-mode shot outside that loop.
- `Lb7a7_potentially_hit_a_robot` (`ld a, 60` / `sub` / `srl a` / `srl a`), `Lb7c8_damage_calculation_loop`; strength 100 at spawn; `Lb7d7_robot_destroyed`, `Lb116_robot_destroyed`.
- `Lb99f_fire_nuclear_bomb` (`ld de, #070a`, `ld de, #0507`, `ld bc, #0909`), `Lba02_look_for_robots_in_range_of_nuclear_bomb`, `Lba44_robots_handled`, `Lbc27_replace_building_by_debris`.

## Deviations

- [Bullet scan: only robots, and only robots tall enough](../../_specs/deviations-from-original.md#bullet-scan-only-robots-and-only-robots-tall-enough).
- [One bullet channel per robot](../../_specs/deviations-from-original.md#one-bullet-channel-per-robot).
- [Debris variant](../../_specs/deviations-from-original.md#debris-variant).
- Not a recorded deviation: the Spectrum keeps a destroyed robot blinking for 4 cycles before removal (`Lb7d7_robot_destroyed` writes −4); the engine removes it at once. Listed for the owner in [resolved-questions.md](../../_specs/resolved-questions.md#damage-accuracy-and-electronics-effects).

## Tests that pin it

- `engine/tests/test_combat_fire.py::test_channel_occupied_rejects_a_second_normal_weapon_fire`
- `engine/tests/test_combat_fire.py::test_nuclear_fire_bypasses_the_channel_even_when_occupied`
- `engine/tests/test_combat_projectile.py::test_a_shot_travels_in_the_robots_facing`
- `engine/tests/test_combat_projectile.py::test_a_robot_mid_turn_cannot_fire`
- `engine/tests/test_combat_projectile.py::test_projectile_travels_exactly_its_code_derived_range`
- `engine/tests/test_combat_projectile.py::test_target_two_or_three_cells_away_is_hit_on_the_fire_tick`
- `engine/tests/test_combat_projectile.py::test_direct_and_autonomous_first_advance_ticks`
- `engine/tests/test_combat_projectile.py::test_robot_fires_at_most_once_per_game_cycle`
- `engine/tests/test_combat_projectile.py::test_a_projectile_passing_beside_a_high_box_stops`
- `engine/tests/test_combat_projectile.py::test_advance_projectiles_hits_a_robot_one_lane_beside_the_path`
- `engine/tests/test_combat_projectile.py::test_advance_projectiles_misses_a_robot_two_lanes_beside_the_path`
- `engine/tests/test_combat_projectile.py::test_advance_projectiles_source_robot_destroyed_mid_flight_does_not_crash`
- `engine/tests/test_robot_terrain_height.py::test_bullet_gate_uses_the_robot_top`
- `engine/tests/test_robot_terrain_height.py::test_bullet_hits_the_shortest_spectrum_robot_even_on_flat_ground`
- `engine/tests/test_combat_damage.py::test_spectrum_worked_example_phaser_on_derived_tracks_cannon_robot_deals_44`
- `engine/tests/test_combat_damage.py::test_apply_damage_exact_threshold_destroys_robot`
- `engine/tests/test_combat_damage.py::test_destroy_robot_relocates_docked_commander_to_free`
- `engine/tests/test_terrain_heights.py::test_robots_on_high_ground_take_less_damage`
- `engine/tests/test_combat_nuclear.py::test_war_base_blast_range_boundaries`
- `engine/tests/test_combat_nuclear.py::test_factory_blast_range_boundaries`
- `engine/tests/test_combat_nuclear.py::test_robot_window_corners`
- `engine/tests/test_combat_nuclear.py::test_two_war_bases_in_range_only_the_first_is_destroyed`
- `engine/tests/test_combat_nuclear.py::test_building_ownership_is_not_checked`
- `engine/tests/test_nuclear_debris.py::test_blast_next_to_a_box_turns_it_into_rough_debris_and_keeps_the_fences`
- `engine/tests/test_robot_debris.py::test_a_robot_killed_on_plain_ground_leaves_rough_debris`
- `engine/tests/test_robot_debris.py::test_robots_killed_by_a_nuclear_blast_leave_nothing`
- `engine/tests/test_robot_debris.py::test_a_nuked_building_becomes_rough_debris`
- `engine/tests/test_victory.py::test_victory_when_opponent_owns_zero_war_bases`
- `engine/tests/test_victory.py::test_no_victory_when_no_player_owns_any_war_base`
- `engine/tests/test_victory.py::test_full_integration_final_war_base_destruction_triggers_victory_on_same_tick`
