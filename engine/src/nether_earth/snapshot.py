"""Canonical, JSON-compatible serialization of :class:`~nether_earth.state.GameState`.

This module implements the "Snapshot" half of the "Snapshot and replay
fixtures" workstream (`_specs/milestones/01-deterministic-engine-foundation.md`).
It gives regression tests and backend/frontend fixture tooling a stable,
plain-data view of engine state, independent of the ``GameState``
dataclass's Python representation.

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

No generic ``GameState``-from-snapshot deserializer is provided: the
contract is canonical serialization ("equivalent states serialize
identically and reproducibly"), not a full round-trip loader. ``replay.py``
does not need one either -- fixtures reconstruct state via
``engine.new_game``/``engine.step``, not by deserializing a snapshot.

Every authoritative ``GameState`` collection (commanders, robots, structure
ownership, capture progress, projectiles, destruction, debris, ...) is
serialized in whatever order ``GameState`` already holds it in, which
``state.py`` guarantees is canonical. New keys are appended after existing
keys. Two pieces of state are deliberately *not* given snapshot keys of
their own, because they are not authoritative state:

- **Destination reservations.** `reservations.py`'s ``ReservationTable`` is
  a pure projection of ``state.robots`` --
  :func:`~nether_earth.reservations.reservations_from_state` derives exactly
  one entry per robot carrying a non-``None``
  :attr:`~nether_earth.robot.Robot.movement`, keyed by that transition's
  destination -- and is never stored on ``GameState``. Serializing every
  robot's ``movement`` transition (which :func:`_robot_snapshot` already
  does) therefore captures the reservation table losslessly. A second,
  independently serialized ``reservations`` key could only ever agree with
  or *drift from* the transitions it was derived from, so this module does
  not create one.
- **Engagement intent.** `orders.py`'s
  :class:`~nether_earth.orders.EngagementIntent` is recomputed from scratch
  by :func:`~nether_earth.orders.evaluate_orders` on every tick and is
  reported as a :class:`~nether_earth.orders.RobotEngagementIntentEvent`; it
  is stored neither on :class:`~nether_earth.robot.Robot` nor on
  ``GameState``. It is a per-tick derived value, not state, so its
  determinism is a property of the inputs it is derived from (robot
  positions/builds/orders and world ownership -- all of which this module
  serializes) rather than something a snapshot can or should carry.

``"structure_destruction"`` and ``"scenery_debris"`` are plain lists of ids
in canonical order (no per-entry helper, since each entry is a bare id).
``"robot_debris"`` is a list of ``{"x", "y"}`` 2×2 debris anchors in the
order the robots fell, elided while empty like ``robot_launches``.

``"ai_memories"`` is appended last, **only when the match has an AI seat**,
so an all-human state (and every replay fixture recorded from one) has no
such key. :func:`ai_memory_from_snapshot` is the inverse of one entry, the
planner state a reconnect or replay tool would restore. Each sub-planner's
memory has its own serializer pair, so they extend independently.

``"robot_launches"`` is appended last, **only once some player has launched
a robot** (an empty list is elided, so absent means ``[]``). Each entry is
``{"player_id", "launched"}``; the count only grows, so robot ids are never
reused after a robot dies. :func:`robot_launch_count_from_snapshot` is the
inverse of one entry.

A robot's cached Search & Destroy (robots) route,
:attr:`~nether_earth.robot.Robot.hunt_route`, is serialized as the robot's
``"hunt_route"`` key, appended last and **only when set**, the same
elision. :func:`robot_hunt_route_from_snapshot` is its inverse.
"""

from __future__ import annotations

import json
from typing import Any, assert_never

from nether_earth.capture import CaptureProgress, StructureOwnership
from nether_earth.combat import Projectile
from nether_earth.commander import Commander, GridTransition, VerticalTransition
from nether_earth.construction_economy import ResourcePool
from nether_earth.construction_session import BuildInProgress, ConstructionSession
from nether_earth.ids import EntityId, PlayerId
from nether_earth.orders import (
    Advance,
    Order,
    Retreat,
    SearchCapture,
    SearchDestroy,
    StopAndDefend,
)
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.robot import Robot, RobotHuntRoute, RobotMoveTransition, RobotTurnTransition
from nether_earth.robot_build import ModuleIdentity, RobotBuild
from nether_earth.state import (
    AiConstructionMemory,
    AiDefenceAssignment,
    AiMemory,
    AiOrderMemory,
    AiSighting,
    GameState,
    RobotLaunchCount,
)
from nether_earth.structures import FactoryType

