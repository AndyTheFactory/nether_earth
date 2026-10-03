# Nether Earth v1 operations runbook

One VPS, Docker Compose, three containers. No database: all match state is in
the backend process's memory; replay/debug artifacts are files on the host.

```text
internet ─▶ gateway (Nginx, :80/:443) ─┬─▶ frontend (static files)
                                       └─▶ backend (FastAPI, 1 process) ─▶ /srv/nether-earth/replays (host)
```

All commands below run from the repository checkout on the VPS (e.g.
`/opt/nether-earth`) unless stated otherwise. `dc` is shorthand for:

```bash
alias dc='docker compose -f deploy/docker-compose.yml --env-file deploy/.env'
# with TLS at Nginx (section 4):
alias dc='docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tls.yml --env-file deploy/.env'
```

## 1. Host prerequisites

- 64-bit Linux, Docker Engine ≥ 24 with the Compose v2 plugin (`docker compose version`), git, curl.
- Sizing: see [performance report](../milestone-10/performance-report.md). The backend is one
  single-threaded process: 1 vCPU and 1 GiB RAM carry the tested envelope; a second vCPU only
  helps Nginx/OS headroom.
- Firewall: allow inbound 80 and 443 (or `NETHER_EARTH_HTTP_PORT`/`NETHER_EARTH_HTTPS_PORT`) and SSH.
  Nothing else is published; the backend port is only reachable inside the Compose network.
- DNS: an A/AAAA record for the public hostname pointing at the VPS.

## 2. Configuration

```bash
git clone <repo-url> /opt/nether-earth && cd /opt/nether-earth
git checkout v1.0.0                      # always deploy a release tag
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env
sudo install -d -o 10001 -g 10001 -m 750 /srv/nether-earth/replays
```

| Variable | Required | Meaning |
| --- | --- | --- |
| `NETHER_EARTH_VERSION` | yes | Image tag to build/run; equals the release version (`1.0.0`). |
| `NETHER_EARTH_PUBLIC_BASE_URL` | yes | Public URL, e.g. `https://nether-earth.example.com`. Its origin is the only one allowed to open `/ws`. |
| `NETHER_EARTH_REPLAY_HOST_DIR` | yes | Host directory for replays; writable by uid 10001. |
| `NETHER_EARTH_HTTP_PORT` | no (80) | Host port of the gateway's HTTP listener. |
| `NETHER_EARTH_HTTPS_PORT` | no (443) | Host port for HTTPS (TLS override only). |
| `NETHER_EARTH_TLS_CERT_DIR` | TLS only | Directory with `fullchain.pem` + `privkey.pem`, readable by uid 101. |
| `NETHER_EARTH_ACME_WEBROOT` | TLS only | certbot `--webroot` directory served at `/.well-known/acme-challenge/`. |
| `NETHER_EARTH_LOG_LEVEL` | no (INFO) | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `NETHER_EARTH_MAX_MATCHES` | no (200) | Matches held in memory at once; beyond this, `create` gets `server_busy`. |
| `NETHER_EARTH_FINISHED_MATCH_RETENTION_SECONDS` | no (300) | How long a finished match stays resolvable for late reconnects. |
| `NETHER_EARTH_WAITING_MATCH_TIMEOUT_SECONDS` | no (900) | How long a lobby waits for its second player. |
| `NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS` | no (30) | How long a lobby with no connected player keeps its capacity (covers a page refresh). |
| `NETHER_EARTH_REPLAY_RETENTION_DAYS` | no (unset = keep forever) | Finished/interrupted replay artifacts older than this are deleted hourly by the backend. |
| `NETHER_EARTH_BACKEND_MEM_LIMIT` / `_CPUS` | no (1g / 1.0) | Backend container limits. |

A missing required variable stops `docker compose config`/`up` with
`required variable ... is missing a value`. The backend itself also refuses to start in
production without a valid public URL and replay directory (log line: `ConfigError: ...`).
Gameplay rules are not configurable here; they ship with the release.

