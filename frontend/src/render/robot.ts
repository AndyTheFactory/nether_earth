// Robot/commander presentation from the authoritative canonical stack.
// The stack array in the snapshot is already in canonical order
// (chassis → cannon → missile → phaser → nuke → electronics); we draw it
// bottom-up and never reorder or re-derive it.
import { Graphics } from 'pixi.js';
import { drawPrism, drawDiamond } from './prism.ts';
import { colorFor, ownerColor, type SemanticAsset } from './assets.ts';

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

export function drawRobotStack(g: Graphics, x: number, y: number, stack: readonly ModuleId[], owner: string | null, opts: { alpha?: number; totalHeight?: number } = {}): number {
  const alpha = opts.alpha ?? 1;
  drawDiamond(g, x, y, ownerColor(owner), 0.35 * alpha, undefined, 0, UNIT_SIZE);
  let z = 0;
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
