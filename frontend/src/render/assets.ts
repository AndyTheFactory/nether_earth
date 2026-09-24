// Asset pipeline (M8.10). Presentation is selected by *semantic id* only.
// Each id may map to an image under public/assets/; when the file is absent
// (all of them today) an explicit procedural placeholder is used so nothing
// blocks UI work. No gameplay value (cost, range, collision) lives here.
import { Assets, Texture } from 'pixi.js';
import type { SceneryAsset, SceneryManifest } from './scenery.ts';

export type SemanticAsset =
  | 'terrain.normal'
  | 'terrain.rough'
  | 'terrain.mountain'
  | 'terrain.ditch'
  | 'structure.warbase'
  | 'structure.factory'
  | 'structure.blocker'
  | 'module.bipod'
  | 'module.tracks'
  | 'module.anti_grav'
  | 'module.cannon'
  | 'module.missile'
  | 'module.phaser'
  | 'module.nuclear'
  | 'module.electronics'
  | 'commander'
  | 'projectile.cannon'
  | 'projectile.missile'
  | 'projectile.phaser'
  | 'projectile.nuclear';

/**
 * Placeholder palette: ZX Spectrum bright colours, chosen to keep the
 * original read (cyan/magenta ownership, yellow terrain, white structures).
 */
export const PALETTE = {
  black: 0x000000,
  blue: 0x0000d7,
  red: 0xd70000,
  magenta: 0xd700d7,
  green: 0x00d700,
  cyan: 0x00d7d7,
  yellow: 0xd7d700,
  white: 0xd7d7d7,
  brightYellow: 0xffff00,
  brightWhite: 0xffffff,
  brightCyan: 0x00ffff,
  brightMagenta: 0xff00ff,
  brightGreen: 0x00ff00,
  brightRed: 0xff0000,
} as const;

/**
 * Fill for a robot or commander sprite: bright white for the viewing
 * player's own units, plain white -- the Spectrum's non-bright white, which
 * reads as light grey -- for the enemy's (owner request, 2026-09-24).
 *
 * Unit sprites are drawn with black ink and white paper, and Pixi `tint`
 * multiplies, so black lines survive any tint and the paper takes exactly
 * this colour. Ownership is therefore carried by brightness rather than by
 * hue; the enemy marker ring and the structure flags still use
 * `ownerColor`, so per-player colour has not disappeared from the screen.
 */
export function unitFill(mine: boolean): number {
  return mine ? PALETTE.brightWhite : PALETTE.white;
}

/**
 * Player ownership tints: p1 cyan, p2 magenta, neutral white.
 *
 * Still used by the enemy marker ring, the ownership flags and a unit's
 * ground shadow. Robot and commander *bodies* no longer use it -- they are
 * white or light grey by viewer relationship instead, see `unitFill`.
 */
export function ownerColor(owner: string | null | undefined): number {
  if (owner === 'p1') return PALETTE.brightCyan;
  if (owner === 'p2') return PALETTE.brightMagenta;
  return PALETTE.white;
}

export const PLACEHOLDER_COLORS: Record<SemanticAsset, number> = {
  'terrain.normal': 0x1c5a1c,
  'terrain.rough': 0x6b5a1c,
  'terrain.mountain': 0x9a7b5a,
  'terrain.ditch': 0x123a5a,
  'structure.warbase': PALETTE.white,
  'structure.factory': PALETTE.yellow,
  'structure.blocker': 0x8a8a8a,
  'module.bipod': PALETTE.green,
  'module.tracks': PALETTE.yellow,
  'module.anti_grav': PALETTE.cyan,
  'module.cannon': PALETTE.white,
  'module.missile': PALETTE.red,
  'module.phaser': PALETTE.magenta,
  'module.nuclear': PALETTE.brightRed,
  'module.electronics': PALETTE.blue,
  commander: PALETTE.brightWhite,
  'projectile.cannon': PALETTE.brightWhite,
  'projectile.missile': PALETTE.brightRed,
  'projectile.phaser': PALETTE.brightMagenta,
  'projectile.nuclear': PALETTE.brightYellow,
};

const textures = new Map<SemanticAsset, Texture>();

/**
 * War-base/factory wall segments (owner-directed extension, 2026-09-22):
 * `walls` maps a `MapComponent.height` (as a string key, since JSON object
 * keys are strings) to a `structure.*` asset id; `assets` is shaped like
 * `SceneryManifest.assets` (sprite/footprint/height) so the same
 * `sceneryPlacements`-style "unmapped falls back to a placeholder prism"
 * contract applies to any height a custom map uses that isn't 7 or 15.
 */
