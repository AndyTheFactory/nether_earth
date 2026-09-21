import { test } from 'vitest';
import assert from 'node:assert/strict';
import { displayTick, interpolateGrid, interpolateAltitude, interpolateProjectile } from './interpolation.ts';

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

test('projectile slides 2 cells per 4-tick cadence and meets the next snapshot without a jump', () => {
  const p = { x: 10, y: 5, dx: 1, dy: 0, travelled_cells: 2, max_range_cells: 10, created_tick: 0 };
  // Snapshot at cadence tick 4: display runs 10 -> 12 over ticks 4..8.
  assert.deepEqual(interpolateProjectile(p, 4, 4), { x: 10, y: 5 });
  assert.deepEqual(interpolateProjectile(p, 4, 5), { x: 10.5, y: 5 });
  assert.deepEqual(interpolateProjectile(p, 7, 8), { x: 12, y: 5 });
  // Tick-8 snapshot has the projectile at 12: continuous with the above.
  assert.deepEqual(interpolateProjectile({ ...p, x: 12, travelled_cells: 4 }, 8, 8), { x: 12, y: 5 });
  // Vertical travel.
  assert.deepEqual(interpolateProjectile({ ...p, dx: 0, dy: -1 }, 4, 6), { x: 10, y: 4 });
});

test('projectile fired between cadence ticks reaches its first cells on the next cadence tick', () => {
  const p = { x: 3, y: 3, dx: -1, dy: 0, travelled_cells: 0, max_range_cells: 10, created_tick: 5 };
  assert.deepEqual(interpolateProjectile(p, 5, 5), { x: 3, y: 3 });
  assert.deepEqual(interpolateProjectile(p, 7, 8), { x: 1, y: 3 });
});

test('projectile never slides past its range', () => {
  const odd = { x: 0, y: 0, dx: 1, dy: 0, travelled_cells: 9, max_range_cells: 10, created_tick: 0 };
  assert.deepEqual(interpolateProjectile(odd, 7, 8), { x: 1, y: 0 });
  const spent = { ...odd, travelled_cells: 10 };
  assert.deepEqual(interpolateProjectile(spent, 7, 8), { x: 0, y: 0 });
});
