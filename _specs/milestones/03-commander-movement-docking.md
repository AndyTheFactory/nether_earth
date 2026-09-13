# Milestone 3 — Commander Movement, Collision & Docking

## Goal

Implement the indestructible anti-grav commander as an authoritative physical entity with deterministic horizontal/vertical movement, height-aware collision, friendly-robot docking, enemy-robot contact behavior, commander-vs-commander collision, and war-base heli-pad interaction.

## Spec references

- `_specs/functional-spec.md` §8 — commander
- `_specs/technical-spec.md` §9 — commander model
- `_specs/open-questions.md` §12–§14

## Dependencies

- Milestone 2 world/occupancy model.

## Deliverable

A deterministic commander subsystem supporting integer X/Y, integer altitude, simultaneous horizontal/vertical movement, height-aware collision against structures/robots/commanders, friendly docking/undocking, enemy-robot physical contact, and heli-pad landing detection.

## Locked commander rules

Spectrum-compatible vertical defaults at 20 Hz:

```text
commander_min_altitude = 0
commander_max_altitude = 48
commander_vertical_update_ticks = 4
commander_ascent_step = 2
commander_descent_step = 1
```

- vertical physics updates every 4 simulation ticks;
- ascent is +2 per update;
- gravity/descent is -1 per update;
- ascent and descent are intentionally asymmetric;
- horizontal and vertical movement may occur simultaneously;
- automatic elevation after exiting a robot/war base uses the same +2 semantics;
- all numeric values belong to centralized engine game-rule configuration.

Commander-vs-commander collision:

- commanders are physically collidable;
- same X/Y is allowed only when vertical ranges do not overlap;
- overlapping vertical ranges block horizontal and vertical movement, including descent;
- commanders remain indestructible, untargetable, and immune to damage.

Enemy-robot landing/contact:

- enemy robots are physical collision surfaces;
- descent stops at the top of the enemy robot stack;
- commander may rest there while geometry permits;
- no docking, control transfer, or contact damage occurs;
- docking/control remains friendly-only.

## Workstreams and candidate tasks

### Commander state model
Implement `FREE` and `DOCKED` modes, authoritative position/altitude state, owner identity, and invariant validation.

### Horizontal movement
Implement cell-to-cell authoritative movement with deterministic legality checks.

### Vertical movement
Implement the configured 4-tick cadence and +2/-1 Spectrum default steps, min/max altitude clamping, simultaneous horizontal motion, and automatic-elevation behavior.

### Height-aware collision
Add vertical collision ranges for commander, static components, robots, and opposing commander.

### Commander blocking
Expose collision queries required by robot movement so a low commander can block a robot without being targetable or destructible.

### Docking/undocking
Implement automatic friendly docking when descending onto robot top, derived placement while docked, and undocking through ascent.

### Enemy-robot contact
Implement physical top-surface collision without docking/control transfer/damage.

### War-base heli-pad interaction
Detect valid landing on the owning player’s heli-pad and emit the state/event needed to enter construction.

## Parallelization

Movement, collision helpers, docking state, and heli-pad interaction can be developed in parallel against agreed commander/world interfaces, followed by integration coverage for simultaneous motion and collision edge cases.

## Acceptance criteria

- Commander is physical, authoritative, indestructible, untargetable, and immune to damage.
- X/Y and altitude are authoritative integers.
- Vertical defaults are 0..48, update every 4 ticks, +2 ascent, -1 descent.
- Horizontal and vertical movement can occur concurrently.
- Height-aware collision is deterministic.
- Commander can fly over obstacles only with sufficient clearance.
- Commander can block robots at intersecting height.
- Opposing commanders block each other only when vertical ranges overlap.
- Friendly docking is automatic and derives placement from robot height.
- Enemy robot contact stops descent at robot top without docking or damage.
- Undocking occurs through ascent.
- Landing on a friendly war-base heli-pad produces construction-entry state/event.
- Commander rule values come from centralized config rather than scattered magic numbers.

## Milestone integration scenario

On a fixture map, move two commanders over low/tall obstacles at several heights, verify allowed/blocked overlap, verify commander-vs-commander vertical collision, dock onto a friendly robot fixture, descend onto an enemy robot fixture without docking, undock, and land on the owner’s heli-pad. Replay the command stream and verify identical state/events.

## Out of scope

- Full robot movement/control.
- Construction UI/resource deduction.
- Weapon targeting/damage.
- Frontend interpolation/visual animation.

## Open questions / blockers

The former commander product questions (§12–§14) are resolved. No commander product blocker remains for this milestone.

If later Spectrum evidence establishes special local ceiling behavior beyond normal collision clearance, treat that as a fidelity refinement rather than a blocker to the current locked vertical model.

## Definition of done

- All milestone issues are closed by merged PRs.
- Commander deterministic integration scenario passes.
- Locked commander semantics match the authoritative specs.
- No frontend/network dependency exists in commander logic.