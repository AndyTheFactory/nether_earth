"""Lossless `nether_earth.orders.Order` <-> JSON mapping, shared by writer/verify.

`Order` is a five-member union with no shared base class and no
discriminator field of its own (see `orders.py`'s own module docstring), so
-- exactly like `nether_earth.snapshot`'s private `_order_snapshot` -- a
`kind` tag is added at the serialization boundary. This module's tokens
match `_order_snapshot`'s exactly (`"stop_and_defend"`, `"advance"`, ...) so
a persisted `SetRobotOrderCommand.order` and a snapshot's own `order` field
are always spelled identically on disk/wire.

Kept separate from both `nether_earth.snapshot` (an engine module; this is
backend-only, `app.replay`) and from `app.protocol.common`'s
`RobotOrderPayload` (a pydantic wire model; this needs a plain
dict-in/dict-out shape for JSON-Lines replay records, not a validated
transport envelope) -- see `writer.py`'s `_command_to_json` and
`verify.py`'s `_command_from_json`, its only two call sites.
"""

from __future__ import annotations

from typing import Any

from nether_earth.ids import EntityId
from nether_earth.orders import (
    Advance,
    Order,
    Retreat,
    SearchCapture,
    SearchCaptureTarget,
    SearchDestroy,
    SearchDestroyTarget,
    StopAndDefend,
)

__all__ = ["order_from_json", "order_to_json"]


def order_to_json(order: Order) -> dict[str, Any]:
    """Return a lossless, JSON-safe representation of `order`."""
    if isinstance(order, StopAndDefend):
        return {"kind": "stop_and_defend"}
    if isinstance(order, Advance):
        return {
            "kind": "advance",
            "distance_miles": order.distance_miles,
            "target_x": order.target_x,
        }
    if isinstance(order, Retreat):
        return {
            "kind": "retreat",
            "distance_miles": order.distance_miles,
            "target_x": order.target_x,
        }
    if isinstance(order, SearchCapture):
        return {
            "kind": "search_capture",
            "target": order.target.value,
            "structure_id": (
                order.structure_id.to_json() if order.structure_id is not None else None
            ),
        }
    if isinstance(order, SearchDestroy):
        return {"kind": "search_destroy", "target": order.target.value}
    raise AssertionError(  # pragma: no cover - exhaustive over a closed union
        f"unhandled Order variant: {order!r}"
    )


def order_from_json(data: dict[str, Any]) -> Order:
    """Inverse of :func:`order_to_json`."""
    kind = data["kind"]
    if kind == "stop_and_defend":
        return StopAndDefend()
    if kind == "advance":
        return Advance(distance_miles=data["distance_miles"], target_x=data["target_x"])
    if kind == "retreat":
        return Retreat(distance_miles=data["distance_miles"], target_x=data["target_x"])
    if kind == "search_capture":
        # ``structure_id`` (CR003.2) is absent from records written before it.
        structure_id = data.get("structure_id")
        return SearchCapture(
            target=SearchCaptureTarget(data["target"]),
            structure_id=EntityId.from_json(structure_id) if structure_id is not None else None,
        )
    if kind == "search_destroy":
        return SearchDestroy(target=SearchDestroyTarget(data["target"]))
    raise ValueError(f"unknown Order kind: {kind!r}")
