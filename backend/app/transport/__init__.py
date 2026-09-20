"""WebSocket transport layer (M7 Task 5, issue #94).

Public surface: :func:`~app.transport.ws.create_websocket_router` (the
``/ws`` endpoint factory) and :class:`~app.transport.connections.ConnectionRegistry`
plus its :func:`~app.transport.connections.broadcast` /
:func:`~app.transport.connections.send_to_player` primitives. See
``ws.py``'s module docstring for the endpoint design, session-binding rules,
and gameplay-command-conversion scope decisions.
"""

from app.transport.connections import ConnectionRegistry, broadcast, send_to_player
from app.transport.ws import create_websocket_router

__all__ = [
    "ConnectionRegistry",
    "broadcast",
    "create_websocket_router",
    "send_to_player",
]
