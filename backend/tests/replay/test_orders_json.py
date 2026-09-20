"""Tests for ``app.replay.orders_json`` (M7 Task 9, issue #98).

M7 Task 9 review, Minor M6: ``Order`` serialization now lives in three
places -- the engine's own private ``nether_earth.snapshot._order_snapshot``,
this module's ``order_to_json``/``order_from_json``, and
``app.transport.commands``'s payload adapter -- with no single shared
implementation (each has a different job: engine-internal snapshot
serialization, backend replay persistence, and transport payload
conversion, respectively). This test closes the drift risk between the
first two directly: for every one of the five ``Order`` variants,
``order_to_json`` must produce exactly what ``_order_snapshot`` would, so a
persisted command's ``order`` field and a snapshot's own ``order`` field are
always spelled identically for the same logical order.
"""

from __future__ import annotations

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
from nether_earth.snapshot import _order_snapshot

from app.replay.orders_json import order_from_json, order_to_json

_ORDER_VARIANTS: tuple[Order, ...] = (
    StopAndDefend(),
    Advance(distance_miles=10),
    Advance(distance_miles=10, target_x=42),
    Retreat(distance_miles=5),
    Retreat(distance_miles=5, target_x=3),
    SearchCapture(target=SearchCaptureTarget.NEUTRAL_FACTORY),
    SearchCapture(target=SearchCaptureTarget.ENEMY_FACTORY),
    SearchCapture(target=SearchCaptureTarget.ENEMY_WAR_BASE),
    SearchDestroy(target=SearchDestroyTarget.ROBOT),
    SearchDestroy(target=SearchDestroyTarget.FACTORY),
    SearchDestroy(target=SearchDestroyTarget.WAR_BASE),
)


def test_order_to_json_matches_the_engines_own_snapshot_serialization() -> None:
    for order in _ORDER_VARIANTS:
        assert order_to_json(order) == _order_snapshot(order)


def test_order_json_round_trips_for_every_variant() -> None:
    for order in _ORDER_VARIANTS:
        assert order_from_json(order_to_json(order)) == order
