# Nether Earth — Agentic Programming Flow PRD

## 1. Purpose

Define an agentic software-development workflow for implementing the Nether Earth clone from the repository specifications.

The workflow should allow one or more coding agents to plan, implement, test, review, and integrate the project with high autonomy while preserving the project's core fidelity requirement: agents must implement the locked specifications, not invent missing game rules.

This PRD governs **how the software is built**. The game behavior itself remains defined by:

- `_specs/functional-spec.md`
- `_specs/technical-spec.md`
- `_specs/open-questions.md`
- `_specs/resolved-questions.md`
- `_specs/deviations-from-original.md`
- `_specs/references.md`

How the engine implements the rules is explained in `docs/mechanics/`.

When this PRD conflicts with those files on game behavior or architecture, the functional and technical specifications take precedence.

---

## 2. Product goal

Create a repeatable agentic development flow in which a human can give a high-level instruction such as:

> Implement the next change request from the Nether Earth specs.

The system should then be able to:

1. read the current specifications and repository state;
2. identify implementation-ready work;
3. decompose it into bounded tasks with explicit acceptance criteria;
4. identify dependencies and parallelizable work;
5. assign work to isolated coding agents;
6. require each task to implement tests and verification evidence;
7. review changes against both code quality and specification fidelity;
8. integrate compatible changes in dependency order;
9. stop and escalate when implementation requires an unresolved design/gameplay decision;
10. leave the repository in a traceable state where every significant change can be tied back to a specification requirement.

The desired outcome is not maximum agent autonomy. It is **maximum safe autonomy within explicitly locked decisions**.

---

## 3. Core principles

### 3.1 Specifications are authoritative

Agents must treat the repository specifications as the source of truth.

Implementation may clarify structure, naming, APIs, tests, or internal representation, but must not change locked game behavior without an explicit specification update.

### 3.2 Open questions are hard autonomy boundaries

Anything listed in `_specs/open-questions.md` is unresolved.

An agent may:

- research the question;
- gather evidence from the references;
- write an investigation report;
- propose alternatives;
- implement code that deliberately leaves the value data-driven or pluggable where the technical spec already allows this.

An agent must **not** silently choose a gameplay value or semantic rule merely to unblock implementation.

If a task requires such a decision, it enters `BLOCKED_DECISION` state and is escalated to the human.

### 3.3 The engine is the primary correctness boundary

The pure-Python game engine owns all gameplay rules.

Agent plans must preserve the architectural boundary that:

- the engine owns game legality and deterministic state transitions;
- the Match layer owns runtime orchestration;
- FastAPI owns transport/session concerns;
- the frontend owns rendering, input collection, interpolation, and UI;
- frontend/backend transport code must not duplicate game-rule decisions.

### 3.4 Determinism is a first-class acceptance criterion

Any task that affects simulation state must preserve deterministic behavior:

```text
same initial state
+ same map/scenario/version
+ same RNG seed
+ same accepted command stream
= same resulting state
```

Tests should prefer tick-driven deterministic execution rather than wall-clock waits.

### 3.5 Small, reviewable changes

Agents should prefer bounded tasks and commits over broad repository rewrites.

Each implementation task should have:

- one clear purpose;
- identified spec references;
- defined files/modules likely to change;
- acceptance criteria;
- tests or verification steps;
- explicit non-goals.

### 3.6 Evidence over confidence

An agent does not complete a task because the code "looks correct".

Completion requires verification evidence such as:

- unit tests;
- deterministic replay tests;
- schema validation;
- type checking;
- linting;
- backend/frontend integration tests;
- manual rendering evidence where automated verification is not practical.

---

## 4. Scope

### Included

The agentic flow covers:

- repository inspection;
- spec parsing and requirement extraction;
- change-request planning;
- task decomposition;
- dependency analysis;
- parallel task execution;
- isolated branches/worktrees;
- implementation;
- automated test creation;
- code review;
- spec-conformance review;
- integration;
- regression verification;
- documentation updates required by implementation;
- research tasks for unresolved rules;
- human decision escalation;
- progress/status recording.

