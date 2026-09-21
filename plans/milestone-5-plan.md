# Milestone 5 Plan — Robot Movement, Orders, Navigation & Capture

Spec: `_specs/milestones/05-orders-navigation-capture.md` (also `_specs/functional-spec.md` §§13-16, `_specs/technical-spec.md` §§8,13-15, `_specs/open-questions.md` §§3-7,11).

GitHub tracking: issue #69 (plan), tasks #60-#68. Each task below IS the corresponding GitHub issue; close the issue with the task's PR.

## Dependency waves

```
Wave 1: Task 1 (#60 movement executor)      + Task 2 (#61 timing research, fully independent)
Wave 2: Task 3 (#62 reservations/contention) + Task 6 (#66 capture subsystem)   [both need #60 only]
Wave 3: Task 4 (#63 direct control)          + Task 5 (#65 navigation policies) [both need #60+#62]
Wave 4: Task 7 (#64 orders/target selection/engagement intent) [needs #60,#62,#65,#66]
Wave 5: Task 8 (#67 snapshot/replay integration) [needs everything above]
Wave 6: Task 9 (#68 integration scenario)    [needs everything above, final gate]
```

Within a wave, tasks touch disjoint modules and may run as parallel implementer dispatches; across waves, later tasks must not start until earlier-wave tasks are reviewed clean (or parked) so their interfaces are stable.

## Global Constraints

