// Full-screen ROBOT CONSTRUCTION screen (CR002.9), laid out after the
// original's screen (_specs/milestones/cr002/construction-screen.png) and its
// code (netherearth-disassembly Lc85d/Lca0f/Lcb00/Lcb52/Lcb8e/Lcc1f).
//
// Everything shown comes from the authoritative snapshot (the session build
// and resource buffer) or from engine rules data (module costs, exported from
// rules.py by scripts/generate-rules.mjs). The screen never decides whether a
// module can be added or a robot started: the cursor only chooses which
// existing command to send (select/deselect_module, launch_robot,
// cancel_construction) and the engine accepts or rejects it.
import type { SnapshotState } from '../../../protocol/generated/types';
import rulesData from '../generated/rules/construction.json';
import { moduleIcon, ICON_SIZE, Bitmap } from './construction-icons.ts';
import { MODULE_VISUAL_HEIGHT } from '../render/robot.ts';

export type ModuleName = 'bipod' | 'tracks' | 'anti_grav' | 'cannon' | 'missile' | 'phaser' | 'nuclear' | 'electronics';
type Session = SnapshotState['construction_sessions'][number];

/** Spectrum piece order (Lcaf0_piece_costs): cursor piece index 0 = bipod … 7 = electronics. */
export const PIECES: readonly ModuleName[] = ['bipod', 'tracks', 'anti_grav', 'cannon', 'missile', 'phaser', 'nuclear', 'electronics'];

export const PIECE_LABEL: Readonly<Record<ModuleName, string>> = {
  electronics: 'ELECTRONICS',
  nuclear: 'NUCLEAR',
  phaser: 'PHASERS',
  missile: 'MISSILES',
  cannon: 'CANNON',
  anti_grav: 'ANTI-GRAV',
  tracks: 'TRACKS',
  bipod: 'BIPOD',
};

/** Engine rules data (EngineRules.module_cost_*), never a UI constant. */
export const MODULE_COSTS: Readonly<Record<ModuleName, number>> = rulesData.module_costs;

/** Canonical bottom-to-top stack order (engine robot_stack): chassis, cannon, missile, phaser, nuclear, electronics. */
const STACK_ORDER: readonly ModuleName[] = ['bipod', 'tracks', 'anti_grav', 'cannon', 'missile', 'phaser', 'nuclear', 'electronics'];

// ---- cursor (Lfd1f_cursor_position) ----

export const COL_EXIT = 0;
export const COL_START = 1;
export const COL_PIECES = 2;
export type CursorColumn = typeof COL_EXIT | typeof COL_START | typeof COL_PIECES;

/** UI-only cursor, tied to one session by its entry tick so each visit starts fresh. */
export interface BuildCursor {
  entryTick: number;
  column: CursorColumn;
  /** Piece index into PIECES; kept while the cursor visits EXIT/START, as on the Spectrum. */
  piece: number;
}

/** The Spectrum starts every visit on the pieces column, first piece (BIPOD). */
export function cursorFor(cursor: BuildCursor | null, entryTick: number): BuildCursor {
  return cursor && cursor.entryTick === entryTick ? cursor : { entryTick, column: COL_PIECES, piece: 0 };
}

/**
 * Lcb00: left/right walk the three columns (EXIT MENU, START ROBOT, pieces);
 * up/down walk the pieces only while on the pieces column. A move that would
 * leave the screen is ignored. `dy` is screen-down positive.
 */
export function moveCursor(c: BuildCursor, dx: number, dy: number): BuildCursor {
  if (dx !== 0) {
    const column = c.column + Math.sign(dx);
    return column < COL_EXIT || column > COL_PIECES ? c : { ...c, column: column as CursorColumn };
  }
  if (dy === 0 || c.column !== COL_PIECES) return c;
  const piece = c.piece - Math.sign(dy); // electronics (7) is at the top of the list
  return piece < 0 || piece >= PIECES.length ? c : { ...c, piece };
}

export type CursorTarget = { kind: 'exit' } | { kind: 'start' } | { kind: 'piece'; module: ModuleName };

/** What "fire" acts on at the cursor (Lca0f). */
export function cursorTarget(c: BuildCursor): CursorTarget {
  if (c.column === COL_EXIT) return { kind: 'exit' };
  if (c.column === COL_START) return { kind: 'start' };
  return { kind: 'piece', module: PIECES[c.piece] };
}

// ---- view model ----

export interface ConstructionView {
  /** RESOURCES AVAILABLE rows: the session buffer, in the Spectrum order. */
  resources: { label: string; value: number }[];
  total: number;
  /** Piece list top to bottom, as on screen. */
  pieces: { module: ModuleName; label: string; cost: number; selected: boolean; cursor: boolean }[];
  exitCursor: boolean;
  startCursor: boolean;
  /** Modules of the build in progress, bottom to top. */
  stack: ModuleName[];
}

interface Buffer {
  general?: number;
  category?: Record<string, number>;
}
interface Build {
  chassis?: string | null;
  weapons?: string[];
  electronics?: string | null;
}

