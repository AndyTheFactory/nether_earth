// @vitest-environment happy-dom
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { Panel } from './dom.ts';

// ---- #247: clicks dropped by a re-render race ----
//
// renderMenus() (ui/menus.ts) builds one HTML string per frame that puts a
// DAY/TIME clock (changes every simulation tick, 20 Hz) as a sibling of the
// menu's interactive buttons. The old Panel.set() did `root.innerHTML = html`
// on every change, so the clock ticking alone tore down and rebuilt the
// button elements dozens of times a second — including mid-click, between a
// real mousedown and its mouseup. Verified against live Chromium (not just
// this happy-dom suite): a burst of full-subtree rebuilds landing inside a
// single click's mousedown→mouseup window dropped the click 90-100% of the
// time; an isolated, non-bursty rebuild did not. Panel.set() now patches
// only the top-level child whose content actually changed, so an unrelated
// sibling (the clock) never touches the buttons' DOM nodes.

function menuHtml(clockTick: number, cursorOn: 'direct_control' | 'orders'): string {
  return (
    `<div class="mclock">DAY:${clockTick}</div>` +
    `<div class="mopts">` +
    `<button class="mblk${cursorOn === 'direct_control' ? ' on' : ''}" data-action="menu" data-arg="direct_control">DIRECT</button>` +
    `<button class="mblk${cursorOn === 'orders' ? ' on' : ''}" data-action="menu" data-arg="orders">ORDERS</button>` +
    `</div>`
  );
}

test('a sibling-only change (the clock ticking) leaves button DOM nodes untouched', () => {
  const panel = new Panel('menus');
  document.body.appendChild(panel.root);
  panel.set(menuHtml(100, 'direct_control'));
  const button = panel.root.querySelector('[data-arg="direct_control"]');
  assert.ok(button);

  // The clock advances every tick; the buttons/cursor do not change.
  for (let tick = 101; tick <= 120; tick++) panel.set(menuHtml(tick, 'direct_control'));

  assert.equal(panel.root.querySelector('[data-arg="direct_control"]'), button, 'button node identity must survive unrelated re-renders');
  assert.ok(panel.root.contains(button), 'button must still be attached to the live DOM');
  document.body.removeChild(panel.root);
});

test('#247 regression: a click survives a burst of re-renders landing between mousedown and mouseup', () => {
  const calls: [string, string][] = [];
  const panel = new Panel('menus', (a, arg) => calls.push([a, arg]));
  document.body.appendChild(panel.root);
  panel.set(menuHtml(100, 'direct_control'));

  const button = panel.root.querySelector<HTMLElement>('[data-arg="direct_control"]')!;
  button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));

  // A burst of snapshot-driven re-renders lands inside the mousedown→mouseup
  // gap (exactly the network-burst / catch-up scenario that reproduced the
  // bug live): the clock ticks several times in a row while the button is
  // mid-press, before the user's mouseup arrives.
  for (let tick = 101; tick <= 105; tick++) panel.set(menuHtml(tick, 'direct_control'));

  button.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  button.dispatchEvent(new MouseEvent('click', { bubbles: true }));

  assert.deepEqual(calls, [['menu', 'direct_control']], 'the click must still reach the action handler after an interleaved re-render burst');
  document.body.removeChild(panel.root);
});

test('a genuine options change (cursor moves) still replaces the affected button', () => {
  const panel = new Panel('menus');
  document.body.appendChild(panel.root);
  panel.set(menuHtml(100, 'direct_control'));
  const before = panel.root.querySelector('[data-arg="direct_control"]');

  panel.set(menuHtml(100, 'orders')); // cursor moved: .mopts content genuinely differs

  const after = panel.root.querySelector('[data-arg="direct_control"]');
  assert.notEqual(after, before, 'a real content change must still update the DOM');
  assert.ok(!after!.className.includes('on'));
  assert.ok(panel.root.querySelector('[data-arg="orders"]')!.className.includes('on'));
  document.body.removeChild(panel.root);
});

test('set("") hides the panel and clears its children', () => {
  const panel = new Panel('menus');
  document.body.appendChild(panel.root);
  panel.set(menuHtml(100, 'direct_control'));
  panel.set('');
  assert.equal(panel.root.style.display, 'none');
  assert.equal(panel.root.children.length, 0);
  document.body.removeChild(panel.root);
});
