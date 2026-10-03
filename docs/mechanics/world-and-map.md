# World and map

## Purpose

Load the original ZX Spectrum battlefield as immutable data, apply the PvP scenario overlay, build the tick-0 state, and answer the static questions every other system asks: what terrain is here, how high is the surface, which cells does a structure occupy, where are the heli-pad, exit and capture cell.

## State involved

- `map.WorldMap` (immutable): `map_id`, `version`, `width`, `height`, `terrain` (`terrain.TerrainGrid`), `war_bases`, `factories`, `blockers` (`structures.WarBase` / `Factory` / `Blocker`, each a tuple of `Component(x, y, height)`), `interaction_points` (`interactions.InteractionPoint`), `spawn_positions`.
- `GameState.structure_ownership`, `structure_destruction`, `scenery_debris`, `robot_debris`: runtime changes layered on the map, never written into it.
- Units (robots, commanders, projectiles) are 2×2 bodies addressed by their anchor (`occupancy.py`).

## Algorithm

### Loading (`map.py`, `terrain.py`, `structures.py`, `interactions.py`)

1. `map.load_world_map(path)` reads `data/maps/zx-spectrum-original.yaml` (512 × 16, map version 1): terrain cells with class and piece height, 4 war bases, 24 factories, 165 blockers, 36 interaction points.
2. `terrain.parse_terrain_grid` builds the grid: every cell defaults to `NORMAL` at height 0; listed cells carry `ROUGH` (height 2 or 3), `MOUNTAIN` (6) or `DITCH` (0). `terrain.debris_height` is 3.
3. Structures and blockers are parsed into components; blockers carry an opaque `kind` (`"box_low"` 7, `"box_high"` 15, `"fence"` 99) and `destructible` (boxes only). The engine never branches on `kind`.
4. Interaction points are typed `HELI_PAD`, `EXIT` (war bases only), `WARBASE_CAPTURE` and `FACTORY_CAPTURE`. On the original map each war base has a one-cell capture point and a one-cell exit on the same anchor cell, and a four-cell heli-pad at (anchor.x, anchor.y − 4) on its 15-high roof; each factory has one capture cell.
5. Ids must be globally unique, every component and footprint in bounds, and no two static components may share a cell (`occupancy.OccupancyGrid` raises `OccupancyConflictError`).

### Scenario overlay (`map_overlay.py`)

`map_overlay.default_pvp_overlay(world)` picks the extreme-left war base (smallest min-x, ties by id) for Player 1 and the extreme-right one (largest max-x) for Player 2, and leaves every other war base and every factory neutral by not mentioning it. Commander spawns are the war base's capture cell + (−5, +1) for Player 1 and + (+5, +1) for Player 2, clamped to the map: (17, 10) and (499, 9) on the original map. `map_overlay.apply_overlay` returns a new `WorldMap` with that ownership and those spawns; the backend's `load_standard_world` applies it once per process.

### Initial state (`scenario.py`)

`scenario.create_initial_state(scenario, world, seed=…)` builds tick 0:

- players in canonical order (`initialize_players`);
- one free commander per *human* seat at its spawn cell, altitude `commander_min_altitude`; an AI seat (`player_two_controller = "ai"`) gets an `AiMemory` and no commander;
- one resource pool per player with `starting_general_resources` general and 0 in every type-specific pool;
- one `StructureOwnership` record per owned structure, so `capture.effective_world` is the identity at tick 0.

### 2×2 bodies (`occupancy.py`)

- `unit_footprint_cells(x, y)` = `(x, y)`, `(x+1, y)`, `(x, y−1)`, `(x+1, y−1)`.
- `unit_footprints_overlap(ax, ay, bx, by)` is true when `|ax − bx| ≤ 1` and `|ay − by| ≤ 1`: the Spectrum's 3×3 window of anchors.
- `unit_footprint_in_bounds` keeps the whole body on the map: `0 ≤ x ≤ width − 2`, `1 ≤ y ≤ height − 1`.
- `OccupancyGrid` maps each occupied cell to one entity; `blocks_unit(x, y, ignore=…)` tests a whole body.

### Surface heights (`collision.py`)

- `surface_height_at(world, x, y)`: the higher of the terrain piece height and the highest static component on the cell; 0 off the map. Component heights are folded once per world and memoized.
- `unit_surface_height(world, x, y)`: the maximum over a body's four cells — the Spectrum's `Lb5d6_map_altitude_2x2`.
- `robot_top(world, robot)`: `unit_surface_height` at the robot's authoritative anchor plus its stack height.

These three functions are the only source of "how high is it here" for commander collision and landing, projectile termination, damage, robot altitude and the heli-pad.

### Derived worlds (`capture.py`, `destruction.py`)

