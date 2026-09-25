// Authoritative frontend state boundary (M8.1).
//
// Holds exactly what the technical spec §23 lists, kept in separate slots:
//   latest/previous authoritative snapshot  (engine data, verbatim)
//   lifecycle/connection state              (runtime messages, not gameplay)
//   local UI-only state                     (menus, focus, toggles)
// Nothing here validates gameplay legality; snapshots are trusted verbatim.
import type {
  ServerMessage,
  SnapshotMessage,
  SnapshotState,
  ErrorInfo,
  PlayerSummary,
} from '../../../protocol/generated/types';

export type MatchPhase =
  | 'idle'
  | 'waiting'
  | 'active'
  | 'paused'
  | 'finished'
  | 'forfeit'
  | 'no_contest';

export type ConnectionStatus =
  | 'offline'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected'
  | 'fixture';

export interface Session {
  matchId: string;
  playerId: string;
  sessionToken: string;
  joinCode: string | null;
  nickname: string;
  /** True for a solo match created with `opponent: 'computer'` (CR004.8): the second seat is the engine's AI, not a human. */
  vsComputer: boolean;
}

export interface LifecycleState {
  phase: MatchPhase;
  players: PlayerSummary[];
  pausedBy: string | null;
  graceDeadlineMs: number | null;
  winnerPlayerId: string | null;
  forfeitingPlayerId: string | null;
  resultTick: number | null;
  resultReason: string | null;
}

export interface ConnectionState {
  status: ConnectionStatus;
  session: Session | null;
  lastError: ErrorInfo | null;
  /** Monotonic counter bumped when a full snapshot replaces state (resync). */
  resyncGeneration: number;
}

export type UiScreen = 'lobby' | 'match';
export type MenuMode =
  | 'none'
  | 'robot_menu'
  | 'direct_control'
  | 'orders'
  | 'order_distance'
  | 'order_target'
  | 'combat';

export interface UiState {
  screen: UiScreen;
  menu: MenuMode;
  pendingOrder: 'advance' | 'retreat' | 'search_capture' | 'search_destroy' | null;
  distanceMiles: number;
  /** Keyboard-highlighted index into the current menu's block list (CR003.241: arrows/WASD + Space). */
  menuCursor: number;
  debugGrid: boolean;
  /** Structure name labels and robot strength numbers (CR002.23); default off. */
  labels: boolean;
  notice: string | null;
  /** Wall-clock ms when the latest snapshot arrived; visual-only. */
  latestSnapshotAtMs: number;
}

export interface AppState {
  latest: SnapshotState | null;
  previous: SnapshotState | null;
  lifecycle: LifecycleState;
  connection: ConnectionState;
  ui: UiState;
}

export function initialLifecycle(): LifecycleState {
  return {
    phase: 'idle',
    players: [],
    pausedBy: null,
    graceDeadlineMs: null,
    winnerPlayerId: null,
    forfeitingPlayerId: null,
    resultTick: null,
    resultReason: null,
  };
}

export function initialState(): AppState {
  return {
    latest: null,
    previous: null,
    lifecycle: initialLifecycle(),
    connection: { status: 'offline', session: null, lastError: null, resyncGeneration: 0 },
    ui: {
      screen: 'lobby',
      menu: 'none',
      pendingOrder: null,
      distanceMiles: 10,
      menuCursor: 0,
      debugGrid: false,
      labels: false,
      notice: null,
      latestSnapshotAtMs: 0,
    },
  };
}

type Listener = (state: AppState) => void;

export class Store {
  private state: AppState = initialState();
  private listeners = new Set<Listener>();

  get(): AppState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private commit(next: AppState): void {
    this.state = next;
    for (const l of this.listeners) l(next);
  }

  /** Per-tick snapshot: latest becomes previous so visuals can interpolate. */
  applySnapshot(msg: SnapshotMessage, nowMs: number): void {
    const s = this.state;
    if (s.latest && msg.tick < s.latest.tick) return; // stale, drop
    this.commit({
      ...s,
      previous: s.latest,
      latest: msg.state,
      ui: { ...s.ui, latestSnapshotAtMs: nowMs },
    });
  }

  /** Reconnect/full resync: atomically replaces state; no previous, no interpolation. */
  replaceSnapshot(msg: SnapshotMessage, nowMs: number): void {
    const s = this.state;
    this.commit({
      ...s,
      previous: null,
      latest: msg.state,
      connection: { ...s.connection, resyncGeneration: s.connection.resyncGeneration + 1 },
      ui: { ...s.ui, latestSnapshotAtMs: nowMs },
    });
  }

  setConnection(patch: Partial<ConnectionState>): void {
    this.commit({ ...this.state, connection: { ...this.state.connection, ...patch } });
  }

