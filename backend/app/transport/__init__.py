"""WebSocket transport layer.

Public surface: :func:`~app.transport.ws.create_websocket_router` (the
``/ws`` endpoint factory) and :class:`~app.transport.connections.ConnectionRegistry`
plus its :func:`~app.transport.connections.broadcast` /
:func:`~app.transport.connections.send_to_player` primitives. See
``ws.py`` for the endpoint design and receive loop, ``handlers.py`` for the
session-binding and per-message dispatch rules, and ``connection.py`` for
per-socket state. ``app.transport.snapshots`` is imported directly from its
own submodule by callers (``handlers.py``, ``app.main``) rather than
re-exported here.
"""

from app.transport.connections import ConnectionRegistry, broadcast, send_to_player
from app.transport.ws import create_websocket_router

__all__ = [
    "ConnectionRegistry",
    "broadcast",
    "create_websocket_router",
    "send_to_player",
]