| Function | Adds | Used for |
|---|---|---|
| `capture.effective_world` | ownership overrides | ownership reads that must not see destruction |
| `destruction.effective_world` | ownership, destroyed structures removed, all debris | orders, capture, combat, production, victory, construction |
| `destruction.scenery_world` | debris only (no ownership) | robot move validation, commander collision, robot altitude |

Debris (`destruction._apply_debris`): a destroyed structure or a destroyed box is removed and every cell it covered becomes `ROUGH` at `terrain.debris_height`; every anchor in `GameState.robot_debris` turns its 2×2 into the same. Results are memoized on the identity of the input world and the debris tuples, so the same tick reuses one world object.

### Heli-pad (`heli_pad.py`)

`detect_heli_pad_landing` fires for a free commander when the player owns the war base, every cell of the commander's body is inside that war base's `HELI_PAD` footprint, and its altitude equals `heli_pad_surface_altitude` (the highest component under the body, never below `commander_min_altitude`): 15 on the original roof. The event is `CommanderConstructionEntryEligible`; Step 7 of `engine.step` turns it into a construction session ([construction.md](construction.md)).

## Constants

| Name | Value |
|---|---:|
| map size | 512 × 16 (map data) |
| `terrain.debris_height` | 3 (map data) |
| piece heights | rough 2/3, mountain 6, normal/ditch 0 (map data) |
| `commander_min_altitude` | 0 |
| `starting_general_resources` | 20 |
| spawn offset | (−5, +1), mirrored for Player 2 (`map_overlay._SPAWN_OFFSET_FROM_ANCHOR`) |

## Determinism notes

The map is parsed once into frozen values; structures keep their declared order (the Spectrum's building-index order) and every lookup that matters sorts by id. Derived worlds are pure functions of `(world, state)` fields.

## Spectrum evidence

- `Ld7bc_map_piece_heights` — piece heights per element type.
- `Lbd91_add_element_to_map` — 2×2 elements stamped from the min-x/max-y anchor.
- `Lbd61_add_complex_structure_to_map`, `Lbfb2_warbase`, `Lbfe2_factory` — building compositions (element types 15/16, heights 7/15).
- `Lbb86_assign_warbase_to_player` — "H" pad at (anchor.x, anchor.y − 4); `La6c8` enters construction at altitude 15.
- `La600_start` — Player 1's ship starts at (17, 10), altitude 0.
- Provenance of every decoded cell: `data/maps/zx-spectrum-original.md`.

## Deviations

None. Player 2's mirrored spawn and the neutral interior war bases are PvP adaptations ([resolved-questions.md](../../_specs/resolved-questions.md#world-and-map)).

## Tests that pin it

- `engine/tests/test_original_map.py::test_original_map_terrain_class_counts_match_decoded_spectrum_map`
- `engine/tests/test_original_map.py::test_original_map_occupancy_has_no_conflicts`
- `engine/tests/test_m9_map_fidelity.py::test_map_dimensions_match_the_original`
- `engine/tests/test_m9_map_fidelity.py::test_each_war_base_declares_one_capture_one_exit_on_free_ground_and_a_roof_heli_pad`
- `engine/tests/test_m9_map_fidelity.py::test_commander_settles_on_the_roof_pad_at_landing_altitude`
- `engine/tests/test_scenery_blockers.py::test_original_map_encodes_the_660_decoded_scenery_cells_as_2x2_blockers`
- `engine/tests/test_scenery_blockers.py::test_fences_close_both_ends_of_the_map`
- `engine/tests/test_terrain_heights.py::test_original_map_piece_heights_per_class`
- `engine/tests/test_terrain_heights.py::test_one_surface_height_for_terrain_and_structures`
- `engine/tests/test_map_overlay.py::test_default_pvp_overlay_assigns_extreme_left_and_right_leaves_middle_neutral`
- `engine/tests/test_map_overlay.py::test_default_pvp_overlay_pins_locked_commander_spawns_on_original_map`
- `engine/tests/test_m9_scenario.py::test_starting_ownership_is_left_right_with_two_neutral_interior_bases`
- `engine/tests/test_unit_footprint_2x2.py::test_body_is_the_anchor_its_right_neighbour_and_the_row_above`
- `engine/tests/test_unit_footprint_2x2.py::test_bodies_overlap_exactly_within_the_3x3_anchor_window`
- `engine/tests/test_unit_footprint_2x2.py::test_anchor_bounds_keep_the_whole_body_on_the_map`
- `engine/tests/test_heli_pad.py::test_original_map_roof_pad_lands_at_15_not_at_the_anchor_on_the_ground`
- `engine/tests/test_heli_pad.py::test_a_body_only_partly_over_the_pad_does_not_trigger`
- `engine/tests/test_ai_seat.py::test_ai_seat_starts_with_memory_and_no_commander`
