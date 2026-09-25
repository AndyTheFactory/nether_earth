// Generated from protocol/schemas/*.schema.json. Do not edit by hand.

export type ClientMessage =
  ClientCreateMatch | ClientJoinMatch | ClientSetReady | ClientLeaveMatch | ClientGameplayCommand;

export type ReconnectMessage = ClientReconnect | ServerResync;

export type ServerMessage =
  | ServerCreated
  | ServerJoined
  | ServerReadyState
  | ServerStarted
  | ServerPaused
  | ServerResumed
  | ServerForfeit
  | ServerNoContest
  | ServerFinished
  | ServerError;

export interface ClientCreateMatch {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "create";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "nickname".
   */
  nickname: string;
}
export interface ClientJoinMatch {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "join";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "joinCode".
   */
  joinCode: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "nickname".
   */
  nickname: string;
}
export interface ClientSetReady {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "ready";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sessionToken".
   */
  sessionToken: string;
  ready: boolean;
}
export interface ClientLeaveMatch {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "leave";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sessionToken".
   */
  sessionToken: string;
}
/**
 * Envelope for in-match gameplay commands. `payload` is the discriminated commandPayload union (issue #98, common.schema.json); matchId/playerId/sessionToken/clientSequence are stable and did not change when the real payload variants were added.
 */
export interface ClientGameplayCommand {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "command";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sessionToken".
   */
  sessionToken: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sequenceNumber".
   */
  clientSequence: number;
  /**
   * Discriminated gameplay command payload (issue #98). One variant per concrete nether_earth.commands.Command subclass a player can trigger in v1: commanderMove, commanderSetVerticalIntent, directRobotMove, robotFire, setRobotOrder, selectModule, deselectModule, cancelConstruction, launchRobot. Each variant is a thin field-shape mirror of its engine Command dataclass; it carries no legality decision of its own (see AGENTS.md's adapter rule) -- an illegal command still parses here and is rejected structurally/gameplay-wise by the engine, not by this schema.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "commandPayload".
   */
  payload:
    | CommanderMoveCommandPayload
    | CommanderSetVerticalIntentCommandPayload
    | DirectRobotMoveCommandPayload
    | RobotFireCommandPayload
    | SetRobotOrderCommandPayload
    | SelectModuleCommandPayload
    | DeselectModuleCommandPayload
    | CancelConstructionCommandPayload
    | LaunchRobotCommandPayload;
}
/**
 * Mirrors nether_earth.commander_movement.CommanderMoveCommand.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "commanderMoveCommandPayload".
 */
export interface CommanderMoveCommandPayload {
  kind: "commander_move";
  /**
   * A single classic 4-directional grid step: exactly one of dx/dy is nonzero, each restricted to {-1,0,1}. Mirrors nether_earth's CommanderMoveCommand/DirectRobotMoveCommand `__post_init__` shape check -- structural, not a legality decision.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "cellDelta".
   */
  dx: -1 | 0 | 1;
  /**
   * A single classic 4-directional grid step: exactly one of dx/dy is nonzero, each restricted to {-1,0,1}. Mirrors nether_earth's CommanderMoveCommand/DirectRobotMoveCommand `__post_init__` shape check -- structural, not a legality decision.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "cellDelta".
   */
  dy: -1 | 0 | 1;
}
/**
 * Mirrors nether_earth.commander_movement.CommanderSetVerticalIntentCommand.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "commanderSetVerticalIntentCommandPayload".
 */
export interface CommanderSetVerticalIntentCommandPayload {
  kind: "commander_set_vertical_intent";
  rising: boolean;
}
/**
 * Mirrors nether_earth.direct_control.DirectRobotMoveCommand. No entityId: exactly one robot is ever directly controllable (the one the issuing player's commander is currently docked to), resolved server-side.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "directRobotMoveCommandPayload".
 */
export interface DirectRobotMoveCommandPayload {
  kind: "direct_robot_move";
  /**
   * A single classic 4-directional grid step: exactly one of dx/dy is nonzero, each restricted to {-1,0,1}. Mirrors nether_earth's CommanderMoveCommand/DirectRobotMoveCommand `__post_init__` shape check -- structural, not a legality decision.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "cellDelta".
   */
  dx: -1 | 0 | 1;
  /**
   * A single classic 4-directional grid step: exactly one of dx/dy is nonzero, each restricted to {-1,0,1}. Mirrors nether_earth's CommanderMoveCommand/DirectRobotMoveCommand `__post_init__` shape check -- structural, not a legality decision.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "cellDelta".
   */
  dy: -1 | 0 | 1;
}
/**
 * Mirrors nether_earth.combat.FireCommand. There is no target: a shot travels in the firing robot's own facing (owner decision, 2026-09-23), so aiming means turning the robot first.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "robotFireCommandPayload".
 */
