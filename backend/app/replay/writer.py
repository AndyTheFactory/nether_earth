"""Filesystem replay/debug artifact writer (M7 Task 8, issue #97).

Scope: this module owns the *backend-only* filesystem I/O side of replay
persistence. It never touches ``nether_earth.engine``/``GameState`` mutation
(it only reads/serializes state already produced elsewhere) and the engine
package gains no new dependency because of it -- every import here runs
backend -> engine, never the reverse.

Artifact layout: one directory per match, keyed by ``match_id`` (a
``uuid4().hex`` string -- see ``app.match.manager.MatchManager.create_match``
-- so two concurrently-live matches can never collide on a path)::

    <base_dir>/<match_id>/meta.json
    <base_dir>/<match_id>/commands.jsonl
    <base_dir>/<match_id>/lifecycle.jsonl

``meta.json`` carries the match's fixed identity/seed/scenario header
(written once, at match start) plus a ``status`` field
(``"in_progress"``/``"finished"``) and, once finished, the final tick,
result, and final engine snapshot. It is always rewritten via a temp file
plus ``os.replace`` (atomic rename on every filesystem this backend
targets), so a reader never observes a torn/partially-written ``meta.json``
-- either the old contents or the new ones, never a mix. A match whose
process crashes (or is disposed without ever finishing) simply never gets
its final rewrite: its ``meta.json`` is left exactly as ``start_match`` wrote
it, ``status: "in_progress"`` forever, which is precisely how a crashed
artifact stays distinguishable from a finalized one -- no separate
"is this crashed" detection is needed.

``commands.jsonl``/``lifecycle.jsonl`` are append-only JSON-Lines streams:
one authoritative tick's accepted command batch (plus a short debug summary
of that tick's engine events) per line in ``commands.jsonl``, and one
disconnect/reconnect/pause/forfeit/no-contest lifecycle event (with a
wall-clock timestamp) per line in ``lifecycle.jsonl``. These are two
*separate* files precisely so a pause's wall-clock duration can never be
mistaken for gameplay ticks: nothing about a pause/resume/forfeit/no-contest
lifecycle event ever creates or numbers a tick entry in ``commands.jsonl``,
and nothing in this module infers tick numbers from wall-clock gaps.

Never persisted, anywhere in this module: session tokens, join codes, or any
other guest credential. ``meta.json``'s ``players`` field carries only
public-facing nicknames and player ids -- see ``_meta_players`` -- and every
lifecycle event carries only player ids and wall-clock/tick numbers.

No secrets, no database: every write here is a plain local file call. In
production ``app.main`` hands this writer a single-worker executor, so the
tick loop and lifecycle watchers only enqueue; one worker thread runs every
write in submission order, which is what keeps a match's lines from
interleaving and keeps a slow disk from stalling any match's tick cadence.
Without an executor the same methods run inline (unit tests, scripts).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor
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
from nether_earth.events import Event
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO
from nether_earth.map import BootstrapMap
from nether_earth.orders import SetRobotOrderCommand
from nether_earth.rules import RULES_VERSION, rules_content_hash
from nether_earth.scenario import Scenario
from nether_earth.snapshot import to_snapshot
from nether_earth.state import GameState

from app.match.models import Match
from app.match.reconnect import (
    DisconnectEvent,
    DisconnectNotifier,
    ForfeitEvent,
    NoContestEvent,
    PausedEvent,
    ResumedEvent,
)
from app.match.runtime import TickCommandObserver
from app.replay.orders_json import order_to_json

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ReplayWriter",
    "default_replay_dir",
    "make_replay_lifecycle_notifier",
    "make_replay_tick_recorder",
    "match_dir",
    "write_meta_atomic",
]

#: Bumped whenever the on-disk artifact *shape* changes incompatibly.
#: Independent of gameplay-rules content (see ``nether_earth.rules.RULES_VERSION``) and of
#: either package's own ``pyproject.toml`` version (both are still ``0.0.0``
#: placeholders repo-wide, not meaningful semantic versions).
ARTIFACT_SCHEMA_VERSION = 1


_ENV_VAR = "NETHER_EARTH_REPLAY_DIR"

_META_FILENAME = "meta.json"
_COMMANDS_FILENAME = "commands.jsonl"
_LIFECYCLE_FILENAME = "lifecycle.jsonl"

#: Upper bound on writes queued to the executor. Beyond it a write is dropped
#: and reported (``replay_write_failed``, action ``backlog``) rather than
#: letting a stalled disk grow memory without limit (security review NE-02).
MAX_PENDING_WRITES = 10_000


def _package_relative_default_replay_dir() -> Path:
    """Return ``<repo>/replays`` anchored to this module's own file location, not the process CWD.

    ``replays/`` is the repo's own locked top-level directory for this
    purpose (see `_specs/milestones/00-repository-agentic-foundation.md`'s
    repository-skeleton workstream, and `replays/.gitkeep`, which already
    exists at the repo root).

    Deliberately *not* CWD-relative (M7 Task 8 review, Important I6): this
    repo's own ``README.md`` documents launching the backend for local dev
    as ``uvicorn app.main:app --app-dir backend --reload`` -- run from the
    *repo root*, which never changes the process CWD, only the import path.
    A CWD-relative default (e.g. a literal ``"../replays"``) would silently
    resolve outside the repo entirely under that exact documented command,
    while happening to resolve correctly only under a *different* launch
    style (``cd backend && uvicorn ...``). Anchoring to ``__file__`` instead
    is deterministic regardless of which of those two launch styles was
    used, because this repo's local dev tooling installs both packages
    editable (see `backend/pyproject.toml`'s standard `pip install -e`
    flow): ``__file__`` here always resolves to the real
    ``<repo>/backend/app/replay/writer.py`` path, four directories below
    the repo root (``replay`` -> ``app`` -> ``backend`` -> repo root).

    This anchor is *not* relied on for the containerized deployment: a
    non-editable ``pip install`` (see `backend/Dockerfile`) copies this
    package's files into site-packages, severing any fixed relationship
    between ``__file__`` and ``/app/replays`` (the volume
    `deploy/docker-compose.yml` mounts). `backend/Dockerfile` therefore
    sets ``$NETHER_EARTH_REPLAY_DIR=/app/replays`` explicitly, which
    :func:`default_replay_dir` always prefers over this fallback -- this
    function only needs to be correct for the editable-install/local-dev
    case.
    """
    return Path(__file__).resolve().parents[3] / "replays"


def default_replay_dir() -> Path:
    """Return the configured replay directory: ``$NETHER_EARTH_REPLAY_DIR``, else a package-relative default.

    Every test that exercises a real ``ReplayWriter``/``create_app`` passes
    an explicit ``tmp_path``-scoped override instead of relying on either of
    these -- see ``tests/replay/test_replay_log.py`` and
    ``tests/transport/test_ws.py``'s ``client`` fixture.
    """
    override = os.environ.get(_ENV_VAR)
    return Path(override) if override else _package_relative_default_replay_dir()


logger = logging.getLogger(__name__)

#: Production match ids are server-generated ``uuid4().hex`` strings. Only
#: allowing a separator- and dot-free charset here means no value can ever
#: steer a replay path outside ``base_dir`` (``..``, ``/``, absolute paths).
_MATCH_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def match_dir(base_dir: Path, match_id: str) -> Path:
    """Return the directory a match's artifact lives in under ``base_dir``."""
    if not _MATCH_ID_RE.fullmatch(match_id):
        raise ValueError(f"refusing unsafe replay match id {match_id!r}")
    return base_dir / match_id


