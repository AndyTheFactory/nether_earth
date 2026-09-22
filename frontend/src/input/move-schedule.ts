// Held-key commander move scheduling (CR003.5). Timing only: it decides WHEN
// the held direction is sent, never whether a move is legal. The engine still
// accepts or rejects every command it receives.
//
// The engine rejects a commander_move while a horizontal transition is in
// flight, so a blind fixed-rate pulse only chains cells when a command happens
// to land in the right tick. Instead, while a move is in flight nothing is
// sent until the estimated server tick is within MOVE_LEAD_TICKS of its end;
// from then on (and whenever no move is in flight) the direction is re-sent
// every MOVE_RESEND_MS. Half a tick between sends means every server tick
// interval receives at least one command despite frame/timer jitter, so the
// first tick at which the engine can start the next cell always has one.
// Commands that arrive early are simply rejected by the engine; the lead
// covers the round trip so the command is not late.
import { TICK_MS, type GridTransition } from '../render/interpolation.ts';

/** Ticks before the in-flight move's end at which re-sending starts (covers ~RTT). */
export const MOVE_LEAD_TICKS = 3;
/** Minimum gap between two commander_move sends while a direction is held. */
export const MOVE_RESEND_MS = TICK_MS / 2;

export interface MoveScheduleInput {
  nowMs: number;
  /** Tick of the latest authoritative snapshot and when it arrived. */
  latestTick: number;
  latestSnapshotAtMs: number;
  /** The commander's in-flight horizontal transition, if any. */
  transition: GridTransition | null;
  /** When the previous commander_move was sent (-Infinity if never). */
  lastSentMs: number;
  leadTicks?: number;
}

/** Estimated current server tick: latest snapshot tick plus elapsed wall-clock ticks. */
export function estimatedServerTick(latestTick: number, latestSnapshotAtMs: number, nowMs: number): number {
  return latestTick + Math.max(0, nowMs - latestSnapshotAtMs) / TICK_MS;
}

/** Whether the held direction should be sent as a commander_move now. */
export function shouldSendCommanderMove(i: MoveScheduleInput): boolean {
  if (i.nowMs - i.lastSentMs < MOVE_RESEND_MS) return false;
  const t = i.transition;
  if (!t) return true;
  const end = t.started_tick + t.duration_ticks;
  return estimatedServerTick(i.latestTick, i.latestSnapshotAtMs, i.nowMs) >= end - (i.leadTicks ?? MOVE_LEAD_TICKS);
}
