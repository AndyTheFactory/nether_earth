# Project Review — 2026-09-21

- **Commit reviewed:** `main` @ `cfa992b`
- **Scope:** implementation status against `_specs/` and milestones M0–M10; architecture, code quality, maintainability, security, readability, and scalability.
- **Method:** read the specs, open questions, and GitHub issues; ran all quality gates in a clean environment; read the engine, backend, frontend, and deploy code; ran targeted checks (isolated frontend build, tick/snapshot benchmark).

## Summary

The engine is in good shape. It is deterministic and pure, respects the spec's architecture rules, and has thorough tests. The main risks sit around the engine:

- CI is disabled.
- The frontend Docker image cannot build.
- The backend has no limits on abuse or on how many matches it keeps.
- One open gameplay decision (§19, autonomous nuclear use) causes a self-destruct in normal matches.

The code also carries more comment prose than code. Much of that prose records process history rather than behavior.

## 1. Implementation status

### Quality gates (all pass)

| Gate | Result |
|---|---|
| `ruff check engine backend` | pass |
| `mypy` strict (engine, backend) | pass (see §4.3 for an environment caveat) |
| `pytest` (engine + backend) | 1430 passed, 130 s; warnings are all from third-party libraries |
| Frontend `tsc --noEmit` | pass |
| Frontend `vitest` | 28 passed |
| Frontend `vite build` | pass (from inside the repo only; see §4.2) |
| `npm audit --omit=dev` | 0 vulnerabilities |

### Milestones

| Milestone | Status |
|---|---|
| M0–M8 | Done. All task issues closed. |
| M9 | 6 of 8 tasks done. #119 (human two-browser match) and #120 (final gate) are waiting on the owner. |
| M10 | 0 of 10 tasks started. |

### Tracker hygiene

- **#144** ("Wire normal in-engine victory to `finish_match`") is already implemented by `backend/app/transport/victory.py` and covered by `backend/tests/transport/test_victory.py`. Close it.
- Plan trackers **#59, #69, #82, #100** (M4–M7) are still open although all their child tasks are closed. No GitHub milestone has been closed either.
- **#146** is a real gap: `MatchManager.dispose_match` (`backend/app/match/manager.py:341`) is never called in production.

### Open questions waiting on the owner

| Section | Topic | Impact |
|---|---|---|
| §19 | Autonomous nuclear use | **Critical.** A nuclear robot on Stop & Defend detonates as soon as any enemy robot exists anywhere on the map. Every completed Advance or Retreat falls back to Stop & Defend, so this happens in normal play. Recommendation: option 2, detonate only when the target is within the blast radius. |
| §18 | War-base heli-pad location | The pad is at ground level. The original has it on the war-base roof, at altitude 15. The game is playable either way; fidelity differs. |
| §17 | Commander start positions | Player 2's start mirrors Player 1's. No evidence supports it. |
| §4, §8, §9 | Movement timing, projectiles, combat detail | Partly resolved. Values are configurable, so these do not block play. |

### Spec deviations

1. **Server message names.** Technical spec §21 lists `match_joined`, `match_started`, `state_delta`, `event`, `command_rejected`, `match_paused`, `match_resumed`, `match_ended`. The implemented protocol uses `created`, `joined`, `ready_state`, `started`, `paused`, `resumed`, `finished`, `forfeit`, `no_contest`, `error`, `snapshot`, `resync`. Update the spec or record the decision.
2. **Engine rejections are never reported to clients.** The engine produces `CommandRejected` events, but they are never sent. The transport's own `command_rejected` error covers only an inactive match or a duplicate sequence number. For example, a player who cannot afford a build gets no feedback.
3. **Rules version is a hard-coded backend string.** `RULES_VERSION = "m7"` (`backend/app/replay/writer.py:120`). Spec §5 and §22 require an engine-owned rules version or content hash. Rules have changed since M7, so older replays cannot be reliably re-verified.

## 2. Architecture

### What is good

- The engine imports no FastAPI, asyncio, networking, or wall-clock code, and uses no floats for gameplay state.
- It uses one seeded RNG (`engine/src/nether_earth/rng.py`).
- The tick loop is paced from deadlines, which is the drift compensation the spec asks for.
- The frontend renders and collects input; it never decides legality.
- JSON Schema is the protocol source of truth, with TypeScript types generated from it.
- Map data for the frontend is generated from the engine's YAML, not maintained by hand.

