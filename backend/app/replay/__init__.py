"""Filesystem replay/debug logging (M7 Task 8, issue #97).

Public surface: :class:`~app.replay.writer.ReplayWriter` (the artifact
writer, wired into ``app.main``'s composition root as
``MatchManager``'s ``on_match_start``/``on_match_finish``/
``on_tick_commands_factory`` hooks) plus the read-side helpers in
``app.replay.verify`` used by tests and any future debug tooling to
reconstruct and independently re-run a persisted match through the engine.
See ``writer.py``'s module docstring for the on-disk artifact layout and
atomicity/no-secrets guarantees, and ``verify.py``'s for the
replay-verification contract.
"""

from app.replay.retention import mark_interrupted, prune_replays
from app.replay.verify import (
    ReplayRulesMismatchError,
    ReplayVerificationResult,
    check_rules_identity,
    load_commands_by_tick,
    load_meta,
    verify_replay,
)
from app.replay.writer import (
    ReplayWriter,
    default_replay_dir,
    make_replay_lifecycle_notifier,
    make_replay_tick_recorder,
    match_dir,
    write_meta_atomic,
)

__all__ = [
    "ReplayRulesMismatchError",
    "ReplayVerificationResult",
    "ReplayWriter",
    "check_rules_identity",
    "default_replay_dir",
    "load_commands_by_tick",
    "load_meta",
    "make_replay_lifecycle_notifier",
    "make_replay_tick_recorder",
    "mark_interrupted",
    "match_dir",
    "prune_replays",
    "verify_replay",
    "write_meta_atomic",
]
