// War-base/factory wall segments (owner-directed extension, 2026-09-22): see
// `manifest.json`'s `structures` section and renderer.ts's `structureWallAsset`.
// Mirrors scenery.test.ts's "asset heights agree with the map heights" check.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
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
    assert.deepEqual(asset.footprint, [1, 1]);
  }
});

test('the two wall assets point at distinct decoded sprites', () => {
  const m = shipped();
  const sprites = new Set(Object.values(m.assets).map((a) => a.sprite));
  assert.equal(sprites.size, Object.keys(m.assets).length);
});