def write_meta_atomic(directory: Path, meta: dict[str, Any]) -> None:
    """Write ``meta`` as ``meta.json`` in ``directory`` via temp file + ``os.replace``."""
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f"{_META_FILENAME}.tmp"
    tmp_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_path, directory / _META_FILENAME)  # atomic rename on every target filesystem.


def _epoch_ms() -> int:
    return int(time.time() * 1000)


#: `command.__class__.__name__` -> the JSON `kind` tag `_command_to_json`
#: writes for it, matching `app.protocol.common`'s `CommandPayload` `kind`
#: tokens one for one (issue #98) so a persisted command and the transport
#: payload that produced it are always spelled identically on disk/wire.
_COMMAND_KIND_BY_CLASS: dict[type[Command], str] = {
    CommanderMoveCommand: "commander_move",
    CommanderSetVerticalIntentCommand: "commander_set_vertical_intent",
    DirectRobotMoveCommand: "direct_robot_move",
    FireCommand: "robot_fire",
    SetRobotOrderCommand: "set_robot_order",
    SelectModuleCommand: "select_module",
    DeselectModuleCommand: "deselect_module",
    CancelConstructionCommand: "cancel_construction",
    LaunchRobotCommand: "launch_robot",
}


def _command_to_json(command: Command) -> dict[str, Any]:
    """Serialize ``command`` losslessly enough for ``load_commands_by_tick`` to reconstruct it.

    Covers the base ``player``/``sequence`` contract every ``Command``
    carries, plus every one of the nine concrete gameplay ``Command``
    subclasses ``engine.step`` dispatches on (issue #98) -- see
    :data:`_COMMAND_KIND_BY_CLASS`. Each subclass's own gameplay-specific
    fields are appended after ``kind``, using the exact same ``kind`` tokens
    as ``app.protocol.common``'s ``CommandPayload``/``app.transport.commands``'
    adapter (only the ``kind`` discriminator and field *names* are shared --
    this is a plain snake_case JSON-Lines record, not the camelCase wire
    envelope, so it is not byte-for-byte identical to a transport payload),
    so a persisted command and the transport payload that produced it are
    never spelled differently for the same logical command.

    Raises ``NotImplementedError`` for anything other than the bare
    ``Command`` contract or one of the nine known subclasses (M7 Task 8
    review, Important I3, extended by Task 9 rather than relaxed): a
    silently-dropped gameplay-specific field would produce a persisted
    artifact that *looks* fine but has quietly lost information -- a
    corrupted replay with no error anywhere. A *new* tenth ``Command``
    subclass added by a future milestone must extend this function (and
    ``app.replay.verify``'s ``_command_from_json``) explicitly, the same way
    this task extended it for the first nine.
    """
    if type(command) is Command:
        return {"player": command.player.to_json(), "sequence": command.sequence}

    kind = _COMMAND_KIND_BY_CLASS.get(type(command))
    if kind is None:
        raise NotImplementedError(
            f"_command_to_json does not support {type(command).__name__!r}. Extend "
            "_COMMAND_KIND_BY_CLASS/_command_to_json (and app.replay.verify's "
            "_command_from_json) to serialize this concrete gameplay Command "
            "subclass losslessly before submitting it through the backend."
        )
    base: dict[str, Any] = {
        "player": command.player.to_json(),
        "sequence": command.sequence,
        "kind": kind,
    }
    base.update(_command_fields_to_json(command))
    return base


