// Wires input, menus, transport and the store together. Every player action
// ends in one explicit protocol command (or a local UI-state change). The
// controller never mutates gameplay fields and never pre-validates legality.
import type { Store, Session, MenuMode } from '../state/store.ts';
import { dockedRobot, myCommander, myConstruction } from '../state/store.ts';
import { WebSocketClient, defaultWsUrl, type GameClient, type InboundMessage, RecordingClient } from '../net/client.ts';
import { CommandSender } from '../net/commands.ts';
import type { InputSink, MoveIntent } from '../input/keyboard.ts';
import { findFixture } from '../fixtures/index.ts';
import { playFixture, runFixtureMessage } from '../fixtures/harness.ts';
import { CAPTURE_TARGETS, DESTROY_TARGETS, CHASSIS, WEAPONS, MAX_ORDER_MILES } from '../ui/menus.ts';
import { COL_PIECES, cursorFor, cursorTarget, moveCursor, type BuildCursor, type CursorColumn } from '../ui/construction.ts';

const SESSION_KEY = 'nether-earth.session';
/** Lcb4a: the construction cursor pauses 10 frames (50 Hz) after each move, so a held key repeats every 200 ms. */
export const CURSOR_REPEAT_MS = 200;

export class GameController implements InputSink {
  sender: CommandSender;
  aim: MoveIntent = { dx: 1, dy: 0 };
  weaponIndex = 0;
  /** Construction-screen cursor (UI-only; resets on every new session). */
  buildCursor: BuildCursor | null = null;
  private lastCursorMoveMs = -Infinity;
  private client: GameClient;
  private stopFixture: (() => void) | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;

  constructor(
    private readonly store: Store,
    private readonly now: () => number = () => performance.now(),
  ) {
    this.client = new RecordingClient();
    this.sender = new CommandSender(this.client);
    store.subscribe((s) => {
      this.sender.bind(s.connection.session);
    });
  }

  // ---- modes ----

  startLive(url = defaultWsUrl()): void {
    this.stopFixture?.();
    this.stopFixture = null;
    this.client.close();
    const ws = new WebSocketClient(url, {
      onOpen: () => {
        this.reconnectAttempts = 0;
        const session = this.store.get().connection.session;
        if (session) {
          this.store.setConnection({ status: 'reconnecting' });
          this.sender.reconnect();
        } else {
          this.store.setConnection({ status: 'connected' });
        }
      },
      onMessage: (msg) => this.handleInbound(msg),
      onClose: () => this.onClosed(),
    });
    this.client = ws;
    this.sender = new CommandSender(ws);
    this.sender.bind(this.store.get().connection.session);
    this.store.setConnection({ status: 'connecting', lastError: null });
    ws.connect();
  }

  startFixture(id: string, limit = Infinity): void {
    const fixture = findFixture(id);
    if (!fixture) return;
    this.client.close();
    this.stopFixture?.();
    const rec = new RecordingClient();
    this.client = rec;
    this.sender = new CommandSender(rec);
    this.stopFixture = playFixture(this.store, fixture, 50, this.now, limit);
    const startsMatch = fixture.messages.some((m) => m.type === 'started');
    this.store.setUi({ screen: startsMatch ? 'match' : 'lobby', menu: 'none' });
  }

  /** Outbound messages captured in fixture mode (for the dev harness/tests). */
  get recorded(): RecordingClient | null {
    return this.client instanceof RecordingClient ? this.client : null;
  }

  private handleInbound(msg: InboundMessage): void {
    runFixtureMessage(this.store, msg, this.now());
    if (msg.type === 'resync') this.store.setConnection({ status: 'connected' });
    if (msg.type === 'created' || msg.type === 'joined') {
      const session = this.store.get().connection.session;
      if (session && this.pendingNickname) this.store.setConnection({ session: { ...session, nickname: this.pendingNickname } });
      this.persistSession();
    }
    if (msg.type === 'error' && (msg.error.code === 'invalid_session' || msg.error.code === 'session_mismatch')) {
      this.clearSession();
    }
  }

