import { Application } from 'pixi.js';
import './style.css';
import { Store } from './state/store.ts';
import { GameController } from './app/controller.ts';
import { WorldRenderer } from './render/renderer.ts';
import { loadAssets } from './render/assets.ts';
import { loadMap, DEFAULT_MAP_ID } from './world/map.ts';
import { KeyboardIntent, bindKeyboard } from './input/keyboard.ts';
import { mountLobby } from './ui/lobby.ts';
import { Panel } from './ui/dom.ts';
import { renderHud } from './ui/hud.ts';
import { renderMenus } from './ui/menus.ts';
import { renderOverlay } from './ui/overlays.ts';

async function main(): Promise<void> {
  const host = document.querySelector<HTMLDivElement>('#app');
  if (!host) throw new Error('Missing #app host element');

  const app = new Application();
  await app.init({ background: '#000000', resizeTo: window, antialias: false, roundPixels: true });
  host.appendChild(app.canvas);

  const map = loadMap(DEFAULT_MAP_ID);
  const missing = await loadAssets();
  if (missing.length) console.info(`[assets] ${missing.length} semantic assets have no image; using placeholders`);

  const store = new Store();
  const controller = new GameController(store);
  const renderer = new WorldRenderer(app, map);

  const ui = document.createElement('div');
  ui.id = 'ui';
  host.appendChild(ui);
  mountLobby(ui, store, {
    create: (n) => controller.create(n),
    join: (c, n) => controller.join(c, n),
    ready: (r) => controller.ready(r),
    fixture: (id) => controller.startFixture(id),
  });
  const hud = new Panel('hud');
  const menus = new Panel('menus', (a, arg) => controller.menuAction(a, arg));
  const overlay = new Panel('overlay', (a, arg) => controller.menuAction(a, arg));
  ui.append(hud.root, menus.root, overlay.root);

  const keyboard = new KeyboardIntent(controller);
  bindKeyboard(window, keyboard);
  app.canvas.addEventListener('click', (ev) => controller.aimAtCell(renderer.screenToCell(ev.offsetX, ev.offsetY)));

  const params = new URLSearchParams(location.search);
  const fixture = params.get('fixture');
  if (fixture) controller.startFixture(fixture, params.has('until') ? Number(params.get('until')) : Infinity);
  else if (params.has('resume')) controller.resumeSaved();
  else if (params.get('auto') === 'create') controller.create(params.get('nick') ?? 'Alpha');
  else if (params.get('auto') === 'join') controller.join((params.get('code') ?? '').toUpperCase(), params.get('nick') ?? 'Bravo');
  if (params.has('autoready')) {
    let sent = false;
    store.subscribe((s) => {
      if (!sent && s.connection.session && s.lifecycle.phase === 'waiting' && s.connection.status === 'connected') {
        sent = true;
        controller.ready(true);
      }
    });
  }

  app.ticker.add(() => {
    const now = performance.now();
    const s = store.get();
    app.canvas.style.visibility = s.ui.screen === 'match' ? 'visible' : 'hidden';
    renderer.render(s, now);
    renderHud(hud, s, map);
    renderMenus(menus, s, controller.aim, controller.weaponIndex);
    renderOverlay(overlay, s, Date.now());
  });

  // dev hooks for the fixture harness / live checks
  (window as unknown as { nether: unknown }).nether = { store, controller, renderer };
}

void main();