def _command_fields_to_json(command: Command) -> dict[str, Any]:
    """Return the gameplay-specific fields (beyond ``player``/``sequence``/``kind``) for ``command``."""
    if isinstance(command, (CommanderMoveCommand, DirectRobotMoveCommand)):
        return {"dx": command.dx, "dy": command.dy}
    if isinstance(command, CommanderSetVerticalIntentCommand):
        return {"rising": command.rising}
    if isinstance(command, FireCommand):
        return {
            "entity_id": command.entity_id.to_json(),
            "weapon": command.weapon.value,
        }
    if isinstance(command, SetRobotOrderCommand):
        return {"entity_id": command.entity_id.to_json(), "order": order_to_json(command.order)}
    if isinstance(command, (SelectModuleCommand, DeselectModuleCommand)):
        return {"module": command.module.value}
    if isinstance(command, (CancelConstructionCommand, LaunchRobotCommand)):
        return {}
    raise AssertionError(  # pragma: no cover - exhaustive over _COMMAND_KIND_BY_CLASS
        f"unhandled Command subclass: {type(command).__name__}"
    )


def _event_summary(event: Event) -> dict[str, Any]:
    """Return a JSON-safe, human-debuggable (not round-trippable) summary of ``event``.

    Concrete gameplay ``Event`` subclasses (victory, combat, capture, ...)
    carry heterogeneous fields with no single generic, lossless JSON shape
    short of duplicating each one's definition here -- and replay
    verification never needs to reconstruct events from disk, only commands
    (``nether_earth.replay.run_fixture`` recomputes every event fresh from
    the command stream). This is therefore deliberately debug-only.
    """
    return {"type": type(event).__name__, "sequence": event.sequence, "repr": repr(event)}


def _meta_players(match: Match) -> dict[str, str]:
    """Return ``{player_id: nickname}`` for ``match`` -- never a session token."""
    return {player_id.to_json(): slot.nickname for player_id, slot in match.players.items()}


