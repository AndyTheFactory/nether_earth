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
- **Terrain** (rough/ditch cell placement): left entirely at the schema
  default (`normal`) — see "Terrain" below for why.
- **Generic blockers/scenery** (boxes/cubes): omitted entirely — see
  "Blockers/scenery" below for why.

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

## Terrain — NOT ATTEMPTED (left at default)

`Lbda9_map_elements_part1` / `Lbe79_map_elements_part2` contain ~130 further
`(type, x, y)` map-element entries (rocks, ravines, other scenery — element
type bytes ranging roughly `0x02`-`0x12` plus several more `0x8x` complex
structures) at real coordinates across the whole map. This is real evidence
that a rich terrain/scenery layer exists, but decoding which element-type
numbers correspond to `ROUGH` vs `DITCH` vs decorative/solid scenery (as
opposed to war bases/factories, whose type-to-meaning mapping was directly
evidenced above) was not completed in this pass — it would require either
further disassembly tracing of how each element type affects movement/
collision, or emulator/screenshot cross-referencing. Rather than guess a
rough/ditch classification, `terrain.default: normal` is left as the only
terrain data in this map, and no `terrain.cells` overrides are present. This
is an explicit gap, not a claim that the original map is flat.

## Blockers/scenery — NOT ATTEMPTED (omitted)

For the same reason as terrain, the ~130 generic map-element entries above
are not translated into `blockers` entries: several of their type bytes are
also "complex structures" (indices 2-10 of
`Lbf9c_map_complex_structure_ptrs`, i.e. `Lbff4`, `Lc018`, `Lc03c`, etc.)
whose shapes I did not decode, and I don't have confirmed evidence
distinguishing solid/collidable scenery from purely decorative map dressing.
Per the milestone's explicit allowance to leave unsupported data out of the
YAML entirely, no `blockers` section is present in this map. This is a real
gap for a later ingestion pass, not a claim that the original map had no
scenery.

## Spawn positions

Not included. Named spawn/reference positions are scenario/overlay concerns
(`map_overlay.py`) built on top of this map's war-base anchor cells, not raw
map data with independent evidence of their own; the schema treats
`spawn_positions` as optional and M2's PvP overlay work (already merged) does
not require this map to declare any.

## Reproducibility

`data/maps/zx-spectrum-original.yaml` is deterministic, hand-derived data
(no code-generation step in the repo); regenerating it from the same
disassembly bytes as documented above will always produce the same file.
`engine/tests/test_original_map.py` asserts that loading it twice produces
canonical-equal `WorldMap` values.
