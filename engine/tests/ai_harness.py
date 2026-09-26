"""Headless AI match harness (CR004.10, #291) -- test/harness code, not shipped.

Runs whole matches straight against the engine: no backend, no frontend, no
commander input. Both seats are ``"ai"`` controllers; a seat can instead be
driven by :func:`baseline_plan`, a fixed scripted opponent, swapped in the
same way the seat tests swap planners (``nether_earth.ai.seat.plan``).

Used by ``test_ai_harness.py`` (determinism and a bounded strength smoke
test) and by ``scripts/ai_strength.py`` (the full win-rate run).

Baseline (deliberately naive, deterministic, draws no random numbers):

- **Build**: whenever the pool pays for it, the cheapest legal robot
  (bipod + cannon), at its first owned war base in map order. It saves
  nothing and never adapts the design.
- **Orders**: every robot holds Search & Capture of the nearest neutral
  factory (the engine picks the factory and keeps same-order claims
  exclusive). A robot for which the engine finds no neutral factory is
  ordered Search & Capture of the enemy war base instead, so the baseline
  can still win once the neutrals are gone.

Everything the baseline issues is an ordinary command that goes through the
engine's validation, exactly like the real planner's.

Engine defect workaround (``unique_robot_ids``): the engine numbers a new
robot ``1 + <robots the owner has now>`` (``robot_launch._next_robot_id``),
so once a robot has been destroyed the next launch can reuse a living
robot's id and ``engine.step`` raises ``ValueError: duplicate robot
entity_id``. Full matches hit this routinely. It is an engine bug outside
CR004.10's scope (reported, not fixed here); ``run_match`` can swap in a
collision-free numbering (``1 + <highest ordinal the owner has now>``) so the
strength run measures play rather than the crash. The swap only renames
robots: it changes no rule, but ids do break ranking ties, so a patched run
is not bit-identical to an unpatched one.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from nether_earth import engine, robot_launch
from nether_earth.ai import planner, seat
from nether_earth.ai.construction import DESIGNS, design_cost, owned_war_bases, pool_after
from nether_earth.capture import effective_world
from nether_earth.commands import Command
from nether_earth.construction_commands import (
    CancelConstructionCommand,
    EnterConstructionRemotelyCommand,
    LaunchRobotCommand,
    SelectModuleCommand,
)
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId
from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.orders import (
    SearchCapture,
    SearchCaptureTarget,
    SetRobotOrderCommand,
    select_capture_target,
)
from nether_earth.resource_pool import PlayerResourcePool
from nether_earth.robot_launch import LaunchRejectionReason, resolve_launch_exit
from nether_earth.rules import EngineRules
from nether_earth.scenario import Scenario, create_initial_state, default_pvp_scenario
from nether_earth.snapshot import snapshot_to_json_string
from nether_earth.state import AiMemory, GameState
from nether_earth.victory import VictoryEvent

__all__ = [
    "ORIGINAL_MAP_PATH",
    "MatchResult",
    "SeatScore",
    "ai_vs_ai_scenario",
    "baseline_plan",
    "load_world",
    "run_match",
    "seat_planners",
    "unique_robot_ids",
]

#: A seat planner, as ``nether_earth.ai.seat`` calls it.
Planner = Callable[
    [GameState, AiMemory, WorldMap, EngineRules, int],
    tuple[tuple[Command, ...], AiMemory],
]

ORIGINAL_MAP_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "maps" / "zx-spectrum-original.yaml"
)

#: The cheapest legal design (lowest cost, then canonical order).
_CHEAPEST = min(DESIGNS, key=lambda design: design_cost(design, EngineRules()))


def load_world() -> WorldMap:
    """Return the original map with the standard PvP overlay (the shipped match map)."""
    base = load_world_map(ORIGINAL_MAP_PATH)
    return apply_overlay(base, default_pvp_overlay(base))


def ai_vs_ai_scenario() -> Scenario:
    """Return the default PvP scenario with both seats controlled by the engine AI."""
    return dataclasses.replace(
        default_pvp_scenario(), player_one_controller="ai", player_two_controller="ai"
    )


# --- the scripted baseline -----------------------------------------------------------


def baseline_plan(
    state: GameState,
    memory: AiMemory,
    world: WorldMap,
    rules: EngineRules,
    seed: int,
) -> tuple[tuple[Command, ...], AiMemory]:
    """The fixed scripted baseline; same signature as :func:`nether_earth.ai.planner.plan`.

    ``world`` is the effective world the seat hook passes. ``seed`` and the
    memory are unused: the baseline keeps no state and draws nothing.
    """
    del seed
    player = memory.player_id
    commands: list[Command] = []
    if state.construction_session_for(player) is not None:
        commands.append(CancelConstructionCommand(player=player, sequence=0))

    robots = state.robots_for(player)
    owned = owned_war_bases(world, player)
    pool = state.resource_pool_for(player) or PlayerResourcePool(player_id=player)
    if (
        len(robots) < rules.max_robots_per_player
        and owned
        and pool_after(pool.to_resource_pool(), _CHEAPEST, rules) is not None
        and not isinstance(resolve_launch_exit(world, state, owned[0]), LaunchRejectionReason)
    ):
        commands.append(
            EnterConstructionRemotelyCommand(player=player, sequence=0, war_base_id=owned[0])
        )
        commands.extend(SelectModuleCommand(player=player, sequence=0, module=m) for m in _CHEAPEST)
        commands.append(LaunchRobotCommand(player=player, sequence=0))

    for robot in robots:
        wanted = SearchCaptureTarget.NEUTRAL_FACTORY
        if isinstance(robot.order, SearchCapture) and robot.order.target is wanted:
            if select_capture_target(robot, wanted, state, world) is not None:
                continue
        elif select_capture_target(robot, wanted, state, world) is not None:
            commands.append(_order(player, robot.entity_id, wanted))
            continue
        fallback = SearchCaptureTarget.ENEMY_WAR_BASE
        if not (isinstance(robot.order, SearchCapture) and robot.order.target is fallback):
            commands.append(_order(player, robot.entity_id, fallback))
    return tuple(commands), memory


def _order(player: PlayerId, robot_id: EntityId, target: SearchCaptureTarget) -> Command:
    return SetRobotOrderCommand(
        player=player, sequence=0, entity_id=robot_id, order=SearchCapture(target)
    )


#: The name ``nether_earth.ai.seat`` calls each AI seat's planner through.
_SEAT_HOOK = "plan"


@contextmanager
def seat_planners(overrides: Mapping[PlayerId, Planner] | None = None) -> Iterator[None]:
    """Route the named seats to other planners for the duration of the block.

    Seats not named keep the real planner. Restores the hook on exit.
    """
    real: Planner = getattr(seat, _SEAT_HOOK)
    table = dict(overrides or {})

    def dispatch(
        state: GameState, memory: AiMemory, world: WorldMap, rules: EngineRules, seed: int
    ) -> tuple[tuple[Command, ...], AiMemory]:
        chosen = table.get(memory.player_id, planner.plan)
        return chosen(state, memory, world, rules, seed)

    setattr(seat, _SEAT_HOOK, dispatch)
    try:
        yield
    finally:
        setattr(seat, _SEAT_HOOK, real)


def _collision_free_robot_id(state: GameState, owner: PlayerId) -> EntityId:
    prefix = f"robot-{owner.value}-"
    ordinals = [
        int(robot.entity_id.value.removeprefix(prefix))
        for robot in state.robots_for(owner)
        if robot.entity_id.value.startswith(prefix)
    ]
    return EntityId(f"{prefix}{max(ordinals, default=0) + 1}")


@contextmanager
def unique_robot_ids() -> Iterator[None]:
    """Swap in collision-free robot numbering (see the module docstring)."""
    real = robot_launch._next_robot_id
    robot_launch._next_robot_id = _collision_free_robot_id
    try:
        yield
    finally:
        robot_launch._next_robot_id = real


# --- running a match -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeatScore:
    """One seat's standing at the end of a match."""

    war_bases: int
    factories: int
    robots: int


