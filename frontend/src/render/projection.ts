// World→screen 2.5D projection (M8.2). Defined once; every layer uses it.
// Classic 2:1 dimetric view: +x runs down-right, +y runs down-left, z lifts up.
// Numbers here are pixels; they encode presentation, never gameplay.

export const CELL_W = 32; // full diamond width per cell
export const CELL_H = 16; // full diamond height per cell
export const Z_PX = 4; // pixels per authoritative height unit

export interface ScreenPoint {
  x: number;
  y: number;
}

/** Project a world cell (fractional allowed) at height z to screen space. */
export function project(x: number, y: number, z = 0): ScreenPoint {
  return {
    x: (x - y) * (CELL_W / 2),
    y: (x + y) * (CELL_H / 2) - z * Z_PX,
  };
}

/** Inverse of project() at z=0; used for debug picking only. */
export function unproject(sx: number, sy: number): { x: number; y: number } {
  const a = sx / (CELL_W / 2);
  const b = sy / (CELL_H / 2);
  return { x: (a + b) / 2, y: (b - a) / 2 };
}

/**
 * Stable painter's-order key: objects further "down" the screen (larger x+y)
 * draw later; taller bases at the same cell draw after ground. Ties break on
 * x so equal-depth siblings are deterministic.
 */
export function depthKey(x: number, y: number, z = 0): number {
  return (x + y) * 1000 + z + x * 0.001;
}
