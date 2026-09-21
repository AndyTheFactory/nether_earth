import { test } from 'vitest';
import assert from 'node:assert/strict';
import { project, unproject, depthKey, groundDepth } from './projection.ts';
import { KEY_TO_AXIS } from '../input/keyboard.ts';

test('project/unproject round-trip', () => {
  for (const [x, y] of [[10, 3], [0, 0], [511, 15], [2.5, 7.25]]) {
    const p = project(x, y);
    const w = unproject(p.x, p.y);
    assert.ok(Math.abs(w.x - x) < 1e-9 && Math.abs(w.y - y) < 1e-9, `${x},${y}`);
  }
});

test('Spectrum orientation: +x runs lower-left to upper-right, +y runs down-right', () => {
  const o = project(0, 0);
  const px = project(1, 0);
  const py = project(0, 1);
  assert.ok(px.x > o.x && px.y < o.y, '+x goes right and up');
  assert.ok(px.x - o.x > o.y - px.y, '+x is mostly rightward (shallow)');
  assert.ok(py.x > o.x && py.y > o.y, '+y goes right and down');
  assert.ok(py.y - o.y > py.x - o.x, '+y is mostly downward (steep)');
  // the far (high-x) end of the 512-cell map lies up and to the right
  const far = project(490, 1);
  const home = project(18, 1);
  assert.ok(far.x > home.x && far.y < home.y);
});

test('height lifts screen y', () => {
  assert.ok(project(1, 1, 8).y < project(1, 1, 0).y);
});

test('depth: nearer the lower-left viewer (lower x, higher y) draws later; higher draws later', () => {
  assert.ok(depthKey(5, 5) > depthKey(6, 5), 'lower x is nearer');
  assert.ok(depthKey(5, 6) > depthKey(5, 5), 'higher y is nearer');
  assert.ok(depthKey(5, 5, 3) > depthKey(5, 5, 0));
  // the key follows ground screen y, so anything lower on screen is nearer
  assert.equal(groundDepth(3, 4), project(3, 4).y);
});

test('keyboard directions match on-screen directions', () => {
  const dir = (code: string) => {
    const m = KEY_TO_AXIS[code];
    const a = project(0, 0);
    const b = project(m.dx, m.dy);
    return { x: b.x - a.x, y: b.y - a.y };
  };
  const right = dir('ArrowRight');
  const left = dir('ArrowLeft');
  const up = dir('ArrowUp');
  const down = dir('ArrowDown');
  assert.ok(right.x > Math.abs(right.y), 'right');
  assert.ok(-left.x > Math.abs(left.y), 'left');
  assert.ok(-up.y > Math.abs(up.x), 'up');
  assert.ok(down.y > Math.abs(down.x), 'down');
});
