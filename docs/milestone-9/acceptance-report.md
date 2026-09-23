# M9.7 / M9.8 — PvP vertical-slice acceptance report

Issues: #119 (human two-browser acceptance), #120 (final gate).
Branch: `worktree-m9-pvp-vertical-slice`.

Refreshed for the CR001 acceptance gate (#156) after CR001 (#148–#155) changed
the rules. Rules version `cr001`.

## Automated evidence

| Gate | Evidence | Result |
|---|---|---|
| Canonical scenario locked (M9.2) | `engine/tests/test_m9_scenario.py` | pass |
| Real-map fidelity (M9.3) | `engine/tests/test_m9_map_fidelity.py`, `map-fidelity-pass.md` | pass; §17/§18 resolved by CR001 (roof pad, locked P2 spawn) |
| Scripted full match, create → victory (M9.4) | `backend/tests/acceptance/test_m9_full_match.py` via `MatchManager` + `MatchRuntime` + replay writer + victory finalizer | pass: Player 1 wins by zero war bases at tick 21989, with no autonomous nuclear detonation |
| Regression fixture | `backend/tests/acceptance/fixtures/m9-full-match` (seed 20260920, 1051 command ticks, rules `cr001`) | replays to the committed final snapshot; the only robot losses are in the one directly fired blast |
| Cross-system edge cases (M9.5) | `engine/tests/test_m9_edge_cases.py` plus M7 forfeit/no-contest/reconnect tests | pass |
| Replay and client/server consistency (M9.6) | persisted artifact replays through the engine alone; every broadcast frame is schema-valid and equals the engine snapshot; `test_m9_runtime_resync.py` resync mid-move | pass |
| Live two clients over WebSocket | `npm run live:check` against `uvicorn app.main:app` | 16/16, nothing blocked (roof-pad landing at altitude 15; projectile 2 cells per advance, cannon range 10) |
| Whole suite | `pytest` (engine + backend), `npm test`, `npm run build` | 1626 Python + 32 frontend tests pass; build succeeds |

The scripted match reaches, in order: construction on the roof heli-pad, launch,
Search & Capture of a neutral factory, direct fire at a structure without
destroying it, chained Advance orders, docking and direct control, capture
of the neutral `warbase-2`, daily production, a second robot, autonomous
fire, and a directly fired nuclear detonation destroying `warbase-4`. No
autonomous order detonates: the striker's completed Advance becomes Stop &
Defend, which never uses the nuclear weapon (open-questions §19). The engine then emits
`VictoryEvent`, the backend records a durable VICTORY result, broadcasts
`finished` after the final snapshot, and the replay artifact persists it.

## Defects found and where they were fixed

See `readiness-audit.md` §2 and §6. All fixes landed in the owning layer.
M9 itself adds only fixtures, tests, the scenario overlay data, and documents.

## Open items that need the owner

1. **Human two-browser match (M9.7).** Must be played by a person; checklist below.
2. **Owner decisions found during CR001** (`_specs/open-questions.md`). Neither
   blocks a normal match:
   - §4: scenery boxes and walls (element types 17/18/21) are decoded but not
     movement blockers, so robots can walk through them;
   - §8: the Spectrum makes a projectile's first move on the fire tick; the
     engine makes it on the next advance tick.

CR001 resolved the earlier items §17 (P2 spawn locked), §18 (roof pad) and §19
(no autonomous detonation except Search & Destroy arriving at a structure).

M9.8 cannot close until item 1 is done.

## Human two-browser checklist (M9.7)

Start the backend and the dev client:

```bash
uvicorn app.main:app --app-dir backend --port 8010
cd frontend && npm run dev
```

Open two browser windows at `http://localhost:5173/?ws=ws://localhost:8010/ws`.

Record for each defect: steps, owning milestone, severity, and whether it is
gameplay correctness or presentation.

- [ ] Create in window A, join with the code in window B, both ready, match starts.
- [ ] Each commander appears beside its own war base: Player 1 far left at (17, 10), Player 2 far right at (499, 9), both on the ground.
- [ ] Move, rise and descend the commander; it is blocked by a war-base wall when low and clears it when high.
- [ ] **Roof pad (§18).** Hovering or landing on the ground at the war-base anchor does **not** open construction.
- [ ] **Roof pad (§18).** Rise above the roof (altitude 16 or more), fly onto the green pad on the own war-base roof at (anchor.x, anchor.y − 4), for Player 1 (22, 5), and release rise. The commander settles on the roof at altitude 15 and the construction menu opens with 20 general resources.
- [ ] Select chassis and weapon, launch; the robot appears at the exit, which is the anchor cell 4 cells south of the pad (Player 1: (22, 9)), and resources drop. The commander stays on the roof and can take off.
- [ ] Land on the robot; direct control moves it one cell per key press; rising undocks.
- [ ] Give Advance, Search & Capture, and Stop & Defend orders; the robot acts on its own.
- [ ] **Terrain (§4).** Rough, mountain and ditch cells are drawn on the map. A bipod slows on rough and cannot enter mountain or ditch. Tracks enter mountain but not ditch. Anti-grav crosses every class, ditch at its flat speed. Boxes and walls do not block robots yet (open owner decision, §4); do not log that as a defect.
- [ ] Capture a neutral factory; ownership changes on the map and in the HUD. (Since the owner decision of 2026-09-23 this needs 1,440 ticks / 72 s of continuous occupation, not a single tick.)
- [ ] After a game day, resources increase by war-base and factory production.
- [ ] **Projectiles (§8).** Fire a weapon at an enemy robot; projectiles, hits and strength are visible. A projectile moves 2 cells per step (every 4 ticks, 5 steps per second) and disappears after 10 cells for cannon and phaser and 14 for missile, 2 more with electronics. A second shot from the same robot is refused while its projectile is in flight.
- [ ] **No autonomous detonation (§19).** Build a robot with a nuclear weapon and give it Advance, Retreat, Stop & Defend, Search & Capture, and Search & Destroy against robots, with enemy robots on the map. It never detonates on its own.
- [ ] **Search & Destroy detonation (§19).** Give a nuclear robot Search & Destroy against an enemy factory or war base. It detonates only when it reaches that structure's target cell, the same cell Search & Capture goes to.
- [ ] **Blast shapes (§20).** Fire the nuclear weapon under direct control. Robots of either side in the 9×9 window around the carrier are destroyed, with the corner rows trimmed to widths 5, 7, 9, 9, 9, 9, 9, 7, 5; robots outside it survive. At most one building is destroyed: a war base when the carrier is within its diamond (dx < 7, dy < 7, dx + dy < 10, with dy measured from carrier.y + 5, so park just south of the anchor), otherwise a factory within dx < 5, dy < 5, dx + dy < 7 (dy from carrier.y + 1). Ownership is not checked, so an own structure in range is destroyed too. The carrier is destroyed.
- [ ] Close window B; window A shows paused with a countdown; reopen with `?resume` and the match resumes.
- [ ] Destroy or capture the last enemy war base; both windows show the correct victory or defeat.
- [ ] No state divergence between the two windows at any point.
