import { test } from 'vitest';
import assert from 'node:assert/strict';
import { FIXTURES } from '../fixtures/index.ts';
import { runFixtureMessage } from '../fixtures/harness.ts';
import { Store } from '../state/store.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { PALETTE } from '../render/assets.ts';
import { radarBitmap, radarMarks, radarScroll, radarSize, commanderCell, RADAR_COLUMNS } from './radar.ts';

const map = loadMap(DEFAULT_MAP_ID);
const W = RADAR_COLUMNS;

function snapshotOf(id: string) {
  const store = new Store();
  for (const msg of FIXTURES.find((f) => f.id === id)!.messages) runFixtureMessage(store, msg, 0);
  return store.get().latest!;
}

const staticCells = (skip: Set<string> = new Set()) =>
  [...map.blockers, ...map.war_bases, ...map.factories].filter((s) => !skip.has(s.id)).flatMap((s) => s.components);

test('radar is a 128-column window, one pixel per cell and row', () => {
  assert.equal(W, 128);
  assert.deepEqual(radarSize(map), { width: 128, height: map.height });
});

test('scroll follows the commander in 64-column steps (Lafe6_radar_scroll)', () => {
  // Game start: scroll 0, commander at x=17 (tile 2) stays put.
  assert.equal(radarScroll(0, 17, map.width), 0);
  assert.equal(radarScroll(0, 111, map.width), 0); // tile 13
  assert.equal(radarScroll(0, 112, map.width), 64); // tile 14 -> right edge band
  // Hysteresis: back left scrolls only inside the 2-tile left band.
  assert.equal(radarScroll(64, 80, map.width), 64); // tile 10 - 8 = 2
  assert.equal(radarScroll(64, 79, map.width), 0); // tile 9 - 8 = 1
  // Clamped to [0, width - 128] (the Spectrum stops at tile 48 = column 384).
  assert.equal(radarScroll(0, 0, map.width), 0);
  assert.equal(radarScroll(384, 511, map.width), 384);
  // A jump (reconnect) settles in one call: 300 is tile 37, window 192..319.
  assert.equal(radarScroll(0, 300, map.width), 192);
  assert.equal(radarScroll(384, 17, map.width), 0);
});

test('every mark is white and inside the 128 x 16 strip, for every fixture and scroll', () => {
  for (const f of FIXTURES) {
    const store = new Store();
    for (const msg of f.messages) runFixtureMessage(store, msg, 0);
    for (const scroll of [0, 64, 192, 384]) {
      for (const m of radarMarks(map, store.get().latest, scroll, f.playerId)) {
        assert.equal(m.color, PALETTE.brightWhite, f.id);
        assert.ok(m.x >= 0 && m.x + m.w <= W && m.y >= 0 && m.y + m.h <= map.height && m.w >= 1 && m.h === 1, `${f.id}: ${JSON.stringify(m)}`);
      }
    }
  }
});

test('static structures and scenery inside the window are lit, nothing outside it', () => {
  for (const scroll of [0, 64, 384]) {
    const bits = radarBitmap(map, null, scroll, null, true);
    const expected = new Set(staticCells().filter((c) => c.x >= scroll && c.x < scroll + W).map((c) => (c.y * W + c.x - scroll)));
    assert.ok(expected.size > 0);
    for (let i = 0; i < bits.length; i++) assert.equal(bits[i], expected.has(i) ? 1 : 0, `scroll ${scroll} pixel ${i % W},${Math.floor(i / W)}`);
  }
});

test('robots XOR a 2x2 area at x..x+1, rows y-1..y, only when x - scroll is in [0, 126]', () => {
  const snap = snapshotOf('robots-orders');
  const empty = { ...snap, robots: [], commanders: [] };
  // Open ground check: pick a cell whose 2x2 area holds no static mark.
  const base = radarBitmap(map, empty, 0, null, true);
  let x = 40;
  const y = 8;
  while ([0, 1].some((dx) => [0, -1].some((dy) => base[(y + dy) * W + x + dx]))) x++;
  const r = { ...snap.robots[0], x, y };
  const bits = radarBitmap(map, { ...empty, robots: [r] }, 0, null, true);
  const lit = [...bits.keys()].filter((i) => bits[i] !== base[i]).map((i) => [i % W, Math.floor(i / W)]);
  assert.deepEqual(lit.sort(), [[x, y - 1], [x + 1, y - 1], [x, y], [x + 1, y]].sort());
  // Outside the window, or in the last column, no mark (Ld67d_get_radar_view_pointer).
  for (const rx of [127, 128, 300]) {
    assert.deepEqual(radarBitmap(map, { ...empty, robots: [{ ...r, x: rx }] }, 0, null, true), base, `x ${rx}`);
  }
  // Scrolled: same robot drawn relative to the window start.
  const scrolled = radarBitmap(map, { ...empty, robots: [{ ...r, x: x + 64 }] }, 64, null, true);
  assert.equal(scrolled[y * W + x], radarBitmap(map, empty, 64, null, true)[y * W + x] ^ 1);
});

