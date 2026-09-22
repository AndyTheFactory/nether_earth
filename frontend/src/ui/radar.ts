// Spectrum RADAR strip (CR002.11, CR002.22): a 128-column window of the map
// at one pixel per cell, scrolled in 64-column steps to follow the local
// commander, all marks white. Drawn from the authoritative snapshot and static
// map data only; it derives nothing about gameplay.
//
// Disassembly (santiontanon/netherearth-disassembly):
// - Ld59e_draw_radar: a 16-byte x 16-row bitmap, i.e. 128 x 16 pixels.
// - Ld5f8_update_radar_buffers: a pixel is set where the map element is >= 15
//   (buildings, scenery boxes, fences); debris and terrain stay clear.
// - Ld65a_flip_2x2_radar_area / Ld67d_get_radar_view_pointer: robots and the
//   player XOR a 2x2 area, columns x..x+1 and rows y-1..y, only when
//   x - scroll is in [0, 126].
// - Lafe6_radar_scroll: scroll is a multiple of 64 columns, starts at 0, and
//   moves one 64-column step when the player's x/8 tile is < 2 or >= 14 tiles
//   into the window, never below 0 or past width - 128.
// - Lb048_update_radar: the player's 2x2 flips every game cycle, so it blinks.
// - There is no view-window indicator.
import type { SnapshotState } from '../../../protocol/generated/types';
import type { MapData } from '../world/map.ts';
import { PALETTE } from '../render/assets.ts';

/** Map columns shown at once (16 bytes of 8 pixels). */
export const RADAR_COLUMNS = 128;
/** Scroll step in columns (8 tiles of 8 cells). */
export const RADAR_SCROLL_STEP = 64;
/** The window scrolls when the commander is within this many columns of an edge. */
const EDGE_TILES_LEFT = 2;
const EDGE_TILES_RIGHT = 14;
/** Commander blink half-period (visual only; the Spectrum flips it every game cycle). */
export const RADAR_BLINK_MS = 250;
export const RADAR_COLOR = PALETTE.brightWhite;

export interface RadarMark {
  x: number;
  y: number;
  w: number;
  h: number;
  color: number;
}

export function radarSize(map: MapData): { width: number; height: number } {
  return { width: Math.min(RADAR_COLUMNS, map.width), height: map.height };
}

/**
 * Next radar scroll (first visible column) for a commander at column `x`,
 * applying Lafe6_radar_scroll's 64-column steps until the commander is off
 * the edge bands (repeated so a jump, e.g. on reconnect, settles at once).
 */
export function radarScroll(scroll: number, x: number, mapWidth: number): number {
  const max = Math.max(0, mapWidth - RADAR_COLUMNS);
  for (;;) {
    const t = Math.floor(x / 8) - scroll / 8;
    if (t < EDGE_TILES_LEFT && scroll - RADAR_SCROLL_STEP >= 0) scroll -= RADAR_SCROLL_STEP;
    else if (t >= EDGE_TILES_RIGHT && scroll + RADAR_SCROLL_STEP <= max) scroll += RADAR_SCROLL_STEP;
    else return scroll;
  }
}

/** Cell the local commander marks on the radar (its docked robot's cell when docked), or null. */
export function commanderCell(snap: SnapshotState | null, me: string | null): { x: number; y: number } | null {
  const c = snap?.commanders.find((k) => k.player_id === me);
  if (!c) return null;
  const docked = c.mode === 'docked' && c.docked_robot_id ? snap!.robots.find((r) => r.entity_id === c.docked_robot_id) : undefined;
  return docked ? { x: docked.x, y: docked.y } : { x: c.x, y: c.y };
}

/**
 * The radar bitmap (row-major, width x height, 1 = lit) for the window
 * starting at column `scroll`. `commanderLit` is the blink phase of the local
 * commander's mark.
 */
export function radarBitmap(map: MapData, snap: SnapshotState | null, scroll: number, me: string | null, commanderLit: boolean): Uint8Array {
  const { width, height } = radarSize(map);
  const bits = new Uint8Array(width * height);
  const inside = (x: number, y: number) => x >= 0 && x < width && y >= 0 && y < height;
  const set = (x: number, y: number) => {
    if (inside(x - scroll, y)) bits[y * width + x - scroll] = 1;
  };
  const flip2x2 = (x: number, y: number) => {
    const dx = x - scroll;
    if (dx < 0 || dx > width - 2) return;
    for (const [cx, cy] of [[dx, y], [dx + 1, y], [dx, y - 1], [dx + 1, y - 1]]) {
      if (inside(cx, cy)) bits[cy * width + cx] ^= 1;
    }
  };

  const destroyed = new Set(snap?.structure_destruction ?? []);
  // Nuclear debris (CR002.18) and destroyed structures become rough ground: no mark.
  const debris = new Set(snap?.scenery_debris ?? []);
  for (const b of map.blockers) if (!debris.has(b.id)) for (const c of b.components) set(c.x, c.y);
  for (const s of [...map.war_bases, ...map.factories]) if (!destroyed.has(s.id)) for (const c of s.components) set(c.x, c.y);
  if (snap) {
    for (const r of snap.robots) flip2x2(r.x, r.y);
    const c = commanderCell(snap, me);
    if (c && commanderLit) flip2x2(c.x, c.y);
  }
  return bits;
}

