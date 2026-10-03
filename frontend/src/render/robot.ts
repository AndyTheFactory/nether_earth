// Robot/commander presentation from the authoritative canonical stack.
// The stack array in the snapshot is already in canonical order
// (chassis → cannon → missile → phaser → nuke → electronics); we draw it
// bottom-up and never reorder or re-derive it.
//
// Owner-directed extension (2026-09-22): pieces and the commander are drawn
// with the decoded Spectrum sprites (robot-sprites.ts / commander-sprites.ts,
// see frontend/scripts/decode-unit-sprites.py) instead of procedural prisms,
// sliced per footprint cell the same way scenery is (sprite-slice.ts). Each
// sprite's silhouette is ink=black/paper=white (owner request, 2026-09-24):
// PixiJS `tint` multiplies, so the black lines survive any tint and only the
// paper takes the unit's colour, in one draw call. That colour is brightness,
// not hue -- bright white for the viewing player's units, the Spectrum's
// plain (non-bright) white for the enemy's, see `unitFill` -- matching the
// Spectrum's single per-player screen attribute (it has no per-module
// colour; the previous placeholder's per-module rainbow was our own
// invention and is dropped along with the prisms it decorated).
import { Container, Graphics, Sprite, type Texture } from 'pixi.js';
import { PALETTE, unitFill } from './assets.ts';
import { interpolateAltitude, type GridTransition } from './interpolation.ts';
import type { SurfaceMap } from './surface.ts';
import { depthKey, project } from './projection.ts';
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

/** The snapshot fields the destroyed-robot blink reads. */
type BlinkingRobot = { entity_id: string; blink?: { visible: boolean } };

/**
 * Whether a robot is drawn this frame. A destroyed robot blinks for four game
 * cycles before it is removed (owner decision 2026-10-03): the Spectrum draws
 * a map cell's object only while its mark is set (`Lcd18_draw_map_cell`),
 * and `Lb0fa_robot_update` toggles that mark once per cycle. The engine
 * decides the phase; this only reads `blink.visible`.
 */
export function robotDrawn(robot: BlinkingRobot): boolean {
  return robot.blink?.visible ?? true;
}

/**
 * Robots that died between two snapshots, for the explosion effect: those
 * that started blinking, and those that vanished without blinking (a nuclear
 * blast removes robots outright). A blinking robot's later removal is not a
 * second death.
 */
export function robotDeaths<R extends BlinkingRobot>(previous: readonly R[], next: readonly R[]): R[] {
  const now = new Map(next.map((r) => [r.entity_id, r]));
  return previous.filter((was) => {
    if (was.blink) return false;
    const r = now.get(was.entity_id);
    return !r || !!r.blink;
  });
}

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

const SPRITE_INK = PALETTE.black;
const SPRITE_PAPER = PALETTE.brightWhite;
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
 * like the position; presentation only. The ground under it is drawn
 * exaggerated (`GROUND_LIFT`, CR005.2), so the returned elevation is a
 * drawing elevation, not the engine altitude.
 */