def meta_seat_controllers(scenario: Scenario) -> dict[str, str]:
    """Return ``{player_id: "human" | "ai"}`` -- who drives each seat (CR004.7).

    Only human commands are recorded; replay re-derives an AI seat's
    commands by stepping the engine, so the verifier must rebuild the
    scenario with the same seat controllers.
    """
    return {
        player.to_json(): scenario.controller_for(player) for player in (PLAYER_ONE, PLAYER_TWO)
    }


def _result_to_json(match: Match) -> dict[str, Any] | None:
    """Return ``match.result`` (forfeit/no-contest) as JSON, or ``None`` for a normal engine finish.

    ``match.result`` is only ever set by ``ReconnectCoordinator`` (M7 Task
    7) for the two runtime-level outcomes it can produce; a normal in-game
    engine victory leaves it ``None`` and is read from the persisted final
    snapshot's own victory-bearing fields instead (see ``Match.result``'s
    own docstring) -- this function never invents a result neither side
    actually recorded.
    """
    result = match.result
    if result is None:
        return None
    return {
        "outcome": result.outcome.value,
        "reason": result.reason,
        "winner_player_id": (
            result.winner_player_id.to_json() if result.winner_player_id is not None else None
        ),
        "forfeiting_player_id": (
            result.forfeiting_player_id.to_json()
            if result.forfeiting_player_id is not None
            else None
        ),
    }


