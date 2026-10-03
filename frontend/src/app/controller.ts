// Wires input, menus, transport and the store together. Every player action
// ends in one explicit protocol command (or a local UI-state change). The
// controller never mutates gameplay fields and never pre-validates legality.
import type { Store, Session, MenuMode } from '../state/store.ts';
import { dockedRobot, myCommander, myConstruction } from '../state/store.ts';
import { saveLabels } from '../state/labels.ts';
import { WebSocketClient, defaultWsUrl, type GameClient, type InboundMessage, RecordingClient, REPLACED_CLOSE_CODE } from '../net/client.ts';
import { CommandSender } from '../net/commands.ts';
import type { InputSink, MoveIntent } from '../input/keyboard.ts';
import { findFixture } from '../fixtures/index.ts';
import { playFixture, runFixtureMessage } from '../fixtures/harness.ts';
import { CAPTURE_TARGETS, DESTROY_TARGETS, CHASSIS, WEAPONS, MAX_ORDER_MILES, navItems } from '../ui/menus.ts';
import { shouldSendCommanderMove } from '../input/move-schedule.ts';
import { isGridTransition } from '../render/interpolation.ts';
import { COL_PIECES, cursorFor, cursorTarget, moveCursor, type BuildCursor, type CursorColumn } from '../ui/construction.ts';

const SESSION_KEY = 'nether-earth.session';
/** Lcb4a: the construction cursor pauses 10 frames (50 Hz) after each move, so a held key repeats every 200 ms. */
export const CURSOR_REPEAT_MS = 200;

export class GameController implements InputSink {
  sender: CommandSender;
  /**
   * Optional UI sound sink (#272). The controller is where clicks and keys
   * converge, so it is the one place that can beep for both. It stays a
   * callback rather than a dependency: audio is presentation and the
   * controller must keep working (and testing) without it.
   */
  onUiSound: ((name: 'cursor' | 'select' | 'built') => void) | null = null;
  weaponIndex = 0;
  /** Construction-screen cursor (UI-only; resets on every new session). */
  buildCursor: BuildCursor | null = null;
  private lastCursorMoveMs = -Infinity;
  private lastCommanderMoveMs = -Infinity;
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
      onClose: (code) => this.onClosed(code),
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

  /**
   * Routes one inbound server message into the store, plus the controller's
   * own session bookkeeping. Public (not just `private`) so it doubles as
   * the test seam for driving the live-mode message flow without a real
   * socket -- see `controller.test.ts`'s solo-create round trip.
   */
  handleInbound(msg: InboundMessage): void {
    runFixtureMessage(this.store, msg, this.now());
    if (msg.type === 'resync') this.store.setConnection({ status: 'connected' });
    if (msg.type === 'created' || msg.type === 'joined') {
      const session = this.store.get().connection.session;
      if (session && this.pendingNickname) this.store.setConnection({ session: { ...session, nickname: this.pendingNickname } });
      this.persistSession();
    }
    if (msg.type === 'created' && msg.opponent === 'computer') {
      // Solo match (CR004.8): the AI seat is always ready, so the human's
      // own `setReady` is all that is left before the match starts. Send it
      // immediately so the player never sees a waiting screen or has to
      // click ready -- there is no second human to wait on or ready up.
      this.sender.ready(true);
    }
    if (msg.type === 'error' && (msg.error.code === 'invalid_session' || msg.error.code === 'session_mismatch')) {
      this.clearSession();
    }
  }

  private onClosed(code: number): void {
    if (code === REPLACED_CLOSE_CODE) {
      // A newer socket took over this session (another tab/window holds the
      // same token). Reconnecting would evict it in turn and the two would
      // evict each other forever; the player can take over with "Reconnect now".
      this.store.setConnection({
        status: 'disconnected',
        lastError: { code: 'session_replaced', message: 'Session opened in another tab or window' },
      });
      return;
    }
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
    if (this.store.get().connection.lastError?.code === 'session_replaced') this.store.setConnection({ lastError: null });
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

  /** "Play vs computer" (CR004.8): a solo match filled with the engine's AI seat. */
  playVsComputer(nickname: string): void {
    this.store.setConnection({ session: null });
    this.startLive();
    this.pendingNickname = nickname;
    this.whenOpen(() => this.sender.create(nickname, 'computer'));
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
      if (this.buildCursor !== before) {
        this.lastCursorMoveMs = now;
        this.onUiSound?.('cursor');
      }
      return;
    }
    switch (s.ui.menu) {
      case 'direct_control':
        this.sender.command({ kind: 'direct_robot_move', dx: intent.dx, dy: intent.dy });
        return;
      case 'combat':
        // There is no aiming (owner decision, 2026-09-23): a shot goes where
        // the robot faces, so the combat menu's arrows drive the robot, which
        // turns it. Turning costs ticks, so a reversal takes two nudges.
        this.sender.command({ kind: 'direct_robot_move', dx: intent.dx, dy: intent.dy });
        return;
      case 'order_distance':
        this.adjustDistance(intent.dx !== 0 ? intent.dx : -intent.dy);
        return;
      case 'robot_menu':
      case 'orders':
      case 'order_target': {
        // CR003.241: Up/Down (arrows or W/S) move the highlighted block; the
        // commander/robot must not also move while a menu list is open.
        if (intent.dy === 0) return;
        const items = navItems(s.ui.menu, s.ui.pendingOrder);
        if (!items.length) return;
        const next = ((s.ui.menuCursor + intent.dy) % items.length + items.length) % items.length;
        this.store.setUi({ menuCursor: next });
        return;
      }
      case 'none': {
        const c = myCommander(s.latest, me);
        if (c?.mode !== 'free') return;
        // CR003.5: timed so the next cell starts as soon as the engine allows.
        const now = this.now();
        const due = shouldSendCommanderMove({
          nowMs: now,
          latestTick: s.latest.tick,
          latestSnapshotAtMs: s.ui.latestSnapshotAtMs,
          transition: isGridTransition(c.horizontal_transition) ? c.horizontal_transition : null,
          lastSentMs: this.lastCommanderMoveMs,
        });
        if (!due) return;
        this.lastCommanderMoveMs = now;
        this.sender.command({ kind: 'commander_move', dx: intent.dx, dy: intent.dy });
        return;
      }
      default:
        return;
    }
  }

