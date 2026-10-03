"""Minimal war-base-ownership victory evaluation.

`_specs/technical-spec.md` §6 locks the v1 victory condition:

    victory = opponent owns zero war bases

and §10 requires it be "evaluated in the same authoritative simulation
step" as any war-base ownership change. This module is deliberately
small: one pure evaluation function and one
event. It does not persist a "match is over" flag on ``GameState``, does
not stop the simulation, and does not decide finalization/session
lifecycle -- per `_specs/technical-spec.md` §4.1, orchestration (ending a
match, notifying players, etc.) is the match/session layer's job, not the
engine's; this module only detects and deterministically announces the
condition so that layer has something to react to.

Generalized statement of the locked rule
------------------------------------------
`_specs/open-questions.md` §2 locks v1 as an exactly-2-player match, so
"opponent owns zero war bases" and "exactly one player owns any war base"
are equivalent for v1. :func:`evaluate_victory` is written the second way
(one player owns at least one war base, every *other* participant owns
zero) so it does not silently assume ``len(state.players) == 2`` -- it
simply returns ``None`` (no victory yet) for any player count where that
exact-one-nonzero-owner condition does not hold, which is always true for
today's exactly-2-player matches until one side is fully captured out.
It is the same single locked condition, stated so it does not hard-code a
player count
that `_specs/open-questions.md` never actually restricts this check to.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.events import Event, EventSequencer
from nether_earth.ids import PlayerId
from nether_earth.map import WorldMap
from nether_earth.state import GameState

__all__ = ["VictoryEvent", "evaluate_victory"]


@dataclass(frozen=True, slots=True)
class VictoryEvent(Event):
    """Announces that ``winner`` has met the v1 victory condition on ``tick``.

    Emitted at most once per :func:`evaluate_victory` call. Carrying no
    "match over" state beyond this event is intentional -- see the module
    docstring.
    """

    winner: PlayerId
    tick: int


def evaluate_victory(
    world: WorldMap,
    state: GameState,
    tick: int,
    sequencer: EventSequencer | None = None,
) -> VictoryEvent | None:
    """Return a :class:`VictoryEvent` if exactly one participant now owns any war base.

    Reads war-base ownership directly from ``world.war_bases`` -- callers
    that just applied a capture completion must pass
    ``capture.effective_world(base_world, state)`` (or an equivalent
    already-updated ``WorldMap``), not the pre-capture base map, so the
    just-completed ownership change is visible to this check in the same
    tick (`engine.py` does this). ``state.players`` is walked in its own
    canonical order so war-base counts are computed independently of
    ``world.war_bases``' declaration order.

    Returns ``None`` when fewer than two players are recorded (no
    "opponent" to compare against), when nobody owns any war base yet, or
    when more than one participant still owns at least one -- i.e. every
    tick before the locked condition actually holds.
    """
    if len(state.players) < 2:
        return None

    owned_counts: dict[PlayerId, int] = {player_id: 0 for player_id in state.players}
    for war_base in world.war_bases:
        if war_base.owner in owned_counts:
            owned_counts[war_base.owner] += 1

    non_zero_owners = [player_id for player_id in state.players if owned_counts[player_id] > 0]
    zero_owners = [player_id for player_id in state.players if owned_counts[player_id] == 0]

    if len(non_zero_owners) != 1 or len(zero_owners) != len(state.players) - 1:
        return None

    resolved_sequencer = sequencer if sequencer is not None else EventSequencer()
    return VictoryEvent(
        sequence=resolved_sequencer.next_sequence(),
        winner=non_zero_owners[0],
        tick=tick,
    )