Validate: `dc config -q && echo OK`.

## 3. First deployment

```bash
dc build                      # builds nether-earth-{backend,frontend}:$NETHER_EARTH_VERSION
dc up -d --wait               # returns once all three services are healthy
dc ps                         # expect three services "Up ... (healthy)"
```

Then run the checks in section 6. Images are local to the host; there is no registry in v1.
Keep previous tags (`docker image ls 'nether-earth-*'`) — they are the rollback targets.

## 4. HTTPS (TLS terminated at Nginx)

1. Point DNS at the host and start the plain-HTTP stack once (section 3).
2. Obtain the first certificate. The TLS gateway needs a certificate to start, so the first
   issuance uses certbot's standalone mode while the gateway is briefly stopped:

   ```bash
   sudo install -d /srv/nether-earth/acme-webroot /srv/nether-earth/tls
   dc stop gateway
   sudo certbot certonly --standalone -d nether-earth.example.com
   ```

   Set `NETHER_EARTH_TLS_CERT_DIR=/srv/nether-earth/tls` and
   `NETHER_EARTH_ACME_WEBROOT=/srv/nether-earth/acme-webroot` in `deploy/.env`.
3. Install a deploy hook that copies the certificate where the unprivileged gateway (uid 101)
   can read it and reloads Nginx — `/etc/letsencrypt/renewal-hooks/deploy/nether-earth.sh`:

   ```bash
   #!/bin/sh
   set -e
   d=/srv/nether-earth/tls
   cp -L /etc/letsencrypt/live/nether-earth.example.com/fullchain.pem "$d/fullchain.pem"
   cp -L /etc/letsencrypt/live/nether-earth.example.com/privkey.pem "$d/privkey.pem"
   chown 101:101 "$d"/*.pem && chmod 600 "$d/privkey.pem"
   docker exec nether-earth-gateway-1 nginx -s reload || true
   ```

   Run it once by hand.
