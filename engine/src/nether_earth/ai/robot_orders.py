"""AI robot-order sub-planner (CR004.5). Stub: issues nothing yet."""

from __future__ import annotations

from nether_earth.commands import Command
from nether_earth.map import WorldMap
from nether_earth.rng import MatchRandom
from nether_earth.rules import EngineRules
from nether_earth.state import AiMemory, GameState


def plan(
    state: GameState,
    memory: AiMemory,
    world: WorldMap,
    rules: EngineRules,
    random: MatchRandom,
) -> tuple[tuple[Command, ...], AiMemory]:
    """Return this seat's robot-order commands and updated memory (none yet)."""
    return (), memory
