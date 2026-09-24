// Robot/commander presentation from the authoritative canonical stack.
// The stack array in the snapshot is already in canonical order
// (chassis → cannon → missile → phaser → nuke → electronics); we draw it
// bottom-up and never reorder or re-derive it.
//
// Owner-directed extension (2026-09-22): pieces and the commander are drawn
// with the decoded Spectrum sprites (robot-sprites.ts / commander-sprites.ts,
// see frontend/scripts/decode-unit-sprites.py) instead of procedural prisms,
// sliced per footprint cell the same way scenery is (sprite-slice.ts). Each
// sprite's silhouette is ink=white/paper=mid-grey so PixiJS `tint` recolors
// the whole piece to the owner's colour in one draw call, matching the
// Spectrum's single per-player screen attribute (it has no per-module
// colour; the previous placeholder's per-module rainbow was our own
// invention and is dropped along with the prisms it decorated).
import { Container, Graphics, Sprite, type Texture } from 'pixi.js';
import { drawDiamond } from './prism.ts';
import { ownerColor } from './assets.ts';
import { interpolateAltitude, type GridTransition } from './interpolation.ts';
import type { SurfaceMap } from './surface.ts';
import { depthKey } from './projection.ts';
import { pixelTexture, sliceSpriteRows, spriteOriginFor, type SpriteSlice } from './sprite-slice.ts';
import { ROBOT_SPRITES } from './robot-sprites.ts';
import { COMMANDER_SPRITES } from './commander-sprites.ts';

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

/**
 * The 2×2 body's own footprint cells (x..x+1, y-1..y; CR002.3/4), so a robot
 * or commander can be drawn one cell at a time and share the structures'/
 * scenery's per-cell painter's-order key (#242): a single anchor-only key
 * puts the wrong body in front when two 2×2 bodies partly overlap.
 */
export function unitFootprintCells(x: number, y: number): { x: number; y: number }[] {
  const cells: { x: number; y: number }[] = [];
  for (let dx = 0; dx < UNIT_SIZE; dx++) for (let dy = 0; dy < UNIT_SIZE; dy++) cells.push({ x: x + dx, y: y - dy });
  return cells;
}

export type ModuleId = 'bipod' | 'tracks' | 'anti_grav' | 'cannon' | 'missile' | 'phaser' | 'nuclear' | 'electronics';

/**
 * The cardinal direction a robot's body faces, as the snapshot's `facing`
 * field spells it (owner request, 2026-09-23). Each piece has its own sprite
 * per facing (`Ld6c8_piece_direction_graphic_indices`); several pieces reuse
 * one sprite across two or four facings, which is the original's own table.
 * Declared here rather than imported because the protocol generator emits
 * this union inline on the robot snapshot instead of as a named type.
 */
export type RobotFacing = 'east' | 'west' | 'south' | 'north';

/** The facing a robot without one falls back to: south, the launch facing (`La6c8`). */
export const DEFAULT_FACING: RobotFacing = 'south';

/**
 * Visual module heights: the Spectrum's `Ld7b4_piece_heights`, the same values
 * as the engine's `EngineRules.module_height_*` (CR003.3). Drawing only: the
 * stack is still scaled to the snapshot's authoritative `height`.
 */
export const MODULE_VISUAL_HEIGHT: Record<ModuleId, number> = {
  bipod: 11,
  tracks: 7,
  anti_grav: 8,
  cannon: 6,
  missile: 6,
  phaser: 7,
  nuclear: 7,
  electronics: 7,
};

const SPRITE_INK = 0xffffff;
const SPRITE_PAPER = 0xaaaaaa;
const FOOTPRINT: readonly [number, number] = [UNIT_SIZE, UNIT_SIZE];

interface UnitSliceTexture {
  slice: SpriteSlice;
  texture: Texture;
}

const moduleSliceCache = new Map<string, UnitSliceTexture[]>();

/**
 * Per-footprint-cell slices of a module's sprite for one facing, built once
 * (cached like scenery's `sceneryTexturesFor`). Keyed per (module, facing)
 * now that each piece has four sprites: a cache keyed by module alone would
 * hand back the first-drawn facing's textures for every later one.
 */
function moduleSlices(m: ModuleId, facing: RobotFacing): UnitSliceTexture[] {
  const key = `${m}:${facing}`;
  let s = moduleSliceCache.get(key);
  if (!s) {
    s = sliceSpriteRows(ROBOT_SPRITES[m][facing], FOOTPRINT, MODULE_VISUAL_HEIGHT[m]).map((slice) => ({
      slice,
      texture: pixelTexture(slice.rows, SPRITE_INK, SPRITE_PAPER),
    }));
    moduleSliceCache.set(key, s);
  }
  return s;
}

