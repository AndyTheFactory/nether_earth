"""Canonical, JSON-compatible serialization of :class:`~nether_earth.state.GameState`.

This module implements the "Snapshot" half of the "Snapshot and replay
fixtures" workstream (`_specs/milestones/01-deterministic-engine-foundation.md`,
issue #8). It gives regression tests and future backend/frontend fixture
tooling a stable, plain-data view of engine state, independent of the
``GameState`` dataclass's Python representation.

Canonical form convention: :func:`to_snapshot` returns a structure built only
from JSON-safe primitives (``dict``, ``list``, ``str``, ``int``, ``bool``,
``None``) with a fixed, explicit key insertion order. Two dataclass-equal
``GameState`` instances always produce an identical snapshot structure,
because:

- every field is serialized through an explicit, order-fixed sequence of
  ``dict`` insertions (never ``vars()``/``dataclasses.asdict`` on the raw
  dataclass, whose key order is merely "declaration order" and is not a
  contract this module wants to depend on);
- ``players`` is serialized in whatever order ``GameState.players`` already
  holds it in. ``state.py`` guarantees that order is canonical (sorted by
  ``PlayerId.value``) for any two dataclass-equal states, via
  :func:`nether_earth.state.create_game_state`; this module does not re-sort
  it, both to avoid a second, possibly-divergent sort key and to make a
  snapshot faithfully reflect exactly what ``GameState`` stores;
- player ids are serialized via :meth:`~nether_earth.ids.PlayerId.to_json`,
  the existing canonical id->JSON-primitive conversion, rather than a second,
  ad hoc reimplementation.

No generic ``GameState``-from-snapshot deserializer is provided: this issue's
scope is canonical serialization (proving "equivalent states serialize
identically and reproducibly"), not a full round-trip loader. ``replay.py``
does not need one either -- fixtures reconstruct state via
``engine.new_game``/``engine.step``, not by deserializing a snapshot.
"""

from __future__ import annotations

import json
from typing import Any

from nether_earth.state import GameState

__all__ = [
    "snapshot_to_json_string",
    "to_snapshot",
]


def to_snapshot(state: GameState) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of ``state``.

    The result contains only ``dict``/``list``/``str``/``int``/``bool``/
    ``None`` values, with a fixed key insertion order (``tick``, ``players``,
    ``seed``). Two dataclass-equal ``GameState`` instances always produce an
    identical snapshot; two states that differ in any field produce a
    detectably different snapshot.
    """
    return {
        "tick": state.tick,
        "players": [player.to_json() for player in state.players],
        "seed": state.seed,
    }


def snapshot_to_json_string(state: GameState) -> str:
    """Return a stable JSON string form of ``to_snapshot(state)``.

    Uses ``sort_keys=False`` so the fixed insertion order established by
    :func:`to_snapshot` is preserved verbatim in the output string (rather
    than being re-sorted alphabetically, which would still be deterministic
    but would diverge from the dict's own canonical order for no benefit).
    This is a convenience for regression fixtures/tests that want a single
    comparable string rather than a nested structure.
    """
    return json.dumps(to_snapshot(state), sort_keys=False)
