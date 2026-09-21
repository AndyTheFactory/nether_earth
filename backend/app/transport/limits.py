"""Per-connection transport bounds (M10.4). Resource limits only, no gameplay rules.

A browser client sends at most ~20 messages/s while a movement key is held
(``frontend/src/input/keyboard.ts`` pulses every 50 ms) plus occasional
discrete actions, so the rate limit below leaves 2x headroom for honest
clients while bounding how much work one socket can push at the server.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: Largest accepted inbound text frame, in UTF-8 bytes. Valid client
#: messages are a few hundred bytes at most. Uvicorn is started with the
#: same ``--ws-max-size``.
MAX_MESSAGE_BYTES = 16 * 1024

#: Sustained inbound messages per second, and the burst allowed on top.
MESSAGE_RATE_PER_S = 40.0
MESSAGE_BURST = 80

#: Failed ``join`` attempts (unknown code / full match) one connection may
#: make before it is closed. With the gateway's per-IP handshake limit this
#: bounds join-code guessing to a few hundred codes per minute per IP.
MAX_FAILED_JOINS = 5

#: A send to one client that cannot complete within this many seconds
#: (peer stopped reading, TCP window full) marks that socket stalled; it is
#: closed so the normal disconnect/pause/grace policy takes over instead of
#: the match's tick loop blocking on it.
SEND_TIMEOUT_S = 5.0


class TokenBucket:
    """Classic token bucket; ``allow()`` spends one token if available."""

    def __init__(
        self,
        rate_per_s: float = MESSAGE_RATE_PER_S,
        burst: int = MESSAGE_BURST,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate_per_s
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._last = clock()

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True