/** Key of COMMANDER_SPRITES (single frame, no facing: `Lcd83_render_player`). */
const COMMANDER_SPRITE_ID = 'spectrum.commander';
let commanderSliceCache: UnitSliceTexture[] | null = null;

function commanderSlices(): UnitSliceTexture[] {
  if (!commanderSliceCache) {
    const rows = COMMANDER_SPRITES[COMMANDER_SPRITE_ID]!;
    commanderSliceCache = sliceSpriteRows(rows, FOOTPRINT, rows.length).map((slice) => ({
      slice,
      texture: pixelTexture(slice.rows, SPRITE_INK, SPRITE_PAPER),
    }));
  }
  return commanderSliceCache;
}

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

/**
 * Sliced, owner-tinted Sprites (plus the ground shadow diamond) for a 2×2
 * body's stack, anchored at (x, y) and standing at elevation `opts.ground`
 * (default 0). Each object is already positioned and `zIndex`-ed for the
 * shared scene painter's order (CR002.14); the caller only adds them.
 * Returns the stack's top elevation too (ground + total height).
 */
export function drawRobotStack(
  x: number,
  y: number,
  stack: readonly ModuleId[],
  owner: string | null,
  opts: { alpha?: number; totalHeight?: number; ground?: number; facing?: RobotFacing } = {},
  getPiece: (index: number, textured: boolean) => Container = () => new Graphics(),
): number {
  const alpha = opts.alpha ?? 1;
  const ground = opts.ground ?? 0;
  const facing = opts.facing ?? DEFAULT_FACING;
  let idx = 0;

  const shadow = getPiece(idx++, false) as Graphics;
  if ('clear' in shadow) shadow.clear();
  drawDiamond(shadow, x, y, ownerColor(owner), 0.35 * alpha, undefined, ground, UNIT_SIZE);
  shadow.zIndex = depthKey(x, y, ground);

  let z = ground;
  // If the authoritative height differs from our visual sum, scale to match it
  // so the docked commander lands exactly on the authoritative top. The
  // sprite's own pixel size never stretches (it would smear the pixel art);
  // only the elevation each piece stacks at is rescaled.
  const visualSum = stack.reduce((h, m) => h + MODULE_VISUAL_HEIGHT[m], 0) || 1;
  const scale = opts.totalHeight !== undefined ? opts.totalHeight / visualSum : 1;
  const tint = ownerColor(owner);
  for (const m of stack) {
    const h = MODULE_VISUAL_HEIGHT[m] * scale;
    const origin = spriteOriginFor(ROBOT_SPRITES[m][facing], FOOTPRINT, { x, y }, z);
    for (const { slice, texture } of moduleSlices(m, facing)) {
      const s = getPiece(idx++, true) as Sprite;
      s.texture = texture;
      s.position.set(origin.x, origin.y);
      s.tint = tint;
      s.alpha = alpha;
      s.zIndex = depthKey(x + slice.dx, y + slice.dy, z);
    }
    z += h;
  }
  return z;
}

/**
 * `surfaceZ` is the top of whatever lies under the commander (ground,
 * structure roof, heli-pad; see surface.ts): its shadow is drawn there so
 * altitude reads against the surface. No shadow when resting on it. Returns
 * the sliced, owner-tinted commander Sprites plus the shadow diamond,
 * positioned and `zIndex`-ed for the scene.
 */
export function drawCommander(
  x: number,
  y: number,
  altitude: number,
  owner: string,
  surfaceZ = 0,
  zBias = 0,
  getPiece: (index: number, textured: boolean) => Container = () => new Graphics(),
): void {
  // The body slices take the leading indices and the (airborne-only) shadow
  // the trailing one, so a given index always asks for the same kind of
  // object. Drawing the shadow first instead made index 0 flip Graphics <->
  // Sprite on every take-off and landing; a pool keyed by index then had to
  // swap the object under a key that stayed in use, which left the old one
  // painted at the lift-off cell (a frozen commander "trace").
  let idx = 0;
  const rows = COMMANDER_SPRITES[COMMANDER_SPRITE_ID]!;
  const origin = spriteOriginFor(rows, FOOTPRINT, { x, y }, altitude);
  const tint = ownerColor(owner);
  for (const { slice, texture } of commanderSlices()) {
    const s = getPiece(idx++, true) as Sprite;
    s.texture = texture;
    s.position.set(origin.x, origin.y);
    s.tint = tint;
    s.zIndex = depthKey(x + slice.dx, y + slice.dy, altitude) + zBias;
  }
  if (altitude > surfaceZ) {
    const shadow = getPiece(idx++, false) as Graphics;
    if ('clear' in shadow) shadow.clear();
    drawDiamond(shadow, x, y, 0x000000, 0.35, undefined, surfaceZ, UNIT_SIZE);
    shadow.zIndex = depthKey(x, y, surfaceZ) + zBias;
  }
}
