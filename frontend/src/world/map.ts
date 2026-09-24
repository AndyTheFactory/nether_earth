// Static world data shape, mirroring data/maps/*.yaml as converted by
// scripts/generate-map.mjs. Presentation reads this; it never derives
// occupancy, collision, or interaction legality from it.
import zxOriginal from '../generated/maps/zx-spectrum-original.json';

export type TerrainType = 'normal' | 'rough' | 'mountain' | 'ditch';

export interface MapComponent {
  x: number;
  y: number;
  height: number;
}

export interface MapWarBase {
  id: string;
  components: MapComponent[];
}

export type FactoryType = 'chassis' | 'electronics' | 'nuclear' | 'missile' | 'phaser' | 'cannon';

export interface MapFactory {
  id: string;
  components: MapComponent[];
  factory_type: FactoryType;
}

export interface MapBlocker {
  id: string;
  /** Opaque scenery label (e.g. `box_low`, `box_high`, `fence`) for asset mapping (CR002.5). */
  kind?: string;
  /** A nuclear blast turns it into rough debris (CR002.18); ids land in `SnapshotState.scenery_debris`. */
  destructible?: boolean;
  components: MapComponent[];
}

export type InteractionKind = 'warbase_capture' | 'factory_capture' | 'heli_pad' | 'exit';

export interface MapCell {
  x: number;
  y: number;
}

export interface MapInteractionPoint {
  id: string;
  kind: InteractionKind;
  structure_id: string;
  /** One cell, or a cell list (the 2×2 heli-pad, CR002.4). */
  footprint: MapCell | MapCell[];
}

/** The cells of an interaction point's footprint. */
export function footprintCells(ip: MapInteractionPoint): MapCell[] {
  return Array.isArray(ip.footprint) ? ip.footprint : [ip.footprint];
}

export interface MapData {
  id: string;
  version: number;
  width: number;
  height: number;
  terrain: {
    default: TerrainType;
    /** Height of the rough piece a nuclear blast leaves (`Ld7bc_map_piece_heights` types 6/7, CR002.21). */
    debris_height: number;
    /** `height`: the cell's terrain piece height (`Ld7bc_map_piece_heights`, CR002.21); absent = 0. */
    cells: { x: number; y: number; type: TerrainType; height?: number }[];
    /**
     * The 2x2 map elements `cells` were stamped from, in stamping order so a
     * later element drawn over an earlier one reproduces the original.
     * `type` is the raw Spectrum element index, which picks the sprite: it is
     * finer than the terrain class, since rough is elements 2-7 and mountain
     * 8-11, each its own graphic. Presentation only -- `cells` stays the
     * class and height that gameplay reads.
     */
    elements: { x: number; y: number; type: number }[];
  };
  war_bases: MapWarBase[];
  factories: MapFactory[];
  blockers: MapBlocker[];
  interaction_points: MapInteractionPoint[];
}

const MAPS: Record<string, MapData> = {
  [zxOriginal.id]: zxOriginal as MapData,
};

export function loadMap(id: string): MapData {
  const map = MAPS[id];
  if (!map) throw new Error(`unknown map ${id}`);
  return map;
}

export const DEFAULT_MAP_ID = 'zx-spectrum-original';

export function terrainAt(map: MapData, x: number, y: number): TerrainType {
  for (const cell of map.terrain.cells) {
    if (cell.x === x && cell.y === y) return cell.type;
  }
  return map.terrain.default;
}

/** Top of the static structure component standing on (x, y), or 0 on open ground. Presentation only. */
export function surfaceHeightAt(map: MapData, x: number, y: number): number {
  let top = 0;
  for (const s of [...map.war_bases, ...map.factories, ...map.blockers]) {
    for (const c of s.components) if (c.x === x && c.y === y) top = Math.max(top, c.height);
  }
  return top;
}