@dataclass(frozen=True, slots=True)
class MatchResult:
    """The outcome of one headless match.

    ``winner`` is the :class:`VictoryEvent` winner, or ``None`` if the tick
    cap was reached first. ``error`` is the engine exception that ended the
    match early, if any (``ticks`` is then the last tick completed). ``digest`` is a SHA-256 over every tick's
    snapshot JSON (``None`` when not recorded).
    """

    seed: int
    ticks: int
    winner: PlayerId | None
    scores: dict[str, SeatScore]
    digest: str | None
    error: str | None = None

    @property
    def leader(self) -> PlayerId | None:
        """The winner, else the seat ahead on (war bases, factories) at the cap, else ``None``."""
        if self.winner is not None:
            return self.winner
        one, two = self.scores[PLAYER_ONE.value], self.scores[PLAYER_TWO.value]
        a, b = (one.war_bases, one.factories), (two.war_bases, two.factories)
        if a == b:
            return None
        return PLAYER_ONE if a > b else PLAYER_TWO


def run_match(
    seed: int,
    tick_cap: int,
    *,
    baseline: PlayerId | None = None,
    world: WorldMap | None = None,
    record: bool = False,
    patch_robot_ids: bool = False,
) -> MatchResult:
    """Run one AI match from the default PvP scenario until victory or ``tick_cap``.

    ``baseline`` names the seat played by :func:`baseline_plan` (``None``:
    AI vs AI). With ``record``, every tick's snapshot is folded into
    :attr:`MatchResult.digest`. With ``patch_robot_ids``, the engine's
    colliding robot numbering is worked around (:func:`unique_robot_ids`);
    without it, a collision ends the match with :attr:`MatchResult.error`.
    """
    world = world if world is not None else load_world()
    overrides: dict[PlayerId, Planner] = {baseline: baseline_plan} if baseline is not None else {}
    state = create_initial_state(ai_vs_ai_scenario(), world, seed=seed)
    digest = hashlib.sha256() if record else None
    winner: PlayerId | None = None
    error: str | None = None
    with seat_planners(overrides), unique_robot_ids() if patch_robot_ids else nullcontext():
        while state.tick < tick_cap and winner is None:
            try:
                state, events = engine.step(state, (), world=world)
            except ValueError as exc:
                error = f"{type(exc).__name__}: {exc}"
                break
            if digest is not None:
                digest.update(snapshot_to_json_string(state).encode())
            for event in events:
                if isinstance(event, VictoryEvent):
                    winner = event.winner
    final = effective_world(world, state)
    scores = {
        player.value: SeatScore(
            war_bases=sum(1 for base in final.war_bases if base.owner == player),
            factories=sum(1 for factory in final.factories if factory.owner == player),
            robots=len(state.robots_for(player)),
        )
        for player in (PLAYER_ONE, PLAYER_TWO)
    }
    return MatchResult(
        seed=seed,
        ticks=state.tick,
        winner=winner,
        scores=scores,
        digest=digest.hexdigest() if digest is not None else None,
        error=error,
    )
