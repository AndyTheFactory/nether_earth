import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { project } from './projection.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
import { sceneryPlacements, sliceSprite, spriteOrigin, type SceneryAsset, type SceneryManifest } from './scenery.ts';

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

test('nuclear debris resolves through the manifest debris kind (CR002.18)', () => {
  const debris = new Set(['blocker-9']);
  const { placements, unmapped } = sceneryPlacements(map, shipped(), debris);
  assert.equal(unmapped.length, 0);
  const p = placements.find((q) => q.blockerId === 'blocker-9')!;
  assert.equal(p.assetId, 'scenery.debris_a');
  assert.equal(p.asset.sprite, 'spectrum.element_6');
  assert.deepEqual(p.anchor, { x: 16, y: 14 });
  // Data-driven: remapping the kind switches the sprite; no mapping falls back to a prism.
  const m = shipped();
  m.kinds['debris'] = 'scenery.debris_b';
  assert.equal(sceneryPlacements(map, m, debris).placements.find((q) => q.blockerId === 'blocker-9')!.asset.sprite, 'spectrum.element_7');
  delete m.kinds['debris'];
  assert.deepEqual(sceneryPlacements(map, m, debris).unmapped.map((b) => b.id), ['blocker-9']);
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

/** Screen centre of a sprite's post base: midpoint of its left and right ground corners (lowest ink pixel of the outermost columns). */
function baseCentre(asset: SceneryAsset, anchor: { x: number; y: number }): { x: number; y: number } {
  const rows = SCENERY_SPRITES[asset.sprite]!;
  const cols = rows.flatMap((row) => [...row].map((ch, c) => (ch === ' ' ? -1 : c))).filter((c) => c >= 0);
  const lowest = (c: number) => rows.reduce((acc, row, r) => (row[c] !== ' ' ? r : acc), -1);
  const [l, r] = [Math.min(...cols), Math.max(...cols)];
  const o = spriteOrigin(asset, anchor);
  return { x: o.x + (l + r) / 2 + 0.5, y: o.y + (lowest(l) + lowest(r)) / 2 + 0.5 };
}

test('an asset offset shifts the drawn sprite and its slices, nothing else (CR003.7)', () => {
  const fence = shipped().assets['scenery.fence']!;
  const plain = { ...fence, offset: undefined };
  const a = spriteOrigin(fence, { x: 12, y: 1 });
  const b = spriteOrigin(plain, { x: 12, y: 1 });
  assert.deepEqual([a.x - b.x, a.y - b.y], fence.offset);
  // Slices still partition the sprite (checked above); the offset only moves where it lands.
  assert.equal(sliceSprite(fence).length, 4);
});

test('fence post is centred on its 2x2 footprint, so both map-end walls look alike (CR003.7)', () => {
  const m = shipped();
  const fence = m.assets['scenery.fence']!;
  for (const b of map.blockers.filter((q) => q.kind === 'fence')) {
    const anchor = { x: Math.min(...b.components.map((c) => c.x)), y: Math.max(...b.components.map((c) => c.y)) };
    const c = baseCentre(fence, anchor);
    const centre = project(anchor.x + 0.5, anchor.y - 0.5);
    assert.ok(Math.abs(c.x - centre.x) <= 1 && Math.abs(c.y - centre.y) <= 1, `${b.id}: base ${c.x},${c.y} vs ${centre.x},${centre.y}`);
  }
  // Without the offset the post stands on the footprint's -x column (the left-wall gap).
  const off = baseCentre({ ...fence, offset: undefined }, { x: 12, y: 1 });
  const centre = project(12.5, 0.5);
  assert.ok(Math.abs(off.y - centre.y) >= 3);
});