export interface RobotFireCommandPayload {
  kind: "robot_fire";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "entityId".
   */
  entityId: string;
  /**
   * The subset of moduleIdentity that is a weapon (nether_earth.combat.FireCommand.weapon).
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "weaponIdentity".
   */
  weapon: "cannon" | "missile" | "phaser" | "nuclear";
}
/**
 * Mirrors nether_earth.orders.SetRobotOrderCommand.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "setRobotOrderCommandPayload".
 */
export interface SetRobotOrderCommandPayload {
  kind: "set_robot_order";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "entityId".
   */
  entityId: string;
  /**
   * Mirrors nether_earth.orders.Order's five-member union, tagged with an explicit `kind` discriminator (the union itself has no discriminator field in the engine; `kind` is this protocol's own serialization tag, matching nether_earth.snapshot's own `_order_snapshot` tokens). `targetX` is never supplied by a client: it is engine-bound state (PENDING -> ACTIVE), not a player input -- see SetRobotOrderCommand/Advance/Retreat's own docstrings for why a caller-supplied target_x would bypass the 0-50-mile validation.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "robotOrder".
   */
  order: StopAndDefendOrder | AdvanceOrder | RetreatOrder | SearchCaptureOrder | SearchDestroyOrder;
}
export interface StopAndDefendOrder {
  kind: "stop_and_defend";
}
export interface AdvanceOrder {
  kind: "advance";
  distanceMiles: number;
}
export interface RetreatOrder {
  kind: "retreat";
  distanceMiles: number;
}
export interface SearchCaptureOrder {
  kind: "search_capture";
  /**
   * Mirrors nether_earth.orders.SearchCaptureTarget.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "searchCaptureTarget".
   */
  target: "neutral_factory" | "enemy_factory" | "enemy_war_base";
}
export interface SearchDestroyOrder {
  kind: "search_destroy";
  /**
   * Mirrors nether_earth.orders.SearchDestroyTarget.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "searchDestroyTarget".
   */
  target: "robot" | "factory" | "war_base";
}
/**
 * Mirrors nether_earth.construction_commands.SelectModuleCommand.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "selectModuleCommandPayload".
 */
export interface SelectModuleCommandPayload {
  kind: "select_module";
  /**
   * Mirrors nether_earth.robot_build.ModuleIdentity's eight canonical values.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "moduleIdentity".
   */
  module: "bipod" | "tracks" | "anti_grav" | "cannon" | "missile" | "phaser" | "nuclear" | "electronics";
}
/**
 * Mirrors nether_earth.construction_commands.DeselectModuleCommand.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "deselectModuleCommandPayload".
 */
export interface DeselectModuleCommandPayload {
  kind: "deselect_module";
  /**
   * Mirrors nether_earth.robot_build.ModuleIdentity's eight canonical values.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "moduleIdentity".
   */
  module: "bipod" | "tracks" | "anti_grav" | "cannon" | "missile" | "phaser" | "nuclear" | "electronics";
}
/**
 * Mirrors nether_earth.construction_commands.CancelConstructionCommand (no fields beyond the discriminator).
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "cancelConstructionCommandPayload".
 */
export interface CancelConstructionCommandPayload {
  kind: "cancel_construction";
}
/**
 * Mirrors nether_earth.construction_commands.LaunchRobotCommand (no fields beyond the discriminator).
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "launchRobotCommandPayload".
 */
export interface LaunchRobotCommandPayload {
  kind: "launch_robot";
}

export interface ProtocolCommon {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
}
/**
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "errorInfo".
 */
