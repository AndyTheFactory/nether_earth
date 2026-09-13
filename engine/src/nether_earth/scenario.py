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

from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.state import GameState, create_game_state

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
    starting_general_resources: int = 30
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
    `_specs/functional-spec.md` §4.1/§10.1: one starting war base per
    player, 30 starting general resources, neutral factories, and the
    "opponent owns zero war bases" victory rule.
    """
    return Scenario(
        id="pvp-v1",
        map_id="zx-spectrum-original",
        map_version=1,
        player_starting_warbases=1,
        starting_general_resources=30,
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


def create_initial_state(scenario: Scenario) -> GameState:
    """Build the deterministic tick-0 :class:`GameState` for ``scenario``.

    This proves that scenario-driven initialization is deterministic: the
    same scenario always yields a canonical-equivalent ``GameState`` with
    tick 0 and the canonically ordered player set from
    :func:`initialize_players`. Composing resources/economy onto
    ``GameState`` is out of scope for this issue (see issue #7 and later).
    """
    return create_game_state(0, initialize_players(scenario))
