// War-base/factory wall segments (owner-directed extension, 2026-09-22): see
// `manifest.json`'s `structures` section and renderer.ts's `structureWallAsset`.
// Mirrors scenery.test.ts's "asset heights agree with the map heights" check.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
import { wallBlocks } from './scenery.ts';
import type { StructureManifest } from './assets.ts';

const manifestJson = readFileSync(new URL('../../public/assets/manifest.json', import.meta.url), 'utf8');
const shipped = (): StructureManifest => JSON.parse(manifestJson).structures as StructureManifest;
const map = loadMap(DEFAULT_MAP_ID);

test('every war-base and factory component height the shipped map uses has a wall asset', () => {
  const m = shipped();
  const heights = new Set([...map.war_bases, ...map.factories].flatMap((s) => s.components.map((c) => c.height)));
  assert.deepEqual([...heights].sort((a, b) => a - b), [7, 15]);
  for (const h of heights) {
    const id = m.walls[String(h)];
    assert.ok(id, `no wall asset mapped for height ${h}`);
    const asset = m.assets[id!]!;
    assert.equal(asset.height, h);
    assert.ok(SCENERY_SPRITES[asset.sprite], `sprite ${asset.sprite} missing from SCENERY_SPRITES`);
    // 2x2, not 1x1: the decoded art is a 2x2 map element (32 px against a
    // 12 px cell), the same size as a scenery box. Declaring it 1x1 made every
    // wall overlap its neighbours and sit a cell low, because
    // `spriteOriginFor` offsets by the footprint's depth.
    assert.deepEqual(asset.footprint, [2, 2]);
  }
});

test('the two wall assets point at distinct decoded sprites', () => {
  const m = shipped();
  const sprites = new Set(Object.values(m.assets).map((a) => a.sprite));
  assert.equal(sprites.size, Object.keys(m.assets).length);
});

test('every factory decomposes into whole 2x2 wall blocks', () => {
  // The map stores a structure as 1x1 cells, but its sprites are 2x2 elements.
  // A factory is wholly made of them -- a doorway is a missing block, not a
  // partial one -- so none of its cells fall back to a placeholder prism.
  for (const factory of map.factories) {
    const { blocks, loose } = wallBlocks(factory.components);
    assert.deepEqual(loose, [], `${factory.id} has cells outside a 2x2 block`);
    assert.equal(blocks.length * 4, factory.components.length);
    for (const block of blocks) assert.ok(block.height === 7 || block.height === 15);
  }
});

// `Lbfb2_warbase` walks its template with mixed +/-1 y offsets, so each
// two-cell-wide column's stamps start wherever that column's wall starts.
// These are the 15 elements it stamps, as (x, y, type) offsets from the walk's
// first element, with the anchor being the min-x / max-y cell of the 2x2 and
// type 16 the tall piece (`Ld7bc_map_piece_heights`: 15 -> 7, 16 -> 15).
const WARBASE_TEMPLATE: readonly [number, number, number][] = [
  [0, 0, 16], [0, 2, 16],
  [2, -1, 16], [2, 1, 15], [2, 3, 15], [2, 5, 15],
  [4, 0, 16], [4, 2, 16], [4, 4, 15],
  [6, -1, 16], [6, 1, 15], [6, 3, 15], [6, 5, 15],
  [8, 0, 16], [8, 2, 16],
];

test('a war base decomposes into exactly the 15 blocks its template stamps', () => {
  // Pairing rows on a fixed grid from the structure's min-y split any column
  // whose wall starts one row lower into a 2x1, a 2x2 and a 2x1 -- visible in
  // game as two slivers beside the front corners of every war base.
  const heightOf = (type: number) => (type === 16 ? 15 : 7);
  for (const base of map.war_bases) {
    const { blocks, loose } = wallBlocks(base.components);
    assert.deepEqual(loose, [], `${base.id} has cells outside a 2x2 block`);
    assert.equal(blocks.length, 15);
    assert.equal(blocks.length * 4, base.components.length);
    // Anchor the template on the block nearest the map origin and compare.
    const x0 = Math.min(...blocks.map((b) => b.anchor.x));
    const y0 = Math.min(...blocks.filter((b) => b.anchor.x === x0).map((b) => b.anchor.y));
    const got = blocks.map((b) => [b.anchor.x - x0, b.anchor.y - y0, b.height] as const);
    const want = WARBASE_TEMPLATE.map(([dx, dy, type]) => [dx, dy, heightOf(type)] as const);
    assert.deepEqual(
      [...got].sort((a, b) => a[0] - b[0] || a[1] - b[1]),
      [...want].sort((a, b) => a[0] - b[0] || a[1] - b[1]),
      base.id,
    );
  }
});

test('a 2x2 block anchors at its min-x / max-y cell, like a scenery placement', () => {
  const { blocks, loose } = wallBlocks([
    { x: 10, y: 4, height: 15 },
    { x: 11, y: 4, height: 15 },
    { x: 10, y: 5, height: 15 },
    { x: 11, y: 5, height: 15 },
  ]);
  assert.deepEqual(loose, []);
  assert.equal(blocks.length, 1);
  assert.deepEqual(blocks[0]!.anchor, { x: 10, y: 5 });
  assert.equal(blocks[0]!.height, 15);
});

test('cells of differing heights never merge into one block', () => {
  const { blocks, loose } = wallBlocks([
    { x: 0, y: 0, height: 15 },
    { x: 1, y: 0, height: 15 },
    { x: 0, y: 1, height: 7 },
    { x: 1, y: 1, height: 15 },
  ]);
  assert.deepEqual(blocks, []);
  assert.equal(loose.length, 4);
});
