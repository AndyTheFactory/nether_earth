"""Shared protocol field types and envelope pieces.

Mirrors every scalar/object `$def` in `protocol/schemas/common.schema.json`.
Message-specific envelopes (client/server/snapshot/reconnect) live in their
own sibling modules and import these types -- this is the single source for
shared shapes so they cannot silently diverge across message families.

Architecture note (AGENTS.md): this module is a transport/serialization
boundary only. It does not validate gameplay legality; `CommandPayload`/
`SnapshotState` below are the real, fully enumerated command/state shapes
(issue #98), exactly matching the JSON Schema they mirror. An adapter that
turns a validated `CommandPayload` into a concrete engine `Command`
subclass lives in `app.transport.commands` -- this module only defines the
wire shapes, never gameplay legality.

Naming: wire JSON is camelCase (per the schemas); Python attribute access on
every model is snake_case, matching `app.match`'s existing convention
(`Match.match_id`, `PlayerSlot.session_token`, ...). `ProtocolModel` below
is the shared base that makes that translation automatic via `pydantic`'s
`to_camel` alias generator -- individual models never hand-write aliases.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

#: Mirrors common.schema.json `$defs.protocolVersion` (`const: 1`).
ProtocolVersion = Literal[1]
PROTOCOL_VERSION: ProtocolVersion = 1

MatchId = Annotated[str, Field(min_length=1)]
JoinCode = Annotated[str, Field(min_length=1)]
PlayerId = Annotated[str, Field(min_length=1)]
EntityId = Annotated[str, Field(min_length=1)]
SessionToken = Annotated[str, Field(min_length=1)]
Nickname = Annotated[str, Field(min_length=1, max_length=32)]
SequenceNumber = Annotated[int, Field(ge=0)]
Tick = Annotated[int, Field(ge=0)]
TimestampMs = Annotated[int, Field(ge=0)]
ErrorCode = Annotated[str, Field(min_length=1)]

#: Mirrors common.schema.json `$defs.cellDelta`: a single classic
#: 4-directional grid step component.
CellDelta = Literal[-1, 0, 1]

#: Mirrors common.schema.json `$defs.moduleIdentity`
#: (`nether_earth.robot_build.ModuleIdentity`'s eight values).
ModuleIdentityWire = Literal[
    "bipod", "tracks", "anti_grav", "cannon", "missile", "phaser", "nuclear", "electronics"
]

#: Mirrors common.schema.json `$defs.weaponIdentity`.
WeaponIdentityWire = Literal["cannon", "missile", "phaser", "nuclear"]

#: Mirrors common.schema.json `$defs.searchCaptureTarget`
#: (`nether_earth.orders.SearchCaptureTarget`).
SearchCaptureTargetWire = Literal["neutral_factory", "enemy_factory", "enemy_war_base"]

#: Mirrors common.schema.json `$defs.searchDestroyTarget`
#: (`nether_earth.orders.SearchDestroyTarget`).
SearchDestroyTargetWire = Literal["robot", "factory", "war_base"]


class ProtocolModel(BaseModel):
    """Shared base for every protocol model.

    `alias_generator=to_camel` derives each field's wire name (`matchId`)
    from its Python (snake_case) name (`match_id`) automatically, so the
    JSON stays schema-conformant while Python call sites use the same
    snake_case convention as `app.match`. `populate_by_name=True` lets code
    construct instances with either the snake_case field name or the
    camelCase alias. Subclasses layer their own `extra=` policy on top (this
    merges with, rather than replaces, this base's `model_config`).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ProtocolEnvelope(ProtocolModel):
    """The bare envelope: only the required `protocolVersion` const.

    Mirrors common.schema.json's top-level object exactly
    (`protocolVersion` required, `additionalProperties: false`). Every real
    message defined in the sibling modules extends this shape with its own
    `type` discriminator; this class itself is only a message in its own
    right for the `protocol/fixtures/common/` fixture set exercised by the
    schema-conformance tests.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: ProtocolVersion


class ErrorInfo(ProtocolModel):
    """Mirrors common.schema.json `$defs.errorInfo`."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: Annotated[str, Field(min_length=1)]
    details: dict[str, Any] | None = None


class PlayerSummary(ProtocolModel):
    """Mirrors common.schema.json `$defs.playerSummary`."""

    model_config = ConfigDict(extra="forbid")

    player_id: PlayerId
    nickname: Nickname
    ready: bool


##############################################################################
# Robot orders (mirrors common.schema.json `$defs.robotOrder`, issue #98)
##############################################################################


class StopAndDefendOrderPayload(ProtocolModel):
    """Mirrors `$defs.robotOrder`'s `StopAndDefendOrder` variant
    (`nether_earth.orders.StopAndDefend`)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["stop_and_defend"]


class AdvanceOrderPayload(ProtocolModel):
    """Mirrors `$defs.robotOrder`'s `AdvanceOrder` variant
    (`nether_earth.orders.Advance`).

    No `targetX`: it is engine-bound state (the `PENDING` -> `ACTIVE`
    transition), never a player input -- see `orders.py`'s own docstrings.

    `distance_miles` deliberately carries no `ge`/`le` bound here (issue #98
    review, Important I2): `orders.py`'s own
    `MAX_ORDER_DISTANCE_MILES`/`order_is_valid` already enforce the 0-50-mile
    range, and the locked functional-spec §16 response to an out-of-range
    order is not "reject the frame" -- it is "accept the command and store
    `StopAndDefend` with `OrderStatus.FALLBACK`" (a real, observable engine
    outcome via `RobotOrderChangedEvent`). A transport-layer bound would
    reject the frame before the engine ever saw it, making that documented
    fallback unreachable from any real client. Structural validation here is
    limited to "is this an integer at all" -- the same division of
    responsibility the module docstring's "no adapter may make a legality
    decision" rule already establishes for commands.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["advance"]
    distance_miles: int


