import { test } from 'vitest';
import assert from 'node:assert/strict';
import { loadLabels, saveLabels, textOverlays, LABELS_STORAGE_KEY } from './labels.ts';
import { Store } from './store.ts';
import { GameController } from '../app/controller.ts';

function memory(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), data };
}

const throwing = {
  getItem: () => {
    throw new Error('blocked');
  },
  setItem: () => {
    throw new Error('blocked');
  },
};

test('labels default off: fresh store, no query, empty or unavailable storage', () => {
  assert.equal(new Store().get().ui.labels, false);
  assert.equal(loadLabels('', memory()), false);
  assert.equal(loadLabels('', null), false);
  assert.equal(loadLabels('', throwing), false);
  assert.equal(loadLabels(), false);
});

test('stored choice is used; query parameter overrides it', () => {
  assert.equal(loadLabels('', memory({ [LABELS_STORAGE_KEY]: '1' })), true);
  assert.equal(loadLabels('?labels=0', memory({ [LABELS_STORAGE_KEY]: '1' })), false);
  assert.equal(loadLabels('?labels=1', memory()), true);
  assert.equal(loadLabels('?labels', memory()), true);
  assert.equal(loadLabels('?labels=bogus', memory({ [LABELS_STORAGE_KEY]: '1' })), true);
});

test('saveLabels persists and tolerates blocked storage', () => {
  const s = memory();
  saveLabels(true, s);
  assert.equal(loadLabels('', s), true);
  saveLabels(false, s);
  assert.equal(loadLabels('', s), false);
  assert.doesNotThrow(() => saveLabels(true, throwing));
});

test('text overlays: off by default, L shows structure names and robot strength', () => {
  assert.deepEqual(textOverlays({ labels: false, debugGrid: false }), { structureNames: false, robotStrength: false });
  assert.deepEqual(textOverlays({ labels: true, debugGrid: false }), { structureNames: true, robotStrength: true });
  // The debug grid keeps its structure name labels, but not strength numbers.
  assert.deepEqual(textOverlays({ labels: false, debugGrid: true }), { structureNames: true, robotStrength: false });
});

test('L toggles the labels setting and it survives a store reset', () => {
  const store = new Store();
  const controller = new GameController(store, () => 0);
  controller.action('KeyL');
  assert.equal(store.get().ui.labels, true);
  store.reset();
  assert.equal(store.get().ui.labels, true);
  controller.action('KeyL');
  assert.equal(store.get().ui.labels, false);
});
