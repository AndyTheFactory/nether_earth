// Robot command / orders / combat panels (M8.6/M8.7), laid out as the
// Spectrum's right-hand HUD column while docked (CR003.6, cr003/robot-menu.png).
// Panels show authoritative state and offer command types the UI may send;
// they never decide whether a command is legal.
import type { AppState, MenuMode } from '../state/store.ts';
import { dockedRobot, gameClock, myConstruction } from '../state/store.ts';
import { Panel, esc } from './dom.ts';

export const CHASSIS = ['bipod', 'tracks', 'anti_grav'] as const;
export const WEAPONS = ['cannon', 'missile', 'phaser', 'nuclear'] as const;
export const CAPTURE_TARGETS = ['neutral_factory', 'enemy_factory', 'enemy_war_base'] as const;
export const DESTROY_TARGETS = ['robot', 'factory', 'war_base'] as const;
export const MAX_ORDER_MILES = 50;

/** The four robot-menu blocks, top to bottom, and the menu modes each one stands for. */
const MAIN_BLOCKS: { lines: [string, string]; action?: string; arg?: string; modes: MenuMode[] }[] = [
  { lines: ['DIRECT', 'CONTROL'], action: 'menu', arg: 'direct_control', modes: ['direct_control'] },
  { lines: ['GIVE', 'ORDERS'], action: 'menu', arg: 'orders', modes: ['orders', 'order_distance', 'order_target'] },
  { lines: ['COMBAT', 'MODE'], action: 'menu', arg: 'combat', modes: ['combat'] },
  // Leaving is the existing rise key (hold space); the block is a label, not a new command path.
  { lines: ['LEAVE', 'ROBOT'], modes: [] },
];

const ORDER_BLOCKS: { lines: [string, string]; action: string; arg: string }[] = [
  { lines: ['STOP AND', 'DEFEND'], action: 'order', arg: 'stop_and_defend' },
  { lines: ['ADVANCE', '?? MILES'], action: 'pick', arg: 'advance' },
  { lines: ['RETREAT', '?? MILES'], action: 'pick', arg: 'retreat' },
  { lines: ['SEARCH &', 'CAPTURE'], action: 'pick', arg: 'search_capture' },
  { lines: ['SEARCH &', 'DESTROY'], action: 'pick', arg: 'search_destroy' },
];

const TARGET_LABEL: Record<string, [string, string]> = {
  neutral_factory: ['NEUTRAL', 'FACTORY'],
  enemy_factory: ['ENEMY', 'FACTORY'],
  enemy_war_base: ['ENEMY', 'WARBASE'],
  robot: ['ENEMY', 'ROBOTS'],
  factory: ['ENEMY', 'FACTORY'],
  war_base: ['ENEMY', 'WARBASE'],
};

/** One stacked two-line block: first line left, second line right (as on the Spectrum). */
function block(lines: [string, string], selected: boolean, action?: string, arg = ''): string {
  const inner = `<span class="l1">${esc(lines[0])}</span><span class="l2">${esc(lines[1])}</span>`;
  const cls = `mblk${selected ? ' on' : ''}`;
  if (!action) return `<div class="${cls}">${inner}</div>`;
  return `<button class="${cls}" data-action="${esc(action)}" data-arg="${esc(arg)}">${inner}</button>`;
}

function small(label: string, action: string, arg = '', selected = false): string {
  return `<button class="msm${selected ? ' on' : ''}" data-action="${esc(action)}" data-arg="${esc(arg)}">${esc(label)}</button>`;
}

/** The current order as the column's word stack (STOP / AND / DEFEND). */
export function orderText(order: Record<string, unknown> | null | undefined): string {
  if (!order) return 'NONE';
  switch (String(order.kind)) {
    case 'stop_and_defend':
      return 'STOP AND DEFEND';
    case 'advance':
    case 'retreat':
      return `${String(order.kind).toUpperCase()} ${order.distance_miles} MILES`;
    case 'search_capture':
    case 'search_destroy': {
      const t = TARGET_LABEL[String(order.target)]?.join(' ') ?? String(order.target).replace(/_/g, ' ').toUpperCase();
      return `${order.kind === 'search_capture' ? 'CAPTURE' : 'DESTROY'} ${t}`;
    }
    default:
      return String(order.kind).replace(/_/g, ' ').toUpperCase();
  }
}

/** DAY / TIME lines of the red block, fixed to the Spectrum's 10-column width. */
export function dayTimeLines(tick: number): [string, string] {
  const c = gameClock(tick);
  const day = `DAY:${String(c.day).padStart(6, ' ')}`;
  const time = `TIME:${String(c.hour).padStart(2, '0')}.${String(c.minute).padStart(2, '0')}`.padEnd(10, ' ');
  return [day, time];
}

