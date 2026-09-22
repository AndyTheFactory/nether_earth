import { test } from 'vitest';
import assert from 'node:assert/strict';
import { Store } from '../state/store.ts';
import { GameController, CURSOR_REPEAT_MS } from './controller.ts';
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

test('construction: digits toggle select/deselect from authoritative build; enter fires at the cursor; C cancels', () => {
  const { controller, sent } = boot('construction');
  // rewind to the mid-session snapshot (tick 200) where a session exists
  const store = new Store();
  const c2 = new GameController(store, () => 0);
  c2.startFixture('construction');
  const msgs = findFixture('construction')!.messages;
  for (const m of msgs.slice(0, 4)) runFixtureMessage(store, m, 0);
  c2.action('Digit2'); // tracks already selected → deselect
  c2.action('Digit5'); // missile → select
  c2.move({ dx: 1, dy: 0 }); // cursor already on the rightmost column: ignored, no command
  c2.action('Enter'); // fire on the starting cursor piece: BIPOD
  c2.action('KeyC');
  const payloads = c2.recorded!.sent.filter((m) => m.type === 'command').map((m) => (m.type === 'command' ? m.payload : null));
  assert.deepEqual(payloads, [
    { kind: 'deselect_module', module: 'tracks' },
    { kind: 'select_module', module: 'missile' },
    { kind: 'select_module', module: 'bipod' },
    { kind: 'cancel_construction' },
  ]);
  assert.deepEqual(sent(), []);
  void controller;
});

function constructionBoot() {
  let clock = 0;
  const store = new Store();
  const c = new GameController(store, () => clock);
  c.startFixture('construction');
  for (const m of findFixture('construction')!.messages.slice(0, 4)) runFixtureMessage(store, m, 0);
  const payloads = () => c.recorded!.sent.filter((m) => m.type === 'command').map((m) => (m.type === 'command' ? m.payload : null));
  const step = (intent: { dx: -1 | 0 | 1; dy: -1 | 0 | 1 }) => {
    clock += CURSOR_REPEAT_MS;
    c.move(intent);
  };
  return { c, payloads, step, tick: (ms: number) => (clock += ms) };
}

test('construction: arrows walk the Spectrum cursor; space/enter fire on piece, START ROBOT and EXIT MENU', () => {
  const { c, payloads, step } = constructionBoot();
  step({ dx: 0, dy: -1 }); // up: TRACKS
  step({ dx: 0, dy: -1 }); // up: ANTI-GRAV
  c.vertical(true); // space = fire, never a rise intent while the screen is open
  c.vertical(false);
  step({ dx: -1, dy: 0 }); // START ROBOT
  step({ dx: 0, dy: -1 }); // up/down do nothing off the piece column
  c.action('Enter');
  step({ dx: -1, dy: 0 }); // EXIT MENU
  step({ dx: -1, dy: 0 }); // already leftmost: ignored
  c.vertical(true);
  assert.deepEqual(payloads(), [{ kind: 'select_module', module: 'anti_grav' }, { kind: 'launch_robot' }, { kind: 'cancel_construction' }]);
  step({ dx: 1, dy: 0 });
  step({ dx: 1, dy: 0 }); // back on the pieces: the piece row is kept
  assert.deepEqual(c.buildCursor, { entryTick: 190, column: 2, piece: 2 });
});

test('construction: a held arrow repeats every 200 ms, as the Spectrum pause loop does', () => {
  const { c, tick } = constructionBoot();
  c.move({ dx: 0, dy: -1 });
  tick(50);
  c.move({ dx: 0, dy: -1 }); // key-repeat pulse inside the pause: ignored
  assert.equal(c.buildCursor!.piece, 1);
  tick(CURSOR_REPEAT_MS);
  c.move({ dx: 0, dy: -1 });
  assert.equal(c.buildCursor!.piece, 2);
});

test('construction: clicking an option moves the cursor there and fires', () => {
  const { c, payloads } = constructionBoot();
  c.constructionPick(2, 6); // NUCLEAR
  c.constructionPick(2, 1); // TRACKS (fitted) → deselect
  c.constructionPick(1, 0);
  c.constructionPick(0, 0);
  assert.deepEqual(payloads(), [
    { kind: 'select_module', module: 'nuclear' },
    { kind: 'deselect_module', module: 'tracks' },
    { kind: 'launch_robot' },
    { kind: 'cancel_construction' },
  ]);
});

test('construction: Escape is EXIT MENU (cancel_construction)', () => {
  const store = new Store();
  const c = new GameController(store, () => 0);
  c.startFixture('construction');
  const msgs = findFixture('construction')!.messages;
  for (const m of msgs.slice(0, 4)) runFixtureMessage(store, m, 0);
  c.action('Escape');
  const payloads = c.recorded!.sent.filter((m) => m.type === 'command').map((m) => (m.type === 'command' ? m.payload : null));
  assert.deepEqual(payloads, [{ kind: 'cancel_construction' }]);
});

