# Nether Earth — Repository-wide Code & Security Review

- **Date:** 2026-09-30
- **Scope:** `engine/`, `backend/`, `frontend/` (UI/DOM handling and Docker only), `deploy/`, `.github/`, dependency manifests, existing tests and docs.
- **Method:** static review, end-to-end tracing of the WebSocket workflows, self-challenge of every security finding. No code was modified and no external systems were contacted. One claim (NE-04) was reproduced locally by calling `_validate_nickname`.
- **Relation to earlier work:** builds on `docs/milestone-10/security-review.md` (M10.4) and `docs/reviews/2026-09-21-project-review.md`. Findings there that are still true are not repeated unless this review adds new evidence.
- **Not reviewed in depth:** the pure-Python game rules in `engine/src` (gameplay correctness is covered by the spec-driven tests), the Pixi rendering code, and live infrastructure (firewall, DNS, TLS certificates, host permissions).

---

## 1. Executive summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 3 |
| Low | 8 |
| Informational | 2 |

No Critical or High issue survived the challenge pass. There is no database, no shell/eval, no unsafe deserialization, no file path derived from client input, and no cookie/session-based authentication. The attack surface is a single WebSocket endpoint plus two read-only HTTP probes, and it is already bounded by size, rate, origin and capacity limits.

The most consequential issues:

1. **NE-01 (Medium): a single IP can exhaust match capacity.** The gateway allows about 30 handshakes/min per IP, each can hold one lobby for 15 min, and `max_matches` is 200 in total. One client fills the server in roughly 7 minutes and legitimate players get `server_busy`.
2. **NE-02 (Medium): the tick loop performs synchronous filesystem I/O on the event loop.** Every tick of every match opens, appends and closes a file inside an `async` observer. This couples event-loop latency, tick timing and snapshot broadcast to disk latency for all matches.
3. **NE-03 (Medium): replay artifacts have no retention or quota.** In-memory matches are swept; on-disk replay directories are never removed, so disk fills over time (and, via NE-01, faster under abuse). `/ready` then reports 503 and the stack stops accepting matches.
4. **NE-05 (Low, probable): `/ready` iterates a dict that the event loop mutates from a threadpool thread.** This can intermittently return HTTP 500 and flip the container health check.
5. **Supply chain / CI (NE-08..NE-10):** no dependency or code scanning in CI, actions and base images referenced by mutable tags, lock file without hashes.

---

## 2. Architecture and trust boundaries

**Languages/frameworks:** Python 3.12 (engine, backend: FastAPI + Starlette + uvicorn, pydantic v2), TypeScript (Vite + Pixi.js frontend), Nginx gateway, Docker Compose deployment.

```
browser ──HTTP(S)/WS──▶ Nginx gateway ──▶ frontend (static Nginx)
                            │
                            └──▶ backend (uvicorn/FastAPI)
                                   ws.py ─▶ MatchManager ─▶ MatchRuntime (20 Hz asyncio task)
                                              │                   │
                                              │                   └─▶ engine.step (pure, deterministic)
                                              └─▶ ReplayWriter ─▶ host dir (meta.json, commands.jsonl, lifecycle.jsonl)
```

- **Persistence:** none other than replay files. All match, session and connection state is in process memory (single process, single event loop). A restart ends every match (documented in `docs/operations/runbook.md`).
- **Authentication model:** guest play. `create`/`join` mint a 256-bit `secrets.token_urlsafe(32)` session token; every later message carries `sessionToken`, `matchId`, `playerId`. The token is the only credential (`manager.resolve_session`).
- **Authorization model:** token → `(match, player)` is fixed at create/join. `ws.py` verifies that the message's `matchId`/`playerId` match the token's slot, and pins a connection to one session (`session_mismatch`). Gameplay commands are then re-validated by the engine.
- **Trust boundaries:**
  1. Internet → gateway (Nginx rate/connection limits, security headers, TLS overlay).
  2. Gateway → backend (internal Compose network only, no published port).
  3. WebSocket frame → protocol parser (`parse_client_message`, pydantic) → `payload_to_command` → engine validation.
  4. Backend → host filesystem (replay directory, bind mount).
  5. Server-provided strings (nicknames, error messages) → frontend DOM (`esc()` + `innerHTML`).