### Out of scope

The flow does not autonomously:

- redefine product scope;
- resolve gameplay questions without evidence/human approval;
- rewrite the locked architecture merely because another design is preferred;
- merge knowingly failing code;
- weaken tests to make a change pass;
- update functional gameplay behavior without updating the authoritative specs;
- deploy production changes without an explicit deployment instruction;
- redefine v1 scope beyond the AI opponent already locked in by CR004 (`functional-spec.md` §3.1) without an owner decision.

---

## 5. Users

### Primary user

The project owner/developer supervising the Nether Earth implementation.

The primary user wants to:

- specify intent at change-request or feature level rather than micromanaging individual files;
- run multiple agents safely in parallel;
- minimize repeated explanation of project rules;
- receive explicit escalation only when a genuine decision is required;
- inspect concise evidence that completed work matches the specs.

### Secondary actor: coding agent

An implementation agent should receive enough bounded context to finish one task without redefining the surrounding system.

### Secondary actor: review agent

A review agent evaluates the implementation independently against:

- assigned acceptance criteria;
- relevant specs;
- architectural boundaries;
- test quality;
- regressions and unintended scope.

---

## 6. Required repository inputs

Before planning work, the orchestrator must inspect at minimum:

1. `_specs/functional-spec.md`
2. `_specs/technical-spec.md`
3. `_specs/open-questions.md`
4. `_specs/resolved-questions.md` and `_specs/deviations-from-original.md`
5. `_specs/references.md`
6. `docs/mechanics/` pages for the areas the work touches;
7. current repository tree;
8. current branch and outstanding changes;
9. existing tests and CI configuration.

As the repository grows, repository-local agent instructions such as `AGENTS.md` or equivalent must also be loaded before implementation.

---

## 7. Requirement traceability

Every implementation task must contain a `Spec References` section that points to the relevant specification sections.

Example:

```markdown
## Spec References
- Functional Spec §6 — Game clock
- Technical Spec §5 — Simulation timing and game clock
```

For requirements originating from a currently unresolved question, the task must instead identify the open-question item and be classified as either:

- `RESEARCH_ONLY`, or
- `BLOCKED_DECISION`.

The implementation flow should make it possible to answer:

> Which task/commit implemented this requirement?

and:

> Which spec requirement justified this code?

---

## 8. Work hierarchy

The agentic flow uses four levels.

### 8.1 Change request

A meaningful capability or change the owner requests, tracked as a GitHub issue that records the request and the owner's decisions. The original milestones (M0–M10: skeleton, engine foundation, map, commander, construction, orders, combat, multiplayer, frontend, PvP slice, release hardening) and change requests CR001–CR005 are complete, and their decisions are folded into the specifications.

Examples:

- a fidelity correction from new disassembly evidence;
- a playtest fix list;
- a new mode such as the AI opponent;
- a hardening or operations change.

### 8.2 Workstream

A coherent technical area within a change request.

Typical workstreams:

- engine;
- protocol;
- backend/match runtime;
- frontend/rendering;
- test infrastructure;
- data/map extraction;
- deployment.

### 8.3 Task

A bounded implementation unit suitable for one agent and one branch/worktree.

### 8.4 Integration task

A task specifically responsible for bringing several completed parallel tasks together and running cross-component verification.

Integration is a distinct activity and should not be treated as an incidental side effect of the last implementation task.

---

## 9. Task states

Each task must have exactly one state:

```text
PLANNED
READY
IN_PROGRESS
BLOCKED_DEPENDENCY
BLOCKED_DECISION
IN_REVIEW
CHANGES_REQUESTED
VERIFIED
INTEGRATED
ABANDONED
```

### State rules

