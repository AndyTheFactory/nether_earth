# Milestone 7 — Match Runtime, Protocol & Multiplayer

## Goal

Connect the deterministic engine to the runtime/backend layer through a versioned JSON protocol, implement two-player match lifecycle and WebSocket transport, and preserve the rule that networking orchestrates the engine rather than duplicating gameplay logic.

## Spec references

- `_specs/functional-spec.md` §§4–5 and §18
- `_specs/technical-spec.md` §§1–3, 19–22
- `_specs/open-questions.md` §16

## Start dependencies

- Milestone 0 backend/protocol skeleton.
- Milestone 1 deterministic engine command/state/snapshot contract.

Protocol/lifecycle/runtime infrastructure may begin before all gameplay milestones are complete using stable engine fixtures and versioned contracts.

## Completion dependencies

- Milestones 2–6 complete for the full v1 engine command/state/event surface consumed by multiplayer.

## Deliverable

A single-process FastAPI backend hosting multiple in-memory matches, with create/join/ready/session lifecycle, fixed-tick match orchestration, validated JSON WebSocket commands, authoritative snapshots/events, locked pause/reconnect/forfeit behavior, and replay/debug logging to filesystem.

## Locked disconnect/reconnect policy

- Match pauses immediately when either player disconnects.
- While paused, engine ticks and all gameplay timers stop.
- Default reconnect grace period is **60 seconds**.
- Grace duration is configurable in runtime/server match configuration, not hard-coded in networking branches.
- A reconnecting player receives the current authoritative snapshot.
- Simulation resumes only when both players are connected.
- If a disconnected player's grace deadline expires while an opponent remains eligible, the disconnected player forfeits and the opponent wins.
- If both players disconnect, each has an independent grace deadline.
- If one deadline expires first, that player forfeits under the normal rule.
- If both deadlines expire without either returning, finalize the match as abandoned/no-contest rather than inventing a gameplay winner.
- No manual pause in v1.
- Reconnect/deadline state belongs to the runtime layer; deterministic engine state must remain unchanged while paused.

## Workstreams and candidate tasks

### Shared protocol schemas
Define versioned JSON Schema for identifiers, client commands, server lifecycle/events, snapshots, pause/resume messages, and errors. Generated TypeScript types and Python/Pydantic validation derive from the shared contract.

### Match manager and lifecycle
Implement create, join by code/link, nickname/session token, readiness, start, active, paused-disconnected, finished, and disposal states. Active gameplay state remains in memory.

### Fixed-tick orchestration
Run engine stepping at 20 Hz while active. When runtime state is paused-disconnected, stop stepping the engine entirely.

### WebSocket transport
Associate guest sessions, validate inbound messages, route commands to the correct match, and broadcast authoritative output.

### Snapshot and resynchronization
Provide start/reconnect snapshots and event/delta streams without making the frontend authoritative.

### Disconnect/reconnect state machine
Track per-player connection state and wall-clock reconnect deadline outside the engine. Pause on first disconnect, resume only when both are connected, and resolve timeout outcomes according to the locked policy.

### Replay/debug logging
Persist scenario/map/rules version, seed, accepted command stream, lifecycle metadata including pauses/reconnect outcomes, and final result to mounted filesystem.

## Parallelization

Protocol schema, MatchManager lifecycle, tick orchestration, replay infrastructure, and reconnect state-machine work may proceed in parallel against Milestone 1 contracts. Gameplay-specific protocol messages can be added as engine contracts stabilize.

## Acceptance criteria

- Two guest players can create/join one match and become ready.
- Match starts with scenario-defined state and fixed engine seed.
- Backend hosts multiple matches in one process without state leakage.
- Client messages are schema/Pydantic validated before routing.
- Backend never reimplements movement/combat/economy/capture/victory rules.
- Active match scheduling invokes the deterministic engine at 20 Hz.
- Disconnect pauses engine stepping immediately.
- Gameplay tick/time does not advance while disconnected-paused.
- Default reconnect grace is 60 seconds and configurable at runtime.
- Reconnect sends authoritative snapshot and resumes only when both players are connected.
- Single-player grace expiry produces forfeit/win correctly.
- Both-player disconnect with both deadlines expiring ends abandoned/no-contest.
- No manual pause exists in v1.
- Protocol covers pause/resume/reconnect/result lifecycle.
- Replay/debug data can reproduce gameplay state/result; wall-clock pause metadata remains runtime metadata, not gameplay ticks.

## Milestone integration scenario

Start backend, create/join/ready two clients, exchange representative commands, disconnect client A and verify simulation freezes, reconnect A within 60 seconds and verify snapshot + resume, disconnect again and verify timeout forfeit. Separately test both clients disconnecting and both deadlines expiring to abandoned/no-contest. Replay the accepted gameplay command stream to the same engine state/result.

## Out of scope

- Final renderer/UI polish.
- Accounts/persistent profiles.
- Database/Redis/broker.
- Horizontal scaling/shared matches across processes.
- Production deployment hardening.
- Manual pause.

## Open questions / blockers

The former disconnect/reconnect product question (§16) is resolved. No v1 reconnect-policy blocker remains.

Human review remains required for changes to single-process/in-memory architecture or for any proposal to change the locked disconnect policy.

## Definition of done

- All milestone issues are closed by merged PRs.
- End-to-end two-client integration scenario passes.
- Disconnect/reconnect/forfeit/no-contest scenarios pass.
- Protocol generation/validation is reproducible.
- Replay reproduces completed gameplay state/result.
- Transport/runtime and engine responsibilities remain cleanly separated.