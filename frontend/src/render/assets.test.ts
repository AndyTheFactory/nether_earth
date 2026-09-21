import { test } from 'vitest';
import assert from 'node:assert/strict';
import { PLACEHOLDER_COLORS, type SemanticAsset } from './assets.ts';
import type { TerrainType } from '../world/map.ts';

// The renderer draws each cell as `terrain.${type}`; every terrain class
// (functional spec §7.2) must resolve to a distinct placeholder.
const TERRAIN: readonly TerrainType[] = ['normal', 'rough', 'mountain', 'ditch'];

test('all four terrain classes have distinct placeholder colours', () => {
  const colours = TERRAIN.map((t) => PLACEHOLDER_COLORS[`terrain.${t}` as SemanticAsset]);
  for (const c of colours) assert.equal(typeof c, 'number');
  assert.equal(new Set(colours).size, TERRAIN.length);
});