- `READY` means all dependencies and product decisions required for implementation are available.
- `BLOCKED_DECISION` means the agent found an unresolved semantic/product question that cannot safely be inferred.
- `VERIFIED` means implementation and task-level acceptance criteria passed independently of integration.
- `INTEGRATED` means the change has been incorporated with its dependent work and integration gates pass.

---

## 10. Agent roles

One physical agent/runtime may perform more than one role, but responsibilities must remain logically separate.

### 10.1 Orchestrator

Responsibilities:

- inspect specs and repository state;
- choose the next implementation-ready change request;
- decompose work;
- detect dependencies;
- identify safe parallelism;
- prevent tasks from crossing unresolved-question boundaries;
- dispatch tasks;
- track task state;
- schedule integration;
- present human decision requests.

The orchestrator should not make hidden gameplay decisions in order to simplify its plan.

### 10.2 Research agent

Used when a spec explicitly requires fidelity research.

Responsibilities:

- inspect project references in priority order;
- collect concrete evidence;
- describe conflicting evidence;
- recommend a resolution;
- identify which spec sections would change;
- avoid implementing the unresolved behavior unless separately approved.

### 10.3 Implementation agent

Responsibilities:

- implement exactly the assigned task;
- inspect relevant existing code before modification;
- add/update tests;
- preserve architectural boundaries;
- document implementation-specific decisions;
- run required verification;
- produce a completion report.

### 10.4 Review agent

Responsibilities:

- review the diff rather than trusting the implementation summary;
- compare implementation against task acceptance criteria and specs;
- verify tests cover required behavior rather than merely code paths;
- identify accidental architecture leakage;
- identify unsupported invented behavior;
- classify findings by severity;
- approve or request changes.

### 10.5 Integration agent

Responsibilities:

- combine verified task branches in dependency order;
- resolve mechanical conflicts without changing behavior;
- escalate semantic conflicts;
- run cross-component verification;
- confirm generated artifacts are synchronized;
- report integration status.

---

## 11. Planning flow

### Step 1 — Establish repository state

The orchestrator records:

- current base branch/commit;
- existing implementation status;
- test status;
- uncommitted or conflicting work;
- implemented versus unimplemented spec areas.

### Step 2 — Build a requirement inventory

The orchestrator maps relevant functional requirements to technical implementation areas.

Requirements should be classified as:

```text
LOCKED_IMPLEMENTABLE
LOCKED_DEPENDENT
OPEN_RESEARCHABLE
OPEN_BLOCKING
ALREADY_IMPLEMENTED
```

### Step 3 — Select a change request

Prefer the smallest change request that creates a useful, testable capability and unblocks later work.

Engine and determinism changes come before presentation polish when both are pending.

### Step 4 — Decompose into tasks

Each task must declare:

```markdown
# Task: <name>

## Goal

## Spec References

## Dependencies

## Expected Scope

## Explicit Non-goals

## Acceptance Criteria

## Required Tests / Verification

## Known Risks

## Open Questions Touched
```

A task that cannot complete its acceptance criteria without resolving an open question must not be marked `READY`.

### Step 5 — Compute dependency graph

The orchestrator distinguishes:

- hard dependency — task cannot begin safely;
- soft dependency — task can proceed using an agreed interface/schema;
- integration dependency — tasks can proceed in parallel but must later be combined.

### Step 6 — Dispatch parallel work

Tasks may run concurrently when:

- they modify independent modules or have a stable agreed interface;
- neither depends on the other's implementation details;
- generated artifacts have a clear ownership rule;
- integration order is known.

---

## 12. Recommended implementation sequencing

The orchestrator may refine this based on repository state, but the default dependency direction should be:

```text
specs / rule data
      ↓
engine primitives + state
      ↓
engine commands + deterministic systems
      ↓
protocol contracts
      ↓
match/runtime backend ─────┐
                          ├── integration / multiplayer vertical slice
frontend network/input ───┤
frontend renderer/UI ─────┘
      ↓
deployment / operational hardening
```

