# `zx-spectrum-original.yaml` — evidence and provenance

This document records, for every value committed to `data/maps/zx-spectrum-original.yaml`,
what tier of evidence supports it, per the fidelity priority in `AGENTS.md`:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence (`santiontanon/netherearth-disassembly`)
3. original ZX Spectrum instructions/manual (ESP32 Rainbow)
4. observed gameplay recordings / the speccy.cz map image
5. other ports/remakes (not used for any value here)

Issue: #25 (M2.7). Milestone: `_specs/milestones/02-map-world-model.md`.

## Summary — needs human review before merge

Per the milestone's human-review gate ("Any inferred physical component, occupied
cell, height, or interaction coordinate that would become authoritative gameplay
data requires owner approval if source evidence is ambiguous or conflicting."),
the following are **not** independently/emulator-verified and require owner
sign-off before being treated as final authoritative geometry:

- **War-base and factory component/cell composition** (the full multi-cell
  shape + per-cell height of every `war_bases[*].components` and
  `factories[*].components` entry). These are *derived* from real disassembly
  bytes by me statically decoding the game's "complex structure" interpreter
  (see "War-base and factory composition" below) — this is tier-2 evidence in
  origin, but the decode itself has not been cross-checked against an emulator
  or a screenshot, so treat the exact cell/height values as **evidence-based
  but unverified reconstruction**, not observed fact.
- **Heli-pad and exit interaction points** (`*-helipad`, `*-exit` in
  `interaction_points`): resolved by `_specs/open-questions.md` §18 (CR001).
  The heli-pad is on the war-base roof at (anchor.x, anchor.y − 4) (a 15-high
  component on every war base); the exit is the anchor cell. See
  "Heli-pad / exit interaction points" below.