export interface ErrorInfo {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "errorCode".
   */
  code: string;
  message: string;
  details?: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "playerSummary".
 */
export interface PlayerSummary {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "nickname".
   */
  nickname: string;
  ready: boolean;
}
/**
 * Mirrors nether_earth.snapshot.to_snapshot(state)'s exact return shape verbatim, including its snake_case keys (this is engine data passed through the transport boundary unmodified, per app.transport.snapshots's 'thin field-mapping layer' contract -- it is deliberately NOT re-cased to camelCase like every other protocol field, so a client can compare it byte-for-byte against engine-side snapshot fixtures/tests). Nested per-entity shapes are typed one level deep (matching to_snapshot's own per-entity helper functions); a few deeply-nested/highly-polymorphic leaves (order variants, resource pool categories, build stacks) are intentionally left as loosely-typed objects/arrays rather than re-deriving the engine's own full nested schema here -- see protocol/README.md.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "snapshotState".
 */
export interface SnapshotState {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "tick".
   */
  tick: number;
  players: string[];
  seed: number;
  commanders: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    player_id: string;
    mode: "free" | "docked";
    x: number;
    y: number;
    altitude: number;
    docked_robot_id: string | null;
    rising: boolean;
    horizontal_transition: {
      [k: string]: unknown;
    } | null;
    vertical_transition: {
      [k: string]: unknown;
    } | null;
    elevate_updates_remaining: number;
  }[];
  resource_pools: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    player_id: string;
    general: number;
    chassis: number;
    electronics: number;
    nuclear: number;
    missile: number;
    phaser: number;
    cannon: number;
  }[];
  construction_sessions: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    player_id: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    war_base_id: string;
    entry_tick: number;
    build: {
      [k: string]: unknown;
    };
    buffer: {
      [k: string]: unknown;
    };
    entry_snapshot: {
      [k: string]: unknown;
    };
  }[];
  robots: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    entity_id: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    owner: string;
    x: number;
    y: number;
    build: {
      [k: string]: unknown;
    };
    stack: ("bipod" | "tracks" | "anti_grav" | "cannon" | "missile" | "phaser" | "nuclear" | "electronics")[];
    height: number;
    movement: {
      [k: string]: unknown;
    } | null;
    order: {
      [k: string]: unknown;
    } | null;
    active_projectile_id: string | null;
    strength: number;
    last_fire_tick: number | null;
    exit_steps_remaining: number;
    /**
     * Mirrors nether_earth.robot.RobotFacing. Presentation-only: the cardinal direction a robot's body faces, which selects one of the four per-piece Spectrum sprites. No rule reads it.
     *
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "robotFacing".
     */
    facing: "east" | "west" | "south" | "north";
    turning: {
      [k: string]: unknown;
    } | null;
  }[];
  structure_ownership: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    structure_id: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    owner: string;
  }[];
  capture_progress: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    structure_id: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    capturing_player: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    robot_id: string;
    elapsed_ticks: number;
    required_ticks: number;
  }[];
  projectiles: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    id: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    owner: string;
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "entityId".
     */
    source_robot_id: string;
    /**
     * The subset of moduleIdentity that is a weapon (nether_earth.combat.FireCommand.weapon).
     *
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "weaponIdentity".
     */
    weapon: "cannon" | "missile" | "phaser" | "nuclear";
    x: number;
    y: number;
    z: number;
    /**
     * A single classic 4-directional grid step: exactly one of dx/dy is nonzero, each restricted to {-1,0,1}. Mirrors nether_earth's CommanderMoveCommand/DirectRobotMoveCommand `__post_init__` shape check -- structural, not a legality decision.
     *
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "cellDelta".
     */
    dx: -1 | 0 | 1;
    /**
     * A single classic 4-directional grid step: exactly one of dx/dy is nonzero, each restricted to {-1,0,1}. Mirrors nether_earth's CommanderMoveCommand/DirectRobotMoveCommand `__post_init__` shape check -- structural, not a legality decision.
     *
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "cellDelta".
     */
    dy: -1 | 0 | 1;
    travelled_cells: number;
    max_range_cells: number;
    created_tick: number;
    first_advance_tick: number;
  }[];
  structure_destruction: string[];
  /**
   * Ids of map blockers a nuclear blast turned into rough debris (CR002.18), canonical order. Their cells are rough terrain and no longer block.
   */
  scenery_debris: string[];
  /**
   * One entry per AI seat's AiMemory (CR004.3/#284), canonical (player-sorted) order. Omitted entirely for an all-human match -- this property is deliberately NOT in `required` below, matching to_snapshot's own elision. `player_id` is strict; `construction`/`orders` are deliberately left open objects (`additionalProperties` unset, i.e. allowed) rather than fully enumerated, because CR004.4/CR004.5 fill their fields on the engine side only (this schema is not their file ownership -- see wave-c-common.md) and this schema must accept both the CR004.3 stub shape (`{}`) and each sub-planner's real shape without another edit. Known fields are listed for documentation/tooling value, none `required`: `construction.last_war_base_id` (CR004.4, `_ai_construction_memory_snapshot`) is the war base id the construction planner last acted on, or null; `orders.defences`/`orders.sightings` (CR004.5, `_ai_order_memory_snapshot`) are the robot-order planner's per-tick defence assignments and enemy sightings.
   */
  ai_memories?: {
    /**
     * This interface was referenced by `ProtocolCommon`'s JSON-Schema
     * via the `definition` "playerId".
     */
    player_id: string;
    construction: {
      /**
       * CR004.4: the war base id the construction planner last acted on, or null.
       */
      last_war_base_id?: string | null;
      [k: string]: unknown;
    };
    orders: {
      /**
       * CR004.5: AiDefenceAssignment entries -- which robot is defending which structure against which intruder.
       */
      defences?: {
        /**
         * This interface was referenced by `ProtocolCommon`'s JSON-Schema
         * via the `definition` "entityId".
         */
        defender_id?: string;
        /**
         * This interface was referenced by `ProtocolCommon`'s JSON-Schema
         * via the `definition` "entityId".
         */
        intruder_id?: string;
        /**
         * This interface was referenced by `ProtocolCommon`'s JSON-Schema
         * via the `definition` "entityId".
         */
        structure_id?: string;
        approached?: boolean;
        [k: string]: unknown;
      }[];
      /**
       * CR004.5: AiSighting entries -- an enemy robot last seen and its distance.
       */
      sightings?: {
        /**
         * This interface was referenced by `ProtocolCommon`'s JSON-Schema
         * via the `definition` "entityId".
         */
        robot_id?: string;
        distance?: number;
        [k: string]: unknown;
      }[];
      [k: string]: unknown;
    };
  }[];
}
/**
 * Sent by a client re-establishing a WebSocket connection to an existing match after a disconnect.
 */
