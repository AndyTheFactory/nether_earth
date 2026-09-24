// Spectrum terrain sprites (owner request, 2026-09-24). Rough, mountain and
// ditch are 2x2 map elements with their own graphics, chosen by the raw
// element index the map now carries alongside each cell's class and height.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
import type { TerrainManifest } from './assets.ts';

const manifestJson = readFileSync(new URL('../../public/assets/manifest.json', import.meta.url), 'utf8');
const shipped = (): TerrainManifest => JSON.parse(manifestJson).terrain as TerrainManifest;
const map = loadMap(DEFAULT_MAP_ID);

test('every terrain element the map uses has a decoded sprite', () => {
  const m = shipped();
  const used = new Set(map.terrain.elements.map((e) => e.type));
  assert.ok(used.size > 0, 'map carries no terrain elements');
  for (const type of used) {
    const id = m.elements[String(type)];
    assert.ok(id, `no terrain asset mapped for element type ${type}`);
    const asset = m.assets[id!]!;
    assert.ok(SCENERY_SPRITES[asset.sprite], `sprite ${asset.sprite} missing from SCENERY_SPRITES`);
    // Every map element is a 2x2 stamp, terrain included.
    assert.deepEqual(asset.footprint, [2, 2]);
  }
});

test('the element types present are exactly the rough, mountain and ditch classes', () => {
  // decode_zx_terrain.py's TERRAIN_CLASS: rough 2-7, mountain 8-11, ditch 12-14.
  const used = [...new Set(map.terrain.elements.map((e) => e.type))].sort((a, b) => a - b);
  assert.ok(used.every((t) => t >= 2 && t <= 14), `unexpected element types: ${used}`);
  assert.ok(used.some((t) => t >= 2 && t <= 7), 'no rough elements');
  assert.ok(used.some((t) => t >= 8 && t <= 11), 'no mountain elements');
  assert.ok(used.some((t) => t >= 12 && t <= 14), 'no ditch elements');
});

test('elements cover exactly the non-normal terrain cells, four per stamp', () => {
  // A 2x2 stamp per element, and the map has no buried ones, so the cells the
  // engine reads and the sprites the renderer draws describe the same ground.
  const cells = new Set(map.terrain.cells.map((c) => `${c.x},${c.y}`));
  const covered = new Set<string>();
  for (const e of map.terrain.elements) {
    for (const dy of [0, -1]) for (const dx of [0, 1]) covered.add(`${e.x + dx},${e.y + dy}`);
  }
  assert.equal(covered.size, cells.size);
  assert.equal(map.terrain.elements.length * 4, cells.size);
  for (const key of cells) assert.ok(covered.has(key), `cell ${key} has no element`);
});

test('each element sits on cells of its own terrain class', () => {
  const classOf = new Map(map.terrain.cells.map((c) => [`${c.x},${c.y}`, c.type]));
  const expected = (type: number) => (type <= 7 ? 'rough' : type <= 11 ? 'mountain' : 'ditch');
  for (const e of map.terrain.elements) {
    for (const dy of [0, -1]) for (const dx of [0, 1]) {
      assert.equal(classOf.get(`${e.x + dx},${e.y + dy}`), expected(e.type));
    }
  }
});
