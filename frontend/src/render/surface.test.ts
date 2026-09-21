import { test } from 'vitest';
import assert from 'node:assert/strict';
import { footprintRange, RUBBLE_HEIGHT, SurfaceMap } from './surface.ts';
import { footprintCells, loadMap, DEFAULT_MAP_ID } from '../world/map.ts';

const map = loadMap(DEFAULT_MAP_ID);
const surface = new SurfaceMap(map);
const pad = footprintCells(map.interaction_points.find((ip) => ip.id === 'warbase-1-helipad')!);

test('footprint ranges: point, 1-wide and 2-wide, whole and mid-move', () => {
  assert.deepEqual(footprintRange(20, 0), [20, 20]);
  assert.deepEqual(footprintRange(20.3, 0), [20, 20]);
  assert.deepEqual(footprintRange(20, 1), [20, 20]);
  assert.deepEqual(footprintRange(20.3, 1), [20, 21]);
  assert.deepEqual(footprintRange(20.5, 2), [20, 21]);
});

test('open ground is 0; structure tops come from map data', () => {
  assert.equal(surface.under(22, 12), 0);
  assert.equal(surface.under(18, 3), 15); // war base tall block
  assert.equal(surface.under(20, 6), 7); // war base low block
  assert.equal(surface.under(37, 5), 7); // factory low block
});

test('heli-pad roof: the shadow over the 2×2 pad sits on the war-base roof', () => {
  assert.equal(pad.length, 4);
  for (const cell of pad) assert.equal(surface.under(cell.x, cell.y), 15);
  // A commander anchored on the pad (its min-x/max-y cell) covers the whole pad.
  const anchor = { x: Math.min(...pad.map((c) => c.x)), y: Math.max(...pad.map((c) => c.y)) };
  assert.equal(surface.underUnit(anchor.x, anchor.y), 15);
});

test('2×2 unit surface reads columns x..x+1 and rows y-1..y', () => {
  // (19,6) is open ground, (20,6) a 7-high war-base block
  assert.equal(surface.underUnit(18, 7), 0);
  assert.equal(surface.underUnit(19, 7), 7);
  // (20,9) is open ground but the body's row above, (20,8), is a 7-high block
  assert.equal(surface.heightAt(20, 9), 0);
  assert.equal(surface.underUnit(20, 9), 7);
});

test('mid-move footprint takes the highest surface it overlaps', () => {
  // (19,6) is open ground, (20,6) a 7-high block
  assert.equal(surface.under(19, 6), 0);
  assert.equal(surface.under(19.5, 6), 7);
  // a point sample (projectile) only reads its own cell
  assert.equal(surface.under(19.4, 6, new Set(), 0), 0);
});

test('destroyed structures leave rubble height', () => {
  assert.equal(surface.under(37, 5, new Set(['factory-1'])), RUBBLE_HEIGHT);
});

test('terrain pieces have their Spectrum heights (CR002.21)', () => {
  assert.equal(surface.heightAt(32, 12), 2); // rough, element types 2-5
  assert.equal(surface.heightAt(54, 14), 3); // rough, element types 6/7
  assert.equal(surface.heightAt(167, 9), 6); // mountain
  // A 2×2 shadow next to the mountain reaches it once the body overlaps it.
  assert.equal(surface.underUnit(165, 9), 0);
  assert.equal(surface.underUnit(166, 9), 6);
});

test('nuclear debris is as high as a rough piece of type 6/7 (CR002.18/21)', () => {
  const box = map.blockers.find((b) => b.destructible)!;
  const { x, y } = box.components[0];
  assert.equal(map.terrain.debris_height, 3);
  assert.ok(surface.heightAt(x, y) > 3);
  assert.equal(surface.heightAt(x, y, new Set([box.id])), 3);
});
