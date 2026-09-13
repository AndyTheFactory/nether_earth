# Milestone 0 — Repository & Agentic Development Foundation

## Goal

Establish the repository structure, quality gates, and execution conventions required for safe autonomous implementation of later milestones.

This milestone is intentionally light on gameplay implementation. Its purpose is to ensure that subsequent work can be decomposed into GitHub issues, implemented on isolated branches/worktrees, reviewed through pull requests, verified automatically, and integrated without ambiguity.

The default operating mode after this milestone is **autonomous execution within the locked specifications**. Human review is required when an agent encounters a blocker, a conflict between authoritative sources, or a decision that would change or infer unresolved product/gameplay behavior.

## Spec references

- `_specs/agentic-programming-prd.md` — agentic workflow, task states, branch/worktree model, quality gates, and integration model
- `AGENTS.md` — repository-wide agent rules
- `CLAUDE.md` — Claude Code execution guidance
- `_specs/technical-spec.md` §2 — locked technology stack
- `_specs/technical-spec.md` §3 — repository layout
- `_specs/technical-spec.md` §4 — separation of responsibilities

## Deliverable

At the end of this milestone, the repository must provide a clean and executable development foundation for all major components:

- pure-Python engine package;
- FastAPI backend package;
- Vite/TypeScript/PixiJS frontend package;
- shared protocol schema area;
- map/data area;
- deployment configuration area;
- test and quality tooling;
- CI capable of validating the current repository state;
- documented issue → task branch/worktree → PR → review → integration workflow.

A developer or coding agent must be able to clone the repository, install the relevant dependencies, run the configured checks, and verify that the skeleton components build or execute successfully.

## Dependencies

None.

This is the first implementation milestone.

## Execution model

When implementation of this milestone starts:

- each task is represented by a GitHub issue;
- each implementation task uses an isolated branch/worktree;
- each task is completed through a pull request;
- task PRs are independently reviewed before integration;
- agents proceed autonomously while requirements are locked and non-conflicting;
- the project owner is asked to review only when a task reaches a genuine blocker, authoritative-source conflict, or unresolved decision boundary.

Routine implementation choices that do not alter locked behavior or architecture do not require pre-approval.

## Workstreams and candidate tasks

The items below describe the expected task decomposition. They are not GitHub issues yet; issues are created when milestone execution begins.

### 1. Repository skeleton

Create the locked top-level structure from the technical specification:

```text
frontend/
backend/
engine/
protocol/
data/maps/
replays/
deploy/
_specs/
```

Include only the minimum package/module files required to make each component independently installable or runnable.

### 2. Engine package bootstrap

Create the pure-Python engine package and test layout.

Requirements:

- package can be imported independently;
- no FastAPI, WebSocket, frontend, deployment, or network dependency;
- test runner is configured;
- one trivial deterministic test proves the package/test harness works;
- no gameplay rules beyond what is necessary to establish the package boundary.

### 3. Backend bootstrap

Create the FastAPI backend package.

Requirements:

- app starts successfully;
- health endpoint exists;
- dependency on the engine is one-way: backend may consume engine, engine may not consume backend;
- no game-rule logic is introduced in the transport layer;
- backend test harness is configured.

### 4. Frontend bootstrap

Create the frontend using the locked stack:

- TypeScript;
- Vite;
- PixiJS;
- plain HTML/CSS;
- no React or other UI framework.

Requirements:

- development build starts;
- production build succeeds;
- a minimal canvas/application shell can initialize PixiJS;
- no gameplay legality is implemented client-side.

### 5. Protocol bootstrap

Create the shared protocol structure under `protocol/`.

Requirements:

- JSON Schema directory structure exists;
- schema validation tooling is defined;
- TypeScript generation path is defined and executable;
- Python/Pydantic consumption strategy is documented or minimally scaffolded;
- generated artifacts have one clear source of truth and are not hand-edited.

The actual gameplay message catalog is deferred to later milestones.

### 6. Map/data bootstrap

Create the versioned map-data location and an initial schema/placeholder sufficient to validate data loading without inventing unresolved map semantics.

Requirements:

- YAML is the storage format;
- loader/validation entry point can be added without binding the map format to frontend concerns;
- no guessed values for unresolved gameplay rules are introduced.

### 7. Quality tooling

Configure the minimal quality gates appropriate to each stack.

Expected Python checks:

- tests;
- formatter/linter;
- static type checking.

Expected TypeScript checks:

- type checking;
- linting/formatting if adopted;
- production build.

