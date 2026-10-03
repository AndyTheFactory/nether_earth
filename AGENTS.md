# AGENTS.md

## Purpose

This repository is developed from the specifications in `_specs/`.

Agents must treat the specifications as the product contract and must not invent gameplay rules or architecture decisions that are not supported by them.

## Source of truth

Read these files before planning or implementing work:

1. `_specs/functional-spec.md` — gameplay and product behavior (the current rules)
2. `_specs/technical-spec.md` — architecture and implementation constraints
3. `_specs/open-questions.md` — unresolved decisions that must not be guessed
4. `_specs/resolved-questions.md` — decided questions: the decision, its source and evidence
5. `_specs/deviations-from-original.md` — intentional differences from the ZX Spectrum, not to be "fixed"
6. `_specs/references.md` — authoritative reference material
7. `_specs/agentic-programming-prd.md` — agentic development workflow
8. `docs/mechanics/` — how the engine implements each rule, module by module, with constants and the tests that pin them

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

If not specified otherwise use git worktrees for task branches.
Clean up your worktree after finishing a task to avoid clutter and potential conflicts.

The original milestones (M0–M10) and change requests CR001–CR005 are complete; their decisions now live in the specifications above. New work arrives as change requests tracked as GitHub issues: the owner's request and decisions go in the issue, the work is split into task issues that reference it, and the specifications are updated in the same pull request as the behaviour they describe.

Implement change-request tasks in a parallel (where possible) and incremental manner, ensuring that each task can be independently verified and integrated.

Change-request planning should result in clear GitHub issues that link the parent change request and carry the context a task needs.

Implement the issue on a dedicated branch/worktree where practical.
Create commits after relevant changes have been made and tested.

When finishing a task open a pull request that references the issue and explains how the acceptance criteria were satisfied. Merge the PR only when the task is verified and no blocking findings remain.

Always create PR and if there are no blocking findings, merge it after your review.

## Human review and autonomous execution

Agents should proceed autonomously on routine implementation choices that are clearly inside the locked specifications and architecture.

Human review is mandatory when an agent encounters:

- an unresolved product/gameplay decision;
- conflicting authoritative evidence or specifications;
- a semantic merge/integration conflict where either resolution changes behavior;
- a required architecture or scope change;
- a blocker for which proceeding would require inventing behavior.

Do not interrupt the project owner for ordinary implementation choices, naming, local refactors, or tool usage when those choices preserve the specification and task scope.

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
