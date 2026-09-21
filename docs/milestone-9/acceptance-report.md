# M9.7 / M9.8 — PvP vertical-slice acceptance report

Issues: #119 (human two-browser acceptance), #120 (final gate).
Branch: `worktree-m9-pvp-vertical-slice`.

## Automated evidence

| Gate | Evidence | Result |
|---|---|---|
| Canonical scenario locked (M9.2) | `engine/tests/test_m9_scenario.py` | pass |
| Real-map fidelity (M9.3) | `engine/tests/test_m9_map_fidelity.py`, `map-fidelity-pass.md` | pass; §17/§18 open for owner |
| Scripted full match, create → victory (M9.4) | `backend/tests/acceptance/test_m9_full_match.py` via `MatchManager` + `MatchRuntime` + replay writer + victory finalizer | pass: Player 1 wins by zero war bases at tick 19285 |
| Regression fixture | `backend/tests/acceptance/fixtures/m9-full-match` (seed 20260920, 558 command ticks) | replays to the committed final snapshot |
| Cross-system edge cases (M9.5) | `engine/tests/test_m9_edge_cases.py` plus M7 forfeit/no-contest/reconnect tests | pass |
| Replay and client/server consistency (M9.6) | persisted artifact replays through the engine alone; every broadcast frame is schema-valid and equals the engine snapshot; `test_m9_runtime_resync.py` resync mid-move | pass |
| Live two clients over WebSocket | `npm run live:check` against `uvicorn app.main:app` | 15/15, nothing blocked |
| Whole suite | `pytest` (engine + backend), `npm test`, `npm run build` | 1430 Python + 28 frontend tests pass; build succeeds |

The scripted match reaches, in order: construction on the heli-pad, launch,
Search & Capture of a neutral factory, direct fire at a structure without
destroying it, chained Advance orders, docking and direct control, capture
of the neutral `warbase-2`, daily production, a second robot, autonomous
fire, and a nuclear detonation destroying `warbase-4`. The engine then emits
`VictoryEvent`, the backend records a durable VICTORY result, broadcasts
`finished` after the final snapshot, and the replay artifact persists it.

## Defects found and where they were fixed

See `readiness-audit.md` §2 and §6. All fixes landed in the owning layer.
M9 itself adds only fixtures, tests, the scenario overlay data, and documents.

## Open items that need the owner

1. **Human two-browser match (M9.7).** Must be played by a person; checklist below.
2. **Open question §19.** A nuclear carrier on Stop & Defend detonates at the
   nearest enemy robot at any distance. Reachable in a normal match whenever
   an Advance or Retreat completes. This is a critical gameplay decision for
   M5/M6.
3. **Open question §18.** Heli-pad is on the war-base roof at altitude 15 in the
   original; the engine uses a ground-level pad at the exit cell.
4. **Open question §17.** Confirm the mirrored Player 2 spawn.

M9.8 cannot close until item 1 is done and item 2 is decided or explicitly
deferred by the owner.

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
- [ ] Each commander appears beside its own war base: Player 1 far left, Player 2 far right.
- [ ] Move, rise and descend the commander; it is blocked by a war-base wall when low and clears it when high.
- [ ] Fly onto the green heli-pad on the own war-base roof and let the commander settle; the construction menu opens with 20 general resources.
- [ ] Select chassis and weapon, launch; the robot appears at the exit and resources drop.
- [ ] Land on the robot; direct control moves it one cell per key press; rising undocks.
- [ ] Give Advance, Search & Capture, and Stop & Defend orders; the robot acts on its own.
- [ ] Capture a neutral factory; ownership changes on the map and in the HUD.
- [ ] After a game day, resources increase by war-base and factory production.
- [ ] Fire a weapon at an enemy robot; projectiles, hits and strength are visible.
- [ ] Close window B; window A shows paused with a countdown; reopen with `?resume` and the match resumes.
- [ ] Destroy or capture the last enemy war base; both windows show the correct victory or defeat.
- [ ] No state divergence between the two windows at any point.
