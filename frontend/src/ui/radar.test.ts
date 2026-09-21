import { test } from 'vitest';
import assert from 'node:assert/strict';
import { FIXTURES } from '../fixtures/index.ts';
import { runFixtureMessage } from '../fixtures/harness.ts';
import { Store } from '../state/store.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { ownerColor, PALETTE } from '../render/assets.ts';
import { radarMarks, radarSize, viewFromCorners, RADAR_CELLS_PER_PX, RADAR_PX_PER_ROW } from './radar.ts';

const map = loadMap(DEFAULT_MAP_ID);

function snapshotOf(id: string) {
  const store = new Store();
  for (const msg of FIXTURES.find((f) => f.id === id)!.messages) runFixtureMessage(store, msg, 0);
  return store.get().latest!;
}

const at = (x: number, y: number) => ({ x: Math.floor(x / RADAR_CELLS_PER_PX), y: y * RADAR_PX_PER_ROW });

test('radar covers the whole map', () => {
  assert.deepEqual(radarSize(map), { width: map.width / RADAR_CELLS_PER_PX, height: map.height * RADAR_PX_PER_ROW });
});

test('every mark stays inside the radar', () => {
  const { width, height } = radarSize(map);
  for (const f of FIXTURES) {
    const store = new Store();
    for (const msg of f.messages) runFixtureMessage(store, msg, 0);
    for (const m of radarMarks(map, store.get().latest, { minX: -40, maxX: map.width + 40 })) {
      assert.ok(m.x >= -1 && m.x + m.w <= width + 1 && m.y >= -1 && m.y + m.h <= height + 1, `${f.id}: ${JSON.stringify(m)}`);
    }
  }
});

test('structures are coloured by their snapshot owner, neutral ones white', () => {
  const snap = snapshotOf('robots-orders');
  const marks = radarMarks(map, snap, null);
  const colorAt = (x: number, y: number) => {
    const p = at(x, y);
    return marks.filter((m) => m.x <= p.x && p.x < m.x + m.w && m.y <= p.y && p.y < m.y + m.h).at(-1)?.color;
  };
  for (const o of snap.structure_ownership) {
    const s = [...map.war_bases, ...map.factories].find((x) => x.id === o.structure_id)!;
    const c = s.components[0];
    assert.equal(colorAt(c.x, c.y), ownerColor(o.owner), o.structure_id);
  }
  const neutral = map.factories.find((f) => !snap.structure_ownership.some((o) => o.structure_id === f.id))!;
  assert.equal(colorAt(neutral.components[0].x, neutral.components[0].y), PALETTE.brightWhite);
});

test('robots and commanders are marked at their snapshot cells in owner colour', () => {
  const snap = snapshotOf('robots-orders');
  const marks = radarMarks(map, snap, null);
  for (const r of snap.robots) {
    const p = at(r.x, r.y);
    assert.ok(marks.some((m) => m.x === p.x && m.y === p.y && m.color === ownerColor(r.owner)), r.entity_id);
  }
  for (const c of snap.commanders) {
    const p = at(c.x, c.y);
    assert.ok(marks.some((m) => m.x === p.x - 1 && m.w === 3 && m.y === p.y && m.color === ownerColor(c.player_id)), c.player_id);
  }
});

test('the view window is drawn as two full-height edges', () => {
  const { height } = radarSize(map);
  const marks = radarMarks(map, null, { minX: 10, maxX: 51 });
  const edges = marks.slice(-2);
  assert.deepEqual(edges.map((m) => [m.x, m.y, m.w, m.h]), [
    [Math.floor(10 / RADAR_CELLS_PER_PX), 0, 1, height],
    [Math.floor(51 / RADAR_CELLS_PER_PX), 0, 1, height],
  ]);
});

test('view range comes from the corner cells of the play view', () => {
  assert.deepEqual(viewFromCorners([{ x: 20, y: -3 }, { x: 44, y: 9 }, { x: 12, y: 20 }, { x: 36, y: 31 }]), { minX: 12, maxX: 44 });
  assert.equal(viewFromCorners([]), null);
});
