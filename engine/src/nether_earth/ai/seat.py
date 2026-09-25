"""The ``engine.step`` hook that runs every AI seat's planner (CR004.3).

Cadence: the planner runs only when the tick being simulated
(``state.tick + 1``) is a multiple of ``rules.ai_decision_interval_ticks``.

Seeding: each seat gets a fresh
``MatchRandom(derive_seed(state.seed, "ai", player, tick))`` per decision, so
no RNG state has to be carried and each decision draws its own stream.

Sequence numbers: the AI's commands join the tick's batch after any command
already submitted for that seat, numbered ``max(existing) + 1`` onwards in
the order the planner returned them. Planners never choose sequence numbers,
so a collision with an externally submitted command cannot happen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from nether_earth.ai.planner import plan
from nether_earth.commands import Command
from nether_earth.map import WorldMap
from nether_earth.rng import MatchRandom, derive_seed
from nether_earth.rules import EngineRules
from nether_earth.state import GameState

__all__ = ["is_ai_decision_tick", "issue_ai_commands"]


def is_ai_decision_tick(tick: int, rules: EngineRules) -> bool:
    """Return whether AI seats plan on ``tick`` (the tick being simulated)."""
    return tick % rules.ai_decision_interval_ticks == 0


def issue_ai_commands(
    state: GameState,
    world: WorldMap,
    commands: Sequence[Command],
    rules: EngineRules,
) -> tuple[GameState, tuple[Command, ...]]:
    """Run each AI seat's planner for the tick after ``state.tick``.

    Returns ``state`` with every AI seat's memory updated, and the AI
    commands to append to this tick's batch (``commands`` is the batch
    submitted so far, read only to number the AI's commands after it).
    Seats are planned in canonical player order. Off decision ticks, and in
    a match with no AI seat, this returns ``state`` unchanged and no commands.
    """
    tick = state.tick + 1
    if not state.ai_memories or not is_ai_decision_tick(tick, rules):
        return state, ()

    issued: list[Command] = []
    for memory in state.ai_memories:
        player = memory.player_id
        random = MatchRandom(derive_seed(state.seed, "ai", player.value, tick))
        planned, updated = plan(state, memory, world, rules, random)
        if updated.player_id != player:
            raise ValueError(f"AI planner for {player.value!r} returned another seat's memory")
        next_sequence = 1 + max(
            (command.sequence for command in commands if command.player == player), default=-1
        )
        for offset, command in enumerate(planned):
            if command.player != player:
                raise ValueError(
                    f"AI planner for {player.value!r} issued a command for "
                    f"{command.player.value!r}"
                )
            issued.append(replace(command, sequence=next_sequence + offset))
        state = state.with_ai_memory(updated)
    return state, tuple(issued)