__all__ = [
    "ai_memory_from_snapshot",
    "robot_hunt_route_from_snapshot",
    "robot_launch_count_from_snapshot",
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

    New keys are appended after existing keys so any snapshot-shape test
    that checks key order can be extended additively rather than reshuffled.
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
        "elevate_updates_remaining": commander.elevate_updates_remaining,
    }


#: Deterministic declaration order for a serialized resource pool's
#: type-specific category keys, matching ``FactoryType``'s own declaration
#: order in ``structures.py`` -- the one place this ordering is defined for
#: this module.
_CATEGORY_ORDER: tuple[FactoryType, ...] = tuple(FactoryType)


def _player_resource_pool_snapshot(pool: PlayerResourcePool) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``PlayerResourcePool``."""
    return {
        "player_id": pool.player_id.to_json(),
        "general": pool.general,
        "chassis": pool.chassis,
        "electronics": pool.electronics,
        "nuclear": pool.nuclear,
        "missile": pool.missile,
        "phaser": pool.phaser,
        "cannon": pool.cannon,
    }


def _resource_pool_snapshot(pool: ResourcePool) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a ``construction_economy.ResourcePool``.

    Used for a :class:`~nether_earth.construction_session.ConstructionSession`'s
    session-local ``buffer``/``entry_snapshot`` fields, which are this
    (unhashable, ``Mapping``-backed) type rather than the authoritative
    :class:`~nether_earth.resource_pool.PlayerResourcePool` -- see
    ``construction_session.py``'s module docstring. ``category`` is always
    serialized in :data:`_CATEGORY_ORDER` (matching ``FactoryType``'s own
    declaration order), independent of the pool's internal mapping's
    iteration order.
    """
    return {
        "general": pool.general,
        "category": {category.value: pool.amount(category) for category in _CATEGORY_ORDER},
    }


def _module_identity_json(module: ModuleIdentity | None) -> str | None:
    """Return ``module``'s JSON-safe value, or ``None``."""
    return module.value if module is not None else None


def _build_in_progress_snapshot(build: BuildInProgress) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``BuildInProgress``."""
    return {
        "chassis": _module_identity_json(build.chassis),
        "weapons": [weapon.value for weapon in build.weapons],
        "electronics": _module_identity_json(build.electronics),
    }


def _robot_build_snapshot(build: RobotBuild) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single, complete ``RobotBuild``."""
    return {
        "chassis": build.chassis.value,
        "weapons": [weapon.value for weapon in build.weapons],
        "electronics": _module_identity_json(build.electronics),
    }