export function selectedModules(cs: Session): Set<string> {
  const b = cs.build as Build;
  return new Set([b.chassis, ...(b.weapons ?? []), b.electronics].filter((m): m is string => !!m));
}

export function constructionView(cs: Session, cursor: BuildCursor): ConstructionView {
  const buf = cs.buffer as Buffer;
  const cat = buf.category ?? {};
  const resources = [
    { label: 'GENERAL', value: buf.general ?? 0 },
    { label: 'ELECTRONICS', value: cat.electronics ?? 0 },
    { label: 'NUCLEAR', value: cat.nuclear ?? 0 },
    { label: 'PHASERS', value: cat.phaser ?? 0 },
    { label: 'MISSILES', value: cat.missile ?? 0 },
    { label: 'CANNON', value: cat.cannon ?? 0 },
    { label: 'CHASSIS', value: cat.chassis ?? 0 },
  ];
  const selected = selectedModules(cs);
  const pieces = [...PIECES].reverse().map((m) => ({
    module: m,
    label: PIECE_LABEL[m],
    cost: MODULE_COSTS[m],
    selected: selected.has(m),
    cursor: cursor.column === COL_PIECES && PIECES[cursor.piece] === m,
  }));
  return {
    resources,
    total: resources.reduce((t, r) => t + r.value, 0),
    pieces,
    exitCursor: cursor.column === COL_EXIT,
    startCursor: cursor.column === COL_START,
    stack: STACK_ORDER.filter((m) => selected.has(m)),
  };
}

// ---- layout ----
//
// Positions are in Spectrum pixels measured from the reference screenshot
// (2 screenshot px per Spectrum px vertically): text rows are 8 px apart,
// the left block starts at x=49, the piece list at x=249 with its icons at
// x=204, one piece every 24 px. SCREEN_W x SCREEN_H is the content box
// (reference minus its empty margin), scaled by an integer factor so the
// 8x8 font stays crisp.

const ORIGIN_X = 32;
const ORIGIN_Y = 32;
export const SCREEN_W = 320;
export const SCREEN_H = 208;
const LEFT = 49;
const TOP = 40;
const col = (c: number) => LEFT + 8 * c;
const row = (r: number) => TOP + 8 * r;
const RESOURCE_ROWS = [9, 11, 12, 13, 14, 15, 16];
const VALUE_END_COL = 15; // values right-aligned to end at column 14
const LABEL_END_COL = 11; // labels right-aligned to end at column 10
const PIECE_ICON_X = 204;
const PIECE_NAME_COL = 25;
const PREVIEW = { x: 40, bottom: 224, w: 24, h: 72 };

export function screenScale(viewW: number, viewH: number): number {
  return Math.max(1, Math.floor(Math.min(viewW / SCREEN_W, viewH / SCREEN_H)));
}

const INK_WHITE = '#ffffff';
const INK_YELLOW = '#ffff00';

function paint(canvas: HTMLCanvasElement, bm: Bitmap, ink: string): void {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = ink;
  for (let y = 0; y < bm.h; y++) for (let x = 0; x < bm.w; x++) if (bm.get(x, y)) ctx.fillRect(x, y, 1, 1);
}

/** Bottom-up stack preview (Lcc1f): each piece hides what is behind it, as a solid sprite would. */
export function previewBitmap(stack: readonly ModuleName[]): Bitmap {
  const out = new Bitmap(PREVIEW.w, PREVIEW.h);
  let base = PREVIEW.h; // y just below the next piece
  for (const m of stack) {
    const icon = moduleIcon(m);
    const top = base - ICON_SIZE;
    const dx = Math.floor((PREVIEW.w - ICON_SIZE) / 2);
    for (let y = 0; y < ICON_SIZE; y++) {
      let min = -1;
      let max = -1;
      for (let x = 0; x < ICON_SIZE; x++) {
        if (!icon.get(x, y)) continue;
        if (min < 0) min = x;
        max = x;
      }
      if (min < 0) continue;
      for (let x = min; x <= max; x++) out.set(x + dx, y + top, icon.get(x, y));
    }
    base -= MODULE_VISUAL_HEIGHT[m] * 3;
  }
  return out;
}

type Pick = (column: CursorColumn, piece: number) => void;

export class ConstructionScreen {
  readonly root: HTMLElement;
  private readonly screen: HTMLElement;
  private readonly values: HTMLElement[] = [];
  private readonly total: HTMLElement;
  private readonly exit: HTMLElement;
  private readonly start: HTMLElement;
  private readonly rows: { name: HTMLElement; icon: HTMLCanvasElement; ink: string }[] = [];
  private readonly preview: HTMLCanvasElement;
  private lastKey = '';
  private scale = 0;