class ReplayWriter:
    """Owns one filesystem artifact per match: header/meta, gameplay stream, lifecycle stream.

    Every public method returns without waiting for the filesystem when an
    executor is supplied (writes run on it in submission order), or does the
    plain local file I/O inline when not -- see the module docstring. Callers (``app.main``'s
    composition root) supply this object's bound methods directly as
    ``MatchManager``'s ``on_match_start``/``on_match_finish`` hooks, and use
    :func:`make_replay_tick_recorder`/:func:`make_replay_lifecycle_notifier`
    to build the tick/lifecycle callback shapes those layers expect.
    """

    def __init__(self, base_dir: Path | None = None, *, executor: Executor | None = None) -> None:
        self._base_dir = base_dir if base_dir is not None else default_replay_dir()
        self._failed_match_ids: set[str] = set()
        # ``executor`` (NE-02): when set, every write runs there and the
        # caller never waits for the filesystem. It must be single-worker so
        # lines land in submission order across all matches. ``None`` runs
        # writes inline (unit tests, scripts).
        self._executor = executor
        self._pending = 0
        self._pending_lock = threading.Lock()

    def _report_failure(self, match_id: str, action: str, *, exc: bool = True) -> None:
        """Log a replay I/O failure without interrupting the match.

        The first failure per match is an ERROR with traceback (``/ready``
        also turns 503 while the directory is unwritable); repeats for the
        same match (every tick) are DEBUG.
        """
        first = match_id not in self._failed_match_ids
        self._failed_match_ids.add(match_id)
        logger.log(
            logging.ERROR if first else logging.DEBUG,
            "replay artifact write failed; the match continues without it",
            exc_info=first and exc,
            extra={"event": "replay_write_failed", "match_id": match_id, "action": action},
        )

    def _run(self, match_id: str, action: str, fn: Callable[[], None]) -> None:
        """Run ``fn`` inline, or queue it on the executor; report (never raise) on failure."""

        def job() -> None:
            try:
                fn()
            except (OSError, ValueError):
                self._report_failure(match_id, action)
            except Exception:
                # Anything else is a bug, not a disk problem: log it here,
                # since an executor future's exception is never read.
                logger.exception(
                    "replay write crashed",
                    extra={"event": "replay_write_crashed", "match_id": match_id, "action": action},
                )

        if self._executor is None:
            job()
            return
        with self._pending_lock:
            if self._pending >= MAX_PENDING_WRITES:
                overflow = True
            else:
                overflow = False
                self._pending += 1
        if overflow:
            self._report_failure(match_id, "backlog", exc=False)
            return
        try:
            future = self._executor.submit(job)
        except RuntimeError:  # executor already shut down (process stopping)
            with self._pending_lock:
                self._pending -= 1
            self._report_failure(match_id, action, exc=False)
            return
        future.add_done_callback(self._on_done)

    def _on_done(self, _future: object) -> None:
        with self._pending_lock:
            self._pending -= 1

    def drain(self) -> None:
        """Block until every queued write has run. Never call this on the event loop."""
        if self._executor is not None:
            self._executor.submit(lambda: None).result()

    def close(self) -> None:
        """Drain and stop the executor; later writes are reported as failures."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    # -- match start / tick append / finish -----------------------------------

    def start_match(self, match: Match, scenario: Scenario, map_data: BootstrapMap) -> None:
        """Create ``match``'s artifact directory and write its ``in_progress`` header.

        Called exactly once per match by ``MatchManager._start_match_locked``,
        before the runtime starts, so this never clobbers a live stream. The
        header is built on the caller's thread (as in ``finish_match``), so
        the write job never reads the live ``Match``.
        """
        match_id = match.match_id
        meta: dict[str, Any] = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "rules_version": RULES_VERSION,
            "rules_hash": rules_content_hash(),
            "match_id": match_id,
            "scenario_id": scenario.id,
            "map_id": scenario.map_id,
            "map_version": scenario.map_version,
            "map_width": map_data.width,
            "map_height": map_data.height,
            "seed": match.seed,
            "players": _meta_players(match),
            "seat_controllers": meta_seat_controllers(scenario),
            "created_at_epoch_ms": _epoch_ms(),
            "status": "in_progress",
            "finished_at_epoch_ms": None,
            "final_tick": None,
            "result": None,
            "final_snapshot": None,
        }

        def job() -> None:
            self._start_match(match_id, meta)
            logger.info(
                "replay artifact started",
                extra={"event": "replay_started", "match_id": match_id},
            )

        self._run(match_id, "start", job)

    def _start_match(self, match_id: str, meta: dict[str, Any]) -> None:
        directory = match_dir(self._base_dir, match_id)
        directory.mkdir(parents=True, exist_ok=True)
        self._write_meta_atomic(match_id, meta)
        # Truncating (rather than appending) is only safe because
        # `start_match` runs exactly once per match -- see this method's
        # own docstring. A second call for the same match_id would silently
        # wipe an already-in-progress gameplay/lifecycle stream.
        (directory / _COMMANDS_FILENAME).write_text("", encoding="utf-8")
        (directory / _LIFECYCLE_FILENAME).write_text("", encoding="utf-8")

    def record_tick(
        self,
        match_id: str,
        tick: int,
        commands: tuple[Command, ...],
        events: tuple[Event, ...],
    ) -> None:
        """Append ``tick``'s accepted command batch (plus a debug event summary) to the gameplay stream."""
        line = {
            "tick": tick,
            "commands": [_command_to_json(command) for command in commands],
            "events": [_event_summary(event) for event in events],
        }
        self._run(
            match_id, "record_tick", lambda: self._append_jsonl(match_id, _COMMANDS_FILENAME, line)
        )

    def record_lifecycle_event(self, match_id: str, payload: dict[str, Any]) -> None:
        """Append one disconnect/reconnect/pause/forfeit/no-contest event to the lifecycle stream.

        ``payload`` must already be JSON-safe and must never carry a session
        token. ``wall_clock_epoch_ms`` is stamped here, not by the caller.
        """
        line = {"wall_clock_epoch_ms": _epoch_ms(), **payload}
        self._run(
            match_id,
            "record_lifecycle_event",
            lambda: self._append_jsonl(match_id, _LIFECYCLE_FILENAME, line),
        )

    def finish_match(self, match: Match) -> None:
        """Atomically flip ``match``'s artifact to ``status: "finished"`` with its final result.

        Idempotent: no artifact, or one already ``"finished"``, is a silent
        no-op (``MatchManager.finish_match`` may be called more than once).
        The final snapshot is captured on the caller's thread so the
        persisted state is the match state at finish time.
        """
        state = match.game_state
        final_tick = state.tick if state is not None else None
        final_snapshot = to_snapshot(state) if state is not None else None
        result = _result_to_json(match)
        match_id = match.match_id

        def job() -> None:
            # Report then forget, as before NE-02: no write follows a finish,
            # so keeping the id flagged would only grow the set.
            try:
                finalized = self._finish_match(match_id, final_tick, result, final_snapshot)
            except (OSError, ValueError):
                self._report_failure(match_id, "finish")
                return
            finally:
                self._failed_match_ids.discard(match_id)
            if finalized:
                logger.info(
                    "replay artifact finalized",
                    extra={"event": "replay_finalized", "match_id": match_id},
                )

        self._run(match_id, "finish", job)

    def _finish_match(
        self,
        match_id: str,
        final_tick: int | None,
        result: dict[str, Any] | None,
        final_snapshot: dict[str, Any] | None,
    ) -> bool:
        meta = self._read_meta(match_id)
        if meta is None or meta.get("status") == "finished":
            return False
        meta["status"] = "finished"
        meta["finished_at_epoch_ms"] = _epoch_ms()
        meta["final_tick"] = final_tick
        meta["result"] = result
        meta["final_snapshot"] = final_snapshot
        self._write_meta_atomic(match_id, meta)
        return True

    # -- internal helpers -------------------------------------------------------

    def _write_meta_atomic(self, match_id: str, meta: dict[str, Any]) -> None:
        write_meta_atomic(match_dir(self._base_dir, match_id), meta)

    def _read_meta(self, match_id: str) -> dict[str, Any] | None:
        path = match_dir(self._base_dir, match_id) / _META_FILENAME
        if not path.exists():
            return None
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
        return loaded  # type: ignore[no-any-return]

    def _append_jsonl(self, match_id: str, filename: str, line: dict[str, Any]) -> None:
        path = match_dir(self._base_dir, match_id) / filename
        # One raw O_APPEND write per line (called every tick for every match):
        # same per-tick durability as a buffered text handle, a fraction of
        # its setup cost (M10.6 profiling).
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(line) + "\n").encode("utf-8"))
        finally:
            os.close(fd)


