"""AI planner entry point (CR004.3).

:func:`plan` is a pure function of ``(state, memory, world, rules, random)``:
it reads the same ``GameState`` any client sees and returns the commands its
seat issues this decision tick plus the seat's updated :class:`AiMemory`
(state is immutable, so the memory is handed back rather than mutated).

It chains the sub-planners in :data:`_SUB_PLANNERS`, in a fixed order, each
seeing the memory the previous one returned. A sub-planner lives in its own
module and owns its own sub-record of :class:`AiMemory`, so construction
(CR004.4) and robot orders (CR004.5) plug in without editing each other's
code.

``world`` is not in the CR004.3 brief's signature; it is added because every
sub-planner needs the map (structures, ownership) to decide anything. It is
the effective world (captures and destruction applied) at the start of the
tick.
"""

from __future__ import annotations

from collections.abc import Callable

from nether_earth.ai import construction, robot_orders
from nether_earth.commands import Command
from nether_earth.map import WorldMap
from nether_earth.rng import MatchRandom
from nether_earth.rules import EngineRules
from nether_earth.state import AiMemory, GameState

__all__ = ["SubPlanner", "plan"]

#: A sub-planner: same inputs as :func:`plan`, same result shape.
SubPlanner = Callable[
    [GameState, AiMemory, WorldMap, EngineRules, MatchRandom],
    tuple[tuple[Command, ...], AiMemory],
]

#: Run order: construction first, then robot orders.
_SUB_PLANNERS: tuple[SubPlanner, ...] = (construction.plan, robot_orders.plan)


def plan(
    state: GameState,
    memory: AiMemory,
    world: WorldMap,
    rules: EngineRules,
    random: MatchRandom,
) -> tuple[tuple[Command, ...], AiMemory]:
    """Return the commands ``memory.player_id`` issues this decision tick, and its new memory.

    Command ``sequence`` values are ignored: the engine hook numbers them
    (:func:`nether_earth.ai.seat.issue_ai_commands`). Every command must name
    ``memory.player_id`` as its player.
    """
    commands: list[Command] = []
    for sub_planner in _SUB_PLANNERS:
        issued, memory = sub_planner(state, memory, world, rules, random)
        commands.extend(issued)
    return tuple(commands), memory