- **Concurrency model:** one asyncio loop. Per-match tick tasks, a 15 s sweeper task, per-connection handler coroutines. `MatchManager` uses a `threading.Lock`, but nothing in the WebSocket path runs in another thread. The sync `/health` and `/ready` handlers run in the Starlette threadpool.
- **Config/secrets:** `NETHER_EARTH_*` env vars, validated in `app/config.py`. Production fails fast on missing/invalid values. There are no application secrets (no signing keys, DB credentials or API tokens).
- **CI/CD:** one GitHub Actions workflow (`python`, `frontend-protocol`, `deployment` jobs) with `contents: read`. There is no release/publish job with secrets.

## 3. Attack surface

| Entry point | Validation | Reaches |
|---|---|---|
| `GET /ws` handshake | `Origin` allow-list (when header present); gateway `limit_req` 30/min, `limit_conn` 32 | accept, per-socket state |
| WS text frame | uvicorn `--ws-max-size 16384`, app size check, token bucket 40/s burst 80, pydantic discriminated union | match/session logic |
| `create`/`join` | nickname 1–32 chars + control/bidi filter; join code pattern; 5 failed joins per socket; `max_matches` | in-memory state; replay `meta.json` (nickname) |
| Gameplay command payload | `payload_to_command` structural checks, then engine `validate_command_batch` | engine state, `commands.jsonl` |
| `GET /api/health`, `/api/ready` | GET only, `limit_req` 10/s | counts only |
| Env vars | `load_settings` strict validation | Path for replay dir, logging |
| `data/maps/*.yaml` | `yaml.safe_load`, repo-controlled | engine world |

**Dangerous sinks checked and found absent or safe:** SQL (none), shell/subprocess (none in `backend/app`, `engine/src`), `eval`/`exec`/pickle (none), dynamic import (none), outbound HTTP (none; only the container healthcheck to localhost), template rendering (none), file paths built from client data (only server-generated `uuid4().hex` match ids, additionally guarded by `_MATCH_ID_RE` in `replay/writer.match_dir`), HTML rendering (`innerHTML` in the frontend, every dynamic value passes through `esc()`; see Positives).

## 4. Security findings

### NE-01 — Match-capacity exhaustion from one IP (Medium, Confidence: High) — Probable vulnerability (availability)

- **Location:** `backend/app/match/manager.py::_create` (capacity check, ~L270); `backend/app/config.py` (`DEFAULT_MAX_MATCHES=200`, `DEFAULT_WAITING_TIMEOUT_S=900`); `deploy/nginx/nether-earth-http.inc` (`ne_ws` 30r/m).
- **Evidence:** a socket may create one match; an unready or solo lobby is retained for 900 s after creation; capacity counts every match in every state; the per-IP handshake limit is 30/min with burst 30.
- **Why it matters:** 30/min × 15 min = 450 lobbies from one address, more than double the 200-match cap. Once at capacity every `create` returns `server_busy`. The M10.4 review accepts *distributed* abuse but the *single-IP* case is arithmetically open.
- **Scenario:** a script opens 30 WebSockets/min, sends `create` on each and holds or drops them. Within about 7 minutes no one else can start a match, and this can be sustained indefinitely.
- **Fix:** (a) drop a WAITING lobby as soon as its creator's socket disconnects (or after a short grace period) instead of waiting 15 min; (b) add a per-source-IP cap on concurrent matches at the backend, or in Nginx (`limit_conn` already exists per IP: lower it and add a lower per-IP `limit_req` for `create`); (c) consider reserving capacity separately for WAITING vs ACTIVE.
- **Regression test:** with a small `max_matches`, create N lobbies over sockets and close them; assert that capacity is released after the creator disconnects and that a further `create` succeeds.
- **Challenge:** the M10.4 doc and `test_manager_capacity_counts_every_match` show the cap is a deliberate control, not a bug; the finding stays Medium because the cap itself becomes the DoS lever. Requires no privileges; impact is limited to availability. Deployment note: if the gateway sits behind another proxy without `real_ip`, all clients share one bucket (documented already).

