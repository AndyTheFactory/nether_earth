"""Concurrent-match load/soak harness for a running backend (M10.6, issue #127).

Drives N real two-player matches over the production WebSocket protocol:
create/join/ready, then each client streams valid commander_move commands
while recording the arrival time of every authoritative snapshot. A
``--churn`` fraction of matches has one player drop and reconnect mid-run.
Afterwards every client leaves, and the harness polls ``/ready`` until the
server has disposed every match (reconnect grace -> no-contest -> finished
retention), proving finished matches release their runtime state.

Needs only ``websockets`` (already a backend runtime dependency). Examples:

  # through the gateway (per-IP limits allow ~15 matches per minute)
  python backend/scripts/soak.py --url ws://localhost/ws --ready-url http://localhost/api/ready \
      --origin http://localhost --matches 8

  # straight at the backend, from a container on the compose network
  docker run --rm --network nether-earth_default -v "$PWD/backend/scripts:/scripts:ro" \
      nether-earth-backend:<version> python /scripts/soak.py --url ws://backend:8000/ws \
      --ready-url http://backend:8000/ready --matches 50 --duration 180
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import websockets

V = 1
TICK_S = 0.05


@dataclass
class ClientStats:
    arrivals: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands_sent: int = 0
    reconnects: int = 0
    resync_latency_s: list[float] = field(default_factory=list)
    unexpected_close: bool = False


@dataclass
class Session:
    match_id: str
    player_id: str
    token: str


class Client:
    def __init__(self, url: str, origin: str | None, stats: ClientStats) -> None:
        self.url = url
        self.origin = origin
        self.stats = stats
        self.ws: Any = None
        self.session: Session | None = None
        self.seq = 0
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self.closing = False

    async def connect(self) -> None:
        self.ws = await websockets.connect(
            self.url, origin=self.origin, max_size=2**22, open_timeout=15  # type: ignore[arg-type]
        )
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "snapshot":
                    self.stats.arrivals.append(time.monotonic())
                elif kind == "error":
                    self.stats.errors.append(message["error"]["code"])
                    await self.inbox.put(message)
                else:
                    await self.inbox.put(message)
        except websockets.ConnectionClosed:
            if not self.closing:
                self.stats.unexpected_close = True

    async def send(self, message: dict[str, Any]) -> None:
        await self.ws.send(json.dumps({"protocolVersion": V, **message}))

    async def expect(self, kind: str, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            message = await asyncio.wait_for(self.inbox.get(), deadline - time.monotonic())
            if message.get("type") == kind:
                return message

    def auth(self) -> dict[str, str]:
        assert self.session is not None
        return {
            "matchId": self.session.match_id,
            "playerId": self.session.player_id,
            "sessionToken": self.session.token,
        }

    async def send_move(self) -> None:
        dx, dy = random.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
        self.seq += 1
        await self.send(
            {
                "type": "command",
                **self.auth(),
                "clientSequence": self.seq,
                "payload": {"kind": "commander_move", "dx": dx, "dy": dy},
            }
        )
        self.stats.commands_sent += 1

    async def drop(self) -> None:
        self.closing = True
        await self.ws.close()
        if self._reader:
            await self._reader

    async def reconnect(self) -> None:
        self.closing = False
        self.inbox = asyncio.Queue()
        started = time.monotonic()
        await self.connect()
        await self.send({"type": "reconnect", **self.auth()})
        await self.expect("resync")
        self.stats.reconnects += 1
        self.stats.resync_latency_s.append(time.monotonic() - started)

    async def leave(self) -> None:
        self.closing = True
        try:
            await self.send({"type": "leave", **self.auth()})
            await asyncio.wait_for(self.ws.wait_closed(), 5)
        except (websockets.ConnectionClosed, TimeoutError, OSError):
            await self.ws.close()


async def run_match(
    index: int, args: argparse.Namespace, a_stats: ClientStats, b_stats: ClientStats, churn: bool
) -> None:
    a = Client(args.url, args.origin, a_stats)
    b = Client(args.url, args.origin, b_stats)
    await a.connect()
    await b.connect()
    await a.send({"type": "create", "nickname": f"soak-a{index}"})
    created = await a.expect("created")
    a.session = Session(created["matchId"], created["playerId"], created["sessionToken"])
    await b.send({"type": "join", "joinCode": created["joinCode"], "nickname": f"soak-b{index}"})
    joined = await b.expect("joined")
    b.session = Session(joined["matchId"], joined["playerId"], joined["sessionToken"])
    for client in (a, b):
        await client.send({"type": "ready", **client.auth(), "ready": True})
    await a.expect("started")
    await b.expect("started")

    end = time.monotonic() + args.duration
    churn_at = time.monotonic() + args.duration / 3 if churn else None
    interval = 1.0 / args.command_rate
    while time.monotonic() < end:
        if churn_at is not None and time.monotonic() >= churn_at:
            churn_at = None
            await b.drop()
            await asyncio.sleep(2.0)
            await b.reconnect()
            continue
        await asyncio.gather(a.send_move(), b.send_move())
        await asyncio.sleep(interval * random.uniform(0.8, 1.2))
    await asyncio.gather(a.leave(), b.leave())


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def _ready(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return dict(json.loads(response.read()))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="WebSocket URL, e.g. ws://localhost/ws")
    parser.add_argument("--ready-url", help="backend /ready URL for server-side counts")
    parser.add_argument("--origin", help="Origin header to send (the public base URL)")
    parser.add_argument("--matches", type=int, default=10)
    parser.add_argument("--duration", type=float, default=120.0, help="seconds of play per match")
    parser.add_argument("--command-rate", type=float, default=10.0, help="commands/s per client")
    parser.add_argument("--churn", type=float, default=0.2, help="fraction of matches that reconnect")
    parser.add_argument("--ramp", type=float, default=0.25, help="seconds between match starts")
    parser.add_argument("--cleanup-timeout", type=float, default=0.0,
                        help="seconds to wait for /ready matches==0 afterwards (0 = skip)")
    args = parser.parse_args()

    random.seed(1)
    churn_count = round(args.matches * args.churn)
    stats = [(ClientStats(), ClientStats()) for _ in range(args.matches)]
    started = time.monotonic()
    tasks = []
    for index in range(args.matches):
        tasks.append(
            asyncio.create_task(run_match(index, args, *stats[index], churn=index < churn_count))
        )
        await asyncio.sleep(args.ramp)
    peak: dict[str, Any] = {}
    if args.ready_url:
        await asyncio.sleep(min(10.0, args.duration / 2))
        peak = _ready(args.ready_url)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [repr(r) for r in results if isinstance(r, BaseException)]

    # Snapshot cadence from matches without churn (a churned match pauses).
    gaps: list[float] = []
    for a_stats, b_stats in stats[churn_count:]:
        for s in (a_stats, b_stats):
            gaps.extend(later - earlier for earlier, later in zip(s.arrivals, s.arrivals[1:]))
    all_clients = [s for pair in stats for s in pair]
    snapshots = sum(len(s.arrivals) for s in all_clients)
    summary: dict[str, Any] = {
        "matches": args.matches,
        "clients": len(all_clients),
        "duration_s": args.duration,
        "command_rate_per_client": args.command_rate,
        "wall_time_s": round(time.monotonic() - started, 1),
        "match_failures": failures,
        "unexpected_closes": sum(s.unexpected_close for s in all_clients),
        "commands_sent": sum(s.commands_sent for s in all_clients),
        "snapshots_received": snapshots,
        "error_codes": sorted({code for s in all_clients for code in s.errors}),
        "reconnects": sum(s.reconnects for s in all_clients),
        "resync_latency_ms_max": round(
            max((x for s in all_clients for x in s.resync_latency_s), default=0.0) * 1000, 1
        ),
        "snapshot_interval_ms": {
            "expected": TICK_S * 1000,
            "mean": round(statistics.fmean(gaps) * 1000, 2) if gaps else None,
            "p50": round(_percentile(gaps, 50) * 1000, 2),
            "p95": round(_percentile(gaps, 95) * 1000, 2),
            "p99": round(_percentile(gaps, 99) * 1000, 2),
            "max": round(max(gaps, default=float("nan")) * 1000, 2),
            "over_100ms": sum(g > 0.1 for g in gaps),
            "samples": len(gaps),
        },
        "server_peak": peak,
    }
    if args.ready_url and args.cleanup_timeout > 0:
        deadline = time.monotonic() + args.cleanup_timeout
        final = _ready(args.ready_url)
        while (final["matches"] or final["runtimes"] or final["connections"]) and (
            time.monotonic() < deadline
        ):
            await asyncio.sleep(5)
            final = _ready(args.ready_url)
        summary["server_after_cleanup"] = final
        summary["cleanup_s"] = round(args.cleanup_timeout - max(0.0, deadline - time.monotonic()), 1)
    print(json.dumps(summary, indent=2))
    ok = not failures and not summary["unexpected_closes"]
    if "server_after_cleanup" in summary:
        leftover = summary["server_after_cleanup"]
        ok = ok and not (leftover["matches"] or leftover["runtimes"] or leftover["connections"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