export interface ClientReconnect {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "reconnect";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sessionToken".
   */
  sessionToken: string;
}
/**
 * Sent in response to a successful reconnect, carrying the current authoritative snapshot per the locked disconnect/reconnect policy.
 */
export interface ServerResync {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "resync";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  snapshot: SnapshotMessage;
}

export interface SnapshotMessage {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "snapshot";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "tick".
   */
  tick: number;
  state: SnapshotState;
}
/**
 * Sent to the creating player only, in response to a client create command.
 */
export interface ServerCreated {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "created";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "joinCode".
   */
  joinCode: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sessionToken".
   */
  sessionToken: string;
}
/**
 * Sent to the joining player only, acknowledging their own identity/session. Roster/readiness for both players is broadcast separately via readyState.
 */
export interface ServerJoined {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "joined";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  playerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "sessionToken".
   */
  sessionToken: string;
}
/**
 * Broadcast to all connected players whenever match roster or readiness changes.
 */
export interface ServerReadyState {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "ready_state";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * @minItems 1
   * @maxItems 2
   */
  players: [PlayerSummary] | [PlayerSummary, PlayerSummary];
}
export interface ServerStarted {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "started";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "tick".
   */
  tick: number;
}
/**
 * Sent when the match pauses because a player disconnected. graceDeadlineMs is wall-clock runtime metadata, not gameplay tick state.
 */
export interface ServerPaused {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "paused";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  disconnectedPlayerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "timestampMs".
   */
  graceDeadlineMs: number;
}
/**
 * Sent when simulation resumes because both players are connected again.
 */
export interface ServerResumed {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "resumed";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "tick".
   */
  tick: number;
}
/**
 * Sent when a disconnected player's reconnect grace deadline expires while the opponent remains eligible to win.
 */
export interface ServerForfeit {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "forfeit";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  forfeitingPlayerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  winnerPlayerId: string;
  reason: "disconnect_timeout";
}
/**
 * Sent when both players disconnect and both independent reconnect grace deadlines expire without either returning.
 */
export interface ServerNoContest {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "no_contest";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  reason: "disconnect_timeout_both";
}
/**
 * Sent when the match ends because a player owns zero war bases (normal victory), per functional-spec.md #4.
 */
export interface ServerFinished {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "finished";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "playerId".
   */
  winnerPlayerId: string;
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "tick".
   */
  tick: number;
}
/**
 * matchId is omitted for errors that occur before a match context exists (e.g. malformed create/join).
 */
export interface ServerError {
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "protocolVersion".
   */
  protocolVersion: 1;
  type: "error";
  /**
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "matchId".
   */
  matchId?: string;
  error: ErrorInfo;
}
