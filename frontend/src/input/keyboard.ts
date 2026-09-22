// Keyboard → explicit command intent (M8.5/M8.7). Pure state machine over
// key events; DOM binding lives in bindKeyboard(). Legality is never checked:
// every press yields a command the engine may accept or ignore.

export type Axis = -1 | 0 | 1;

export interface MoveIntent {
  dx: Axis;
  dy: Axis;
}

// World axes, not screen axes. In the Spectrum orientation (projection.ts)
// +x reads as screen-right and -y as screen-up, so each key moves along the
// world axis closest to its on-screen direction (checked in projection.test.ts).
export const KEY_TO_AXIS: Readonly<Record<string, MoveIntent>> = {
  ArrowUp: { dx: 0, dy: -1 },
  ArrowDown: { dx: 0, dy: 1 },
  ArrowLeft: { dx: -1, dy: 0 },
  ArrowRight: { dx: 1, dy: 0 },
  KeyW: { dx: 0, dy: -1 },
  KeyS: { dx: 0, dy: 1 },
  KeyA: { dx: -1, dy: 0 },
  KeyD: { dx: 1, dy: 0 },
};

export const RISE_KEY = 'Space';

/** Quit-match shortcut: Alt+Q, chosen to be hard to hit by accident (Alt alone has no
 * movement/menu meaning here, and KeyQ is otherwise unbound). See issue #250. */
export const QUIT_KEY = 'KeyQ';
export const QUIT_ACTION = 'Alt+KeyQ';

export interface InputSink {
  /** Horizontal intent for the free commander or direct-controlled robot. */
  move(intent: MoveIntent): void;
  /** Rise/descend intent; called once per edge, never on key repeat. */
  vertical(rising: boolean): void;
  /** Menu/action keys the UI layer interprets (Enter, Escape, digits...). */
  action(code: string): void;
}

/**
 * Tracks held movement keys and emits one move per tick-cadence call
 * (`pulse()`), plus rise/descend edges. Focus loss releases everything.
 */
export class KeyboardIntent {
  private held = new Set<string>();
  private rising = false;

  constructor(private readonly sink: InputSink) {}

  keyDown(code: string, repeat: boolean, altKey = false): boolean {
    if (altKey && code === QUIT_KEY) {
      if (!repeat) this.sink.action(QUIT_ACTION);
      return false;
    }
    if (code === RISE_KEY) {
      if (!repeat && !this.rising) {
        this.rising = true;
        this.sink.vertical(true);
      }
      return true;
    }
    if (code in KEY_TO_AXIS) {
      if (!this.held.has(code)) {
        this.held.add(code);
        this.sink.move(this.current());
      }
      return true;
    }
    if (!repeat) this.sink.action(code);
    // Handled (and its default suppressed) same as the axis/rise keys above.
    // Without this, a key routed to action() (Enter above all: it opens the
    // menu and confirms/fires within it) left the browser's own default
    // key behavior in place, and since clicking any menu <button> gives it
    // DOM focus, a later Enter press natively re-activated that *stale*
    // focused button (independent of, and racing with, our own routing) —
    // e.g. clicking DIRECT CONTROL then later pressing Enter to reopen the
    // list silently jumped back into DIRECT CONTROL. Verified live against
    // Chromium (#247): reproducible every time a button has focus.
    return true;
  }

  keyUp(code: string): void {
    if (code === RISE_KEY) {
      if (this.rising) {
        this.rising = false;
        this.sink.vertical(false);
      }
      return;
    }
    this.held.delete(code);
  }

  /** Re-emit the held direction; called on a fixed cadence while keys are held. */
  pulse(): void {
    if (this.held.size) this.sink.move(this.current());
  }

  /** The held direction, or null when no movement key is down. */
  heldIntent(): MoveIntent | null {
    return this.held.size ? this.current() : null;
  }

  /** window blur / visibility change: stop everything, no stuck intent. */
  releaseAll(): void {
    this.held.clear();
    if (this.rising) {
      this.rising = false;
      this.sink.vertical(false);
    }
  }

  get isRising(): boolean {
    return this.rising;
  }

  current(): MoveIntent {
    let dx = 0;
    let dy = 0;
    for (const code of this.held) {
      const a = KEY_TO_AXIS[code];
      dx += a.dx;
      dy += a.dy;
    }
    // The engine accepts one axis per move (cardinal); prefer the latest axis
    // pressed by letting a non-zero x cancel y only when both are set.
    const cx = Math.sign(dx) as Axis;
    const cy = Math.sign(dy) as Axis;
    if (cx !== 0 && cy !== 0) return { dx: cx, dy: 0 };
    return { dx: cx, dy: cy };
  }
}

export function bindKeyboard(target: Window, intent: KeyboardIntent, pulseMs = 50): () => void {
  const down = (e: KeyboardEvent) => {
    if (isTypingTarget(e.target)) return;
    // Leave OS/browser-reserved combos (Ctrl+1 tab-switch, Cmd+C copy, …)
    // alone; every game key is a bare keypress with no modifier, except the
    // Alt+Q quit shortcut (#250), which needs altKey routed through.
    if (e.ctrlKey || e.metaKey) return;
    if (e.altKey && e.code !== QUIT_KEY) return;
    if (intent.keyDown(e.code, e.repeat, e.altKey)) e.preventDefault();
  };
  const up = (e: KeyboardEvent) => {
    if (isTypingTarget(e.target)) return;
    intent.keyUp(e.code);
  };
  const blur = () => intent.releaseAll();
  const vis = () => {
    if (document.hidden) intent.releaseAll();
  };
  target.addEventListener('keydown', down);
  target.addEventListener('keyup', up);
  target.addEventListener('blur', blur);
  document.addEventListener('visibilitychange', vis);
  const timer = setInterval(() => intent.pulse(), pulseMs);
  return () => {
    target.removeEventListener('keydown', down);
    target.removeEventListener('keyup', up);
    target.removeEventListener('blur', blur);
    document.removeEventListener('visibilitychange', vis);
    clearInterval(timer);
  };
}

function isTypingTarget(t: EventTarget | null): boolean {
  return t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement;
}
