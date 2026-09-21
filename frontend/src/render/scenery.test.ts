import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { project } from './projection.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
import { sceneryPlacements, sliceSprite, spriteOrigin, type SceneryManifest } from './scenery.ts';

const manifestJson = readFileSync(new URL('../../public/assets/manifest.json', import.meta.url), 'utf8');
const shipped = (): SceneryManifest => JSON.parse(manifestJson).scenery as SceneryManifest;
const map = loadMap(DEFAULT_MAP_ID);

test('every decoded blocker resolves to its Spectrum element sprite through the shipped manifest', () => {
  const { placements, unmapped } = sceneryPlacements(map, shipped());
  assert.equal(unmapped.length, 0);
  assert.equal(placements.length, map.blockers.length);
  const expected: Record<string, string> = { box_low: 'spectrum.element_17', box_high: 'spectrum.element_18', fence: 'spectrum.element_21' };
  const kinds = new Map(map.blockers.map((b) => [b.id, b.kind!]));
  for (const p of placements) assert.equal(p.asset.sprite, expected[kinds.get(p.blockerId)!]);
});

test('asset heights agree with the map heights the surface/shadow model uses', () => {
  const m = shipped();
  for (const b of map.blockers) {
    const asset = m.assets[m.kinds[b.kind!]!]!;
    for (const c of b.components) assert.equal(asset.height, c.height, `${b.id} (${b.kind})`);
  }
});

test('the kind -> asset mapping is data: editing the manifest changes the sprite with no code change', () => {
  const m = shipped();
  m.kinds['box_low'] = 'scenery.box_high';
  const lows = new Set(map.blockers.filter((b) => b.kind === 'box_low').map((b) => b.id));
  const { placements } = sceneryPlacements(map, m);
  for (const p of placements.filter((q) => lows.has(q.blockerId))) assert.equal(p.asset.sprite, 'spectrum.element_18');

  const n = shipped();
  n.assets['scenery.box_low']!.sprite = 'spectrum.element_6';
  const again = sceneryPlacements(map, n).placements.filter((q) => lows.has(q.blockerId));
  assert.ok(again.length > 0);
  for (const p of again) assert.equal(p.asset.sprite, 'spectrum.element_6');
});

test('unmapped kinds, unknown sprites and footprint mismatches fall back to placeholders', () => {
  const m = shipped();
  delete m.kinds['fence'];
  m.assets['scenery.box_high']!.sprite = 'nope';
  m.assets['scenery.box_low']!.footprint = [1, 1];
  const { placements, unmapped } = sceneryPlacements(map, m);
  assert.equal(placements.length, 0);
  assert.equal(unmapped.length, map.blockers.length);
  assert.equal(sceneryPlacements(map, null).unmapped.length, map.blockers.length);
});

test('anchor is the Spectrum stamp corner: min x, max y', () => {
  const { placements } = sceneryPlacements(map, shipped());
  const p = placements.find((q) => q.blockerId === 'blocker-1')!;
  assert.deepEqual(p.anchor, { x: 12, y: 1 });
});

test('box sprite lines up with the projected 2x2 box of its height', () => {
  const asset = shipped().assets['scenery.box_low']!;
  const rows = SCENERY_SPRITES[asset.sprite]!;
  const o = spriteOrigin(asset, { x: 0, y: 0 });
  // Top face's left corner: column 0, first ink row of column 0.
  const r = rows.findIndex((row) => row[0] === '#');
  const corner = project(-0.5, -1.5, asset.height);
  assert.ok(Math.abs(o.x + 0.5 - corner.x) <= 1);
  assert.ok(Math.abs(o.y + r + 0.5 - corner.y) <= 1);
  // Lowest row sits on the front ground corner.
  assert.equal(o.y + rows.length - 0.5, project(-0.5, 0.5).y);
});

test('slices partition the sprite and give each face to the right cell', () => {
  for (const id of ['scenery.box_low', 'scenery.box_high', 'scenery.fence', 'scenery.debris_a']) {
    const asset = shipped().assets[id]!;
    const rows = SCENERY_SPRITES[asset.sprite]!;
    const slices = sliceSprite(asset);
    assert.equal(slices.length, 4);
    rows.forEach((row, r) => {
      for (let c = 0; c < row.length; c++) {
        const owners = slices.filter((s) => s.rows[r]![c] !== ' ');
        assert.equal(owners.length, row[c] === ' ' ? 0 : 1, `${id} pixel ${c},${r}`);
        if (owners.length) assert.equal(owners[0]!.rows[r]![c], row[c]);
      }
    });
  }
  const box = sliceSprite(shipped().assets['scenery.box_low']!);
  const at = (c: number, r: number) => box.find((s) => s.rows[r]![c] !== ' ')!;
  // Top vertex (col 16, row 0) is the far cell's roof; the bottom-left of the
  // front face (col 8, row 31) is the anchor (nearest) cell.
  assert.deepEqual([at(16, 0).dx, at(16, 0).dy], [1, -1]);
  assert.deepEqual([at(8, 31).dx, at(8, 31).dy], [0, 0]);
});
