# Security & Code Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close findings NE-01…NE-06 and NE-08…NE-13 (plus the `--forwarded-allow-ips` hardening note) from the 2026-09-30 repository review, each with a regression test.

**Architecture:** All changes stay in `backend/` (match lifecycle, transport, replay, config), `deploy/`, `.github/` and docs. The engine package is untouched. The pattern from the review's Appendix A drives the design: give every shared pool an owner/quota (lobby capacity released on abandonment, replay writes off the event loop with a bounded backlog, replay directory retention), and make thread-safety explicit by moving the HTTP probes onto the event loop.

**Tech Stack:** Python 3.12, FastAPI/Starlette, pytest (`asyncio_mode = auto`, so `async def test_*` needs no marker), ruff, mypy strict, Docker Compose, Nginx, GitHub Actions.

**Spec:** `docs/reviews/2026-09-30-security-and-code-review.md` (the findings; read §4–§9 and §11). Project rules: `AGENTS.md`.

## Scope decisions (read before starting)

- **NE-07 is excluded** by the task owner's instruction.
- **NE-14 (split `ws.py` into per-message handlers) and NE-15 (comment trimming) are excluded** from this plan. NE-14 is a ~600-line mechanical refactor with no behavior change; it deserves its own plan after this one lands, because this plan's `ws.py` edits (NE-01, NE-06, NE-12, NE-13) would otherwise conflict with it. NE-15 is applied opportunistically: when a step edits a docstring/comment that narrates task history (“M7 Task 7 review, Important I2”), replace it with the behavioral statement only. Never touch comments outside the lines a step already changes.
- **NE-11 join-code oracle (unify `match_full`/`match_not_found`) is excluded.** The review's own arithmetic puts a hit at ~10⁻⁷ per guess; unifying codes is a wire-visible protocol change touching frontend copy for near-zero gain. Only the `/api/ready` count exposure half of NE-11 is implemented (Task 2).
- **NE-01 option (c) (reserve capacity WAITING vs ACTIVE) is not implemented.** Releasing abandoned lobbies (Task 5) plus the existing Nginx `limit_conn ne_conn 32` already caps one IP at ≤ 32 held lobbies + at most 15 in the grace window, far under `max_matches=200`. Record this arithmetic in the runbook (Task 5 step 12).
- **NE-04 duplicate-nickname rejection is not implemented.** Nickname policy is not in `_specs/`; a duplicate rule is a product decision. Task 3 records it in `_specs/open-questions.md`. Rejecting invisible format characters is implemented: it extends the existing control-character filter and decides no gameplay rule.
- **NE-03 retention default is "keep forever"** (knob unset). Whether to prune by default is an owner decision recorded in `_specs/open-questions.md` (Task 9). The knob and the startup orphan-marking ship now.

## Global Constraints

- Engine (`engine/`) is never modified; backend never implements gameplay rules (AGENTS.md).
- Python 3.12; `ruff check engine backend` and `(cd backend && mypy app)` must stay clean after every task (strict mypy: annotate everything).
- Run tests from the repo root: `pytest -q backend/tests/<path>` (root `pytest.ini`, `asyncio_mode = auto`).
- No PostgreSQL, Redis, brokers, Kubernetes, React (AGENTS.md).
- Replay artifacts must stay byte-identical in content and line order (`backend/tests/acceptance/test_m9_full_match.py` and `tests/replay/test_replay_log.py` verify this).
- Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Deployment smoke (`deploy/smoke.sh`) needs Docker; run it locally for Tasks 11–13 (CI is billing-blocked on this repo; the owner merges PRs).
- Do the work on a worktree branch `security/2026-09-30-review` (Task 0). One PR at the end referencing the tracking issue.

## Review Focus

Inputs the review implies but no finding's own test exercises. Each line's test is added to the owning task.

1. **An unbound socket that sends an invalid frame every few seconds must still be closed at the bind deadline.** A naive per-receive timeout resets on every frame. → Task 6 step 2, second test (deadline-based).
2. **A lobby whose creator drops and rejoins inside the grace window must survive.** Otherwise a page refresh in the lobby loses the shared join code. → Task 5 step 2, `test_reoccupied_lobby_is_not_disposed`.
3. **A replay write backlog (stalled disk) must drop and report, not grow memory without bound, and the match must keep ticking.** → Task 8 step 2, `test_backlog_beyond_bound_is_dropped_and_reported_once`.
4. **A corrupt or foreign `meta.json` in the replay directory at startup must not crash the process.** → Task 9 step 2, `test_mark_interrupted_skips_unreadable_meta`.
5. **A second socket for a session whose first socket is already gone must not raise** (register returns a dead socket; closing it must be a no-op). → Task 4 step 4: the existing `test_reconnect_returns_resync_snapshot_and_rebinds_connection` covers it; keep it green, do not skip it.

---

### Task 0: Tracking issue and worktree

**Files:** none in the repo.

- [ ] **Step 1: Create the tracking issue**

```bash
gh issue create --title "Security review 2026-09-30: remediate NE-01..NE-06, NE-08..NE-13" \
  --body "Implements docs/reviews/2026-09-30-security-and-code-review.md §11 except NE-07 (owner: skip), NE-14/NE-15 (separate plan) and the join-code unification half of NE-11. Plan: docs/superpowers/plans/2026-10-03-security-review-remediation.md"
```

Note the issue number; every commit body below says `Refs #<n>`.

- [ ] **Step 2: Create the worktree**

```bash
git worktree add ../nether_earth-security -b security/2026-09-30-review main
cd ../nether_earth-security
python -m pip install -c backend/requirements.lock -e './engine[dev]' -e './backend[dev]'
pytest -q backend/tests/transport/test_ws.py  # baseline: must pass before any change
```

---

### Task 1: NE-12 — replace `assert` invariants in the forfeit resync branch

**Files:**
- Modify: `backend/app/transport/ws.py:522-535`
- Test: `backend/tests/transport/test_ws.py::test_reconnect_to_an_already_forfeited_match_replays_the_durable_result` (existing; the gate)

**Interfaces:** none.

- [ ] **Step 1: Run the existing gate test to confirm green baseline**

Run: `pytest -q backend/tests/transport/test_ws.py -k forfeited_match_replays`
Expected: PASS

- [ ] **Step 2: Replace the two asserts**

In `backend/app/transport/ws.py` replace:

```python
                        if match_result.outcome is MatchOutcome.FORFEIT:
                            assert match_result.forfeiting_player_id is not None
                            assert match_result.winner_player_id is not None
                            await websocket.send_text(
```

with:

```python
                        if match_result.outcome is MatchOutcome.FORFEIT:
                            forfeiting = match_result.forfeiting_player_id
                            winner = match_result.winner_player_id
                            if forfeiting is None or winner is None:
                                # Invariant, not client input: a FORFEIT result
                                # always names both seats. A real exception
                                # (not `assert`, which `python -O` strips).
                                raise RuntimeError(
                                    f"match {match.match_id!r} has a FORFEIT result "
                                    "without both player ids"
                                )
                            await websocket.send_text(
```

and in the `ServerForfeit(...)` constructor below it change
`forfeiting_player_id=match_result.forfeiting_player_id.value,` → `forfeiting_player_id=forfeiting.value,`
and `winner_player_id=match_result.winner_player_id.value,` → `winner_player_id=winner.value,`.

- [ ] **Step 3: Verify**

Run: `pytest -q backend/tests/transport/test_ws.py -k "forfeited_match_replays or no_contested" && ruff check backend && (cd backend && mypy app)`
Expected: PASS, no lint/type errors. (`grep -n "assert " backend/app/transport/ws.py` must print nothing.)

- [ ] **Step 4: Commit**

```bash
git add backend/app/transport/ws.py
git commit -m "fix(backend): raise instead of assert on forfeit resync invariants (NE-12)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: NE-05 + NE-11 — `/health` and `/ready` on the event loop; counts only outside production

**Files:**
- Modify: `backend/app/main.py:301-327`
- Test: `backend/tests/test_health.py`

**Interfaces:**
- Produces: `/ready` JSON is `{"status", "checks"}` in production; `{"status", "checks", "matches", "runtimes", "connections"}` otherwise. `tests/match/test_sweep.py::test_expired_lobby_owner_is_told_and_disconnected` already reads `["matches"]` with development settings and keeps working.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_health.py`:

```python
import inspect
from pathlib import Path

from app.config import Settings
from app.main import create_app


def test_operations_endpoints_run_on_the_event_loop(tmp_path: Path) -> None:
    """NE-05: a threadpool handler iterating loop-mutated dicts can raise mid-iteration."""
    app = create_app(settings=Settings(replay_dir=tmp_path))
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}  # type: ignore[attr-defined]
    assert inspect.iscoroutinefunction(endpoints["/health"])
    assert inspect.iscoroutinefunction(endpoints["/ready"])


def test_ready_hides_counts_in_production(tmp_path: Path) -> None:
    """NE-11: load figures are not public in production."""
    settings = Settings(production=True, public_base_url="https://play.example.com", replay_dir=tmp_path)
    with TestClient(create_app(settings=settings)) as client:
        body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert set(body) == {"status", "checks"}


def test_ready_reports_counts_in_development(tmp_path: Path) -> None:
    with TestClient(create_app(settings=Settings(replay_dir=tmp_path))) as client:
        body = client.get("/ready").json()
    assert body["matches"] == 0 and body["runtimes"] == 0 and body["connections"] == 0
```

