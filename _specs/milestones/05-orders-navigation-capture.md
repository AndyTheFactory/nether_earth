# Milestone 5 — Robot Movement, Orders, Navigation & Capture

## Goal

Implement authoritative robot movement, terrain capability, direct-control movement primitives, autonomous orders, electronics-dependent navigation behavior, and factory/war-base capture semantics.

## Spec references

- `_specs/functional-spec.md` §13–§17 — chassis, movement, control states, autonomous orders, electronics
- `_specs/technical-spec.md` §7, §11, §15–§17 — movement, capture, orders, intelligence, interaction modes
- `_specs/open-questions.md` §3–§7 and §11

## Dependencies

- Milestone 2 map/world model.
- Milestone 4 robot entities and structures.

## Deliverable

A deterministic movement/order subsystem shared by direct control and autonomous behavior, including chassis/terrain rules, movement durations, collision/claim resolution, order lifecycle, target selection, navigation intelligence, and capture progress.

## Workstreams and candidate tasks

### Movement executor
Create one low-level robot movement path used by direct control and autonomous orders. Enforce terrain capability, occupancy, commander blocking, transition timing, and deterministic destination claims.

### Chassis movement data
Encode verified ticks-per-cell and rough-terrain penalties as centralized rule data.

### Direct-control movement state
Add robot interaction state required for direct movement, while leaving frontend input mapping for a later milestone.

### Autonomous order model
Implement Stop & Defend, Advance, Retreat, Search & Capture, and Search & Destroy state/lifecycle, including fallback to Stop & Defend when an order becomes impossible.

### Miles/grid conversion
Implement one authoritative conversion used by order distances and later weapon ranges.

### Navigation behavior
Reproduce verified non-electronic routing behavior and improved electronic behavior without changing physical terrain legality.

### Capture subsystem
Implement neutral acquisition and enemy capture progress, interruption semantics, ownership events, and war-base capture if verified.

## Parallelization

Movement executor and capture state can proceed alongside order-domain modeling. Navigation algorithms depend on the movement contract. Miles conversion should be resolved before final Advance/Retreat semantics and combat range work.

## Acceptance criteria

- All robot movement uses the same engine legality/execution path.
- Bipod/tracks/anti-grav terrain permissions match locked rules.
- Movement durations are integer ticks from centralized rule data.
- Competing destination claims resolve deterministically.
- Direct movement and autonomous movement cannot bypass occupancy/collision rules.
- All specified autonomous orders transition correctly.
- Electronics affects intelligence, not chassis terrain capability.
- Capture progress uses authoritative ticks and resolved interruption semantics.
- Ownership changes emit deterministic events.

## Milestone integration scenario

On a fixture battlefield containing normal, rough, ditch, obstacles, commanders, neutral/enemy structures, run one robot of each chassis through direct and autonomous command streams. Verify terrain constraints, destination conflict behavior, dumb/electronic navigation differences, factory capture timing, and deterministic replay.

## Out of scope

- Projectile combat and damage.
- Full combat-control menu.
- Browser input/rendering.
- Match networking.

## Open questions / blockers

This milestone intentionally contains several research dependencies from `_specs/open-questions.md`:

- miles-to-grid conversion (§3);
- exact movement speeds/terrain penalties (§4);
- dumb vs electronic navigation (§5);
- war-base capture mechanics (§6);
- capture interruption semantics (§7);
- simultaneous destination claims (§11).

Relevant implementation tasks must remain blocked until the corresponding rule is verified and approved, unless they can safely implement only the neutral/configurable mechanism. Resolved decisions must update the authoritative specs first.

## Definition of done

- All milestone issues are closed by merged PRs.
- Movement/order/capture integration scenario passes deterministically.
- All blocking fidelity questions used by implemented behavior are resolved in specs.
- No alternate movement legality exists outside the engine.
