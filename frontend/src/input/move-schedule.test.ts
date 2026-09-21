import { test } from 'vitest';
import assert from 'node:assert/strict';
import { MOVE_LEAD_TICKS, MOVE_RESEND_MS, estimatedServerTick, shouldSendCommanderMove } from './move-schedule.ts';
import { TICK_MS, type GridTransition } from '../render/interpolation.ts';

const move = (started: number): GridTransition => ({ from_x: 0, from_y: 0, to_x: 1, to_y: 0, started_tick: started, duration_ticks: 4 });

test('estimated server tick advances with wall clock since the latest snapshot', () => {
  assert.equal(estimatedServerTick(10, 1000, 1000), 10);
  assert.equal(estimatedServerTick(10, 1000, 1000 + TICK_MS * 1.5), 11.5);
  assert.equal(estimatedServerTick(10, 1000, 900), 10);
});

test('no move in flight: send now, then at most once per resend interval', () => {
  const base = { latestTick: 5, latestSnapshotAtMs: 0, transition: null };
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: 0, lastSentMs: -Infinity }), true);
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: MOVE_RESEND_MS - 1, lastSentMs: 0 }), false);
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: MOVE_RESEND_MS, lastSentMs: 0 }), true);
});

test('move in flight: hold until the estimated tick is within the lead of its end', () => {
  // started 10, ends 14; snapshot for tick 10 arrived at t=0.
  const base = { latestTick: 10, latestSnapshotAtMs: 0, transition: move(10), lastSentMs: -Infinity };
  const openAt = (14 - MOVE_LEAD_TICKS - 10) * TICK_MS;
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: 0 }), false);
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: openAt - 1 }), false);
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: openAt }), true);
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: openAt + 1, lastSentMs: openAt }), false);
  assert.equal(shouldSendCommanderMove({ ...base, nowMs: openAt + MOVE_RESEND_MS, lastSentMs: openAt }), true);
  // a later snapshot of the same transition moves the estimate forward too
  assert.equal(shouldSendCommanderMove({ ...base, latestTick: 12, latestSnapshotAtMs: 500, nowMs: 500 }), true);
  assert.equal(shouldSendCommanderMove({ ...base, leadTicks: 0, latestTick: 13, latestSnapshotAtMs: 0, nowMs: 0 }), false);
});

/**
 * Held key against a model of the server: a fixed 50 ms tick, commands
 * applied at the first step after they arrive, a commander_move rejected while
 * a move is in flight, and completions resolved before commands in the same
 * step (engine.step order, CR003.10). Snapshots and commands each take `oneWayMs`.
 */
function holdKey(oneWayMs: number, pollMs: number, jitterMs: number, cells: number): GridTransition[] {
  const starts: GridTransition[] = [];
  let transition: GridTransition | null = null;
  let snapTick = 0;
  let snapTransition: GridTransition | null = null;
  let snapAt = -Infinity;
  let lastSent = -Infinity;
  const inbound: number[] = []; // command arrival times at the server
  const snapshots: { at: number; tick: number; t: GridTransition | null }[] = [];
  let nextStep = TICK_MS;
  let tick = 0;
  let seed = 7;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
  for (let now = 0; starts.length < cells && now < 60_000; now += pollMs + (rand() - 0.5) * jitterMs) {
    while (nextStep <= now) {
      tick += 1;
      const arrived = inbound.filter((a) => a <= nextStep).length;
      inbound.splice(0, arrived);
      if (transition && tick >= transition.started_tick + transition.duration_ticks) transition = null;
      if (arrived && !transition) {
        transition = move(tick);
        starts.push(transition);
      }
      snapshots.push({ at: nextStep + oneWayMs, tick, t: transition });
      nextStep += TICK_MS;
    }
    while (snapshots.length && snapshots[0].at <= now) {
      const s = snapshots.shift()!;
      snapTick = s.tick;
      snapTransition = s.t;
      snapAt = s.at;
    }
    if (snapAt === -Infinity) continue;
    if (shouldSendCommanderMove({ nowMs: now, latestTick: snapTick, latestSnapshotAtMs: snapAt, transition: snapTransition, lastSentMs: lastSent })) {
      lastSent = now;
      inbound.push(now + oneWayMs);
    }
  }
  return starts;
}

test('held key: each cell starts on the first tick the engine accepts it (10 cells)', () => {
  for (const oneWayMs of [1, 20, 45]) {
    const starts = holdKey(oneWayMs, 1000 / 60, 8, 10);
    assert.equal(starts.length, 10);
    for (let i = 1; i < starts.length; i++) {
      const prevEnd = starts[i - 1].started_tick + starts[i - 1].duration_ticks;
      // The engine resolves completions before applying commands within a
      // step (CR003.10), so a new move starts on the previous one's end tick.
      assert.equal(starts[i].started_tick, prevEnd, `latency ${oneWayMs} ms, cell ${i}`);
    }
  }
});