Keep the file's existing imports (`TestClient` is already imported there; check the head of the file and merge imports rather than duplicating).

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/test_health.py`
Expected: the first and second new tests FAIL (`iscoroutinefunction` false; body has extra keys).

- [ ] **Step 3: Implement**

In `backend/app/main.py` replace the two endpoint definitions:

```python
    @fastapi_app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        """Liveness: the process is up and serving HTTP. No gameplay logic.

        ``async`` so it runs on the event loop, never in Starlette's threadpool:
        every registry here is mutated on the loop and is not thread-safe.
        """
        return {"status": "ok"}

    @fastapi_app.get("/ready", tags=["operations"])
    async def ready(response: Response) -> dict[str, object]:
        """Readiness: can this process accept and persist new matches right now?

        503 while shutting down or when the replay directory is not writable.
        Operational counts are included only outside production (NE-11): the
        route is public through the gateway and the figures reveal load.
        ``async`` for the same reason as ``health``.
        """
        checks = {
            "replay_dir": _replay_dir_status(replay_writer.base_dir),
            "accepting": "no" if shutting_down else "ok",
        }
        is_ready = all(value == "ok" for value in checks.values())
        if not is_ready:
            response.status_code = 503
        body: dict[str, object] = {"status": "ready" if is_ready else "not_ready", "checks": checks}
        if not settings.production:
            body["matches"] = len(match_manager)
            body["runtimes"] = len(runtime_registry)
            body["connections"] = connection_registry.connection_count()
        return body
```

Also in `backend/app/match/manager.py` class docstring (lines 112–117) replace the "Not async-aware and not thread-hostile-safe beyond a single coarse lock…" sentence with: `Loop-thread only: every caller runs on the asyncio event loop; the lock only serializes the bookkeeping mutation itself and is not a thread-safety guarantee for readers.` (This is the §6 "document loop-thread only" item.)

- [ ] **Step 4: Verify**

Run: `pytest -q backend/tests/test_health.py backend/tests/match/test_sweep.py backend/tests/test_config.py && ruff check backend && (cd backend && mypy app)`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/match/manager.py backend/tests/test_health.py
git commit -m "fix(backend): run health/ready on the event loop; hide counts in production (NE-05, NE-11)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: NE-04 — reject invisible format characters and nicknames with no visible character

**Files:**
- Modify: `backend/app/match/manager.py:653-667`
- Modify: `_specs/open-questions.md` (append one section before `## Resolution process`)
- Test: `backend/tests/match/test_manager.py`

**Interfaces:**
- Produces: `_validate_nickname` raises `InvalidNicknameError` for any `Cf` character and for a nickname without a letter/number/punctuation/symbol. Wire error code unchanged (`invalid_nickname`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/match/test_manager.py`:

```python
@pytest.mark.parametrize(
    "nickname",
    [
        "\u200b\u200b",  # zero-width spaces only
        "ali\u200bce",  # zero-width space inside
        "bob\u2060",  # word joiner
        "\ufeffcarol",  # BOM
        "\u3000\u3000",  # ideographic spaces only (stripped to empty)
        "\u0301\u0301",  # combining marks only, nothing visible
    ],
)
def test_nickname_invisible_or_format_characters_rejected(manager: MatchManager, nickname: str) -> None:
    with pytest.raises(InvalidNicknameError):
        manager.create_match(nickname)


@pytest.mark.parametrize("nickname", ["alice", "Zoë", "山田", "player-1", "😀"])
def test_nickname_visible_unicode_accepted(manager: MatchManager, nickname: str) -> None:
    result = manager.create_match(nickname)
    assert manager.get_match(result.match_id).players[PLAYER_ONE].nickname == nickname
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/match/test_manager.py -k "invisible or visible_unicode"`
Expected: the `\u200b`, `\u2060`, `\ufeff` and combining-mark cases FAIL (currently accepted); the acceptance cases PASS.

- [ ] **Step 3: Implement**

Replace `_validate_nickname` in `backend/app/match/manager.py`:

```python
#: Bidirectional-text override/isolate controls: they can visually disguise
#: a nickname in other players' UIs and in replay artifacts.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

#: Unicode general categories rejected outright: controls, surrogates,
#: private use, unassigned, and ``Cf`` format characters (zero-width space,
#: word joiner, BOM, ...) which render as nothing and let one nickname
#: impersonate another. ``Cf`` also covers U+200D ZWJ, so multi-person
#: emoji sequences are rejected; single emoji are fine.
_REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

#: At least one character from these major classes must be present so a
#: nickname is never visually empty: Letter, Number, Punctuation, Symbol.
_VISIBLE_MAJOR_CLASSES = frozenset("LNPS")


def _validate_nickname(nickname: str) -> str:
    nickname = nickname.strip()
    if not nickname:
        raise InvalidNicknameError("nickname must be a non-empty string")
    categories = [unicodedata.category(ch) for ch in nickname]
    if any(cat in _REJECTED_CATEGORIES for cat in categories) or any(
        ch in _BIDI_CONTROLS for ch in nickname
    ):
        raise InvalidNicknameError("nickname must not contain control or invisible characters")
    if not any(cat[0] in _VISIBLE_MAJOR_CLASSES for cat in categories):
        raise InvalidNicknameError("nickname must contain at least one visible character")
    return nickname
```

- [ ] **Step 4: Verify**

Run: `pytest -q backend/tests/match/test_manager.py backend/tests/transport/test_ws.py -k "nickname" && ruff check backend && (cd backend && mypy app)`
Expected: PASS.

- [ ] **Step 5: Record the open product question**

Insert in `_specs/open-questions.md` immediately before the line `## Resolution process`:

```markdown
## Nickname policy: duplicates — OPEN (security review 2026-09-30, NE-04)

Invisible/format characters are rejected since 2026-10 (backend `_validate_nickname`). Still undecided: whether a guest may use the exact nickname of the player already in the lobby (allows lobby-UI impersonation; no gameplay effect). Options: reject with `invalid_nickname`; accept and disambiguate in the UI by seat (`p1`/`p2`). Owner decision needed before either is implemented.
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/match/manager.py backend/tests/match/test_manager.py _specs/open-questions.md
git commit -m "fix(backend): reject invisible format characters in nicknames (NE-04)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: NE-06 — close the superseded socket on session takeover

**Files:**
- Modify: `backend/app/transport/connections.py:49-57`
- Modify: `backend/app/transport/ws.py` (close-code constant; one `attach` helper used at the four `register` call sites: lines 288, 337, 376, 467)
- Test: `backend/tests/transport/test_ws.py`

**Interfaces:**
- Produces: `ConnectionRegistry.register(match_id, player_id, websocket) -> WebSocket | None` returns the socket it replaced (or `None` if none, or if it was the same socket). `ws.py` closes a replaced socket with close code `4000`. Task 5 adds one line to the `attach` helper defined here.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/transport/test_ws.py` (after `test_reconnect_returns_resync_snapshot_and_rebinds_connection`):

```python
def test_second_socket_with_same_session_closes_the_first(client: TestClient) -> None:
    """NE-06: a token holder gets exactly one live socket; the superseded one is closed 4000."""
    with client.websocket_connect("/ws") as first_ws:
        created = _create(first_ws)
        with client.websocket_connect("/ws") as second_ws:
            second_ws.send_text(
                json.dumps(
                    {
                        "protocolVersion": 1,
                        "type": "reconnect",
                        "matchId": created["matchId"],
                        "playerId": "p1",
                        "sessionToken": created["sessionToken"],
                    }
                )
            )
            assert second_ws.receive_json()["type"] == "resync"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                first_ws.receive_json()
            assert exc_info.value.code == 4000
            # The newer socket is still the live one: a ready toggle on it is served.
            _ready(
                second_ws,
                match_id=created["matchId"],
                player_id="p1",
                session_token=created["sessionToken"],
                ready=False,
            )
            assert second_ws.receive_json()["type"] == "ready_state"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/transport/test_ws.py -k closes_the_first`
Expected: FAIL (`first_ws.receive_json()` blocks/times out or returns nothing; no 4000 close).

- [ ] **Step 3: Implement**

`backend/app/transport/connections.py`, replace `register`:

```python
    def register(self, match_id: str, player_id: str, websocket: WebSocket) -> WebSocket | None:
        """Associate ``websocket`` with ``(match_id, player_id)``.

        Returns the socket this registration replaced (a reconnect taking
        over a slot), or ``None`` if the slot was empty or already held
        this same socket. The caller owns closing the replaced socket; this
        registry never performs I/O.
        """
        players = self._by_match.setdefault(match_id, {})
        previous = players.get(player_id)
        players[player_id] = websocket
        return previous if previous is not websocket else None
```

`backend/app/transport/ws.py`:

1. Add after `_INTERNAL_ERROR_CLOSE_CODE = 1011`:

```python
#: Close code sent to a socket superseded by a newer connection for the same
#: session (RFC 6455 reserves 4000-4999 for applications). The holder of a
#: token gets exactly one live socket, so a leaked token cannot be used in
#: parallel with its owner unnoticed.
_REPLACED_CLOSE_CODE = 4000
```

2. Inside `websocket_endpoint`, after `_reject_and_close` is defined, add:

```python
        async def attach(match_id: str, player_id: str) -> None:
            """Make this socket the live one for ``(match_id, player_id)``; close any predecessor."""
            replaced = connection_registry.register(match_id, player_id, websocket)
            if replaced is not None:
                await _close(replaced, _REPLACED_CLOSE_CODE)
```

3. Replace the four `connection_registry.register(...)` calls:
   - line 288: `connection_registry.register(bound.match_id, bound.player_id, websocket)` → `await attach(bound.match_id, bound.player_id)`
   - line 337: same substitution.
   - line 376: same substitution.
   - line 467: `connection_registry.register(match.match_id, engine_player_id.value, websocket)` → `await attach(match.match_id, engine_player_id.value)`

- [ ] **Step 4: Verify, including the stale-teardown and dead-predecessor cases**

Run: `pytest -q backend/tests/transport/test_ws.py backend/tests/transport/test_milestone_integration.py && ruff check backend && (cd backend && mypy app)`
Expected: PASS. Watch two existing tests specifically:
- `test_stale_connections_teardown_after_reconnect_does_not_fire_spurious_disconnect`: the stale socket is now closed by the server; its `with` block exit must still not raise and `mark_disconnected` call count must be unchanged. If `WebSocketTestSession.__exit__` raises on the already-closed stale socket, wrap that inner block's exit in the test with `contextlib.suppress(WebSocketDisconnect)` and keep the assertions.
- `test_reconnect_returns_resync_snapshot_and_rebinds_connection` (Review Focus 5): the first socket is already gone when the second registers; `_close`'s `application_state` guard makes the close a no-op.

- [ ] **Step 5: Commit**

```bash
git add backend/app/transport/connections.py backend/app/transport/ws.py backend/tests/transport/test_ws.py
git commit -m "fix(backend): close the superseded socket when a session reconnects (NE-06)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: NE-01 — release lobby capacity when the lobby is abandoned

**Files:**
- Modify: `backend/app/match/models.py` (`Match`: add `abandoned_at`)
- Modify: `backend/app/match/manager.py` (`__init__`, `sweep`, two new methods)
- Modify: `backend/app/config.py` (`DEFAULT_ABANDONED_LOBBY_GRACE_S`, `Settings.abandoned_lobby_grace_s`, env `NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS`)
- Modify: `backend/app/main.py` (pass the setting to `MatchManager`)
- Modify: `backend/app/transport/ws.py` (`teardown_connection`, `attach`)
- Modify: `deploy/docker-compose.yml`, `deploy/.env.example`, `docs/operations/runbook.md` (§2 table, §12 troubleshooting)
- Test: `backend/tests/match/test_sweep.py`, `backend/tests/test_config.py`

**Interfaces:**
- Consumes: `attach()` helper from Task 4.
- Produces: `MatchManager.mark_lobby_abandoned(match_id: str) -> None`, `MatchManager.mark_lobby_occupied(match_id: str) -> None`, constructor kwarg `abandoned_lobby_grace_s: float | None = None`, `Settings.abandoned_lobby_grace_s: int` (default 30).

- [ ] **Step 1: Write the failing manager tests**

Append to `backend/tests/match/test_sweep.py`:

```python
def test_abandoned_lobby_is_disposed_after_grace() -> None:
    """NE-01: capacity held by a lobby nobody is connected to is released after a short grace."""
    clock = _Clock()
    manager = MatchManager(waiting_timeout_s=900, abandoned_lobby_grace_s=30, clock=clock)
    created = manager.create_match("alice")
    manager.mark_lobby_abandoned(created.match_id)
    clock.now += 29
    assert manager.sweep() == []
    clock.now += 1
    assert [m.match_id for m in manager.sweep()] == [created.match_id]
    assert len(manager) == 0


def test_reoccupied_lobby_is_not_disposed() -> None:
    """A creator who refreshes the page inside the grace keeps the lobby and its join code."""
    clock = _Clock()
    manager = MatchManager(waiting_timeout_s=900, abandoned_lobby_grace_s=30, clock=clock)
    created = manager.create_match("alice")
    manager.mark_lobby_abandoned(created.match_id)
    clock.now += 10
    manager.mark_lobby_occupied(created.match_id)
    clock.now += 100
    assert manager.sweep() == []
    manager.get_match_by_join_code(created.join_code)  # still joinable


def test_abandon_mark_is_ignored_for_a_started_match() -> None:
    clock = _Clock()
    manager = MatchManager(abandoned_lobby_grace_s=1, clock=clock)
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")
    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)
    manager.mark_lobby_abandoned(created.match_id)  # ACTIVE: reconnect policy owns this, not sweep
    clock.now += 10_000
    assert manager.sweep() == []


