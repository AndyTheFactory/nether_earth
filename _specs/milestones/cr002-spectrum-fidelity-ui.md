# CR002 — Spectrum Fidelity & UI

## Goal

Implement the owner change request of 2026-09-21: close the two pending owner decisions from CR001 (scenery blockers, projectile first move), give robots, the commander and the heli-pad their Spectrum 2×2 size, fix the war-base construction flow, and bring the look and feel close to the ZX Spectrum original (orientation, zoom, fonts, radar, construction screen, ownership flags, occlusion, shadows).

## Owner decisions (2026-09-21)

| Topic | Decision |
|---|---|
| Scenery blockers (`open-questions.md` §4) | Blockers exist. Wall/box assets are chosen per blocker kind through configuration; v1 ships only the Spectrum assets (3–4 variants), 2×2 in this version. |
| Projectile first move (§8) | Match the Spectrum: first 2-cell move on the fire tick. |
| Map version gap | Keep `zx-spectrum-original` at `version: 1`; Spectrum map matching is a separate story. |
| Release | Tag `v1.0.0-rc1` now; the production human two-player check (release checklist item 11) is postponed to a later story; no rollback target exists for the first release. |
| Footprints | Robots, the commander and the heli-pad are 2×2 (physical and visual; conventions evidenced from the disassembly). |

## References

- `_specs/open-questions.md` §4, §8, §12–§14, §18
- `_specs/functional-spec.md`, `_specs/technical-spec.md`
- Reference screenshots of the original: [`cr002/construction-screen.png`](cr002/construction-screen.png) (robot construction screen), [`cr002/main-screen.png`](cr002/main-screen.png) (play view, status panel, radar)

## Dependencies

- Start: M10 merged and `v1.0.0-rc1` tagged (current `main`).
- Gameplay-rule changes (CR002.1–CR002.4, CR002.12, CR002.13, CR002.18) require one rules-version bump (CR002.16); old replays are rejected, not mis-verified.
- Where the disassembly does not settle a detail (anchor cells, flight over walls, post-launch state), stop and ask the owner instead of choosing.

## Tasks

Tracker: #185.

| ID | Task | Depends on |
|---|---|---|
| CR002.1 (#168) | Scenery blockers: decoded boxes/walls block movement (§4) | — |
| CR002.2 (#169) | Projectile makes its first move on the fire tick (§8) | — |
| CR002.3 (#170) | Robots occupy 2×2 cells | CR002.1 (shared occupancy code) |
| CR002.4 (#171) | Commander and heli-pad are 2×2 | CR002.3 |
| CR002.5 (#172) | Wall/box assets: 2×2 sprites chosen through a configurable asset mapping | CR002.1 |
| CR002.6 (#173) | Ownership flag on factories and war bases (left/right by owner) | — |
| CR002.7 (#174) | Isometric view from lower-left to upper-right | — |
| CR002.8 (#175) | Zoom the viewport in | — |
| CR002.9 (#176) | Full-screen Spectrum robot-construction screen | CR002.10; exit semantics from CR002.12/CR002.13 |
| CR002.10 (#177) | Spectrum fonts | — |
| CR002.11 (#178) | Mini radar | — |
| CR002.12 (#179) | War-base menu: launching or cancelling ejects the commander upwards | — |
| CR002.13 (#180) | Cannot exit the war-base construction menu | — |
| CR002.14 (#181) | Occlusion: units behind structures are hidden | CR002.7 |
| CR002.15 (#182) | Commander shadow ignores the height of buildings under it | CR002.4 |
| CR002.16 (#183) | Rules version bump and spec updates for CR002 | CR002.1–CR002.4, CR002.12, CR002.13, CR002.18 |
| CR002.17 (#184) | CR002 acceptance gate | all |
| CR002.18 (#196) | Nuclear blast turns scenery boxes into rough debris; fences survive | CR002.1 |

Parallel groups: engine (CR002.1, CR002.2, then CR002.3 → CR002.4, CR002.12, CR002.13) and frontend (CR002.6, CR002.7 → CR002.14, CR002.8, CR002.10 → CR002.9, CR002.11, CR002.5 after CR002.1). CR002.7 and CR002.14 both touch `projection.ts`/`renderer.ts`: merge one after the other.

## Acceptance gate (CR002.17)

- All engine, backend and frontend checks pass; M9 scripted full match and live two-client check updated and passing.
- Two-browser gateway run (`docs/milestone-10/browser-smoke.mjs`) passes.
- Owner visual review against the two reference screenshots.
- `open-questions.md` lists only the remaining research items (combat detail; fire-decision scan).

