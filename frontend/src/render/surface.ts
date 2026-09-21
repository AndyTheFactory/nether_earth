// Surface height under a footprint (CR002.15), for drawing shadows where
// they land: on open ground, a structure or scenery block, or the heli-pad
// roof. Reads static map data plus the snapshot's destruction list only;
// presentation, never collision or legality.
import type { MapData } from '../world/map.ts';

/** Destroyed structures are drawn (and cast shadows onto) low rubble of this height. */
export const RUBBLE_HEIGHT = 1;

interface Column {
  structureId: string;
  height: number;
}

/** Inclusive cell range covered by a `size`-wide footprint centred on `c` (size 0 = a point). */
export function footprintRange(c: number, size: number): [number, number] {
  const eps = 1e-9;
  const lo = Math.floor(c - size / 2 + 0.5 + eps);
  const hi = Math.ceil(c + size / 2 - 0.5 - eps);
  return [lo, Math.max(lo, hi)];
}

export class SurfaceMap {
  private readonly cells = new Map<string, Column[]>();

  constructor(map: MapData) {
    for (const s of [...map.war_bases, ...map.factories, ...map.blockers]) {
      for (const c of s.components) {
        const k = `${c.x},${c.y}`;
        const col = this.cells.get(k) ?? [];
        col.push({ structureId: s.id, height: c.height });
        this.cells.set(k, col);
      }
    }
  }

  /** Top of whatever stands on cell (x, y); 0 on open ground. */
  heightAt(x: number, y: number, destroyed: ReadonlySet<string> = new Set()): number {
    let top = 0;
    for (const c of this.cells.get(`${x},${y}`) ?? []) top = Math.max(top, destroyed.has(c.structureId) ? RUBBLE_HEIGHT : c.height);
    return top;
  }

  /**
   * Highest surface under a `size`×`size` footprint centred on (x, y);
   * fractional positions (mid-move) cover every cell they overlap.
   * size 0 samples the single cell containing the point (projectiles).
   */
  under(x: number, y: number, destroyed: ReadonlySet<string> = new Set(), size = 1): number {
    const [x0, x1] = footprintRange(x, size);
    const [y0, y1] = footprintRange(y, size);
    let top = 0;
    for (let cy = y0; cy <= y1; cy++) for (let cx = x0; cx <= x1; cx++) top = Math.max(top, this.heightAt(cx, cy, destroyed));
    return top;
  }
}