  private onClosed(): void {
    const s = this.store.get();
    const terminal = ['finished', 'forfeit', 'no_contest'].includes(s.lifecycle.phase);
    if (!s.connection.session || terminal) {
      this.store.setConnection({ status: 'disconnected' });
      return;
    }
    this.store.setConnection({ status: 'reconnecting' });
    const delay = Math.min(8000, 500 * 2 ** this.reconnectAttempts++);
    this.reconnectTimer = setTimeout(() => this.client.connect(), delay);
  }

  reconnectNow(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectAttempts = 0;
    if (this.store.get().connection.status === 'fixture') return;
    this.client.connect();
  }

  private persistSession(): void {
    const s = this.store.get().connection.session;
    try {
      if (s) sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
    } catch {
      /* storage unavailable: reconnect after reload simply won't be offered */
    }
  }

  private clearSession(): void {
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
    this.store.setConnection({ session: null });
  }

  savedSession(): Session | null {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? (JSON.parse(raw) as Session) : null;
    } catch {
      return null;
    }
  }

  resumeSaved(): boolean {
    const s = this.savedSession();
    if (!s) return false;
    this.store.setConnection({ session: s });
    this.store.setUi({ screen: 'match' });
    this.startLive();
    return true;
  }

  // ---- lobby ----

  create(nickname: string): void {
    this.store.setConnection({ session: null });
    this.startLive();
    this.pendingNickname = nickname;
    this.whenOpen(() => this.sender.create(nickname));
  }

  join(code: string, nickname: string): void {
    this.startLive();
    this.pendingNickname = nickname;
    this.whenOpen(() => this.sender.join(code, nickname));
  }

  private pendingNickname = '';

  private whenOpen(fn: () => void): void {
    const unsub = this.store.subscribe((s) => {
      if (s.connection.status === 'connected' && !s.connection.session) {
        unsub();
        fn();
      }
    });
  }

  ready(ready: boolean): void {
    this.sender.ready(ready);
  }

  leave(): void {
    this.sender.leave();
    this.client.close();
    this.stopFixture?.();
    this.clearSession();
    this.store.reset();
  }

  // ---- InputSink ----

  move(intent: MoveIntent): void {
    if (intent.dx === 0 && intent.dy === 0) return;
    const s = this.store.get();
    if (s.ui.screen !== 'match' || !s.latest || s.lifecycle.phase !== 'active') return;
    const me = s.connection.session?.playerId ?? '';
    const cs = myConstruction(s.latest, me);
    if (cs) {
      const now = this.now();
      if (now - this.lastCursorMoveMs < CURSOR_REPEAT_MS) return;
      const before = cursorFor(this.buildCursor, cs.entry_tick);
      this.buildCursor = moveCursor(before, intent.dx, intent.dy);
      if (this.buildCursor !== before) this.lastCursorMoveMs = now;
      return;
    }
    switch (s.ui.menu) {
      case 'direct_control':
        this.sender.command({ kind: 'direct_robot_move', dx: intent.dx, dy: intent.dy });
        return;
      case 'combat':
        this.aim = intent;
        return;
      case 'order_distance':
        this.adjustDistance(intent.dx !== 0 ? intent.dx : -intent.dy);
        return;
      case 'none': {
        const c = myCommander(s.latest, me);
        if (c?.mode === 'free') this.sender.command({ kind: 'commander_move', dx: intent.dx, dy: intent.dy });
        return;
      }
      default:
        return;
    }
  }

  vertical(rising: boolean): void {
    const s = this.store.get();
    if (s.ui.screen !== 'match' || s.lifecycle.phase !== 'active') return;
    const me = s.connection.session?.playerId ?? '';
    if (s.latest && myConstruction(s.latest, me)) {
      // Space is the Spectrum "fire" key on the construction screen.
      if (rising) this.constructionFire();
      return;
    }
    this.sender.command({ kind: 'commander_set_vertical_intent', rising });
    if (rising && s.ui.menu !== 'none') this.store.setUi({ menu: 'none' });
  }

  action(code: string): void {
    const s = this.store.get();
    if (code === 'KeyG') {
      this.store.setUi({ debugGrid: !s.ui.debugGrid });
      return;
    }
    if (s.ui.screen !== 'match' || !s.latest) return;
    const me = s.connection.session?.playerId ?? '';
    const digit = /^Digit(\d)$/.exec(code)?.[1];
    if (myConstruction(s.latest, me)) {
      if (code === 'Enter') this.constructionFire();
      else if (code === 'Escape' || code === 'KeyC') this.menuAction('cancel', '');
      else if (digit) {
        const n = Number(digit);
        const mod = n >= 1 && n <= 3 ? CHASSIS[n - 1] : n >= 4 && n <= 7 ? WEAPONS[n - 4] : n === 8 ? 'electronics' : null;
        if (mod) this.menuAction('module', mod);
      }
      return;
    }
    const robot = dockedRobot(s.latest, me);
    if (!robot) return;
    if (code === 'Escape') {
      this.menuAction('menu', BACK[s.ui.menu]);
      return;
    }
    if (code === 'Enter') {
      if (s.ui.menu === 'none') this.menuAction('menu', 'robot_menu');
      else if (s.ui.menu === 'order_distance') this.menuAction('confirm', '');
      else if (s.ui.menu === 'combat') this.menuAction('fire', '');
      return;
    }
    if (code === 'Minus' || code === 'NumpadSubtract') return this.adjustDistance(-1);
    if (code === 'Equal' || code === 'NumpadAdd') return this.adjustDistance(1);
    if (!digit) return;
    const n = Number(digit);
    switch (s.ui.menu) {
      case 'robot_menu':
        this.menuAction('menu', (['direct_control', 'orders', 'combat'] as const)[n - 1] ?? s.ui.menu);
        return;
      case 'orders':
        if (n === 1) this.menuAction('order', 'stop_and_defend');
        else this.menuAction('pick', (['advance', 'retreat', 'search_capture', 'search_destroy'] as const)[n - 2] ?? '');
        return;
      case 'order_target': {
        const targets = s.ui.pendingOrder === 'search_capture' ? CAPTURE_TARGETS : DESTROY_TARGETS;
        if (targets[n - 1]) this.menuAction('target', targets[n - 1]);
        return;
      }
      case 'combat':
        this.menuAction('weapon', String(n - 1));
        return;
      default:
        return;
    }
  }

  /** Fire on the construction screen: act on whatever the cursor is on (Lca0f). */
  constructionFire(): void {
    const s = this.store.get();
    const cs = s.latest ? myConstruction(s.latest, s.connection.session?.playerId ?? '') : null;
    if (!cs) return;
    const target = cursorTarget(cursorFor(this.buildCursor, cs.entry_tick));
    if (target.kind === 'exit') this.menuAction('cancel', '');
    else if (target.kind === 'start') this.menuAction('launch', '');
    else this.menuAction('module', target.module);
  }

  /** Mouse on the construction screen: move the cursor to the clicked option and fire. */
  constructionPick(column: CursorColumn, piece: number): void {
    const s = this.store.get();
    const cs = s.latest ? myConstruction(s.latest, s.connection.session?.playerId ?? '') : null;
    if (!cs) return;
    const c = cursorFor(this.buildCursor, cs.entry_tick);
    this.buildCursor = { ...c, column, piece: column === COL_PIECES ? piece : c.piece };
    this.constructionFire();
  }

  private adjustDistance(delta: number): void {
    const d = this.store.get().ui.distanceMiles;
    this.store.setUi({ distanceMiles: Math.max(0, Math.min(MAX_ORDER_MILES, d + delta)) });
  }

  // ---- panel actions (buttons and keys converge here) ----

  menuAction(action: string, arg: string): void {
    const s = this.store.get();
    const me = s.connection.session?.playerId ?? '';
    const snap = s.latest;
    switch (action) {
      case 'menu':
        this.store.setUi({ menu: (arg || 'none') as MenuMode });
        return;
      case 'pick':
        if (arg === 'advance' || arg === 'retreat') this.store.setUi({ pendingOrder: arg, menu: 'order_distance' });
        else if (arg === 'search_capture' || arg === 'search_destroy') this.store.setUi({ pendingOrder: arg, menu: 'order_target' });
        return;
      case 'dist':
        this.adjustDistance(Number(arg));
        return;
      case 'order':
      case 'confirm':
      case 'target': {
        const robot = snap ? dockedRobot(snap, me) : null;
        if (!robot) return;
        const entityId = robot.entity_id;
        if (action === 'order') this.sender.command({ kind: 'set_robot_order', entityId, order: { kind: 'stop_and_defend' } });
        else if (action === 'confirm' && (s.ui.pendingOrder === 'advance' || s.ui.pendingOrder === 'retreat')) {
          this.sender.command({ kind: 'set_robot_order', entityId, order: { kind: s.ui.pendingOrder, distanceMiles: s.ui.distanceMiles } });
        } else if (action === 'target' && s.ui.pendingOrder === 'search_capture') {
          this.sender.command({ kind: 'set_robot_order', entityId, order: { kind: 'search_capture', target: arg as (typeof CAPTURE_TARGETS)[number] } });
        } else if (action === 'target' && s.ui.pendingOrder === 'search_destroy') {
          this.sender.command({ kind: 'set_robot_order', entityId, order: { kind: 'search_destroy', target: arg as (typeof DESTROY_TARGETS)[number] } });
        }
        this.store.setUi({ menu: 'none', pendingOrder: null, notice: 'order sent' });
        return;
      }
      case 'weapon':
        this.weaponIndex = Number(arg);
        return;
      case 'fire': {
        const robot = snap ? dockedRobot(snap, me) : null;
        if (!robot) return;
        const weapons = ((robot.build as { weapons?: string[] }).weapons ?? []) as ('cannon' | 'missile' | 'phaser' | 'nuclear')[];
        const weapon = weapons[this.weaponIndex] ?? weapons[0];
        if (!weapon) return;
        this.sender.command({ kind: 'robot_fire', entityId: robot.entity_id, weapon, targetX: robot.x + this.aim.dx, targetY: robot.y + this.aim.dy });
        this.store.setUi({ notice: `fire ${weapon}` });
        return;
      }
      case 'module': {
        const cs = snap ? myConstruction(snap, me) : null;
        if (!cs) return;
        const b = cs.build as { chassis?: string | null; weapons?: string[]; electronics?: string | null };
        const has = b.chassis === arg || (b.weapons ?? []).includes(arg) || b.electronics === arg;
        const module = arg as 'bipod' | 'tracks' | 'anti_grav' | 'cannon' | 'missile' | 'phaser' | 'nuclear' | 'electronics';
        this.sender.command(has ? { kind: 'deselect_module', module } : { kind: 'select_module', module });
        return;
      }
      case 'launch':
        this.sender.command({ kind: 'launch_robot' });
        this.store.setUi({ notice: 'launch requested' });
        return;
      case 'cancel':
        this.sender.command({ kind: 'cancel_construction' });
        return;
      case 'reconnect':
        this.reconnectNow();
        return;
      case 'leave':
        this.leave();
        return;
    }
  }

  /** Click-to-aim in combat mode: target the clicked cell. */
  aimAtCell(cell: { x: number; y: number }): void {
    const s = this.store.get();
    if (s.ui.menu !== 'combat' || !s.latest) return;
    const robot = dockedRobot(s.latest, s.connection.session?.playerId ?? '');
    if (!robot) return;
    const dx = Math.sign(cell.x - robot.x) as -1 | 0 | 1;
    const dy = Math.sign(cell.y - robot.y) as -1 | 0 | 1;
    if (dx !== 0 || dy !== 0) this.aim = dx !== 0 ? { dx, dy: 0 } : { dx: 0, dy };
  }
}

const BACK: Record<MenuMode, MenuMode> = {
  none: 'none',
  robot_menu: 'none',
  direct_control: 'robot_menu',
  orders: 'robot_menu',
  order_distance: 'orders',
  order_target: 'orders',
  combat: 'robot_menu',
};