Exact tool choices may be made autonomously as implementation details unless they conflict with an existing repository convention or materially change the architecture.

### 8. CI foundation

Add CI that executes the applicable repository checks on pull requests.

At minimum, CI must be capable of detecting:

- Python test failures;
- Python type/lint failures once configured;
- protocol generation/schema failures once configured;
- frontend type/build failures.

CI should favor independent jobs where this shortens feedback and preserves clear failure ownership.

### 9. Local development and deployment skeleton

Create only the deployment/dev structure necessary to prove the locked topology can be represented:

- Dockerfiles or equivalent component build definitions where appropriate;
- Docker Compose skeleton;
- Nginx configuration skeleton;
- mounted replay/debug path.

This task must not introduce PostgreSQL, Redis, Kubernetes, a message broker, or additional production infrastructure.

Full production hardening belongs to a later milestone.

### 10. Agentic execution conventions

Ensure repository documentation is sufficient for an agent starting a task to determine:

- which specs to read;
- how to identify open-question boundaries;
- branch/worktree naming;
- issue and PR expectations;
- required verification evidence;
- when human escalation is mandatory.

`AGENTS.md` and `CLAUDE.md` already provide the foundation; this task should fill only concrete gaps discovered while setting up the repository.

## Parallelization

After the repository skeleton is agreed, the following work can proceed largely in parallel:

```text
                 ┌─ engine bootstrap + Python quality
repository ──────┼─ backend bootstrap
skeleton         ├─ frontend bootstrap + TS quality
                 ├─ protocol bootstrap
                 ├─ map/data bootstrap
                 └─ deployment skeleton
                         │
                         ▼
                    CI integration
```

CI integration should follow sufficiently stable commands from the individual component tasks.

The integration task is responsible for proving that all component checks can run together from a clean checkout.

## Acceptance criteria

Milestone 0 is complete when all of the following are true:

- repository layout matches the locked architectural boundaries;
- engine imports and tests independently;
- backend starts and its health check passes;
- frontend development setup and production build work;
- PixiJS initializes in the frontend shell;
- protocol schema tooling has a working minimal generation/validation path;
- map YAML data has a validation/loading skeleton;
- Python and TypeScript quality commands are documented and executable;
- CI runs the applicable checks on pull requests;
- Docker Compose/Nginx skeleton reflects the intended single-VPS topology without adding out-of-scope infrastructure;
- a clean checkout can execute the documented verification commands;
- no gameplay behavior has been invented to satisfy the foundation work;
- autonomous task execution and human escalation boundaries are clear in repository-local instructions.

## Milestone integration scenario

From a clean checkout, the milestone integration verification must demonstrate a thin end-to-end skeleton:

1. Python engine package imports and its deterministic smoke test passes.
2. FastAPI backend starts and returns a successful health response.
3. Frontend builds successfully and initializes the PixiJS application shell.
4. Protocol sample/minimal schema validates and generated TypeScript output can be produced reproducibly.
5. Example/placeholder YAML map data is accepted by the map validation/loading skeleton.
6. CI executes these checks using the same or equivalent commands.
7. Docker Compose configuration can be parsed/built sufficiently to validate the component topology.

This is a foundation demonstration, not a playable game slice.

## Out of scope

This milestone does not implement:

- game clock semantics beyond any trivial scaffold needed for the engine test harness;
- authoritative world state;
- map fidelity/extraction;
- commander movement;
- robots;
- resource economy;
- factories or war bases;
- combat;
- multiplayer match behavior;
- final WebSocket protocol;
- final rendering or UI;
- replay semantics beyond filesystem/deployment scaffolding;
- AI opponents;
- production deployment hardening.

## Open questions / blockers

No known gameplay question in `_specs/open-questions.md` blocks this milestone.

During implementation, human review is mandatory if:

- the locked technology stack cannot satisfy a required foundation capability without a material architecture change;
- repository-local specifications conflict about ownership or dependency direction;
- implementing the foundation would require choosing behavior listed as unresolved in `_specs/open-questions.md`;
- parallel task changes introduce a semantic conflict that cannot be resolved mechanically.

Normal library/tooling selections and internal implementation details may be resolved autonomously when they preserve the locked architecture and acceptance criteria.

## Definition of done

The milestone is done when:

- all milestone GitHub issues are closed by merged PRs;
- each task PR passed its required checks and review;
- the integration verification passes from the milestone integration state;
- no blocking review findings remain;
- no hidden product/gameplay decision was introduced;
- any implementation-driven documentation changes have been merged;
- the repository is ready for Milestone 1 without requiring foundational restructuring.
