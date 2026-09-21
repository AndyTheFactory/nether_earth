# M10.6 performance, memory and soak report

Date: 2026-09-21. Harness: `backend/scripts/soak.py`. Stack: the production images and
`deploy/docker-compose.yml` (backend limited to **1.0 CPU / 1 GiB**, the compose default).

## Tested host profile

| Item | Value |
| --- | --- |
| CPU | Intel Xeon E5-2680 v4 @ 2.40 GHz (2016 Broadwell server core); backend capped at 1 CPU |
| Backend | one uvicorn process, uvloop, Python 3.12, 20 Hz fixed tick per match |
| Load per match | 2 clients, each sending 10 `commander_move` commands/s (≈ half the peak rate of a human holding a key), receiving every snapshot (permessage-deflate on) |
| Churn | 20 % of matches: one player drops, reconnects 2 s later (pause → resync → resume) |
| Teardown | all clients `leave` → 60 s reconnect grace → no-contest → finished-match retention (20 s in the test) → disposal |

The soak clients ran in a container on the Compose network against `backend:8000`
(the gateway's per-IP limits would otherwise cap one test machine at ~15 matches/min).
The gateway path itself was exercised with a smaller run (4 matches incl. churn, same result
quality) and by `deploy/smoke.sh`.

## Results

Snapshot interval = time between consecutive authoritative snapshots at a client of a
non-churned match (ideal 50 ms). It captures tick scheduling delay, event-loop lag and
broadcast delay together.

| Concurrent matches | Duration | Backend CPU (avg of busy samples) | p50 | p95 | p99 | max | gaps > 100 ms | Failures / unexpected closes | Reconnects OK | Left after cleanup (matches/runtimes/sockets) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 180 s | 38 % | 50.0 ms | 54.5 ms | 79.9 ms | 139 ms | 0.03 % | 0 / 0 | 2/2 | 0 / 0 / 0 |
| 25 | 180 s | 63 % | 50.1 ms | 61.3 ms | 93.3 ms | 161 ms | 0.4 % | 0 / 0 | 5/5 | 0 / 0 / 0 |
| 40 | 120 s | ~60 % avg, peaks 96 % | 50.1 ms | 62.1 ms | 99.1 ms | 189 ms | 0.9 % | 0 / 0 | 8/8 | 0 / 0 / 0 |
| 25 (repeat) | 180 s | 62 % | 50.0 ms | 62.0 ms | 93.7 ms | 160 ms | 0.3 % | 0 / 0 | 5/5 | 0 / 0 / 0 |

No `error` frames, no `tick_overrun` or `tick_loop_crashed` log events, reconnect resync
under 30 ms, every match/runtime/connection released within ~95 s of the last client leaving
(60 s grace + retention + sweep interval).

### Memory

Backend process RSS: 42 MiB idle; **high-water mark 72 MiB across the whole series**
(including 40 concurrent matches), and back to ~72 MiB RSS after cleanup, identical after a
repeated 25-match run — no growth attributable to matches. `docker stats` shows a rising
total (58 → 185 MiB) because the cgroup also counts page cache for replay files written
to the host mount (`memory.stat`: anon 57 MB, file 134 MB). That cache is reclaimable and
does not threaten the 1 GiB limit. Per-match memory is well under 1 MiB.

## Findings and fixes (all merged in M10.6)

| Finding | Severity | Fix |
| --- | --- | --- |
| Finished matches and abandoned lobbies were **never disposed** (no production caller of `dispose_match`): unbounded memory growth for a long-running process | Critical (leak) | Lifespan sweeper: finished matches after retention (default 300 s), lobbies after 900 s. Verified: counts return to 0 after every run. |
| `engine.step` spent most of its time rebuilding the ownership-overlaid `WorldMap` ~8×/tick and rescanning interaction points (53 % of busy CPU) | Major (capacity) | Pure-function memos in `capture.effective_world` / `capture_footprint`; `engine.step` → 34 %. Determinism tests and the M9 full-match replay-hash regression unchanged. |
| A client that stops reading could block its match's tick loop on `send` | Major (griefing/availability) | 5 s send timeout → socket closed → normal disconnect/grace policy (M10.4). |
| Replay append opened a text file handle every tick | Minor | Raw `O_APPEND` write per line. |
| Broadcast re-serialized each snapshot per recipient | Minor | Serialize once per broadcast. |

Remaining cost centres (per py-spy, 15 matches): engine step ~34 %, snapshot build + send
~29 % (of which permessage-deflate ~11 %: kept, it trades CPU for a much smaller
bandwidth bill), replay append ~10 %.

## Supported v1 operating envelope

- **Per backend process on one core of this class: up to 25 concurrent active matches** with
  p99 snapshot interval < 100 ms and ~35 % CPU headroom. 40 matches still ran without failures
  but at the edge (CPU peaks ~96 %, p99 ≈ 99 ms); beyond that, ticks start arriving late, which
  players experience as slow-down (the simulation never skips or rushes ticks).
- A faster modern core scales this roughly linearly; re-run the harness on the target VPS:

  ```bash
  docker run --rm --network nether-earth_default -v "$PWD/backend/scripts:/scripts:ro" \
    nether-earth-backend:<version> python /scripts/soak.py --url ws://backend:8000/ws \
    --ready-url http://backend:8000/ready --matches 25 --duration 180 --cleanup-timeout 600
  ```

  (Run it off-peak: it creates real matches on the production backend.)
- Adding vCPUs does not raise match capacity (single asyncio process by design); it only gives
  Nginx and the OS room. No horizontal scaling is introduced for v1.
- `NETHER_EARTH_MAX_MATCHES` (default 200) bounds memory and lobby spam, not CPU. On a
  host like the tested one, operators who prefer refusing new matches over slow-downs can set it
  to ~2× the active-match envelope (lobbies and recently finished matches also count).
- Replay disk use: ~1.5 MB per match for ~4 minutes of heavy command traffic (every tick is
  recorded); plan retention per runbook §8.

No unresolved critical performance finding.
