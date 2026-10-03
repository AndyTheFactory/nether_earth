"""Replay-verification helper: reconstruct a persisted artifact and re-run it through the engine directly.

Scope: given a completed ``ReplayWriter`` artifact,
reconstruct the accepted command stream from ``commands.jsonl`` *only*, feed
it to ``nether_earth.engine`` exactly the way
``nether_earth.replay.run_fixture`` already does for engine-side test
fixtures, and compare the reproduced final ``GameState`` snapshot against the
one ``ReplayWriter.finish_match`` persisted alongside the live match. This
directly proves the acceptance criterion "replaying persisted gameplay input
reproduces the final engine snapshot/result" -- and it does so by calling
``nether_earth.engine``/``nether_earth.replay`` directly, never by re-running
any backend/transport code, so it also proves the backend layer added no
gameplay logic of its own for this to depend on.

This module is backend-only, like the rest of ``app.replay``: it imports
``nether_earth.replay``/``nether_earth.snapshot`` (backend -> engine), never
the reverse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nether_earth.combat import FireCommand
from nether_earth.commander_movement import (
    CommanderMoveCommand,
    CommanderSetVerticalIntentCommand,
)
from nether_earth.commands import Command
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    DeselectModuleCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.direct_control import DirectRobotMoveCommand
from nether_earth.ids import EntityId, PlayerId
from nether_earth.map import BootstrapMap, WorldMap
from nether_earth.orders import SetRobotOrderCommand
from nether_earth.replay import ReplayFixture, run_fixture, run_from_state
from nether_earth.robot_build import ModuleIdentity
from nether_earth.rules import RULES_VERSION, rules_content_hash
from nether_earth.scenario import Scenario, create_initial_state, default_pvp_scenario
from nether_earth.snapshot import to_snapshot

from app.replay.orders_json import order_from_json
from app.replay.writer import match_dir, meta_seat_controllers

__all__ = [
    "ReplayRulesMismatchError",
    "ReplayVerificationResult",
    "check_rules_identity",
    "load_commands_by_tick",
    "load_meta",
    "verify_replay",
]

#: Seat controllers for an artifact whose ``meta.json`` has no
#: ``seat_controllers``: every seat is human.
_ALL_HUMAN_SEAT_CONTROLLERS = meta_seat_controllers(default_pvp_scenario())


def load_meta(base_dir: Path, match_id: str) -> dict[str, Any]:
    """Return the parsed ``meta.json`` for ``match_id``'s artifact under ``base_dir``."""
    path = match_dir(base_dir, match_id) / "meta.json"
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    return loaded  # type: ignore[no-any-return]


class ReplayRulesMismatchError(ValueError):
    """The artifact was recorded under different engine rules than the running engine's.

    Raised before any replay runs: a replay recorded under other rules cannot
    be expected to reproduce, so a mismatch is reported as such rather than as
    a snapshot divergence.
    """


def check_rules_identity(meta: dict[str, Any]) -> None:
    """Raise :class:`ReplayRulesMismatchError` unless ``meta``'s rules match the running engine's."""
    recorded_version = meta.get("rules_version")
    recorded_hash = meta.get("rules_hash")
    if recorded_version != RULES_VERSION:
        raise ReplayRulesMismatchError(
            f"replay rules version {recorded_version!r} does not match "
            f"engine rules version {RULES_VERSION!r}"
        )
    engine_hash = rules_content_hash()
    if recorded_hash != engine_hash:
        raise ReplayRulesMismatchError(
            f"replay rules hash {recorded_hash!r} does not match engine rules hash "
            f"{engine_hash!r} (rules version {RULES_VERSION!r})"
        )


#: The nine persisted ``kind`` tokens this function knows how to reconstruct,
#: mirroring ``writer.py``'s ``_COMMAND_KIND_BY_CLASS`` keys. Used only for
#: the "unknown kind" error message; the actual reconstruction below
#: dispatches on the string ``kind`` directly (one explicit branch per
#: token, each naming its own field set) rather than resolving a class first
#: and re-dispatching on it, so a type checker can verify each branch's own
#: constructor call against that exact class's fields instead of an ``in (A, B)``
#: grouping it cannot see through.
_KNOWN_COMMAND_KINDS = frozenset(
    {
        "commander_move",
        "commander_set_vertical_intent",
        "direct_robot_move",
        "robot_fire",
        "set_robot_order",
        "select_module",
        "deselect_module",
        "cancel_construction",
        "launch_robot",
    }
)


def _command_from_json(data: dict[str, Any]) -> Command:
    """Inverse of ``ReplayWriter``'s ``_command_to_json``.

    A record with no ``kind`` key is the bare ``Command`` contract; one of the nine
    known ``kind`` tokens reconstructs the matching concrete gameplay
    ``Command`` subclass with its own fields. Raises ``ValueError`` for an
    unrecognized ``kind`` -- a persisted artifact from a newer schema this
    version does not know how to replay, never silently coerced to a
    different command.
    """
    player = PlayerId.from_json(data["player"])
    sequence = data["sequence"]
    kind = data.get("kind")
    if kind is None:
        return Command(player=player, sequence=sequence)

    if kind == "commander_move":
        return CommanderMoveCommand(player=player, sequence=sequence, dx=data["dx"], dy=data["dy"])
    if kind == "commander_set_vertical_intent":
        return CommanderSetVerticalIntentCommand(
            player=player, sequence=sequence, rising=data["rising"]
        )
    if kind == "direct_robot_move":
        return DirectRobotMoveCommand(
            player=player, sequence=sequence, dx=data["dx"], dy=data["dy"]
        )
    if kind == "robot_fire":
        return FireCommand(
            player=player,
            sequence=sequence,
            entity_id=EntityId.from_json(data["entity_id"]),
            weapon=ModuleIdentity(data["weapon"]),
        )
    if kind == "set_robot_order":
        return SetRobotOrderCommand(
            player=player,
            sequence=sequence,
            entity_id=EntityId.from_json(data["entity_id"]),
            order=order_from_json(data["order"]),
        )
    if kind == "select_module":
        return SelectModuleCommand(
            player=player, sequence=sequence, module=ModuleIdentity(data["module"])
        )
    if kind == "deselect_module":
        return DeselectModuleCommand(
            player=player, sequence=sequence, module=ModuleIdentity(data["module"])
        )
    if kind == "cancel_construction":
        return CancelConstructionCommand(player=player, sequence=sequence)
    if kind == "launch_robot":
        return LaunchRobotCommand(player=player, sequence=sequence)

    assert kind not in _KNOWN_COMMAND_KINDS  # every known kind is handled above
    raise ValueError(f"unknown persisted Command kind: {kind!r}")


