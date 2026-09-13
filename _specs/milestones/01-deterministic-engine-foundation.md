# Milestone 1 — Deterministic Engine Foundation

## Goal

Establish the pure-Python authoritative simulation core on which every gameplay system will depend. The milestone must prove that game state advances only through deterministic simulation ticks and explicit commands, producing deterministic state and events.

## Spec references

- `_specs/functional-spec.md` §6 — game clock
- `_specs/technical-spec.md` §4.1 — game engine ownership
- `_specs/technical-spec.md` §5 — simulation timing, clock, determinism
- `_specs/technical-spec.md` §6 — scenario model
- `_specs/agentic-programming-prd.md` §17 — deterministic scenario testing

## Dependencies

- Milestone 0 complete.

## Deliverable

A standalone engine package capable of creating a minimal game state, accepting validated commands, stepping at 20 Hz, emitting ordered engine events, serializing deterministic snapshots, and replaying a command stream to the same result.

## Workstreams and candidate tasks

### Engine state and identifiers
Define stable entity/player identifiers, top-level `GameState`, immutable-or-controlled mutation conventions, and deterministic collection ordering.

### Tick clock
Implement the authoritative tick counter and locked conversions:

- 20 simulation ticks per real second;
- 120 ticks per in-game hour;
- 2,880 ticks per in-game day.

No gameplay timer may use wall-clock time.

### Command and event model
Create explicit engine command and event types, validation entry points, deterministic command ordering, and rejection/error semantics.

### Scenario and seeded initialization
Implement scenario metadata, player initialization, starting general resources, map/scenario version references, and match-local seeded RNG ownership without yet implementing map/world behavior.

### Simulation step contract
Implement the canonical engine API equivalent to:

```python
state = new_game(map_data, scenario, players, seed)
state, events = step(state, commands)
```

The exact Python API may vary, but engine ownership and deterministic semantics may not.

### Snapshot and replay fixtures
Create canonical state serialization sufficient for deterministic assertions and a test fixture format that can replay commands by tick.

## Parallelization

State/types, command/event definitions, scenario initialization, and test-fixture tooling may begin in parallel once their interfaces are agreed. The simulation-step integration task combines them and owns deterministic ordering rules.

## Acceptance criteria

- Engine imports independently of FastAPI/network/frontend code.
- A game state has an authoritative integer tick.
- Stepping the engine advances time deterministically.
- Commands are explicit inputs; gameplay is not driven by wall-clock callbacks.
- Same initial state + scenario/map version + RNG seed + accepted command stream produces byte-for-byte or canonical-equivalent final snapshots and ordered events.
- Invalid commands fail deterministically.
- Seeded randomness, where scaffolded, is owned locally by the engine.
- Determinism tests run repeatedly without intermittent differences.

## Milestone integration scenario

Create a minimal two-player scenario with a fixed seed. Submit a known command stream over several hundred ticks, serialize final state and emitted events, replay from the same initial conditions, and prove both runs are identical. Run a second seed or command stream and prove the result changes only where expected.

## Out of scope

- Full map geometry and occupancy.
- Commander or robot movement.
- Factories, economy, construction, orders, combat.
- WebSockets/multiplayer orchestration.
- Rendering.

## Open questions / blockers

No existing gameplay open question should block the core tick/state/command architecture. Do not encode unresolved values from `_specs/open-questions.md` merely to enrich the test scenario.

Human review is required if an implementation choice would alter the engine contract, make deterministic replay impossible, or create conflicting authoritative ordering semantics not already specified.

## Definition of done

- All milestone tasks are GitHub issues closed by merged PRs.
- Determinism integration scenario passes from a clean checkout.
- Engine has no forbidden dependency leakage.
- Relevant quality gates pass.
- No unresolved gameplay decision has been silently chosen.
