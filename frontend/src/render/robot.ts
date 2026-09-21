// Robot/commander presentation from the authoritative canonical stack.
// The stack array in the snapshot is already in canonical order
// (chassis → cannon → missile → phaser → nuke → electronics); we draw it
// bottom-up and never reorder or re-derive it.
import { Graphics } from 'pixi.js';
import { drawPrism, drawDiamond } from './prism.ts';
import { colorFor, ownerColor, type SemanticAsset } from './assets.ts';

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
  drawDiamond(g, x, y, ownerColor(owner), 0.35 * alpha);
  let z = 0;
  const n = stack.length;
  // If the authoritative height differs from our visual sum, scale to match it
  // so the docked commander lands exactly on the authoritative top.
  const visualSum = stack.reduce((h, m) => h + MODULE_VISUAL_HEIGHT[m], 0) || 1;
  const scale = opts.totalHeight !== undefined ? opts.totalHeight / visualSum : 1;
  for (let i = 0; i < n; i++) {
    const m = stack[i];
    const h = MODULE_VISUAL_HEIGHT[m] * scale;
    drawPrism(g, x, y, z, h, colorFor(`module.${m}` as SemanticAsset), alpha);
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
  if (altitude > surfaceZ) drawDiamond(g, x, y, 0x000000, 0.35, undefined, surfaceZ);
  drawPrism(g, x, y, altitude, height, colorFor('commander'));
  // ownership pennant on top
  drawPrism(g, x, y, altitude + height, 1, ownerColor(owner));
}