- **Terrain**: decoded from the disassembly in CR001.5 (#152) — see
  "Terrain" below. Tier-2 evidence, spot-checked against the speccy.cz image.
- **Blockers/scenery** (boxes and fences): decoded from the disassembly in
  CR002.1 (#168). See "Blockers/scenery" below. Tier-2 evidence.

Everything else below (map dimensions, war-base/factory count, anchor
coordinates, factory production types, and the factory/warbase capture
interaction points) is directly read from disassembly constants/data tables
and is high-confidence tier-2 evidence.

## Sources consulted

- `https://github.com/santiontanon/netherearth-disassembly`, specifically
  `netherearth-annotated.asm` (fetched and grepped directly; quotes below are
  verbatim from that file as of this ingestion).
- `https://www.esp32rainbow.com/games/3391` — fetched, but the page returned
  no usable instruction/rules text (title only); it did not contribute any
  map data to this ingestion. Noted as a source-access limitation, not as
  "no map in the instructions."
- `https://maps.speccy.cz/maps/NetherEarth.png` — fetched and viewed directly
  (not machine-OCR'd). Confirms, as tier-4 visual evidence only: a long
  diagonal battlefield strip with 4 distinguishable base clusters — one at
  each extreme end (colored differently, consistent with player-owned ends)
  and two in the interior — which corroborates (but does not by itself prove
  coordinates for) the "4 war bases, 2 extreme + 2 interior" shape already
  established from the disassembly. No reliable per-cell terrain or factory
  detail could be read from the image at its resolution; no coordinates were
  taken from it.
- `https://www.moddb.com/downloads/netherearthvox-094` — explicitly excluded
  per `_specs/references.md`; not consulted for any gameplay-fidelity value.

## Map dimensions — VERIFIED (tier 2)

`netherearth-annotated.asm`:

```
MAP_LENGTH: equ 512  ; x coordinate
MAP_WIDTH: equ 16  ; y coordinate
```

`Ldd00_map: equ #dd00 ; map: 512*16 = 8192 bytes` confirms the same 512x16
grid. Used directly as `width: 512`, `height: 16`.

## War-base and factory counts — VERIFIED (tier 2)

```
N_WARBASES: equ 4
N_FACTORIES: equ 24
```

## War-base and factory anchor coordinates — VERIFIED (tier 2)

The map's real war-base/factory placement table (comment: "Warbases and
factories: 0 are warbases, and 1 - 6 are factories"):

`Lbf46_warbases_factories_part1` (x < 256) and `Lbf6e_warbases_factories_part2`
(x >= 256, encoded as `x - 256` in the table, per the `d` flag passed to
`Lbcf9_add_warbases_and_factories_to_map`) together contain exactly 4 entries
of type `0` (warbase) and 24 entries of type `1`-`6` (factory), each as a
`(type, x, y)` triple. These are the coordinates stored into
`Lfd70_warbases`/`Lfd84_factories` (`BUILDING_STRUCT_X`/`BUILDING_STRUCT_Y`),
i.e. this is the actual runtime building-position data, not decoration.

Decoded anchors (sorted left-to-right by x, matching `warbase-1..4` /
`factory-1..24` ids in the YAML):

| id | x | y |
|---|---|---|
| warbase-1 | 22 | 9 |
| warbase-2 | 261 | 8 |
| warbase-3 | 369 | 8 |
| warbase-4 | 494 | 8 |

Extreme-left (x=22) and extreme-right (x=494) with two interior war bases —
consistent with the resolved `_specs/open-questions.md` §2 model this map
must support, and with the speccy.cz map image's 4 visually distinct base
clusters.

Factory anchors (24 total; full list embedded as `factories[*].components`'
anchor cell, i.e. each factory's `factory_capture` interaction point, in the
YAML) were decoded the same way from the same two tables.

## Factory production type — VERIFIED (tier 2)

Each factory/warbase table entry's type byte (`1`-`6` for factories) is the
"factory type" used elsewhere in the game code. The mapping from that number
to a production category is directly evidenced by:

```
; Which factory type produces resources for each piece:
; - bipod, tracks, anti-grav are all produced in the "chassis" factory types (6), whereas the other
;   pieces have dedicated factories for themselves.
Lcaf8_piece_factory_type:
    db 6, 6, 6, 5, 4, 3, 2, 1
```

read against the piece order given immediately above it in the same file
(`Lcaf0_piece_costs` comment: "bipod, tracks, anti-grav, cannon, missiles,
phasers, nuclear, electronics"). This yields:

| type byte | production category |
|---|---|
| 1 | electronics |
| 2 | nuclear |
| 3 | phaser |
| 4 | missile |
| 5 | cannon |
| 6 | chassis |

Applied directly to each factory's `factory_type` field. Resulting
distribution across the 24 factories: chassis 5, missile 4, electronics 4,
cannon 4, phaser 4, nuclear 3 (uneven — this is the real distribution, not a
rounding artifact; no attempt was made to "balance" it).

## Factory-capture / war-base-capture interaction points — VERIFIED (tier 2)

The building-capture loop (`Ladb7_building_loop` in the annotated source)
checks occupancy at exactly the building's own registered anchor cell:

```
ld l, (iy + BUILDING_STRUCT_X)
ld h, (iy + BUILDING_STRUCT_X + 1)
ld a, (iy + BUILDING_STRUCT_Y)
call Lcca6_compute_map_ptr
bit 6, (hl)  ; check if building is still there
...
; Something has been in front of the factory for BUILDING_CAPTURE_TIME cycles, capture!
```

iterating over both `Lfd70_warbases` and `Lfd84_factories` (`b = N_WARBASES +
N_FACTORIES`) with the same code path for both kinds. This directly
evidences that the capture-check location for both a war base and a factory
is that structure's own anchor `(x, y)` cell — so `factory_capture` and
`warbase_capture` interaction points use each structure's anchor cell
verbatim, not an inferred offset.

## War-base and factory composition — EVIDENCE-BASED BUT UNVERIFIED (see summary)

`Lbf9c_map_complex_structure_ptrs` indexes 11 "complex structure" shapes by
type; index 0 (`Lbfb2_warbase`) is used for every warbase instance (the
placement code always calls `add a, #80` before invoking
`Lbd61_add_complex_structure_to_map` for a type-0 entry) and index 1
(`Lbfe2_factory`) for every factory instance regardless of its production
type (`add a, #81`) — i.e. **all 4 war bases share one physical shape
template, and all 24 factories share a different single shape template**;
composition does not vary by production type. This one-shared-template-per-
kind structure is itself tier-2 evidence, directly read from the code.

The template bytes for each kind are a sequence of `(type, dx, dy)` triples,
interpreted by `Lbd61_add_complex_structure_to_map` / `Lbd91_add_element_to_map`
/ `Lbd7f_add_map_ptr_offset`: starting at the structure's anchor, each entry
(if `type != 0`) stamps a 2x2 tile block of that `type` at the *current*
running position, then (unless `dx == dy == 0`, which terminates the list)
advances the running position by `(dx, dy)` before the next entry. I decoded
this control flow directly from the annotated disassembly (quoted above in
"Factory-capture..." section context; the loop itself is at
`Lbd61_add_complex_structure_to_map` et seq.) and applied it by hand/script
to the literal template bytes:

```
Lbfb2_warbase:
    db #00,#fc,#fc, #10,#00,#fe, #10,#02,#05, #0f,#00,#fe, #0f,#00,#fe,
       #0f,#00,#fe, #10,#02,#05, #0f,#00,#fe, #10,#00,#fe, #10,#02,#05,
       #0f,#00,#fe, #0f,#00,#fe, #0f,#00,#fe, #10,#02,#03, #10,#00,#fe,
       #10,#00,#00
Lbfe2_factory:
    db #00,#fe,#00, #0f,#00,#fe, #10,#02,#00, #10,#02,#00, #10,#00,#02,
       #0f,#00,#00
```

Per-cell height comes from `Ld7bc_map_piece_heights` (a verified 23-element
table), indexed by the same `type` byte used in the template
(`type 0x0f (15) -> height 7`, `type 0x10 (16) -> height 15`):

```
Ld7bc_map_piece_heights:  ; 23 elements
    db #00,#00,#02,#02,#02,#02,#03,#03,#06,#06,#06,#06,#00,#00,#00,#07,
       #0f,#07,#0f,#00,#00,#63,#00
```
(index 15 = `#07` = 7, index 16 = `#0f` = 15 — confirmed by counting from
index 0.)

Decoding this template against each of the 4 war-base anchors yields 60
distinct `(x, y, height)` components per war base (all within map bounds; no
overlap conflicts with any other structure — confirmed by `load_world_map`
loading the map without an `OccupancyConflictError`). Decoding the factory
template against each of the 24 factory anchors yields 20 components per
factory, similarly bounds-checked and overlap-free.

**Why this is flagged "needs review" despite being byte-accurate:** I wrote
and ran my own interpreter for this bytecode rather than tracing it in a
live emulator or comparing the result against a rendered screenshot/tile
map. The byte values quoted above are verbatim from the fetched disassembly
file (spot-checked with `grep` against the raw file, not just an AI
paraphrase of it), and the decode logic mirrors the annotated Z80 exactly as
written, but I have not independently confirmed the resulting cell/height
shapes visually match the original game. Owner review (or an emulator-based
cross-check) is the appropriate next step before treating this composition
as final authoritative gameplay geometry, per the milestone's human-review
gate.

## Heli-pad / exit interaction points — RESOLVED (open-questions.md §18)

`Lbb86_assign_warbase_to_player` places the war-base "H" decoration at
(anchor.x, anchor.y − 4), and the game loop enters construction only when the
ship is over that decoration at altitude exactly 15 (`cp 15`) — the roof of
the 15-high war-base block. The robot then starts at (pad.x, pad.y + 4), the
anchor cell (`Lcb52_construction_screen_start_robot`). Accordingly each
`*-helipad` point sits at (anchor.x, anchor.y − 4) and each `*-exit` point at
the anchor cell. The engine's landing rule is "altitude equals the pad cell's
component height" (`engine/src/nether_earth/heli_pad.py`).

## Terrain — DECODED (tier 2; CR001.5, issue #152)

`data/maps/decode_zx_terrain.py` rebuilds the map buffer exactly as
`Lbc6f_initialize_map` does and writes the `terrain.cells` block of the YAML:

1. `Lbcd6_add_elements_to_map` walks `Lbda9_map_elements_part1` (x < 256) and
   `Lbe79_map_elements_part2` (x = byte + 256), `(type, x, y)` triples ending
   in `0`. A type with the msb set is a complex structure
   (`Lbf9c_map_complex_structure_ptrs[type & #7f]`, stamped by
   `Lbd61_add_complex_structure_to_map`); any other type is one element.
2. `Lbd91_add_element_to_map` writes an element as a 2x2 block covering
   `x..x+1`, `y-1..y` (two bytes per row, then `dec h; dec h` = one row up).
3. `Lbcf9_add_warbases_and_factories_to_map` then stamps war bases and
   factories over the terrain, so a structure cell never carries terrain.
4. Each cell's element type index (`and #1f`, as `Lb513` and
   `Ld7bc_map_piece_heights` read it) is classified per
   `_specs/open-questions.md` §4: 0–1 normal, 2–7 rough, 8–11 mountain,
   12–14 ditch. Only non-normal cells are written.

**Self-check.** The same decode yields exactly the 720 type-15/16 structure
cells (4 war bases × 60 + 24 factories × 20) already in the YAML, with the same
heights (15 → 7, 16 → 15). This confirms the 2x2 stamping direction and the
coordinate convention used here. No element stamps outside the 512 × 16 map.

**Cell counts** (8192 cells in all):

| Class | Element types present | Cells |
|---|---|---|
| normal (default) | 0 (type 1 is never placed) | 5828 |
| rough | 2: 80, 3: 88, 4: 84, 5: 68, 6: 12, 7: 12 | 344 |
| mountain | 8: 124, 9: 108, 10: 96, 11: 108 | 436 |
| ditch | 12: 48, 13: 108, 14: 48 | 204 |
| war base / factory | 15: 304, 16: 416 | 720 |
| scenery (blockers, see below) | 17: 324, 18: 272, 21: 64 | 660 |

`engine/tests/test_original_map.py` pins the rough/mountain/ditch counts and
checks that every exit and capture cell can be reached from every war-base exit
by every chassis.

**Spot checks against `https://maps.speccy.cz/maps/NetherEarth.png`**
(fetched and viewed at full resolution; tier-4 visual evidence). In the image
the strip runs left to right in increasing x, and y increases towards the blue
edge.

- x 32–55, y 7–14 (east of war base 1): the decode gives a rough field
  around a mountain core (x 44–53, y 9–14). The image shows a dotted
  rough patch on the blue-edge side with peaked mountains inside it.
- x 72–73, y 1–8 and x 83–84, y 7–14: the decode gives two ditches across the
  strip, the first on the far side and the second on the blue-edge side. The
  image shows two dark diagonal trenches in the same order and on the same
  sides.
- x ≤ 254, y 9–14 and x 264–281 (war base 2 surroundings): the decode gives a
  rough field west of war base 2, then two mountain blocks east of it (x 270–277,
  y 1–6 and x 264–281, y 9–14). The image shows a dotted field west of the red
  base and two peaked blocks east of it, with the larger one on the blue-edge
  side.

The image is not precise enough to confirm individual cells. The cell-level
values rest on the disassembly decode.

## Blockers/scenery — DECODED (tier 2; CR002.1, issue #168)

`python data/maps/decode_zx_terrain.py <netherearth-annotated.asm> blockers`
runs the same decode as "Terrain" and writes the `blockers` section. There is
one entry per scenery element, i.e. per 2x2 stamp of `Lbd91_add_element_to_map`
with element type 17, 18 or 21. The decoder tracks which stamp wrote each cell
last. No scenery element is partly overwritten by a later stamp: all 165
survive whole, 4 cells each, 660 cells in all.

| Element type | `kind` | Height (`Ld7bc_map_piece_heights`) | Elements | Cells |
|---|---|---|---|---|
| 17 (`#11`) | `box_low` | 7 | 81 | 324 |
| 18 (`#12`) | `box_high` | 15 | 68 | 272 |
| 21 (`#15`) | `fence` | 99 (`#63`) | 16 | 64 |

- **Names.** Only "fence" comes from the code. The nuclear-blast loop
  (`Lba44_robots_handled`) skips type 21 with the comment "do not destroy the
  fences that mark the end of the map in each end". The 16 fence elements
  fill columns 12–13 and 503–504, just outside the ship's x limits
  (`MIN_PLAYER_X` 14, `MAX_PLAYER_X` 501). `box_low` and `box_high` are
  descriptive labels. The disassembly gives these types no names. They are
  placed singly and as walls by the complex structures `Lc03c`/`Lc048` (four
  type-18 elements), `Lc078` (four type-17) and `Lc084` (17, 18, 17).
- **`kind` is data.** The engine reads the physical effect from each
  component's cell and height only. `kind` is an opaque label that the
  frontend maps to an asset (CR002.5).
- **Rules** (`_specs/open-questions.md` §4, "Scenery blockers"):
  - robots of every chassis are blocked (`Lb513`/`Lb5cd`: type ≥ 8/12/15);
  - the commander crosses a box at altitude ≥ its height and rests on top of
    it (`Lb052_check_player_collision`, `Lafc3_gravity`). It never crosses a
    fence, because 99 > `MAX_PLAYER_ALTITUDE` 48;
  - bullets (altitude 10) fly over `box_low` and stop at `box_high` and
    `fence` (`Lb724_bullet_update_internal`).

Visual cross-check: the CR001.5 review saw solid blocks at these positions in
the speccy.cz image (tier 4). They were not rechecked cell by cell, and the
image is too coarse for that (see "Terrain").

## Spawn positions

Not included. Named spawn/reference positions are scenario/overlay concerns
(`map_overlay.py`) built on top of this map's war-base anchor cells, not raw
map data with independent evidence of their own; the schema treats
`spawn_positions` as optional and M2's PvP overlay work (already merged) does
not require this map to declare any.

## Reproducibility

`data/maps/zx-spectrum-original.yaml` is deterministic data. The structures
and interaction points were derived by hand. The `terrain.cells` block is the
output of `python data/maps/decode_zx_terrain.py <netherearth-annotated.asm>`,
and the `blockers` section is the output of the same command with the extra
argument `blockers`. Both must be regenerated, not edited by hand. Running either derivation again
on the same disassembly bytes always gives the same file.
`engine/tests/test_original_map.py` asserts that loading it twice produces
canonical-equal `WorldMap` values.
