# Milestone 3 — Commander Movement, Collision & Docking

## Goal

Implement the indestructible anti-grav commander as an authoritative physical entity with deterministic horizontal/vertical movement, height-aware collision, friendly-robot docking hooks, and war-base heli-pad interaction.

## Spec references

- `_specs/functional-spec.md` §8 — commander / anti-grav command vehicle
- `_specs/technical-spec.md` §9 — commander model
- `_specs/open-questions.md` §12, §13, §14

## Dependencies

- Milestone 2 world/occupancy model.

## Deliverable

A deterministic commander subsystem supporting integer X/Y, discrete integer Z, simultaneous horizontal/vertical transitions, collision against world geometry, commander blocking semantics, friendly docking state, undocking, and heli-pad landing detection.

## Workstreams and candidate tasks

### Commander state model
Implement `FREE` and `DOCKED` modes, authoritative position/transition state, owner identity, and invariant validation.

### Horizontal movement
Implement square-to-square authoritative movement with duration expressed in simulation ticks and deterministic legality checks.

### Vertical movement
Implement discrete Z transitions driven by rise/descend intent while allowing simultaneous horizontal movement. Exact limits/speeds remain data-driven until verified.

### Height-aware collision
Add vertical ranges for commander and static/robot entities and reject horizontal/vertical movement when physical ranges intersect.

### Commander blocking
Expose collision queries required for later robot movement so a low commander can block a robot without being targetable or destructible.

### Docking/undocking
Implement automatic friendly-robot docking when descending onto the robot top, derived position while docked, and undocking by rising. Initially use test robots/height fixtures if the full robot subsystem is not yet present.

### War-base heli-pad interaction
Detect valid landing on the owning player’s heli-pad and emit the engine state/event needed to enter construction later.

## Parallelization

Movement transitions, collision helpers, and docking state can be developed in parallel against agreed commander/world interfaces, followed by one integration task covering simultaneous movement and collision edge cases.

## Acceptance criteria

- Commander is physical, authoritative, indestructible, untargetable, and unable to take damage.
- X/Y and committed Z values are integers.
- Horizontal and vertical transitions can occur concurrently.
- Height-aware collision is deterministic.
- Commander can fly over obstacles only with sufficient clearance.
- Commander can block movement at intersecting height.
- Friendly docking is automatic and derives commander placement from robot height.
- Undocking occurs through ascent.
- Landing on a friendly war-base heli-pad produces the expected construction-entry state/event.

## Milestone integration scenario

On a fixture map, move a commander over low and tall obstacles at several heights, verify blocked and allowed paths, dock onto a friendly robot-height fixture, move while docked, undock, and land on the owner’s heli-pad. Replay the command stream and verify identical state/events.

## Out of scope

- Full robot movement/control.
- Construction UI or resource deduction.
- Weapon targeting or damage.
- Frontend interpolation/visual animation.

## Open questions / blockers

Research/decision tasks are required before finalizing:

- commander-vs-commander collision (`open-questions.md` §12);
- minimum/maximum Z and Z-step speed (`§13`);
- descending onto an enemy robot (`§14`).

The subsystem may be implemented with explicit configuration/pluggable policy around these points, but the milestone is not fully fidelity-complete until the required semantics are resolved and approved. Agents must surface conflicting source evidence for owner review.

## Definition of done

- All milestone issues are closed by merged PRs.
- Commander deterministic integration scenario passes.
- Resolved fidelity decisions are reflected in authoritative specs before their code is merged.
- No frontend/network dependency exists in commander logic.