- All robot movement uses one engine legality/execution path (engine/src/nether_earth/, new `movement.py`-style module) — no order/direct-control code may re-implement terrain/occupancy/blocking/reservation checks.
- Chassis terrain permissions (locked): bipod = normal+rough, no ditch; tracks = normal+rough, no ditch; anti-grav = normal+rough+ditch. Ordinary-terrain relative speed: bipod < tracks < anti-grav. Rough slows bipod severely, tracks less severely.
- Movement durations are integer simulation ticks sourced from one centralized rules/config module (extend `engine/src/nether_earth/rules.py` unless a clearer home exists) — never hardcoded per call site.
- 1 mile = 2 cells; Advance/Retreat 0-50 miles = 0-100 cells, via one shared helper.
- Move targets are reserved when a move is accepted/started; released on completion/cancellation/failure; unavailable to other claimants until then.
- Same-tick competing destination claims resolve via the match-local seeded deterministic engine RNG (`engine/src/nether_earth/rng.py`): 2 claimants = 50/50 coin flip, N claimants = uniform choice. Never resolve by robot ID/order/priority.
- Direct and autonomous movement cannot bypass occupancy/collision/reservation rules; commander blocking reuses the Milestone 3 contract in `engine/src/nether_earth/collision.py` / `commander_movement.py` — do not duplicate it.
- Non-electronic robots: deliberately limited/original-style local routing, may get stuck even when a longer valid route exists. Electronic robots: deterministic proper pathfinding/replanning. Electronics affects routing intelligence only, never chassis terrain permissions.
- Orders (locked set): Stop & Defend; Advance 0-50mi east; Retreat 0-50mi west; Search & Capture (neutral factory / enemy factory / enemy war base); Search & Destroy (robots / factories / war bases). Invalid/impossible orders deterministically revert to Stop & Defend.
- This milestone produces deterministic engagement intent only (target + intent to fire) for combat-capable orders — no firing, projectiles, or damage. Milestone 6 owns that.
- Capture: default duration 1,440 authoritative ticks (centralized/configurable); progress requires continuous qualifying occupation of the canonical M2 interaction location; any interruption resets progress to zero immediately; ownership changes immediately on completion; war-base ownership change triggers victory evaluation in the same authoritative step.
- Failed/rejected actions must cause no partial state mutation.
- Everything here must be deterministic and replay-safe: same seed + initial state + command stream ⇒ identical state/events/hash across runs. Do not add nondeterministic iteration (e.g. unordered dict/set iteration that can affect outcomes).
- Do not depend on FastAPI/WebSockets/frontend/network I/O from the engine. No PostgreSQL/Redis/etc.
- Add regression tests for every locked rule surface named above; prefer deterministic tests exercising commands/ticks directly, following existing patterns in `engine/tests/`.
- Where exact timing/tick constants are not yet evidence-verified (owned by Task 2 / issue #61), use clearly marked configurable defaults — never spread ad hoc guessed numbers through multiple call sites.

---

### Task 1: Shared robot movement executor and terrain legality (issue #60)

Implement the single authoritative low-level robot movement path that ALL direct and autonomous control must use going forward (later tasks build on this; do not let them bypass it).

Scope:
- One engine-level movement executor (new module, e.g. `engine/src/nether_earth/movement.py`) used by every robot control source.
- Validate terrain capability by chassis per the locked rules above.
- Validate occupancy and commander blocking through the existing M2 (`occupancy.py`) and M3 (`collision.py`/`commander_movement.py`) contracts — do not duplicate their logic.
- Movement duration = integer simulation ticks sourced from centralized movement rules/config (add to `rules.py` or a new `movement_rules.py`; mark unresolved exact values as configurable defaults per issue #61, without blocking this task).
- Keep authoritative X/Y discrete; no frontend interpolation concerns.
- Provide stable move-start / move-complete / rejection result semantics that later reservation logic (Task 3) and higher-level control (Tasks 4, 5, 7) can consume.
- Do NOT implement pathfinding, order logic, or reservation/contention logic here — those are later tasks' scope; expose the hooks/interfaces they'll need.

Acceptance criteria:
- All robot moves go through one authoritative legality/execution path.
- Chassis terrain permissions match locked rules exactly.
- Occupancy and commander-blocking checks reuse existing engine contracts.
- Movement duration comes from centralized config/rules, not literals.
- Failed movement causes no partial state mutation.
- Movement is deterministic and replay-safe.
- Unit tests cover: terrain capability per chassis (allowed/forbidden terrain incl. ditch), commander blocking, occupancy/bounds rejection, successful transition timing.

Out of scope: destination contention (Task 3), direct-control interaction state (Task 4), orders/pathfinding/capture (Tasks 5-7).

---

### Task 2: Research and finalize Spectrum robot movement timings (issue #61)

Fully independent of Task 1's code — pure research/config task, runs in parallel with Wave 1.

Goal: resolve remaining fidelity-open movement constants from ZX Spectrum evidence and encode them as canonical game-rule defaults, without changing movement architecture.

Research questions (in fidelity-priority order: observed Spectrum behavior > disassembly/code evidence > original manual > gameplay recordings > other ports as secondary only):
- default ticks/cycles per cell for bipod, tracks, anti-grav;
- bipod rough-terrain slowdown;
- tracked rough-terrain slowdown;
- whether anti-grav speed is uniform across all traversable terrain;
- how original game-cycle cadence maps onto the locked 20 Hz simulation tick rate (see technical-spec).

Scope:
- Trace relevant movement/update code in the `santiontanon/netherearth-disassembly` reference (see `_specs/references.md`).
- Record evidence and any ambiguity directly in this task's output and in `_specs/open-questions.md` §4.
- Propose exact canonical defaults only where evidence supports them; where ambiguous, preserve the locked *relative* behavior (bipod < tracks < anti-grav; rough penalizes bipod more than tracks) and mark the exact value explicitly as configurable/unverified rather than inventing precision.
- Land the defaults as data in the movement rules/config module Task 1 creates (coordinate: if Task 1 has already merged, add directly there; if concurrent, land as a small follow-up PR once Task 1's config module exists — do not block on it, but do not invent a second config location either).
- Update `_specs/open-questions.md` §4 to reflect what got resolved vs. what remains open.

Acceptance criteria:
- Every finalized constant has a recorded evidence trail (citation to disassembly location or spec passage).
- No unsupported value is presented as verified original Spectrum behavior.
- Defaults live in exactly one place in game-rule configuration.
- Task 1's movement executor can consume the values with zero code-path changes.
- Remaining ambiguity, if any, is explicit in `_specs/open-questions.md` and is non-architectural.

This issue never blocks Task 1's movement architecture — only blocks claiming fidelity-complete exact timing at the final integration gate (Task 9) if left materially unresolved.

---

### Task 3: Deterministic destination reservations and contention resolution (issue #62)

Depends on: Task 1 (movement executor contract) merged/stable. Uses the existing match-local seeded engine RNG (`rng.py`).

Scope:
- Reserve a destination cell when a move is accepted/started (hook into Task 1's move-start point).
- Keep the reservation held until movement completes, is cancelled, or fails/is released.
- Reserved destinations are unavailable to other claimants for the reservation's lifetime.
- Batch all valid same-tick claims targeting the same destination before resolving.
- Two contenders: seeded 50/50 coin flip via the engine RNG. N contenders: uniform seeded choice via the same RNG. Never resolve by robot ID, submission order, or priority.
- Losing robots remain outside the destination cell with a stable, deterministic result they (or their order/policy layer) can use to retry/replan.
- Release reservations safely and correctly on completion, cancellation, and failure — no leaks, no double-release.

Acceptance criteria:
- No two robots can simultaneously hold/enter the same destination reservation.
- Same seed + state + commands ⇒ same contention winner, every run.
- Two-way contention is a seeded coin flip (statistically verifiable in tests, not deterministic-by-ID).
- N-way contention uses a uniform seeded choice.
- Losers get a deterministic outcome with no partial occupancy mutation.
- Tests cover full reservation lifecycle (start/complete/cancel/fail) and replay reproduces identical reservation/winner outcomes across repeated runs with the same seed.

Out of scope: pathfinding policy (Task 5), order lifecycle (Task 7), network command ordering.

---

### Task 4: Direct-control robot movement state (issue #63)

Depends on: Task 1 (movement executor). Integrate with Task 3's reservations if Task 3 has landed; if concurrent, build against Task 3's contract as specified here and reconcile in review.

Scope:
- Add authoritative engine interaction state for a player-controlled/docked robot, consistent with the existing commander/robot interaction contract from Milestone 4 (`robot.py`, `docking.py`, `commander.py`).
- Route directional robot input through the shared movement executor from Task 1 ONLY — no parallel legality logic.
- Ensure terrain, occupancy, commander blocking, and reservations (Task 3) cannot be bypassed via direct control.
- Implement the engine-side transition for leaving direct movement/control per the existing interaction model (docking/undocking or equivalent, per Milestone 4 contract).
- Keep browser key mapping/UI entirely out of the engine — engine exposes commands/state only.

Acceptance criteria:
- Direct-control moves use exactly the same legality/execution path as autonomous movement.
- Invalid direct moves are rejected deterministically with zero side effects.
- Direct control cannot bypass reservation or commander-blocking rules.
- Interaction-state transitions are snapshot/replay-safe.
- Tests cover successful/failed directional moves and leaving direct-control state.

Out of scope: frontend input bindings, autonomous orders/navigation, combat-control firing.

---

### Task 5: Non-electronic and electronic navigation policies (issue #65)

Depends on: Task 1 (movement legality/execution) and Task 3 (reservations/contention) merged/stable.

Scope:
- Define one stable engine navigation policy interface, consumed later by autonomous orders (Task 7).
- Non-electronic policy: local/original-style routing; no globally optimal pathfinding; may fail to route around obstacles or get stuck even when a longer valid route exists (this is intentional, locked product behavior — do not "fix" it into competence).
- Electronic policy: deterministic proper pathfinding/replanning; actively routes around obstacles when a valid chassis-compatible path exists.
- Both policies must use the same chassis terrain legality, occupancy, reservation, and commander-blocking rules from Tasks 1 and 3 — no alternate collision/terrain logic.
- Electronics changes routing intelligence only, never physical terrain capability.
- Tie-breaking within either policy must be deterministic (seeded RNG or stable ordering, never dict/set iteration order).

Acceptance criteria:
- Electronic and non-electronic navigation are separate policies behind one interface.
- Non-electronic behavior can reproduce intentionally limited/stuck outcomes on a fixture with an available longer route.
- Electronic behavior finds/replans a valid route where one exists under the robot's chassis capabilities.
- Electronics never permits terrain traversal the chassis forbids.
- Reservations/dynamic blockers trigger deterministic, policy-appropriate behavior (e.g., electronic robot replans around a reserved cell; non-electronic robot may stall per its limited logic).
- Tests include an obstacle-routing contrast (same fixture, both policies, divergent outcomes) and replay determinism.

Fidelity note: exact original local-routing quirks may be refined later from disassembly evidence, but the locked product behavior above must hold now. Do not replace non-electronic navigation with generic optimal pathfinding "for simplicity."

---

### Task 6: Factory and war-base capture subsystem (issue #66)

Depends on: Task 1 (movement/occupancy contract, for qualifying occupation) and the existing M2 structure ownership + capture-zone metadata. Can run in the same wave as Task 3 (disjoint modules) once Task 1 is stable.

Scope:
- Implement capture state per eligible structure/robot, fitting the existing engine architecture (structures in `structures.py`, interaction points in `interactions.py`).
- Neutral factories: activate for the first qualifying robot/player under the verified original rule (see `_specs/open-questions.md` §6, resolved).
- Enemy factories and war bases: require continuous qualifying occupation of the canonical M2 interaction location.
- Default duration = 1,440 authoritative ticks, centralized/configurable (not hardcoded at call sites).
- Any interruption (occupation lost) resets progress to zero immediately — no partial-credit resume.
- Ownership transfers immediately upon completion; emit a deterministic ownership/capture event.
- War-base ownership change must trigger victory evaluation within the same authoritative simulation step.
- Use the canonical M2 semantic interaction locations for qualifying occupation — never an inferred generic robot footprint/adjacency.

Acceptance criteria:
- Capture progress advances only while the qualifying robot continuously occupies the canonical capture location.
- Interruption resets progress to zero immediately.
- Ownership changes exactly at the configured duration boundary (not off-by-one).
- Neutral acquisition, enemy factory capture, and enemy war-base capture are all deterministic.
- War-base capture invokes victory evaluation in the same step.
- Tests cover start/progress/interruption/reset/completion/ownership/victory, plus replay determinism.

Out of scope: structure destruction/nuclear effects (M6), resource production logic (already owned by M4), frontend progress display.

---

### Task 7: Autonomous robot orders, target selection, and engagement intent (issue #64)

Depends on: Task 1 (movement executor), Task 3 (reservations/contention), Task 5 (navigation policies), Task 6 (capture subsystem, for Search & Capture completion semantics). This is the integration task for the order domain — do not start full implementation until Tasks 5 and 6 are stable; a domain-model skeleton may be drafted earlier per the milestone's parallelization note, but final wiring consumes the real interfaces.

Scope — implement order state/lifecycle for:
- Stop & Defend
- Advance 0-50 miles east
- Retreat 0-50 miles west
- Search & Capture neutral factories
- Search & Capture enemy factories
- Search & Capture enemy war bases
- Search & Destroy robots
- Search & Destroy factories
- Search & Destroy war bases

Rules:
- Use one shared miles-to-cells helper (1 mile = 2 cells) for Advance/Retreat distance conversion — reuse if Task 1 already added it under Global Constraints; otherwise add it once, centrally.
- Invalid/impossible orders revert deterministically to Stop & Defend.
- Target selection is deterministic and uses canonical world/ownership data (no ad hoc distance heuristics that could diverge across replays).
- Orders invoke the shared movement (Task 1) and navigation (Task 5) contracts — no alternate movement logic.
- Stop & Defend and Search & Destroy may produce deterministic engagement intent for M6 (target + intent to fire), but must NOT fire or resolve damage.
- Factory/war-base destruction targets requiring a nuke should only produce valid engagement intent when the robot has suitable capability; M6 owns actual detonation/destruction.
- Advance/Retreat completion transitions the order to Stop & Defend.

Acceptance criteria:
- Every locked order has an explicit authoritative state/lifecycle.
- Advance/Retreat convert 0-50 miles to 0-100 cells through the one shared helper.
- Completion of Advance/Retreat transitions to Stop & Defend.
- Search target selection is deterministic and uses canonical world/ownership data.
- Impossible/invalid orders fall back deterministically to Stop & Defend.
- Combat-capable orders expose stable, well-defined engagement intent without implementing weapon behavior.
- Tests cover order transitions, target disappearance mid-order, no-target cases, range/distance conversion, and replay determinism.

Out of scope: projectile/firing/damage/nuke resolution (M6), frontend menus, network protocol exposure.

---

### Task 8: Integrate movement, orders, navigation, and capture into snapshots and replay (issue #67)

Depends on: Tasks 1, 3, 4, 5, 6, 7 all merged. Reflects Task 2's timing defaults if landed by this point. This is the convergence task — expect it to touch `snapshot.py`, `replay.py`, `engine.py`, and `events.py`.

Scope:
- Include robot movement-transition state and reservations (Task 3) in snapshots/replay.
- Include current robot orders, targets/goals, navigation-relevant authoritative state (Task 5/7), and capture progress (Task 6).
- Ensure engagement intent (Task 7) is serialized/derivable deterministically enough for M6 consumption.
- Emit stable events for movement/order/capture transitions consistent with existing engine event conventions (`events.py`).
- Ensure seeded contention RNG usage (Task 3) replays identically.
- Preserve stable IDs and deterministic ordering everywhere new state is iterated/serialized.
- Do not add backend/WebSocket/frontend rule logic — engine-only.

Acceptance criteria:
- Snapshot/restore reproduces in-progress moves, reservations, orders, and captures correctly.
- Same initial state + seed + command stream ⇒ identical contention winners, navigation/order outcomes, capture ownership, events, and final state hash, across repeated runs and across a snapshot/restore round-trip.
- Rejected/failed movements and interrupted captures replay without hidden side effects.
- Existing M1-M4 determinism tests remain green.

Verification: full engine test/replay/hash suite, repeated deterministic runs, lint/type checks (see repo's existing CI/test tooling under `engine/`).

Out of scope: network protocol exposure (M7), frontend state/rendering (M8), M6 firing/damage state.

---

### Task 9: Deterministic movement/orders/navigation/capture integration scenario (issue #68)

Final M5 gate. Depends on: Tasks 1, 2 (as far as resolved), 3, 4, 5, 6, 7, 8 all merged.

Scope — build one deterministic fixture battlefield (normal/rough/ditch terrain, static blockers, commanders, neutral/enemy factories and war bases, contention points) and exercise, in one integration test/scenario module (follow the pattern of existing `test_m2_integration.py`..`test_m4_integration.py`):
- each chassis through valid/invalid terrain;
- configured movement durations/rough penalties;
- commander blocking through the M3 contract;
- same-tick destination contention with a verified seeded winner and identical outcome on replay;
- a robot moved through direct control;
- Advance/Retreat distances through the shared miles-to-cells conversion;
- Search & Capture and Search & Destroy target selection;
- intentionally limited non-electronic routing vs. electronic replanning, contrasted on the same obstacle layout;
- neutral acquisition and enemy factory/war-base capture;
- capture interruption with immediate reset verified;
- war-base capture completion with same-step victory evaluation verified;
- engagement intent produced without any firing/damage;
- full replay of identical commands/seed asserting identical events/state/hash.

Acceptance criteria:
- Integration scenario is deterministic across repeated runs.
- All locked M5 product rules (see Global Constraints) are covered by the scenario.
- No alternate test-only movement/collision/capture semantics bypass the M2-M4 contracts — the scenario must exercise real engine code paths only.
- M6 combat implementation (actual firing/damage) is not pulled into the scenario.
- Full engine test suite remains green.

Fidelity gate: if Task 2 (#61) could not fully resolve exact Spectrum movement constants, the scenario must use the documented centralized configured defaults and must NOT claim exact timing fidelity — architecture/behavior completion can still be demonstrated independently of exact-constant fidelity.

Close this task only when Tasks 1-8 are merged and this integration/replay scenario passes.
