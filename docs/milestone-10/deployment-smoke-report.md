# M10.9 production deployment smoke, restart, replay persistence and rollback

Date: 2026-09-21. Executed by following [the runbook](../operations/runbook.md) on a fresh
clone of the M10 branch (no source edits on the "host"), on a Linux host with Docker /
Compose v2.27. The public URL was `http://localhost:18080` (plain-HTTP gateway); the TLS
override was exercised separately in M10.3 (self-signed certificate: HTTP→HTTPS redirect,
ACME webroot, HTTP/2, HSTS, `wss://` live two-client check 15/15).

## Scenario and results

| # | Step (issue #130) | Result |
| --- | --- | --- |
| 1 | Configure: `cp deploy/.env.example deploy/.env`, edit, create replay dir owned by uid 10001. Removing a required variable makes `docker compose config` fail with `required variable NETHER_EARTH_PUBLIC_BASE_URL is missing a value` | ✅ |
| 2 | `dc build --no-cache --pull` (clean images, version `1.0.0-rc1`), `dc up -d --wait` | ✅ three services healthy; backend runs as uid 10001 |
| 3 | Health/readiness: `/healthz` `ok`, `/api/health` ok, `/api/ready` ready with zero counts | ✅ |
| 4 | Two browsers through Nginx (headless Chromium, [`browser-smoke.mjs`](browser-smoke.mjs)) | ❌ on rc1: **Pixi failed to start under the gateway CSP** (`Current environment does not allow unsafe-eval`). Fixed in the owning task (M10.3 CSP) by importing `pixi.js/unsafe-eval`; the CSP stays strict |
| 10 | Update/redeploy per runbook §9 to `1.0.0-rc2` (contains the fix): `dc build`, `dc up -d --wait` | ✅ only backend/frontend recreated; the gateway kept running and routed to the new containers |
| 4 (again) | Two browsers on rc2: create, join via `?code=` link, both ready, canvas renders, authoritative ticks advance, tab reload with `?resume` rejoins the running match, no CSP violations, no page errors | ✅ 9/9 |
| 5 | Complete a representative match: player B closes the browser; after the 60 s grace player A sees **VICTORY BY FORFEIT** | ✅ |
| 6 | Logs: every lifecycle step visible per match id — `match_created`, `match_joined`, `match_started`, `replay_started`, `player_disconnected`, `player_reconnected`, `match_finished` (`forfeit`, `disconnect_timeout`), `replay_finalized`, `match_disposed` (after retention). No WARNING/ERROR lines; no session tokens | ✅ |
| 7 | Replay artifacts on the host mount: one directory per started match, `meta.json` `status: "finished"` | ✅ |
| 8 | `dc restart backend`: artifacts still present, `/api/ready` ready | ✅ |
| 9 | Health reflects failure and recovery: replay dir made unwritable → `/api/ready` **503** `replay_dir: unwritable`, `dc ps` shows backend `(unhealthy)`; permissions restored → `(healthy)`. Backend stopped → gateway `/api/health` 504; `dc up -d --wait` → 200 | ✅ |
| 11 | Rollback per runbook §10 to `1.0.0-rc1` (`NETHER_EARTH_VERSION` change + `dc up -d --wait`, no build): containers run the rc1 images, gateway serves the rc1 bundle (`index-Cb01oU6h.js`), ready, live two-client check 15/15 | ✅ |
| 12 | Roll forward to rc2: rc2 images and bundle (`index-DXfhd-Gc.js`), ready; replay artifacts from every version still on the host | ✅ |

The scripted equivalent of steps 2–4 and 6–8 is `deploy/smoke.sh` (a CI gate); it passed on
this branch (`SMOKE OK`, live two-client check 15/15 before and after a backend restart).

## Blockers found and fixed

| Blocker | Owning task | Fix |
| --- | --- | --- |
| Frontend blank under the production CSP (Pixi uses eval by default) | M10.3 | `import 'pixi.js/unsafe-eval'` in `frontend/src/main.ts`; runbook troubleshooting row |
| `leave` racing a concurrent broadcast raised `RuntimeError` in the handler (found via M10.5 logs) | M10.5 | idempotent close |

## Not covered here

- Two **human** players on a real public VPS with a real certificate: v1 release checklist
  item 11, together with the M9 human acceptance (#119).
- The update was between two builds of this branch (rc1 → rc2); there is no earlier
  production release to roll back to yet.
