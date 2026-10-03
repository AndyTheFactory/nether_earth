"""Scenario metadata and deterministic player initialization.

Per `_specs/technical-spec.md` §6 ("Scenario and victory model") and
`_specs/functional-spec.md` §4.1 ("Starting state") / §10.1 ("Initial
resources"), PvP v1 uses a scenario definition kept separate from raw map
geometry. This module records that scenario data and derives the
deterministic starting player set from it.

Scope note: this module intentionally does not implement map/world
geometry, victory checking, or economy processing. ``victory_rule`` and
``factory_initial_ownership`` are recorded as scenario metadata only; the
systems they govern live in `victory.py`, `map_overlay.py` and `capture.py`.
"""

from dataclasses import dataclass
from typing import Literal

from nether_earth.capture import StructureOwnership
from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.map import WorldMap
from nether_earth.resource_pool import starting_player_resource_pool
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import AiMemory, GameState, create_game_state
from nether_earth.structures import Factory, WarBase

#: Locked v1 victory-rule identifier (`_specs/technical-spec.md` §6,
#: `_specs/functional-spec.md` §4.2): a player wins when the opponent owns
#: zero war bases. This is recorded as stable scenario data only; victory
#: is checked by `victory.py`.
VICTORY_RULE_ZERO_WAR_BASES = "zero_war_bases"

#: v1 default factory ownership: factories start neutral unless the map/
#: scenario explicitly overrides them (`_specs/functional-spec.md` §4.1).
FactoryOwnershipDefault = Literal["neutral"]

#: Per-factory ownership overrides, keyed by an id understood by the
#: eventual map/factory model (e.g. a factory id string). Left generic and
#: minimal since no factory entities exist yet; this is structured so a
#: future milestone can add per-factory entries without breaking the
#: ``Scenario`` shape.
FactoryOwnershipOverrides = dict[str, str]

#: Who drives a seat: a human client or the engine's AI planner.
#: Nothing else about the player differs, so this is scenario data, not a
#: kind of ``PlayerId``.
SeatController = Literal["human", "ai"]
_SEAT_CONTROLLERS: tuple[SeatController, ...] = ("human", "ai")


@dataclass(frozen=True, slots=True)
class Scenario:
    """Scenario metadata: identity, map/version references, and starting values.

    This is data only — no gameplay/map/victory logic lives here. Fields
    mirror the "Recommended scenario fields" in `_specs/technical-spec.md`
    §6, plus explicit map identity/version references (naming convention
    matches ``map.BootstrapMap.map_id``/``version``).
    """

    id: str
    map_id: str
    map_version: int
    player_starting_warbases: int
    starting_general_resources: int = 20
    factory_initial_ownership: FactoryOwnershipDefault | FactoryOwnershipOverrides = "neutral"
    victory_rule: str = VICTORY_RULE_ZERO_WAR_BASES
    #: Per-seat controller. Both default to ``"human"`` (PvP).
    player_one_controller: SeatController = "human"
    player_two_controller: SeatController = "human"

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("scenario id must be a non-empty string")
        if not self.map_id.strip():
            raise ValueError("scenario map_id must be a non-empty string")
        if self.map_version <= 0:
            raise ValueError("scenario map_version must be a positive integer")
        if self.player_starting_warbases < 0:
            raise ValueError("player_starting_warbases must be non-negative")
        if self.starting_general_resources < 0:
            raise ValueError("starting_general_resources must be non-negative")
        if not self.victory_rule.strip():
            raise ValueError("victory_rule must be a non-empty string")
        for controller in (self.player_one_controller, self.player_two_controller):
            if controller not in _SEAT_CONTROLLERS:
                raise ValueError(f"seat controller must be one of {_SEAT_CONTROLLERS}")

    def controller_for(self, player_id: PlayerId) -> SeatController:
        """Return who drives ``player_id``'s seat."""
        if player_id == PLAYER_ONE:
            return self.player_one_controller
        if player_id == PLAYER_TWO:
            return self.player_two_controller
        raise ValueError(f"{player_id.value!r} is not a seat of this scenario")


def default_pvp_scenario() -> Scenario:
    """Return the locked v1 two-player PvP scenario.

    Values are locked by `_specs/technical-spec.md` §6 and
    `_specs/functional-spec.md` §4/§10.1: one starting war base per
    player, 20 starting general resources (the same value ``rules.py``'s
    ``starting_general_resources`` seeds into each pool -- kept in
    lock-step by ``engine/tests/test_m9_scenario.py``), neutral
    factories, and the "opponent owns zero war bases" victory rule.
    """
    return Scenario(
        id="pvp-v1",
        map_id="zx-spectrum-original",
        map_version=1,
        player_starting_warbases=1,
        starting_general_resources=20,
        factory_initial_ownership="neutral",
        victory_rule=VICTORY_RULE_ZERO_WAR_BASES,
    )


