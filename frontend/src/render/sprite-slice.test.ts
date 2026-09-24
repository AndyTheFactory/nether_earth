// Generic per-footprint-cell slicing (CR002.5, shared by scenery/robots/
// commander/structure walls since #owner-2026-09-22). scenery.test.ts covers
// the same algorithm through scenery.ts's SceneryAsset-shaped wrapper; these
// tests exercise the underlying functions directly against the robot and
// commander sprite tables, and the structure-wall element sprites.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { sliceSpriteRows, spriteOriginFor } from './sprite-slice.ts';
import { ROBOT_SPRITES } from './robot-sprites.ts';
import { COMMANDER_SPRITES } from './commander-sprites.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';

test('every decoded sprite is rectangular (every row the same width)', () => {
  // One sprite per (piece, facing) since the 4-direction decode.
  for (const [id, byFacing] of Object.entries(ROBOT_SPRITES)) {
    for (const [facing, rows] of Object.entries(byFacing)) {
      const w = rows[0]!.length;
      for (const row of rows) assert.equal(row.length, w, `ROBOT_SPRITES.${id}.${facing}`);
    }
  }
  for (const [id, rows] of Object.entries(COMMANDER_SPRITES)) {
    const w = rows[0]!.length;
    for (const row of rows) assert.equal(row.length, w, `COMMANDER_SPRITES.${id}`);
  }
});

test('war-base/factory wall segments (element 15/16) decoded alongside scenery', () => {
  assert.ok(SCENERY_SPRITES['spectrum.wall_low']!.length > 0);
  assert.ok(SCENERY_SPRITES['spectrum.wall_high']!.length > 0);
});

test('slices of a 2x2-footprint robot piece partition the sprite exactly once per pixel', () => {
  const rows = ROBOT_SPRITES.bipod.south;
  const slices = sliceSpriteRows(rows, [2, 2], 11);
  assert.equal(slices.length, 4);
  rows.forEach((row, r) => {
    for (let c = 0; c < row.length; c++) {
      const owners = slices.filter((s) => s.rows[r]![c] !== ' ');
      assert.equal(owners.length, row[c] === ' ' ? 0 : 1, `pixel ${c},${r}`);
      if (owners.length) assert.equal(owners[0]!.rows[r]![c], row[c]);
    }
  });
});

test('a 1x1 footprint (structure wall) slices to exactly one, unfiltered copy of the sprite', () => {
  const rows = SCENERY_SPRITES['spectrum.wall_low']!;
  const slices = sliceSpriteRows(rows, [1, 1], 7);
  assert.equal(slices.length, 1);
  assert.deepEqual(slices[0]!.rows, rows);
  assert.deepEqual([slices[0]!.dx, slices[0]!.dy], [0, 0]);
});

test('spriteOriginFor lifts the sprite by elevation without changing its footprint placement', () => {
  const rows = COMMANDER_SPRITES['spectrum.commander']!;
  const ground = spriteOriginFor(rows, [2, 2], { x: 10, y: 5 }, 0);
  const raised = spriteOriginFor(rows, [2, 2], { x: 10, y: 5 }, 8);
  assert.equal(ground.x, raised.x);
  assert.equal(ground.y - raised.y, 8);
});

test('spriteOriginFor applies a presentation-only offset on top of elevation', () => {
  const rows = ROBOT_SPRITES.cannon.south;
  const plain = spriteOriginFor(rows, [2, 2], { x: 3, y: 4 }, 2);
  const shifted = spriteOriginFor(rows, [2, 2], { x: 3, y: 4 }, 2, [1, -1]);
  assert.deepEqual([shifted.x - plain.x, shifted.y - plain.y], [1, -1]);
});
