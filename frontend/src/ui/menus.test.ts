import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Store, TICKS_PER_HOUR, HOURS_PER_DAY, type MenuMode } from '../state/store.ts';
import { findFixture } from '../fixtures/index.ts';
import { runFixtureMessage } from '../fixtures/harness.ts';
import type { Panel } from './dom.ts';
import { renderMenus, orderText, dayTimeLines } from './menus.ts';
import { radarScale, MENU_COLUMN_UNITS } from './radar.ts';

function docked(menu: MenuMode = 'none') {
  const store = new Store();
  for (const m of findFixture('commander-docked')!.messages) runFixtureMessage(store, m, 0);
  store.setUi({ screen: 'match', menu });
  const panel = { html: '', set(h: string) { this.html = h; } };
  renderMenus(panel as unknown as Panel, store.get(), { dx: 1, dy: 0 }, 0);
  return { store, html: panel.html };
}

/** Text of each stacked block, top to bottom, with its selected flag. */
function blocks(html: string): { text: string; on: boolean }[] {
  return [...html.matchAll(/class="mblk( on)?"[^>]*><span class="l1">([^<]*)<\/span><span class="l2">([^<]*)<\/span>/g)].map((m) => ({ text: `${m[2]} ${m[3]}`, on: !!m[1] }));
}

test('docked column follows the Spectrum order: DAY/TIME, four blocks, ORDERS, STRENGTH', () => {
  const { store, html } = docked();
  const snap = store.get().latest!;
  const robot = snap.robots.find((r) => r.owner === 'p1')!;
  const at = (s: string) => html.indexOf(s);
  assert.ok(at('class="mclock"') >= 0 && at('class="mclock"') < at('class="mopts"'));
  assert.ok(at('class="mopts"') < at('-ORDERS-') && at('-ORDERS-') < at('STRENGTH'));
  assert.deepEqual(
    blocks(html).map((b) => b.text),
    ['DIRECT CONTROL', 'GIVE ORDERS', 'COMBAT MODE', 'LEAVE ROBOT'],
  );
  assert.ok(html.includes(`<div class="mval">${robot.strength}%</div>`));
  const [day, time] = dayTimeLines(snap.tick);
  assert.ok(html.includes(day) && html.includes(time));
});

test('the active mode highlights its block; closed menu highlights none', () => {
  const on = (menu: MenuMode) => blocks(docked(menu).html).filter((b) => b.on).map((b) => b.text);
  assert.deepEqual(on('none'), []);
  assert.deepEqual(on('robot_menu'), []);
  assert.deepEqual(on('direct_control'), ['DIRECT CONTROL']);
  assert.deepEqual(on('combat'), ['COMBAT MODE']);
});

test('blocks keep the existing menu actions; LEAVE ROBOT is a label (space rises)', () => {
  const { html } = docked('robot_menu');
  for (const arg of ['direct_control', 'orders', 'combat']) assert.ok(html.includes(`data-action="menu" data-arg="${arg}"`), arg);
  assert.match(html, /<div class="mblk"><span class="l1">LEAVE<\/span>/);
});

test('orders sub-menu lists the five orders in digit order', () => {
  const { html } = docked('orders');
  assert.deepEqual(
    blocks(html).map((b) => b.text),
    ['STOP AND DEFEND', 'ADVANCE ?? MILES', 'RETREAT ?? MILES', 'SEARCH &amp; CAPTURE', 'SEARCH &amp; DESTROY'],
  );
  assert.ok(html.includes('data-action="order" data-arg="stop_and_defend"'));
  assert.ok(html.includes('data-action="pick" data-arg="search_destroy"'));
});

test('hidden when not docked or not in a match', () => {
  const { store } = docked();
  const panel = { html: 'x', set(h: string) { this.html = h; } };
  store.setUi({ screen: 'lobby' });
  renderMenus(panel as unknown as Panel, store.get(), { dx: 1, dy: 0 }, 0);
  assert.equal(panel.html, '');
});

test('order text and DAY/TIME use the Spectrum wording and 10-column lines', () => {
  assert.equal(orderText(null), 'NONE');
  assert.equal(orderText({ kind: 'stop_and_defend' }), 'STOP AND DEFEND');
  assert.equal(orderText({ kind: 'advance', distance_miles: 12, target_x: 40 }), 'ADVANCE 12 MILES');
  assert.equal(orderText({ kind: 'search_capture', target: 'neutral_factory' }), 'CAPTURE NEUTRAL FACTORY');
  const tick = (3 * HOURS_PER_DAY + 20) * TICKS_PER_HOUR + TICKS_PER_HOUR / 2;
  assert.deepEqual(dayTimeLines(tick), ['DAY:     4', 'TIME:20.30']);
});

test('radar reserves the menu column width', () => {
  assert.equal(radarScale(1600, 176), 4);
  assert.equal(radarScale(1000 - MENU_COLUMN_UNITS * 2, 176), 4);
  assert.equal(radarScale(900 - MENU_COLUMN_UNITS * 2, 176), 3);
  assert.equal(radarScale(375 - MENU_COLUMN_UNITS, 176), 1);
});

test('CSS docks #menus as a right-hand column and narrows it at phone width', () => {
  const css = readFileSync(resolve(__dirname, '../style.css'), 'utf8');
  const rule = /#menus \{([^}]*)\}/.exec(css)![1];
  assert.match(rule, /right: 8px/);
  assert.doesNotMatch(rule, /left:/);
  assert.match(css, /@media \(max-width: 720px\)[^{]*\{ :root \{ --mu: 1px; \} \}/);
  assert.match(css, /#menus \.mclock \{ background: #ff0000; color: var\(--white\)/);
  assert.match(css, /#menus \.mblk \{[^}]*background: #0000ff; color: var\(--cyan\)/);
});
