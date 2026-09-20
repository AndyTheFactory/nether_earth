"""WebSocket transport layer (M7 Task 5/6, issues #94/#95).

Public surface: :func:`~app.transport.ws.create_websocket_router` (the
``/ws`` endpoint factory), :class:`~app.transport.connections.ConnectionRegistry`
plus its :func:`~app.transport.connections.broadcast` /
:func:`~app.transport.connections.send_to_player` primitives, and
:func:`~app.transport.snapshots.build_snapshot_message` /
:func:`~app.transport.snapshots.make_tick_broadcaster` (the
``to_snapshot``-to-wire mapping and per-tick broadcast hook). See
``ws.py``'s module docstring for the endpoint design, session-binding rules,
and gameplay-command-conversion scope decisions, and ``snapshots.py``'s for
the snapshot/broadcast policy.
"""

from app.transport.connections import ConnectionRegistry, broadcast, send_to_player
from app.transport.snapshots import build_snapshot_message, make_tick_broadcaster
from app.transport.ws import create_websocket_router

__all__ = [
    "ConnectionRegistry",
    "broadcast",
    "build_snapshot_message",
    "create_websocket_router",
    "make_tick_broadcaster",
    "send_to_player",
]
