import { test } from 'vitest';
import assert from 'node:assert/strict';
import { footprintRange, RUBBLE_HEIGHT, SurfaceMap } from './surface.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';

const map = loadMap(DEFAULT_MAP_ID);
const surface = new SurfaceMap(map);
const pad = map.interaction_points.find((ip) => ip.id === 'warbase-1-helipad')!.footprint;

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

test('heli-pad roof: the shadow over the pad sits on the war-base roof', () => {
  assert.equal(surface.under(pad.x, pad.y), 15);
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
