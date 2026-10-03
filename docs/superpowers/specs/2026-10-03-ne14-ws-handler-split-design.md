# NE-14: Split the WebSocket handler into per-message handlers — Design

**Source finding:** `docs/reviews/2026-09-30-security-and-code-review.md` §7, NE-14 (Low). Companion: NE-15 (history-narrating comments), applied to the code this refactor moves.

**Status:** design for review. No behaviour change is permitted by this spec.

## 1. Problem

`backend/app/transport/ws.py` is 650 lines, of which `websocket_endpoint` (inside `create_websocket_router`) is one ~430-line coroutine. It holds six message branches (create, join, ready, leave, command, reconnect), the authentication/pinning logic, the bind deadline, rate and size limits, and four nested helper closures that mutate five `nonlocal` variables (`bound`, `bucket`, `failed_joins`, `disconnect_notified`, `bind_deadline`). Consequences:

- No branch can be unit-tested without a full `TestClient` app; every transport test is an end-to-end test (37 in `test_ws.py`, ~20 s).
- Changes to one branch (NE-01, NE-06, NE-12, NE-13 in PR #307 all touched this function) risk the others, and reviewers must re-read the whole coroutine to judge a ten-line change.
- Closure state makes the control flow hard to follow: `bound` is assigned in three places and read in every helper.

## 2. Goal and non-goals

**Goal.** The same `/ws` behaviour, byte-for-byte on the wire, implemented as: a thin connection loop, one per-connection state object, and one handler function per client message type, each independently unit-testable.

**Non-goals.** No protocol change, no new error codes or close codes, no new log events, no change to limits or timing, no change to `ConnectionRegistry`, `MatchManager`, `MatchRuntimeRegistry` or the frontend. No renaming of the public factory `create_websocket_router` or of the module-level constants tests monkeypatch.

## 3. Behavioural invariants (the contract the refactor must keep)

Every item below is pinned by an existing test and must stay green without editing those tests, except where noted.

1. **Handshake:** `Origin` present and not in `allowed_origins` → close 1008 before `accept`, log `ws_origin_refused`.
2. **Receive loop order per frame:** bind deadline (unbound sockets only, absolute, `UNBOUND_SOCKET_TIMEOUT_S` read at call time) → token bucket (`rate_limited`, 1008, log `ws_rate_limited`) → size (`message_too_large`, 1009) → `parse_client_message` (`invalid_message` error, socket stays open) → dispatch.
3. **create/join on a bound socket:** `already_bound`, close 1008.
4. **create:** `invalid_nickname` / `server_busy` errors keep the socket open; success binds, attaches, sends `created` (with `opponent="computer"` only for solo).
5. **join:** `match_not_found` / `match_full` count toward `MAX_FAILED_JOINS` (then `too_many_join_attempts`, 1008); `invalid_nickname` does not count; success binds, attaches, sends `joined`, then broadcasts `ready_state`.
6. **Authenticated messages (ready/leave/command/reconnect):** `resolve_session` failure → `invalid_session`, 1008; `matchId`/`playerId` not matching the token → `session_mismatch`, 1008; first authenticated message binds and attaches; a later message with a different token or match → `session_mismatch`, 1008.
7. **ready:** `set_ready`; broadcast `ready_state`; if the match just started, broadcast `started` then the tick-0 `snapshot`; `announce_started` runs in a `finally` covering the whole broadcast window; `game_state is None` after ACTIVE raises `RuntimeError`.
8. **leave:** teardown (unregister, disconnect notification once, lobby-abandoned mark when no sockets remain), close 1000, handler returns.
9. **command:** `payload_to_command` failure → `invalid_command_payload`; `submit_command` False → `command_rejected`; both keep the socket open.
10. **reconnect:** attach (closing a predecessor with 4000) → `announce_started` unless WAITING → `resync` (empty snapshot when `game_state is None`) → `mark_reconnected` → durable `finished` / `forfeit` / `no_contest` replay to this socket only; FORFEIT without both ids raises `RuntimeError`.
11. **attach:** `register` then `mark_lobby_occupied`, then close the replaced socket with 4000.
12. **Teardown:** runs exactly once per connection in `finally`; notifies disconnect only when `unregister` returned True; marks the lobby abandoned when no sockets remain for the match.
13. **Fail-safe:** any other exception → log `ws_handler_failed` with `match_id` (never the token), close 1011.
14. **Monkeypatch points kept:** `app.transport.ws.UNBOUND_SOCKET_TIMEOUT_S`; `app.transport.connections.SEND_TIMEOUT_S`.

## 4. Design

### 4.1 Module layout (`backend/app/transport/`)

| File | Responsibility | Size target |
|---|---|---|
| `ws.py` (modified) | `create_websocket_router`: origin check, accept, the receive loop (deadline, rate, size, parse), dispatch table lookup, fail-safe and `finally` teardown. Module constants stay here. | ≤ 180 lines |
| `connection.py` (new) | `Connection`: per-socket state and the primitives handlers need (`send`, `send_error`, `reject_and_close`, `close`, `attach`, `teardown`). | ≤ 170 lines |
| `handlers.py` (new) | `authenticate` plus one `handle_*` per message type, and `HANDLERS` dispatch table. Pure orchestration over `Connection` and the three registries. | ≤ 320 lines |

`_ready_state_message` moves to `handlers.py` (its only users are there). `_close` and `_send_error` become `Connection` methods; the module-level `_close(websocket, code)` is kept as a one-line helper in `connection.py` because `attach` must close a *different* socket than its own.

### 4.2 `Connection`

```python
@dataclass(slots=True)
class BoundSession:
    match_id: str
    player_id: str
    session_token: str


@dataclass(slots=True, eq=False)
class Connection:
    websocket: WebSocket
    match_manager: MatchManager
    runtime_registry: MatchRuntimeRegistry
    connection_registry: ConnectionRegistry
    bound: BoundSession | None = None
    failed_joins: int = 0
    _disconnect_notified: bool = False

    @property
    def match_id(self) -> str | None: ...          # bound.match_id or None

    async def send(self, message: OutboundMessage) -> None          # websocket.send_text(serialize_server_message(message))
    async def send_error(self, code: str, detail: str, *, match_id: str | None = ...) -> None
    async def reject_and_close(self, code: str, detail: str = "session rejected; closing connection",
                               close_code: int = POLICY_VIOLATION_CLOSE_CODE, *, match_id: str | None = ...) -> None
    async def close(self, code: int) -> None                        # guarded by application_state, never raises
    def bind(self, match_id: str, player_id: str, session_token: str) -> None   # sets `bound`; raises RuntimeError if already bound
    async def attach(self) -> None                                  # register(bound) → mark_lobby_occupied → close replaced with REPLACED_CLOSE_CODE
    async def teardown(self) -> None                                # unregister → notify_disconnect_once → mark_lobby_abandoned when empty
```

`match_id` defaults for `send_error`/`reject_and_close` are the bound match id (today's `bound.match_id if bound else None`), overridable because the mismatch path reports the *resolved* match id, not the bound one.

The token bucket and bind deadline stay in the loop (`ws.py`): they gate frames before any handler runs and no handler needs them.

### 4.3 Handlers

```python
class Outcome(Enum):
    CONTINUE = auto()   # keep receiving
    CLOSED = auto()     # handler closed the socket; the loop returns

Handler = Callable[[Connection, Any], Awaitable[Outcome]]

async def handle_create(conn: Connection, message: ClientCreateMatch) -> Outcome
async def handle_join(conn: Connection, message: ClientJoinMatch) -> Outcome
async def handle_ready(conn: Connection, message: ClientSetReady, match: Match, player: PlayerId) -> Outcome
async def handle_leave(conn: Connection, message: ClientLeaveMatch, match: Match, player: PlayerId) -> Outcome
async def handle_command(conn: Connection, message: ClientGameplayCommand, match: Match, player: PlayerId) -> Outcome
async def handle_reconnect(conn: Connection, message: ClientReconnect, match: Match, player: PlayerId) -> Outcome

async def authenticate(conn: Connection, message: AuthenticatedClientMessage) -> tuple[Match, PlayerId] | None
    # resolve_session → invalid_session; id mismatch → session_mismatch; first auth binds+attaches;
    # later auth with another token/match → session_mismatch. Returns None after closing.

async def dispatch(conn: Connection, message: ClientMessage) -> Outcome
    # create/join → unbound-only handlers (already_bound otherwise);
    # everything else → authenticate, then the matching handler.
```

`AuthenticatedClientMessage` is a type alias (`ClientSetReady | ClientLeaveMatch | ClientGameplayCommand | ClientReconnect`) defined in `handlers.py`; the protocol package is not changed.

Rules for the move:

- Each handler body is today's branch, with `websocket.send_text(serialize_server_message(X))` → `await conn.send(X)`, `_send_error(...)` → `await conn.send_error(...)`, `_reject_and_close(...)` → `await conn.reject_and_close(...)` followed by `return Outcome.CLOSED`, `continue` → `return Outcome.CONTINUE`, `return` after a close → `return Outcome.CLOSED`.
- `handle_ready` keeps the `try/finally: announce_started` exactly as it is.
- `handle_reconnect` keeps its statement order (invariant 10).
- Docstrings and comments that travel with the code are rewritten as behaviour statements (NE-15): keep the *why* (e.g. why `announce_started` is in a `finally`, why resync precedes `mark_reconnected`), drop task/issue/review-round references. Nothing outside the moved code is edited for comments.

### 4.4 The loop (`ws.py`)

```python
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if origin refused: close 1008, return
    await websocket.accept()
    conn = Connection(websocket, match_manager, runtime_registry, connection_registry)
    bucket = TokenBucket()
    bind_deadline = loop.time() + UNBOUND_SOCKET_TIMEOUT_S
    try:
        while True:
            raw = await _receive(websocket, conn, bind_deadline)      # WebSocketDisconnect → return; TimeoutError → bind_timeout, return
            if raw is None: return
            if not bucket.allow(): rate_limited → return
            if too large: message_too_large → return
            message = parse or send invalid_message, continue
            if await dispatch(conn, message) is Outcome.CLOSED: return
    except WebSocketDisconnect: pass
    except Exception: log ws_handler_failed, close 1011
    finally: await conn.teardown()
```

`_receive` is a module-level helper so the deadline logic is one readable function; it reads `UNBOUND_SOCKET_TIMEOUT_S` only through the `bind_deadline` computed once after `accept`, exactly as today.

### 4.5 Constants

Close codes (`POLICY_VIOLATION_CLOSE_CODE`, `NORMAL_CLOSE_CODE`, `TOO_BIG_CLOSE_CODE`, `INTERNAL_ERROR_CLOSE_CODE`, `REPLACED_CLOSE_CODE`) move to `limits.py` without the leading underscore, because `connection.py` and `handlers.py` both need them. `ws.py` re-exports nothing; `UNBOUND_SOCKET_TIMEOUT_S` stays in `ws.py` (test monkeypatch point). The private names in `ws.py` are deleted, not aliased: nothing outside `ws.py` imports them today (verify with grep in the plan).

## 5. Testing

**Existing suites are the contract.** `backend/tests/transport/` (test_ws, test_hardening, test_milestone_integration, test_commands, test_snapshots, test_victory), `tests/match/test_sweep.py` and `tests/acceptance/` must pass with no edits other than import paths if a test imports a moved private name (grep `from app.transport.ws import` in tests first; today tests import only `ws as ws_module` for the timeout constant, which stays).

**New unit tests** (`backend/tests/transport/test_handlers.py`) prove the point of the refactor: each handler runs against a `Connection` built on a fake `WebSocket` (records sent texts and close codes; `application_state` CONNECTED) and real `MatchManager()`, `MatchRuntimeRegistry()`, `ConnectionRegistry()` with no TestClient, no network, no event-loop server:

- `handle_create`: PvP success binds, registers, sends `created` without `opponent`; `invalid_nickname` leaves the socket open and unbound; `server_busy` with `max_matches=0`.
- `handle_join`: `match_not_found` increments `failed_joins`; the fifth failure closes 1008 with `too_many_join_attempts`; success broadcasts `ready_state` to the creator's fake socket too.
- `authenticate`: bad token → `invalid_session` + 1008 + returns None; wrong `playerId` → `session_mismatch`; first auth binds and registers; second token on a bound connection → `session_mismatch`.
- `handle_command`: diagonal payload → `invalid_command_payload`; unknown match in the registry → `command_rejected`.
- `handle_leave`: returns `CLOSED`, socket closed 1000, unregistered, `mark_disconnected` called once (spy via monkeypatch).
- `handle_reconnect` on a WAITING match: `resync` carries the empty snapshot; on an ACTIVE match with a FORFEIT result, the `forfeit` message follows `resync`.
- `Connection.attach` with a predecessor: predecessor closed 4000, `mark_lobby_occupied` called.
- `Connection.teardown` twice: second call is a no-op; `mark_lobby_abandoned` called only when no sockets remain.

**Structural checks** (in the plan's final task): `wc -l` against the §4.1 targets; `ruff check`, strict `mypy`; `deploy/smoke.sh`.

## 6. Risks and mitigations

- **Subtle ordering drift** (e.g. binding before/after `attach`, `announce_started` placement). Mitigation: §3 invariants are the review checklist; the implementer moves code rather than rewriting it; the reviewer diffs each handler against the original branch.
- **Closure semantics lost**: today `bound` is read by `teardown` at `finally` time, so a bind that happens late is still torn down. `Connection.bound` is the same object reference, so behaviour is unchanged; a test (`handle_leave` / teardown) pins it.
- **Error `match_id` field**: the mismatch path uses the resolved match's id, the rest use the bound one. `send_error`'s explicit `match_id=` override covers it; the `authenticate` tests assert the field.
- **mypy**: `dispatch` narrows `ClientMessage` by `isinstance`; the protocol union is already a pydantic discriminated union, so no casts are needed.

## 7. Rollout

One PR on a worktree branch `refactor/ne14-ws-handlers`, three commits: (1) `connection.py` + constants move, (2) `handlers.py` + loop rewrite, (3) `test_handlers.py` + docstring trims. Wire-identical, so no deploy note beyond the changelog line. Closes NE-14 and the in-scope part of NE-15 in the review's §11 status table.
