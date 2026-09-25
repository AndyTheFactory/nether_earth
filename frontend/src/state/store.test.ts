import { test } from 'vitest';
import assert from 'node:assert/strict';
import { Store, gameClock, dockedRobot, myCommander, myResources, myConstruction } from './store.ts';
import type { SnapshotMessage, SnapshotState } from '../../../protocol/generated/types';

function snap(tick: number, extra: Partial<SnapshotState> = {}): SnapshotMessage {
  return {
    protocolVersion: 1,
    type: 'snapshot',
    matchId: 'm',
    tick,
    state: {
      tick,
      players: ['p1', 'p2'],
      seed: 1,
      commanders: [],
      resource_pools: [],
      construction_sessions: [],
      robots: [],
      structure_ownership: [],
      capture_progress: [],
      projectiles: [],
      structure_destruction: [],
      scenery_debris: [],
      ...extra,
    },
  };
}

test('applySnapshot shifts latest into previous and drops stale ticks', () => {
  const s = new Store();
  s.applySnapshot(snap(1), 0);
  s.applySnapshot(snap(2), 50);
  assert.equal(s.get().latest?.tick, 2);
  assert.equal(s.get().previous?.tick, 1);
  s.applySnapshot(snap(1), 100);
  assert.equal(s.get().latest?.tick, 2);
});

test('replaceSnapshot clears previous and bumps resync generation', () => {
  const s = new Store();
  s.applySnapshot(snap(5), 0);
  s.applySnapshot(snap(6), 50);
  s.replaceSnapshot(snap(9), 100);
  assert.equal(s.get().previous, null);
  assert.equal(s.get().latest?.tick, 9);
  assert.equal(s.get().connection.resyncGeneration, 1);
});

test('lifecycle messages never touch authoritative snapshot', () => {
  const s = new Store();
  s.applySnapshot(snap(3), 0);
  s.applyServerMessage({ protocolVersion: 1, type: 'paused', matchId: 'm', disconnectedPlayerId: 'p2', graceDeadlineMs: 60000 });
  assert.equal(s.get().lifecycle.phase, 'paused');
  assert.equal(s.get().latest?.tick, 3);
  s.applyServerMessage({ protocolVersion: 1, type: 'resumed', matchId: 'm', tick: 3 });
  assert.equal(s.get().lifecycle.phase, 'active');
  s.applyServerMessage({ protocolVersion: 1, type: 'forfeit', matchId: 'm', forfeitingPlayerId: 'p2', winnerPlayerId: 'p1', reason: 'disconnect_timeout' });
  assert.equal(s.get().lifecycle.phase, 'forfeit');
  assert.equal(s.get().lifecycle.winnerPlayerId, 'p1');
});

test('created/joined bind a session; error is recorded without altering session', () => {
  const s = new Store();
  s.applyServerMessage({ protocolVersion: 1, type: 'created', matchId: 'm', joinCode: 'ABCD', playerId: 'p1', sessionToken: 't' });
  assert.equal(s.get().connection.session?.joinCode, 'ABCD');
  assert.equal(s.get().lifecycle.phase, 'waiting');
  s.applyServerMessage({ protocolVersion: 1, type: 'error', error: { code: 'invalid_message', message: 'bad' } });
  assert.equal(s.get().connection.lastError?.code, 'invalid_message');
  assert.equal(s.get().connection.session?.matchId, 'm');
});

test('gameClock derives from ticks only', () => {
  assert.deepEqual(gameClock(0), { day: 1, hour: 0, minute: 0 });
  assert.deepEqual(gameClock(120 * 25 + 60), { day: 2, hour: 1, minute: 30 });
});

test('dockedRobot resolves via authoritative docked_robot_id only', () => {
  const state = snap(1, {
    commanders: [{ player_id: 'p1', mode: 'docked', x: 1, y: 1, altitude: 4, docked_robot_id: 'r1', rising: false, horizontal_transition: null, vertical_transition: null, elevate_updates_remaining: 0 }],
    robots: [{ entity_id: 'r1', owner: 'p1', x: 1, y: 1, build: {}, stack: ['bipod'], height: 4, movement: null, order: null, active_projectile_id: null, strength: 100, last_fire_tick: null, exit_steps_remaining: 0, facing: 'south' as const, turning: null }],
  }).state;
  assert.equal(dockedRobot(state, 'p1')?.entity_id, 'r1');
  assert.equal(dockedRobot(state, 'p2'), null);
});

// CR004.6: the AI seat has no commander and none is ever shown -- a snapshot
// carries one `commanders` entry (the human's) plus an `ai_memories` entry
// for the computer seat (CR004.3/#284). Every read-model helper must
// tolerate `p2` never appearing in `commanders` (an engine shape the store
// had never seen before this seat existed).
test('read-model helpers tolerate an AI seat with no commander', () => {
  const state = snap(4, {
    commanders: [{ player_id: 'p1', mode: 'free', x: 1, y: 1, altitude: 1, docked_robot_id: null, rising: false, horizontal_transition: null, vertical_transition: null, elevate_updates_remaining: 0 }],
    ai_memories: [{ player_id: 'p2', construction: { last_war_base_id: null }, orders: { defences: [], sightings: [] } }],
  }).state;
  assert.equal(myCommander(state, 'p1')?.player_id, 'p1');
  assert.equal(myCommander(state, 'p2'), null);
  assert.equal(dockedRobot(state, 'p2'), null);
  assert.equal(myResources(state, 'p2'), null);
  assert.equal(myConstruction(state, 'p2'), null);
});