class RetreatOrderPayload(ProtocolModel):
    """Mirrors `$defs.robotOrder`'s `RetreatOrder` variant
    (`nether_earth.orders.Retreat`). See `AdvanceOrderPayload`'s docstring
    for why `distance_miles` carries no `ge`/`le` bound here."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["retreat"]
    distance_miles: int


class SearchCaptureOrderPayload(ProtocolModel):
    """Mirrors `$defs.robotOrder`'s `SearchCaptureOrder` variant
    (`nether_earth.orders.SearchCapture`)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["search_capture"]
    target: SearchCaptureTargetWire


class SearchDestroyOrderPayload(ProtocolModel):
    """Mirrors `$defs.robotOrder`'s `SearchDestroyOrder` variant
    (`nether_earth.orders.SearchDestroy`)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["search_destroy"]
    target: SearchDestroyTargetWire


#: Mirrors common.schema.json `$defs.robotOrder`'s five-member `oneOf`.
RobotOrderPayload = Annotated[
    StopAndDefendOrderPayload
    | AdvanceOrderPayload
    | RetreatOrderPayload
    | SearchCaptureOrderPayload
    | SearchDestroyOrderPayload,
    Field(discriminator="kind"),
]


##############################################################################
# Gameplay command payloads (mirrors common.schema.json `$defs.commandPayload`,
# issue #98). One variant per concrete `nether_earth.commands.Command`
# subclass a player can trigger in v1 (see `engine.step`'s `isinstance`
# dispatch for the exhaustive list). Each payload is a thin field-shape
# mirror of its engine dataclass -- it carries no legality decision; see
# `app.transport.commands` for the adapter that turns an accepted payload
# into the matching engine `Command` (a pure, non-deciding translation).
##############################################################################


class CommanderMoveCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.commander_movement.CommanderMoveCommand`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["commander_move"]
    dx: CellDelta
    dy: CellDelta


class CommanderSetVerticalIntentCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.commander_movement.CommanderSetVerticalIntentCommand`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["commander_set_vertical_intent"]
    rising: bool


class DirectRobotMoveCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.direct_control.DirectRobotMoveCommand`.

    No `entityId`: exactly one robot is ever directly controllable (the one
    the issuing player's commander is currently docked to), resolved
    server-side -- see the engine dataclass's own docstring.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["direct_robot_move"]
    dx: CellDelta
    dy: CellDelta


class RobotFireCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.combat.FireCommand`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["robot_fire"]
    entity_id: EntityId
    weapon: WeaponIdentityWire
    target_x: int
    target_y: int


class SetRobotOrderCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.orders.SetRobotOrderCommand`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["set_robot_order"]
    entity_id: EntityId
    order: RobotOrderPayload


class SelectModuleCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.construction_commands.SelectModuleCommand`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["select_module"]
    module: ModuleIdentityWire


class DeselectModuleCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.construction_commands.DeselectModuleCommand`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["deselect_module"]
    module: ModuleIdentityWire


class CancelConstructionCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.construction_commands.CancelConstructionCommand`
    (no fields beyond the discriminator)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cancel_construction"]


class LaunchRobotCommandPayload(ProtocolModel):
    """Mirrors `nether_earth.construction_commands.LaunchRobotCommand`
    (no fields beyond the discriminator)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["launch_robot"]