export interface StructureManifest {
  walls: Record<string, string>;
  /** Sprites drawn on top of a structure rather than as part of its walls. */
  decorations?: Record<string, string>;
  assets: Record<string, SceneryAsset>;
}

/**
 * Terrain element index -> asset, from the manifest's `terrain` section.
 *
 * A pair is `[even, odd]`, chosen by the element's y parity. That is the
 * Spectrum's own indexing: `Ld6e8_additional_isometric_graphic_pointers`
 * holds two entries per element type and `Lcf2d_draw_sprite_to_buffer`
 * picks between them with the y bit. Only the ditches make the two entries
 * different drawings (a run along y vs a run along x); every other type
 * repeats one drawing, so it stays a plain string here.
 */
export type TerrainElementAsset = string | readonly [string, string];

export interface TerrainManifest {
  elements: Record<string, TerrainElementAsset>;
  assets: Record<string, SceneryAsset>;
}

/**
 * The asset a terrain element of `type` at row `y` draws with: the y-parity
 * entry when the manifest gives a pair. The id comes back with it because
 * the renderer caches sliced textures per asset id.
 */
export function terrainElementAsset(m: TerrainManifest, type: number, y: number): { id: string; asset: SceneryAsset } | undefined {
  const entry = m.elements[String(type)];
  const id = typeof entry === 'string' ? entry : entry?.[Math.abs(y) % 2];
  const asset = id === undefined ? undefined : m.assets[id];
  return id === undefined || asset === undefined ? undefined : { id, asset };
}

export interface AssetManifest {
  /** semantic id → image path under public/ (e.g. "assets/module.bipod.png") */
  images: Partial<Record<SemanticAsset, string>>;
  /** CR002.5: blocker kind -> scenery asset (sprite, footprint, height). */
  scenery?: SceneryManifest;
  /** War-base/factory wall segments (owner-directed extension, 2026-09-22). */
  structures?: StructureManifest;
  /** Spectrum terrain element sprites (owner request, 2026-09-24). */
  terrain?: TerrainManifest;
}

let scenery: SceneryManifest | null = null;
let structures: StructureManifest | null = null;
let terrain: TerrainManifest | null = null;

/** Scenery mapping from the loaded manifest; null (placeholder prisms) if absent. */
export function sceneryManifest(): SceneryManifest | null {
  return scenery;
}

/** War-base/factory wall-segment mapping from the loaded manifest; null (placeholder prisms) if absent. */
export function structureManifest(): StructureManifest | null {
  return structures;
}

/** Terrain element sprites from the loaded manifest; null (flat colour) if absent. */
export function terrainManifest(): TerrainManifest | null {
  return terrain;
}

/**
 * Load `public/assets/manifest.json` and every image it lists. Ids without an
 * entry (or a manifest that is absent/unreadable) fall back to the procedural
 * placeholder; the returned list names them so the degradation is explicit.
 */
export async function loadAssets(manifestUrl = '/assets/manifest.json'): Promise<SemanticAsset[]> {
  const ids = Object.keys(PLACEHOLDER_COLORS) as SemanticAsset[];
  let manifest: AssetManifest = { images: {} };
  try {
    const res = await fetch(manifestUrl);
    if (res.ok) manifest = (await res.json()) as AssetManifest;
  } catch {
    /* no manifest: placeholders everywhere */
  }
  scenery = manifest.scenery ?? null;
  structures = manifest.structures ?? null;
  terrain = manifest.terrain ?? null;
  const missing: SemanticAsset[] = [];
  await Promise.all(
    ids.map(async (id) => {
      const src = manifest.images[id];
      if (!src) {
        missing.push(id);
        return;
      }
      try {
        textures.set(id, await Assets.load<Texture>({ alias: id, src }));
      } catch {
        missing.push(id);
      }
    }),
  );
  return missing;
}

export function textureFor(id: SemanticAsset): Texture | null {
  return textures.get(id) ?? null;
}

export function colorFor(id: SemanticAsset): number {
  return PLACEHOLDER_COLORS[id];
}

export function shade(color: number, factor: number): number {
  const r = Math.min(255, Math.round(((color >> 16) & 0xff) * factor));
  const g = Math.min(255, Math.round(((color >> 8) & 0xff) * factor));
  const b = Math.min(255, Math.round((color & 0xff) * factor));
  return (r << 16) | (g << 8) | b;
}