test('the local commander blinks as a 2x2 mark; other commanders are not shown', () => {
  const snap = snapshotOf('world-static');
  const me = 'p1';
  const c = commanderCell(snap, me)!;
  assert.ok(c);
  const on = radarBitmap(map, snap, 0, me, true);
  const off = radarBitmap(map, snap, 0, me, false);
  const diff = [...on.keys()].filter((i) => on[i] !== off[i]).map((i) => [i % W, Math.floor(i / W)]);
  const area = [[c.x, c.y - 1], [c.x + 1, c.y - 1], [c.x, c.y], [c.x + 1, c.y]].filter(([, y]) => y >= 0);
  assert.deepEqual(diff.sort(), area.sort());
  // No viewer: no commander mark at all.
  assert.deepEqual(radarBitmap(map, snap, 0, null, true), off);
});

test('a docked commander marks its robot cell', () => {
  const snap = snapshotOf('commander-docked');
  const c = snap.commanders.find((k) => k.mode === 'docked')!;
  const robot = snap.robots.find((r) => r.entity_id === c.docked_robot_id)!;
  assert.deepEqual(commanderCell(snap, c.player_id), { x: robot.x, y: robot.y });
});

test('nuclear debris and destroyed structures are not marked (debris is below element 15)', () => {
  const snap = { ...snapshotOf('robots-orders'), robots: [], commanders: [] };
  const box = map.blockers.find((b) => b.id === 'blocker-9')!;
  const scroll = Math.max(0, Math.min(map.width - W, Math.floor(box.components[0].x / 64) * 64));
  const lit = (s: typeof snap, cells: { x: number; y: number }[]) => {
    const bits = radarBitmap(map, s, scroll, null, true);
    return cells.filter((c) => bits[c.y * W + c.x - scroll]).length;
  };
  assert.ok(lit(snap, box.components) > 0);
  assert.equal(lit({ ...snap, scenery_debris: ['blocker-9'] }, box.components), 0);
  const wb = map.war_bases[0];
  const wbScroll = Math.floor(wb.components[0].x / 64) * 64;
  const wbLit = (s: typeof snap) => {
    const bits = radarBitmap(map, s, wbScroll, null, true);
    return wb.components.filter((c) => c.x - wbScroll < W && bits[c.y * W + c.x - wbScroll]).length;
  };
  assert.ok(wbLit(snap) > 0);
  assert.equal(wbLit({ ...snap, structure_destruction: [wb.id] }), 0);
});

test('CR004.6: an AI seat with no commander renders no mark for it, and the map still renders', () => {
  // The AI seat's snapshot has one `commanders` entry (the human's) plus an
  // `ai_memories` entry for the computer seat -- never a second commander.
  const humanOnly = { ...snapshotOf('world-static'), ai_memories: [{ player_id: 'p2', construction: { last_war_base_id: null }, orders: { defences: [], sightings: [] } }] };
  const aiSeat = { ...humanOnly, commanders: humanOnly.commanders.filter((c) => c.player_id === 'p1') };
  assert.equal(aiSeat.commanders.length, 1);
  // The viewer's own (human) commander still marks normally.
  assert.ok(commanderCell(aiSeat, 'p1'));
  // Asking for the AI seat's commander is exactly the "not present" case
  // every other radar-viewer lookup already tolerates.
  assert.equal(commanderCell(aiSeat, 'p2'), null);
  assert.doesNotThrow(() => radarBitmap(map, aiSeat, 0, 'p1', true));
  assert.doesNotThrow(() => radarMarks(map, aiSeat, 0, 'p1', true));
});