### NE-02 — Blocking filesystem I/O in the per-tick async path (Medium, Confidence: High) — Reliability problem

- **Location:** `backend/app/replay/writer.py::ReplayWriter._append_jsonl` (`os.open`/`os.write`/`os.close`), invoked from `make_replay_tick_recorder._on_tick_commands`, an `async` observer called from `MatchRuntime._run`; also `_write_meta_atomic` (write+`os.replace`) on start/finish, and `_replay_dir_status` (creates a temp file) in `/ready`.
- **Evidence:** the M10.6 comment says it "cost a fraction of" a buffered handle, but it is still 3 syscalls per match per tick on the event-loop thread. With 200 matches that is about 4,000 open/write/close cycles per second, plus the snapshot broadcasts on the same loop.
- **Why it matters:** a slow disk, a network-backed bind mount or a full journal stalls *all* matches (tick overruns, stalled sends, then forced closes) not just the affected one. Errors are caught and logged (`_report_failure`), but latency is not.
- **Fix:** keep one append-only file descriptor per match open (closed in `finish_match`/dispose) and/or hand writes to a single background writer (queue + thread via `asyncio.to_thread`/`loop.run_in_executor`). Skip writing ticks with no accepted commands if replay semantics allow (verify against `replay/verify.py` first).
- **Test:** an async test that injects a slow `os.write` and asserts tick interval of a second match stays within tolerance.
- **Note:** `docs/milestone-10/performance-report.md` measured acceptable numbers on a healthy local disk; this finding is about the worst-case coupling, not the measured average.

### NE-03 — Replay directory has no retention or disk quota (Medium, Confidence: Medium) — Reliability problem

- **Location:** `backend/app/replay/writer.py` (`_start_match`, `finish_match`); `deploy/docker-compose.yml` (host bind mount, no size bound); `backend/app/main.py::ready`.
- **Evidence:** in-memory matches are removed by `MatchManager.sweep`, but nothing removes `<replay_dir>/<match_id>/`. Each active match appends to `commands.jsonl` every tick with commands. Nothing bounds directory count or total size. Abandoned matches stay `in_progress` forever (acknowledged in the M10 review).
- **Why it matters:** disk fills over weeks of normal play, faster under NE-01-style abuse. `/ready` then returns 503 ("unwritable"), the gateway health path degrades, and replays of running matches are silently lost.
- **Fix:** define an operator-visible retention policy (age and/or total size; a small cron or startup pruning job is enough), and mark orphaned `in_progress` artifacts as `interrupted` at startup. Document it in the runbook. Confirm with the owner whether replays are to be kept indefinitely (this is a product/ops decision, not a code fact).
- **Test:** unit test on a prune function using `tmp_path` and fake mtimes.
- **Unknown:** host disk size and any external log/backup rotation are not visible from the repository.

### NE-04 — Nickname validation allows invisible/format characters (Low, Confidence: High) — Confirmed defect (low impact)

- **Location:** `backend/app/match/manager.py::_validate_nickname`.
- **Evidence:** rejects categories `Cc, Cs, Co, Cn` and a bidi list, but not `Cf` (for example U+200B zero-width space). Reproduced: `_validate_nickname("\u200b\u200b")` returns `'\u200b\u200b'` (not rejected, not stripped). Duplicate nicknames within a match are also accepted.
- **Why it matters:** invisible or empty-looking nicknames, or a guest copying the host's exact name, allow player impersonation in the lobby UI and in replay `meta.json`. No injection: the frontend escapes output (see Positives).
- **Fix:** reject or strip the `Cf` category (except where a legitimate need is shown), require at least one visible character, and optionally reject a nickname equal to the opponent's. Nickname policy is not specified in `_specs`; confirm before tightening.
- **Test:** parametrize `test_nickname_control_characters_rejected` with `"\u200b"`, `"\u2060"`, `"\ufeff"`.