def _construction_session_snapshot(session: ConstructionSession) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``ConstructionSession``."""
    return {
        "player_id": session.player_id.to_json(),
        "war_base_id": session.war_base_id.to_json(),
        "entry_tick": session.entry_tick,
        "build": _build_in_progress_snapshot(session.build),
        "buffer": _resource_pool_snapshot(session.buffer),
        "entry_snapshot": _resource_pool_snapshot(session.entry_snapshot),
    }


def _robot_move_transition_snapshot(
    transition: RobotMoveTransition | None,
) -> dict[str, Any] | None:
    """Return a canonical, JSON-safe snapshot of a ``RobotMoveTransition``, or ``None``."""
    if transition is None:
        return None
    return {
        "entity_id": transition.entity_id.to_json(),
        "from_x": transition.from_x,
        "from_y": transition.from_y,
        "to_x": transition.to_x,
        "to_y": transition.to_y,
        "started_tick": transition.started_tick,
        "duration_ticks": transition.duration_ticks,
    }


def _robot_turn_transition_snapshot(
    turn: RobotTurnTransition | None,
) -> dict[str, Any] | None:
    """Return a canonical, JSON-safe snapshot of a ``RobotTurnTransition``, or ``None``."""
    if turn is None:
        return None
    return {
        "entity_id": turn.entity_id.to_json(),
        "from_facing": turn.from_facing.value,
        "to_facing": turn.to_facing.value,
        "started_tick": turn.started_tick,
        "duration_ticks": turn.duration_ticks,
    }


def _order_snapshot(order: Order | None) -> dict[str, Any] | None:
    """Return a canonical, JSON-safe snapshot of a robot's ``Order``, or ``None``.

    :data:`~nether_earth.orders.Order` is a union of five plain value types
    with no shared base class and no discriminator field of their own, so
    this function tags each member with a stable ``kind`` token. The tokens
    are spelled here rather than added to `orders.py` because they are a
    property of *this* module's serialization contract (the one place the
    engine converts state to plain data), exactly as :data:`_CATEGORY_ORDER`
    encodes the serialized category ordering here rather than in
    `structures.py`. Bound goal state (``Advance``/``Retreat``'s
    ``target_x``) is serialized as-is: it is authoritative -- an in-flight
    ``Advance`` that has already bound its goal column is a different state
    from a freshly assigned one, and `orders.py`'s ``PENDING`` -> ``ACTIVE``
    transition is exactly that difference. ``SearchCapture``'s stored
    ``structure_id`` is serialized for the same reason.
    """
    if order is None:
        return None
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
    assert_never(order)


def _robot_snapshot(robot: Robot) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``Robot``.

    Every per-robot authoritative field is serialized, new keys appended
    last. Dropping any of them would break a snapshot/restore round-trip:
    ``movement`` (an in-flight move), ``order`` (``StopAndDefend`` vs. a
    ``SearchCapture`` in progress), ``active_projectile_id`` (an occupied
    combat channel), ``strength`` (accumulated damage), ``last_fire_tick``
    (the one-shot-per-game-cycle fire rule), ``exit_steps_remaining`` (a
    launch walk-out), ``turning`` (a robot mid-turn neither moves nor fires)
    and ``facing`` (presentation-only, picks one of the four per-piece
    Spectrum sprites; see :class:`~nether_earth.robot.RobotFacing`; without
    it a restored robot would face south again).

    ``hunt_route`` is appended last and **only when the robot holds one**
    (absent means ``None``), so snapshots without a hunter stay
    byte-identical (no ``hunt_route`` key). See :func:`_robot_hunt_route_snapshot` for its
    compact form.
    """
    snapshot: dict[str, Any] = {
        "entity_id": robot.entity_id.to_json(),
        "owner": robot.owner.to_json(),
        "x": robot.x,
        "y": robot.y,
        "build": _robot_build_snapshot(robot.build),
        "stack": [module.value for module in robot.stack],
        "height": robot.height,
        "movement": _robot_move_transition_snapshot(robot.movement),
        "order": _order_snapshot(robot.order),
        "active_projectile_id": (
            robot.active_projectile_id.to_json()
            if robot.active_projectile_id is not None
            else None
        ),
        "strength": robot.strength,
        "last_fire_tick": robot.last_fire_tick,
        "exit_steps_remaining": robot.exit_steps_remaining,
        "facing": robot.facing.value,
        "turning": _robot_turn_transition_snapshot(robot.turning),
    }
    if robot.hunt_route is not None:
        snapshot["hunt_route"] = _robot_hunt_route_snapshot(robot.hunt_route)
    return snapshot


def _robot_hunt_route_snapshot(route: RobotHuntRoute) -> dict[str, Any]:
    """Return a robot's cached hunt route as JSON-safe data.

    The route is a string of one direction letter per step from the origin
    rather than a list of cells: a route across the 512-wide map is a few
    hundred bytes instead of a few kilobytes, and the entry only changes on
    a re-plan, since the robot's progress is its own position on it.
    """
    return {
        "target_id": route.target_id.to_json(),
        "planned_tick": route.planned_tick,
        "origin_x": route.origin_x,
        "origin_y": route.origin_y,
        "steps": route.steps,
    }