def test_abandon_and_occupy_unknown_match_are_no_ops() -> None:
    manager = MatchManager(abandoned_lobby_grace_s=1)
    manager.mark_lobby_abandoned("missing")
    manager.mark_lobby_occupied("missing")


def test_capacity_is_released_when_lobby_creator_disconnects(tmp_path: Path) -> None:
    """The review's regression test: create at capacity, drop the socket, sweep, create again."""
    app = create_app(settings=Settings(replay_dir=tmp_path, max_matches=1, abandoned_lobby_grace_s=1))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert _create(ws)["type"] == "created"
        with client.websocket_connect("/ws") as ws:
            assert _create(ws)["error"]["code"] == "server_busy"  # still inside the grace window
        time.sleep(1.05)
        client.portal.call(app.state.sweep_expired_matches)  # type: ignore[union-attr]
        with client.websocket_connect("/ws") as ws:
            assert _create(ws)["type"] == "created"
```

And to `backend/tests/test_config.py`:

```python
def test_abandoned_lobby_grace_defaults_and_parses() -> None:
    assert load_settings({}).abandoned_lobby_grace_s == 30
    assert load_settings({"NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS": "5"}).abandoned_lobby_grace_s == 5
    with pytest.raises(ConfigError, match="NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS"):
        load_settings({"NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS": "0"})
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/match/test_sweep.py backend/tests/test_config.py`
Expected: new tests FAIL with `TypeError: unexpected keyword argument 'abandoned_lobby_grace_s'` / `AttributeError`.

- [ ] **Step 3: Model and manager**

`backend/app/match/models.py`, in `Match` after `finished_at: float | None = None`:

```python
    #: ``MatchManager``'s clock when the last socket left a WAITING lobby;
    #: ``None`` while someone is attached. Sweep disposes a lobby abandoned
    #: for longer than the configured grace (security review NE-01).
    abandoned_at: float | None = None
```

`backend/app/match/manager.py`:

1. `__init__` signature: add `abandoned_lobby_grace_s: float | None = None,` after `waiting_timeout_s`. Body: after `self._waiting_timeout_s = waiting_timeout_s` add `self._abandoned_lobby_grace_s = abandoned_lobby_grace_s`.

2. In `sweep`, extend the list comprehension condition with a third alternative:

```python
                or (
                    match.state is MatchRuntimeState.WAITING
                    and self._abandoned_lobby_grace_s is not None
                    and match.abandoned_at is not None
                    and now - match.abandoned_at >= self._abandoned_lobby_grace_s
                )
```

and change the docstring's first line to: `"""Dispose finished matches past retention, lobbies past the waiting timeout, and lobbies abandoned past the grace.`

3. After `mark_reconnected`, add:

```python
    def mark_lobby_abandoned(self, match_id: str) -> None:
        """Record that no socket is attached to ``match_id`` while it is still WAITING.

        The transport calls this when the last registered socket for the
        match goes away. Only a WAITING lobby is affected: an ACTIVE/PAUSED
        match is governed by the reconnect-grace policy instead. Unknown ids
        are ignored (the match may already be disposed).
        """
        with self._lock:
            match = self._matches.get(match_id)
            if (
                match is not None
                and match.state is MatchRuntimeState.WAITING
                and match.abandoned_at is None
            ):
                match.abandoned_at = self._clock()

    def mark_lobby_occupied(self, match_id: str) -> None:
        """Clear the abandonment mark: a socket attached to ``match_id`` again."""
        with self._lock:
            match = self._matches.get(match_id)
            if match is not None:
                match.abandoned_at = None
```

- [ ] **Step 4: Config and composition root**

`backend/app/config.py`:
- After `DEFAULT_WAITING_TIMEOUT_S = 900` add `DEFAULT_ABANDONED_LOBBY_GRACE_S = 30`.
- In `Settings` after `waiting_timeout_s`: 

```python
    #: Seconds a lobby survives with no socket attached (page refresh grace)
    #: before its capacity is released.
    abandoned_lobby_grace_s: int = DEFAULT_ABANDONED_LOBBY_GRACE_S
```
- In `load_settings` return, after `waiting_timeout_s=...`:

```python
        abandoned_lobby_grace_s=_positive_int(
            env, "NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS", DEFAULT_ABANDONED_LOBBY_GRACE_S
        ),
```

`backend/app/main.py`, `MatchManager(...)` call: add `abandoned_lobby_grace_s=settings.abandoned_lobby_grace_s,` after `waiting_timeout_s=settings.waiting_timeout_s,`.

- [ ] **Step 5: Transport**

`backend/app/transport/ws.py`:

1. In `teardown_connection`, replace

```python
            removed = connection_registry.unregister(bound.match_id, bound.player_id, websocket)
            if removed:
                await notify_disconnect_once()
```
with
```python
            removed = connection_registry.unregister(bound.match_id, bound.player_id, websocket)
            if removed:
                await notify_disconnect_once()
                if not connection_registry.connections_for(bound.match_id):
                    # Nobody is attached any more. A no-op unless the match
                    # is still WAITING (NE-01: abandoned lobbies must not
                    # hold capacity for the whole waiting timeout).
                    match_manager.mark_lobby_abandoned(bound.match_id)
```

2. In `attach` (Task 4), after the `register` line add `match_manager.mark_lobby_occupied(match_id)`.

- [ ] **Step 6: Run the tests**

Run: `pytest -q backend/tests/match/test_sweep.py backend/tests/test_config.py backend/tests/transport/test_ws.py && ruff check backend && (cd backend && mypy app)`
Expected: PASS.

- [ ] **Step 7: Deployment wiring and docs**

`deploy/docker-compose.yml`, backend `environment:` after the waiting timeout line:
```yaml
      NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS: ${NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS:-30}
```

`deploy/.env.example`, after the waiting-timeout line:
```
# NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS=30      # lobby with no socket attached is dropped after this
```

`docs/operations/runbook.md` §2 configuration table, add a row after `NETHER_EARTH_WAITING_MATCH_TIMEOUT_SECONDS`:
```
| `NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS` | no (30) | How long a lobby with no connected player keeps its capacity (covers a page refresh). |
```

`docs/operations/runbook.md` §12 Troubleshooting, append:
```markdown
- **`server_busy` for everyone:** capacity is `NETHER_EARTH_MAX_MATCHES` across every state. One IP
  can hold at most 32 sockets (gateway `limit_conn`) and therefore at most 32 lobbies, plus whatever
  it created inside the abandonment grace (30 handshakes/min × 30 s ≈ 15). Filling 200 needs several
  addresses. Lower `NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS` or the gateway `limit_conn` if abused.
```

- [ ] **Step 8: Validate compose config**

Run: `make compose-check`
Expected: exit 0.

- [ ] **Step 9: Commit**

```bash
git add backend/app/match/models.py backend/app/match/manager.py backend/app/config.py backend/app/main.py backend/app/transport/ws.py backend/tests/match/test_sweep.py backend/tests/test_config.py deploy/docker-compose.yml deploy/.env.example docs/operations/runbook.md
git commit -m "fix(backend): release lobby capacity after the last socket leaves (NE-01)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: NE-13 — deadline for sockets that never bind a session

**Files:**
- Modify: `backend/app/transport/ws.py` (constant; receive loop)
- Test: `backend/tests/transport/test_hardening.py`

**Interfaces:**
- Produces: module constant `UNBOUND_SOCKET_TIMEOUT_S = 30.0` in `app.transport.ws`; error code `bind_timeout`, close 1008.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/transport/test_hardening.py` (add `from app.transport import ws as ws_module` and `import time` to the imports):

```python
def test_unbound_socket_is_closed_at_the_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NE-13: an accepted socket that never creates/joins/reconnects is dropped."""
    monkeypatch.setattr(ws_module, "UNBOUND_SOCKET_TIMEOUT_S", 0.1)
    with client.websocket_connect("/ws") as ws:
        assert _error(ws)["error"]["code"] == "bind_timeout"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
    assert exc_info.value.code == 1008


