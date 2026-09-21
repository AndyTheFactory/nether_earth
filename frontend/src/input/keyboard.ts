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

  keyDown(code: string, repeat: boolean): boolean {
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
    return false;
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
    if (intent.keyDown(e.code, e.repeat)) e.preventDefault();
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