  setLifecycle(patch: Partial<LifecycleState>): void {
    this.commit({ ...this.state, lifecycle: { ...this.state.lifecycle, ...patch } });
  }

  setUi(patch: Partial<UiState>): void {
    this.commit({ ...this.state, ui: { ...this.state.ui, ...patch } });
  }

  /** Back to a fresh state; the viewer's labels preference survives. */
  reset(): void {
    const init = initialState();
    this.commit({ ...init, ui: { ...init.ui, labels: this.state.ui.labels } });
  }

  /** Route a lifecycle/error server message. Snapshot/resync go through the two snapshot methods. */
  applyServerMessage(msg: ServerMessage): void {
    const s = this.state;
    switch (msg.type) {
      case 'created':
        this.commit({
          ...s,
          connection: {
            ...s.connection,
            status: 'connected',
            lastError: null,
            session: {
              matchId: msg.matchId,
              playerId: msg.playerId,
              sessionToken: msg.sessionToken,
              joinCode: msg.joinCode,
              nickname: s.connection.session?.nickname ?? '',
              vsComputer: msg.opponent === 'computer',
            },
          },
          lifecycle: { ...s.lifecycle, phase: 'waiting' },
        });
        return;
      case 'joined':
        this.commit({
          ...s,
          connection: {
            ...s.connection,
            status: 'connected',
            lastError: null,
            session: {
              matchId: msg.matchId,
              playerId: msg.playerId,
              sessionToken: msg.sessionToken,
              joinCode: s.connection.session?.joinCode ?? null,
              nickname: s.connection.session?.nickname ?? '',
              vsComputer: false,
            },
          },
          lifecycle: { ...s.lifecycle, phase: 'waiting' },
        });
        return;
      case 'ready_state':
        this.setLifecycle({ players: [...msg.players] });
        return;
      case 'started':
        this.commit({
          ...s,
          lifecycle: { ...s.lifecycle, phase: 'active', pausedBy: null, graceDeadlineMs: null },
          ui: { ...s.ui, screen: 'match' },
        });
        return;
      case 'paused':
        this.setLifecycle({
          phase: 'paused',
          pausedBy: msg.disconnectedPlayerId,
          graceDeadlineMs: msg.graceDeadlineMs,
        });
        return;
      case 'resumed':
        this.setLifecycle({ phase: 'active', pausedBy: null, graceDeadlineMs: null });
        return;
      case 'forfeit':
        this.setLifecycle({
          phase: 'forfeit',
          winnerPlayerId: msg.winnerPlayerId,
          forfeitingPlayerId: msg.forfeitingPlayerId,
          resultReason: msg.reason,
        });
        return;
      case 'no_contest':
        this.setLifecycle({ phase: 'no_contest', resultReason: msg.reason });
        return;
      case 'finished':
        this.setLifecycle({ phase: 'finished', winnerPlayerId: msg.winnerPlayerId, resultTick: msg.tick });
        return;
      case 'error':
        this.setConnection({ lastError: msg.error });
        return;
    }
  }
}

// ---- read-model helpers (pure lookups over authoritative state; no derivation of rules) ----

export function myCommander(state: SnapshotState, playerId: string) {
  return state.commanders.find((c) => c.player_id === playerId) ?? null;
}

export function myResources(state: SnapshotState, playerId: string) {
  return state.resource_pools.find((p) => p.player_id === playerId) ?? null;
}

export function myConstruction(state: SnapshotState, playerId: string) {
  return state.construction_sessions.find((c) => c.player_id === playerId) ?? null;
}

export function dockedRobot(state: SnapshotState, playerId: string) {
  const c = myCommander(state, playerId);
  if (!c || c.mode !== 'docked' || !c.docked_robot_id) return null;
  return state.robots.find((r) => r.entity_id === c.docked_robot_id) ?? null;
}

export function structureOwner(state: SnapshotState, structureId: string): string | null {
  return state.structure_ownership.find((o) => o.structure_id === structureId)?.owner ?? null;
}

export function isDestroyed(state: SnapshotState, structureId: string): boolean {
  return state.structure_destruction.includes(structureId);
}

/** Game clock derived from ticks only (technical spec §4: 120 ticks per in-game hour). */
export const TICKS_PER_HOUR = 120;
export const HOURS_PER_DAY = 24;

export function gameClock(tick: number): { day: number; hour: number; minute: number } {
  const hoursTotal = Math.floor(tick / TICKS_PER_HOUR);
  const minute = Math.floor(((tick % TICKS_PER_HOUR) / TICKS_PER_HOUR) * 60);
  return { day: Math.floor(hoursTotal / HOURS_PER_DAY) + 1, hour: hoursTotal % HOURS_PER_DAY, minute };
}
