"""Engine-state -> protocol-snapshot mapping and the per-tick broadcast hook.

Scope (M7 Task 6, issue #95): a thin field-mapping layer from
``nether_earth.snapshot.to_snapshot`` into
``app.protocol.snapshot.SnapshotMessage`` -- no gameplay value is re-derived
or duplicated here, only wrapped in the protocol envelope. Also builds the
concrete ``app.match.runtime.TickObserver`` callback the transport layer
installs on a match's ``MatchRuntime`` (``runtime.py`` itself never imports
this package; see its module docstring).

Broadcast policy: **snapshot-only** (not event+snapshot or a delta).
``SnapshotState`` is still the Task 3 placeholder
(`dict[str, Any]`/`additionalProperties: true`; full enumeration is issue
#98's job), so there is no stable delta/event shape to diff or encode
against yet -- inventing one now would be exactly the speculative,
likely-to-be-redone work this task's brief asks to avoid. A full snapshot
every tick is also the simplest thing that provably satisfies "a
newly-connected/reconnected client can reconstruct all currently exposed
state," for every client, not just ones that saw every prior tick.
**Engine ``Event``s are therefore never transmitted at all** under this
policy -- "authoritative ordering is preserved" holds vacuously (there is
nothing to reorder), not because events are delivered in order; a future
event/delta policy would need its own ordering proof.

Cost, deliberately not addressed here (YAGNI): full-snapshot-per-tick scales
with `O(state_size x connections)` per tick, and (see `runtime.py`'s
`_advance_one_tick`) the broadcast await sits on the tick loop's own
critical path, so a slow/backpressured connection also delays this match's
simulation cadence, not just that connection's own delivery. Throttling,
diffing, or a per-connection queue are the fixes if profiling ever shows
either cost matters; neither is built speculatively now.

Runtime lifecycle state (active/paused/finished) is never folded into the
snapshot payload -- ``Match.state`` is surfaced by other messages
(``ServerStarted``/``ServerReadyState``/a future pause notification), never
by wrapping or mutating ``to_snapshot``'s output.
"""

from __future__ import annotations

from nether_earth.events import Event
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

from app.match.runtime import TickObserver
from app.protocol.common import PROTOCOL_VERSION, SnapshotState
from app.protocol.snapshot import SnapshotMessage
from app.transport.connections import ConnectionRegistry, broadcast


def build_snapshot_message(match_id: str, state: GameState) -> SnapshotMessage:
    """Return the current authoritative snapshot of ``state`` as a wire message.

    A thin field-mapping layer only: ``to_snapshot(state)``'s return value is
    validated into the matching ``SnapshotState`` model (a structural
    parse, not a value re-derivation -- every field is passed through
    verbatim; no field is picked out, renamed, or recomputed here). Callers
    are responsible for never calling this with a mutated/advanced
    ``GameState`` merely to produce a snapshot (e.g. the reconnect path reads
    ``match.game_state`` as-is; see ``app.transport.ws``).
    """
    return SnapshotMessage(
        protocol_version=PROTOCOL_VERSION,
        type="snapshot",
        match_id=match_id,
        tick=state.tick,
        state=SnapshotState.model_validate(to_snapshot(state)),
    )


def empty_snapshot_message(match_id: str) -> SnapshotMessage:
    """Return a ``tick=0``, empty-state snapshot for a match with no ``GameState`` yet.

    Only correct for a match that has never gone ACTIVE (``game_state is
    None`` -- e.g. a reconnect to a still-``WAITING`` match): there is
    genuinely no authoritative gameplay state to report yet. Kept as its own
    helper so this shape is spelled once rather than duplicated at every
    reconnect/fallback call site. ``state`` is a structurally valid, fully
    empty ``SnapshotState`` (every list field empty, ``seed=0``) now that
    ``SnapshotState`` is a real, strict model rather than an open
    placeholder dict (issue #98) -- an empty ``{}`` no longer validates.
    """
    return SnapshotMessage(
        protocol_version=PROTOCOL_VERSION,
        type="snapshot",
        match_id=match_id,
        tick=0,
        state=SnapshotState(
            tick=0,
            players=[],
            seed=0,
            commanders=[],
            resource_pools=[],
            construction_sessions=[],
            robots=[],
            structure_ownership=[],
            capture_progress=[],
            projectiles=[],
            structure_destruction=[],
        ),
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