#: Mirrors common.schema.json `$defs.commandPayload`'s nine-member `oneOf`.
CommandPayload = Annotated[
    CommanderMoveCommandPayload
    | CommanderSetVerticalIntentCommandPayload
    | DirectRobotMoveCommandPayload
    | RobotFireCommandPayload
    | SetRobotOrderCommandPayload
    | SelectModuleCommandPayload
    | DeselectModuleCommandPayload
    | CancelConstructionCommandPayload
    | LaunchRobotCommandPayload,
    Field(discriminator="kind"),
]


##############################################################################
# Snapshot state (mirrors common.schema.json `$defs.snapshotState`, issue #98)
##############################################################################
#
# `SnapshotEntity`/`SnapshotState` deliberately do NOT use `ProtocolModel`'s
# `alias_generator=to_camel`: this payload is engine data
# (`nether_earth.snapshot.to_snapshot`'s own return shape) passed through the
# transport boundary verbatim, snake_case keys included -- see
# `app.transport.snapshots`' "thin field-mapping layer" contract and
# `common.schema.json`'s `snapshotState` $def docstring for why re-casing it
# would misrepresent what is actually on the wire. Nested per-entity shapes
# are typed one level deep, matching `to_snapshot`'s own per-entity helper
# functions; a few deeply-nested/highly-polymorphic leaves (order variants,
# resource-pool categories, build stacks) are left as loosely-typed
# `dict[str, Any]` rather than re-deriving the engine's own full nested
# schema a second time here (see `protocol/README.md`).


class _SnapshotSubModel(BaseModel):
    """Shared base for snapshot sub-shapes: plain snake_case fields, no alias generation."""

    model_config = ConfigDict(extra="forbid")


class CommanderSnapshot(_SnapshotSubModel):
    player_id: PlayerId
    mode: Literal["free", "docked"]
    x: int
    y: int
    altitude: int
    docked_robot_id: EntityId | None
    rising: bool
    horizontal_transition: dict[str, Any] | None
    vertical_transition: dict[str, Any] | None
    elevate_updates_remaining: int = Field(ge=0)


class ResourcePoolSnapshot(_SnapshotSubModel):
    player_id: PlayerId
    general: int
    chassis: int
    electronics: int
    nuclear: int
    missile: int
    phaser: int
    cannon: int


class ConstructionSessionSnapshot(_SnapshotSubModel):
    player_id: PlayerId
    war_base_id: EntityId
    entry_tick: int
    build: dict[str, Any]
    buffer: dict[str, Any]
    entry_snapshot: dict[str, Any]


class RobotSnapshot(_SnapshotSubModel):
    entity_id: EntityId
    owner: PlayerId
    x: int
    y: int
    build: dict[str, Any]
    stack: list[ModuleIdentityWire]
    height: int
    movement: dict[str, Any] | None
    order: dict[str, Any] | None
    active_projectile_id: EntityId | None
    strength: int
    last_fire_tick: int | None


class StructureOwnershipSnapshot(_SnapshotSubModel):
    structure_id: EntityId
    owner: PlayerId


class CaptureProgressSnapshot(_SnapshotSubModel):
    structure_id: EntityId
    capturing_player: PlayerId
    robot_id: EntityId
    elapsed_ticks: int
    required_ticks: int


class ProjectileSnapshot(_SnapshotSubModel):
    id: EntityId
    owner: PlayerId
    source_robot_id: EntityId
    weapon: WeaponIdentityWire
    x: int
    y: int
    z: int
    dx: CellDelta
    dy: CellDelta
    travelled_cells: int
    max_range_cells: int
    created_tick: int
    first_advance_tick: int


class SnapshotState(_SnapshotSubModel):
    """Mirrors `nether_earth.snapshot.to_snapshot(state)`'s exact return shape.

    See the section docstring above for why this is plain snake_case, not
    `ProtocolModel`'s camelCase-aliased convention.
    """

    tick: Tick
    players: list[PlayerId]
    seed: int
    commanders: list[CommanderSnapshot]
    resource_pools: list[ResourcePoolSnapshot]
    construction_sessions: list[ConstructionSessionSnapshot]
    robots: list[RobotSnapshot]
    structure_ownership: list[StructureOwnershipSnapshot]
    capture_progress: list[CaptureProgressSnapshot]
    projectiles: list[ProjectileSnapshot]
    structure_destruction: list[EntityId]
    #: Blocker ids turned into rough debris by a nuclear blast (CR002.18).
    scenery_debris: list[EntityId]
