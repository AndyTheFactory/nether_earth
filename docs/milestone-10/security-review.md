# M10.4 runtime security and abuse-resistance review

Scope: the guest/session/WebSocket runtime and its production deployment
(issue #125). There are no accounts, no secrets in configuration, and no
database, so the review focuses on untrusted client input, resource bounds,
token handling, and what the deployment exposes.

**Result: no unresolved critical finding. No finding requires an
architecture or scope change.**

## Controls

| Area | Control | Where | Evidence |
| --- | --- | --- | --- |
| Session/reconnect tokens | `secrets.token_urlsafe(32)` (256 bits), in memory only, sent only to their owner; never logged, never in replay artifacts | `app/match/manager.py`, `app/replay/writer.py` | `tests/replay/test_replay_log.py`, `tests/test_logging.py` (M10.5) |
| Join codes | 6 chars from 36 symbols via `secrets.choice`; a connection is closed after 5 failed joins; gateway limits handshakes to 30/min/IP (burst 30) | `app/transport/ws.py`, `deploy/nginx/` | `test_join_code_guessing_is_capped_per_connection` |
| Nicknames | 1–32 chars (schema + Pydantic), trimmed, control/unassigned/private-use and bidi override characters refused; frontend escapes all interpolated text | `app/match/manager.py`, `frontend/src/ui/dom.ts` | `test_nickname_control_characters_rejected` |
| Message size | 16 KiB per frame, enforced by uvicorn (`--ws-max-size`) and by the handler (close 1009) | `backend/Dockerfile`, `app/transport/limits.py` | `test_oversized_message_is_rejected_and_closed` |
| Message rate | 40 msg/s sustained, burst 80, per connection (honest clients peak around 20/s); over the limit → `rate_limited`, close 1008 | `app/transport/limits.py` | `test_message_flood_is_rate_limited_and_closed` |
| Malformed input | Schema validation before any routing; invalid frames get `invalid_message` (error count only, no echo); structurally invalid command payloads never reach the engine; duplicate/replayed sequence numbers rejected (M7) | `app/transport/ws.py` | existing `tests/transport/test_ws.py`, `test_commands.py` |
| Handler failures | Unexpected exception → logged with match id, socket closed 1011, no details sent | `app/transport/ws.py` | code review |
| Slow/stalled peers | A send that blocks > 5 s marks the socket stalled and closes it; the normal disconnect → pause → 60 s grace policy then applies instead of the peer freezing the match tick loop | `app/transport/connections.py` | `test_stalled_peer_is_closed_instead_of_blocking` |
| Memory growth | At most `NETHER_EARTH_MAX_MATCHES` (default 200) matches at once → `server_busy`; finished/abandoned matches are disposed (M10.6) | `app/match/manager.py` | `test_match_capacity_returns_server_busy` |
| Origin / CSWSH | `/ws` handshakes with an `Origin` other than `NETHER_EARTH_PUBLIC_BASE_URL`'s origin are refused (HTTP 403). Missing `Origin` (non-browser tools) is allowed: only browsers carry ambient credentials, and there are none here anyway | `app/transport/ws.py`, `app/config.py` | `test_foreign_origin_handshake_is_refused` |
| CORS | None configured, deliberately: the app is same-origin behind the gateway, so browsers refuse cross-origin reads of `/api/*` | `app/main.py` | — |
| Replay paths | Match ids are server-generated; `match_dir` additionally refuses any id outside `[A-Za-z0-9_-]{1,64}` so no value can escape the replay directory | `app/replay/writer.py` | `test_replay_match_dir_refuses_unsafe_ids` |
| Error responses | Production disables `/docs`, `/redoc`, `/openapi.json`; FastAPI runs without debug, so HTTP 500s carry no traceback | `app/main.py` | `test_production_app_hides_interactive_docs` |
| Configuration | Production refuses to start without `NETHER_EARTH_PUBLIC_BASE_URL`/`NETHER_EARTH_REPLAY_DIR`; no secrets exist in configuration; TLS keys mounted read-only into the gateway only | `app/config.py`, `deploy/` | `tests/test_config.py` |
| Gateway | Body 16 KiB (413), headers 8 KiB (400), header/body timeouts, GET-only API/static routes, per-IP rate/connection limits (429), `server_tokens off`, CSP `connect-src 'self'` (also neutralises the frontend's `?ws=` debug override), `X-Frame-Options DENY`, `nosniff`, HSTS on TLS | `deploy/nginx/` | M10.3 probes, `make compose-check` |
| Containers | Non-root users, read-only root filesystems, all capabilities dropped, `no-new-privileges`, memory/CPU limits; backend port not published | `deploy/docker-compose.yml` | M10.1/M10.2 |

## Dependency review (2026-09-21)

- Python runtime lock (`backend/requirements.lock`): `pip-audit` reports no known vulnerabilities.
- Frontend: `npm audit --omit=dev` clean (the only runtime dependency is `pixi.js`).
  Dev/build tooling had 6 advisories (vitest, vite/esbuild dev server, js-yaml, ajv); none reach the production image,
  which ships only static files. Fixed anyway by pinning vite 6.4.3, js-yaml 4.3.2, ajv 8.20.0 and vitest 4.1.x
  (`npm audit` now reports 0).

## Accepted residual risks (non-critical)

- **No authentication** (v1 design: guest nicknames + join code). Anyone with a join code can take the second seat of a waiting match.
- **Distributed abuse** from many IPs can still fill match capacity or bandwidth; mitigation beyond per-IP limits (WAF/CDN) is out of scope for v1.
- **Rate-limit key is the client IP seen by the gateway.** If another proxy/CDN is put in front of the gateway, configure Nginx `real_ip` first, or all clients share one bucket.
- **In-memory state**: a backend restart ends every live match (see runbook); replay artifacts of interrupted matches remain `in_progress`.
