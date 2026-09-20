import { test } from 'vitest';
import assert from 'node:assert/strict';
import { Store } from '../state/store.ts';
import { GameController } from './controller.ts';
import { findFixture } from '../fixtures/index.ts';
import { runFixtureMessage } from '../fixtures/harness.ts';

function boot(fixtureId: string) {
  const store = new Store();
  const controller = new GameController(store, () => 0);
  controller.startFixture(fixtureId);
  // drive the fixture synchronously instead of waiting on its timer
  for (const m of findFixture(fixtureId)!.messages) runFixtureMessage(store, m, 0);
  const sent = () => controller.recorded!.sent.filter((m) => m.type === 'command').map((m) => (m.type === 'command' ? m.payload : null));
  return { store, controller, sent };
}

test('free commander: movement and rise edges become explicit commander commands', () => {
  const { controller, sent } = boot('world-static');
  controller.move({ dx: 1, dy: 0 });
  controller.vertical(true);
  controller.vertical(false);
  assert.deepEqual(sent(), [
    { kind: 'commander_move', dx: 1, dy: 0 },
    { kind: 'commander_set_vertical_intent', rising: true },
    { kind: 'commander_set_vertical_intent', rising: false },
  ]);
});

test('docked commander: no commander_move; menu → direct control sends direct_robot_move', () => {
  const { store, controller, sent } = boot('commander-docked');
  controller.move({ dx: 0, dy: -1 });
  assert.deepEqual(sent(), []);
  controller.action('Enter');
  assert.equal(store.get().ui.menu, 'robot_menu');
  controller.action('Digit1');
  assert.equal(store.get().ui.menu, 'direct_control');
  controller.move({ dx: 0, dy: -1 });
  assert.deepEqual(sent(), [{ kind: 'direct_robot_move', dx: 0, dy: -1 }]);
  controller.action('Escape');
  assert.equal(store.get().ui.menu, 'robot_menu');
});

test('orders menu covers every order kind with canonical payloads', () => {
  const { store, controller, sent } = boot('commander-docked');
  controller.action('Enter');
  controller.action('Digit2'); // orders
  controller.action('Digit1'); // stop & defend
  controller.action('Enter');
  controller.action('Digit2');
  controller.action('Digit2'); // advance → distance
  controller.move({ dx: 1, dy: 0 }); // +1 mile
  controller.action('Enter');
  controller.action('Enter');
  controller.action('Digit2');
  controller.action('Digit3'); // retreat
  controller.action('Digit3'); // ignored: not a menu digit in distance mode
  controller.menuAction('dist', '-10');
  controller.menuAction('confirm', '');
  controller.action('Enter');
  controller.action('Digit2');
  controller.action('Digit4'); // search & capture
  controller.action('Digit2'); // enemy_factory
  controller.action('Enter');
  controller.action('Digit2');
  controller.action('Digit5'); // search & destroy
  controller.action('Digit3'); // war_base
  assert.deepEqual(sent(), [
    { kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'stop_and_defend' } },
    { kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'advance', distanceMiles: 11 } },
    { kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'retreat', distanceMiles: 1 } },
    { kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'search_capture', target: 'enemy_factory' } },
    { kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'search_destroy', target: 'war_base' } },
  ]);
  assert.equal(store.get().ui.menu, 'none');
});

test('combat: aim with movement keys, fitted weapon index, fire targets adjacent cell', () => {
  const { controller, sent } = boot('commander-docked');
  controller.action('Enter');
  controller.action('Digit3');
  controller.move({ dx: 0, dy: 1 });
  controller.action('Digit1');
  controller.action('Enter');
  assert.deepEqual(sent(), [{ kind: 'robot_fire', entityId: 'robot-1', weapon: 'cannon', targetX: 30, targetY: 11 }]);
});

test('construction: digits toggle select/deselect from authoritative build; enter launches; C cancels', () => {
  const { controller, sent } = boot('construction');
  // rewind to the mid-session snapshot (tick 200) where a session exists
  const store = new Store();
  const c2 = new GameController(store, () => 0);
  c2.startFixture('construction');
  const msgs = findFixture('construction')!.messages;
  for (const m of msgs.slice(0, 4)) runFixtureMessage(store, m, 0);
  c2.action('Digit2'); // tracks already selected → deselect
  c2.action('Digit5'); // missile → select
  c2.move({ dx: 1, dy: 0 }); // movement ignored during construction
  c2.action('Enter');
  c2.action('KeyC');
  const payloads = c2.recorded!.sent.filter((m) => m.type === 'command').map((m) => (m.type === 'command' ? m.payload : null));
  assert.deepEqual(payloads, [
    { kind: 'deselect_module', module: 'tracks' },
    { kind: 'select_module', module: 'missile' },
    { kind: 'launch_robot' },
    { kind: 'cancel_construction' },
  ]);
  assert.deepEqual(sent(), []);
  void controller;
});

test('no gameplay commands while paused or finished', () => {
  const { controller, sent } = boot('lifecycle-paused');
  controller.move({ dx: 1, dy: 0 });
  controller.vertical(true);
  assert.deepEqual(sent(), []);
});