### NE-05 — `/ready` reads event-loop-mutated dicts from a threadpool thread (Low, Confidence: Medium) — Probable defect

- **Location:** `backend/app/main.py::ready` (plain `def`, so it runs in Starlette's threadpool); `backend/app/transport/connections.py::ConnectionRegistry.connection_count` (`sum(len(players) for players in self._by_match.values())`), `len(match_manager)`.
- **Evidence:** `register`/`unregister` mutate `_by_match` on the event-loop thread. Iterating `.values()` in another thread while the dict changes size raises `RuntimeError: dictionary changed size during iteration`.
- **Scenario:** at the moment a player connects, the 10-second Docker health check can hit the window and receive HTTP 500, which counts toward `retries: 3` and is visible to the gateway `depends_on` chain. The window is narrow, so it is intermittent.
- **Fix:** make `ready` (and `health`) `async def` so it runs on the loop, or snapshot with `tuple(self._by_match.values())` under a lock.
- **Test:** a stress test running `connection_count()` in a thread while another thread registers/unregisters sockets.
- **Challenge:** CPython's GIL makes this rare, and nothing corrupts state; therefore Low.

### NE-06 — Session takeover leaves the previous socket open and functional (Low, Confidence: High) — Defense-in-depth

- **Location:** `backend/app/transport/connections.py::register` (docstring: "does not itself close the old one"); `ws.py` reconnect branch.
- **Evidence:** a `reconnect` with a valid token replaces the registry entry, but the old connection stays alive, is still bound, and can still submit commands until it drops.
- **Why it matters:** only the token holder can trigger this, so it is not an authorization bypass. But a stolen token (see NE-07) remains usable in parallel with the victim, and the victim gets no signal that a second client attached.
- **Fix:** close the superseded socket with a defined code (for example 4000 "replaced") when a newer one registers for the same `(match, player)`.
- **Test:** connect twice with one token; assert the first socket is closed.

### NE-07 — Default gateway serves plain HTTP/WS; the token is the only credential (Low, Confidence: Medium) — Defense-in-depth / deployment

- **Location:** `deploy/nginx/gateway.conf` (`listen 8080`, no TLS), `deploy/docker-compose.tls.yml` (optional TLS overlay), `deploy/README.md`.
- **Evidence:** the base Compose stack has no TLS; session tokens travel in JSON frames; TLS is an opt-in overlay (`gateway-tls.conf` has TLS 1.2/1.3, HTTP→HTTPS redirect, HSTS).
- **Why it matters:** over `ws://` an on-path attacker can read a session token and take over the seat. It is not exploitable when the recommended TLS overlay is used.
- **Fix:** state clearly in `deploy/README.md` and the release checklist that production must use the TLS overlay (or terminate TLS upstream); optionally make `NETHER_EARTH_PUBLIC_BASE_URL` in production require `https://`. Verify at deploy time which variant is running: cannot be determined from the repository.
- **Related:** `Strict-Transport-Security` lacks `includeSubDomains`/`preload`, which is a reasonable choice for a single host.

### NE-11 — Small information exposures (Low, Confidence: High) — Defense-in-depth

- `/api/ready` is public and returns exact counts of matches, runtimes and connections, and replay-dir status. This reveals load and lets an attacker measure NE-01 progress. Consider restricting `/api/ready` at Nginx to the internal network or removing counts from the public response (the Docker health check calls the backend directly and does not need the gateway route).
- `join` distinguishes `match_not_found` from `match_full`, letting an attacker learn which codes exist. Combined with a 36^6 (~2.2×10^9) space, 5 failures per socket and 30 sockets/min/IP, the expected guess rate is about 150/min/IP, and with at most 200 live lobbies the chance of any hit per guess is about 10^-7. Targeted hijack is impractical; use one error code to remove the oracle if desired.

## 5. Correctness and reliability findings

### NE-12 — `assert` used for runtime invariants in the WebSocket handler (Low, Confidence: High) — Correctness/maintainability

- **Location:** `backend/app/transport/ws.py` lines ~524–525 (forfeit resync).
- **Evidence:** the same file explains at ~L407 that a real exception is used "not `assert`, which `python -O` strips". The forfeit branch still uses `assert`. The container does not run with `-O`, so it currently works; it is inconsistent and would become `None` dereference in an optimized run.
- **Fix:** replace with explicit checks/`RuntimeError`, matching the rest of the file.

### NE-13 — No application-level handshake/idle timeout for unauthenticated sockets (Informational, Confidence: Medium)

- An accepted socket that never sends `create`/`join`/`reconnect` is held until the gateway's 120 s `proxy_read_timeout` (and 32 per IP). Direct backend access is not possible from outside Compose. Not exploitable as deployed; note it if the backend is ever exposed without the gateway. Consider a 10–30 s "must bind" timeout in `ws.py`.

### Other correctness observations (no separate ID)

- **Sequence high-water mark:** `MatchRuntime.submit_command` rejects any `client_sequence <= last`. A client can send one huge sequence and lock itself out. Self-inflicted only, and harmless to others.
- **Fail-safe handler:** `ws.py` catches `Exception`, logs by `match_id` (never token) and closes 1011. Good, but tick-loop exceptions in observers should be verified to be isolated per match (see the sweep and tick tests).

## 6. Concurrency and data-integrity findings

- The real concurrency model is a single asyncio loop; `MatchManager._lock` is a `threading.Lock` held only for short synchronous sections, and several read paths (`mark_disconnected`, `_get_match_locked` callers) touch the same dicts without it. Since no thread other than the `/ready` handler (NE-05) reads them, this is currently safe but the lock gives a false impression of thread-safety. Either document "loop-thread only" or make the sync HTTP handlers `async`.
- **Replay integrity:** meta writes use temp file + `os.replace` (atomic); `commands.jsonl` uses single `O_APPEND` writes per line (no torn lines below `PIPE_BUF`-like limits on local filesystems). Failures are caught and reported once per match rather than crashing the match. There is no `fsync`; a host crash may lose the tail of a replay. Acceptable for v1; mention in the runbook.
- **No persistence layer** means no transaction/migration/N+1 issues. Restart loses all matches by design.
- **Overrun handling:** the tick loop tracks `consecutive_overruns`; combined with NE-02 this is where a slow disk would surface.

## 7. Architecture and maintainability findings

- **NE-14 (Low): `transport/ws.py` is a single ~450-line coroutine with deeply nested branches** (create, join, ready, leave, command, reconnect). It closes over five mutable locals (`bound`, `bucket`, `failed_joins`, `disconnect_notified`, functions). This hinders unit testing of individual handlers and makes changes like NE-06 riskier. Extract per-message handlers taking a small per-connection context object. Do it incrementally behind the existing WS tests.
- **NE-15 (Low): comment volume.** As noted in the 2026-09-21 review, many docstrings narrate task/issue history rather than behavior (for example the "M7 Task 7 review, Important I2" annotations). This makes real invariants harder to find. Trim opportunistically.
- **Layering is otherwise sound and matches the spec:** engine has no FastAPI/network imports; backend orchestrates; protocol models are the boundary. The composition-root wiring in `create_app` is verbose but explicit.
- No substantive over-engineering was found. The observer composition helpers are small and justified.

## 8. Dependency, deployment and CI findings

### Confirmed (repository-visible)

- **NE-08 (Low): no automated dependency/code scanning.** `.github/` has no Dependabot config, no CodeQL, no `pip-audit`/`npm audit` step. The 2026-09-21 review ran `npm audit` manually once (0 vulns). Add Dependabot for `pip`, `npm`, `docker` and `github-actions`, and an audit step or scheduled workflow.
- **NE-09 (Low): mutable references.** `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4` are tag-pinned only; Docker images `python:3.12-slim-bookworm`, `node:22-alpine`, `nginxinc/nginx-unprivileged:1.28-alpine` are tag-pinned, not digest-pinned. Workflow permissions are minimal (`contents: read`) and no secrets are used, so the blast radius is small. Pin by SHA/digest and let Dependabot update them.
- **NE-10 (Low): `backend/requirements.lock` has exact versions but no `--hash` entries**, and `frontend/package.json` has a caret range for `vitest` (dev only; `package-lock.json` is used by `npm ci`). Consider `pip-compile --generate-hashes` and `pip install --require-hashes`. I did not verify CVEs for the pinned versions against an advisory database in this review, so no CVE is claimed.

### Hardening recommendations / observations

- `uvicorn --forwarded-allow-ips "*"` trusts `X-Forwarded-*` from any peer. It is safe as deployed (backend has no published port, and the app does not use client IPs), but it becomes a spoofing vector if the backend port is ever published. Prefer the gateway's network address or subnet.
- Nginx forwards `X-Forwarded-For $proxy_add_x_forwarded_for`, appending to any client-supplied header. Harmless today because nothing consumes it; the gateway rate-limits on `$binary_remote_addr` (correct).
- The backend healthcheck uses `urllib` against `127.0.0.1:8000/ready` — fine, but it turns NE-05 into a container health flap.
- Compose: `read_only`, `cap_drop: ALL`, `no-new-privileges`, memory/CPU limits, log rotation. `replay-dir-init` runs as root but with `network_mode: none`, only `CHOWN`, and a fixed command. Acceptable.
- Not verifiable from the repo: host firewall, whether port 80/443 is the only exposure, TLS certificate handling, branch protection, backups of the replay directory.

## 9. Test gaps (highest value first)

1. **Capacity release on lobby abandonment** (NE-01): creator disconnects → capacity freed.
2. **Replay writer under slow/failing I/O** (NE-02): a second match's tick cadence is not delayed; failures do not stop matches.
3. **Replay retention/prune** (NE-03).
4. **Nickname with format characters / duplicate nicknames** (NE-04).
5. **Threaded `/ready`/`connection_count` stress** (NE-05).
6. **Second socket with same token closes the first** (NE-06).
7. **Unbound socket idle timeout** (NE-13) if implemented.
8. **Cross-session command isolation:** a socket bound to session A sending a message with session B's valid token is covered by `session_mismatch`; add an explicit test that P2's token cannot issue commands as P1 across a live two-player match if not already present in `test_commands.py`.
9. **Config:** production requires `https://` public URL (if NE-07 is adopted).

Existing coverage is strong for: origin refusal, oversized frames, message flood, join-code guessing cap, stalled peer, capacity, unsafe replay ids, protocol schema conformance, deterministic replay hashing, reconnect/grace policy and victory.

## 10. Positive observations

- **Token quality:** `secrets.token_urlsafe(32)` session tokens, `secrets.choice` join codes, `secrets.randbits(63)` seeds, `uuid4` match ids; no weak randomness, no custom crypto.
- **Authorization is done once, centrally, before routing:** `resolve_session` → match/player equality checks → connection pinning. Tokens are never logged (`ws.py` logs `match_id` only); `_meta_players` explicitly excludes tokens from replay files.
- **Input bounds at every layer:** 16 KiB frame limit in both uvicorn and app, token bucket, per-socket join attempt cap, nickname bounds, pydantic strict discriminated unions, engine-side validation of every command.
- **Cross-site WebSocket hijacking is mitigated** via `Origin` allow-list, tested in unit tests and in `deploy/smoke.sh` (foreign origin → 403).
- **Slow-consumer protection:** `_send_text` times out sends and closes stalled sockets so one peer cannot freeze a match.
- **Filesystem safety:** replay paths are built from server-generated ids and validated by regex; atomic meta writes; no user-controlled filenames.
- **Safe parsing:** only `yaml.safe_load` on repository-owned map files; no pickle/eval/subprocess in application code.
- **Frontend output encoding:** all dynamic values in `innerHTML` templates pass through `esc()` (escapes `& < > " '`), and the gateway CSP forbids inline/remote scripts (`script-src 'self'`).
- **Configuration hygiene:** production fails fast on missing public URL/replay dir; OpenAPI docs are disabled in production; no secrets in the repository.
- **Container hardening:** non-root uid 10001 backend, unprivileged Nginx, read-only root FS, dropped capabilities, resource limits, pinned Python lock installed with `--no-deps` and `pip check`.
- **CI:** least-privilege token, concurrency control, lint + strict mypy + tests + generated-artifact drift check + deployment smoke test on every PR.

## 11. Prioritized remediation plan

Findings NE-01…NE-15 above; there are no Critical/High items.

### Immediate
| Item | Findings | Effort | Change risk | Status |
|---|---|---|---|---|
| Release lobby capacity when the creator disconnects; add per-IP concurrent-match cap | NE-01 | Medium | Low–Medium (gameplay unaffected; touches lifecycle) | done (PR #307) |
| Make `/health` and `/ready` `async` (or snapshot dicts) | NE-05 | Small | Low | done (PR #307) |
| Document/enforce TLS overlay for production | NE-07 | Small | Low | skipped (owner) |

### Near term
| Item | Findings | Effort | Change risk | Status |
|---|---|---|---|---|
| Move replay writes off the tick path (persistent fd / writer thread) | NE-02 | Medium | Medium (must keep replay verification byte-identical) | done (PR #307) |
| Replay retention/prune + mark orphaned `in_progress` on startup | NE-03 | Medium | Low | done (PR #307) |
| Add Dependabot + audit/CodeQL workflow | NE-08 | Small | Low | done (PR #307) |
| Close superseded socket on session takeover | NE-06 | Small | Low | done (PR #307) |
| Nickname policy for format characters/duplicates (needs owner confirmation) | NE-04 | Small | Low | done (PR #307) for format characters; duplicates: open question |

### Later
| Item | Findings | Effort | Change risk | Status |
|---|---|---|---|---|
| Pin actions by SHA and images by digest; lock hashes | NE-09, NE-10 | Small–Medium | Low | done (PR #307) |
| Trim `/api/ready` public output; unify join error codes | NE-11 | Small | Low | done (PR #307) for `/ready` counts; join-code unification: open question |
| Replace `assert` in `ws.py` | NE-12 | Small | Low | done (PR #307) |
| Unbound-socket handshake timeout | NE-13 | Small | Low | done (PR #307) |
| Split `ws.py` into per-message handlers; prune history-narrating comments | NE-14, NE-15 | Medium | Medium (large diff; rely on existing WS tests) | separate plan |
| Restrict `--forwarded-allow-ips` | §8 | Small | Low | done (PR #307) |

## Appendix A — Systemic patterns

1. **Resource bounds are per-connection or per-IP, but shared pools (match capacity, disk, event loop) are global** (NE-01, NE-02, NE-03). The fix pattern is the same: introduce ownership/quotas for each shared pool (per-IP match count, retention for artifacts, off-loop I/O).
2. **Thread-safety intent is implicit** (NE-05, the unused-in-practice `threading.Lock`). Decide on "loop-thread only" and enforce it (async handlers) or make shared registries genuinely thread-safe.
3. **Supply-chain controls are manual** (NE-08–NE-10): pins exist but nothing automatically keeps them fresh or audited.

## Appendix B — Findings challenged and downgraded/removed

- *Join-code brute force* → kept only as an informational oracle (NE-11): search space, per-socket and per-IP limits make targeted takeover impractical.
- *CSWSH via missing Origin* → not a vulnerability: no ambient credentials (cookies) exist, so a non-browser client omitting `Origin` gains nothing a direct client lacks.
- *Non-constant-time token lookup* → not exploitable: dictionary lookup on a 256-bit random token; no realistic timing oracle over a network with rate limiting.
- *Path traversal in replay writer* → removed: ids are server-generated and regex-validated.
- *XSS through nicknames* → removed: `esc()` on every interpolation; CSP disallows inline scripts.
- *`--forwarded-allow-ips "*"` as High* → downgraded to hardening: the backend is not published and does not use client IPs.