def test_invalid_frames_do_not_extend_the_bind_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 1: the deadline is absolute, not reset per received frame."""
    monkeypatch.setattr(ws_module, "UNBOUND_SOCKET_TIMEOUT_S", 0.2)
    with client.websocket_connect("/ws") as ws:
        time.sleep(0.12)
        ws.send_text("not json")
        assert _error(ws)["error"]["code"] == "invalid_message"
        started = time.monotonic()
        assert _error(ws)["error"]["code"] == "bind_timeout"
        assert time.monotonic() - started < 0.15  # ~0.08 s left, not a fresh 0.2 s
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_bound_socket_is_not_subject_to_the_bind_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws_module, "UNBOUND_SOCKET_TIMEOUT_S", 0.1)
    with client.websocket_connect("/ws", headers={"origin": _ORIGIN}) as ws:
        assert _create(ws)["type"] == "created"
        time.sleep(0.15)
        ws.send_text("not json")
        assert _error(ws)["error"]["code"] == "invalid_message"  # still open and serving
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/transport/test_hardening.py -k bind`
Expected: first two FAIL (`AttributeError: UNBOUND_SOCKET_TIMEOUT_S` or hang → the test client raises on exit), third PASS.

- [ ] **Step 3: Implement**

`backend/app/transport/ws.py`:

1. Add `import asyncio` at the top of the imports.
2. After `_REPLACED_CLOSE_CODE = 4000` add:

```python
#: Seconds an accepted socket may stay without a bound session (no
#: successful create/join/ready/leave/command/reconnect) before it is
#: closed. Bounds idle unauthenticated sockets independently of the
#: gateway's proxy_read_timeout (security review NE-13).
UNBOUND_SOCKET_TIMEOUT_S = 30.0
```

3. In `websocket_endpoint`, right after `await websocket.accept()` add:

```python
        bind_deadline = asyncio.get_running_loop().time() + UNBOUND_SOCKET_TIMEOUT_S
```

4. Replace the receive at the top of the `while True:` loop:

```python
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    return
```
with
```python
                try:
                    if bound is None:
                        remaining = bind_deadline - asyncio.get_running_loop().time()
                        raw = await asyncio.wait_for(websocket.receive_text(), max(remaining, 0.0))
                    else:
                        raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    return
                except TimeoutError:
                    logger.info(
                        "closing websocket: no session bound before the deadline",
                        extra={"event": "ws_bind_timeout"},
                    )
                    await _reject_and_close(
                        websocket, None, "bind_timeout", "no session bound in time; closing"
                    )
                    return
```

(Python 3.12: `asyncio.TimeoutError` is the builtin `TimeoutError`.)

- [ ] **Step 4: Verify**

Run: `pytest -q backend/tests/transport/ && ruff check backend && (cd backend && mypy app)`
Expected: PASS. If `test_milestone_integration.py` opens a socket and leaves it unbound for > 30 s anywhere, it will fail here; it does not today (all its sockets bind immediately), but confirm by the run.

- [ ] **Step 5: Document in the runbook**

`docs/operations/runbook.md` §12 Troubleshooting, append:
```markdown
- **`bind_timeout` errors in a client:** a socket must send `create`/`join`/`reconnect` within 30 s
  of connecting; the backend closes it otherwise (code 1008).
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/transport/ws.py backend/tests/transport/test_hardening.py docs/operations/runbook.md
git commit -m "fix(backend): close sockets that never bind a session within 30s (NE-13)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Cross-session command isolation regression test (§9 item 8)

**Files:**
- Test: `backend/tests/transport/test_ws.py`

**Interfaces:** none (test only; the behavior already exists at `ws.py:377-384`).

- [ ] **Step 1: Write the test**

Append to `backend/tests/transport/test_ws.py` (the file already imports `_start_active_match_keeping_sockets_open` and `_next_non_snapshot` from `_helpers`; if not, add them):

```python
def test_opponents_token_cannot_issue_commands_from_another_players_socket(
    client: TestClient,
) -> None:
    """§9.8: a socket bound as P2 presenting P1's valid token is a session mismatch, not P1."""
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        created, _joined, _snapshot = _start_active_match_keeping_sockets_open(ws_a, ws_b)
        ws_b.send_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "type": "command",
                    "matchId": created["matchId"],
                    "playerId": "p1",
                    "sessionToken": created["sessionToken"],
                    "clientSequence": 0,
                    "payload": {"kind": "commander_move", "dx": 1, "dy": 0},
                }
            )
        )
        message = _next_non_snapshot(ws_b, own_match_id=created["matchId"])
        assert message["type"] == "error"
        assert message["error"]["code"] == "session_mismatch"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            for _ in range(1000):
                ws_b.receive_json()
        assert exc_info.value.code == 1008
```

- [ ] **Step 2: Run**

Run: `pytest -q backend/tests/transport/test_ws.py -k opponents_token`
Expected: PASS on the first run (regression pin). If it fails, the mismatch branch is broken; stop and report.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/transport/test_ws.py
git commit -m "test(backend): pin cross-session command isolation over a live match

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: NE-02 — replay writes off the event loop, in order, with a bounded backlog

**Files:**
- Modify: `backend/app/replay/writer.py` (`ReplayWriter.__init__`, `_report_failure`, `start_match`, `record_tick`, `record_lifecycle_event`, `finish_match`; new `_run`, `drain`, `close`; module constant `MAX_PENDING_WRITES`)
- Modify: `backend/app/main.py` (executor wiring; `app.state.replay_writer`; close at shutdown)
- Modify: `backend/tests/transport/_helpers.py` (`_replay_writer(client)` helper)
- Modify: `backend/tests/transport/test_ws.py:322` region and `backend/tests/transport/test_milestone_integration.py:525,748,751` (drain before reading files)
- Test: `backend/tests/replay/test_replay_log.py`

**Interfaces:**
- Produces: `ReplayWriter(base_dir=None, *, executor: concurrent.futures.Executor | None = None)`. `executor=None` keeps today's synchronous inline behavior (unit tests, scripts). With an executor, every public write method enqueues and returns immediately; a single-worker executor preserves global FIFO order. `ReplayWriter.drain() -> None` blocks the calling thread until the queue is empty (never call it on the event loop; tests call it from the TestClient thread or via `asyncio.to_thread`). `ReplayWriter.close() -> None` drains and shuts the executor down. `app.state.replay_writer` exposes the app's writer.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/replay/test_replay_log.py` (add `import time`, `import logging`, `from concurrent.futures import ThreadPoolExecutor`, and `from app.replay import writer as writer_module` to the imports):

```python
def _async_writer(tmp_path: Path) -> ReplayWriter:
    return ReplayWriter(
        base_dir=tmp_path, executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="replay-test")
    )


async def test_slow_disk_does_not_delay_ticks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NE-02: the tick path never waits for the filesystem."""
    original = ReplayWriter._append_jsonl

    def slow_append(self: ReplayWriter, match_id: str, filename: str, line: dict[str, Any]) -> None:
        time.sleep(0.05)
        original(self, match_id, filename, line)

    monkeypatch.setattr(ReplayWriter, "_append_jsonl", slow_append)
    match, scenario, map_data = _build_match("match-slow-disk")
    writer = _async_writer(tmp_path)
    writer.start_match(match, scenario, map_data)
    runtime = MatchRuntime(match, on_tick_commands=make_replay_tick_recorder(writer, match.match_id))

    started = time.monotonic()
    for _ in range(20):
        await runtime._advance_one_tick()
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, f"20 ticks took {elapsed:.2f}s: writes are blocking the loop"

    await asyncio.to_thread(writer.drain)
    lines = (match_dir(tmp_path, match.match_id) / "commands.jsonl").read_text().splitlines()
    assert [json.loads(line)["tick"] for line in lines] == list(range(1, 21))
    writer.close()


async def test_queued_writes_land_in_order_and_finalize(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-queued")
    writer = _async_writer(tmp_path)
    writer.start_match(match, scenario, map_data)
    await _play_three_ticks(match, writer)
    writer.finish_match(match)
    await asyncio.to_thread(writer.drain)

    meta = load_meta(tmp_path, match.match_id)
    assert meta["status"] == "finished"
    assert meta["final_tick"] == 3
    assert [t for t in load_commands_by_tick(tmp_path, match.match_id)] == [1, 2, 3]
    writer.close()


async def test_backlog_beyond_bound_is_dropped_and_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 3: a stalled disk must not grow memory without bound or stop the match."""
    monkeypatch.setattr(writer_module, "MAX_PENDING_WRITES", 3)
    gate = threading.Event()
    original = ReplayWriter._append_jsonl

    def blocked_append(self: ReplayWriter, match_id: str, filename: str, line: dict[str, Any]) -> None:
        gate.wait(timeout=5)
        original(self, match_id, filename, line)

    monkeypatch.setattr(ReplayWriter, "_append_jsonl", blocked_append)
    match, scenario, map_data = _build_match("match-backlog")
    writer = _async_writer(tmp_path)
    writer.start_match(match, scenario, map_data)
    await asyncio.to_thread(writer.drain)  # header written before the gate matters

    with caplog.at_level(logging.DEBUG, logger="app.replay.writer"):
        for tick in range(1, 11):
            writer.record_tick(match.match_id, tick, (), ())
    errors = [r for r in caplog.records if r.getMessage().startswith("replay artifact write failed")]
    assert len(errors) >= 1
    assert sum(1 for r in errors if r.levelno == logging.ERROR) == 1  # first failure is ERROR, repeats DEBUG

    gate.set()
    await asyncio.to_thread(writer.drain)
    lines = (match_dir(tmp_path, match.match_id) / "commands.jsonl").read_text().splitlines()
    assert 0 < len(lines) < 10  # some landed, the overflow was dropped
    writer.close()


def test_inline_writer_without_executor_is_synchronous(tmp_path: Path) -> None:
    match, scenario, map_data = _build_match("match-inline")
    writer = ReplayWriter(base_dir=tmp_path)
    writer.start_match(match, scenario, map_data)
    writer.record_tick(match.match_id, 1, (), ())
    assert (match_dir(tmp_path, match.match_id) / "commands.jsonl").read_text().count("\n") == 1
    writer.drain()  # no-op without an executor
    writer.close()
```

