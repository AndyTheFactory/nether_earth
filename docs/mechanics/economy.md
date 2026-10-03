# Economy

## Purpose

Players earn resources from the structures they own and spend them on robot modules. Spending follows the Spectrum: each module is paid from its own type-specific pool first and general resources cover only the shortfall.

## State involved

- `GameState.resource_pools`: one `resource_pool.PlayerResourcePool` per player (`general`, `chassis`, `electronics`, `cannon`, `missile`, `phaser`, `nuclear`), all non-negative integers, no upper bound.
- `construction_economy.ResourcePool`: the same seven numbers without a player id, the operand of the spend/refund functions and of a construction session's buffer.
- Structure ownership as seen through `destruction.effective_world` (captures applied, destroyed structures removed).

Each module identity has one resource category (`robot_build.resource_category`): the three chassis use `chassis`, each weapon its own pool, electronics `electronics`. Factory types use the same six categories.

## Algorithm

### Start of match

`resource_pool.starting_player_resource_pool` gives every player `starting_general_resources` general and 0 in every other pool.

### Daily production (`resource_production.apply_daily_production`, Step 9 of `engine.step`)

1. Compare `clock.ticks_to_game_days_floor` of the tick before and after the step. No change → nothing happens. (Ticks 2880, 5760, … are boundaries.)
2. For each player in canonical order, sum `war_base_production_amount` per owned war base into general, and `factory_production_amount` per owned factory into that factory's category, multiplied by the number of boundaries crossed (always 1 in a running match).
3. Credit the player's pool and emit one `DailyProductionApplied` per player who produced anything. Neutral and destroyed structures produce nothing.

Production reads ownership after this tick's captures and destruction.

### Spending (`construction_economy.spend_module`)

For a module of cost `c` and category `k` against pool `p`:

1. if `p[k] ≥ c`: `p[k] −= c`;
2. else the shortfall `s = c − p[k]` must be ≤ `p.general`, otherwise the spend is rejected (`INSUFFICIENT_RESOURCES`) with no partial deduction;
3. else `p[k] = 0`, `p.general −= s`.

### Refunds (`construction_economy.refund_module`)

Refunding a module restores its category up to the amount that category held when the construction session opened (`entry_snapshot`), and puts the rest into general: `restore = min(c, max(0, entry[k] − p[k]))`, `p[k] += restore`, `p.general += c − restore`. Selecting then deselecting a module therefore returns the pool exactly to where it was.

### Commit

Spending and refunds act on the session's temporary buffer only ([construction.md](construction.md)). The player's pool is replaced by the buffer when `robot_launch.launch_robot` succeeds; EXIT MENU (`CancelConstructionCommand`) drops the buffer and the pool is untouched.

Because the launch writes the buffer copied when the screen opened, production credited to the pool while the screen stays open is overwritten by the launch. Leaving with EXIT MENU keeps it. This is current behaviour, reported to the owner as a disagreement with the "commit atomically" rule; the computer seat is unaffected because it opens, fills and launches a session within one tick.

There is no cap on any pool; the computer seat's own spending limits are AI policy ([ai.md](ai.md)).

## Constants

| Name | Value |
|---|---:|
| `starting_general_resources` | 20 |
| `factory_production_amount` | 2 |
| `war_base_production_amount` | 5 |
| `module_cost_bipod` | 3 |
| `module_cost_tracks` | 5 |
| `module_cost_anti_grav` | 10 |
| `module_cost_cannon` | 2 |
| `module_cost_missile` | 4 |
| `module_cost_phaser` | 4 |
| `module_cost_nuclear` | 20 |
| `module_cost_electronics` | 3 |
| `clock.TICKS_PER_GAME_DAY` | 2880 |

The frontend shows the costs from `frontend/src/generated/rules/construction.json`, generated from these fields (`npm run rules:generate`); CI fails when it drifts.

## Determinism notes

All arithmetic is integer. Day boundaries are detected by integer floors, never floats. Players and structures are walked in canonical order, and the per-player sums are order-independent anyway.

## Spectrum evidence

- `INITIAL_PLAYER_RESOURCES: equ 20`.
- `Lca57_construction_add_piece` (spend own category, then general), `Lcac1_update_resources_buffer_when_removing_a_piece` (refund), `Lcb52_construction_screen_start_robot` (copy the buffer to the player on START ROBOT).

## Deviations

None. (In the Spectrum the game is paused while the construction screen is open, so no production can arrive during a session; see [Construction does not pause the match](../../_specs/deviations-from-original.md#construction-does-not-pause-the-match).)

## Tests that pin it

- `engine/tests/test_construction_economy.py::test_default_rules_match_locked_spectrum_construction_economy`
- `engine/tests/test_construction_economy.py::test_spend_shortfall_drawn_from_general_after_draining_type_pool`
- `engine/tests/test_construction_economy.py::test_spend_rejection_performs_no_partial_deduction`
- `engine/tests/test_construction_economy.py::test_refund_reversible_buffer_scenario_restores_up_to_pre_construction_then_general`
- `engine/tests/test_construction_economy.py::test_select_then_deselect_then_reselect_different_module_is_consistent`
- `engine/tests/test_resource_pool.py::test_starting_pool_seeds_general_only`
- `engine/tests/test_resource_production.py::test_production_fires_exactly_at_day_boundary`
- `engine/tests/test_resource_production.py::test_boundary_check_is_integer_exact_not_floating_point`
- `engine/tests/test_resource_production.py::test_neutral_factory_never_produces`
- `engine/tests/test_resource_production.py::test_different_type_factories_aggregate_independently`
- `engine/tests/test_engine_construction_integration.py::test_daily_production_applies_once_crossing_2880_tick_boundary`
- `engine/tests/test_construction_session.py::test_deselect_module_uses_entry_time_baseline_not_drifting`
- `engine/tests/test_construction_session.py::test_cancel_construction_leaves_actual_resources_byte_for_byte_unchanged`