### Issues

1. **One slow client slows the whole match.** Snapshot broadcasts are awaited inside the tick loop, one connection at a time (`backend/app/transport/connections.py:109`). A slow reader delays every tick for both players. The code comments already acknowledge this and defer it.
   *Fix:* give each connection a bounded outbound queue that keeps only the latest snapshot, or use `asyncio.wait_for` with a short timeout and drop or disconnect the laggard.
2. **`step()` is about 730 lines** (`engine/src/nether_earth/engine.py:278`). Its phases are separated only by comments such as "Step 2b2" and "Step 2c2".
   *Fix:* extract named phase functions (`(state, ctx) -> (state, events)`) run in a fixed order. This makes each phase testable and the order explicit.
3. **Legacy code for running without a world is still in the main path.** Large parts of `step()` sit behind `if world is not None:`. `MatchManager` also keeps a fallback to the M1 `new_game` bootstrap map (`backend/app/match/manager.py:280`). Production always has a world.
   *Fix:* remove the no-world mode or restrict it to test fixtures.
4. **Some rule constants live outside `EngineRules`.**
   - `MAX_ORDER_DISTANCE_MILES` is in `engine/src/nether_earth/orders.py:184` and is duplicated in `frontend/src/ui/menus.ts:12`.
   - The frontend also duplicates `TICKS_PER_HOUR`, `TICK_MS`, and the chassis, weapon, and target lists.

   *Fix:* move gameplay constants into `EngineRules`, and expose what the UI needs through the protocol schema or the snapshot.

## 3. Security

Hardening is scheduled for M10.4 and has not started. These are the concrete gaps:

