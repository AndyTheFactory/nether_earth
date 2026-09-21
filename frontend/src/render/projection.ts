// World→screen 2.5D projection (M8.2, CR002.7). Defined once; every layer uses it.
//
// Spectrum orientation (_specs/milestones/cr002/main-screen.png): the ground
// grid is a top-down square grid rotated so the map's long +x axis runs from
// lower-left to upper-right (2 px right per 1 px up) and +y runs steeply
// down-right (1 px right per 2 px down); height lifts straight up. The viewer
// stands at the lower-left, so the visible side faces of a block are its -x
// face (left) and its +y face (front).
//
// Units are Spectrum pixels (world space); the renderer scales the world to
// screen pixels by viewZoom(). They encode presentation, never gameplay.

export interface ScreenPoint {
  x: number;
  y: number;
}

/** Screen offset of one cell step along +x (up-right). */
export const AXIS_X: ScreenPoint = { x: 8, y: -4 };
/** Screen offset of one cell step along +y (down-right). */
export const AXIS_Y: ScreenPoint = { x: 4, y: 8 };
/** Pixels per authoritative height unit: the reference shows 15-unit blocks ~15 px tall. */
export const Z_PX = 1;
/** Screen bounding box of one ground cell. */
export const TILE_W = Math.abs(AXIS_X.x) + Math.abs(AXIS_Y.x);
export const TILE_H = Math.abs(AXIS_X.y) + Math.abs(AXIS_Y.y);

const DET = AXIS_X.x * AXIS_Y.y - AXIS_Y.x * AXIS_X.y;

/** Project a world cell (fractional allowed) at height z to screen space. */
export function project(x: number, y: number, z = 0): ScreenPoint {
  return {
    x: x * AXIS_X.x + y * AXIS_Y.x,
    y: x * AXIS_X.y + y * AXIS_Y.y - z * Z_PX,
  };
}

/** Inverse of project() at z=0 (fractional world coordinates). */
export function unproject(sx: number, sy: number): { x: number; y: number } {
  return {
    x: (sx * AXIS_Y.y - sy * AXIS_Y.x) / DET,
    y: (sy * AXIS_X.x - sx * AXIS_X.y) / DET,
  };
}

/** Ground-plane distance toward the viewer: the screen y of (x, y) at z=0. */
export function groundDepth(x: number, y: number): number {
  return x * AXIS_X.y + y * AXIS_Y.y;
}

/**
 * Stable painter's-order key shared by structures, scenery and entities:
 * whatever stands nearer the viewer (lower x, higher y — lower on screen)
 * draws later and so covers what is behind it; at the same ground point,
 * higher things draw later. Ties break on x so siblings are deterministic.
 */
export function depthKey(x: number, y: number, z = 0): number {
  return groundDepth(x, y) * 1000 + z - x * 0.001;
}

/**
 * Zoom (CR002.8): Spectrum pixels of world shown across the shorter side of
 * the play view. The original's play window is ~168 px square (main-screen.png),
 * about 19 cells along the map and its full 16-cell width. The one tunable.
 */
export const VIEW_SPAN_PX = 168;

/** Screen pixels per world pixel for a play view of the given size. */
export function viewZoom(width: number, height: number): number {
  return Math.max(1, Math.min(width, height) / VIEW_SPAN_PX);
}