  /**
   * Frame-rate poll of the held direction (CR003.5). Only the free
   * commander's move is polled this often; the scheduler decides when to send.
   * Other held-key uses keep the keyboard's own pulse cadence.
   */
  pollCommanderMove(intent: MoveIntent | null): void {
    const s = this.store.get();
    if (!intent || s.ui.menu !== 'none' || !s.latest) return;
    if (myConstruction(s.latest, s.connection.session?.playerId ?? '')) return;
    this.move(intent);
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
    // CR003.241: while a menu block list is open, Space activates the
    // highlighted block instead of lifting the commander out of the robot.
    // The matching key-up must not then send a stray rising:false, unless the
    // activated block was itself LEAVE ROBOT (which does send rising:true and
    // needs its release to follow through normally, same as holding rise).
    if (rising && NAV_MENUS.has(s.ui.menu)) {
      const sentRise = this.activateMenuCursor();
      this.pendingMenuRiseSuppressed = !sentRise;
      return;
    }
    if (!rising && this.pendingMenuRiseSuppressed) {
      this.pendingMenuRiseSuppressed = false;
      return;
    }
    this.pendingMenuRiseSuppressed = false;
    this.sender.command({ kind: 'commander_set_vertical_intent', rising });
    if (rising && s.ui.menu !== 'none') this.store.setUi({ menu: 'none' });
  }

  /** True while a Space press was consumed by menu navigation, so its release sends nothing. */
  private pendingMenuRiseSuppressed = false;

  /** Space activation of the currently highlighted menu block (CR003.241). Returns whether it sent the LEAVE ROBOT rise command. */
  private activateMenuCursor(): boolean {
    const s = this.store.get();
    const item = navItems(s.ui.menu, s.ui.pendingOrder)[s.ui.menuCursor];
    if (!item) return false;
    this.menuAction(item.action, item.arg);
    return item.action === 'undock';
  }

  action(code: string): void {
    const s = this.store.get();
    if (code === 'Alt+KeyQ') {
      // Quit-match shortcut (#250): deliberately Alt-gated so it can't be hit
      // by accident during normal play. Works from anywhere in an active
      // match, not just when a menu is open.
      if (s.ui.screen === 'match') this.leave();
      return;
    }
    if (code === 'KeyG') {
      this.store.setUi({ debugGrid: !s.ui.debugGrid });
      return;
    }
    if (code === 'KeyL') {
      this.store.setUi({ labels: !s.ui.labels });
      saveLabels(!s.ui.labels);
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
    this.onUiSound?.(action === 'launch' ? 'built' : 'select');
    const s = this.store.get();
    const me = s.connection.session?.playerId ?? '';
    const snap = s.latest;
    switch (action) {
      case 'menu':
        this.store.setUi({ menu: (arg || 'none') as MenuMode, menuCursor: 0 });
        return;
      case 'pick':
        if (arg === 'advance' || arg === 'retreat') this.store.setUi({ pendingOrder: arg, menu: 'order_distance', menuCursor: 0 });
        else if (arg === 'search_capture' || arg === 'search_destroy') this.store.setUi({ pendingOrder: arg, menu: 'order_target', menuCursor: 0 });
        return;
      case 'undock':
        // The LEAVE ROBOT block is the existing rise-to-undock key (CR003.6);
        // Space activating it performs the same command as holding rise.
        this.sender.command({ kind: 'commander_set_vertical_intent', rising: true });
        this.store.setUi({ menu: 'none', menuCursor: 0 });
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
        this.store.setUi({ menu: 'none', pendingOrder: null, notice: 'order sent', menuCursor: 0 });
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
        this.sender.command({ kind: 'robot_fire', entityId: robot.entity_id, weapon });
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

}

/** Menu modes whose block list is keyboard-navigable (CR003.241: arrows/WASD move, Space activates). */
const NAV_MENUS = new Set<MenuMode>(['robot_menu', 'orders', 'order_target']);

const BACK: Record<MenuMode, MenuMode> = {
  none: 'none',
  robot_menu: 'none',
  direct_control: 'robot_menu',
  orders: 'robot_menu',
  order_distance: 'orders',
  order_target: 'orders',
  combat: 'robot_menu',
};
