// The one 2.5D block primitive every layer draws with: a cell-sized prism of
// `height` authoritative units standing on cell (x, y) at base elevation z0.
// Cell (x, y) covers [x-0.5, x+0.5] × [y-0.5, y+0.5]; corners go through
// project(), so the faces follow whatever orientation projection.ts defines.
// `size` > 1 draws a size×size block anchored at (x, y) as its min-x/max-y
// cell (the engine's 2×2 unit convention, CR002.3/4): it covers cells
// x..x+size-1 and y-size+1..y.
import { Graphics, Sprite, type Texture } from 'pixi.js';
import { project, type ScreenPoint } from './projection.ts';
import { shade } from './assets.ts';

type Corners = [ScreenPoint, ScreenPoint, ScreenPoint, ScreenPoint];

/** Block corners at height z, in order (-x,-y), (+x,-y), (+x,+y), (-x,+y). */
function corners(x: number, y: number, z: number, size = 1): Corners {
  const x0 = x - 0.5;
  const x1 = x - 0.5 + size;
  const y0 = y + 0.5 - size;
  const y1 = y + 0.5;
  return [project(x0, y0, z), project(x1, y0, z), project(x1, y1, z), project(x0, y1, z)];
}

const flat = (pts: ScreenPoint[]): number[] => pts.flatMap((p) => [p.x, p.y]);

export function drawPrism(g: Graphics, x: number, y: number, z0: number, height: number, color: number, alpha = 1, size = 1): void {
  const [tA, tB, tC, tD] = corners(x, y, z0 + height, size);
  if (height > 0) {
    const [bA, , bC, bD] = corners(x, y, z0, size);
    // -x face (left, facing the lower-left viewer)
    g.poly(flat([tA, tD, bD, bA])).fill({ color: shade(color, 0.4), alpha });
    // +y face (front)
    g.poly(flat([tD, tC, bC, bD])).fill({ color: shade(color, 0.6), alpha });
  }
  g.poly(flat([tA, tB, tC, tD])).fill({ color, alpha });
}

/** Flat cell (or size×size block) tile at height z (ground tiles, shadows, markers). */
export function drawDiamond(g: Graphics, x: number, y: number, color: number, alpha = 1, stroke?: number, z = 0, size = 1): void {
  g.poly(flat(corners(x, y, z, size))).fill({ color, alpha });
  if (stroke !== undefined) g.stroke({ color: stroke, width: 1, alpha: 0.6 });
}

/** Sprite placement for a real texture standing on a cell, if one is loaded. */
export function placeSprite(tex: Texture, x: number, y: number, z: number): Sprite {
  const s = new Sprite(tex);
  const p = project(x, y, z);
  const bottom = Math.max(...corners(x, y, z).map((c) => c.y));
  s.anchor.set(0.5, 1);
  s.position.set(p.x, bottom);
  return s;
}
