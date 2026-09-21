# Nether Earth v1 — Milestone Dependency Guide

This file defines the canonical cross-milestone dependency model. Individual milestone files remain authoritative for their detailed scope and acceptance criteria.

## Dependency semantics

Milestone specs distinguish two kinds of dependency:

- **Start dependencies** — the contracts or earlier capabilities that must exist before work on the milestone can safely begin.
- **Completion dependencies** — the milestones/capabilities that must be complete before the milestone itself can be declared done.

A start dependency does not imply that every upstream milestone must be entirely complete before any work begins. Stable interfaces, deterministic fixtures, generated protocol contracts, and recorded snapshots may be used to unlock safe parallel work.

Completion dependencies are strict: a milestone may not claim completion while required upstream behavior remains incomplete.

## Canonical dependency graph

```text
M0  Repository & Agentic Development Foundation
 │
 ▼
M1  Deterministic Engine Foundation
 │
 ▼
M2  Map & World Model
 │
 ├──────────────► M3  Commander Movement, Collision & Docking
 │                  │
 │                  ▼
 └──────────────► M4  Robots, Construction & Economy
                    │
                    ▼
                  M5  Robot Movement, Orders, Navigation & Capture
                    │
                    ▼
                  M6  Combat, Damage, Destruction & Victory

Parallel groundwork enabled by stable contracts:

M0 + M1 ───────────────► M7 protocol/runtime groundwork
M0 + M1 + M2 ──────────► M8 renderer/UI groundwork using fixtures

Completion joins:

M2–M6 complete ─────────► M7 Match Runtime, Protocol & Multiplayer complete
M2–M7 complete ─────────► M8 Frontend Rendering, Controls & Game UI complete
M2–M8 complete ─────────► M9 Full PvP Vertical Slice
M9 complete ─────────────► M10 Hardening, Deployment & v1 Release
```

## Milestone relationships

### M0 — Repository & Agentic Development Foundation

- Start dependencies: none.
- Completion unlocks the repository/tooling skeleton for all later work.

### M1 — Deterministic Engine Foundation

- Start dependency: M0.
- Establishes the deterministic command/state/event/snapshot contracts used by every later engine/runtime milestone.

### M2 — Map & World Model

- Start dependency: M1.
- Owns world geometry, terrain, occupancy, structures, and canonical structure interaction points such as heli-pads, exits, and capture locations.

### M3 — Commander Movement, Collision & Docking

- Start dependency: M2 world/occupancy contract.
- Owns commander movement and collision semantics, including the blocking contract consumed by robot movement.

### M4 — Robots, Construction & Economy

- Start dependencies: M2 world/structure interaction contract and M3 heli-pad/commander interaction contract.
- Owns canonical robot/module identity, construction metadata/costs, stack/height, resources, economy, and robot creation.

### M5 — Robot Movement, Orders, Navigation & Capture

- Start dependencies: M2 world contract, M3 commander-blocking contract, and M4 robot contract.
- Owns movement, routing, target selection, order lifecycle, capture, and combat **engagement intent**.
- Does not own actual firing or damage.

### M6 — Combat, Damage, Destruction & Victory

- Start dependencies: M4 component/height contract and M5 movement/range/target/engagement contract.
- Owns actual weapon firing, projectiles, damage, nuclear effects, destruction, and victory.
- Consumes the M4 canonical component catalog rather than defining a second weapon catalog.

### M7 — Match Runtime, Protocol & Multiplayer

- Groundwork may start after M0/M1 using deterministic fixtures and stable engine contracts.
- Completion requires the complete v1 engine surface from M2–M6.
- Owns transport, session/match lifecycle, WebSocket orchestration, snapshots/resynchronization, and replay/debug logging.

### M8 — Frontend Rendering, Controls & Game UI

- Renderer/UI groundwork may start after M0/M1/M2 using deterministic snapshots and protocol fixtures.
- Completion requires the complete v1 engine behavior and stable M7 transport/protocol.
- Owns rendering, input collection, UI, interpolation, and authoritative-state presentation.
- Does not require a complete start-to-victory live match; that belongs to M9.

### M9 — Full PvP Vertical Slice

- Starts only after M2–M8 are complete; M0/M1 are transitively complete.
- Integrates already-complete subsystem work into one real PvP game from create/join through victory.
- Missing subsystem behavior found here is fixed in the owning subsystem rather than becoming new M9 product logic.

### M10 — Hardening, Deployment & v1 Release

- Start dependency: M9 complete.
- Owns production deployment, security/operational hardening, observability, soak testing, release gates, and v1 operational readiness.

### CR001 — Open Questions Resolution

- Change request, spec: `cr001-open-questions.md`. Start dependency: M9 merged.
- Implements the owner decisions for open questions 4, 8, 17, 18, 19, and 20, changing behavior delivered by M2, M3, M5, and M6.
- Must complete before the M9.8 gate and before M10 freezes rules.

## Cross-milestone ownership rules

To prevent duplicate logic across milestones:

- M2 owns authoritative structure geometry and interaction points.
- M3 owns commander physical/collision semantics.
- M4 owns canonical robot/module identity, construction costs, component ordering, and physical height.
- M5 owns robot movement, navigation, autonomous target selection, capture, and engagement intent.
- M6 owns firing, projectiles, damage/destruction, and victory.
- M7 owns networking/runtime orchestration, not gameplay legality.
- M8 owns presentation/input/interpolation, not gameplay legality.
- M9 integrates; it does not become a fallback owner for unfinished subsystem logic.

## Open-question ownership

Current open questions map to milestones as follows:

- M2: questions 2, 15
- M3: questions 12, 13, 14
- M4: questions 1, 10
- M5: questions 3, 4, 5, 6, 7, 11
- M6: questions 3, 8, 9
- M7: question 16
- CR001: questions 4, 8, 17, 18, 19, 20 (owner decisions 2026-09-21)

Question 3 (miles-to-grid conversion) is resolved in M5 because movement/orders need it first; M6 consumes that resolved conversion for weapon ranges and nuclear radius.

No implementation milestone may silently resolve an open question. Research can proceed autonomously, but conflicting evidence or a required semantic choice is escalated to the project owner and reflected in the authoritative specs before implementation is finalized.
