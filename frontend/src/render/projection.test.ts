import { test } from 'vitest';
import assert from 'node:assert/strict';
import { project, unproject, depthKey } from './projection.ts';

test('project/unproject round-trip', () => {
  const p = project(10, 3);
  const w = unproject(p.x, p.y);
  assert.ok(Math.abs(w.x - 10) < 1e-9 && Math.abs(w.y - 3) < 1e-9);
});

test('height lifts screen y; depth increases down-screen', () => {
  assert.ok(project(1, 1, 8).y < project(1, 1, 0).y);
  assert.ok(depthKey(2, 2) > depthKey(1, 1));
});
