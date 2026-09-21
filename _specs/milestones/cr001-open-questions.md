# CR001 — Open Questions Resolution

## Goal

Implement the owner decisions of 2026-09-21 that resolve `_specs/open-questions.md` §4, §8, §17, §18, §19, and the new §20. Each decision replaces a placeholder or a previously locked value with behavior evidenced by the ZX Spectrum disassembly.

## Spec references

- `_specs/open-questions.md` §3, §4, §8, §17, §18, §19, §20 (evidence and decisions)
- `_specs/functional-spec.md` §7.1, §7.2, §8, §13, §16, §17.3, §19
- `_specs/technical-spec.md` §7.3, §13, §18, §26

## Dependencies

- Start: M9 merged (current `main`).
- This change request modifies behavior delivered by M2, M3, M5, and M6. It must be complete before the M9.8 gate (#120) and before M10 release work that freezes rules.

## Summary of changes

| Area | Before | After |
|---|---|---|
| Autonomous nuclear use (§19) | any autonomous order may detonate; no range gate | only on arrival at the target cell of a Search & Destroy factory/war-base order |
| Nuclear blast (§20) | uniform 16-cell radius; every structure inside destroyed | robots: 9×9 trimmed window; buildings: per-kind shape, at most one per blast |
| Projectiles (§8) | 1 cell per 4 ticks; ranges 20/28/20 cells, +6 electronics | 2 cells per 4 ticks; ranges 10/14/10 cells, +2 electronics |
| Terrain (§4) | normal/rough/ditch; rough ×3/×2/×1 multipliers | normal/rough/mountain/ditch; per-(chassis, terrain) tick table |
| Map terrain (§4) | none encoded | rough/mountain/ditch cells decoded from the original map |
| Heli-pad (§18) | ground level at the anchor | roof at (anchor.x, anchor.y − 4); land at the pad cell's height |
| P2 spawn (§17) | provisional | locked mirror of P1 |

## Tasks

Tracker: #157.

| ID | Task | Depends on |
|---|---|---|
| CR001.1 (#148) | Autonomous nuclear use only on Search & Destroy structure arrival | — |
| CR001.2 (#149) | Nuclear blast shapes from the Spectrum code | — |
| CR001.3 (#150) | Projectile speed 2 cells/advance and code-derived ranges | — |
| CR001.4 (#151) | Terrain model: `MOUNTAIN` class and per-(chassis, terrain) tick table | — |
| CR001.5 (#152) | Decode original map terrain into `data/maps` | CR001.4 |
| CR001.6 (#153) | Roof-top war-base heli-pad | — |
| CR001.7 (#154) | Lock Player 2 spawn convention | — |
| CR001.8 (#155) | Engine-owned rules version and content hash; bump for CR001 | — (final bump after CR001.1–CR001.7) |
| CR001.9 (#156) | CR001 acceptance gate | all |

CR001.1, CR001.2, CR001.3, CR001.4, CR001.6, and CR001.7 can run in parallel. CR001.1 and CR001.3 both touch `autonomous_combat.py`, so merge them one after the other and rebase the second.

## Acceptance gate

- All engine, backend, and frontend checks pass.
- The M9 scripted full match and the live two-client check pass under the new rules, with no tolerated autonomous nuclear self-destruct.
- Replays record the new rules version; replays recorded under the old version are rejected with a clear error, not silently mis-verified.
- `open-questions.md` lists only the remaining research items (combat detail; fire-decision scan).