export function robotGround(
  surface: Pick<SurfaceMap, 'underUnit' | 'liftUnit'>,
  x: number,
  y: number,
  move: GridTransition | null,
  tick: number,
  destroyed: ReadonlySet<string> = new Set(),
): number {
  const at = (ax: number, ay: number) => surface.underUnit(ax, ay, destroyed) + surface.liftUnit(ax, ay, destroyed);
  if (!move) return at(x, y);
  const from = at(move.from_x, move.from_y);
  const to = at(move.to_x, move.to_y);
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
  opts: { alpha?: number; totalHeight?: number; ground?: number; facing?: RobotFacing; mine?: boolean } = {},
  getPiece: (index: number, textured: boolean) => Container = () => new Graphics(),
): number {
  const alpha = opts.alpha ?? 1;
  const ground = opts.ground ?? 0;
  const facing = opts.facing ?? DEFAULT_FACING;
  // Brightness, not hue: see `unitFill`.
  const mine = opts.mine ?? true;
  let idx = 0;

  // A robot always rests on its surface, so it gets no ground shadow -- the
  // commander's only appears while it is airborne. This used to be an
  // owner-coloured diamond (cyan/magenta) under every robot's feet, which
  // read as part of the unit rather than as ground (owner request,
  // 2026-09-25). The slot itself stays: the pool `getPiece` draws from is
  // keyed by index, so index 0 must keep asking for a Graphics or it would
  // flip Graphics <-> Sprite under a live key (see `drawCommander`).
  const shadow = getPiece(idx++, false) as Graphics;
  if ('clear' in shadow) shadow.clear();
  shadow.zIndex = depthKey(x, y, ground);

  let z = ground;
  // If the authoritative height differs from our visual sum, scale to match it
  // so the docked commander lands exactly on the authoritative top. The
  // sprite's own pixel size never stretches (it would smear the pixel art);
  // only the elevation each piece stacks at is rescaled.
  const visualSum = stack.reduce((h, m) => h + MODULE_VISUAL_HEIGHT[m], 0) || 1;
  const scale = opts.totalHeight !== undefined ? opts.totalHeight / visualSum : 1;
  const tint = unitFill(mine);
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
 * altitude reads against the surface. Given per cell, the shadow is cut
 * along cell edges and each part lies on the surface of the cell it covers
 * (CR005.4), so over uneven ground it falls on each cell below the body
 * rather than floating at the highest one. No shadow on a cell the
 * commander rests on or below. Returns
 * the sliced, owner-tinted commander Sprites plus the shadow diamond,
 * positioned and `zIndex`-ed for the scene.
 */
export function drawCommander(
  x: number,
  y: number,
  altitude: number,
  owner: string,
  surfaceZ: number | ((cx: number, cy: number) => number) = 0,
  zBias = 0,
  getPiece: (index: number, textured: boolean) => Container = () => new Graphics(),
  mine = true,
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
  const tint = unitFill(mine);
  for (const { slice, texture } of commanderSlices()) {
    const s = getPiece(idx++, true) as Sprite;
    s.texture = texture;
    s.position.set(origin.x, origin.y);
    s.tint = tint;
    s.zIndex = depthKey(x + slice.dx, y + slice.dy, altitude) + zBias;
  }
  const surfaceAt = typeof surfaceZ === 'number' ? () => surfaceZ : surfaceZ;
  const parts = shadowParts(x, y, surfaceAt).filter((p) => p.z < altitude);
  if (parts.length) {
    const shadow = getPiece(idx++, false) as Graphics;
    if ('clear' in shadow) shadow.clear();
    for (const p of parts) {
      const pts = [project(p.x0, p.y0, p.z), project(p.x1, p.y0, p.z), project(p.x1, p.y1, p.z), project(p.x0, p.y1, p.z)];
      shadow.poly(pts.flatMap((q) => [q.x, q.y])).fill({ color: 0x000000, alpha: 0.35 });
    }
    shadow.zIndex = depthKey(x, y, Math.max(...parts.map((p) => p.z))) + zBias;
  }
}

/**
 * The 2×2 body anchored at (x, y) (fractional mid-move) cut along cell
 * edges: one rectangle per cell it overlaps, at that cell's surface.
 * Cell (cx, cy) covers [cx-0.5, cx+0.5] × [cy-0.5, cy+0.5].
 */
export function shadowParts(
  x: number,
  y: number,
  surfaceAt: (cx: number, cy: number) => number,
): { x0: number; x1: number; y0: number; y1: number; z: number }[] {
  const eps = 1e-9;
  const [ax, bx] = [x - 0.5, x - 0.5 + UNIT_SIZE];
  const [ay, by] = [y + 0.5 - UNIT_SIZE, y + 0.5];
  const parts = [];
  for (let cy = Math.floor(ay + 0.5 + eps); cy <= Math.ceil(by + 0.5 - eps) - 1; cy++) {
    for (let cx = Math.floor(ax + 0.5 + eps); cx <= Math.ceil(bx + 0.5 - eps) - 1; cx++) {
      const x0 = Math.max(ax, cx - 0.5);
      const x1 = Math.min(bx, cx + 0.5);
      const y0 = Math.max(ay, cy - 0.5);
      const y1 = Math.min(by, cy + 0.5);
      if (x1 - x0 > eps && y1 - y0 > eps) parts.push({ x0, x1, y0, y1, z: surfaceAt(cx, cy) });
    }
  }
  return parts;
}