  constructor(onPick: Pick) {
    this.root = document.createElement('div');
    this.root.id = 'construction';
    this.root.style.display = 'none';
    this.screen = document.createElement('div');
    this.screen.className = 'cs-screen';
    this.root.appendChild(this.screen);

    this.text('ROBOT', col(3), row(0), 'cs-title cs-wide');
    this.text('CONSTRUCTION', col(2), row(2), 'cs-title');
    this.text('-- RESOURCES --', col(0), row(6), 'cs-head');
    this.text('-- AVAILABLE --', col(0), row(7), 'cs-head');
    const labels = ['GENERAL', 'ELECTRONICS', 'NUCLEAR', 'PHASERS', 'MISSILES', 'CANNON', 'CHASSIS'];
    labels.forEach((l, i) => {
      this.text(l, col(LABEL_END_COL - l.length), row(RESOURCE_ROWS[i]), 'cs-label');
      this.values.push(this.text('', col(VALUE_END_COL), row(RESOURCE_ROWS[i]), 'cs-value'));
    });
    this.text('TOTAL', col(LABEL_END_COL - 5), row(18), 'cs-label');
    this.total = this.text('', col(VALUE_END_COL), row(18), 'cs-value');
    this.exit = this.text('EXIT\nMENU', col(5), row(21), 'cs-option');
    this.start = this.text('START\nROBOT', col(10), row(21), 'cs-option');
    this.exit.addEventListener('click', () => onPick(COL_EXIT, 0));
    this.start.addEventListener('click', () => onPick(COL_START, 0));

    [...PIECES].reverse().forEach((m, i) => {
      const hit = this.box('cs-piece', PIECE_ICON_X, row(3 * i), col(PIECE_NAME_COL) + 8 * 11 - PIECE_ICON_X, 24);
      hit.dataset.module = m;
      hit.addEventListener('click', () => onPick(COL_PIECES, PIECES.indexOf(m)));
      const icon = this.canvas(ICON_SIZE, ICON_SIZE, PIECE_ICON_X, row(3 * i));
      const name = this.text(PIECE_LABEL[m], col(PIECE_NAME_COL), row(3 * i + 1), 'cs-name');
      this.text(String(MODULE_COSTS[m]), col(PIECE_NAME_COL + 1), row(3 * i + 2), 'cs-cost');
      this.rows.push({ name, icon, ink: '' });
    });
    this.preview = this.canvas(PREVIEW.w, PREVIEW.h, PREVIEW.x, PREVIEW.bottom - PREVIEW.h);
  }

  private place(e: HTMLElement, x: number, y: number): void {
    e.style.left = `calc(var(--u) * ${x - ORIGIN_X})`;
    e.style.top = `calc(var(--u) * ${y - ORIGIN_Y})`;
  }

  private text(s: string, x: number, y: number, cls: string): HTMLElement {
    const e = document.createElement('div');
    e.className = `cs-text ${cls}`;
    e.textContent = s;
    this.place(e, x, y);
    this.screen.appendChild(e);
    return e;
  }

  private box(cls: string, x: number, y: number, w: number, h: number): HTMLElement {
    const e = document.createElement('div');
    e.className = cls;
    this.place(e, x, y);
    e.style.width = `calc(var(--u) * ${w})`;
    e.style.height = `calc(var(--u) * ${h})`;
    this.screen.appendChild(e);
    return e;
  }

  private canvas(w: number, h: number, x: number, y: number): HTMLCanvasElement {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    c.className = 'cs-sprite';
    this.place(c, x, y);
    c.style.width = `calc(var(--u) * ${w})`;
    c.style.height = `calc(var(--u) * ${h})`;
    this.screen.appendChild(c);
    return c;
  }

  /** Show the screen for `cs` (or hide it when null); cheap when nothing changed. */
  update(cs: Session | null, cursor: BuildCursor | null, viewW: number, viewH: number): void {
    if (!cs) {
      if (this.lastKey !== '') {
        this.root.style.display = 'none';
        this.lastKey = '';
      }
      return;
    }
    const scale = screenScale(viewW, viewH);
    if (scale !== this.scale) {
      this.scale = scale;
      this.root.style.setProperty('--u', `${scale}px`);
    }
    const view = constructionView(cs, cursorFor(cursor, cs.entry_tick));
    const key = JSON.stringify(view);
    if (key === this.lastKey) return;
    this.lastKey = key;
    this.root.style.display = '';
    view.resources.forEach((r, i) => setRightAligned(this.values[i], String(r.value)));
    setRightAligned(this.total, String(view.total));
    this.exit.classList.toggle('cursor', view.exitCursor);
    this.start.classList.toggle('cursor', view.startCursor);
    view.pieces.forEach((p, i) => {
      const r = this.rows[i];
      r.name.classList.toggle('cursor', p.cursor);
      // Lcc1f: fitted pieces are drawn white, the others yellow.
      const ink = p.selected ? INK_WHITE : INK_YELLOW;
      if (ink !== r.ink) {
        r.ink = ink;
        paint(r.icon, moduleIcon(p.module), ink);
      }
    });
    paint(this.preview, previewBitmap(view.stack), INK_WHITE);
  }
}

/** Numbers end at a fixed column, like the Spectrum's right-aligned counters. */
function setRightAligned(e: HTMLElement, s: string): void {
  e.textContent = s;
  e.style.transform = `translateX(calc(var(--u) * ${-8 * s.length}))`;
}
