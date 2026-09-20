# Milestone 7 Plan — Match Runtime, Protocol & Multiplayer

Spec: `_specs/milestones/07-match-protocol-multiplayer.md` (also `_specs/functional-spec.md` §§4-5, §18; `_specs/technical-spec.md` §§1-3, 19-22; `_specs/open-questions.md` §16 — resolved, no blocker).

GitHub tracking: issue #100 (plan), tasks #90-#99. Each task below IS the corresponding GitHub issue; close the issue with the task's PR. Reference the issue number in the task's PR description and use `Closes #<n>`.

## Repository state at plan start

- `protocol/schemas/{common,client_messages,server_messages,snapshot}.schema.json` are M0-only (`ping`/`pong`, bare tick). `protocol/generated/` is empty. Frontend has `npm run protocol:validate` / `protocol:generate`.
- `backend/app/main.py` exposes only `GET /health`. `backend/pyproject.toml` has `fastapi`, `uvicorn`, and a `dev` extra (`httpx`, `mypy`, `pytest`, `ruff`); no `pydantic`, no dependency on the engine package yet.
- `engine/` is a separate installable package (`nether-earth-engine`, src-layout, `mypy --strict`, `ruff`, `pytest`). Nothing in `backend/` depends on it yet — wiring that dependency (uv workspace member or local path dependency, whichever matches this repo's existing tooling) is in scope for the first task that needs to import the engine from `backend/`.
- Engine public surface relevant to this milestone: `nether_earth.engine.new_game(...)` / `nether_earth.engine.step(state, commands, ...) -> (GameState, tuple[Event, ...])`; `nether_earth.commands.Command` (base dataclass: `player: PlayerId`, `sequence: int`) plus per-subsystem `Command` subclasses defined across `commander_movement.py`, `orders.py`, `construction_commands.py`, `combat.py`, etc.; `nether_earth.snapshot.to_snapshot(state) -> dict[str, Any]` / `snapshot_to_json_string`; `nether_earth.replay.ReplayFixture` / `run_fixture`. M4/M5/M6 are complete and closed; treat their command/state/event surface as stable and final for v1.
- All M5 and M6 per-task issues are closed; only their tracking "Plan" issues (#59 is stale/mislabeled M4, #69, #82) remain open as trackers — this satisfies M7.9/M7.10's "M5 and M6 complete" dependency.
- CI is intentionally disabled; verify everything locally (`engine/`: `pytest`, `ruff check`, `mypy`; `backend/`: same; `frontend/`: `npm run protocol:validate`, `npm run protocol:generate`, `tsc --noEmit` if configured).

## Dependency waves (per issue #100's dependency graph)

```
Wave 1: Task 1 (#90 protocol schemas)            + Task 2 (#92 MatchManager/lifecycle)   [independent]
Wave 2: Task 3 (#91 Pydantic transport models)   [needs #90]
        Task 4 (#93 fixed-tick runtime)          [needs #92]
Wave 3: Task 5 (#94 WebSocket transport)         [needs #90, #91, #92, #93]
Wave 4: Task 6 (#95 snapshot/resync)             [needs #94]
        Task 7 (#96 reconnect/forfeit/no-contest)[needs #94, #95, #93's pause hook]
        Task 8 (#97 replay/debug logging)        [needs #93, #92, #96's lifecycle metadata]
Wave 5: Task 9 (#98 full v1 protocol mapping)    [needs all above; M5/M6 already stable]
Wave 6: Task 10 (#99 end-to-end integration gate)[needs everything above, final gate]
```

Execute strictly in this numeric order (1-10). Even where the issue graph allows parallelism within a wave, this plan runs one implementer dispatch at a time (shared files: `protocol/schemas/*`, `backend/app/main.py`, match runtime module) — do not dispatch two tasks' implementers concurrently.

## Global Constraints

- **Architecture (AGENTS.md, non-negotiable):** the engine package stays pure Python, no FastAPI/WebSocket/network I/O, deterministic, stepped only via `engine.step`. The backend/match layer orchestrates sessions, commands, broadcasting, lifecycle, and replay logging — it must never reimplement movement/combat/economy/capture/victory legality. It only: (1) validates transport shape (schema/Pydantic), (2) validates session/match ownership, (3) converts validated transport commands to engine `Command` objects and calls `engine.step`, (4) converts engine snapshots/events back to transport payloads. Any judgment call that would require inventing a gameplay rule is out of scope — escalate via `_specs/open-questions.md`, do not guess.
- **Protocol source of truth:** JSON Schema Draft 2020-12 under `protocol/schemas/` remains authoritative. Python/Pydantic models and generated TypeScript are derived from or mechanically validated against these schemas — never a second hand-maintained message catalog. Every top-level message carries an explicit `protocolVersion`. Reject unknown message `type` and unexpected additional properties wherever the schema is meant to be strict (`additionalProperties: false` on envelopes).
- **Determinism boundary:** wall-clock time, reconnect deadlines, and pause/disconnect bookkeeping live in the runtime/backend layer only, keyed off `asyncio`/monotonic clocks — never fed into the engine as gameplay state, and never advancing `GameState.tick` while paused. The engine's only time axis is ticks advanced by `engine.step`.
- **Command ordering:** inbound gameplay commands for a given tick are queued and handed to `engine.step` using the engine's own `(player.value, sequence)` deterministic ordering (`commands.order_commands`/`validate_command_batch`) — the backend must not invent a second ordering rule or reorder by arrival/wall-clock time. Duplicate/replayed `client_sequence` values per player must be rejected/no-op, never applied twice.
- **Single mutation point per match:** exactly one coroutine/task may call `engine.step` for a given match at a time (an `asyncio.Lock` or single owning task per match is sufficient) so concurrent WebSocket handlers cannot race `engine.step`.
- **Locked reconnect/disconnect policy** (from the milestone spec, do not alter without human review): first disconnect pauses the match immediately and stops engine ticking entirely; default grace period is 60 seconds and is runtime-configurable (not hard-coded in a branch); a reconnecting player receives the current authoritative snapshot; simulation resumes only once both players are connected; a lone expired grace deadline forfeits that player (opponent wins); if both players are disconnected each has an independent deadline, and if both expire without either returning the match finalizes as abandoned/no-contest (never invent a winner); there is no manual pause in v1.
- **No new infra:** no PostgreSQL/Redis/message broker/Kubernetes. Single backend process, many in-memory matches. Replay/debug persistence is filesystem-only, one artifact per match, never a database.
- **Testing:** async backend tests use a controllable/injectable clock or `asyncio` test utilities — no real 60-second sleeps and no flaky wall-clock-dependent assertions. Every task adds regression tests colocated with the existing `backend/tests/` (create subpackages as needed) and, where schemas change, `protocol/` validation fixtures. Run `pytest`, `ruff check`, and `mypy --strict` for every changed Python package before calling a task done; run `npm run protocol:validate` / `protocol:generate` (and check in the regenerated `protocol/generated/types.ts`) for every schema change.
- **Scope discipline:** each task closes exactly its own issue's scope; do not pull forward a later task's work (e.g., do not implement reconnect policy while doing Task 4's tick runtime — leave a clean extension point instead, matching what the issue's "Out of scope" section says).

---

### Task 1: Expand versioned JSON protocol schemas and TypeScript generation (issue #90)

Depends on: M0/M1 (stable, already true). May start immediately.

Scope:
- Replace the M0-only `client_messages`/`server_messages`/`snapshot` schemas with a modular, versioned schema set under `protocol/schemas/`: shared identifiers/envelope (`common.schema.json`), client lifecycle commands (create/join/ready/leave), client gameplay command envelope (match id, session token, `client_sequence`, a discriminated `payload` placeholder that M7.9 will extend), server lifecycle/events (created/joined/ready-state/started/paused/resumed/forfeit/no-contest/finished/error), authoritative snapshot envelope, and reconnect/resync messages.
- Keep composition modular (e.g. `$ref` into shared envelope/id definitions) so M7.9 can add gameplay payload variants without rewriting lifecycle envelopes.
- `protocolVersion` is an explicit required `const` on every top-level message type, exactly as in the current M0 schemas.
- No gameplay legality (ranges, damage, movement rules, etc.) is ever encoded in a schema — only shape/type/enum constraints for transport.
- Regenerate `protocol/generated/types.ts` via the existing `npm run protocol:generate` script (extend the generator config if needed for new schema files) and run `npm run protocol:validate`.

Acceptance criteria (from issue #90): schemas validate under existing frontend protocol tooling; generated TypeScript is reproducible and committed per repo convention; unknown message types/unexpected fields are rejected on strict envelopes; lifecycle/snapshot/event/reconnect/error envelopes are all represented; protocol version is explicit on every top-level message; no gameplay legality is encoded in schemas.

Out of scope: FastAPI routing, engine command conversion, full M5/M6 gameplay payload enumeration (M7.9).

---

### Task 2: Implement guest sessions, create/join/ready lifecycle, and MatchManager (issue #92)

Depends on: M0 backend skeleton, M1 engine init contract. Independent of Task 1; may start immediately, but this plan runs it second.

Scope:
- Add a `MatchManager` (new `backend/app/` module, e.g. `matches.py` or a `match/` subpackage) owning multiple in-memory matches keyed by match id.
- Create match: generates a join code/link identity and a first guest player slot from a supplied nickname.
- Join: second guest joins by code, exactly two player slots in v1; reject over-capacity joins cleanly.
- Issue an opaque, unguessable per-player reconnect/session token on create/join.
- Track per-player readiness; transition to a started state exactly once both players are ready, initializing one authoritative engine state via `engine.new_game(...)` (wire the backend→engine dependency now: uv workspace member or local editable path dependency, whichever fits this repo's existing tooling — add it to `backend/pyproject.toml`).
- Runtime states at minimum: `WAITING`, `ACTIVE`, `PAUSED_DISCONNECTED`, `FINISHED` (leave room for Task 4/7 to add transitions; this task only needs `WAITING`→`ACTIVE` and a `FINISHED`/disposal hook).
- Explicit disposal hook for finished matches (no persistence beyond replay files, added in Task 8).
- This task is pure lifecycle logic; it does not need Task 3's Pydantic models (plain Python method calls / dataclasses are fine) and does not do WebSocket I/O (Task 5) or fixed-tick stepping (Task 4).

Acceptance criteria (from issue #92): multiple matches coexist with no shared state; exactly two player slots; ready/start transition occurs once and initializes one authoritative engine state; session tokens reconnect to the correct player/match and cannot claim another slot; MatchManager lookup/removal is unit tested; no gameplay rules implemented in this layer.

Out of scope: fixed-tick stepping (Task 4), WebSocket I/O (Task 5), disconnect grace semantics (Task 7).

---

### Task 3: Add backend Pydantic transport models and schema conformance tests (issue #91)

Depends on: Task 1 (#90) merged — needs the real lifecycle/envelope schemas, not the M0 stubs.

Scope:
- Add `backend/app/protocol/` (or similar) Pydantic v2 models mirroring every schema from Task 1: client lifecycle/command envelopes, server lifecycle/event/snapshot/error messages.
- Mechanically derive-or-validate against the canonical JSON Schema — e.g. a test that round-trips representative fixtures through both the JSON Schema validator (via a small Python JSON Schema library already available, or by shelling to the frontend's `ajv`-based validator if that's simpler — pick whichever avoids a second hand-authored enum/type catalog) and the Pydantic models, asserting they agree on accept/reject for the same fixtures.
- One clear entry point to parse+validate an inbound raw JSON client message into a typed Pydantic model (reject invalid before it reaches Match/engine code) and one clear entry point to serialize an outbound server message to schema-valid JSON.
- Add `pydantic` to `backend/pyproject.toml` dependencies.

Acceptance criteria (from issue #91): invalid inbound payloads fail before reaching Match/engine code; valid fixtures validate under both JSON Schema tooling and Pydantic; representative server messages round-trip to schema-valid JSON; no duplicate hand-authored enum/message catalog can silently diverge from the protocol schemas; backend `mypy`/`pytest`/`ruff` pass locally.

Out of scope: WebSocket connection handling, gameplay rule validation.

---

### Task 4: Implement fixed-tick Match runtime and deterministic command queue (issue #93)

Depends on: Task 2 (#92) MatchManager/lifecycle merged; M1 engine step contract (stable).

Scope:
- Implement an asyncio-based Match runtime loop that steps the engine at a configured 20 Hz while a match's state is `ACTIVE` (make the tick rate a named runtime constant, not a magic literal, matching the technical spec's fixed tick rate).
- Queue validated player commands for the next eligible tick; order same-tick commands using engine-native `(player.value, sequence)` ordering (reuse `commands.order_commands`/the engine's own batch validation — do not invent a second ordering rule).
- Reject/no-op duplicate or replayed `client_sequence` values per player (track last-applied sequence per player).
- Serialize `engine.step` calls per match (single lock/owning task) so concurrent WebSocket tasks cannot race it.
- Stop stepping entirely while runtime state is `PAUSED_DISCONNECTED` (Task 7 drives that transition; this task only needs to honor it — e.g. the loop checks state before each tick and is a no-op cost while paused, not busy-looping).
- Cleanly cancel/finalize the tick task on finished/disposed match — no orphan `asyncio.Task`.
- Scheduler may correct for wall-clock drift (e.g. `asyncio.sleep` with drift compensation) but must never pass a variable delta-time value into `engine.step` — ticks are always whole, fixed-size steps.
- Gameplay-specific command *conversion* (transport payload → concrete engine `Command` subclass) may use a minimal fixture/stub set for now; full coverage is Task 9's job. Do not block this task on that.

Acceptance criteria (from issue #93): active matches call `engine.step` exactly once per authoritative tick; the same accepted command sequence produces the same engine ordering regardless of coroutine scheduling; duplicate client sequences are rejected/no-op; no engine ticks advance while runtime-paused; multiple matches run independently in one process; cancellation/disposal leaves no orphan tick task. Use a controllable/fake clock in tests; avoid slow real-time sleeps in the core suite.

---

### Task 5: Implement WebSocket transport, session association, and command routing (issue #94)

Depends on: Tasks 1-4 (#90-#93) merged.

Scope:
- Add a FastAPI WebSocket endpoint (e.g. `/ws/{match_id}`) in `backend/app/main.py` (or a router module it includes).
- Associate each connection with exactly one authenticated guest session token (from Task 2); reject/close on missing or invalid token, or a token for the wrong match.
- Parse and Pydantic-validate every inbound message (Task 3's entry point) before routing; return a schema-valid error message for malformed/unauthorized/invalid requests instead of silently dropping them.
- Route lifecycle/runtime commands (ready, leave, etc.) to `MatchManager`/Match, and gameplay domain commands to Task 4's engine command queue — the handler itself makes no gameplay legality decisions.
- Broadcast authoritative server messages only to the connections belonging to that match (no cross-match leakage).
- On socket close/error, notify the runtime disconnect state machine's connection-state hook exactly once (Task 7 owns grace/outcome policy; this task only needs a clean, single notification point — e.g. a `Match.on_disconnect(player)` call in a `finally` block).
- Keep the WebSocket handler thin: parse → validate → route → respond/broadcast, with no embedded business logic beyond that.

Acceptance criteria (from issue #94): invalid JSON/schema/session messages never reach `engine.step`; a player cannot submit commands for another match/player; messages from one match are never broadcast to another; disconnect reaches the runtime lifecycle exactly once; the handler is thin and testable; no engine rule logic is duplicated in transport code. Use FastAPI's `TestClient`/`websockets` test utilities for connect, valid/invalid message, wrong-session, cross-match isolation, and disconnect tests.

---

### Task 6: Implement authoritative snapshot/event streaming and reconnect resynchronization (issue #95)

Depends on: Task 5 (#94). Uses Task 1/3 schemas/models; snapshot completeness is finalized in Task 9.

Scope:
- Map `nether_earth.snapshot.to_snapshot(state)` output into the protocol's snapshot payload shape (Task 1's schema) without re-deriving or duplicating any gameplay value the engine already computed — a thin field-mapping layer only.
- Send the initial authoritative snapshot on match start and to a client that (re)attaches.
- After each authoritative `engine.step`, broadcast ordered events/state updates using one documented policy (pick one: snapshot-only, event+snapshot, or a deterministic delta — record the choice and rationale in this task's PR description; do not silently mix policies across message types).
- On reconnect (Task 7 drives when this fires), send the current authoritative snapshot before gameplay resumes — never advance or mutate engine state merely to produce a snapshot.
- Include runtime lifecycle state the UI needs (active/paused/finished) as a field separate from deterministic gameplay state — never inject it into the engine snapshot payload itself.
- Keep serialization order/identity stable wherever replay/debug tooling (Task 8) depends on it (reuse the engine's own canonical ordering from `to_snapshot`, do not re-sort).

Acceptance criteria (from issue #95): a newly connected/reconnected client can reconstruct all currently exposed authoritative gameplay state from the snapshot; reconnect never advances/mutates the engine merely to generate state; engine events retain authoritative ordering; runtime pause/reconnect metadata is never injected into deterministic engine tick state; snapshot payload validates against the canonical protocol schema.

Out of scope: frontend state store/interpolation (M8).

---

### Task 7: Implement disconnect/reconnect pause, grace deadlines, forfeit, and no-contest (issue #96)

Depends on: Task 2 (lifecycle), Task 4 (pause/resume hook), Task 5 (connection events), Task 6 (reconnect snapshot).

Scope: implement the Locked disconnect/reconnect policy from Global Constraints above, entirely in the runtime layer:
- Per-player connection/deadline runtime state, using monotonic wall-clock time (`asyncio`'s loop clock or `time.monotonic`) — never engine ticks — for deadlines.
- First disconnect pauses the match (flips Task 4's runtime state to `PAUSED_DISCONNECTED`) atomically with recording that player's deadline.
- Reconnect with the existing session token reattaches the connection safely (replacing any stale socket for that player) and, once both players are connected, resumes stepping.
- Grace duration is a runtime `MatchConfig`-style field defaulting to 60 seconds — never a bare literal in a branch.
- Lone deadline expiry ⇒ that player forfeits, opponent wins, engine is not asked to invent a result — this is a runtime-level result annotation alongside (not instead of) whatever engine victory state already exists.
- Both players disconnected ⇒ independent deadlines; first expiry still applies the lone-forfeit rule if the other player is by then reconnected/eligible; both expiring with neither returning ⇒ finalize as abandoned/no-contest.
- Define simultaneous/near-simultaneous disconnect handling deterministically at the runtime level (e.g. process disconnect notifications in arrival order, since this is wall-clock runtime bookkeeping, not gameplay state) without ever mutating engine state for it.
- Emit the paused/resumed/forfeit/no_contest lifecycle messages from Task 1's schema (Task 1 names the both-disconnected-timeout outcome `no_contest`, not "abandoned" — use that exact type).
- Cancel all pending deadline timers on match finish/disposal.

Acceptance criteria (from issue #96): tick count is unchanged for the full paused interval; reconnect within grace resumes only after both players are connected; grace expiry resolves the documented outcome exactly once; both-disconnected/no-return ends no-contest; no gameplay timer is simulated during pause; runtime timers are cancelled on finish/disposal; tests use controllable/injectable time, no 60-second sleeps in tests.

Human review: escalate rather than guess if implementation pressure suggests changing the locked forfeit/no-contest semantics.

---

### Task 8: Implement filesystem replay/debug logging with runtime lifecycle metadata (issue #97)

Depends on: Task 4 (accepted command stream/tick ownership), Task 2 (lifecycle/finalization), Task 7 (adds reconnect lifecycle metadata to record).

Scope:
- Add a filesystem replay/debug writer owned by the backend runtime (new module, e.g. `backend/app/replay_log.py`), one artifact per match under a configured replay directory (no database).
- Record: map/scenario/rules version, RNG seed, the accepted authoritative command stream in tick order, important engine events and final result, and — separately from the gameplay tick stream — disconnect/pause/reconnect/timeout lifecycle metadata (wall-clock durations, not fake ticks).
- Never write session tokens or other secrets into the log.
- Finalize the artifact atomically enough that a completed match's finished file is unambiguous (e.g. write to a temp path and rename, or a clear `status: finished` vs `status: in_progress` marker) — a crashed/incomplete match's artifact must be distinguishable from a finalized one.
- Concurrent matches write to separate files/paths with no collision (key by match id).
- Add a replay-verification helper/test: replay the persisted accepted command stream through `engine.new_game`/`engine.step` directly (bypassing the backend) and assert it reproduces the final engine snapshot/result — mirror the pattern already used by `nether_earth.replay.ReplayFixture`/`run_fixture` in the engine's own test suite.
- Filesystem I/O stays a backend-only dependency; the engine package must not gain a new dependency on this module.

Acceptance criteria (from issue #97): replaying persisted gameplay input reproduces the final engine snapshot/result; pause wall-clock duration never creates fake gameplay ticks; logs contain no reconnect/session secrets; concurrent matches write separate artifacts without collision; failed/incomplete artifacts are distinguishable from finalized ones; filesystem I/O never becomes an engine dependency.

---

### Task 9: Finalize complete v1 engine command/event/snapshot protocol mapping (issue #98)

Depends on: Tasks 1-8 merged. M5 and M6 engine command/state/event surfaces are complete and closed — consume them as-is; do not invent provisional semantics.

Scope:
- Inventory every v1 player-facing engine action across M2-M6 (commander movement/docking, construction/economy, robot movement/orders/navigation/capture, combat/fire/nuclear, victory) by reading each subsystem's `*_commands.py`/`commands.py`-subclass definitions and the corresponding `engine.step` integration points.
- Inventory all snapshot state (`snapshot.to_snapshot`) and authoritative events (`events.py` + each subsystem's event types) that M8/the frontend will need.
- Extend Task 1's schemas with the final gameplay payload variants (discriminated union on the client-command and server-event/snapshot envelopes).
- Extend Task 3's Pydantic models and regenerate TypeScript to match.
- Implement explicit, one-to-one adapters: validated transport command → concrete engine `Command` subclass (no legality decisions in the adapter — an illegal command is simply handed to `engine.step`/`validate_command_batch` and comes back rejected exactly like any other), and engine snapshot/event/result → transport payload (thin field mapping only, reusing `snapshot.to_snapshot`'s existing structure rather than re-deriving values).
- Add compatibility fixtures per subsystem group: construction/economy, commander, movement/orders/capture, firing/combat/nuclear, victory.

Acceptance criteria (from issue #98): every player-accessible v1 engine command has one documented transport representation; every state field needed for reconnect is present in the authoritative snapshot schema; every UI-significant authoritative event/result has a transport representation; no transport adapter makes movement/capture/construction/combat/victory decisions; generated TypeScript and backend validation remain schema-conformant; representative full-surface protocol fixtures pass.

Blocking rule: do not invent provisional M5/M6 domain semantics — none should be needed since both milestones are closed, but if a genuine gap is found, stop and record it in `_specs/open-questions.md` rather than guessing.

---

### Task 10: Add end-to-end multiplayer runtime integration scenario (issue #99)

Depends on: Tasks 1-9 (#90-#98) complete. Final M7 gate.

Scope: using two real WebSocket test clients and the real backend runtime (no frontend), exercise the full scenario from issue #99 / the milestone spec's "Milestone integration scenario":
1. create match, obtain join code; 2. join second guest; 3. ready both, start one deterministic seeded game; 4. receive authoritative start snapshot; 5. submit representative commands spanning commander + M4 construction/economy + M5 movement/orders/capture + M6 combat/victory; 6. verify invalid/unauthorized message rejection; 7. disconnect one player, assert engine tick freezes; 8. reconnect within grace, receive current snapshot, resume; 9. verify timeout-forfeit in a separate case; 10. verify both-disconnected/no-return no-contest in a separate case; 11. run a second match concurrently, assert no cross-match state/message leakage; 12. finish/dispose the match; 13. replay the persisted authoritative command stream and assert identical engine final state/result.

Acceptance criteria (from issue #99): scenario is repeatable, no arbitrary sleeps for correctness (use controllable time/deadlines); full protocol path is schema/Pydantic validated; engine is stepped only by the Match runtime; disconnect pause semantics are exact; cross-match isolation holds; persisted replay reproduces gameplay result; runtime tasks/connections are cleaned up after tests; no frontend dependency required.

Definition of done: this is the final M7 gate — close only when Tasks 1-9 (#90-#98) and their PRs are merged.
