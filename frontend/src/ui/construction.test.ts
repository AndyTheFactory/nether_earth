// CR002.9: the ROBOT CONSTRUCTION view model, cursor and icons.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { findFixture } from '../fixtures/index.ts';
import { runFixtureMessage } from '../fixtures/harness.ts';
import { Store } from '../state/store.ts';
import { ICON_SIZE, moduleIcon } from './construction-icons.ts';
import { ROBOT_SPRITES } from '../render/robot-sprites.ts';
import { COL_EXIT, COL_PIECES, COL_START, MODULE_COSTS, PIECES, constructionView, cursorFor, cursorTarget, moveCursor, previewBitmap, screenScale, SCREEN_W, SCREEN_H } from './construction.ts';

function session() {
  const store = new Store();
  for (const m of findFixture('construction')!.messages.slice(0, 4)) runFixtureMessage(store, m, 0);
  return store.get().latest!.construction_sessions[0];
}

const ink = (bm: { w: number; h: number; get(x: number, y: number): number }) => {
  let n = 0;
  let top = bm.h;
  for (let y = 0; y < bm.h; y++) for (let x = 0; x < bm.w; x++) if (bm.get(x, y)) (n++, (top = Math.min(top, y)));
  return { n, top };
};

test('module costs are the engine rules defaults (generated from rules.py, not UI constants)', () => {
  const rules = readFileSync(resolve(__dirname, '../../../engine/src/nether_earth/rules.py'), 'utf8');
  for (const m of PIECES) assert.match(rules, new RegExp(`^\\s+module_cost_${m}: int = ${MODULE_COSTS[m]}\\s*$`, 'm'), m);
});

test('view: RESOURCES AVAILABLE is the session buffer; pieces listed top to bottom with engine costs', () => {
  const v = constructionView(session(), cursorFor(null, 190));
  assert.deepEqual(
    v.resources.map((r) => `${r.label} ${r.value}`),
    ['GENERAL 4', 'ELECTRONICS 0', 'NUCLEAR 0', 'PHASERS 0', 'MISSILES 0', 'CANNON 2', 'CHASSIS 1'],
  );
  assert.equal(v.total, 7);
  assert.deepEqual(
    v.pieces.map((p) => `${p.label} ${p.cost}`),
    ['ELECTRONICS 3', 'NUCLEAR 20', 'PHASERS 4', 'MISSILES 4', 'CANNON 2', 'ANTI-GRAV 10', 'TRACKS 5', 'BIPOD 3'],
  );
  assert.deepEqual(
    v.pieces.filter((p) => p.selected).map((p) => p.module),
    ['cannon', 'tracks'],
  );
  assert.deepEqual(v.stack, ['tracks', 'cannon']);
  // Every visit starts on BIPOD (Lc85d: cursor column 2, piece 0).
  assert.deepEqual(
    v.pieces.filter((p) => p.cursor).map((p) => p.module),
    ['bipod'],
  );
  assert.equal(v.exitCursor || v.startCursor, false);
});

test('cursor: Lcb00 bounds and column rules', () => {
  let c = cursorFor(null, 5);
  assert.deepEqual(c, { entryTick: 5, column: COL_PIECES, piece: 0 });
  assert.equal(moveCursor(c, 0, 1), c, 'down from BIPOD stays');
  assert.equal(moveCursor(c, 1, 0), c, 'right of the pieces stays');
  for (let i = 0; i < 7; i++) c = moveCursor(c, 0, -1);
  assert.deepEqual(cursorTarget(c), { kind: 'piece', module: 'electronics' });
  assert.equal(moveCursor(c, 0, -1), c, 'up from ELECTRONICS stays');
  c = moveCursor(c, -1, 0);
  assert.equal(c.column, COL_START);
  assert.deepEqual(cursorTarget(c), { kind: 'start' });
  assert.equal(moveCursor(c, 0, 1), c, 'up/down only on the piece column');
  c = moveCursor(c, -1, 0);
  assert.equal(c.column, COL_EXIT);
  assert.deepEqual(cursorTarget(c), { kind: 'exit' });
  assert.equal(moveCursor(c, -1, 0), c);
  c = moveCursor(moveCursor(c, 1, 0), 1, 0);
  assert.deepEqual(cursorTarget(c), { kind: 'piece', module: 'electronics' }, 'piece row survives a visit to EXIT/START');
  assert.deepEqual(cursorFor(c, 6), { entryTick: 6, column: COL_PIECES, piece: 0 }, 'a new session starts fresh');
});

test('icons: each module icon is its own decoded robot piece sprite, facing west', () => {
  // The icons are the robot's real pieces (owner request, 2026-09-24), not
  // hand-drawn stand-ins, so the construction screen and the battlefield
  // agree on what a piece looks like. Heights differ per piece, unlike the
  // old fixed square, so only the width is uniform.
  const seen = new Set<string>();
  for (const m of PIECES) {
    const bm = moduleIcon(m);
    const rows = ROBOT_SPRITES[m].west;
    assert.equal(bm.w, ICON_SIZE);
    assert.equal(bm.h, rows.length);
    assert.ok(ink(bm).n > 60, m);
    // The bitmap carries the sprite's paper, so the ink lines read as gaps.
    const paper = rows.join('').split('.').length - 1;
    assert.equal(ink(bm).n, paper, m);
    seen.add(Array.from(bm.px).join(''));
  }
  assert.equal(seen.size, PIECES.length);
});

test('preview: stack drawn bottom-up, taller with more modules, empty when nothing is fitted', () => {
  assert.equal(ink(previewBitmap([])).n, 0);
  const one = ink(previewBitmap(['tracks']));
  const three = ink(previewBitmap(['tracks', 'cannon', 'electronics']));
  assert.ok(one.n > 0);
  assert.ok(three.top < one.top);
});

test('screen scale is an integer so the 8x8 font stays crisp', () => {
  assert.equal(screenScale(1280, 800), 3);
  assert.equal(screenScale(SCREEN_W - 1, SCREEN_H), 1);
  assert.equal(screenScale(1920, 1080), 5);
});
