# M9.3 — Original-map geometry, interaction and ownership fidelity pass

Issue: #115. Evidence source: `santiontanon/netherearth-disassembly`
(`netherearth-annotated.asm`, tier 2 in the locked fidelity order), read
against `data/maps/zx-spectrum-original.yaml` and its evidence doc.
Automated checks: `engine/tests/test_m9_map_fidelity.py`,
`engine/tests/test_m9_scenario.py`.

| Item | Verdict | Evidence / note |
|---|---|---|
| Map dimensions 512×16 | verified | `MAP_LENGTH equ 512`, `MAP_WIDTH equ 16` |
| War-base anchors (22,9) (261,8) (369,8) (494,8) | verified | placement tables `Lbf46`/`Lbf6e` (already in M2 evidence doc) |
| Four war bases share one physical template; 24 factories share another | verified (structure) | `Lbd61_add_complex_structure_to_map` with `#80`/`#81`; asserted by shape test |
| Component heights 7/15, 60 / 20 components | evidence-based, unverified against emulator | decoded from template bytes (M2 doc); M9 asserts stability, not truth |
| Capture cells = structure anchor cell | verified | `Ladb7_building_loop` checks the anchor cell (M2 doc) |
| War-base **exit** = anchor cell | **verified (new)** | `Lcb52_construction_screen_start_robot`: robot placed at player y + 4 while the player sits on the pad at (anchor.x, anchor.y − 4) → anchor |
| War-base **heli-pad** | **verified** — resolved by CR001 (open-questions §18) | `Lbb86_assign_warbase_to_player` puts the "H" decoration at (anchor.x, anchor.y − 4); construction requires altitude 15 (`cp 15`) on it, i.e. the roof. Data places the pad there; the engine lands at the pad cell's component height |
| Commander spawn P1 (17,10) alt 0 | **verified (new)** | `La600_start` |
| Commander spawn P2 (499,9) alt 0 | locked PvP adaptation — owner decision (open-questions §17, CR001) | mirrored offset of P1; no Spectrum evidence (single-player original) |
| Starting ownership left/right, interior neutral | spec-locked | open-questions §2; `initialize_map` in the original assigns bases 1–3 to the AI, which the PvP scenario intentionally replaces |
| Starting resources 20 | verified | `INITIAL_PLAYER_RESOURCES equ 20` |
| Terrain rough/ditch | not decoded (M2 gap) | map is uniformly NORMAL; asserted so nothing unverified sneaks in |
| Scenery blockers | not decoded (M2 gap) | ~130 map elements in `Lbda9`/`Lbe79`; omitted |
| Commander clearance | verified against engine rule | grounded commander blocked by a 15-high component, clears it at altitude 15; max altitude 48 ≥ 15 |
| Spawn → roof pad reachability | verified on real map | BFS with `commander_horizontal_move_allowed` at roof altitude 15 (unreachable on the ground); the commander settles on the roof at 15 |

## Impact on the normal v1 path

No geometry defect blocks a normal match: both spawns are free ground next
to the owner's base, the pad/exit/capture cells are free ground, factories
are reachable along normal terrain, and the two neutral interior bases are
capturable. The two deviations (pad placement; missing terrain/scenery)
make the game *more* permissive than the original, never less.

## Corrections routed to owners (not patched in M9)

1. **M3 + M2 data** — roof-top heli-pad and altitude-15 landing (open-questions §18).
2. **M2 data** — terrain and scenery decode (already listed in the M2 evidence doc "Unknown / not attempted").
3. **Scenario data** — Player 2 spawn convention confirmation (open-questions §17).
