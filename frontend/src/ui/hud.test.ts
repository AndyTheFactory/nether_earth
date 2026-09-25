// @vitest-environment happy-dom
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { Store } from '../state/store.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { Panel } from './dom.ts';
import { renderHud } from './hud.ts';
import type { SnapshotMessage } from '../../../protocol/generated/types';

const map = loadMap(DEFAULT_MAP_ID);

// CR004.6: the AI seat has no commander and none is ever shown. A match
// snapshot against a computer opponent carries one `commanders` entry (the
// human's own) and an `ai_memories` entry for the computer seat -- never a
// second commander. The HUD only ever renders the *viewer's own* commander
// (`myCommander`), so this exercises that a commanderless second seat does
// not crash HUD rendering for either the human viewer or (degenerately) the
// AI seat's own id.
function aiSeatSnapshot(tick: number): SnapshotMessage {
  return {
    protocolVersion: 1,
    type: 'snapshot',
    matchId: 'm',
    tick,
    state: {
      tick,
      players: ['p1', 'p2'],
      seed: 1,
      commanders: [
        { player_id: 'p1', mode: 'free', x: 1, y: 1, altitude: 1, docked_robot_id: null, rising: false, horizontal_transition: null, vertical_transition: null, elevate_updates_remaining: 0 },
      ],
      resource_pools: [
        { player_id: 'p1', general: 10, chassis: 0, electronics: 0, nuclear: 0, missile: 0, phaser: 0, cannon: 0 },
        { player_id: 'p2', general: 10, chassis: 0, electronics: 0, nuclear: 0, missile: 0, phaser: 0, cannon: 0 },
      ],
      construction_sessions: [],
      robots: [],
      structure_ownership: [],
      capture_progress: [],
      projectiles: [],
      structure_destruction: [],
      scenery_debris: [],
      ai_memories: [{ player_id: 'p2', construction: {}, orders: {} }],
    },
  };
}

test('the HUD renders an AI-seat match for the human viewer without a commander line for the opponent', () => {
  const store = new Store();
  store.applyServerMessage({ protocolVersion: 1, type: 'created', matchId: 'm', joinCode: 'ABCD', playerId: 'p1', sessionToken: 't' });
  store.applyServerMessage({ protocolVersion: 1, type: 'started', matchId: 'm', tick: 0 });
  store.applySnapshot(aiSeatSnapshot(4), 0);

  const panel = new Panel('hud');
  assert.doesNotThrow(() => renderHud(panel, store.get(), map));
  // The human's own commander line is present ...
  assert.match(panel.root.innerHTML, /commander \(1,1\)/);
  // ... and nothing crashes looking up the AI seat's (nonexistent) commander.
  assert.doesNotThrow(() => renderHud(panel, { ...store.get(), connection: { ...store.get().connection, session: { ...store.get().connection.session!, playerId: 'p2' } } }, map));
});
