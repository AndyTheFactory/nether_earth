// @vitest-environment happy-dom
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { KeyboardIntent, bindKeyboard, type InputSink } from './keyboard.ts';

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

test('Alt+Q is forwarded as the quit action once, ignoring repeat', () => {
  const { log, k } = harness();
  k.keyDown('KeyQ', false, true);
  k.keyDown('KeyQ', true, true);
  assert.deepEqual(log, ['action Alt+KeyQ']);
});

test('plain Q (no Alt) is forwarded as an ordinary action, not the quit action', () => {
  const { log, k } = harness();
  k.keyDown('KeyQ', false);
  assert.deepEqual(log, ['action KeyQ']);
});

test('Alt held with an unrelated key does not trigger the quit action', () => {
  const { log, k } = harness();
  k.keyDown('Escape', false, true);
  assert.deepEqual(log, ['action Escape']);
});

// ---- #247: a stale-focused menu button natively re-activating on Enter ----
//
// Clicking any menu <button> (ui/menus.ts) leaves it holding DOM focus.
// keyDown() previously returned false for action()-routed keys (Enter,
// Escape, digits, …), so bindKeyboard() never called preventDefault() for
// them — only the browser's own default activation behavior for a focused
// <button> (Enter/Space triggers a native click) was left in place. Verified
// live against Chromium: click DIRECT CONTROL, press Escape (back to the
// list), then press Enter to do something else — the *stale* focused
// DIRECT CONTROL button natively re-activated instead, silently overriding
// the intended navigation. keyDown() must report every game key as handled
// so bindKeyboard() suppresses that default, exactly like the axis/rise keys.
test('action-routed keys (Enter, digits, Escape) report handled, like axis/rise keys, so a stale-focused button cannot be natively re-activated later', () => {
  const { k } = harness();
  assert.equal(k.keyDown('Enter', false), true);
  assert.equal(k.keyDown('Escape', false), true);
  assert.equal(k.keyDown('Digit1', false), true);
});

test('bindKeyboard suppresses the default action for every routed key, not just movement/rise', () => {
  const sink: InputSink = { move() {}, vertical() {}, action() {} };
  const intent = new KeyboardIntent(sink);
  const unbind = bindKeyboard(window, intent, 1000);
  try {
    const e = new KeyboardEvent('keydown', { code: 'Enter', bubbles: true, cancelable: true });
    window.dispatchEvent(e);
    assert.equal(e.defaultPrevented, true, 'Enter must suppress the browser default (native button re-activation)');
  } finally {
    unbind();
  }
});

test('bindKeyboard leaves modifier combos (Ctrl/Meta) alone, even for routed key codes', () => {
  const sink: InputSink = { move() {}, vertical() {}, action() {} };
  const intent = new KeyboardIntent(sink);
  const unbind = bindKeyboard(window, intent, 1000);
  try {
    const e = new KeyboardEvent('keydown', { code: 'Digit1', ctrlKey: true, bubbles: true, cancelable: true });
    window.dispatchEvent(e);
    assert.equal(e.defaultPrevented, false, 'Ctrl+1 (browser tab-switch) must not be hijacked');
  } finally {
    unbind();
  }
});

test('bindKeyboard leaves Alt combos alone except the Alt+Q quit shortcut', () => {
  const sink: InputSink = { move() {}, vertical() {}, action() {} };
  const intent = new KeyboardIntent(sink);
  const unbind = bindKeyboard(window, intent, 1000);
  try {
    const other = new KeyboardEvent('keydown', { code: 'Digit1', altKey: true, bubbles: true, cancelable: true });
    window.dispatchEvent(other);
    assert.equal(other.defaultPrevented, false, 'Alt+1 must not be hijacked');

    let quitFired = false;
    const quitSink: InputSink = { move() {}, vertical() {}, action: (c) => { if (c === 'Alt+KeyQ') quitFired = true; } };
    const quitIntent = new KeyboardIntent(quitSink);
    const unbindQuit = bindKeyboard(window, quitIntent, 1000);
    try {
      const quit = new KeyboardEvent('keydown', { code: 'KeyQ', altKey: true, bubbles: true, cancelable: true });
      window.dispatchEvent(quit);
      assert.equal(quitFired, true, 'Alt+Q must still route to the quit action');
    } finally {
      unbindQuit();
    }
  } finally {
    unbind();
  }
});