// ---- #241: arrows/WASD move the menu cursor, Space activates it, and neither leaks to the commander ----

test('robot menu: arrows/WASD move the highlighted block and Space activates it, without moving the commander', () => {
  const { store, controller, sent } = boot('commander-docked');
  controller.action('Enter');
  assert.equal(store.get().ui.menu, 'robot_menu');
  assert.equal(store.get().ui.menuCursor, 0); // DIRECT CONTROL

  controller.move({ dx: 0, dy: 1 }); // Down / S
  assert.equal(store.get().ui.menuCursor, 1); // GIVE ORDERS
  controller.move({ dx: 0, dy: 1 });
  assert.equal(store.get().ui.menuCursor, 2); // COMBAT MODE
  controller.move({ dx: 0, dy: 1 });
  assert.equal(store.get().ui.menuCursor, 3); // LEAVE ROBOT
  controller.move({ dx: 0, dy: 1 }); // wraps back to the top
  assert.equal(store.get().ui.menuCursor, 0);
  controller.move({ dx: 0, dy: -1 }); // Up / W wraps the other way
  assert.equal(store.get().ui.menuCursor, 3);

  controller.move({ dx: 0, dy: -1 }); // Up: back to COMBAT MODE
  assert.equal(store.get().ui.menuCursor, 2);
  controller.vertical(true); // Space activates COMBAT MODE
  controller.vertical(false);
  assert.equal(store.get().ui.menu, 'combat');

  // No commander_move / direct_robot_move / vertical intent leaked while the menu was open.
  assert.deepEqual(sent(), []);
});

test('robot menu: Space on LEAVE ROBOT sends the same rise command as holding the rise key', () => {
  const { store, controller, sent } = boot('commander-docked');
  controller.action('Enter');
  controller.move({ dx: 0, dy: -1 }); // Up wraps to LEAVE ROBOT (index 3)
  assert.equal(store.get().ui.menuCursor, 3);
  controller.vertical(true);
  assert.deepEqual(sent(), [{ kind: 'commander_set_vertical_intent', rising: true }]);
  assert.equal(store.get().ui.menu, 'none');
  controller.vertical(false); // key release, menu already closed: normal rise-off path
  assert.deepEqual(sent(), [
    { kind: 'commander_set_vertical_intent', rising: true },
    { kind: 'commander_set_vertical_intent', rising: false },
  ]);
});

test('orders sub-menu: arrows move the cursor and Space sends the highlighted order', () => {
  const { store, controller, sent } = boot('commander-docked');
  controller.action('Enter');
  controller.action('Digit2'); // GIVE ORDERS
  assert.equal(store.get().ui.menu, 'orders');
  assert.equal(store.get().ui.menuCursor, 0);
  controller.move({ dx: 0, dy: 1 }); // STOP AND DEFEND → ADVANCE
  controller.move({ dx: 0, dy: 1 }); // → RETREAT
  controller.move({ dx: 0, dy: 1 }); // → SEARCH & CAPTURE
  assert.equal(store.get().ui.menuCursor, 3);
  controller.vertical(true); // Space picks SEARCH & CAPTURE, opening the target list
  controller.vertical(false);
  assert.equal(store.get().ui.menu, 'order_target');
  assert.equal(store.get().ui.menuCursor, 0);
  controller.move({ dx: 0, dy: 1 }); // NEUTRAL FACTORY → ENEMY FACTORY
  controller.vertical(true);
  controller.vertical(false); // key release: no stray rising:false either, since Space only ever picked blocks
  assert.deepEqual(sent(), [{ kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'search_capture', target: 'enemy_factory' } }]);
  assert.equal(store.get().ui.menu, 'none');
});

test('no gameplay commands while paused or finished', () => {
  const { controller, sent } = boot('lifecycle-paused');
  controller.move({ dx: 1, dy: 0 });
  controller.vertical(true);
  assert.deepEqual(sent(), []);
});

test('Alt+Q (#250) leaves the match from anywhere, even with a menu open', () => {
  const { store, controller } = boot('commander-docked');
  controller.action('Enter'); // open the robot menu first: the shortcut must still work
  assert.equal(store.get().ui.menu, 'robot_menu');
  assert.equal(store.get().ui.screen, 'match');
  controller.action('Alt+KeyQ');
  assert.equal(store.get().ui.screen, 'lobby');
  assert.equal(store.get().connection.session, null);
  assert.deepEqual(
    controller.recorded!.sent.map((m) => m.type),
    ['leave'],
  );
});

test('plain Q and other Alt combos do not leave the match', () => {
  const { store, controller } = boot('commander-docked');
  controller.action('KeyQ');
  controller.action('Alt+KeyW');
  assert.equal(store.get().ui.screen, 'match');
  assert.deepEqual(controller.recorded!.sent, []);
});