/** Horizontal runs of lit pixels, all in the radar colour. */
export function radarMarks(map: MapData, snap: SnapshotState | null, scroll: number, me: string | null, commanderLit = true): RadarMark[] {
  const { width, height } = radarSize(map);
  const bits = radarBitmap(map, snap, scroll, me, commanderLit);
  const marks: RadarMark[] = [];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (!bits[y * width + x]) continue;
      const x0 = x;
      while (x + 1 < width && bits[y * width + x + 1]) x++;
      marks.push({ x: x0, y, w: x - x0 + 1, h: 1, color: RADAR_COLOR });
    }
  }
  return marks;
}

const hex = (c: number) => `#${c.toString(16).padStart(6, '0')}`;

/** DOM strip: a "RADAR" label and a pixel canvas redrawn only when its inputs change. */
/** Width of the right-hand robot menu column plus its margins, in Spectrum pixels (--mu); see style.css #menus. */
export const MENU_COLUMN_UNITS = 96;

/** Screen pixels the menu column reserves at the current --mu (style.css breakpoints). */
export function menuColumnPx(): number {
  const mu = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mu')) || 0;
  return MENU_COLUMN_UNITS * mu;
}

/** Largest integer radar scale (1-4) whose strip fits in `availablePx` beside the page margins. */
export function radarScale(availablePx: number, stripWidth: number): number {
  return Math.max(1, Math.min(4, Math.floor((availablePx - 48) / stripWidth)));
}

export class Radar {
  readonly root: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private lastSnap: SnapshotState | null | undefined = undefined;
  private lastKey = '';
  private scroll = 0;

  constructor(private readonly map: MapData) {
    this.root = document.createElement('div');
    this.root.id = 'radar';
    this.root.className = 'panel';
    this.root.style.display = 'none';
    const label = document.createElement('span');
    label.className = 'radar-label';
    label.textContent = 'RADAR';
    this.canvas = document.createElement('canvas');
    const { width, height } = radarSize(map);
    this.canvas.width = width;
    this.canvas.height = height;
    this.ctx = this.canvas.getContext('2d')!;
    this.root.append(label, this.canvas);
    this.fit();
    window.addEventListener('resize', () => this.fit());
  }

  /** Integer pixel scale so radar pixels stay square and crisp; the label uses the same pixel. */
  private fit(): void {
    // "RADAR" in the tall font sized to the strip height: 5 glyphs, each half as wide as tall.
    const labelWidth = 5 * 0.5 * this.canvas.height + 8;
    // The docked robot menu column (CR003.6) keeps the right edge; the radar stays left of it.
    const scale = radarScale(window.innerWidth - menuColumnPx(), this.canvas.width + labelWidth);
    const root = document.documentElement.style;
    root.setProperty('--rs', String(scale));
    root.setProperty('--radar-h', `${this.canvas.height * scale}px`);
  }

  update(visible: boolean, snap: SnapshotState | null, me: string | null, nowMs: number): void {
    this.root.style.display = visible ? '' : 'none';
    if (!visible) return;
    const c = commanderCell(snap, me);
    this.scroll = snap ? (c ? radarScroll(this.scroll, c.x, this.map.width) : this.scroll) : 0;
    const lit = Math.floor(nowMs / RADAR_BLINK_MS) % 2 === 0;
    const key = `${this.scroll},${lit},${me}`;
    if (snap === this.lastSnap && key === this.lastKey) return;
    this.lastSnap = snap;
    this.lastKey = key;
    const g = this.ctx;
    g.fillStyle = '#000000';
    g.fillRect(0, 0, this.canvas.width, this.canvas.height);
    for (const m of radarMarks(this.map, snap, this.scroll, me, lit)) {
      g.fillStyle = hex(m.color);
      g.fillRect(m.x, m.y, m.w, m.h);
    }
  }
}
