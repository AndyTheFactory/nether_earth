"""Scenario metadata and deterministic player initialization.

Per `_specs/technical-spec.md` §6 ("Scenario and victory model") and
`_specs/functional-spec.md` §4.1 ("Starting state") / §10.1 ("Initial
resources"), PvP v1 uses a scenario definition kept separate from raw map
geometry. This module records that scenario data and derives the
deterministic starting player set from it.

Scope note: this module intentionally does not implement map/world
geometry, victory checking, or economy processing. ``victory_rule`` and
``factory_initial_ownership`` are recorded as scenario metadata only,
because the entities they will govern (war bases, factories) do not exist
until later milestones (M2+).
"""

from dataclasses import dataclass
from typing import Literal

from nether_earth.capture import StructureOwnership
from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.map import WorldMap
from nether_earth.resource_pool import starting_player_resource_pool
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import Factory, WarBase

#: Locked v1 victory-rule identifier (`_specs/technical-spec.md` §6,
#: `_specs/functional-spec.md` §4.2): a player wins when the opponent owns
#: zero war bases. This is recorded as stable scenario data only; the
#: engine does not check victory in this issue (no war-base entities exist
#: yet).
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


@dataclass(frozen=True, slots=True)
class Scenario:
    """Scenario metadata: identity, map/version references, and starting values.

    This is data only — no gameplay/map/victory logic lives here. Fields
    mirror the "Recommended scenario fields" in `_specs/technical-spec.md`
    §6, plus explicit map identity/version references (naming convention
    matches ``map.BootstrapMap.map_id``/``version`` from M0).
    """

    id: str
    map_id: str
    map_version: int
    player_starting_warbases: int
    starting_general_resources: int = 20
    factory_initial_ownership: FactoryOwnershipDefault | FactoryOwnershipOverrides = "neutral"
    victory_rule: str = VICTORY_RULE_ZERO_WAR_BASES

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


def default_pvp_scenario() -> Scenario:
    """Return the locked v1 two-player PvP scenario.

    Values are locked by `_specs/technical-spec.md` §6 and
    `_specs/functional-spec.md` §4/§10.1: one starting war base per
    player, 20 starting general resources (the same value ``rules.py``'s
    ``starting_general_resources`` seeds into each pool -- M9.2 keeps the
    two in lock-step via ``engine/tests/test_m9_scenario.py``), neutral
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
    set in this issue. The result is always in the same canonical order
    that :func:`create_initial_state`/``create_game_state`` produce, so
    two calls with equivalent scenarios yield an identical player tuple.
    """
    del scenario  # v1 PvP participants are fixed; scenario data does not select them yet.
    return (PLAYER_ONE, PLAYER_TWO)


def commander_spawn_key(player_id: PlayerId) -> str:
    """Return the ``WorldMap.spawn_positions`` key naming ``player_id``'s commander start cell.

    Spawn cells are scenario-overlay data (`_specs/milestones/02-map-world-model.md`
    "scenario spawn/reference positions"; `map_overlay.ScenarioOverlay`), keyed
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

    With ``world`` omitted this is the bare M1 contract: tick 0, the
    canonically ordered player set from :func:`initialize_players`, nothing
    else (kept for the M1 tests and for callers that compose commanders/
    robots by hand, e.g. the M3-M6 milestone fixtures).

    With ``world`` supplied (a :class:`~nether_earth.map.WorldMap` that
    already has the scenario overlay applied -- see
    ``map_overlay.apply_overlay``/``default_pvp_overlay``), this composes
    the full authoritative starting state `_specs/functional-spec.md` §5
    step 5 requires ("Server initializes map, scenario, resources,
    commanders, factories, and game clock"):

    - one ``FREE`` :class:`~nether_earth.commander.Commander` per player at
      ``rules.commander_min_altitude`` on the cell
      ``world.spawn_positions[commander_spawn_key(player)]`` -- a missing
      spawn entry is a scenario-data error, not a silent default;
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
    if world is None:
        return create_game_state(0, players, seed=seed)

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
    )
