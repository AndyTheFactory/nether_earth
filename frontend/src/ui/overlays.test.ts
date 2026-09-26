// @vitest-environment happy-dom
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { Store } from '../state/store.ts';
import { Panel } from './dom.ts';
import { renderOverlay } from './overlays.ts';

// M5 (final-review fix wave): the result overlay must name the AI seat
// "Computer", the same way the HUD does (`playerLabel`, CR004.8), instead of
// showing its bare player id ("Winner: p2") the way a human opponent would
// be shown.
test('the finished overlay names the AI seat "Computer" as the winner, not its bare player id', () => {
  const store = new Store();
  store.applyServerMessage({ protocolVersion: 1, type: 'created', matchId: 'm', joinCode: null, playerId: 'p1', sessionToken: 't', opponent: 'computer' });
  store.applyServerMessage({ protocolVersion: 1, type: 'started', matchId: 'm', tick: 0 });
  store.applyServerMessage({ protocolVersion: 1, type: 'finished', matchId: 'm', winnerPlayerId: 'p2', tick: 100 });

  const panel = new Panel('overlay');
  renderOverlay(panel, store.get(), Date.now());

  assert.match(panel.root.innerHTML, /Winner: Computer/);
  assert.doesNotMatch(panel.root.innerHTML, /Winner: p2/);
});

// A human PvP winner must still be shown by their real player id (no
// "Computer" naming leaks into an ordinary PvP match).
test('the finished overlay names a human winner by their player id', () => {
  const store = new Store();
  store.applyServerMessage({ protocolVersion: 1, type: 'created', matchId: 'm', joinCode: 'ABCD', playerId: 'p1', sessionToken: 't' });
  store.applyServerMessage({ protocolVersion: 1, type: 'started', matchId: 'm', tick: 0 });
  store.applyServerMessage({ protocolVersion: 1, type: 'finished', matchId: 'm', winnerPlayerId: 'p2', tick: 100 });

  const panel = new Panel('overlay');
  renderOverlay(panel, store.get(), Date.now());

  assert.match(panel.root.innerHTML, /Winner: p2/);
});
