// Eval-free shader/uniform code paths, so the production CSP needs no 'unsafe-eval'.
import 'pixi.js/unsafe-eval';
import { Application } from 'pixi.js';
import './style.css';
import { Store, myConstruction } from './state/store.ts';
import { GameController } from './app/controller.ts';
import { WorldRenderer } from './render/renderer.ts';
import { loadAssets } from './render/assets.ts';
import { loadMap, DEFAULT_MAP_ID } from './world/map.ts';
import { KeyboardIntent, bindKeyboard } from './input/keyboard.ts';
import { mountLobby } from './ui/lobby.ts';
import { Panel } from './ui/dom.ts';
import { renderHud } from './ui/hud.ts';
import { renderMenus, menuColumnShown } from './ui/menus.ts';
import { ConstructionScreen } from './ui/construction.ts';
import { renderOverlay } from './ui/overlays.ts';
import { Radar } from './ui/radar.ts';
import { loadLabels } from './state/labels.ts';
import { AudioEngine } from './audio/engine.ts';
import { sfxForSnapshot } from './audio/events.ts';

/**
 * Version and commit this bundle was built from, logged once at boot.
 *
 * `VITE_APP_VERSION` comes from package.json via Vite's own env, and
 * `VITE_GIT_COMMIT` from the image build argument (frontend/Dockerfile). A
 * loaded page therefore states which build it is, so a stale bundle behind a
 * cache is visible in the console instead of being mistaken for a bug.
 */
function logBuild(): void {
  const version = import.meta.env.VITE_APP_VERSION ?? 'dev';
  const commit = import.meta.env.VITE_GIT_COMMIT ?? 'unknown';
  console.info(`Nether Earth frontend ${version} (commit ${commit})`);
}

async function main(): Promise<void> {
  logBuild();
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
  const radar = new Radar(map);
  const construction = new ConstructionScreen((column, piece) => controller.constructionPick(column, piece));
  // Construction covers the play view full screen; lifecycle overlays stay above it.
  ui.append(hud.root, radar.root, menus.root, construction.root, overlay.root);

  // Spectrum audio (#272). Browsers only allow an AudioContext to start from
  // a user gesture, so the engine stays silent until the first click or key.
  const audio = new AudioEngine();
  controller.onUiSound = (name) => audio.play(name);
  const unlock = () => audio.unlock();
  window.addEventListener('pointerdown', unlock);
  window.addEventListener('keydown', unlock);
  // M mutes. It is read here rather than in the controller because muting is
  // presentation, and KeyM reaches the controller unhandled either way.
  window.addEventListener('keydown', (e) => {
    if (e.code === 'KeyM' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      store.setUi({ notice: audio.toggleMuted() ? 'sound off' : 'sound on' });
      // Let the ticker decide again whether music should be running.
      musicScreen = null;
    }
  });

  const keyboard = new KeyboardIntent(controller);
  bindKeyboard(window, keyboard);

  const params = new URLSearchParams(location.search);
  store.setUi({ labels: loadLabels(location.search) });
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

  // Title music plays on the lobby screen, as it does on the Spectrum's title
  // screen, and stops the moment the match view takes over.
  let musicScreen: string | null = null;
  let lastSoundTick = -1;

  app.ticker.add(() => {
    const now = performance.now();
    controller.pollCommanderMove(keyboard.heldIntent());
    const s = store.get();
    app.canvas.style.visibility = s.ui.screen === 'match' ? 'visible' : 'hidden';
    renderer.render(s, now);
    renderHud(hud, s, map);
    radar.update(s.ui.screen === 'match', s.latest, s.connection.session?.playerId ?? null, now);
    ui.classList.toggle('menu-open', menuColumnShown(s));
    renderMenus(menus, s, controller.weaponIndex);
    const cs = s.ui.screen === 'match' && s.latest ? myConstruction(s.latest, s.connection.session?.playerId ?? '') : null;
    construction.update(cs, controller.buildCursor, window.innerWidth, window.innerHeight);
    renderOverlay(overlay, s, Date.now());

    if (s.ui.screen !== musicScreen) {
      musicScreen = s.ui.screen;
      if (s.ui.screen === 'lobby') audio.startMusic();
      else audio.stopMusic();
    }
    if (s.latest && s.latest.tick !== lastSoundTick) {
      lastSoundTick = s.latest.tick;
      for (const name of sfxForSnapshot(s.previous, s.latest)) audio.play(name);
    }
  });

  // dev hooks for the fixture harness / live checks
  (window as unknown as { nether: unknown }).nether = { store, controller, renderer };
}

void main();