def initialize_players(scenario: Scenario) -> tuple[PlayerId, ...]:
    """Return the deterministic v1 two-player PvP player set for ``scenario``.

    v1 scope is a fixed two-player PvP match (`PLAYER_ONE`, `PLAYER_TWO`
    from ``ids.py``); the scenario itself does not vary the participant
    set. The result is always in the same canonical order
    that :func:`create_initial_state`/``create_game_state`` produce, so
    two calls with equivalent scenarios yield an identical player tuple.
    """
    del scenario  # v1 PvP participants are fixed; scenario data does not select them yet.
    return (PLAYER_ONE, PLAYER_TWO)


def initial_ai_memories(
    scenario: Scenario, players: tuple[PlayerId, ...]
) -> tuple[AiMemory, ...]:
    """Return a fresh :class:`~nether_earth.state.AiMemory` for each AI seat among ``players``."""
    return tuple(
        AiMemory(player_id=seat)
        for seat in (PLAYER_ONE, PLAYER_TWO)
        if seat in players and scenario.controller_for(seat) == "ai"
    )


def commander_spawn_key(player_id: PlayerId) -> str:
    """Return the ``WorldMap.spawn_positions`` key naming ``player_id``'s commander start cell.

    Spawn cells are scenario-overlay data (`docs/mechanics/world-and-map.md`;
    `map_overlay.ScenarioOverlay`), keyed
    ``"<player id>_commander"`` (e.g. ``"p1_commander"``), the convention
    ``map_overlay.default_pvp_overlay`` declares.
    """
    return f"{player_id.value}_commander"


def create_initial_state(
    scenario: Scenario,
    world: WorldMap | None = None,
    *,
    seed: int = 0,
    rules: EngineRules = DEFAULT_RULES,
) -> GameState:
    """Build the deterministic tick-0 :class:`GameState` for ``scenario``.

    With ``world`` omitted this is the bare contract: tick 0, the
    canonically ordered player set from :func:`initialize_players`, nothing
    else (for callers that compose commanders/robots by hand, e.g. test
    fixtures).

    With ``world`` supplied (a :class:`~nether_earth.map.WorldMap` that
    already has the scenario overlay applied -- see
    ``map_overlay.apply_overlay``/``default_pvp_overlay``), this composes
    the full authoritative starting state `_specs/functional-spec.md` §5
    step 5 requires ("Server initializes map, scenario, resources,
    commanders, factories, and game clock"):

    - one ``FREE`` :class:`~nether_earth.commander.Commander` per human seat at
      ``rules.commander_min_altitude`` on the cell
      ``world.spawn_positions[commander_spawn_key(player)]`` -- a missing
      spawn entry is a scenario-data error, not a silent default. An AI
      seat gets no commander (owner decision 2026-09-25) and a fresh
      :class:`~nether_earth.state.AiMemory` instead;
    - one :func:`~nether_earth.resource_pool.starting_player_resource_pool`
      per player (``rules.starting_general_resources``, which
      ``Scenario.starting_general_resources`` must match -- checked here so
      scenario metadata and the rule that actually seeds pools cannot
      drift apart);
    - one :class:`~nether_earth.capture.StructureOwnership` record per war
      base/factory the overlaid ``world`` marks as owned, so the snapshot
      clients consume is self-contained (the frontend reads ownership from
      ``state.structure_ownership`` only) and
      ``capture.effective_world(world, state)`` is the identity at tick 0.

    ``world`` must describe ``scenario``'s map (same id/version). Pure and
    deterministic: equal inputs always yield an equal ``GameState``.
    """
    players = initialize_players(scenario)
    ai_memories = initial_ai_memories(scenario, players)
    if world is None:
        return create_game_state(0, players, seed=seed, ai_memories=ai_memories)

    if world.map_id != scenario.map_id or world.version != scenario.map_version:
        raise ValueError(
            f"world {world.map_id!r} v{world.version} does not match scenario "
            f"{scenario.map_id!r} v{scenario.map_version}"
        )
    if scenario.starting_general_resources != rules.starting_general_resources:
        raise ValueError(
            f"scenario.starting_general_resources={scenario.starting_general_resources} "
            f"differs from rules.starting_general_resources={rules.starting_general_resources}"
        )

    commanders: list[Commander] = []
    for player in players:
        if scenario.controller_for(player) == "ai":
            continue
        key = commander_spawn_key(player)
        if key not in world.spawn_positions:
            raise ValueError(
                f"world {world.map_id!r} declares no spawn position {key!r} for {player.value!r}"
            )
        x, y = world.spawn_positions[key]
        commanders.append(
            Commander(
                player_id=player,
                mode=CommanderMode.FREE,
                x=x,
                y=y,
                altitude=rules.commander_min_altitude,
            )
        )

    ownable: tuple[WarBase | Factory, ...] = (*world.war_bases, *world.factories)
    ownership = tuple(
        StructureOwnership(structure_id=structure.id, owner=structure.owner)
        for structure in ownable
        if structure.owner is not None
    )
    return create_game_state(
        0,
        players,
        seed=seed,
        commanders=tuple(commanders),
        resource_pools=tuple(starting_player_resource_pool(player, rules) for player in players),
        structure_ownership=ownership,
        ai_memories=ai_memories,
    )
