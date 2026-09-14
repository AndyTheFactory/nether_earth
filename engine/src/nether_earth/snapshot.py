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

Commanders (added by issue #37): each entry of ``GameState.commanders`` is
serialized to a JSON-safe ``dict`` via :func:`_commander_snapshot`, in
whatever order ``GameState.commanders`` already holds it in -- ``state.py``
guarantees that order is canonical (sorted by ``player_id.value``) for any
two dataclass-equal states, matching the ``players`` convention documented
above.
"""

from __future__ import annotations

import json
from typing import Any

from nether_earth.commander import Commander, GridTransition, VerticalTransition
from nether_earth.state import GameState

__all__ = [
    "snapshot_to_json_string",
    "to_snapshot",
]


def _grid_transition_snapshot(transition: GridTransition | None) -> dict[str, Any] | None:
    """Return a canonical, JSON-safe snapshot of a single ``GridTransition``, or ``None``."""
    if transition is None:
        return None
    return {
        "from_x": transition.from_x,
        "from_y": transition.from_y,
        "to_x": transition.to_x,
        "to_y": transition.to_y,
        "started_tick": transition.started_tick,
        "duration_ticks": transition.duration_ticks,
    }


def _vertical_transition_snapshot(
    transition: VerticalTransition | None,
) -> dict[str, Any] | None:
    """Return a canonical, JSON-safe snapshot of a single ``VerticalTransition``, or ``None``."""
    if transition is None:
        return None
    return {
        "from_altitude": transition.from_altitude,
        "to_altitude": transition.to_altitude,
        "started_tick": transition.started_tick,
        "duration_ticks": transition.duration_ticks,
    }


def _commander_snapshot(commander: Commander) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``Commander``.

    Extended by issue #42 (M3.6) to also serialize the movement fields added
    by issue #38 (``rising``, ``horizontal_transition``, ``vertical_transition``)
    -- new keys are appended after the existing #37 keys so any existing
    snapshot-shape test that checks key order can be extended additively
    rather than reshuffled.
    """
    return {
        "player_id": commander.player_id.to_json(),
        "mode": commander.mode.value,
        "x": commander.x,
        "y": commander.y,
        "altitude": commander.altitude,
        "docked_robot_id": (
            commander.docked_robot_id.to_json() if commander.docked_robot_id is not None else None
        ),
        "rising": commander.rising,
        "horizontal_transition": _grid_transition_snapshot(commander.horizontal_transition),
        "vertical_transition": _vertical_transition_snapshot(commander.vertical_transition),
    }


def to_snapshot(state: GameState) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of ``state``.

    The result contains only ``dict``/``list``/``str``/``int``/``bool``/
    ``None`` values, with a fixed key insertion order (``tick``, ``players``,
    ``seed``, ``commanders``). Two dataclass-equal ``GameState`` instances
    always produce an identical snapshot; two states that differ in any
    field produce a detectably different snapshot.
    """
    return {
        "tick": state.tick,
        "players": [player.to_json() for player in state.players],
        "seed": state.seed,
        "commanders": [_commander_snapshot(commander) for commander in state.commanders],
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
