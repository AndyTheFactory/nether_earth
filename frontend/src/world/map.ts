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
  components: MapComponent[];
}

export type InteractionKind = 'warbase_capture' | 'factory_capture' | 'heli_pad' | 'exit';

export interface MapInteractionPoint {
  id: string;
  kind: InteractionKind;
  structure_id: string;
  footprint: { x: number; y: number };
}

export interface MapData {
  id: string;
  version: number;
  width: number;
  height: number;
  terrain: { default: TerrainType; cells: { x: number; y: number; type: TerrainType }[] };
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