function options(s: AppState, weapons: string[], aim: { dx: number; dy: number }, weaponIndex: number, busy: boolean): string {
  const menu = s.ui.menu;
  const back = (to: MenuMode) => small('ESC BACK', 'menu', to);
  switch (menu) {
    case 'orders':
      return ORDER_BLOCKS.map((o) => block(o.lines, false, o.action, o.arg)).join('') + back('robot_menu');
    case 'order_distance': {
      const o = ORDER_BLOCKS.find((x) => x.arg === s.ui.pendingOrder);
      return (
        block([o?.lines[0] ?? '', `${String(s.ui.distanceMiles).padStart(2, '0')} MILES`], true) +
        `<div class="mrow">${small('-10', 'dist', '-10')}${small('-', 'dist', '-1')}${small('+', 'dist', '1')}${small('+10', 'dist', '10')}</div>` +
        `<div class="mhint">0-${MAX_ORDER_MILES} MILES</div>` +
        `<div class="mrow">${small('⏎ SEND', 'confirm')}${back('orders')}</div>`
      );
    }
    case 'order_target': {
      const o = ORDER_BLOCKS.find((x) => x.arg === s.ui.pendingOrder);
      const targets = s.ui.pendingOrder === 'search_capture' ? CAPTURE_TARGETS : DESTROY_TARGETS;
      return block(o?.lines ?? ['', ''], true) + targets.map((t) => block(TARGET_LABEL[t], false, 'target', t)).join('') + back('orders');
    }
    case 'combat': {
      const arrow = aim.dx > 0 ? '→' : aim.dx < 0 ? '←' : aim.dy > 0 ? '↓' : '↑';
      return (
        block(MAIN_BLOCKS[2].lines, true) +
        `<div class="mhint">AIM ${arrow}</div>` +
        `<div class="mrow">${weapons.map((w, i) => small(`${i + 1} ${w.toUpperCase()}`, 'weapon', String(i), i === weaponIndex)).join('')}</div>` +
        `<div class="mrow">${small('⏎ FIRE', 'fire')}${back('robot_menu')}</div>` +
        (busy ? '<div class="mhint">PROJECTILE IN FLIGHT</div>' : '')
      );
    }
    default:
      return MAIN_BLOCKS.map((m) => block(m.lines, m.modes.includes(menu), m.action, m.arg)).join('');
  }
}

const HINT: Record<MenuMode, string> = {
  none: '⏎ MENU  SPACE LEAVE',
  robot_menu: '1-3 SELECT  ESC CLOSE',
  direct_control: 'ARROWS MOVE  ESC BACK',
  orders: '1-5 SELECT  ESC BACK',
  order_distance: 'ARROWS/+- MILES',
  order_target: '1-3 SELECT  ESC BACK',
  combat: 'ARROWS AIM  1-4 WEAPON',
};

/** The docked robot the column is shown for, or null when the column is hidden. */
function columnRobot(s: AppState) {
  const snap = s.latest;
  const me = s.connection.session?.playerId ?? '';
  if (s.ui.screen !== 'match' || !snap) return null;
  // The construction session has its own full-screen view (ui/construction.ts).
  if (myConstruction(snap, me)) return null;
  return dockedRobot(snap, me);
}

/** Whether the right-hand menu column is on screen (the camera centres beside it). */
export function menuColumnShown(s: AppState): boolean {
  return columnRobot(s) !== null;
}

export function renderMenus(panel: Panel, s: AppState, aim: { dx: number; dy: number }, weaponIndex: number): void {
  const snap = s.latest;
  const robot = columnRobot(s);
  if (!snap || !robot) {
    panel.set('');
    return;
  }
  const b = robot.build as { weapons?: string[] };
  const [day, time] = dayTimeLines(snap.tick);
  panel.set(
    `<div class="mclock"><div>${esc(day)}</div><div>${esc(time)}</div></div>` +
      `<div class="mopts">${options(s, b.weapons ?? [], aim, weaponIndex, !!robot.active_projectile_id)}</div>` +
      `<div class="mhint">${esc(HINT[s.ui.menu])}</div>` +
      `<div class="morders"><div class="mhead">-ORDERS-</div><div class="mtext">${orderText(robot.order as Record<string, unknown> | null).split(' ').map(esc).join('<br>')}</div></div>` +
      `<div class="mstrength"><div class="mhead">STRENGTH</div><div class="mval">${esc(robot.strength)}%</div></div>`,
  );
}