def robot_hunt_route_from_snapshot(data: dict[str, Any]) -> RobotHuntRoute:
    """Rebuild a robot snapshot's ``"hunt_route"`` entry into a :class:`RobotHuntRoute`."""
    return RobotHuntRoute(
        target_id=EntityId.from_json(data["target_id"]),
        planned_tick=data["planned_tick"],
        origin_x=data["origin_x"],
        origin_y=data["origin_y"],
        steps=data["steps"],
    )


def _structure_ownership_snapshot(record: StructureOwnership) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``StructureOwnership``."""
    return {
        "structure_id": record.structure_id.to_json(),
        "owner": record.owner.to_json(),
    }


def _capture_progress_snapshot(progress: CaptureProgress) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``CaptureProgress``.

    ``required_ticks`` is carried on the record itself (see
    `capture.py`), so an in-progress capture serializes self-describingly
    without the snapshot also having to carry the rule set that produced it.
    """
    return {
        "structure_id": progress.structure_id.to_json(),
        "capturing_player": progress.capturing_player.to_json(),
        "robot_id": progress.robot_id.to_json(),
        "elapsed_ticks": progress.elapsed_ticks,
        "required_ticks": progress.required_ticks,
    }


def _projectile_snapshot(projectile: Projectile) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of a single ``Projectile``.

    Serializes every field ``combat.Projectile`` carries, matching this
    module's "every stored field gets a snapshot key" convention.
    """
    return {
        "id": projectile.id.to_json(),
        "owner": projectile.owner.to_json(),
        "source_robot_id": projectile.source_robot_id.to_json(),
        "weapon": projectile.weapon.value,
        "x": projectile.x,
        "y": projectile.y,
        "z": projectile.z,
        "dx": projectile.dx,
        "dy": projectile.dy,
        "travelled_cells": projectile.travelled_cells,
        "max_range_cells": projectile.max_range_cells,
        "created_tick": projectile.created_tick,
        "first_advance_tick": projectile.first_advance_tick,
    }


def _ai_construction_memory_snapshot(memory: AiConstructionMemory) -> dict[str, Any]:
    """Return the construction sub-planner's memory as JSON-safe data."""
    last = memory.last_war_base_id
    return {"last_war_base_id": None if last is None else last.to_json()}


def _ai_construction_memory_from_snapshot(data: dict[str, Any]) -> AiConstructionMemory:
    """Inverse of :func:`_ai_construction_memory_snapshot`."""
    last = data["last_war_base_id"]
    return AiConstructionMemory(last_war_base_id=None if last is None else EntityId.from_json(last))


def _ai_order_memory_snapshot(memory: AiOrderMemory) -> dict[str, Any]:
    """Return the robot-order sub-planner's memory as JSON-safe data."""
    return {
        "defences": [
            {
                "defender_id": entry.defender_id.to_json(),
                "intruder_id": entry.intruder_id.to_json(),
                "structure_id": entry.structure_id.to_json(),
                "approached": entry.approached,
            }
            for entry in memory.defences
        ],
        "sightings": [
            {"robot_id": entry.robot_id.to_json(), "distance": entry.distance}
            for entry in memory.sightings
        ],
    }


def _ai_order_memory_from_snapshot(data: dict[str, Any]) -> AiOrderMemory:
    """Inverse of :func:`_ai_order_memory_snapshot`."""
    return AiOrderMemory(
        defences=tuple(
            AiDefenceAssignment(
                defender_id=EntityId.from_json(entry["defender_id"]),
                intruder_id=EntityId.from_json(entry["intruder_id"]),
                structure_id=EntityId.from_json(entry["structure_id"]),
                approached=entry.get("approached", False),
            )
            for entry in data.get("defences", [])
        ),
        sightings=tuple(
            AiSighting(robot_id=EntityId.from_json(entry["robot_id"]), distance=entry["distance"])
            for entry in data.get("sightings", [])
        ),
    )


def _ai_memory_snapshot(memory: AiMemory) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of one AI seat's :class:`AiMemory`."""
    return {
        "player_id": memory.player_id.to_json(),
        "construction": _ai_construction_memory_snapshot(memory.construction),
        "orders": _ai_order_memory_snapshot(memory.orders),
    }


