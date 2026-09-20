import type { AppState } from '../state/store.ts';
import { Panel, esc, btn } from './dom.ts';

export function renderOverlay(panel: Panel, s: AppState, nowEpochMs: number): void {
  const me = s.connection.session?.playerId;
  const lc = s.lifecycle;
  if (s.ui.screen !== 'match') {
    panel.set('');
    return;
  }
  if (s.connection.status === 'reconnecting' || s.connection.status === 'disconnected') {
    panel.set(`<div class="box"><h2>${s.connection.status === 'reconnecting' ? 'RECONNECTING…' : 'DISCONNECTED'}</h2><p>Gameplay is paused until the server confirms resume.</p>${btn('reconnect', 'Reconnect now')}</div>`);
    return;
  }
  switch (lc.phase) {
    case 'paused': {
      // graceDeadlineMs is a wall-clock epoch timestamp (runtime metadata, not tick state).
      const remaining = lc.graceDeadlineMs !== null ? Math.max(0, Math.ceil((lc.graceDeadlineMs - nowEpochMs) / 1000)) : null;
      panel.set(`<div class="box"><h2>PAUSED</h2><p>${esc(lc.pausedBy)} disconnected. Simulation is frozen.</p>${remaining !== null ? `<p>Forfeit in about ${remaining}s unless they return.</p>` : ''}</div>`);
      return;
    }
    case 'finished':
      panel.set(`<div class="box result"><h2>${lc.winnerPlayerId === me ? 'VICTORY' : 'DEFEAT'}</h2><p>Winner: ${esc(lc.winnerPlayerId)} at tick ${lc.resultTick}</p>${btn('leave', 'Back to lobby')}</div>`);
      return;
    case 'forfeit':
      panel.set(`<div class="box result"><h2>${lc.winnerPlayerId === me ? 'VICTORY BY FORFEIT' : 'FORFEIT'}</h2><p>${esc(lc.forfeitingPlayerId)} failed to reconnect (${esc(lc.resultReason)}).</p>${btn('leave', 'Back to lobby')}</div>`);
      return;
    case 'no_contest':
      panel.set(`<div class="box result"><h2>NO CONTEST</h2><p>Both players disconnected (${esc(lc.resultReason)}).</p>${btn('leave', 'Back to lobby')}</div>`);
      return;
    case 'waiting':
      panel.set(`<div class="box"><h2>WAITING</h2><p>Match has not started.</p></div>`);
      return;
    default:
      panel.set('');
  }
}