4. Set `NETHER_EARTH_PUBLIC_BASE_URL=https://...`, start with the TLS override (`dc` alias with
   `docker-compose.tls.yml`): `dc up -d --wait`.
   Then move renewals to the webroot method so they work while the site is up:
   `sudo certbot certonly --webroot -w /srv/nether-earth/acme-webroot -d nether-earth.example.com --force-renewal`
   (certbot's timer renews from then on and runs the deploy hook).
5. Verify: `curl -sI http://<host>/` → `301` to `https://`; `curl -sI https://<host>/` shows
   `strict-transport-security`; the browser loads the game and the lobby connects (`wss://`).

Nginx speaks TLS 1.2/1.3 with HTTP/2; WebSockets use HTTP/1.1 upgrade on the same port.
If another proxy/CDN terminates TLS in front of the VPS instead, keep the plain gateway and
configure Nginx `real_ip` so per-IP rate limits see client addresses.

## 5. Start, stop, restart

| Action | Command | Effect on players |
| --- | --- | --- |
| Start | `dc up -d --wait` | — |
| Stop | `dc down` | Ends every live match (in-memory state). Replays stay on disk. |
| Restart backend | `dc restart backend` | Ends every live match; clients show disconnect. |
| Restart gateway only | `dc restart gateway` | Drops sockets; clients reconnect within the 60 s grace, matches continue. |
| Reload Nginx config | `docker exec nether-earth-gateway-1 nginx -s reload` | No interruption. |

Live matches cannot be migrated. Before a planned backend restart/update, check
`curl -s localhost/api/ready` → `"matches": 0`, or accept ending the running matches.

## 6. Health and verification

| Check | Command | Expected |
| --- | --- | --- |
| Containers | `dc ps` | three services `(healthy)` |
| Gateway liveness | `curl -s http://localhost/healthz` | `ok` |
| Backend liveness | `curl -s http://localhost/api/health` | `{"status":"ok"}` |
| Backend readiness | `curl -s http://localhost/api/ready` | `"status":"ready"`, counts of matches/runtimes/connections |
| Frontend | open the public URL | lobby renders; create a match; a second browser joins with the code |
| Full smoke (non-destructive, separate project/port) | `deploy/smoke.sh` | ends with `SMOKE OK` |

With the TLS override, use `https://<host>/...` (plain HTTP redirects everything except `/healthz`).

`/api/ready` returns **503** with `"checks": {"replay_dir": "unwritable"}` when the replay
directory cannot be written, and during shutdown. Docker's health check uses it: the backend
shows `(unhealthy)` in `dc ps` and `docker inspect --format '{{json .State.Health}}' nether-earth-backend-1`
shows the last probe outputs.

## 7. Logs

- Backend: JSON lines on stdout — `dc logs -f backend`. Useful filters:

  ```bash
  dc logs --no-log-prefix backend | jq -c 'select(.level=="ERROR" or .level=="WARNING")'
  dc logs --no-log-prefix backend | jq -c 'select(.match_id=="<id>")'       # one match's lifecycle
  dc logs --no-log-prefix backend | jq -r '.event' | sort | uniq -c           # event totals
  ```

  Lifecycle events: `process_started`, `process_stopping`, `match_created`, `match_joined`,
  `match_started`, `player_disconnected`, `player_reconnected`, `match_finished` (with
  `outcome`, `reason`, `winner_player_id`, `tick`), `match_disposed`, `replay_started`,
  `replay_finalized`. Problems: `replay_write_failed`, `tick_overrun`, `tick_loop_crashed`,
  `ws_handler_failed`, `ws_send_stalled`, `ws_rate_limited`, `ws_origin_refused`,
  `ws_join_attempts_exceeded`, `match_sweep_failed`. Session tokens are never logged.
- Gateway: Nginx access/error logs — `dc logs -f gateway` (429 = per-IP limits, 413/400 = size limits).
- Docker keeps 5 × 10 MB per container (`json-file` rotation); ship elsewhere only if needed.

## 8. Replay/debug artifacts

- Location: `$NETHER_EARTH_REPLAY_HOST_DIR/<match_id>/` with `meta.json` (map/scenario/rules
  versions, seed, players' nicknames, status, result, final snapshot), `commands.jsonl`
  (accepted commands per tick + event summary) and `lifecycle.jsonl` (disconnect/pause/
  resume/forfeit/no-contest). Created when a match starts; finalized when it ends.
- `status` is `"in_progress"` while the match runs, `"finished"` when it ended normally, and
  `"interrupted"` if the backend stopped or crashed while it ran (set automatically at the next
  startup; the file is still a valid prefix of the command stream). `commands.jsonl` has no
  `fsync`; a host crash can lose its last lines.
- Size: every tick is recorded; measured ~1.5 MB per match for ~4 minutes of heavy command traffic (see the performance report).
- Retention: unset `NETHER_EARTH_REPLAY_RETENTION_DAYS` keeps everything (the disk fills over time
  and `/ready` turns 503 when it is full). Set it to delete finished/interrupted artifacts older
  than N days, checked hourly. A host cron is no longer required but still works.
- Backup: replays are the only persistent data. They are optional debugging/audit material;
  back them up with any file-level tool if you want to keep them, e.g.
  `tar -C /srv/nether-earth -czf replays-$(date +%F).tgz replays` or `rsync -a` to another host.
  No database backup/restore exists or is needed. Also back up `deploy/.env` (and TLS material
  if not re-issuable).

## 9. Update / redeploy

```bash
cd /opt/nether-earth
curl -s localhost/api/ready                    # note "matches"; live matches will end
grep NETHER_EARTH_VERSION deploy/.env          # this is the rollback target, write it down
git fetch --tags && git checkout v1.0.1
sed -i 's/^NETHER_EARTH_VERSION=.*/NETHER_EARTH_VERSION=1.0.1/' deploy/.env
dc build                                       # old images stay tagged with the old version
dc up -d --wait                                # recreates changed services only
```

Then run section 6. `up --wait` exits non-zero if a service does not become healthy.
The gateway re-resolves backend/frontend addresses, so it does not need a restart.

## 10. Rollback

Rollback = run the previous version's images with the previous version's compose files.

```bash
git checkout v1.0.0                            # previous release tag (compose + nginx config)
sed -i 's/^NETHER_EARTH_VERSION=.*/NETHER_EARTH_VERSION=1.0.0/' deploy/.env
docker image ls 'nether-earth-*'               # confirm 1.0.0 images still exist
dc up -d --wait                                # no build: reuses the kept images
```

If the previous images were pruned, `dc build` rebuilds them from the checked-out tag
(the Python lock and `package-lock.json` pin dependencies). Verify with section 6.
Replay files need no migration; each `meta.json` records its `schema_version`/`rules_version`.

## 11. Failed deployment

- `dc up --wait` fails or a service stays `unhealthy`: `dc ps`, `dc logs --tail 100 <service>`.
  - backend `ConfigError: ...` → fix `deploy/.env`, `dc up -d --wait`.
  - backend healthy check failing with `replay_dir: unwritable` → `sudo chown -R 10001:10001 "$NETHER_EARTH_REPLAY_HOST_DIR"`; check free disk (`df -h`).
  - gateway `[emerg]` in logs → Nginx config error, e.g. missing certificate files with the TLS override.
- If it is not fixable quickly: roll back (section 10). Rollback only touches containers;
  replay data is unaffected.

## 12. Troubleshooting

| Symptom | Check | Likely cause / fix |
| --- | --- | --- |
| Page does not load | `curl -sI localhost/`, `dc ps` | gateway/frontend down → `dc up -d --wait`; firewall/DNS |
| Lobby shows "connection: closed" immediately | browser devtools → `/ws` status | **403**: `NETHER_EARTH_PUBLIC_BASE_URL` origin does not match the URL in the address bar (scheme/host/port). **429**: per-IP limit (many tabs/players behind one NAT or reconnect loop). **502/504**: backend down (`dc ps`, `dc logs backend`) |
| Frontend shows a blank page, console mentions `unsafe-eval` | browser console | a custom build dropped the `pixi.js/unsafe-eval` import in `frontend/src/main.ts`; the gateway CSP forbids eval |
| Players disconnected every ~2 min | gateway logs, proxies in front | an extra proxy with a short idle timeout; Nginx here allows 120 s idle and uvicorn pings every 20 s |
| `server_busy` on create | `/api/ready` matches | capacity reached; raise `NETHER_EARTH_MAX_MATCHES` only if CPU allows (performance report) |
| Stutter / `tick_overrun` warnings | `docker stats nether-earth-backend-1` | CPU saturated: fewer concurrent matches or a faster CPU |
| No new replays | `/api/ready` checks, `replay_write_failed` logs | permissions or full disk; matches keep running without replays |
| Backend restarts | `docker inspect -f '{{.RestartCount}} {{.State.OOMKilled}}' nether-earth-backend-1` | OOM → raise `NETHER_EARTH_BACKEND_MEM_LIMIT`, check leak signs in `/api/ready` counts |

- **`server_busy` for everyone:** capacity is `NETHER_EARTH_MAX_MATCHES` across every state. One IP
  can hold at most 32 sockets (gateway `limit_conn`) and therefore at most 32 lobbies, plus whatever
  it created inside the abandonment grace (30 handshakes/min × 30 s ≈ 15). Filling 200 needs several
  addresses. Lower `NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS` or the gateway `limit_conn` if abused.
- **`bind_timeout` errors in a client:** a socket must send `create`/`join`/`reconnect` within 30 s
  of connecting; the backend closes it otherwise (code 1008).
