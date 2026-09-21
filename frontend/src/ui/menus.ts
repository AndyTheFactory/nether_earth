// Robot command / orders / combat and construction panels (M8.6/M8.7/M8.8).
// Panels show authoritative state and offer command types the UI may send;
// they never decide whether a command is legal.
import type { AppState } from '../state/store.ts';
import { dockedRobot, myConstruction, myResources } from '../state/store.ts';
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
  const cs = myConstruction(snap, me);
  if (cs) {
    panel.set(renderConstruction(cs, myResources(snap, me)));
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

function renderConstruction(cs: NonNullable<ReturnType<typeof myConstruction>>, res: ReturnType<typeof myResources>): string {
  const build = cs.build as { chassis?: string | null; weapons?: string[]; electronics?: string | null };
  const buffer = cs.buffer as { general: number; category: Record<string, number> };
  const entry = cs.entry_snapshot as { general: number; category: Record<string, number> };
  const has = (m: string) => build.chassis === m || (build.weapons ?? []).includes(m) || build.electronics === m;
  const mod = (m: string, key: number) => btn('module', `${key} ${m.replace('_', '-')}${has(m) ? ' ✔' : ''}`, m, has(m) ? 'on' : '');
  let html = `<h3>CONSTRUCTION · ${esc(cs.war_base_id)}</h3>`;
  html += `<div>chassis: ${CHASSIS.map((c, i) => mod(c, i + 1)).join(' ')}</div>`;
  html += `<div>weapons: ${WEAPONS.map((w, i) => mod(w, i + 4)).join(' ')} · ${mod('electronics', 8)}</div>`;
  const stack = [build.chassis, ...WEAPONS.filter((w) => (build.weapons ?? []).includes(w)), build.electronics].filter(Boolean) as string[];
  html += `<div class="preview">stack (bottom→top): ${stack.length ? stack.map((m) => `<span class="mod ${esc(m)}">${esc(m)}</span>`).join(' ') : '<i>empty</i>'}</div>`;
  const c = buffer.category;
  html += `<div>session buffer: GEN ${buffer.general} · CHS ${c.chassis ?? 0} · ELE ${c.electronics ?? 0} · NUK ${c.nuclear ?? 0} · MIS ${c.missile ?? 0} · PHA ${c.phaser ?? 0} · CAN ${c.cannon ?? 0}</div>`;
  if (res) html += `<div class="hint">committed pool: GEN ${res.general} · CHS ${res.chassis} · ELE ${res.electronics} · NUK ${res.nuclear} · MIS ${res.missile} · PHA ${res.phaser} · CAN ${res.cannon}</div>`;
  const spentGen = entry.general - buffer.general;
  const spentCat = Object.entries(entry.category)
    .map(([k, v]) => [k, v - (buffer.category[k] ?? 0)] as const)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => `${k} ${v}`)
    .join(', ');
  html += `<div>spent this session: general ${spentGen}${spentCat ? ` · ${esc(spentCat)}` : ''}</div>`;
  // Spectrum semantics (CR002.12/13): EXIT MENU discards the build; both leave the screen.
  html += `<div>${btn('cancel', 'esc Exit menu', '')} ${btn('launch', '⏎ Start robot', '')}</div>`;
  return html;
}
