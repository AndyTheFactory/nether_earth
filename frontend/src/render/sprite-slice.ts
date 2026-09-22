// Generic per-footprint-cell sprite slicing (CR002.5, extended to robots,
// commanders and structure walls by owner request 2026-09-22). Factored out
// of scenery.ts so every category that draws a decoded Spectrum sprite over
// an N×M footprint (scenery blockers, robot pieces, the commander, war-base/
// factory wall segments) shares one algorithm instead of four copies.
import { Texture } from 'pixi.js';
import { project, unproject } from './projection.ts';

export interface SpriteSlice {
  /** Footprint cell offset from the anchor (dx >= 0, dy <= 0). */
  dx: number;
  dy: number;
  /** Same size as the sprite; ' ' where another cell owns the pixel. */
  rows: string[];
}

/**
 * World-space top-left of a sprite for a footprint anchored at (x, y),
 * standing at elevation `z` (authoritative units; 0 = ground). The Spectrum
 * draws every sprite this way (`Lcf2d_draw_sprite_to_buffer`): column 0 sits
 * on the footprint's leftmost ground corner and the last row on its lowest
 * ground corner (pixel centres on the corners), lifted by its elevation.
 * `offset` shifts the result further (presentation-only, e.g. CR003.7).
 */
export function spriteOriginFor(
  rows: readonly string[],
  footprint: readonly [number, number],
  anchor: { x: number; y: number },
  z = 0,
  offset: readonly [number, number] = [0, 0],
): { x: number; y: number } {
  const left = project(anchor.x - 0.5, anchor.y - footprint[1] + 0.5, z);
  const bottom = project(anchor.x - 0.5, anchor.y + 0.5, z);
  return { x: left.x - 0.5 + offset[0], y: bottom.y - rows.length + 0.5 + offset[1] };
}

/**
 * Split a sprite into one slice per footprint cell so it can be drawn one
 * cell at a time and share the shared painter's-order key with every other
 * per-cell body (structures, scenery, robots). A pixel belongs to the cell
 * whose visible surface it shows: the highest point of the footprint's solid
 * (0..height) on that pixel's view line. Depends only on the sprite/footprint/
 * height, not on where the body stands, so callers cache the result once per
 * asset (see `sceneryTexturesFor` / the per-module cache in robot.ts).
 */
export function sliceSpriteRows(rows: readonly string[], footprint: readonly [number, number], height: number): SpriteSlice[] {
  const [fw, fh] = footprint;
  const o = spriteOriginFor(rows, footprint, { x: 0, y: 0 });
  const cells: [number, number][] = [];
  for (let dy = 0; dy > -fh; dy--) for (let dx = 0; dx < fw; dx++) cells.push([dx, dy]);
  const out = cells.map(() => rows.map((r) => [...r].map(() => ' ')));
  const zTop = Math.min(height, rows.length);
  rows.forEach((row, r) => {
    for (let c = 0; c < row.length; c++) {
      if (row[c] === ' ') continue;
      const sx = o.x + c + 0.5;
      const sy = o.y + r + 0.5;
      let owner = -1;
      let nearest = 0;
      let nearestD = Infinity;
      for (let z = zTop; z >= 0 && owner < 0; z -= 0.25) {
        const g = unproject(sx, sy + z);
        cells.forEach(([dx, dy], i) => {
          const d = Math.max(Math.abs(g.x - dx), Math.abs(g.y - dy));
          if (d <= 0.5 && owner < 0) owner = i;
          if (d < nearestD) [nearestD, nearest] = [d, i];
        });
      }
      out[owner < 0 ? nearest : owner]![r]![c] = row[c]!;
    }
  });
  return cells.map(([dx, dy], i) => ({ dx, dy, rows: out[i]!.map((r) => r.join('')) }));
}

/** Nearest-filtered texture of a '#'/'.'/' ' pixel sprite (world units are Spectrum pixels). */
export function pixelTexture(rows: readonly string[], ink: number, paper: number): Texture {
  const w = rows[0]?.length ?? 0;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, w);
  canvas.height = Math.max(1, rows.length);
  const ctx = canvas.getContext('2d')!;
  const img = ctx.createImageData(canvas.width, canvas.height);
  rows.forEach((row, r) => {
    for (let c = 0; c < row.length; c++) {
      if (row[c] === ' ') continue;
      const col = row[c] === '#' ? ink : paper;
      const i = (r * canvas.width + c) * 4;
      img.data.set([(col >> 16) & 0xff, (col >> 8) & 0xff, col & 0xff, 255], i);
    }
  });
  ctx.putImageData(img, 0, 0);
  const tex = Texture.from(canvas);
  tex.source.scaleMode = 'nearest';
  return tex;
}
