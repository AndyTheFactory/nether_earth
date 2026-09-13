# AGENTS.md

## Purpose

This repository is developed from the specifications in `_specs/`.

Agents must treat the specifications as the product contract and must not invent gameplay rules or architecture decisions that are not supported by them.

## Source of truth

Read these files before planning or implementing work:

1. `_specs/functional-spec.md` — gameplay and product behavior
2. `_specs/technical-spec.md` — architecture and implementation constraints
3. `_specs/open-questions.md` — unresolved decisions that must not be guessed
4. `_specs/references.md` — authoritative reference material
5. `_specs/agentic-programming-prd.md` — agentic development workflow

When documents conflict, stop and surface the conflict rather than silently choosing an interpretation.

For gameplay fidelity, use the priority defined in the functional specification:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

## Non-negotiable architecture rules

- The game engine is a separate pure-Python package.
- The engine owns all gameplay rules and validation.
- The engine must not depend on FastAPI, WebSockets, frontend code, deployment code, or network I/O.
- The engine must remain deterministic.
- Authoritative simulation runs at the fixed tick rate defined in the technical specification.
- Gameplay time is derived from simulation ticks, never independent wall-clock timers.
- The backend/match layer orchestrates sessions, commands, broadcasting, lifecycle, and replay logging; it does not implement gameplay rules.
- The frontend renders state, collects input, interpolates visuals, and presents UI; it does not decide gameplay legality.
- Shared protocol schemas are the source of truth for messages crossing frontend/backend boundaries.

## Open questions are hard gates

Before implementing a requirement, check `_specs/open-questions.md`.

If the task depends on an unresolved question:

- do not invent a value or behavior;
- do not hide an assumption in code;
- mark the task as blocked by that decision, or perform an explicit research task if requested;
- identify the exact open question in the implementation report.

It is acceptable to build interfaces, data structures, fixtures, or placeholders that leave the unresolved rule configurable, provided they do not implicitly choose an answer.

## Task workflow

For each implementation task:

1. Read the relevant specification sections.
2. Identify dependencies and open questions.
3. State the acceptance criteria before editing code.
4. Make the smallest coherent implementation that satisfies the task.
5. Add or update tests for the behavior.
6. Run the relevant test/lint/type-check commands.
7. Review the diff against the specification, not just against the tests.
8. Report what changed, tests run, remaining risks, and any blocked decisions.

Prefer independent tasks that can be developed in separate branches/worktrees and integrated only after their contracts are stable.

## Spec traceability

Every substantial feature should be traceable to one or more specification sections.

When practical, include the relevant spec section in the task, PR description, test name, or implementation notes. Do not add noisy comments to every line of code merely for traceability.

If implementation reveals that a specification is incomplete or contradictory, update the specification only when the new decision has been explicitly resolved. Otherwise update `_specs/open-questions.md` instead.

## Testing requirements

Changes are not complete until their relevant verification passes.

For engine work, prefer deterministic tests that exercise commands and simulation ticks directly. Important engine behaviors should be reproducible from:

- initial state / scenario
- map/version
- RNG seed, if randomness is involved
- accepted command stream

Add regression tests for bugs.

For protocol changes, verify both generated/frontend types and backend validation remain aligned.

For frontend work, do not duplicate engine rule logic merely to make UI tests pass.

## Implementation principles

- Keep rule constants and tunable gameplay data centralized rather than duplicated across layers.
- Prefer explicit domain models and pure functions inside the engine.
- Avoid nondeterministic iteration when it can influence game outcomes.
- Avoid floating-point gameplay state when integer ticks/grid values can express the rule.
- Keep changes scoped; do not perform unrelated refactors during feature work.
- Do not introduce PostgreSQL, Redis, message brokers, Kubernetes, React, or other out-of-scope infrastructure for v1 unless the specifications are changed first.
- Do not add AI-opponent implementation to v1. The architecture may support a future AI controller without altering the engine contract.

## Git and integration

- One task should produce one coherent change set.
- Prefer a dedicated branch/worktree for parallel tasks.
- Do not mix unrelated fixes into the same commit.
- Never overwrite another agent's work to resolve a conflict; reconcile both changes explicitly.
- Integration should happen only after the task's acceptance criteria and tests pass.

## Completion report

When finishing a task, provide a concise report containing:

- specification sections implemented;
- files changed;
- tests/checks run and their result;
- unresolved decisions or assumptions;
- follow-up work, if any.

A task that depends on an unresolved product decision is not complete merely because the code compiles.