# Milestone 5 — Robot Movement, Orders, Navigation & Capture

## Goal

Implement authoritative robot movement, terrain capability, direct-control movement primitives, autonomous orders, electronics-dependent navigation behavior, deterministic destination reservations/contention, and factory/war-base capture semantics.

This milestone owns autonomous movement, target selection, routing, order lifecycle, and engagement intent. Actual firing, projectile simulation, damage, and destruction belong to Milestone 6.

## Spec references

- `_specs/functional-spec.md` §§13–16
- `_specs/technical-spec.md` §§8, 13–15
- `_specs/open-questions.md` §§3–7 and §11

## Start dependencies

- Milestone 2 map/world model and canonical structure interaction points.
- Milestone 3 commander collision/blocking contract.
- Milestone 4 robot entity/component contract.

## Completion dependencies

- Milestones 2–4 complete for the world, commander-blocking, and robot-state behavior consumed here.

## Deliverable

A deterministic movement/order subsystem shared by direct control and autonomous behavior, including chassis/terrain rules, movement durations, destination reservations, seeded contention resolution, order lifecycle, target selection, navigation intelligence, capture progress, and combat engagement intent for Milestone 6.

## Locked rules

### Miles/grid conversion

```text
1 mile = 2 cells
1 cell = 0.5 miles
Advance/Retreat 0–50 miles = 0–100 cells
```

Implement once in shared game-rule/helper code.

### Terrain capability

```text
Bipod:     normal yes; rough yes/severe slowdown; ditch no
Tracks:    normal yes; rough yes/smaller slowdown; ditch no
Anti-grav: normal yes; rough yes; ditch yes
```

Ordinary-terrain relative speed:

```text
bipod < tracks < anti-grav
```

Exact ticks-per-cell and rough penalties remain fidelity research/configuration.

### Navigation

- Non-electronic robots use deliberately limited/original-style local routing and may get stuck even if a longer valid route exists.
- Electronic robots use deterministic proper pathfinding/replanning around obstacles.
- Electronics improves routing only; it never changes chassis terrain permissions.

### Capture

- qualifying enemy robots can capture factories and war bases;
- default capture duration = 1,440 ticks;
- qualifying occupation must be continuous;
- interruption resets progress immediately to zero;
- ownership changes immediately on completion;
- victory is evaluated in the same authoritative step after war-base ownership change.

### Destination reservations/contention

- target cell is reserved when a move is accepted/started;
- reserved target cannot be claimed by another robot until completion/cancellation;
- same-tick competing claims are resolved using the match-local seeded deterministic RNG;
- two valid claimants = 50/50 coin flip;
- N valid claimants = uniform choice;
- losing robots remain outside the cell and may retry/replan.

## Workstreams and candidate tasks

### Movement executor
Create one low-level robot movement path used by direct control and autonomous orders. Enforce terrain capability, occupancy, commander blocking, transition timing, and reservations.

### Chassis movement data
Encode verified/default ticks-per-cell and rough-terrain penalties as centralized rule data. Keep currently unverified exact values clearly marked configurable.

### Destination reservation manager
Reserve targets at move start, release on completion/cancellation, and batch same-tick contenders before selecting a seeded-RNG winner.

### Direct-control movement state
Add robot interaction state required for direct movement, leaving frontend input mapping to a later milestone.

### Autonomous order model
Implement Stop & Defend, Advance, Retreat, Search & Capture, and Search & Destroy lifecycle/fallback behavior.

For combat-capable orders, this milestone determines target selection, movement/positioning, and deterministic engagement intent only. Milestone 6 resolves firing/damage.

### Navigation policies
Implement separate non-electronic and electronic policies behind one engine navigation interface.

### Capture subsystem
Implement capture progress, reset semantics, ownership events, and war-base capture using canonical interaction points from Milestone 2. Neutral and enemy-owned structures share one continuous-occupation rule (owner decision, 2026-09-23).

## Parallelization

Movement executor, reservation logic, order-domain modeling, and capture state can proceed in parallel against agreed contracts. Navigation depends on movement legality. Engagement-intent work can proceed independently once target contracts are stable.

## Acceptance criteria

- All robot movement uses one engine legality/execution path.
- Chassis terrain permissions match locked rules.
- Movement durations are integer ticks from centralized rules.
- Move targets are reserved at move start.
- Same-tick competing claims use deterministic seeded-RNG selection and replay identically.
- Direct and autonomous movement cannot bypass occupancy/collision/reservation rules.
- Commander blocking uses the Milestone 3 collision contract.
- Autonomous orders transition correctly through movement/target-selection lifecycle.
- Stop & Defend/Search & Destroy can produce deterministic engagement intent without firing in this milestone.
- Electronics changes navigation intelligence, not terrain capability.
- Capture uses authoritative ticks and resets immediately on interruption.
- Ownership changes emit deterministic events.

## Milestone integration scenario

On a fixture battlefield containing normal/rough/ditch terrain, obstacles, commanders, neutral/enemy structures, and contention points, run each chassis through direct/autonomous command streams. Verify terrain constraints, commander blocking, target reservation, seeded contention outcomes, dumb/electronic navigation differences, target selection/engagement intent, capture timing/reset, and deterministic replay.

## Out of scope

- Weapon firing/projectiles/damage.
- Full combat-control menu.
- Browser input/rendering.
- Match networking.

## Open questions / blockers

Resolved for this milestone:

- miles/grid conversion (§3);
- dumb vs electronic navigation (§5);
- war-base capture (§6);
- capture interruption (§7);
- simultaneous destination claims (§11).

Still fidelity-open:

- exact chassis movement ticks-per-cell/rough penalties (§4).

This remaining research item must stay isolated in configuration and must not block implementation of the already-locked movement architecture/relative behavior.

## Definition of done

- All milestone issues are closed by merged PRs.
- Movement/order/capture integration scenario passes deterministically.
- Locked rules match authoritative specs.
- Exact unresolved movement constants remain centralized/configurable rather than guessed throughout code.
- No alternate movement legality exists outside the engine.
- Combat-capable orders expose a stable engagement-intent contract for Milestone 6.