Some frontend renderer work may proceed in parallel using stable protocol fixtures or recorded snapshots.

The implementation must not force engine rules to conform to a prematurely designed frontend API.

---

## 13. Branch and worktree model

Each implementation task should execute on an isolated branch/worktree.

Recommended naming:

```text
agent/<change-request>/<task-slug>
```

Examples:

```text
agent/engine-foundation/tick-clock
agent/engine-foundation/world-state
agent/protocol/base-schemas
```

Rules:

- one active task per branch;
- do not mix unrelated cleanup;
- commits should be logically grouped and buildable where practical;
- task branches are reviewed before integration;
- cross-task merges belong to the integration task/branch.

Parallel agents must not share a mutable working tree.

---

## 14. Implementation-agent contract

Before editing, an implementation agent must be able to state:

1. what requirement it is implementing;
2. where that requirement is specified;
3. which architecture layer owns the behavior;
4. what is explicitly outside the task;
5. how success will be tested.

During implementation the agent must:

- inspect existing code before creating replacement abstractions;
- prefer existing project conventions;
- keep gameplay logic in the engine;
- avoid wall-clock gameplay behavior;
- avoid hidden non-determinism;
- avoid duplicating shared protocol definitions;
- use data-driven placeholders where the specs intentionally leave verified values unresolved;
- stop if implementation exposes a genuine open gameplay decision.

---

## 15. Definition of done for a task

A task can enter `VERIFIED` only when all applicable conditions are met:

- acceptance criteria are satisfied;
- relevant automated tests pass;
- new behavior is tested at the correct architecture layer;
- no known locked spec requirement is violated;
- no unresolved gameplay decision was silently introduced;
- format/lint/type checks pass where configured;
- generated files are synchronized where applicable;
- implementation notes identify any assumptions;
- review agent has no unresolved blocking findings.

Passing tests alone is not sufficient if the code implements the wrong rule.

---

## 16. Quality gates

The flow should gradually enforce the following gates as the repository acquires the corresponding components.

### Engine gate

Required for engine-related tasks:

- unit tests;
- deterministic state-transition tests;
- no FastAPI/network/frontend dependency;
- replayable command inputs for complex scenarios;
- integer tick-based rule timing.

### Protocol gate

Required for protocol changes:

- schema validates;
- generated TypeScript definitions are up to date;
- Python/Pydantic representation matches the shared schema;
- backward compatibility impact is documented when protocol already has consumers.

### Backend gate

Required for backend/match changes:

- transport layer does not implement game rules;
- invalid client commands are validated and forwarded/rejected correctly;
- connection/session behavior is tested;
- match tick orchestration can be tested without real-time sleeps wherever practical.

### Frontend gate

Required for frontend changes:

- game legality is not inferred client-side;
- interpolation does not mutate authoritative state;
- protocol messages conform to generated types;
- input maps into explicit commands;
- renderer can consume deterministic fixtures/snapshots where practical.

### Integration gate

Required before a change request is considered complete:

- all included task-level tests pass together;
- backend and frontend agree on protocol schema/version;
- at least one scenario test covering the change request passes;
- no newly introduced TODO represents a hidden product decision;
- spec traceability is intact.

---

## 17. Deterministic scenario testing

The preferred high-value integration test format is a deterministic scenario fixture.

Conceptually:

```yaml
scenario: two_player_test
seed: 1234
commands:
  - tick: 10
    player: p1
    command: ...
  - tick: 12
    player: p2
    command: ...
expected:
  final_tick: 500
  assertions:
    - ...
```

These fixtures should be usable for:

- engine regression tests;
- backend match tests;
- replay verification;
- frontend snapshot/render test data;
- reproducing bugs.

A bug that can be expressed as a deterministic command stream should preferably receive a regression fixture before or alongside the fix.

---

## 18. Review model

Review occurs in two dimensions.

### 18.1 Software review

Checks:

- correctness;
- maintainability;
- unnecessary complexity;
- test quality;
- concurrency hazards;
- interface stability;
- error handling;
- accidental unrelated changes.

