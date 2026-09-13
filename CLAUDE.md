# CLAUDE.md

Read `AGENTS.md` first. It defines the repository-wide development rules and applies to all Claude Code work in this repository.

## Before coding

Read the relevant sections of:

- `_specs/functional-spec.md`
- `_specs/technical-spec.md`
- `_specs/open-questions.md`
- `_specs/references.md` when gameplay fidelity or historical behavior matters
- `_specs/agentic-programming-prd.md` for the development workflow

Do not rely on memory of Nether Earth when the repository specifications or references can answer the question.

## Planning

For non-trivial tasks, make a short implementation plan before editing. The plan should identify:

- affected subsystem: engine, backend/match layer, protocol, frontend, data, deployment, or tests;
- relevant spec sections;
- dependencies on other work;
- whether any item in `_specs/open-questions.md` blocks implementation;
- tests needed for acceptance.

Keep plans focused on implementation. Do not redesign unrelated parts of the repository.

## Hard stop conditions

Stop and ask for a decision, or report the task as blocked, when:

- a required behavior is explicitly unresolved in `_specs/open-questions.md`;
- two specifications contradict each other;
- implementation would require changing a locked architecture decision;
- a change would move gameplay authority from the engine into the backend or frontend;
- a requested v1 feature is explicitly out of scope.

Do not resolve these situations by choosing the most convenient implementation.

## Engine work

The engine must stay pure, deterministic, and independent of transport/UI code.

When implementing engine behavior:

- express time in simulation ticks;
- use integer authoritative grid coordinates and the specified discrete state models;
- keep gameplay validation inside the engine;
- use match-local seeded randomness if randomness is necessary;
- add deterministic tests that reproduce behavior from state + commands + ticks;
- avoid frontend or networking concepts in engine APIs.

A useful conceptual contract is:

```python
state = engine.new_game(map_data, scenario, players, seed)
state, events = engine.step(state, commands)
```

The exact API may evolve, but maintain this separation of concerns.

## Backend work

FastAPI and the match layer may handle:

- match creation/join/readiness;
- guest sessions;
- WebSocket transport;
- command queues;
- fixed-tick orchestration;
- state broadcasting;
- reconnect/runtime lifecycle;
- replay/debug persistence.

They must not independently decide movement legality, capture, economy, construction, combat, ownership, or victory.

## Frontend work

The frontend may handle rendering, interpolation, input collection, menus, overlays, and connection state.

Do not reproduce authoritative game rules in TypeScript. UI prediction or convenience validation must never become the source of truth.

## Protocol work

Treat shared JSON Schema as the protocol source of truth.

When a message changes:

1. update the schema;
2. update/regenerate TypeScript types;
3. update backend validation models;
4. update producers and consumers;
5. add compatibility/validation tests as appropriate.

Avoid parallel hand-written protocol definitions that can drift.

## Working style

- Prefer small, reviewable changes.
- Search the repository before introducing a new abstraction or constant.
- Preserve existing naming and structure unless there is a concrete reason to change them.
- Add regression tests with bug fixes.
- Do not leave TODOs that silently encode an unresolved gameplay decision; reference the corresponding open question instead.
- Do not add dependencies or infrastructure casually.

## Verification

Before declaring a task complete:

- run the tests relevant to the modified subsystem;
- run lint/type checks when configured;
- inspect the diff for accidental scope growth;
- compare the result with the relevant spec sections;
- confirm that no unresolved question was silently answered by the implementation.

If the full suite cannot be run, state exactly what was and was not verified.

## Final response

At the end of an implementation task, summarize:

- what was implemented;
- specification sections addressed;
- files changed;
- verification performed;
- any unresolved questions, assumptions, or recommended follow-up.

Keep `CLAUDE.md` concise. General repository rules belong in `AGENTS.md`; detailed product and architecture knowledge belongs in `_specs/`, not duplicated here.