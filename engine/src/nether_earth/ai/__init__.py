"""Engine-side AI opponent.

The AI is a player, not a rule: a pure, deterministic planner that emits
ordinary :class:`~nether_earth.commands.Command`\\ s for its own seat, which
``engine.step`` validates and applies exactly like a human's. Carry-over state
lives in ``GameState.ai_memories`` (:class:`~nether_earth.state.AiMemory`).

- :mod:`nether_earth.ai.seat` -- the ``engine.step`` hook (cadence, seeding,
  sequence numbers).
- :mod:`nether_earth.ai.planner` -- the per-seat entry point, chaining the
  sub-planners.
- :mod:`nether_earth.ai.construction` / :mod:`nether_earth.ai.robot_orders`
  -- the sub-planners.
"""

from nether_earth.ai.planner import SubPlanner, plan
from nether_earth.ai.seat import is_ai_decision_tick, issue_ai_commands

__all__ = ["SubPlanner", "is_ai_decision_tick", "issue_ai_commands", "plan"]