### 18.2 Specification review

Checks:

- correct spec sections were implemented;
- behavior is owned by the correct layer;
- no locked rule was altered;
- no open question was silently resolved;
- tests encode the intended rule;
- implementation remains compatible with future scenario/AI goals already present in the specs.

Both dimensions must pass.

---

## 19. Handling open questions

When work reaches an unresolved item, the orchestrator should choose one of four actions.

### A. Continue without the value

Use when the architecture can be built with a data/configuration seam and no behavior must yet be selected.

Example: define movement-speed tables without filling unverified exact tick values.

### B. Create a research task

Use when repository references can likely resolve the question.

Research output must include:

- evidence source;
- observed behavior/code finding;
- confidence;
- recommended resolution;
- affected spec sections;
- suggested tests.

### C. Escalate to human

Use when evidence is insufficient or a product choice is intentionally required.

Human decision requests should be concise:

```markdown
## Decision required
Question: ...

Why it blocks implementation: ...

Evidence: ...

Options:
A. ...
B. ...

Recommendation: ...

Affected tasks/spec sections: ...
```

### D. Split the task

Complete the independent implementation portion and leave only the genuinely blocked part pending.

The agent should prefer splitting over blocking a large task unnecessarily.

---

## 20. Human checkpoints

Human approval is required when:

- an item in `_specs/open-questions.md` must be resolved;
- research evidence contradicts a locked specification;
- a proposed change alters the locked technology stack or major architecture boundary;
- a task requires reducing functional scope;
- deterministic fidelity and user-experience convenience conflict materially;
- integration exposes two incompatible interpretations of the spec;
- an agent proposes deleting or substantially rewriting already verified behavior.

Human approval is not required for routine implementation details that preserve the contract, such as local naming, private helper decomposition, or test organization.

---

## 21. Completion reports

Every implementation task must produce a concise machine- and human-readable completion report.

Template:

```markdown
# Completion report

## Implemented
- ...

## Spec coverage
- Functional Spec §...
- Technical Spec §...

## Tests / verification
- command: result

## Changed interfaces
- ...

## Assumptions
- ...

## Remaining issues
- none | ...

## Open questions encountered
- none | ...
```

The report is evidence for review, not a substitute for inspecting the diff.

---

## 22. Progress model

The orchestrator should present progress by capability rather than by raw file count.

Recommended change-request status:

```text
Change request: Deterministic engine foundation
Status: 4/6 tasks integrated

Integrated
✓ state primitives
✓ tick clock
✓ command envelope
✓ deterministic RNG

In review
◐ map loader

Blocked
! movement timing tables — Open Question #4
```

This makes unresolved fidelity questions visible instead of hiding them behind nominal percentage completion.

---

## 23. Failure and recovery behavior

### Task implementation failure

The task remains isolated. Other independent tasks may continue.

### Test failure caused by the task

The task cannot enter `VERIFIED`.

### Review failure

Task enters `CHANGES_REQUESTED` with concrete findings.

### Integration conflict

Mechanical conflicts may be resolved by the integration agent.

Semantic conflicts must be returned to the relevant task agents or escalated.

### Agent context loss or interrupted session

Another agent should be able to resume using:

- task definition;
- branch/worktree;
- commit history;
- completion notes if present;
- test output;
- authoritative specs.

The workflow must not depend on hidden conversational state to understand an unfinished task.

---

## 24. Guardrails against common agentic failure modes

### Do not overbuild

Agents must not introduce infrastructure explicitly excluded by the v1 specs, including database, Redis, broker, Kubernetes, or per-match containers.

### Do not modernize gameplay accidentally

Agents must not replace original-style mechanics with conventional RTS behavior merely because it is easier or more familiar.

### Do not leak rules into the frontend

UI convenience cannot become a second game engine.

### Do not use realtime sleeps as simulation tests

Advance deterministic ticks directly where possible.

