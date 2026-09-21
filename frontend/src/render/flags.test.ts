import { test } from 'vitest';
import assert from 'node:assert/strict';
import { FLAG_POLE_COLUMN, FLAG_SPRITES, ownershipFlags } from './flags.ts';
import { loadMap, DEFAULT_MAP_ID, type MapData } from '../world/map.ts';
import { FIXTURES } from '../fixtures/index.ts';

const map = loadMap(DEFAULT_MAP_ID);
const roofCells = (m: MapData, id: string) =>
  new Set([...m.war_bases, ...m.factories].find((s) => s.id === id)!.components.filter((c) => c.height === 15).map((c) => `${c.x},${c.y}`));

test('neutral structures carry no flag', () => {
  assert.deepEqual(ownershipFlags(map, []), []);
  assert.deepEqual(ownershipFlags(map, [{ structure_id: 'factory-1', owner: null }]), []);
});

test('p1 flies its flag on the -x side, p2 on the +x side (factory: 2 cells, war base: 4 cells)', () => {
  // factory-1 anchor (39, 6); warbase-1 anchor (22, 9).
  assert.deepEqual(ownershipFlags(map, [{ structure_id: 'factory-1', owner: 'p1' }]), [{ structureId: 'factory-1', owner: 'p1', x: 37, y: 4 }]);
  assert.deepEqual(ownershipFlags(map, [{ structure_id: 'factory-1', owner: 'p2' }]), [{ structureId: 'factory-1', owner: 'p2', x: 41, y: 4 }]);
  assert.deepEqual(ownershipFlags(map, [{ structure_id: 'warbase-1', owner: 'p1' }]), [{ structureId: 'warbase-1', owner: 'p1', x: 18, y: 5 }]);
  assert.deepEqual(ownershipFlags(map, [{ structure_id: 'warbase-1', owner: 'p2' }]), [{ structureId: 'warbase-1', owner: 'p2', x: 26, y: 5 }]);
});

test('every flag cell is a roof cell of its own structure, for both owners', () => {
  for (const owner of ['p1', 'p2']) {
    const all = [...map.war_bases, ...map.factories].map((s) => ({ structure_id: s.id, owner }));
    const flags = ownershipFlags(map, all);
    assert.equal(flags.length, all.length);
    for (const f of flags) assert.ok(roofCells(map, f.structureId).has(`${f.x},${f.y}`), `${f.structureId} ${owner}`);
  }
});

test('destroyed structures lose their flag', () => {
  assert.deepEqual(ownershipFlags(map, [{ structure_id: 'warbase-4', owner: 'p2' }], new Set(['warbase-4'])), []);
});

test('capture moves the flag in the very next snapshot (ownership-flags fixture)', () => {
  const f = FIXTURES.find((x) => x.id === 'ownership-flags')!;
  const factory1 = f.messages
    .filter((m) => m.type === 'snapshot')
    .map((m) => ownershipFlags(map, m.state.structure_ownership).find((fl) => fl.structureId === 'factory-1') ?? null);
  assert.deepEqual(factory1, [null, { structureId: 'factory-1', owner: 'p1', x: 37, y: 4 }, { structureId: 'factory-1', owner: 'p2', x: 41, y: 4 }]);
});

test('flag sprites share one size and keep the pole in the pole column', () => {
  for (const rows of Object.values(FLAG_SPRITES)) {
    assert.equal(rows.length, 11);
    for (const row of rows) assert.equal(row.length, 8);
    assert.equal(rows.at(-1)![FLAG_POLE_COLUMN], '#');
  }
  assert.notDeepEqual(FLAG_SPRITES.p1, FLAG_SPRITES.p2);
});