def make_replay_tick_recorder(writer: ReplayWriter, match_id: str) -> TickCommandObserver:
    """Return a ``TickCommandObserver`` that appends every tick to ``match_id``'s gameplay stream.

    Mirrors ``app.transport.snapshots.make_tick_broadcaster``'s own
    per-match closure style: ``match_id`` is bound once, at match-start
    time, matching ``MatchManager.on_tick_commands_factory``'s call
    contract (see ``manager.py``).
    """

    async def _on_tick_commands(
        tick: int,
        commands: tuple[Command, ...],
        state: GameState,
        events: tuple[Event, ...],
    ) -> None:
        del state  # the final snapshot is captured once, at finish, not every tick.
        writer.record_tick(match_id, tick, commands, events)

    return _on_tick_commands


def make_replay_lifecycle_notifier(writer: ReplayWriter) -> DisconnectNotifier:
    """Return a ``DisconnectNotifier`` that appends every disconnect-policy event to its lifecycle stream.

    Mirrors ``app.transport.disconnects.make_disconnect_notifier``'s own
    event-to-payload mapping, except the destination is this writer's
    ``lifecycle.jsonl`` rather than a broadcast wire message. Every payload
    below carries only player ids, ticks, and reasons/deadlines already
    public knowledge to both match participants -- never a session token
    (``PausedEvent``/``ResumedEvent``/``ForfeitEvent``/``NoContestEvent``
    never carry one in the first place; see ``app.match.reconnect``).
    """

    async def _notify(event: DisconnectEvent) -> None:
        match_id = event.match.match_id
        if isinstance(event, PausedEvent):
            writer.record_lifecycle_event(
                match_id,
                {
                    "type": "paused",
                    "disconnected_player_id": event.disconnected_player_id.to_json(),
                    "grace_deadline_epoch_ms": event.grace_deadline_epoch_ms,
                },
            )
        elif isinstance(event, ResumedEvent):
            state = event.match.game_state
            writer.record_lifecycle_event(
                match_id,
                {"type": "resumed", "tick": state.tick if state is not None else None},
            )
        elif isinstance(event, ForfeitEvent):
            writer.record_lifecycle_event(
                match_id,
                {
                    "type": "forfeit",
                    "forfeiting_player_id": event.forfeiting_player_id.to_json(),
                    "winner_player_id": event.winner_player_id.to_json(),
                },
            )
        elif isinstance(event, NoContestEvent):
            writer.record_lifecycle_event(match_id, {"type": "no_contest"})
        else:  # pragma: no cover - exhaustive union guarded for future additions.
            raise TypeError(f"unhandled DisconnectEvent variant: {event!r}")

    return _notify