def load_commands_by_tick(base_dir: Path, match_id: str) -> dict[int, tuple[Command, ...]]:
    """Return ``match_id``'s persisted accepted command stream, keyed by tick.

    Reads ``commands.jsonl`` line by line (the on-disk format
    ``ReplayWriter.record_tick`` appends); a trailing blank line (e.g. the
    empty file ``ReplayWriter.start_match`` creates before any tick is
    appended) is skipped rather than raising.
    """
    path = match_dir(base_dir, match_id) / "commands.jsonl"
    commands_by_tick: dict[int, tuple[Command, ...]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            record = json.loads(line)
            commands_by_tick[record["tick"]] = tuple(
                _command_from_json(entry) for entry in record["commands"]
            )
    return commands_by_tick


@dataclass(frozen=True, slots=True)
class ReplayVerificationResult:
    """Outcome of replaying a persisted artifact independently through the engine.

    ``matches`` is the acceptance-criterion check itself: the reproduced
    final snapshot equals the one persisted at match finish. ``reproduced_
    snapshot``/``persisted_snapshot`` are exposed too so a caller (or a
    failing test assertion) can see exactly where they diverge, rather than
    only a boolean.
    """

    matches: bool
    reproduced_snapshot: dict[str, Any]
    persisted_snapshot: dict[str, Any] | None


def verify_replay(
    base_dir: Path,
    match_id: str,
    *,
    scenario: Scenario,
    map_data: BootstrapMap | None = None,
    world: WorldMap | None = None,
) -> ReplayVerificationResult:
    """Reconstruct ``match_id``'s persisted command stream and replay it through the engine directly.

    ``scenario``/``map_data`` are supplied by the caller rather than read
    back from ``meta.json`` verbatim: a ``Scenario`` is a code-defined value
    (referenced by id, not itself serialized -- see
    ``nether_earth.scenario``'s and ``nether_earth.replay``'s own
    conventions), so persisting only its ``id``/``map_id``/``map_version``
    identity in ``meta.json`` and requiring the verifying caller to supply
    the matching ``Scenario``/``BootstrapMap`` object mirrors exactly how a
    real ``ReplayFixture`` is constructed everywhere else in this codebase.

    This calls ``nether_earth.replay.run_fixture`` -- i.e. ``engine.new_game``
    plus one ``engine.step`` call per persisted tick -- directly. No backend
    module (``MatchManager``, ``MatchRuntime``, transport) is imported or
    exercised by this call path; only ``commands.jsonl``/``meta.json`` (the
    filesystem artifact) and the engine package itself are involved, proving
    the persisted command stream alone is sufficient to reproduce the match.

    Raises :class:`ReplayRulesMismatchError` if the artifact's recorded rules
    version or content hash differs from the running engine's.
    """
    meta = load_meta(base_dir, match_id)
    check_rules_identity(meta)
    # Only human commands are persisted; an AI seat's are re-derived by the
    # engine, so the scenario must name the same AI seats. An artifact
    # without recorded seat controllers is all-human.
    recorded_controllers = meta.get("seat_controllers") or _ALL_HUMAN_SEAT_CONTROLLERS
    if meta_seat_controllers(scenario) != recorded_controllers:
        raise ValueError(
            f"scenario seat controllers {meta_seat_controllers(scenario)} do not match "
            f"artifact {recorded_controllers}"
        )
    commands_by_tick = load_commands_by_tick(base_dir, match_id)
    tick_count = meta["final_tick"]
    if tick_count is None:
        raise ValueError(
            f"match {match_id!r}'s artifact has no final_tick recorded "
            "(status is not 'finished' -- verify only a finished artifact)"
        )

    if world is not None:
        # A real match: the same ``create_initial_state`` +
        # ``engine.step`` path ``MatchManager``/``MatchRuntime`` used live.
        if world.map_id != meta["map_id"] or world.version != meta["map_version"]:
            raise ValueError(
                f"world {world.map_id!r} v{world.version} does not match artifact "
                f"{meta['map_id']!r} v{meta['map_version']}"
            )
        initial = create_initial_state(scenario, world, seed=meta["seed"])
        final_state, _events = run_from_state(initial, commands_by_tick, tick_count, world=world)
    else:
        if map_data is None:
            raise ValueError("verify_replay needs either world= or map_data=")
        fixture = ReplayFixture(
            scenario=scenario,
            map_data=map_data,
            seed=meta["seed"],
            tick_count=tick_count,
            commands_by_tick=commands_by_tick,
        )
        final_state, _events = run_fixture(fixture)
    reproduced_snapshot = to_snapshot(final_state)
    persisted_snapshot = meta.get("final_snapshot")

    return ReplayVerificationResult(
        matches=reproduced_snapshot == persisted_snapshot,
        reproduced_snapshot=reproduced_snapshot,
        persisted_snapshot=persisted_snapshot,
    )