Add `import threading` to the imports as well, and make sure `import asyncio`, `import json`, `from typing import Any`, `MatchRuntime`, `load_meta`, `load_commands_by_tick` and `match_dir` are imported (most already are; check the file head).

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/replay/test_replay_log.py -k "slow_disk or queued_writes or backlog or inline_writer"`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'executor'`.

- [ ] **Step 3: Implement the writer**

`backend/app/replay/writer.py`:

1. Imports: add `import threading` and `from collections.abc import Callable` and `from concurrent.futures import Executor`.

2. After `_LIFECYCLE_FILENAME = "lifecycle.jsonl"` add:

```python
#: Upper bound on writes queued to the executor. Beyond it a write is dropped
#: and reported (``replay_write_failed``, action ``backlog``) rather than
#: letting a stalled disk grow memory without limit (security review NE-02).
MAX_PENDING_WRITES = 10_000
```

3. Replace `__init__` and `_report_failure`, and add `_run`, `drain`, `close`:

```python
    def __init__(self, base_dir: Path | None = None, *, executor: Executor | None = None) -> None:
        self._base_dir = base_dir if base_dir is not None else default_replay_dir()
        self._failed_match_ids: set[str] = set()
        # ``executor`` (NE-02): when set, every write runs there and the
        # caller never waits for the filesystem. It must be single-worker so
        # lines land in submission order across all matches. ``None`` runs
        # writes inline (unit tests, scripts).
        self._executor = executor
        self._pending = 0
        self._pending_lock = threading.Lock()

    def _report_failure(self, match_id: str, action: str, *, exc: bool = True) -> None:
        """Log a replay I/O failure without interrupting the match.

        The first failure per match is an ERROR with traceback (``/ready``
        also turns 503 while the directory is unwritable); repeats for the
        same match (every tick) are DEBUG.
        """
        first = match_id not in self._failed_match_ids
        self._failed_match_ids.add(match_id)
        logger.log(
            logging.ERROR if first else logging.DEBUG,
            "replay artifact write failed; the match continues without it",
            exc_info=first and exc,
            extra={"event": "replay_write_failed", "match_id": match_id, "action": action},
        )

    def _run(self, match_id: str, action: str, fn: Callable[[], None]) -> None:
        """Run ``fn`` inline, or queue it on the executor; report (never raise) on failure."""

        def job() -> None:
            try:
                fn()
            except (OSError, ValueError):
                self._report_failure(match_id, action)

        if self._executor is None:
            job()
            return
        with self._pending_lock:
            if self._pending >= MAX_PENDING_WRITES:
                overflow = True
            else:
                overflow = False
                self._pending += 1
        if overflow:
            self._report_failure(match_id, "backlog", exc=False)
            return
        try:
            future = self._executor.submit(job)
        except RuntimeError:  # executor already shut down (process stopping)
            with self._pending_lock:
                self._pending -= 1
            self._report_failure(match_id, action, exc=False)
            return
        future.add_done_callback(self._on_done)

    def _on_done(self, _future: object) -> None:
        with self._pending_lock:
            self._pending -= 1

    def drain(self) -> None:
        """Block until every queued write has run. Never call this on the event loop."""
        if self._executor is not None:
            self._executor.submit(lambda: None).result()

    def close(self) -> None:
        """Drain and stop the executor; later writes are reported as failures."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
```

4. Replace `start_match`, `record_tick`, `record_lifecycle_event`, `finish_match` bodies so that they build the line/validate on the caller's thread and run the I/O through `_run`:

```python
    def start_match(self, match: Match, scenario: Scenario, map_data: BootstrapMap) -> None:
        """Create ``match``'s artifact directory and write its ``in_progress`` header.

        Called exactly once per match by ``MatchManager._start_match_locked``,
        before the runtime starts, so this never clobbers a live stream.
        """

        def job() -> None:
            self._start_match(match, scenario, map_data)
            logger.info(
                "replay artifact started",
                extra={"event": "replay_started", "match_id": match.match_id},
            )

        self._run(match.match_id, "start", job)

    def record_tick(
        self,
        match_id: str,
        tick: int,
        commands: tuple[Command, ...],
        events: tuple[Event, ...],
    ) -> None:
        """Append ``tick``'s accepted command batch (plus a debug event summary) to the gameplay stream."""
        line = {
            "tick": tick,
            "commands": [_command_to_json(command) for command in commands],
            "events": [_event_summary(event) for event in events],
        }
        self._run(match_id, "record_tick", lambda: self._append_jsonl(match_id, _COMMANDS_FILENAME, line))

    def record_lifecycle_event(self, match_id: str, payload: dict[str, Any]) -> None:
        """Append one disconnect/reconnect/pause/forfeit/no-contest event to the lifecycle stream.

        ``payload`` must already be JSON-safe and must never carry a session
        token. ``wall_clock_epoch_ms`` is stamped here, not by the caller.
        """
        line = {"wall_clock_epoch_ms": _epoch_ms(), **payload}
        self._run(
            match_id, "record_lifecycle_event", lambda: self._append_jsonl(match_id, _LIFECYCLE_FILENAME, line)
        )

    def finish_match(self, match: Match) -> None:
        """Atomically flip ``match``'s artifact to ``status: "finished"`` with its final result.

        Idempotent: no artifact, or one already ``"finished"``, is a silent
        no-op (``MatchManager.finish_match`` may be called more than once).
        The final snapshot is captured on the caller's thread so the
        persisted state is the match state at finish time.
        """
        state = match.game_state
        final_tick = state.tick if state is not None else None
        final_snapshot = to_snapshot(state) if state is not None else None
        result = _result_to_json(match)
        match_id = match.match_id

        def job() -> None:
            try:
                finalized = self._finish_match(match_id, final_tick, result, final_snapshot)
            finally:
                self._failed_match_ids.discard(match_id)
            if finalized:
                logger.info(
                    "replay artifact finalized",
                    extra={"event": "replay_finalized", "match_id": match_id},
                )

        self._run(match_id, "finish", job)

    def _finish_match(
        self,
        match_id: str,
        final_tick: int | None,
        result: dict[str, Any] | None,
        final_snapshot: dict[str, Any] | None,
    ) -> bool:
        meta = self._read_meta(match_id)
        if meta is None or meta.get("status") == "finished":
            return False
        meta["status"] = "finished"
        meta["finished_at_epoch_ms"] = _epoch_ms()
        meta["final_tick"] = final_tick
        meta["result"] = result
        meta["final_snapshot"] = final_snapshot
        self._write_meta_atomic(match_id, meta)
        return True
```

Note `_command_to_json` still raises `NotImplementedError` on the caller's thread for an unknown `Command` subclass (the existing `test_command_serialization_rejects_a_concrete_command_subclass` depends on that; it must keep passing). `to_snapshot` returns `dict[str, Any]`; check its annotation in `nether_earth/snapshot.py` and match it in `_finish_match`'s signature.

5. Update the module docstring paragraph starting "No secrets, no database: every write here is a plain local file open/write call…" to:

```
No secrets, no database: every write here is a plain local file call. In
production ``app.main`` hands this writer a single-worker executor, so the
tick loop and lifecycle watchers only enqueue; one worker thread runs every
write in submission order, which is what keeps a match's lines from
interleaving and keeps a slow disk from stalling any match's tick cadence.
Without an executor the same methods run inline (unit tests, scripts).
```

- [ ] **Step 4: Wire the executor in the app**

`backend/app/main.py`:
- Import: `from concurrent.futures import ThreadPoolExecutor`.
- Replace `replay_writer = ReplayWriter(base_dir=replay_dir)` with:

```python
    # One worker thread (NE-02): replay I/O never runs on the event loop,
    # and a single worker keeps every match's lines in submission order.
    replay_writer = ReplayWriter(
        base_dir=replay_dir,
        executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="replay-writer"),
    )
```
- In `lifespan`, after `sweeper.cancel()` and before the "backend stopping" log: `await asyncio.to_thread(replay_writer.close)`.
- After `fastapi_app.state.connection_registry = connection_registry` add `fastapi_app.state.replay_writer = replay_writer`.

- [ ] **Step 5: Make app-level tests drain before reading artifacts**

`backend/tests/transport/_helpers.py`, after `_match_manager`:

