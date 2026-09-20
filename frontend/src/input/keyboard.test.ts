import { test } from 'vitest';
import assert from 'node:assert/strict';
import { KeyboardIntent, type InputSink } from './keyboard.ts';

function harness() {
  const log: string[] = [];
  const sink: InputSink = {
    move: (i) => log.push(`move ${i.dx},${i.dy}`),
    vertical: (r) => log.push(`vertical ${r}`),
    action: (c) => log.push(`action ${c}`),
  };
  return { log, k: new KeyboardIntent(sink) };
}

test('space press/release yields one rise edge and one descend edge, ignoring repeat', () => {
  const { log, k } = harness();
  k.keyDown('Space', false);
  k.keyDown('Space', true);
  k.keyDown('Space', true);
  k.keyUp('Space');
  assert.deepEqual(log, ['vertical true', 'vertical false']);
});

test('arrow keys emit moves; pulse repeats while held; release stops', () => {
  const { log, k } = harness();
  k.keyDown('ArrowRight', false);
  k.pulse();
  k.keyUp('ArrowRight');
  k.pulse();
  assert.deepEqual(log, ['move 1,0', 'move 1,0']);
});

test('opposite keys cancel; two axes collapse to one cardinal move', () => {
  const { k } = harness();
  k.keyDown('ArrowLeft', false);
  k.keyDown('ArrowRight', false);
  assert.deepEqual(k.current(), { dx: 0, dy: 0 });
  k.keyUp('ArrowLeft');
  k.keyDown('ArrowUp', false);
  assert.deepEqual(k.current(), { dx: 1, dy: 0 });
});

test('focus loss releases held keys and rising intent', () => {
  const { log, k } = harness();
  k.keyDown('Space', false);
  k.keyDown('KeyD', false);
  k.releaseAll();
  k.pulse();
  assert.deepEqual(log, ['vertical true', 'move 1,0', 'vertical false']);
  assert.equal(k.isRising, false);
});

test('non-movement keys are forwarded as actions once', () => {
  const { log, k } = harness();
  k.keyDown('Enter', false);
  k.keyDown('Enter', true);
  assert.deepEqual(log, ['action Enter']);
});
