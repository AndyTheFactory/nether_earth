# Mechanics — how the engine implements the rules

These pages explain the algorithms the code actually runs, module by module. They are written from `engine/src/nether_earth` and `backend/app`, not from the specifications.

- The **specifications** in `_specs/` are the product contract: [functional-spec.md](../../_specs/functional-spec.md) says what the game does, [technical-spec.md](../../_specs/technical-spec.md) how the system is built. When a page here and a specification disagree, the code is what runs; the disagreement is a defect to report, not a choice to make silently.
- The **mechanics pages** say how a rule is computed: which function, in which order, with which constants, and which tests pin it.
- Why a rule is what it is lives in [resolved-questions.md](../../_specs/resolved-questions.md); intentional differences from the ZX Spectrum in [deviations-from-original.md](../../_specs/deviations-from-original.md).

## Pages

| Page | Covers |
|---|---|
| [timing-and-determinism.md](timing-and-determinism.md) | `clock.py`, `rng.py`, `engine.py` step order, `replay.py` |
| [world-and-map.md](world-and-map.md) | `map.py`, `map_overlay.py`, `terrain.py`, `structures.py`, `occupancy.py`, `heli_pad.py`, `scenario.py` |
| [commander.md](commander.md) | `commander.py`, `commander_movement.py`, `docking.py`, `collision.py` |
| [economy.md](economy.md) | `resource_production.py`, `resource_pool.py`, `construction_economy.py` |
| [construction.md](construction.md) | `construction_session.py`, `robot_build.py`, `robot_stack.py`, `robot_launch.py`, `robot.py` |
| [movement.md](movement.md) | `movement.py`, `reservations.py`, `interactions.py`, `direct_control.py` |
| [navigation.md](navigation.md) | `navigation.py` |
| [orders-and-capture.md](orders-and-capture.md) | `orders.py`, `capture.py`, `autonomous_combat.py` |
| [combat.md](combat.md) | `combat.py`, `destruction.py`, `victory.py` |
| [ai.md](ai.md) | `ai/` package |
| [match-runtime.md](match-runtime.md) | `backend/app`: runtime loop, lobby, reconnect, replay, transport limits |

## Page convention

Every page has the same sections, in this order:

1. **Purpose** — what the mechanic is for.
2. **State involved** — the `GameState` fields and value types it reads and writes.
3. **Algorithm** — step by step, in tick order.
4. **Constants** — `EngineRules` field names (grep-able) and default values.
5. **Determinism notes** — ordering, RNG streams, memoization.
6. **Spectrum evidence** — disassembly labels from `netherearth-annotated.asm` (`santiontanon/netherearth-disassembly`).
7. **Deviations** — links into [deviations-from-original.md](../../_specs/deviations-from-original.md).
8. **Tests that pin it** — `file::test` names.

## Glossary

| Term | Meaning |
|---|---|
| tick | One authoritative simulation step, 50 ms at 20 Hz. `GameState.tick` counts them; `engine.step` advances it by exactly one. |
| game cycle | One iteration of the Spectrum's main loop, 5 per second: 4 ticks. Bullets advance, robots update and the AI decides on this grid. |
| cadence tick | A tick that is a positive multiple of a cadence constant (tick 0 never is): vertical physics, projectile advance. |
| in-game hour / day | 120 ticks / 2,880 ticks (`clock.TICKS_PER_GAME_HOUR`, `clock.TICKS_PER_GAME_DAY`). |
| anchor | A 2×2 unit's `(x, y)`: the min-x / max-y cell of a body covering `x..x+1`, `y−1..y`. |
| body | The four cells a unit at an anchor covers (`occupancy.unit_footprint_cells`). |
| robot update | The tick on which an order-driven robot may act: no move in flight and one move period since its last shot. |
| top | A robot's terrain altitude plus its stack height (`collision.robot_top`). |
| effective world | The base `WorldMap` with ownership overrides, destroyed structures and debris layered on (`destruction.effective_world`). |
| physical world | The base map with debris only (`destruction.scenery_world`): what robot moves and commander collision are checked against. |

## Rule constants

All in `engine/src/nether_earth/rules.py`, class `EngineRules`, instance `DEFAULT_RULES`. `RULES_VERSION` is `"cr005"`; `rules_content_hash` hashes every field below.

| Field | Default | Used by |
|---|---:|---|
| `commander_min_altitude` | 0 | commander |
| `commander_max_altitude` | 48 | commander |
| `commander_vertical_update_ticks` | 4 | commander |
| `commander_ascent_step` | 2 | commander |
| `commander_descent_step` | 2 | commander |
| `commander_height` | 4 | commander collision |
| `commander_horizontal_move_ticks` | 4 | commander |
| `commander_exit_elevate_updates` | 5 | commander lift |
| `module_height_bipod` / `module_height_tracks` / `module_height_anti_grav` | 11 / 7 / 8 | construction |
| `module_height_cannon` / `module_height_missile` / `module_height_phaser` / `module_height_nuclear` / `module_height_electronics` | 6 / 6 / 7 / 7 / 7 | construction |
| `starting_general_resources` | 20 | economy |
| `module_cost_bipod` / `module_cost_tracks` / `module_cost_anti_grav` | 3 / 5 / 10 | economy |
| `module_cost_cannon` / `module_cost_missile` / `module_cost_phaser` / `module_cost_nuclear` / `module_cost_electronics` | 2 / 4 / 4 / 20 / 3 | economy |
| `factory_production_amount` | 2 | economy |
| `war_base_production_amount` | 5 | economy |
| `max_robots_per_player` | 24 | construction |
| `robot_move_ticks_bipod_normal` / `robot_move_ticks_bipod_rough` | 24 / 32 | movement |
| `robot_move_ticks_tracks_normal` / `robot_move_ticks_tracks_rough` / `robot_move_ticks_tracks_mountain` | 16 / 24 / 28 | movement |
| `robot_move_ticks_anti_grav_normal` / `robot_move_ticks_anti_grav_rough` / `robot_move_ticks_anti_grav_mountain` / `robot_move_ticks_anti_grav_ditch` | 12 / 12 / 16 / 12 | movement |
| `capture_duration_ticks` | 1440 | capture |
| `cannon_range_cells` / `missile_range_cells` / `phaser_range_cells` | 10 / 14 / 10 | combat |
| `electronics_range_bonus_cells` | 2 | combat |
| `nuclear_robot_window_row_widths` | (5, 7, 9, 9, 9, 9, 9, 7, 5) | combat |
| `nuclear_building_dy_offset` | 1 | combat |
| `nuclear_war_base_extra_dy_offset` | 4 | combat |
| `nuclear_war_base_axis_limit` / `nuclear_war_base_sum_limit` | 7 / 10 | combat |
| `nuclear_factory_axis_limit` / `nuclear_factory_sum_limit` | 5 / 7 | combat |
| `normal_projectile_altitude` | 10 | combat |
| `cannon_damage_multiplier` / `missile_damage_multiplier` / `phaser_damage_multiplier` | 2 / 3 / 4 | combat |
| `projectile_advance_ticks` | 4 | combat |
| `projectile_cells_per_advance` | 2 | combat |
| `robot_turn_ticks` | 4 | movement, combat |
| `robot_fire_cycle_ticks` | 4 | combat |
| `robot_launch_exit_steps` | 5 | construction |
| `dumb_wander_commit_ticks` | 16 | navigation |
| `ai_decision_interval_ticks` | 4 | AI |
| `robot_hunt_replan_ticks` | 20 | navigation |

Unit definition (not a field): `CELLS_PER_MILE = 2`, through `miles_to_cells`.