```python
def _replay_writer(client: TestClient) -> ReplayWriter:
    writer = client.app.state.replay_writer
    assert isinstance(writer, ReplayWriter)
    return writer


def _drain_replays(client: TestClient) -> None:
    """Wait for the app's queued replay writes (NE-02) before reading artifact files."""
    _replay_writer(client).drain()
```
(add `from app.replay import ReplayWriter` to its imports).

Then insert `_drain_replays(client)` immediately before each artifact read:
- `backend/tests/transport/test_ws.py` ~line 322 (`meta_path = tmp_path / "replays" / ... / "meta.json"`): add the call on the line before. Import `_drain_replays` from `_helpers`.
- `backend/tests/transport/test_milestone_integration.py` ~line 525 (`commands.jsonl` read inside the scenario), ~line 748 (`verify_replay(replay_dir, ...)`), ~line 751 (`meta.json` read): add `_drain_replays(client)` before each (the test's `TestClient` variable name may differ; use it). Run `grep -n "commands.jsonl\|meta.json\|verify_replay(" backend/tests/transport/test_milestone_integration.py` to find every site.

`tests/match/test_solo.py` and `tests/acceptance/m9_script.py` construct `ReplayWriter` directly (inline mode) and need no change.

- [ ] **Step 6: Verify**

Run: `pytest -q backend/tests/replay backend/tests/transport backend/tests/match backend/tests/acceptance && ruff check backend && (cd backend && mypy app)`
Expected: PASS. The M9 acceptance replay hashes must be unchanged (the test asserts them).

- [ ] **Step 7: Commit**

```bash
git add backend/app/replay/writer.py backend/app/main.py backend/tests/replay/test_replay_log.py backend/tests/transport/_helpers.py backend/tests/transport/test_ws.py backend/tests/transport/test_milestone_integration.py
git commit -m "perf(backend): move replay writes to a single worker thread off the event loop (NE-02)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: NE-03 — replay retention knob and startup marking of orphaned artifacts

**Files:**
- Create: `backend/app/replay/retention.py`
- Modify: `backend/app/replay/writer.py` (extract module-level `write_meta_atomic(directory, meta)`; `_write_meta_atomic` calls it)
- Modify: `backend/app/replay/__init__.py` (export `mark_interrupted`, `prune_replays`)
- Modify: `backend/app/replay/verify.py:261-264` (error message)
- Modify: `backend/app/config.py` (`replay_retention_days: int | None`, env `NETHER_EARTH_REPLAY_RETENTION_DAYS`)
- Modify: `backend/app/main.py` (lifespan: mark on startup; prune task)
- Modify: `deploy/docker-compose.yml`, `deploy/.env.example`, `docs/operations/runbook.md` §2 and §8, `_specs/open-questions.md`
- Test: `backend/tests/replay/test_retention.py` (new), `backend/tests/test_config.py`

**Interfaces:**
- Produces: `mark_interrupted(base_dir: Path) -> list[str]` (ids flipped from `in_progress` to `interrupted`), `prune_replays(base_dir: Path, *, max_age_s: float, now: float | None = None) -> list[str]` (ids deleted), `Settings.replay_retention_days: int | None` (None = keep forever), `REPLAY_PRUNE_INTERVAL_S = 3600.0` in `app.main`. New `meta.json` status value `"interrupted"` (artifact shape unchanged; `ARTIFACT_SCHEMA_VERSION` stays 1).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/replay/test_retention.py`:

```python
"""Replay directory retention and orphan marking (security review NE-03)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from app.replay import mark_interrupted, prune_replays

import pytest


def _artifact(base: Path, match_id: str, status: str, *, age_s: float = 0.0) -> Path:
    directory = base / match_id
    directory.mkdir(parents=True)
    meta = directory / "meta.json"
    meta.write_text(json.dumps({"match_id": match_id, "status": status}), encoding="utf-8")
    (directory / "commands.jsonl").write_text("", encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(meta, (stamp, stamp))
    return directory


def test_mark_interrupted_flips_only_in_progress(tmp_path: Path) -> None:
    _artifact(tmp_path, "a", "in_progress")
    _artifact(tmp_path, "b", "finished")
    assert mark_interrupted(tmp_path) == ["a"]
    assert json.loads((tmp_path / "a" / "meta.json").read_text())["status"] == "interrupted"
    assert json.loads((tmp_path / "b" / "meta.json").read_text())["status"] == "finished"
    assert mark_interrupted(tmp_path) == []  # idempotent


def test_mark_interrupted_skips_unreadable_meta(tmp_path: Path) -> None:
    """Review Focus 4: a corrupt or foreign file must not stop startup."""
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "meta.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "stray-file").write_text("x", encoding="utf-8")
    _artifact(tmp_path, "ok", "in_progress")
    assert mark_interrupted(tmp_path) == ["ok"]


def test_mark_interrupted_on_missing_dir_is_a_no_op(tmp_path: Path) -> None:
    assert mark_interrupted(tmp_path / "nope") == []


def test_prune_deletes_old_finished_and_interrupted_only(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-finished", "finished", age_s=100)
    _artifact(tmp_path, "old-interrupted", "interrupted", age_s=100)
    _artifact(tmp_path, "old-live", "in_progress", age_s=100)
    _artifact(tmp_path, "new-finished", "finished", age_s=1)
    deleted = prune_replays(tmp_path, max_age_s=50)
    assert sorted(deleted) == ["old-finished", "old-interrupted"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new-finished", "old-live"]


def test_prune_uses_injected_now(tmp_path: Path) -> None:
    _artifact(tmp_path, "m", "finished", age_s=0)
    assert prune_replays(tmp_path, max_age_s=10, now=time.time() + 11) == ["m"]


def test_startup_marks_orphans_interrupted(tmp_path: Path) -> None:
    _artifact(tmp_path, "orphan", "in_progress")
    with TestClient(create_app(settings=Settings(replay_dir=tmp_path))):
        pass
    assert json.loads((tmp_path / "orphan" / "meta.json").read_text())["status"] == "interrupted"


def test_retention_setting_is_optional() -> None:
    assert load_settings({}).replay_retention_days is None
    assert load_settings({"NETHER_EARTH_REPLAY_RETENTION_DAYS": "30"}).replay_retention_days == 30
    with pytest.raises(ConfigError, match="NETHER_EARTH_REPLAY_RETENTION_DAYS"):
        load_settings({"NETHER_EARTH_REPLAY_RETENTION_DAYS": "-1"})
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q backend/tests/replay/test_retention.py`
Expected: FAIL at import (`ImportError: cannot import name 'mark_interrupted'`).

- [ ] **Step 3: Extract the atomic meta write and add the retention module**

`backend/app/replay/writer.py`: add a module-level function after `match_dir`:

```python
def write_meta_atomic(directory: Path, meta: dict[str, Any]) -> None:
    """Write ``meta`` as ``meta.json`` in ``directory`` via temp file + ``os.replace``."""
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f"{_META_FILENAME}.tmp"
    tmp_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_path, directory / _META_FILENAME)  # atomic rename on every target filesystem.
```

and make the method delegate:

```python
    def _write_meta_atomic(self, match_id: str, meta: dict[str, Any]) -> None:
        write_meta_atomic(match_dir(self._base_dir, match_id), meta)
```

Add `"write_meta_atomic"` to `__all__`.

Create `backend/app/replay/retention.py`:

```python
"""Replay directory housekeeping: orphan marking at startup and age-based pruning.

Both functions do blocking filesystem work and are meant to be called off
the event loop (``asyncio.to_thread``). Neither touches a live match's
artifact: ``mark_interrupted`` runs only at startup, when no match can be
live, and ``prune_replays`` never deletes a ``status: "in_progress"``
artifact.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from app.replay.writer import write_meta_atomic

logger = logging.getLogger(__name__)

_META_FILENAME = "meta.json"


def _read_meta(directory: Path) -> dict[str, Any] | None:
    """Parsed ``meta.json`` for an artifact directory, or ``None`` if absent/unreadable."""
    path = directory / _META_FILENAME
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _artifact_dirs(base_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in base_dir.iterdir() if p.is_dir())
    except OSError:
        return []


def mark_interrupted(base_dir: Path) -> list[str]:
    """Flip every ``in_progress`` artifact under ``base_dir`` to ``interrupted``.

    Startup only: a process that is just starting has no live matches, so
    every ``in_progress`` artifact belongs to a match the previous process
    never finished. Returns the ids changed. Unreadable directories are
    skipped with a warning, never raised.
    """
    changed: list[str] = []
    for directory in _artifact_dirs(base_dir):
        meta = _read_meta(directory)
        if meta is None:
            logger.warning(
                "skipping replay directory with unreadable meta.json",
                extra={"event": "replay_meta_unreadable", "path": str(directory)},
            )
            continue
        if meta.get("status") != "in_progress":
            continue
        meta["status"] = "interrupted"
        try:
            write_meta_atomic(directory, meta)
        except OSError:
            logger.warning(
                "could not mark replay artifact interrupted",
                exc_info=True,
                extra={"event": "replay_mark_interrupted_failed", "path": str(directory)},
            )
            continue
        changed.append(directory.name)
    if changed:
        logger.info(
            "marked orphaned replay artifacts interrupted",
            extra={"event": "replay_orphans_marked", "count": len(changed)},
        )
    return changed


def prune_replays(base_dir: Path, *, max_age_s: float, now: float | None = None) -> list[str]:
    """Delete artifact directories finished/interrupted more than ``max_age_s`` ago.

    Age is ``meta.json``'s mtime, which the writer rewrites at finish, so
    it measures time since the match ended. ``in_progress`` artifacts are
    never deleted. Returns the ids removed.
    """
    current = time.time() if now is None else now
    removed: list[str] = []
    for directory in _artifact_dirs(base_dir):
        meta = _read_meta(directory)
        if meta is None or meta.get("status") == "in_progress":
            continue
        try:
            age = current - (directory / _META_FILENAME).stat().st_mtime
            if age < max_age_s:
                continue
            shutil.rmtree(directory)
        except OSError:
            logger.warning(
                "could not prune replay artifact",
                exc_info=True,
                extra={"event": "replay_prune_failed", "path": str(directory)},
            )
            continue
        removed.append(directory.name)
    if removed:
        logger.info(
            "pruned replay artifacts past retention",
            extra={"event": "replay_pruned", "count": len(removed)},
        )
    return removed
```

`backend/app/replay/__init__.py`: add `from app.replay.retention import mark_interrupted, prune_replays` and both names to `__all__` (open the file; follow its existing export style).

`backend/app/replay/verify.py` line ~263: change the message to `"(status is not 'finished' -- verify only a finished artifact)"`.

- [ ] **Step 4: Config and app wiring**

`backend/app/config.py`:
- `Settings`: after `abandoned_lobby_grace_s` add

```python
    #: Days a finished/interrupted replay artifact is kept before the
    #: backend deletes it; ``None`` keeps everything (owner decision pending,
    #: see _specs/open-questions.md).
    replay_retention_days: int | None = None
```
- Add helper after `_positive_int`:

```python
def _optional_positive_int(env: Mapping[str, str], name: str) -> int | None:
    return None if (env.get(name) or None) is None else _positive_int(env, name, 0)
```
- In `load_settings` return: `replay_retention_days=_optional_positive_int(env, "NETHER_EARTH_REPLAY_RETENTION_DAYS"),`.

`backend/app/main.py`:
- Import `from app.replay import ReplayWriter, make_replay_lifecycle_notifier, make_replay_tick_recorder, mark_interrupted, prune_replays`.
- After `SWEEP_INTERVAL_S = 15.0` add `REPLAY_PRUNE_INTERVAL_S = 3600.0` with comment `#: How often artifacts past NETHER_EARTH_REPLAY_RETENTION_DAYS are deleted.`
- Inside `create_app`, next to `sweep_forever`, add:

```python
    async def prune_replays_forever(max_age_s: float) -> None:
        while True:
            try:
                await asyncio.to_thread(prune_replays, replay_writer.base_dir, max_age_s=max_age_s)
            except Exception:
                logger.exception("replay prune failed", extra={"event": "replay_prune_failed"})
            await asyncio.sleep(REPLAY_PRUNE_INTERVAL_S)
```
- In `lifespan`, before `sweeper = asyncio.create_task(sweep_forever())`:

```python
        await asyncio.to_thread(mark_interrupted, replay_writer.base_dir)
        pruner: asyncio.Task[None] | None = None
        if settings.replay_retention_days is not None:
            pruner = asyncio.create_task(
                prune_replays_forever(settings.replay_retention_days * 86_400.0)
            )
```
and after `sweeper.cancel()`: `if pruner is not None: pruner.cancel()`.
- Add `"replay_retention_days": settings.replay_retention_days,` to the `process_started` log `extra`.

- [ ] **Step 5: Verify**

Run: `pytest -q backend/tests/replay backend/tests/test_config.py backend/tests/transport/test_ws.py && ruff check backend && (cd backend && mypy app)`
Expected: PASS.

- [ ] **Step 6: Deployment and docs**

`deploy/docker-compose.yml` backend `environment:` add:
```yaml
      NETHER_EARTH_REPLAY_RETENTION_DAYS: ${NETHER_EARTH_REPLAY_RETENTION_DAYS:-}
```
(empty means unset; `load_settings` treats `""` as absent.)

`deploy/.env.example` optional block add:
```
# NETHER_EARTH_REPLAY_RETENTION_DAYS=30   # delete finished/interrupted replays older than this; unset = keep forever
```

`docs/operations/runbook.md` §2 table add:
```
| `NETHER_EARTH_REPLAY_RETENTION_DAYS` | no (unset = keep forever) | Finished/interrupted replay artifacts older than this are deleted hourly by the backend. |
```

`docs/operations/runbook.md` §8: replace the `status: "in_progress"` bullet and the Retention bullet with:
```markdown
- `status` is `"in_progress"` while the match runs, `"finished"` when it ended normally, and
  `"interrupted"` if the backend stopped or crashed while it ran (set automatically at the next
  startup; the file is still a valid prefix of the command stream). `commands.jsonl` has no
  `fsync`; a host crash can lose its last lines.
- Retention: unset `NETHER_EARTH_REPLAY_RETENTION_DAYS` keeps everything (the disk fills over time
  and `/ready` turns 503 when it is full). Set it to delete finished/interrupted artifacts older
  than N days, checked hourly. A host cron is no longer required but still works.
```

`_specs/open-questions.md`, before `## Resolution process`:
```markdown
## Replay artifact retention — OPEN (security review 2026-09-30, NE-03)

The backend can delete finished/interrupted replays older than `NETHER_EARTH_REPLAY_RETENTION_DAYS`; the knob defaults to unset (keep forever). Undecided: whether production should prune by default and after how many days. Operational, not gameplay; owner decision.
```

- [ ] **Step 7: Validate compose and commit**

Run: `make compose-check`
Expected: exit 0.

```bash
git add backend/app/replay/retention.py backend/app/replay/writer.py backend/app/replay/__init__.py backend/app/replay/verify.py backend/app/config.py backend/app/main.py backend/tests/replay/test_retention.py deploy/docker-compose.yml deploy/.env.example docs/operations/runbook.md _specs/open-questions.md
git commit -m "feat(backend): replay retention knob and startup marking of interrupted artifacts (NE-03)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: NE-08 — Dependabot and a dependency audit workflow

**Files:**
- Create: `.github/dependabot.yml`
- Create: `.github/workflows/audit.yml`

**Interfaces:** none.

- [ ] **Step 1: Dependabot config**

Create `.github/dependabot.yml`:

```yaml
# Weekly dependency update PRs (security review NE-08). backend/requirements.lock
# is regenerated by `make lock`, not by Dependabot; Dependabot bumps the
# pyproject ranges and the lock is refreshed in the same PR by hand.
version: 2
updates:
  - package-ecosystem: pip
    directory: /backend
    schedule: { interval: weekly }
  - package-ecosystem: pip
    directory: /engine
    schedule: { interval: weekly }
  - package-ecosystem: npm
    directory: /frontend
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /backend
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /frontend
    schedule: { interval: weekly }
  - package-ecosystem: docker-compose
    directory: /deploy
    schedule: { interval: weekly }
  - package-ecosystem: github-actions
    directory: /
    schedule: { interval: weekly }
```

- [ ] **Step 2: Audit workflow**

Create `.github/workflows/audit.yml`:

```yaml
# Known-vulnerability scan of the pinned dependencies (security review NE-08).
name: Dependency audit

on:
  schedule:
    - cron: "17 6 * * 1"  # Mondays 06:17 UTC
  pull_request:
    paths:
      - backend/requirements.lock
      - backend/pyproject.toml
      - engine/pyproject.toml
      - frontend/package-lock.json
      - .github/workflows/audit.yml
  workflow_dispatch:

permissions:
  contents: read

jobs:
  pip-audit:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install pip-audit
      - run: pip-audit --strict -r backend/requirements.lock

  npm-audit:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend
      - run: npm audit --audit-level=high
        working-directory: frontend
```

(Task 11 pins these action references by SHA; write them as tags here, Task 11 rewrites every workflow at once.)

- [ ] **Step 3: Verify locally (CI is billing-blocked)**

```bash
python -c "import yaml,sys; [yaml.safe_load(open(p)) for p in ('.github/dependabot.yml','.github/workflows/audit.yml')]; print('yaml ok')"
python -m pip install pip-audit && pip-audit --strict -r backend/requirements.lock
(cd frontend && npm audit --audit-level=high)
```
Expected: `yaml ok`; both audits exit 0. If an audit reports a real vulnerability, do not silence it: note the advisory in the PR description and stop this task for the owner's decision (upgrading a pinned runtime dependency is outside this plan).

- [ ] **Step 4: Commit**

```bash
git add .github/dependabot.yml .github/workflows/audit.yml
git commit -m "ci: add Dependabot and a weekly pip/npm audit workflow (NE-08)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: NE-09 — pin actions by commit SHA and images by digest

**Files:**
- Modify: `.github/workflows/ci.yml`, `.github/workflows/audit.yml`
- Modify: `backend/Dockerfile:5,19`, `frontend/Dockerfile:6,19`, `deploy/docker-compose.yml` (gateway image), `Makefile:47` (lock image)

**Interfaces:** none.

- [ ] **Step 1: Resolve the SHAs (do not guess them)**

```bash
for repo_tag in actions/checkout:v4 actions/setup-python:v5 actions/setup-node:v4; do
  repo=${repo_tag%%:*}; tag=${repo_tag##*:}
  latest=$(git ls-remote --tags --refs "https://github.com/$repo" "refs/tags/$tag.*" | awk -F/ '{print $NF}' | sort -V | tail -1)
  sha=$(git ls-remote "https://github.com/$repo" "refs/tags/$latest^{}" | cut -f1)
  [ -n "$sha" ] || sha=$(git ls-remote "https://github.com/$repo" "refs/tags/$latest" | cut -f1)
  echo "$repo@$sha # $latest"
done
```

- [ ] **Step 2: Rewrite every `uses:` line in both workflows**

Format (one per action, the comment is mandatory so Dependabot and humans can read the version):
```yaml
      - uses: actions/checkout@<sha> # v4.x.y
```
Apply to all `actions/checkout`, `actions/setup-python`, `actions/setup-node` lines in `.github/workflows/ci.yml` and `.github/workflows/audit.yml`.

- [ ] **Step 3: Resolve the image digests**

```bash
for img in python:3.12-slim-bookworm node:22-alpine nginxinc/nginx-unprivileged:1.28-alpine; do
  echo "$img@$(docker buildx imagetools inspect "$img" --format '{{.Manifest.Digest}}')"
done
```

- [ ] **Step 4: Pin the images**

- `backend/Dockerfile` lines 5 and 19: `FROM python:3.12-slim-bookworm@sha256:<digest> AS build` / `AS runtime`.
- `frontend/Dockerfile` line 6: `FROM node:22-alpine@sha256:<digest> AS build`; line 19: `FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:<digest> AS runtime`.
- `deploy/docker-compose.yml` gateway: `image: nginxinc/nginx-unprivileged:1.28-alpine@sha256:<digest>`.
- `Makefile` line 47: `docker run --rm -v "$(CURDIR)":/src:ro python:3.12-slim-bookworm@sha256:<digest> sh -c '\`.

Add one comment above each `FROM`: `# Digest-pinned (NE-09); Dependabot (docker ecosystem) proposes updates.`

- [ ] **Step 5: Verify**

```bash
python -c "import yaml; [yaml.safe_load(open(p)) for p in ('.github/workflows/ci.yml','.github/workflows/audit.yml')]; print('yaml ok')"
make compose-check
docker build -f backend/Dockerfile -t nether-earth-backend:pin-check .
docker build -f frontend/Dockerfile -t nether-earth-frontend:pin-check .
```
Expected: `yaml ok`, compose-check exit 0, both builds succeed.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/audit.yml backend/Dockerfile frontend/Dockerfile deploy/docker-compose.yml Makefile
git commit -m "build: pin GitHub Actions by SHA and base images by digest (NE-09)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: NE-10 — hashed Python lock file and an exact vitest pin

**Files:**
- Modify: `Makefile:46-52` (`lock` target)
- Modify: `backend/requirements.lock` (regenerated)
- Modify: `backend/Dockerfile` (`--require-hashes`)
- Modify: `.github/workflows/ci.yml` (constraints derived from the hashed lock)
- Modify: `frontend/package.json:28`, `frontend/package-lock.json`

**Interfaces:** none.

- [ ] **Step 1: Regenerate the lock with hashes via pip-tools inside the pinned image**

Replace the `lock` target in `Makefile`:

```makefile
# Re-resolves against the current pins (hash lines stripped: constraints
# files take no --hash) so `make lock` only re-hashes. To upgrade a pin,
# edit its version in backend/requirements.lock first, then run make lock.
lock:
	docker run --rm -v "$(CURDIR)":/src:ro python:3.12-slim-bookworm@sha256:<digest from Task 11> sh -c '\
	  pip install -q --root-user-action=ignore --disable-pip-version-check pip-tools && \
	  cp -r /src/engine /src/backend /tmp/ && \
	  sed -e "s/ \\\\$$//" -e "/^    --hash/d" /src/backend/requirements.lock > /tmp/constraints.txt && \
	  pip-compile -q --generate-hashes --strip-extras --no-header --no-annotate \
	    -c /tmp/constraints.txt \
	    --output-file /tmp/lock.txt /tmp/engine/pyproject.toml /tmp/backend/pyproject.toml && \
	  grep -v -E "^(nether-earth-engine|nether-earth-backend)==" /tmp/lock.txt' > backend/requirements.lock.new
	{ head -3 backend/requirements.lock; cat backend/requirements.lock.new; } > backend/requirements.lock.tmp
	mv backend/requirements.lock.tmp backend/requirements.lock && rm backend/requirements.lock.new
```

(Make escaping: `\\\\$$` reaches the shell as `\\$`, i.e. the sed pattern ` \\$` for a trailing backslash. Verify with `make -n lock`.)

Run `make lock`. Check `git diff backend/requirements.lock`: every pin must keep its current version (this task adds hashes, it does not upgrade). If a version changed, the constraint did not apply; stop and fix the recipe before committing.

- [ ] **Step 2: Require hashes in the image**

`backend/Dockerfile` line ~12: `/opt/venv/bin/pip install --no-deps --require-virtualenv --require-hashes -r backend/requirements.lock`.

- [ ] **Step 3: Keep CI's constraints usage working**

Constraints files do not take `--hash` lines. In `.github/workflows/ci.yml` replace the install step:

```yaml
      - name: Install (runtime deps pinned to the production lock)
        run: |
          # Constraints files cannot carry --hash entries; strip them for -c.
          sed -e 's/ \\$//' -e '/^    --hash/d' backend/requirements.lock > /tmp/constraints.txt
          python -m pip install -c /tmp/constraints.txt -e './engine[dev]' -e './backend[dev]'
          python -m pip check
```

- [ ] **Step 4: Pin vitest exactly**

`frontend/package.json`: `"vitest": "4.1.11"` (drop the caret; use whatever exact version `package-lock.json` currently resolves: `node -p "require('./frontend/package-lock.json').packages['node_modules/vitest'].version"`). Then `(cd frontend && npm install --package-lock-only)` and confirm `git diff frontend/package-lock.json` is empty or only the root spec line.

- [ ] **Step 5: Verify**

```bash
sed -e 's/ \\$//' -e '/^    --hash/d' backend/requirements.lock > /tmp/constraints.txt
python -m pip install -c /tmp/constraints.txt -e './engine[dev]' -e './backend[dev]' && python -m pip check
docker build -f backend/Dockerfile -t nether-earth-backend:hash-check .
(cd frontend && npm ci && npm test)
```
Expected: all succeed. The docker build fails loudly if any hash mismatches; that is the control working.

- [ ] **Step 6: Commit**

```bash
git add Makefile backend/requirements.lock backend/Dockerfile .github/workflows/ci.yml frontend/package.json frontend/package-lock.json
git commit -m "build: hash-pin the Python lock and require hashes in the image; pin vitest (NE-10)

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: §8 hardening — stop trusting `X-Forwarded-*` from any peer

**Files:**
- Modify: `backend/Dockerfile` (uvicorn `CMD`)
- Modify: `docs/operations/runbook.md` §12

**Interfaces:** none. Nothing in the app reads client IPs or the forwarded scheme (docs are disabled in production), so dropping the flag changes no behavior.

- [ ] **Step 1: Edit the CMD**

In `backend/Dockerfile` remove `"--forwarded-allow-ips", "*",` from the `CMD` list and change the comment above it to:

```
# --ws-max-size bounds inbound WebSocket frames at the server. Forwarded
# headers are trusted only from 127.0.0.1 (uvicorn's default): the app never
# uses the client address, and trusting any peer would become a spoofing
# vector if the backend port were ever published. Access logs come from the
# Nginx gateway instead.
```

- [ ] **Step 2: Verify with the full deployment smoke**

Run: `deploy/smoke.sh`
Expected: exit 0 (health, headers, origin refusal, a full match, replay, restart).

- [ ] **Step 3: Runbook note**

`docs/operations/runbook.md` §12, append:
```markdown
- **Backend behind a second proxy that needs client IPs:** the backend ignores `X-Forwarded-*`
  (it never uses client addresses). Rate limits key on the gateway's `$binary_remote_addr`; put
  `real_ip` handling in the gateway, not the backend.
```

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile docs/operations/runbook.md
git commit -m "build(backend): stop trusting forwarded headers from any peer

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Full verification, review doc status, PR

**Files:**
- Modify: `docs/reviews/2026-09-30-security-and-code-review.md` §11 (status column)

- [ ] **Step 1: Whole-suite verification**

```bash
ruff check engine backend && (cd engine && mypy src) && (cd backend && mypy app)
pytest -q
(cd frontend && npm run typecheck && npm test)
make compose-check
deploy/smoke.sh
graphify update .
```
Expected: all exit 0. Paste the pytest summary line and the smoke script's final line into the PR description.

- [ ] **Step 2: Mark the review's remediation table**

In `docs/reviews/2026-09-30-security-and-code-review.md` §11, add a `Status` column to the three tables: `done (PR #<pr>)` for NE-01, NE-02, NE-03, NE-04 (format chars), NE-05, NE-06, NE-08, NE-09, NE-10, NE-11 (ready counts), NE-12, NE-13, `--forwarded-allow-ips`; `skipped (owner)` for NE-07; `separate plan` for NE-14/NE-15; `open question` for NE-04 duplicates and NE-11 join-code unification.

- [ ] **Step 3: Commit and open the PR**

```bash
git add docs/reviews/2026-09-30-security-and-code-review.md
git commit -m "docs: record remediation status for the 2026-09-30 review

Refs #<n>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin security/2026-09-30-review
gh pr create --title "Security review 2026-09-30 remediation (NE-01..06, 08..13)" --body-file - <<'EOF'
Closes #<n>.

Implements docs/superpowers/plans/2026-10-03-security-review-remediation.md.

## Acceptance per finding
- NE-01: lobby capacity released `NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS` (30 s) after the last socket leaves; regression `test_capacity_is_released_when_lobby_creator_disconnects`.
- NE-02: replay I/O on a single worker thread, FIFO, bounded backlog; `test_slow_disk_does_not_delay_ticks`; M9 replay hashes unchanged.
- NE-03: `NETHER_EARTH_REPLAY_RETENTION_DAYS` (unset = keep forever, owner decision recorded in open-questions) + startup `interrupted` marking; `tests/replay/test_retention.py`.
- NE-04: `Cf` and visually-empty nicknames rejected; duplicate policy recorded as open question.
- NE-05: `/health`, `/ready` are `async`; pinned by `test_operations_endpoints_run_on_the_event_loop`.
- NE-06: superseded socket closed with 4000; `test_second_socket_with_same_session_closes_the_first`.
- NE-08/09/10: Dependabot + audit workflow; SHA/digest pins; hashed lock with `--require-hashes`.
- NE-11: `/ready` counts hidden in production (join-code unification deliberately not done, see plan).
- NE-12: asserts replaced. NE-13: 30 s bind deadline, absolute; `test_invalid_frames_do_not_extend_the_bind_deadline`.
- §8: `--forwarded-allow-ips "*"` removed; smoke passes.
- Not in this PR: NE-07 (owner), NE-14/NE-15 (separate plan).

## Verification
<pytest summary line>
<smoke.sh final line>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

CI is billing-blocked on this repository; the owner merges after review. Do not merge from the agent.

- [ ] **Step 4: Clean up the worktree after merge**

```bash
cd /home/andrei/Projects/nether_earth && git worktree remove ../nether_earth-security && git branch -d security/2026-09-30-review
```
