import { test } from 'vitest';
import assert from 'node:assert/strict';
import { displayTick, interpolateGrid, interpolateAltitude } from './interpolation.ts';

test('displayTick never exceeds latest tick + 1 and freezes when paused', () => {
  assert.equal(displayTick(10, 0, 25, false), 10.5);
  assert.equal(displayTick(10, 0, 500, false), 11);
  assert.equal(displayTick(10, 0, 500, true), 10);
});

test('grid interpolation follows from→to over duration and clamps', () => {
  const t = { from_x: 2, from_y: 3, to_x: 3, to_y: 3, started_tick: 10, duration_ticks: 4 };
  assert.deepEqual(interpolateGrid(3, 3, t, 10), { x: 2, y: 3 });
  assert.deepEqual(interpolateGrid(3, 3, t, 12), { x: 2.5, y: 3 });
  assert.deepEqual(interpolateGrid(3, 3, t, 20), { x: 3, y: 3 });
  assert.deepEqual(interpolateGrid(3, 3, null, 12), { x: 3, y: 3 });
});

test('altitude interpolation', () => {
  const t = { from_altitude: 4, to_altitude: 6, started_tick: 0, duration_ticks: 4 };
  assert.equal(interpolateAltitude(6, t, 2), 5);
  assert.equal(interpolateAltitude(6, null, 2), 6);
});
