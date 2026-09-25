import type { Store } from '../state/store.ts';
import { FIXTURES } from '../fixtures/index.ts';
import { el, esc, btn } from './dom.ts';

export interface LobbyActions {
  create(nickname: string): void;
  playVsComputer(nickname: string): void;
  join(code: string, nickname: string): void;
  ready(ready: boolean): void;
  fixture(id: string): void;
}

export function mountLobby(host: HTMLElement, store: Store, actions: LobbyActions): void {
  const root = el('div', { id: 'lobby' });
  const fixtureOptions = FIXTURES.map((f) => `<option value="${f.id}">${esc(f.title)}</option>`).join('');
  root.innerHTML = `
    <h1>NETHER EARTH</h1>
    <div class="row"><label>Nickname <input id="nick" maxlength="24" value="Commander"></label></div>
    <div class="row"><button id="create">Create match</button><button id="play-vs-computer">Play vs computer</button></div>
    <div class="row"><label>Join code <input id="code" maxlength="12" placeholder="ABCD"></label><button id="join">Join</button></div>
    <div id="lobby-status"></div>
    <div class="row fixtures"><label>Fixture <select id="fixture">${fixtureOptions}</select></label><button id="play-fixture">Play fixture</button></div>
    <p class="hint">Live mode talks to <code>/ws</code> (override with <code>?ws=ws://host/ws</code>). Fixtures need no backend.</p>
  `;
  host.appendChild(root);
  const nick = root.querySelector<HTMLInputElement>('#nick')!;
  const code = root.querySelector<HTMLInputElement>('#code')!;
  const status = root.querySelector<HTMLElement>('#lobby-status')!;
  const params = new URLSearchParams(location.search);
  if (params.get('code')) code.value = params.get('code')!;
  root.querySelector('#create')!.addEventListener('click', () => actions.create(nick.value.trim()));
  root.querySelector('#play-vs-computer')!.addEventListener('click', () => actions.playVsComputer(nick.value.trim()));
  root.querySelector('#join')!.addEventListener('click', () => actions.join(code.value.trim().toUpperCase(), nick.value.trim()));
  root.querySelector('#play-fixture')!.addEventListener('click', () => actions.fixture(root.querySelector<HTMLSelectElement>('#fixture')!.value));
  status.addEventListener('click', (ev) => {
    const t = (ev.target as HTMLElement).closest<HTMLElement>('[data-action]');
    if (t?.dataset.action === 'ready') actions.ready(t.dataset.arg === 'true');
  });

  let last = '';
  store.subscribe((s) => {
    root.style.display = s.ui.screen === 'lobby' ? '' : 'none';
    const c = s.connection;
    const me = c.session?.playerId;
    const myReady = s.lifecycle.players.find((p) => p.playerId === me)?.ready ?? false;
    let html = `<div class="conn">connection: <b>${esc(c.status)}</b>`;
    if (c.lastError) html += ` <span class="err">${esc(c.lastError.code)}: ${esc(c.lastError.message)}</span>`;
    html += '</div>';
    if (c.session) {
      html += `<div>match <code>${esc(c.session.matchId.slice(0, 8))}</code> · you are <b>${esc(c.session.playerId)}</b>`;
      if (c.session.joinCode) html += ` · join code <b class="code">${esc(c.session.joinCode)}</b> <a href="?code=${esc(c.session.joinCode)}" target="_blank">link</a>`;
      html += '</div>';
      html += '<ul>' + s.lifecycle.players.map((p) => `<li>${esc(p.nickname)} (${esc(p.playerId)}) ${p.ready ? '✔ ready' : '… not ready'}</li>`).join('');
      // Solo (CR004.8): `ready_state` lists only the human -- the AI seat has
      // no nickname/session to broadcast -- so the second row is synthesized
      // here rather than left blank or showing a guest nickname.
      if (c.session.vsComputer) html += '<li>Computer ✔ ready</li>';
      html += '</ul>';
      if (!c.session.vsComputer && s.lifecycle.players.length < 2) html += '<div>waiting for opponent…</div>';
      if (!c.session.vsComputer) html += `<div class="row">${btn('ready', myReady ? 'Un-ready' : 'Ready', String(!myReady))}</div>`;
    }
    if (html !== last) {
      last = html;
      status.innerHTML = html;
    }
  });
}
