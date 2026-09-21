// Spectrum RADAR strip (CR002.11): the whole map at a glance, drawn from the
// authoritative snapshot and static map data only. It derives nothing about
// gameplay; it just places marks where the snapshot says things are.
import type { SnapshotState } from '../../../protocol/generated/types';
import type { MapData } from '../world/map.ts';
import { ownerColor, PALETTE } from '../render/assets.ts';

/**
 * Map cells per radar pixel horizontally, and radar pixels per map row. The
 * Spectrum radar draws one pixel per cell (a factory is a 6x4 block, a war
 * base 10x8), so the whole 512x16 map is a 512x16 strip.
 */
export const RADAR_CELLS_PER_PX = 1;
export const RADAR_PX_PER_ROW = 1;

export interface RadarMark {
  x: number;
  y: number;
  w: number;
  h: number;
  color: number;
}

/** Visible map-column range of the play view, inclusive; null when unknown. */
export interface RadarView {
  minX: number;
  maxX: number;
}

export function radarSize(map: MapData): { width: number; height: number } {
  return { width: Math.ceil(map.width / RADAR_CELLS_PER_PX), height: map.height * RADAR_PX_PER_ROW };
}

const DESTROYED = 0x555555;

function cellRect(x: number, y: number, color: number, wCells = 1, hRows = 1): RadarMark {
  const px = Math.floor(x / RADAR_CELLS_PER_PX);
  return { x: px, y: y * RADAR_PX_PER_ROW, w: Math.max(1, Math.ceil((x + wCells) / RADAR_CELLS_PER_PX) - px), h: hRows * RADAR_PX_PER_ROW, color };
}

/**
 * Marks in draw order (later marks overwrite earlier ones): blockers, war
 * bases and factories coloured by owner, robots by owner, commanders, then
 * the view-window edges.
 */
export function radarMarks(map: MapData, snap: SnapshotState | null, view: RadarView | null): RadarMark[] {
  const marks: RadarMark[] = [];
  const owner = (id: string) => snap?.structure_ownership.find((o) => o.structure_id === id)?.owner ?? null;
  const destroyed = new Set(snap?.structure_destruction ?? []);
  for (const b of map.blockers) for (const c of b.components) marks.push(cellRect(c.x, c.y, PALETTE.white));
  for (const s of [...map.war_bases, ...map.factories]) {
    const o = owner(s.id);
    const color = destroyed.has(s.id) ? DESTROYED : o ? ownerColor(o) : PALETTE.brightWhite;
    for (const c of s.components) marks.push(cellRect(c.x, c.y, color));
  }
  if (snap) {
    // Robots read as 2x2 marks, as on the Spectrum radar, drawn from their snapshot cell.
    for (const r of snap.robots) marks.push(cellRect(r.x, r.y, ownerColor(r.owner), 2, 2));
    for (const c of snap.commanders) {
      // A plus sign, so the commander reads apart from the robots.
      const m = cellRect(c.x, c.y, ownerColor(c.player_id));
      marks.push({ ...m, x: m.x - 1, w: 3 });
      marks.push({ ...m, y: m.y - 1, h: m.h + 2 });
    }
  }
  if (view) {
    const { width, height } = radarSize(map);
    const clamp = (v: number) => Math.min(width - 1, Math.max(0, v));
    const left = clamp(Math.floor(view.minX / RADAR_CELLS_PER_PX));
    const right = clamp(Math.floor(view.maxX / RADAR_CELLS_PER_PX));
    marks.push({ x: left, y: 0, w: 1, h: height, color: PALETTE.brightWhite });
    if (right !== left) marks.push({ x: right, y: 0, w: 1, h: height, color: PALETTE.brightWhite });
  }
  return marks;
}

/** Column range covered by the given play-view corner cells (see WorldRenderer.screenToCell). */
export function viewFromCorners(corners: { x: number; y: number }[]): RadarView | null {
  if (!corners.length) return null;
  const xs = corners.map((c) => c.x).filter(Number.isFinite);
  if (!xs.length) return null;
  return { minX: Math.min(...xs), maxX: Math.max(...xs) };
}

const hex = (c: number) => `#${c.toString(16).padStart(6, '0')}`;

/** DOM strip: a "RADAR" label and a pixel canvas redrawn only when its inputs change. */
export class Radar {
  readonly root: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private lastSnap: SnapshotState | null | undefined = undefined;
  private lastView = '';

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
    const scale = Math.max(1, Math.min(4, Math.floor((window.innerWidth - 48) / (this.canvas.width + labelWidth))));
    const root = document.documentElement.style;
    root.setProperty('--rs', String(scale));
    root.setProperty('--radar-h', `${this.canvas.height * scale}px`);
  }

  update(visible: boolean, snap: SnapshotState | null, view: RadarView | null): void {
    this.root.style.display = visible ? '' : 'none';
    if (!visible) return;
    const viewKey = view ? `${Math.floor(view.minX / RADAR_CELLS_PER_PX)},${Math.floor(view.maxX / RADAR_CELLS_PER_PX)}` : '';
    if (snap === this.lastSnap && viewKey === this.lastView) return;
    this.lastSnap = snap;
    this.lastView = viewKey;
    const g = this.ctx;
    g.fillStyle = '#000000';
    g.fillRect(0, 0, this.canvas.width, this.canvas.height);
    for (const m of radarMarks(this.map, snap, view)) {
      g.fillStyle = hex(m.color);
      g.fillRect(m.x, m.y, m.w, m.h);
    }
  }
}
