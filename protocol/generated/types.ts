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
 * Generic envelope for in-match gameplay commands. The payload shape is a placeholder extended by issue #98; matchId/playerId/sessionToken/clientSequence are stable and do not change when payload variants are added.
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
   * Discriminated gameplay command payload. Issue #98 extends this oneOf with concrete payload variants (commander_input/construction_action/robot_control_action/robot_order/robot_fire) once the M5/M6 engine command surface is enumerated; the client gameplay command envelope in client_messages.schema.json does not need to change when that happens.
   *
   * This interface was referenced by `ProtocolCommon`'s JSON-Schema
   * via the `definition` "commandPayload".
   */
  payload: PlaceholderCommandPayload;
}
/**
 * Placeholder payload shape. Not a real gameplay command; kept only so the envelope/generation pipeline has a concrete variant to validate against until issue #98 lands.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "placeholderCommandPayload".
 */
export interface PlaceholderCommandPayload {
  kind: "placeholder";
  [k: string]: unknown;
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
 * Placeholder authoritative engine state shape. Full field enumeration (players/resources/commanders/robots/ownership/projectiles/map+scenario+rules versions/result per technical-spec.md #22) is added by issue #98.
 *
 * This interface was referenced by `ProtocolCommon`'s JSON-Schema
 * via the `definition` "snapshotState".
 */
export interface SnapshotState {
  [k: string]: unknown;
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