### Do not resolve uncertainty through arbitrary constants

If an exact rule value is unverified, expose it as unresolved/data-driven rather than pretending it is known.

### Do not weaken tests to unblock integration

Fix code, fixtures, or an explicitly changed specification instead.

### Do not combine speculative refactors with feature implementation

Refactors should be separately justified and bounded.

---

## 25. Original implementation sequence (complete)

The first milestones as originally planned, kept as a record. Milestones M0–M10 are complete; new work follows §8.1.

The exact task plan should be generated from current repository state, but the following is a reasonable initial sequence for the currently spec-only repository.

### Milestone 0 — Repository and quality skeleton

Goal: establish the three runtime components and common development gates without implementing uncertain gameplay.

Possible tasks:

- create engine package skeleton;
- create backend FastAPI skeleton;
- create Vite/PixiJS frontend skeleton;
- create protocol schema/generation skeleton;
- configure Python/TypeScript tests, lint, and type checking;
- configure Docker Compose/Nginx development/deployment skeleton.

Acceptance: all components build/test independently and respect the specified repository boundaries.

### Milestone 1 — Deterministic engine foundation

Possible tasks:

- immutable/controlled game-state model;
- 20 Hz tick clock and game-time conversion;
- command envelope and ordering;
- seeded RNG ownership;
- engine event model;
- deterministic replay fixture format;
- scenario model.

Avoid gameplay areas requiring unresolved values.

### Milestone 2 — Static world and map

Possible tasks:

- YAML map schema/versioning;
- terrain representation;
- static structures;
- occupancy model;
- map validation;
- first original-map data ingestion/reconstruction tasks.

Escalate static footprint questions if required by actual map representation.

### Milestone 3 — Commander vertical slice

Possible tasks that do not depend on unresolved commander constants can establish:

- commander state machine;
- horizontal/vertical transition abstractions;
- height-aware collision API;
- docking architecture;
- command protocol;
- renderer interpolation fixtures.

Exact Z limits/speeds remain blocked until Open Question #13 is resolved.

### Milestone 4 — Multiplayer transport vertical slice

Possible tasks:

- create/join/ready flow;
- MatchManager;
- Match tick loop;
- command queueing;
- snapshots;
- WebSocket protocol;
- basic frontend connection/state display;
- two-browser smoke test using simplified implemented engine state.

This milestone proves architecture without requiring the full game.

Subsequent milestones should expand rules in a dependency-aware sequence while creating targeted research tasks for unresolved Spectrum behavior.

---

## 26. Success criteria

The agentic development flow is successful when:

1. a human can request change-request-level work without re-explaining repository architecture;
2. agents reliably distinguish locked requirements from unresolved questions;
3. implementation tasks are independently reviewable and resumable;
4. multiple safe tasks can run concurrently without sharing mutable workspaces;
5. gameplay rules remain centralized in the deterministic engine;
6. every completed task provides objective verification evidence;
7. integration failures are detected before a change request is completed;
8. open questions remain visible and explicitly managed rather than becoming accidental implementation decisions;
9. a new agent can reconstruct why a piece of code exists from repository artifacts rather than chat history;
10. the project can progress incrementally from specifications to a complete PvP v1 without architectural drift.

---

## 27. PRD acceptance criteria

This PRD is considered implemented when the repository has an agent workflow that can demonstrate the following end-to-end behavior:

1. inspect the specs and repository;
2. propose an implementation-ready change request;
3. generate a dependency-aware task plan;
4. mark tasks touching unresolved rules as blocked/research rather than inventing behavior;
5. run at least two independent tasks in isolated branches/worktrees where safe;
6. require tests and completion reports;
7. run an independent review step;
8. integrate verified work;
9. run change-request-level verification;
10. report remaining blocked decisions and implemented spec coverage.

The specific orchestration framework or coding-agent vendor is intentionally not locked by this PRD. The workflow contract should remain usable with different agent runtimes.