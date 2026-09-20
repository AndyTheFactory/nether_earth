"""Engine-state -> protocol-snapshot mapping and the per-tick broadcast hook.

Scope (M7 Task 6, issue #95): a thin field-mapping layer from
``nether_earth.snapshot.to_snapshot`` (the engine's own canonical, JSON-safe
serialization of ``GameState``) into ``app.protocol.snapshot.SnapshotMessage``
-- this module never re-derives or duplicates a gameplay value the engine
already computed, it only wraps ``to_snapshot``'s output in the protocol
envelope. It also builds the concrete ``app.match.runtime.TickObserver``
callback the transport layer installs on a match's ``MatchRuntime`` so every
completed tick gets broadcast to that match's connections -- this is the
"concrete implementation" half of the observer hook ``runtime.py``
deliberately stays ignorant of (that module must never import from this
package; see its module docstring).

Broadcast policy (documented choice, per the task brief -- pick one of
snapshot-only / event+snapshot / a deterministic delta): **snapshot-only**.
``SnapshotState`` is still the Task 3 placeholder (`dict[str, Any]`,
`additionalProperties: true`; full field enumeration is issue #98's job), not
a delta-shaped type, so a partial/event-based wire format has nothing stable
to diff against yet -- sending a delta or a separate event stream today would
mean inventing an ad hoc, currently-undocumented event wire shape ahead of
issue #98, which is exactly the kind of unscoped/likely-to-be-redone work
this task's brief asks to avoid. A full snapshot every tick is also the
simplest thing that provably satisfies this task's actual acceptance
criterion ("a newly connected/reconnected client can reconstruct all
currently exposed authoritative state from the snapshot") for every
connected client, not just ones that received every prior tick's message
uninterrupted. Cost/perf note (intentionally not addressed by this task,
YAGNI): broadcasting a full snapshot at 20 Hz to every connection scales
with `O(match_state_size * connections)` per tick; if profiling ever shows
this to be a bottleneck, throttling/diffing/delta-encoding is the fix, but
building that speculatively now would be exactly the kind of infrastructure
the brief says not to build without it being asked for.

Runtime lifecycle state (active/paused/finished) is intentionally never
folded into this module's snapshot payload -- ``Match.state``
(``MatchRuntimeState``) is surfaced to clients by other messages
(``ServerStarted``/``ServerReadyState``/future pause notifications), not by
mutating or wrapping ``to_snapshot``'s output, which stays exactly what the
engine considers deterministic gameplay state.
"""

from __future__ import annotations

from nether_earth.events import Event
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

from app.match.runtime import TickObserver
from app.protocol.common import PROTOCOL_VERSION
from app.protocol.snapshot import SnapshotMessage
from app.transport.connections import ConnectionRegistry, broadcast


def build_snapshot_message(match_id: str, state: GameState) -> SnapshotMessage:
    """Return the current authoritative snapshot of ``state`` as a wire message.

    A thin field-mapping layer only: ``state`` (``to_snapshot(state)``'s
    return value) is passed through verbatim as the envelope's ``state``
    payload -- no field is picked out, renamed, or recomputed here. Callers
    are responsible for never calling this with a mutated/advanced
    ``GameState`` merely to produce a snapshot (e.g. the reconnect path reads
    ``match.game_state`` as-is; see ``app.transport.ws``).
    """
    return SnapshotMessage(
        protocol_version=PROTOCOL_VERSION,
        type="snapshot",
        match_id=match_id,
        tick=state.tick,
        state=to_snapshot(state),
    )


def make_tick_broadcaster(connection_registry: ConnectionRegistry, match_id: str) -> TickObserver:
    """Return a ``TickObserver`` that broadcasts a fresh snapshot to ``match_id``.

    This is the concrete implementation the transport layer supplies to
    ``MatchManager``'s ``on_tick_factory`` (see ``app.match.manager`` and
    ``app.main``) so ``app.match.runtime`` never has to know ``broadcast()``
    or ``ConnectionRegistry`` exist. ``match_id`` is bound at match-start
    time via this closure, matching ``TickObserver``'s own
    ``(GameState, events) -> Awaitable[None]`` shape (no ``match_id``
    parameter -- one ``MatchRuntime`` only ever ticks one match, so a single
    bound closure per match is simpler than threading ``match_id`` through
    every call).
    """

    async def _on_tick(state: GameState, events: tuple[Event, ...]) -> None:
        del events  # snapshot-only broadcast policy; see module docstring.
        message = build_snapshot_message(match_id, state)
        await broadcast(connection_registry, match_id, message)

    return _on_tick
