// Robot/commander presentation from the authoritative canonical stack.
// The stack array in the snapshot is already in canonical order
// (chassis → cannon → missile → phaser → nuke → electronics); we draw it
// bottom-up and never reorder or re-derive it.
import { Graphics } from 'pixi.js';
import { drawPrism, drawDiamond } from './prism.ts';
import { colorFor, ownerColor, type SemanticAsset } from './assets.ts';
import { interpolateAltitude, type GridTransition } from './interpolation.ts';
import type { SurfaceMap } from './surface.ts';

/**
 * Robots and commanders are 2×2 bodies (CR002.3/4). A snapshot's (x, y) is
 * the body's anchor: its min-x/max-y cell, the one nearest the viewer. The
 * body covers columns x..x+1 and rows y-1..y (engine `occupancy.py`,
 * radar `Ld65a_flip_2x2_radar_area`).
 */
export const UNIT_SIZE = 2;

/** Ground-plane centre of the 2×2 body anchored at (x, y), for labels, effects and the camera. */
export function unitCentre(x: number, y: number): { x: number; y: number } {
  return { x: x + (UNIT_SIZE - 1) / 2, y: y - (UNIT_SIZE - 1) / 2 };
}

export type ModuleId = 'bipod' | 'tracks' | 'anti_grav' | 'cannon' | 'missile' | 'phaser' | 'nuclear' | 'electronics';

/** Visual module heights mirror the engine defaults (chassis 4, others 2); used only for drawing. */
export const MODULE_VISUAL_HEIGHT: Record<ModuleId, number> = {
  bipod: 4,
  tracks: 4,
  anti_grav: 4,
  cannon: 2,
  missile: 2,
  phaser: 2,
  nuclear: 2,
  electronics: 2,
};

/**
 * Elevation a robot stands at (CR002.25): the highest map piece under its
 * 2×2 body, the Spectrum's ROBOT_STRUCT_ALTITUDE (`Lb5d6_map_altitude_2x2`,
 * stored by `Lb495`), which `Lcee8_draw_robot_to_buffer` draws the robot at.
 * At rest it is the engine's own value (`collision.unit_surface_height` at the
 * authoritative anchor), read from the same map heights, so the robot's top
 * is exactly where the engine docks and lands the commander. Mid-move it is
 * blended from the origin body's surface to the destination's over the move,
 * like the position; presentation only.
 */
export function robotGround(
  surface: Pick<SurfaceMap, 'underUnit'>,
  x: number,
  y: number,
  move: GridTransition | null,
  tick: number,
  destroyed: ReadonlySet<string> = new Set(),
): number {
  if (!move) return surface.underUnit(x, y, destroyed);
  const from = surface.underUnit(move.from_x, move.from_y, destroyed);
  const to = surface.underUnit(move.to_x, move.to_y, destroyed);
  return interpolateAltitude(from, { from_altitude: from, to_altitude: to, started_tick: move.started_tick, duration_ticks: move.duration_ticks }, tick);
}

/** Draws the stack standing at elevation `opts.ground` (default 0); returns its top. */
export function drawRobotStack(g: Graphics, x: number, y: number, stack: readonly ModuleId[], owner: string | null, opts: { alpha?: number; totalHeight?: number; ground?: number } = {}): number {
  const alpha = opts.alpha ?? 1;
  const ground = opts.ground ?? 0;
  drawDiamond(g, x, y, ownerColor(owner), 0.35 * alpha, undefined, ground, UNIT_SIZE);
  let z = ground;
  const n = stack.length;
  // If the authoritative height differs from our visual sum, scale to match it
  // so the docked commander lands exactly on the authoritative top.
  const visualSum = stack.reduce((h, m) => h + MODULE_VISUAL_HEIGHT[m], 0) || 1;
  const scale = opts.totalHeight !== undefined ? opts.totalHeight / visualSum : 1;
  for (let i = 0; i < n; i++) {
    const m = stack[i];
    const h = MODULE_VISUAL_HEIGHT[m] * scale;
    drawPrism(g, x, y, z, h, colorFor(`module.${m}` as SemanticAsset), alpha, UNIT_SIZE);
    z += h;
  }
  return z;
}

/**
 * `surfaceZ` is the top of whatever lies under the commander (ground,
 * structure roof, heli-pad; see surface.ts): its shadow is drawn there so
 * altitude reads against the surface. No shadow when resting on it.
 */
export function drawCommander(g: Graphics, x: number, y: number, altitude: number, owner: string, surfaceZ = 0, height = 4): void {
  if (altitude > surfaceZ) drawDiamond(g, x, y, 0x000000, 0.35, undefined, surfaceZ, UNIT_SIZE);
  drawPrism(g, x, y, altitude, height, colorFor('commander'), 1, UNIT_SIZE);
  // ownership pennant on top
  drawPrism(g, x, y, altitude + height, 1, ownerColor(owner), 1, UNIT_SIZE);
}
