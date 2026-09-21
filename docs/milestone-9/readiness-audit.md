# M9.1 — Readiness audit: M2–M8 completion and v1 PvP prerequisites

Issue: #113. Milestone: M9 — Full PvP Vertical Slice.
Audited branch: `worktree-m9-pvp-vertical-slice` (based on the M8 branch, which
contains M6 (merged, PR #142), M7 (PR #143) and M8 (PR #145)).

## 1. Milestone completion status (evidence)

| Milestone | Status | Evidence |
|---|---|---|
| M0–M1 | complete | merged to `main`; `engine/tests/test_m1_integration.py` |
| M2 map/world model | complete with documented data gaps | merged; `data/maps/zx-spectrum-original.yaml` + evidence doc; `engine/tests/test_m2_integration.py`, `test_original_map.py` |
| M3 commander | complete | merged; `test_m3_integration.py` |
| M4 construction/economy | complete | merged; `test_m4_integration.py` |
| M5 movement/orders/capture | complete | merged (PRs #133–#141); `test_m5_integration.py` |
| M6 combat/victory | complete | merged (PR #142); `test_m6_integration.py` |
| M7 match/protocol/multiplayer | implemented, PR #143 open | `backend/tests/transport/test_milestone_integration.py` |
| M8 frontend | implemented, PR #145 open | `frontend` tests, fixtures, `scripts/live-two-client.mjs` (2 steps BLOCKED, see §2) |

Baseline on this branch: `pytest engine/tests backend/tests` → 1383 passed.

## 2. Blocking gaps for the normal create → victory path

Each gap is owned by a subsystem outside M9 and is fixed there (per
`_specs/milestones/09-pvp-vertical-slice.md`, "If testing reveals genuinely
missing prerequisite behavior, create/fix the task under the owning
milestone/subsystem").

### G1 — Backend starts matches without map, commanders, resources, or ownership (M7 / engine scenario init)

`backend/app/match/manager.py::_default_bootstrap_map` builds a 1×1
`BootstrapMap` placeholder; `MatchRuntime` steps with `world=None`, which
disables collision, heli-pad, launch, movement, capture, combat and victory
checks. `engine.new_game` never spawns commanders, seeds resource pools, or
records starting ownership in `GameState`, and the frontend reads ownership
only from `state.structure_ownership`. Spec `functional-spec.md` §5 step 5
("Server initializes map, scenario, resources, commanders, factories, and
game clock") is therefore unimplemented end to end. The M8 PR reports this
explicitly (two BLOCKED live-check steps).

Fix (owning layers): engine `scenario.create_initial_state(scenario, world=…)`
composes the tick-0 state from scenario + overlaid `WorldMap`; backend loads
`data/maps/zx-spectrum-original.yaml`, applies `default_pvp_overlay`, passes the
`WorldMap` to `engine.new_game`/`MatchRuntime`; replay verification receives
the same world.

### G2 — Engine victory never ends the match (M7)

`engine.step` emits `VictoryEvent`, but nothing in `backend/app` observes it:
the match stays `ACTIVE`, the runtime keeps ticking, and `ServerFinished`
(`protocol/schemas/server_messages.schema.json` `$defs.finished`, already
consumed by the frontend) is never sent. `Match.result` only covers
forfeit/no-contest.

Fix (M7): a tick observer that, on `VictoryEvent`, records a durable
`MatchResult(outcome=VICTORY)`, runs `MatchManager.finish_match`, broadcasts
`finished`, and replays it on reconnect; replay meta persists the result.

### G3 — Scenario metadata drift: starting resources (M1 scenario data)

`scenario.default_pvp_scenario()` records `starting_general_resources=30`;
`functional-spec.md` §4/§10.1, `technical-spec.md` §6 and `rules.py`
(`starting_general_resources = 20`, the value actually used to seed pools)
say 20. Fix: scenario data → 20 plus a drift test (M9.2).

### G4 — Commander spawn positions are undefined (M2 overlay data / open question)

No spec, map, or overlay declares where commanders start;
`default_pvp_overlay` returns empty `spawn_positions`. Disassembly evidence
(`La600_start`: `ld hl, 17 / ld (Lfd0e_player_x)`, `ld a, 10 / ld
(Lfd0d_player_y)`, altitude 0) fixes Player 1's start at cell (17, 10),
altitude 0, i.e. just outside the extreme-left war base (anchor (22, 9)).
The original is single-player, so Player 2 has no evidence. Handling: the
overlay declares both spawns as data; Player 2 uses the mirrored offset from
its own war-base anchor (see `_specs/open-questions.md` §17, recorded as a
provisional convention requiring owner confirmation, not a locked rule).

## 3. Non-blocking gaps (documented, routed to owners)

- **Heli-pad location/height (M2 data + M3 rule).** Disassembly
  (`Lbb86_assign_warbase_to_player`, game loop `cp 15` altitude check) places
  the "H" pad on the war-base roof at (anchor.x, anchor.y − 4), landed at
  altitude 15. The engine's landing rule (`heli_pad.py`) requires altitude 0
  on a ground cell, so the roof pad cannot be expressed without an M3 rule
  change. The current placeholder (pad = anchor ground cell) keeps the
  normal path playable. Owner decision recorded in
  `docs/milestone-9/map-fidelity-pass.md` and `_specs/open-questions.md` §18.
- **Exit cell.** Verified by evidence (robot placed at pad y + 4 = anchor
  cell, `Lcb52_construction_screen_start_robot`); current data already uses
  the anchor cell.
- **Terrain and scenery blockers** not decoded (documented in the M2 evidence
  doc). The map is more open than the original but the normal path is not
  blocked. Not a v1 completion-path question.
- **Frontend rejection feedback** is snapshot-only by protocol policy (M7/M8
  documented limitation). Not blocking.

## 4. Open questions affecting the v1 path

`_specs/open-questions.md` §1–§16 are resolved or partially resolved with
locked engine defaults. §4 and §8 ("partially resolved") lock every value the
engine consumes; the unresolved remainders are fidelity refinements, not
completion-path blockers. New §17 (commander spawn) and §18 (heli-pad
placement/height) are added by this milestone as owner-review items; the
match is playable under the provisional data either way.

## 5. Go/no-go gate for M9.2–M9.8

M9 work may proceed once G1–G4 are fixed in their owning layers on the
integration branch. Everything M9 adds afterwards is limited to fixtures,
acceptance tests, scenario verification, replay validation and acceptance
documentation.

## 6. Defects found during M9.4–M9.6 execution

| Defect | Owner | Status |
|---|---|---|
| Commander collision, auto-dock and follow ignored live `state.robots`; a commander could never dock, so direct control was unreachable in a real match | M3/M4 engine (`engine.step`) | fixed, commit a930be0 |
| Snapshot and resync frames were serialized with `exclude_none`, dropping required nullable fields (`docked_robot_id`, transitions, `order`); every real-match snapshot failed the protocol schema on the wire | M7 protocol (`app.protocol.serialize_server_message`) | fixed, regression test in `tests/transport/test_snapshots.py` |
| A nuclear carrier on Stop & Defend detonates at the nearest enemy robot at any distance | M5/M6 rule, unspecified | open, `_specs/open-questions.md` §19 |
| Construction buffer is snapshotted at entry, so income arriving while the menu is open needs cancel and re-entry | M4 rule (locked) | documented, no change |
| A robot launched onto the pad/exit cell encloses a grounded commander until it steps aside | M2 data, tied to §18 | documented |
