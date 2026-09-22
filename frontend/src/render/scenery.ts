// CR002.5: scenery blockers drawn with Spectrum map-element sprites chosen by
// data. `public/assets/manifest.json` maps each blocker `kind` (opaque label
// from the map, CR002.1) to a scenery asset, and each asset to a sprite from
// SCENERY_SPRITES plus its footprint and height. Changing a kind's asset in
// the manifest changes the render with no code change. Presentation only:
// collision and heights stay with the engine and the map data.
import type { MapBlocker, MapData } from '../world/map.ts';
import { depthKey, project, unproject } from './projection.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';

export interface SceneryAsset {
  /** Key into SCENERY_SPRITES. */
  sprite: string;
  /** Cells along x and y; must match the blocker's component extent. */
  footprint: [number, number];
  /** Drawn height in authoritative units (the sprite's box height). */
  height: number;
  /** Colours as '#rrggbb'; default Spectrum black ink on yellow paper. */
  ink?: string;
  paper?: string;
  /**
   * Optional presentation-only shift of the drawn sprite, in world pixels
   * [right, down]. Default [0, 0] (the Spectrum placement). Collision and
   * heights are unaffected (CR003.7: the fence post is centred on its
   * footprint, owner decision 2026-09-22).
   */
  offset?: [number, number];
}

export interface SceneryManifest {
  /** blocker kind -> scenery asset id */
  kinds: Record<string, string>;
  /** scenery asset id -> asset */
  assets: Record<string, SceneryAsset>;
}

export interface SceneryPlacement {
  blockerId: string;
  assetId: string;
  asset: SceneryAsset;
  /** Footprint's nearest cell (min x, max y): where the Spectrum anchors a 2x2 stamp. */
  anchor: { x: number; y: number };
}

/** Manifest kind a blocker resolves through once a nuclear blast made it debris (CR002.18). */
export const DEBRIS_KIND = 'debris';

/**
 * Resolve every blocker to its asset. Blockers whose kind has no valid
 * mapping (unknown kind or asset, missing sprite, footprint mismatch) are
 * returned in `unmapped` so the renderer can fall back to placeholder prisms.
 * Blockers listed in `debris` (snapshot `scenery_debris`) use the
 * `debris` kind instead of their own.
 */
export function sceneryPlacements(
  map: MapData,
  manifest: SceneryManifest | null,
  debris: ReadonlySet<string> = new Set(),
): { placements: SceneryPlacement[]; unmapped: MapBlocker[] } {
  const placements: SceneryPlacement[] = [];
  const unmapped: MapBlocker[] = [];
  for (const b of map.blockers) {
    const kind = debris.has(b.id) ? DEBRIS_KIND : b.kind;
    const assetId = kind !== undefined ? manifest?.kinds[kind] : undefined;
    const asset = assetId !== undefined ? manifest?.assets[assetId] : undefined;
    const xs = b.components.map((c) => c.x);
    const ys = b.components.map((c) => c.y);
    const minX = Math.min(...xs);
    const maxY = Math.max(...ys);
    const fits =
      !!asset &&
      !!SCENERY_SPRITES[asset.sprite] &&
      Math.max(...xs) - minX + 1 === asset.footprint[0] &&
      maxY - Math.min(...ys) + 1 === asset.footprint[1] &&
      b.components.length === asset.footprint[0] * asset.footprint[1];
    if (fits) placements.push({ blockerId: b.id, assetId: assetId!, asset: asset!, anchor: { x: minX, y: maxY } });
    else unmapped.push(b);
  }
  return { placements, unmapped };
}

/**
 * World-space top-left of the sprite for a footprint anchored at (x, y).
 * The Spectrum draws every map element with the same routine
 * (Lcf2d_draw_sprite_to_buffer), so one rule fits all sprites: column 0 sits
 * on the footprint's leftmost ground corner and the last row on its lowest
 * ground corner (pixel centres on the corners). An asset's `offset` shifts
 * the result.
 */
export function spriteOrigin(asset: SceneryAsset, anchor: { x: number; y: number }): { x: number; y: number } {
  const rows = SCENERY_SPRITES[asset.sprite]!.length;
  const left = project(anchor.x - 0.5, anchor.y - asset.footprint[1] + 0.5);
  const bottom = project(anchor.x - 0.5, anchor.y + 0.5);
  const [dx, dy] = asset.offset ?? [0, 0];
  return { x: left.x - 0.5 + dx, y: bottom.y - rows + 0.5 + dy };
}

export interface SpriteSlice {
  /** Footprint cell offset from the anchor (dx >= 0, dy <= 0). */
  dx: number;
  dy: number;
  /** Same size as the sprite; ' ' where another cell owns the pixel. */
  rows: string[];
}

/**
 * Split a sprite into one slice per footprint cell so each slice joins the
 * shared painter's order with its own cell's depth key, like the per-cell
 * prisms of war bases and factories. A pixel belongs to the cell whose
 * visible surface it shows: the highest point of the footprint's solid
 * (0..height) on that pixel's view line.
 */
export function sliceSprite(asset: SceneryAsset): SpriteSlice[] {
  const rows = SCENERY_SPRITES[asset.sprite]!;
  const [fw, fh] = asset.footprint;
  const o = spriteOrigin(asset, { x: 0, y: 0 });
  const cells: [number, number][] = [];
  for (let dy = 0; dy > -fh; dy--) for (let dx = 0; dx < fw; dx++) cells.push([dx, dy]);
  const out = cells.map(() => rows.map((r) => [...r].map(() => ' ')));
  const zTop = Math.min(asset.height, rows.length);
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

/** Painter's key of a slice for a blocker anchored at `anchor`. */
export function sliceDepth(anchor: { x: number; y: number }, s: SpriteSlice): number {
  return depthKey(anchor.x + s.dx, anchor.y + s.dy);
}

export function parseColor(hex: string | undefined, fallback: number): number {
  return hex && /^#[0-9a-f]{6}$/i.test(hex) ? parseInt(hex.slice(1), 16) : fallback;
}
