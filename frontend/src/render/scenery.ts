// CR002.5: scenery blockers drawn with Spectrum map-element sprites chosen by
// data. `public/assets/manifest.json` maps each blocker `kind` (opaque label
// from the map, CR002.1) to a scenery asset, and each asset to a sprite from
// SCENERY_SPRITES plus its footprint and height. Changing a kind's asset in
// the manifest changes the render with no code change. Presentation only:
// collision and heights stay with the engine and the map data.
import type { MapBlocker, MapComponent, MapData } from '../world/map.ts';
import { depthKey } from './projection.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
import { sliceSpriteRows, spriteOriginFor, type SpriteSlice } from './sprite-slice.ts';

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
  return spriteOriginFor(SCENERY_SPRITES[asset.sprite]!, asset.footprint, anchor, 0, asset.offset ?? [0, 0]);
}

export type { SpriteSlice };

/**
 * Split a sprite into one slice per footprint cell so each slice joins the
 * shared painter's order with its own cell's depth key, like the per-cell
 * prisms of war bases and factories. A pixel belongs to the cell whose
 * visible surface it shows: the highest point of the footprint's solid
 * (0..height) on that pixel's view line. See `sprite-slice.ts` (CR002.5,
 * shared with robots/commander/structure walls since #owner-2026-09-22).
 */
export function sliceSprite(asset: SceneryAsset): SpriteSlice[] {
  return sliceSpriteRows(SCENERY_SPRITES[asset.sprite]!, asset.footprint, asset.height);
}

/** Painter's key of a slice for a blocker anchored at `anchor`. */
export function sliceDepth(anchor: { x: number; y: number }, s: SpriteSlice): number {
  return depthKey(anchor.x + s.dx, anchor.y + s.dy);
}

export function parseColor(hex: string | undefined, fallback: number): number {
  return hex && /^#[0-9a-f]{6}$/i.test(hex) ? parseInt(hex.slice(1), 16) : fallback;
}

/** One 2x2 wall block of a war base or factory, with its shared height. */
export interface WallBlock {
  /** Sprite anchor: the block's min-x, max-y cell, as `sceneryPlacements` uses. */
  anchor: { x: number; y: number };
  /** The height all four cells share, which selects the wall sprite. */
  height: number;
  /** The four cells, so per-cell overlays (heli-pads, flags) still find them. */
  cells: MapComponent[];
}

/**
 * Group a structure's 1x1 components into the 2x2 blocks its sprites are drawn as.
 *
 * A war base or factory is stored in the map as individual cells, but the
 * Spectrum draws it from 2x2 map elements -- the same size as a scenery box,
 * and the same artwork size (32 px against a 12 px cell). Drawing one sprite
 * per cell therefore overlapped every neighbour and, because `spriteOriginFor`
 * offsets by the footprint's depth, placed each one a cell too low. This
 * recovers the blocks the cells were flattened from.
 *
 * A block qualifies only when all four of its cells are present and share a
 * height, which is what makes one sprite able to stand for it. Cells that do
 * not form such a block come back in `loose` for the caller's placeholder
 * path -- on the built-in map every factory is wholly 2x2 (a doorway is a
 * missing block, not a partial one) while the war bases have some half
 * blocks.
 *
 * Blocks are keyed from the structure's own min-x/min-y so the parity is the
 * structure's, not the map origin's, and returned in a deterministic order.
 */
export function wallBlocks(components: readonly MapComponent[]): {
  blocks: WallBlock[];
  loose: MapComponent[];
} {
  if (!components.length) return { blocks: [], loose: [] };
  const minX = Math.min(...components.map((c) => c.x));
  const minY = Math.min(...components.map((c) => c.y));
  const grouped = new Map<string, MapComponent[]>();
  for (const c of components) {
    const key = `${Math.floor((c.x - minX) / 2)},${Math.floor((c.y - minY) / 2)}`;
    const bucket = grouped.get(key);
    if (bucket) bucket.push(c);
    else grouped.set(key, [c]);
  }
  const blocks: WallBlock[] = [];
  const loose: MapComponent[] = [];
  for (const cells of grouped.values()) {
    const heights = new Set(cells.map((c) => c.height));
    if (cells.length === 4 && heights.size === 1) {
      blocks.push({
        anchor: { x: Math.min(...cells.map((c) => c.x)), y: Math.max(...cells.map((c) => c.y)) },
        height: cells[0]!.height,
        cells: [...cells],
      });
    } else {
      loose.push(...cells);
    }
  }
  blocks.sort((a, b) => a.anchor.x - b.anchor.x || a.anchor.y - b.anchor.y);
  loose.sort((a, b) => a.x - b.x || a.y - b.y);
  return { blocks, loose };
}
