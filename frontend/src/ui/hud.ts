import type { AppState } from '../state/store.ts';
import { gameClock, myResources, dockedRobot, myConstruction, myCommander } from '../state/store.ts';
import type { MapData } from '../world/map.ts';
import { Panel, esc } from './dom.ts';

const ORDER_LABEL: Record<string, string> = {
  stop_and_defend: 'Stop & Defend',
  advance: 'Advance',
  retreat: 'Retreat',
  search_capture: 'Search & Capture',
  search_destroy: 'Search & Destroy',
};

export function describeOrder(order: Record<string, unknown> | null | undefined): string {
  if (!order) return 'none';
  const kind = String(order.kind);
  const label = ORDER_LABEL[kind] ?? kind;
  if ('distance_miles' in order) return `${label} ${order.distance_miles} mi → x${order.target_x}`;
  if ('target' in order) return `${label}: ${String(order.target).replace(/_/g, ' ')}`;
  return label;
}

export function renderHud(panel: Panel, s: AppState, map: MapData): void {
  const snap = s.latest;
  const me = s.connection.session?.playerId ?? '';
  if (s.ui.screen !== 'match') {
    panel.set('');
    return;
  }
  const phase = s.lifecycle.phase;
  let html = `<div class="line"><b>${esc(s.connection.session?.nickname || me)}</b> (${esc(me)}) · ${esc(s.connection.status)} · <span class="phase ${esc(phase)}">${esc(phase.toUpperCase())}</span>`;
  if (s.connection.session?.joinCode) html += ` · code <b>${esc(s.connection.session.joinCode)}</b>`;
  html += '</div>';
  if (!snap) {
    panel.set(html + '<div class="line">awaiting authoritative snapshot…</div>');
    return;
  }
  const clock = gameClock(snap.tick);
  html += `<div class="line clock">DAY ${clock.day} · ${String(clock.hour).padStart(2, '0')}:${String(clock.minute).padStart(2, '0')} · tick ${snap.tick}${phase === 'paused' ? ' (frozen)' : ''}</div>`;
  const res = myResources(snap, me);
  if (res) {
    html += `<div class="line res">GEN <b>${res.general}</b> · CHS ${res.chassis} · ELE ${res.electronics} · NUK ${res.nuclear} · MIS ${res.missile} · PHA ${res.phaser} · CAN ${res.cannon}</div>`;
  }
  const owned = (p: string, kind: 'war' | 'fac') =>
    snap.structure_ownership.filter((o) => o.owner === p && (kind === 'war' ? map.war_bases.some((w) => w.id === o.structure_id) : map.factories.some((f) => f.id === o.structure_id)) && !snap.structure_destruction.includes(o.structure_id)).length;
  // The AI seat has no nickname/session to read (CR004.8: it has no commander
  // and is never a connected player), so it is named "Computer" here rather
  // than falling back to its bare player id like a disconnected guest would.
  const opponentLabel = (p: string) => (s.connection.session?.vsComputer && p !== me ? 'Computer' : p);
  html += `<div class="line own"><span class="p1">${esc(opponentLabel('p1'))}: ${owned('p1', 'war')} bases / ${owned('p1', 'fac')} factories</span> · <span class="p2">${esc(opponentLabel('p2'))}: ${owned('p2', 'war')} bases / ${owned('p2', 'fac')} factories</span> · robots ${snap.robots.filter((r) => r.owner === me).length}</div>`;
  const cmd = myCommander(snap, me);
  if (cmd) {
    html += `<div class="line">commander (${cmd.x},${cmd.y}) alt ${cmd.altitude} ${cmd.mode.toUpperCase()}${cmd.rising ? ' ↑' : ''}`;
    const robot = dockedRobot(snap, me);
    if (robot) {
      html += ` · docked on <b>${esc(robot.entity_id)}</b> str <b>${robot.strength}</b> · order ${esc(describeOrder(robot.order as Record<string, unknown> | null))}${robot.active_projectile_id ? ' · projectile in flight' : ''}`;
    }
    html += '</div>';
  }
  const cs = myConstruction(snap, me);
  html += `<div class="line mode">mode: ${cs ? 'CONSTRUCTION' : esc(s.ui.menu.replace(/_/g, ' '))}${s.ui.notice ? ` · <span class="notice">${esc(s.ui.notice)}</span>` : ''}${s.connection.lastError ? ` · <span class="err">${esc(s.connection.lastError.code)}: ${esc(s.connection.lastError.message)}</span>` : ''}</div>`;
  for (const cp of snap.capture_progress) {
    html += `<div class="line cap">${esc(cp.capturing_player)} capturing ${esc(cp.structure_id)}: ${Math.floor((100 * cp.elapsed_ticks) / cp.required_ticks)}%</div>`;
  }
  html += `<div class="line hint">arrows/WASD move · space rise · land on the green roof pad of your war base to build · enter menu · esc back · G grid · M sound</div>`;
  panel.set(html);
}
