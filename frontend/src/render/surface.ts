// Surface height under a footprint (CR002.15), for drawing shadows where
// they land: on open ground, a terrain piece, a structure or scenery block,
// or the heli-pad roof. Reads static map data plus the snapshot's
// destruction/debris lists only; presentation, never collision or legality.
// Terrain pieces carry the map's `Ld7bc_map_piece_heights` height (rough
// 2/3, mountain 6) and nuclear debris the rough piece height (CR002.21), the
// same data the engine's `collision.surface_height_at` reads.
import type { MapData } from '../world/map.ts';

/**
 * Presentation-only exaggeration of ground heights (terrain pieces and
 * debris) when drawing what stands or flies over them (CR005.2, owner
 * request): a robot on a 6-high mountain is drawn 18 px up instead of 6, so
 * the climb reads. Buildings and scenery boxes are unaffected; everything
 * drawn over the ground (robots, the commander, bullets, shadows) is raised
 * by the same `lift`, so they stay consistent with each other.
 */
export const GROUND_LIFT = 3;

interface Column {
  structureId: string;
  height: number;
  /** Height once `structureId` is gone: rough debris (CR002.18, CR005.3). */
  goneHeight: number;
}

/** Key a robot-debris cell (snapshot `robot_debris`) takes in a `destroyed` set. */
const debrisCellKey = (x: number, y: number): string => `@${x},${y}`;

/**
 * Everything that no longer stands as built: destroyed buildings, debris
 * blockers and the 2×2 debris robots killed in combat left (CR005.3).
 */
export function goneSet(snap: {
  structure_destruction: readonly string[];
  scenery_debris: readonly string[];
  robot_debris?: readonly { x: number; y: number }[];
}): Set<string> {
  const gone = new Set([...snap.structure_destruction, ...snap.scenery_debris]);
  for (const d of snap.robot_debris ?? []) {
    for (const [dx, dy] of [[0, 0], [1, 0], [0, -1], [1, -1]]) gone.add(debrisCellKey(d.x + dx, d.y + dy));
  }
  return gone;
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
  private readonly debrisHeight: number;

  constructor(map: MapData) {
    this.debrisHeight = map.terrain.debris_height;
    const add = (x: number, y: number, column: Column) => {
      const k = `${x},${y}`;
      this.cells.set(k, [...(this.cells.get(k) ?? []), column]);
    };
    for (const c of map.terrain.cells) {
      if (c.height) add(c.x, c.y, { structureId: '', height: c.height, goneHeight: c.height });
    }
    for (const s of [...map.war_bases, ...map.factories]) {
      for (const c of s.components) add(c.x, c.y, { structureId: s.id, height: c.height, goneHeight: map.terrain.debris_height });
    }
    for (const b of map.blockers) {
      for (const c of b.components) add(c.x, c.y, { structureId: b.id, height: c.height, goneHeight: map.terrain.debris_height });
    }
  }

  /**
   * Top of whatever stands on cell (x, y); 0 on open ground. `destroyed` is
   * a `goneSet`. With `groundOnly`, only terrain pieces and debris count.
   */
  heightAt(x: number, y: number, destroyed: ReadonlySet<string> = new Set(), groundOnly = false): number {
    let top = destroyed.has(debrisCellKey(x, y)) ? this.debrisHeight : 0;
    for (const c of this.cells.get(`${x},${y}`) ?? []) {
      const gone = destroyed.has(c.structureId);
      if (groundOnly && c.structureId && !gone) continue;
      top = Math.max(top, gone ? c.goneHeight : c.height);
    }
    return top;
  }

  /** Extra drawing elevation over cell (x, y): the ground under it, exaggerated (GROUND_LIFT). */
  liftAt(x: number, y: number, destroyed: ReadonlySet<string> = new Set()): number {
    return (GROUND_LIFT - 1) * this.heightAt(x, y, destroyed, true);
  }

  /** `liftAt` over the 2×2 unit body anchored at (x, y), like `underUnit`. */
  liftUnit(x: number, y: number, destroyed: ReadonlySet<string> = new Set()): number {
    return (GROUND_LIFT - 1) * this.under(x + 0.5, y - 0.5, destroyed, 2, true);
  }

  /**
   * Highest surface under a `size`×`size` footprint centred on (x, y);
   * fractional positions (mid-move) cover every cell they overlap.
   * size 0 samples the single cell containing the point (projectiles).
   */
  under(x: number, y: number, destroyed: ReadonlySet<string> = new Set(), size = 1, groundOnly = false): number {
    const [x0, x1] = footprintRange(x, size);
    const [y0, y1] = footprintRange(y, size);
    let top = 0;
    for (let cy = y0; cy <= y1; cy++) for (let cx = x0; cx <= x1; cx++) top = Math.max(top, this.heightAt(cx, cy, destroyed, groundOnly));
    return top;
  }

  /**
   * Highest surface under the 2×2 unit body anchored at (x, y) (CR002.3/4):
   * columns x..x+1, rows y-1..y; fractional anchors (mid-move) widen it.
   */
  underUnit(x: number, y: number, destroyed: ReadonlySet<string> = new Set()): number {
    return this.under(x + 0.5, y - 0.5, destroyed, 2);
  }
}
