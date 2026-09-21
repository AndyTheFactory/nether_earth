// Robot command / orders / combat panels (M8.6/M8.7).
// Panels show authoritative state and offer command types the UI may send;
// they never decide whether a command is legal.
import type { AppState } from '../state/store.ts';
import { dockedRobot, myConstruction } from '../state/store.ts';
import { Panel, btn, esc } from './dom.ts';

export const CHASSIS = ['bipod', 'tracks', 'anti_grav'] as const;
export const WEAPONS = ['cannon', 'missile', 'phaser', 'nuclear'] as const;
export const CAPTURE_TARGETS = ['neutral_factory', 'enemy_factory', 'enemy_war_base'] as const;
export const DESTROY_TARGETS = ['robot', 'factory', 'war_base'] as const;
export const MAX_ORDER_MILES = 50;

export function renderMenus(panel: Panel, s: AppState, aim: { dx: number; dy: number }, weaponIndex: number): void {
  const snap = s.latest;
  const me = s.connection.session?.playerId ?? '';
  if (s.ui.screen !== 'match' || !snap) {
    panel.set('');
    return;
  }
  // The construction session has its own full-screen view (ui/construction.ts).
  if (myConstruction(snap, me)) {
    panel.set('');
    return;
  }
  const robot = dockedRobot(snap, me);
  if (!robot) {
    panel.set('');
    return;
  }
  const b = robot.build as { chassis?: string; weapons?: string[]; electronics?: string | null };
  const head = `<h3>${esc(robot.entity_id)} · str ${robot.strength} · ${esc(b.chassis ?? '')} ${(b.weapons ?? []).join('+')}${b.electronics ? '+electronics' : ''}</h3>`;
  switch (s.ui.menu) {
    case 'none':
      panel.set(head + `<div>${btn('menu', '⏎ Robot menu', 'robot_menu')} <span class="hint">space: rise to undock</span></div>`);
      return;
    case 'robot_menu':
      panel.set(head + `<div>${btn('menu', '1 Direct control', 'direct_control')} ${btn('menu', '2 Orders', 'orders')} ${btn('menu', '3 Combat', 'combat')} ${btn('menu', 'esc Close', 'none')}</div>`);
      return;
    case 'direct_control':
      panel.set(head + `<div><b>DIRECT CONTROL</b> arrows/WASD move robot · ${btn('menu', 'esc Back', 'robot_menu')}</div>`);
      return;
    case 'orders':
      panel.set(
        head +
          `<div><b>ORDERS</b> ${btn('order', '1 Stop & Defend', 'stop_and_defend')} ${btn('pick', '2 Advance', 'advance')} ${btn('pick', '3 Retreat', 'retreat')} ${btn('pick', '4 Search & Capture', 'search_capture')} ${btn('pick', '5 Search & Destroy', 'search_destroy')} ${btn('menu', 'esc Back', 'robot_menu')}</div>`,
      );
      return;
    case 'order_distance':
      panel.set(
        head +
          `<div><b>${esc(s.ui.pendingOrder?.toUpperCase() ?? '')}</b> distance <b>${s.ui.distanceMiles}</b> miles (0–${MAX_ORDER_MILES}) ${btn('dist', '−', '-1')} ${btn('dist', '+', '1')} ${btn('dist', '−10', '-10')} ${btn('dist', '+10', '10')} ${btn('confirm', '⏎ Send', '')} ${btn('menu', 'esc Back', 'orders')}</div>`,
      );
      return;
    case 'order_target': {
      const targets = s.ui.pendingOrder === 'search_capture' ? CAPTURE_TARGETS : DESTROY_TARGETS;
      panel.set(head + `<div><b>${esc(s.ui.pendingOrder?.toUpperCase() ?? '')}</b> ${targets.map((t, i) => btn('target', `${i + 1} ${t.replace(/_/g, ' ')}`, t)).join(' ')} ${btn('menu', 'esc Back', 'orders')}</div>`);
      return;
    }
    case 'combat': {
      const weapons = b.weapons ?? [];
      const arrow = aim.dx > 0 ? '→' : aim.dx < 0 ? '←' : aim.dy > 0 ? '↓' : '↑';
      panel.set(
        head +
          `<div><b>COMBAT</b> aim ${arrow} (arrows) · ${weapons.map((w, i) => btn('weapon', `${i + 1} ${w}${i === weaponIndex ? ' ●' : ''}`, String(i))).join(' ')} ${btn('fire', '⏎ Fire', '')}${robot.active_projectile_id ? ' <span class="hint">projectile channel busy</span>' : ''} ${btn('menu', 'esc Back', 'robot_menu')}</div>`,
      );
      return;
    }
  }
}