| Severity | Finding | Fix |
|---|---|---|
| High | **No inbound limits.** Anyone can create unlimited matches. There is no per-connection message rate limit. The per-tick command queue is unbounded (`backend/app/match/runtime.py:302`), and every accepted command is written to the replay file. The WebSocket frame size is only capped by uvicorn's default of 16 MB. A single client can use up CPU and fill the disk. | Set `--ws-max-size` to about 64 KB. Cap commands per player per tick. Cap matches per IP and globally. |
| High | **Memory grows without bound.** Finished matches are never removed (#146), and matches that stay in `WAITING` never expire. | Call `dispose_match` after finalization and after the replay is flushed. Add a TTL for `WAITING` matches. |
| Medium | **The server URL can be overridden from a link.** `frontend/src/net/client.ts:28` accepts `?ws=<url>` in production. A crafted link can point the client at an attacker's server. If a session is stored in that tab's `sessionStorage`, the reconnect sends its session token there. | Honor the override only when `import.meta.env.DEV` is true. |
| Medium | **Backend slow-reader stall** (see §2.1). Doubles as a way to grief the opponent. | As in §2.1. |
| Low | No `Origin` check on `/ws`. Tokens travel inside messages, not cookies, so existing sessions cannot be hijacked this way; this only makes abuse easier. | Check `Origin` against `PUBLIC_BASE_URL`. |
| Low | The backend container runs as root. There is no TLS, and nginx sets no security headers. | M10.1 and M10.3. |

What is already right:

- Session tokens use `secrets.token_urlsafe(32)`, and join codes come from `secrets`.
- Nicknames are limited to 1–32 characters in the schema.
- The frontend escapes every value it puts into `innerHTML` (`esc()` in `frontend/src/ui/dom.ts`).
- `npm audit` is clean.

## 4. Code quality and maintainability

1. **CI is disabled.** Every line of `.github/workflows/ci.yml` is commented out. Much of the work here is agent-driven and runs in parallel, so this is the largest maintainability risk. Re-enable it now instead of waiting for M10.7, and add `npm test`, which the old workflow did not run.
2. **The frontend Docker image cannot build.** Compose uses `context: ../frontend`, but the build reads `../data/maps` (map generation) and imports `../../../protocol/generated/types`. Building the frontend directory on its own fails with `ENOENT .../data/maps/zx-spectrum-original.yaml`, so `docker compose up --build` fails. The Dockerfile also runs `npm install` without copying `package-lock.json`, so builds are not reproducible.
   *Fix:* use the repo root as the build context with `dockerfile: frontend/Dockerfile`, copy the lockfile, and run `npm ci`.
3. **mypy fails when a parent directory contains `__init__.py`.** On the reviewer's machine, `~/Projects/__init__.py` made mypy report "Source file found twice under different module names." Add `explicit_package_bases = true` to both `[tool.mypy]` sections.
4. **Ruff runs only its default rules.** Enable `B`, `UP`, `SIM`, `I`, and `RUF` for cheap bug catching.
5. **Small duplication.** `_manhattan` is defined in both `orders.py:607` and `destruction.py:419`. Keep one in a shared geometry helper.
6. **`assert` is used for invariants in production code** (`engine.py`, `backend/app/transport/victory.py`, `backend/app/transport/ws.py`). Running Python with `-O` removes these checks. Raise explicit errors instead.
7. **Dependency pinning is inconsistent.** `vitest` uses `^3.2.4`; every other frontend dependency is pinned exactly.
8. **Stale docs.** The root `README.md` still says "M0 intentionally provides only foundation behavior."

## 5. Readability

- **Comments and docstrings outweigh code.** Across `engine/src` and `backend/app`:

  | Lines | Count | Share |
  |---|---|---|
  | Code | 8,388 | 41% |
  | Comments and docstrings | 9,750 | 48% |

  The most extreme modules:

  | Module | Code lines | Comment and docstring lines |
  |---|---|---|
  | `rules.py` | 130 | 388 |
  | `robot_stack.py` | 29 | 121 |
  | `heli_pad.py` | 45 | 131 |
  | `docking.py` | 113 | 258 |

- **Process history is embedded in source.** There are 334 references such as "issue #97", "M7 Task 10 review, Important I3", and "M9.1 audit gap G1". That history already lives in git and in the PRs, and inside the code it goes stale.
- **Recommendation:**
  - Keep docstrings to the contract: what the code does, which rule it enforces, and a pointer to the evidence (`rules.py` or `_specs/open-questions.md`).
  - Remove narrative history.
  - Add an AGENTS.md rule: "Do not write issue, task, or review history into code comments; put it in commits or PRs." Without that rule, agents will keep adding it.
- Naming, type annotations, and module boundaries are good.

## 6. Scalability

- **Current cost is low.** On the real 512×16 map with no robots, an idle `step()` takes 0.51 ms and a snapshot takes 0.06 ms and about 1 KB.
- **Pathfinding may exceed the tick budget.** Each robot with electronics runs A* over the 8,192-cell map. A blocked robot re-plans every tick (`orders._navigate`, reported as `BLOCKED`). Checking an unreachable goal searches the whole map. With many stuck robots, one tick could exceed the 50 ms budget.
  *Action:* profile during M10.6. If needed, cache routes until the goal changes or the relevant cells change.
- **Everything runs in one process.** All matches share one event loop, simulation runs on that loop, and match state is in memory. This matches the spec's single-host v1, but capacity is bounded by one CPU core.
  *Action:* measure how many matches fit per core in M10.6 and set a global match cap from that number.
- **Bandwidth.** A full snapshot every tick is fine at about 1 KB. Revisit sending only changes once robot and projectile counts grow the snapshot.

## Prioritized actions

| # | Action | Area | Suggested owner |
|---|---|---|---|
| 1 | Re-enable CI, including `npm test` | Maintainability | M10.7 (pull forward) |
| 2 | Fix the frontend Docker build context; use `npm ci` with the lockfile | Deployment | M10.1 / M10.2 |
| 3 | Decide open question §19 (autonomous nuclear use) | Gameplay | Owner, then M5/M6 |
| 4 | Remove finished matches (#146), add a `WAITING` TTL, and cap matches, commands per tick, and frame size | Security / memory | M10.4 |
| 5 | Stop a slow client from stalling the tick loop | Architecture / security | M10.4 or M10.6 |
| 6 | Restrict `?ws=` to dev builds | Security | M10.4 |
| 7 | Engine-owned rules version and content hash; reconcile §21 message names with the spec | Spec compliance | M7 follow-up |
| 8 | Split `step()` into phase functions and remove the no-world mode | Maintainability | Refactor issue |
| 9 | Remove process history from comments; add the AGENTS.md rule | Readability | Refactor issue |
| 10 | Close #144 and the M4–M7 plan trackers; run the human match (#119) | Tracker hygiene | Owner |
