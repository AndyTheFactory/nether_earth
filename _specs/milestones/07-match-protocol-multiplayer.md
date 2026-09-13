# Milestone 7 — Match Runtime, Protocol & Multiplayer

## Goal

Connect the deterministic engine to the runtime/backend layer through a versioned JSON protocol, implement two-player match lifecycle and WebSocket transport, and preserve the rule that networking orchestrates the engine rather than duplicating gameplay logic.

## Spec references

- `_specs/functional-spec.md` §4–§5 — PvP setup and match flow
- `_specs/technical-spec.md` §1–§4 — architecture and responsibility boundaries
- `_specs/technical-spec.md` protocol, match/runtime, snapshot and replay sections
- `_specs/open-questions.md` §16 — disconnect/reconnect rules

## Start dependencies

- Milestone 0 backend/protocol skeleton.
- Milestone 1 deterministic engine command/state/snapshot contract.

Protocol/lifecycle/runtime infrastructure may begin before all gameplay milestones are complete by using stable engine fixtures and versioned contracts.

## Completion dependencies

- Milestones 2–6 complete for the full v1 engine command/state/event surface consumed by multiplayer.
- Disconnect/reconnect semantics required for v1 resolved and reflected in the authoritative specs.

## Deliverable

A single-process FastAPI backend hosting multiple in-memory matches, with create/join/ready/session lifecycle, fixed-tick match orchestration, validated JSON WebSocket commands, authoritative snapshots/events, reconnect support according to the resolved policy, and replay/debug logging to filesystem.

## Workstreams and candidate tasks

### Shared protocol schemas
Define versioned JSON Schema for common identifiers, client commands, server events, snapshots, lifecycle messages, and errors. Generated TypeScript types and Python/Pydantic validation must derive from the shared contract.

The protocol may evolve incrementally as Milestones 2–6 add stable engine commands/events, but each schema revision must remain synchronized across generated consumers.

### Match manager and lifecycle
Implement create, join by code/link, nickname/session token, readiness, start, active, finished, and disposal states. Active game state remains in memory.

### Fixed-tick orchestration
Run engine stepping at 20 Hz in the match layer, queue accepted commands deterministically, and keep wall-clock scheduling outside gameplay rules.

### WebSocket transport
Authenticate/associate guest sessions, validate inbound messages, route commands to the correct match, and broadcast authoritative output.

### Snapshot and resynchronization
Provide initial/reconnect snapshots and any event/delta stream required by the client without making the frontend authoritative.

### Replay/debug logging
Persist scenario/map version, seed, accepted command stream, relevant lifecycle metadata, and final result to mounted filesystem files so engine replay remains possible.

### Disconnect/reconnect policy
Implement only after the policy is verified/approved: continuation or pause, grace period, abandonment/surrender, and both-player disconnect behavior.

## Parallelization

Protocol schema, MatchManager lifecycle, tick orchestration, and replay infrastructure may proceed against Milestone 1 contracts while later gameplay milestones are under development. Gameplay-specific protocol messages can be added as their engine contracts stabilize. WebSocket transport can proceed against fixture commands/snapshots. Reconnect behavior remains a separate policy-dependent task.

## Acceptance criteria

- Two guest players can create/join the same match and become ready.
- Match starts with the scenario-defined initial state and fixed engine seed.
- Backend hosts multiple matches in one process without cross-match state leakage.
- Client messages are schema/Pydantic validated before routing.
- Backend does not reimplement movement, combat, economy, capture, or victory legality.
- Tick scheduling invokes the deterministic engine at 20 Hz while gameplay timing remains tick-derived.
- Clients receive authoritative snapshots/events sufficient to render and recover state.
- Protocol schemas cover the complete v1 command/state/event surface by milestone completion.
- Replay/debug files can reproduce the engine command stream and result.
- Match ends and in-memory state is disposed according to the spec.

## Milestone integration scenario

After Milestones 2–6 are available, start the backend, create a match with client A, join with client B, ready both, exchange representative commands spanning the v1 engine surface over WebSockets, verify both receive consistent authoritative state, disconnect/reconnect one client under the resolved policy, finish the match, and replay the stored command stream to the same final engine state/result.

Earlier task-level integration may use deterministic fixture snapshots/commands before the full gameplay surface exists.

## Out of scope

- Final visual renderer and UI polish.
- Accounts or persistent profiles.
- Database/Redis/message broker.
- Horizontal scaling or multiple backend processes sharing matches.
- Production deployment hardening.

## Open questions / blockers

`_specs/open-questions.md` §16 must be resolved before reconnect/abandonment behavior is finalized. The rest of the runtime can be implemented independently around a policy interface.

Human review is required for any proposal that changes the single-process/in-memory architecture, introduces persistent infrastructure, or chooses disconnect semantics without an approved spec update.

## Definition of done

- All milestone issues are closed by merged PRs.
- End-to-end two-client integration scenario passes against the complete v1 engine surface.
- Protocol generation/validation is reproducible.
- Replay reproduces the completed match state/result.
- Transport and engine responsibilities remain cleanly separated.
