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

Later owner decisions (2026-09-21), recorded in the specs by CR002.16:

| Topic | Decision |
|---|---|
| Construction exit lift (PR #190) | Up/down moves during the lift do not shorten it (Spectrum quirk ignored). Leaving a robot gets the same 5-update lift (CR002.24). |
| Construction modality (PR #190) | Only the building player's commander is frozen; the PvP match keeps running (Spectrum pauses the whole game). |
| Fire cycles (PR #191) | One shot per robot per 4-tick game cycle; AI shots move 4 cells in their fire cycle, direct shots 2. AI robots fire only on their own robot update (CR002.19). |
| Bullet slots | Stay per robot (Spectrum shares 2+2+1 slots per side): documented deviation. |
| Bullet scan (PR #209) | Keep the engine's altitude gate; only robots stop bullets: documented deviation. |
| Launch walk-out (PR #209) | Match the original: 5 steps south on Stop & Defend. |
| Construction costs in the UI | Build-time copy of the engine rules, checked by CI; not sent in the protocol. |
| New stories | Nuke debris (CR002.18), AI fire on robot update (CR002.19), chassis swap (CR002.20), terrain heights (CR002.21), radar window (CR002.22), optional labels (CR002.23), undock lift (CR002.24), robots on terrain height (CR002.25). |
| Robots on terrain (PR #212) | Match the original: the terrain height under a robot raises its top, docking altitude, ship collision and drawing (CR002.25). |
| Radar | Shows only the viewer's own commander, as in the original; enemy robots are shown; white only. |
| Original artwork | Allowed while the repository and deployments are private; blocking release item before any public release or deployment (#201). |

## References

- `_specs/open-questions.md` §4, §8, §12–§14, §18
- `_specs/functional-spec.md`, `_specs/technical-spec.md`
- Reference screenshots of the original: [`cr002/construction-screen.png`](cr002/construction-screen.png) (robot construction screen), [`cr002/main-screen.png`](cr002/main-screen.png) (play view, status panel, radar)

## Dependencies

- Start: M10 merged and `v1.0.0-rc1` tagged (current `main`).
- Gameplay-rule changes (CR002.1–CR002.4, CR002.12, CR002.13, CR002.18–CR002.21, CR002.24, CR002.25) require one rules-version bump (`cr002`, CR002.16, the last gameplay PR of CR002); old replays are rejected, not mis-verified.
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
| CR002.16 (#183) | Rules version bump and spec updates for CR002 | CR002.1–CR002.4, CR002.12, CR002.13, CR002.18–CR002.21, CR002.24, CR002.25 |
| CR002.17 (#184) | CR002 acceptance gate | all |
| CR002.18 (#196) | Nuclear blast turns scenery boxes into rough debris; fences survive | CR002.1 |
| CR002.19 (#197) | AI robots fire only on their own robot update | CR002.2 |
| CR002.20 (#198) | Construction: picking another chassis swaps it | — |
| CR002.21 (#203) | Terrain piece heights for commander, projectiles and shadows | CR002.3, CR002.4, CR002.18 |
| CR002.22 (#205) | Radar: 128-column scrolling window, white only, own commander only | CR002.11 |
| CR002.23 (#206) | Structure labels and robot strength numbers: optional, default off | — |
| CR002.24 (#207) | Leaving a robot lifts the commander like leaving the war base | CR002.4, CR002.12 |
| CR002.25 (#214) | Robots stand on terrain height (drawing, robot top, docking, ship collision) | CR002.21, CR002.24 |
| — (#201) | Release gate: original-artwork licensing before any public release or deployment (release checklist item 16) | CR002.5, CR002.6 |

Parallel groups: engine (CR002.1, CR002.2, then CR002.3 → CR002.4, CR002.12, CR002.13) and frontend (CR002.6, CR002.7 → CR002.14, CR002.8, CR002.10 → CR002.9, CR002.11, CR002.5 after CR002.1). CR002.7 and CR002.14 both touch `projection.ts`/`renderer.ts`: merge one after the other.

## Acceptance gate (CR002.17)

- All engine, backend and frontend checks pass; M9 scripted full match and live two-client check updated and passing.
- Two-browser gateway run (`docs/milestone-10/browser-smoke.mjs`) passes.
- Owner visual review against the two reference screenshots.
- `open-questions.md` lists only the remaining research items (combat detail; fire-decision scan).

