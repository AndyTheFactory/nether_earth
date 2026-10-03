"""AI planner entry point.

:func:`plan` is a pure function of ``(state, memory, world, rules, seed)``:
it reads the same ``GameState`` any client sees and returns the commands its
seat issues this decision tick plus the seat's updated :class:`AiMemory`
(state is immutable, so the memory is handed back rather than mutated).

It chains the sub-planners in :data:`_SUB_PLANNERS`, in a fixed order, each
seeing the memory the previous one returned. A sub-planner lives in its own
module and owns its own sub-record of :class:`AiMemory`, so construction
and robot orders plug in without editing each other's code. Each sub-planner draws from its own
``MatchRandom(derive_seed(seed, name))``, so how many draws one makes never
shifts another's stream.

``world`` is passed because every sub-planner needs the map (structures,
ownership) to decide anything. It is the effective world (captures and
destruction applied) at the start of the tick. ``seed`` (the seat's
per-decision seed, from :func:`nether_earth.ai.seat.issue_ai_commands`) is
used instead of a single ``MatchRandom`` so each sub-planner can get an
independent stream.
"""

from __future__ import annotations

from collections.abc import Callable

from nether_earth.ai import construction, robot_orders
from nether_earth.commands import Command
from nether_earth.map import WorldMap
from nether_earth.rng import MatchRandom, derive_seed
from nether_earth.rules import EngineRules
from nether_earth.state import AiMemory, GameState

__all__ = ["SubPlanner", "plan"]

#: A sub-planner: the state, its seat's memory, the effective world, the
#: rules and its own seeded random stream; returns commands and new memory.
SubPlanner = Callable[
    [GameState, AiMemory, WorldMap, EngineRules, MatchRandom],
    tuple[tuple[Command, ...], AiMemory],
]

#: Run order, construction first, then robot orders. The name is folded into
#: the sub-planner's seed, so it must stay stable once a sub-planner uses it.
_SUB_PLANNERS: tuple[tuple[str, SubPlanner], ...] = (
    ("construction", construction.plan),
    ("robot_orders", robot_orders.plan),
)


def plan(
    state: GameState,
    memory: AiMemory,
    world: WorldMap,
    rules: EngineRules,
    seed: int,
) -> tuple[tuple[Command, ...], AiMemory]:
    """Return the commands ``memory.player_id`` issues this decision tick, and its new memory.

    Command ``sequence`` values are ignored: the engine hook numbers them
    (:func:`nether_earth.ai.seat.issue_ai_commands`). Every command must name
    ``memory.player_id`` as its player.
    """
    commands: list[Command] = []
    for name, sub_planner in _SUB_PLANNERS:
        random = MatchRandom(derive_seed(seed, name))
        issued, memory = sub_planner(state, memory, world, rules, random)
        commands.extend(issued)
    return tuple(commands), memory
