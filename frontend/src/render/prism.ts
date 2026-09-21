// The one 2.5D block primitive every layer draws with: a cell-sized prism of
// `height` authoritative units standing on cell (x, y) at base elevation z0.
import { Graphics, Sprite, type Texture } from 'pixi.js';
import { CELL_H, CELL_W, Z_PX, project } from './projection.ts';
import { shade } from './assets.ts';

export function drawPrism(g: Graphics, x: number, y: number, z0: number, height: number, color: number, alpha = 1): void {
  const hw = CELL_W / 2;
  const hh = CELL_H / 2;
  const top = project(x, y, z0 + height);
  const base = project(x, y, z0);
  const hpx = height * Z_PX;
  // top diamond
  g.poly([top.x, top.y - hh, top.x + hw, top.y, top.x, top.y + hh, top.x - hw, top.y]).fill({ color, alpha });
  if (hpx > 0) {
    // left face
    g.poly([top.x - hw, top.y, top.x, top.y + hh, top.x, base.y + hh, top.x - hw, base.y]).fill({ color: shade(color, 0.6), alpha });
    // right face
    g.poly([top.x, top.y + hh, top.x + hw, top.y, top.x + hw, base.y, top.x, base.y + hh]).fill({ color: shade(color, 0.4), alpha });
  }
}

export function drawDiamond(g: Graphics, x: number, y: number, color: number, alpha = 1, stroke?: number, z = 0): void {
  const hw = CELL_W / 2;
  const hh = CELL_H / 2;
  const p = project(x, y, z);
  g.poly([p.x, p.y - hh, p.x + hw, p.y, p.x, p.y + hh, p.x - hw, p.y]).fill({ color, alpha });
  if (stroke !== undefined) g.stroke({ color: stroke, width: 1, alpha: 0.6 });
}

/** Sprite placement for a real texture standing on a cell, if one is loaded. */
export function placeSprite(tex: Texture, x: number, y: number, z: number): Sprite {
  const s = new Sprite(tex);
  const p = project(x, y, z);
  s.anchor.set(0.5, 1);
  s.position.set(p.x, p.y + CELL_H / 2);
  return s;
}