def ai_memory_from_snapshot(data: dict[str, Any]) -> AiMemory:
    """Rebuild one ``"ai_memories"`` entry of :func:`to_snapshot` into an :class:`AiMemory`."""
    return AiMemory(
        player_id=PlayerId.from_json(data["player_id"]),
        construction=_ai_construction_memory_from_snapshot(data["construction"]),
        orders=_ai_order_memory_from_snapshot(data["orders"]),
    )


def _robot_launch_count_snapshot(count: RobotLaunchCount) -> dict[str, Any]:
    return {"player_id": count.player_id.to_json(), "launched": count.launched}


def robot_launch_count_from_snapshot(data: dict[str, Any]) -> RobotLaunchCount:
    """Rebuild one ``"robot_launches"`` entry of :func:`to_snapshot`."""
    return RobotLaunchCount(
        player_id=PlayerId.from_json(data["player_id"]), launched=data["launched"]
    )


def to_snapshot(state: GameState) -> dict[str, Any]:
    """Return a canonical, JSON-safe snapshot of ``state``.

    The result contains only ``dict``/``list``/``str``/``int``/``bool``/
    ``None`` values, with a fixed key insertion order (``tick``, ``players``,
    ``seed``, ``commanders``, ``resource_pools``, ``construction_sessions``,
    ``robots``, ``structure_ownership``, ``capture_progress``). Two
    dataclass-equal ``GameState`` instances always produce an identical
    snapshot; two states that differ in any field produce a detectably
    different snapshot.

    Later keys (``resource_pools``, ``construction_sessions``, ``robots``,
    ``structure_ownership``, ``capture_progress``, ``projectiles``,
    ``structure_destruction``, ``scenery_debris``) are each appended after
    the existing keys so any snapshot-shape test can be extended
    additively. Each is serialized in whatever order ``GameState`` already
    holds it in (canonical per ``state.py``'s own ordering guarantees), not
    re-sorted by this module. ``scenery_debris`` holds the ids of map
    blockers a nuclear blast turned into rough debris.

    ``ai_memories``: appended last, and only when the state has an AI seat
    (see the module docstring).

    ``robot_launches``: appended last, and only once a robot has been
    launched (see the module docstring).

    Every field of ``GameState`` is serialized; see the module docstring
    for why reservations and engagement intent correctly have no keys of
    their own.
    """
    snapshot: dict[str, Any] = {
        "tick": state.tick,
        "players": [player.to_json() for player in state.players],
        "seed": state.seed,
        "commanders": [_commander_snapshot(commander) for commander in state.commanders],
        "resource_pools": [_player_resource_pool_snapshot(pool) for pool in state.resource_pools],
        "construction_sessions": [
            _construction_session_snapshot(session) for session in state.construction_sessions
        ],
        "robots": [_robot_snapshot(robot) for robot in state.robots],
        "structure_ownership": [
            _structure_ownership_snapshot(record) for record in state.structure_ownership
        ],
        "capture_progress": [
            _capture_progress_snapshot(progress) for progress in state.capture_progress
        ],
        "projectiles": [
            _projectile_snapshot(projectile) for projectile in state.projectiles
        ],
        "structure_destruction": [
            structure_id.to_json() for structure_id in state.structure_destruction
        ],
        "scenery_debris": [blocker_id.to_json() for blocker_id in state.scenery_debris],
    }
    if state.ai_memories:
        # Elided for an all-human match: see the module docstring.
        snapshot["ai_memories"] = [_ai_memory_snapshot(memory) for memory in state.ai_memories]
    if state.robot_launches:
        # Elided until the first launch: see the module docstring.
        snapshot["robot_launches"] = [
            _robot_launch_count_snapshot(count) for count in state.robot_launches
        ]
    if state.robot_debris:
        # Elided until a robot leaves debris, like robot_launches.
        snapshot["robot_debris"] = [{"x": x, "y": y} for x, y in state.robot_debris]
    return snapshot


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
