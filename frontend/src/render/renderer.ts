// PixiJS world renderer (M8.2/M8.3/M8.8). Reads the store; never writes it.
import { Application, Container, Graphics, Sprite, Text, Texture } from 'pixi.js';
import type { SnapshotState } from '../../../protocol/generated/types';
import type { AppState } from '../state/store.ts';
import type { MapData, MapComponent } from '../world/map.ts';
import { footprintCells, surfaceHeightAt, terrainAt } from '../world/map.ts';
import { CO_LOCATED_TIE_BIAS, TILE_H, TILE_W, bandCovers, depthKey, playViewCentre, project, terrainBand, unproject, viewZoom, type ScreenPoint } from './projection.ts';
import { displayTick, interpolateAltitude, interpolateGrid, interpolateProjectile, isGridTransition, isVerticalTransition } from './interpolation.ts';
import { drawPrism, drawDiamond } from './prism.ts';
import { FLAG_POLE_COLUMN, FLAG_SPRITES, ownershipFlags, type FlagOwner } from './flags.ts';
import { drawRobotStack, drawCommander, robotGround, unitCentre, unitFootprintCells, type ModuleId } from './robot.ts';
import { RUBBLE_HEIGHT, SurfaceMap } from './surface.ts';
import { colorFor, ownerColor, PALETTE, sceneryManifest, shade, structureManifest, terrainElementAsset, terrainManifest, type SemanticAsset } from './assets.ts';
import { SCENERY_SPRITES } from './scenery-sprites.ts';
import { factoryDecorationAnchor, footprintCellsOf, parseColor, sceneryPlacements, sliceDepth, sliceSprite, spriteOrigin, spriteRows, wallBlocks, type SceneryAsset, type SpriteSlice, type WallBlock } from './scenery.ts';
import { pixelTexture, spriteOriginFor } from './sprite-slice.ts';
import { BULLET_FOOTPRINT, bulletAxis, bulletRows, bulletTexture } from './bullets.ts';
import { textOverlays } from '../state/labels.ts';
import { menuColumnShown } from '../ui/menus.ts';
import { menuColumnPx } from '../ui/radar.ts';

/**
 * War-base/factory wall segments (owner-directed extension, 2026-09-22):
 * `Lbfb2_warbase` / `Lbfe2_factory` (`netherearth-annotated.asm`) build both
 * structures from just these two map-element types (15 and 16), placed via
 * `Lbd61_add_complex_structure_to_map`. Their heights (`Ld7bc_map_piece_heights`
 * types 15/16 = 7/15) are exactly the two component heights the built-in map
 * (`data/maps/zx-spectrum-original.yaml`) already uses for every war-base and
 * factory cell, so `manifest.json`'s `structures.walls` maps each height to a
 * sprite with no invented data (`public/assets/README.md`). A component whose
 * height has no entry (a custom/future map, or a missing/unreadable manifest)
 * falls back to the placeholder prism, like an unmapped scenery kind.
 */
function structureWallAsset(height: number): { id: string; asset: SceneryAsset } | undefined {
  const m = structureManifest();
  const id = m?.walls[String(height)];
  const asset = id !== undefined ? m?.assets[id] : undefined;
  return id !== undefined && asset ? { id, asset } : undefined;
}

interface Effect {
  x: number;
  y: number;
  z: number;
  kind: 'hit' | 'explosion' | 'nuclear';
  startMs: number;
  durationMs: number;
}

export class WorldRenderer {
  readonly world = new Container();
  private terrain = new Graphics();
  // Terrain element sprites sit above the flat colour and below everything
  // else. Child order is draw order (no `sortableChildren`): the map lists
  // elements in stamping order, which is what resolves one overlapping another.
  private terrainSprites = new Container();
  // CR002.14: structures, scenery, robots, commanders and projectiles share
  // one painter's ordering (zIndex = depthKey), so nearer geometry covers
  // whatever stands behind it. Structure cells are cached; the rest is
  // rebuilt every frame.
  private scene = new Container({ sortableChildren: true });
  private structureCells: { g: Container; x: number }[] = [];
  // Dynamic (robot/commander/projectile) Graphics/Sprites are pooled by a
  // stable key (#256): every frame still redraws/repositions their contents
  // (interpolated position changes every tick), but reusing the object
  // instead of destroying and reallocating one avoids per-frame GC churn and
  // scene-graph add/remove on top of that redraw cost. Sprite pieces (owner
  // extension, 2026-09-22) are pooled the same way, keyed per slice index.
  private dynamicPool = new Map<string, Container>();
  private structureLabels = new Container();
  private effects = new Graphics();
  private overlay = new Graphics();
  // Text stays unscaled: labels live in a sibling layer that tracks the
  // world's offset, positioned via labelAt().
  private readonly labels = new Container();
  private overlayLabels = new Container();
  private zoom = 1;
  private cam = { x: 24, y: 8 };
  private readonly surface: SurfaceMap;
  private readonly sceneryTextures = new Map<string, { slice: SpriteSlice; texture: Texture }[]>();
  private lastStructureKey = '';
  private terrainBandDrawn: { x0: number; x1: number } | null = null;
  private spritedTerrain: Set<string> | null = null;
  private lastResync = -1;
  private prevSnapshot: SnapshotState | null = null;
  private fx: Effect[] = [];

  constructor(
    private readonly app: Application,
    private readonly map: MapData,
  ) {
    this.surface = new SurfaceMap(map);
    this.world.addChild(this.terrain, this.terrainSprites, this.scene, this.effects, this.overlay);
    this.labels.addChild(this.structureLabels, this.overlayLabels);
    app.stage.addChild(this.world, this.labels);
  }

  // ---- static layers ----

  /**
   * Rebuilds the terrain band around the camera when the view has left the
   * drawn one. The whole 512x16 map used to live in this one `Graphics`,
   * built once but submitted in full every frame for the ~30 columns on
   * screen; a band plus `TERRAIN_MARGIN` cells of slack draws what is
   * visible and rebuilds only every few cells of camera travel.
   */
  private updateTerrain(): void {
    const span = this.viewSpanX();
    if (this.terrainBandDrawn && bandCovers(this.terrainBandDrawn, this.cam.x, span, this.map.width)) return;
    const band = terrainBand(this.cam.x, span, this.map.width);
    this.terrainBandDrawn = band;
    const g = this.terrain;
    g.clear();
    const drawn = this.spritedTerrainCells();
    for (let y = 0; y < this.map.height; y++) {
      for (let x = band.x0; x <= band.x1; x++) {
        // A cell a terrain sprite covers keeps the plain ground checker: the
        // sprite draws the rough/mountain/ditch itself, over transparent
        // paper, so tinting the ground under it only showed through as a
        // block of colour around the drawing. Cells with no sprite (an
        // unmapped element type, or no terrain manifest at all) keep the
        // per-class tint as the fallback look.
        const t = drawn.has(`${x},${y}`) ? 'normal' : terrainAt(this.map, x, y);
        const id = `terrain.${t}` as SemanticAsset;
        const base = colorFor(id);
        drawDiamond(g, x, y, (x + y) % 2 ? base : shade(base, 1.12));
      }
    }
    this.drawTerrainElements(band);
  }

  /**
   * The cells covered by a terrain element that actually has a sprite to
   * draw, keyed "x,y". Computed once: the map's elements are static.
   */
  private spritedTerrainCells(): Set<string> {
    if (this.spritedTerrain) return this.spritedTerrain;
    const out = new Set<string>();
    const manifest = terrainManifest();
    if (manifest) {
      for (const e of this.map.terrain.elements) {
        const entry = terrainElementAsset(manifest, e.type, e.y);
        if (!entry || !SCENERY_SPRITES[entry.asset.sprite]) continue;
        for (let dy = 0; dy > -entry.asset.footprint[1]; dy--) {
          for (let dx = 0; dx < entry.asset.footprint[0]; dx++) out.add(`${e.x + dx},${e.y + dy}`);
        }
      }
    }
    this.spritedTerrain = out;
    return out;
  }

  /**
   * Draw the Spectrum terrain sprites for the elements inside `band`.
   *
   * Rough, mountain and ditch are 2x2 map elements with their own graphics
   * (owner request, 2026-09-24); the flat checker drawn above stays as the
   * ground underneath them, so an unmapped element or a missing manifest
   * simply leaves the old look. Elements are added in the map's stamping
   * order into a plain container, so a later element covers an earlier one
   * exactly as it does on the Spectrum -- child order is draw order, which
   * is why this container is deliberately not `sortableChildren`.
   */
  private drawTerrainElements(band: { x0: number; x1: number }): void {
    this.terrainSprites.removeChildren().forEach((c) => c.destroy());
    const manifest = terrainManifest();
    if (!manifest) return;
    for (const element of this.map.terrain.elements) {
      if (element.x + 1 < band.x0 || element.x > band.x1) continue;
      // The y parity picks between a type's two graphics, as on the Spectrum:
      // for ditches that is the run-along-y piece vs the run-along-x one.
      const entry = terrainElementAsset(manifest, element.type, element.y);
      if (!entry || !SCENERY_SPRITES[entry.asset.sprite]) continue;
      const { id: assetId, asset } = entry;
      const origin = spriteOrigin(asset, { x: element.x, y: element.y });
      for (const { texture } of this.sceneryTexturesFor(assetId, asset)) {
        const s = new Sprite(texture);
        s.position.set(origin.x, origin.y);
        this.terrainSprites.addChild(s);
      }
    }
  }

  /** Half-width, in cells, of the view around `cam.x` (shared by terrain and structure culling). */
  private viewSpanX(): number {
    return (this.app.screen.width + this.app.screen.height) / this.zoom / TILE_W + 4;
  }

  // Structure name labels are an optional overlay (CR002.23, textOverlays); flags show ownership.
  private drawStructures(state: SnapshotState | null, debug: boolean): void {
    const key = JSON.stringify([this.zoom, debug, state?.structure_ownership, state?.structure_destruction, state?.scenery_debris]);
    if (key === this.lastStructureKey) return;
    this.lastStructureKey = key;
    for (const { g } of this.structureCells) g.destroy();
    this.structureCells = [];
    this.structureLabels.removeChildren().forEach((t) => t.destroy());
    const owner = (id: string) => state?.structure_ownership.find((o) => o.structure_id === id)?.owner ?? null;
    const destroyedIds = new Set(state?.structure_destruction ?? []);
    const destroyed = (id: string) => destroyedIds.has(id);

    // CR002.18: a nuclear blast turns destructible scenery into rough debris.
    const debrisIds = new Set(state?.scenery_debris ?? []);
    // War-base/factory blocks (owner-directed extension, 2026-09-22): a
    // component whose height matches a decoded wall segment (7 or 15, see
    // structureWallAsset) draws that Spectrum sprite; anything else (a
    // custom map's own heights, or a dead structure's rubble) keeps the
    // placeholder prism, exactly like an unmapped scenery kind.
    const structureBlocks: { c: MapComponent; color: number; dead: boolean }[] = [];
    const structureWalls: WallBlock[] = [];
    const collect = (components: MapComponent[], color: number, isDead: boolean): void => {
      if (isDead) {
        // A destroyed structure is rubble, drawn per cell; no wall sprites.
        for (const c of components) structureBlocks.push({ c, color, dead: true });
        return;
      }
      const { blocks: walls, loose } = wallBlocks(components);
      for (const wall of walls) {
        if (structureWallAsset(wall.height)) structureWalls.push(wall);
        // A block whose height has no decoded sprite (a custom map's own
        // heights) falls back to per-cell prisms, like an unmapped scenery kind.
        else for (const c of wall.cells) structureBlocks.push({ c, color, dead: false });
      }
      for (const c of loose) structureBlocks.push({ c, color, dead: false });
    };
    for (const wb of this.map.war_bases) {
      const col = ownerColor(owner(wb.id));
      collect(wb.components, col, destroyed(wb.id));
      if (debug) this.label(wb.id, wb.components, `WAR BASE ${owner(wb.id) ?? 'neutral'}${destroyed(wb.id) ? ' ✕' : ''}`, col);
    }
    for (const f of this.map.factories) {
      const col = shade(colorFor('structure.factory'), owner(f.id) ? 1.2 : 0.9);
      collect(f.components, col, destroyed(f.id));
      if (debug) this.label(f.id, f.components, `${f.factory_type.toUpperCase()} ${owner(f.id) ?? 'neutral'}${destroyed(f.id) ? ' ✕' : ''}`, ownerColor(owner(f.id)));
    }
    const blocks: { c: MapComponent; color: number; dead: boolean; debris?: boolean }[] = [];
    // CR002.5: mapped blockers are Spectrum sprites (below); the rest keep placeholder prisms.
    // CR002.18: debris blockers resolve through the manifest's `debris` kind.
    const scenery = sceneryPlacements(this.map, sceneryManifest(), debrisIds);
    for (const b of scenery.unmapped) {
      const debris = debrisIds.has(b.id);
      const color = colorFor(debris ? 'terrain.rough' : 'structure.blocker');
      for (const c of b.components) blocks.push({ c, color, dead: false, debris });
    }
    // Heli-pads sit on the war-base roof (open-questions §18). The pad is one
    // 2×2 sprite (the Spectrum's "H" decoration, Lce38_draw_decoration), but
    // it is drawn a cell at a time like the walls under it so a nearer block
    // still occludes it: each cell maps to the pad anchor it belongs to.
    const padAsset = this.decorationAsset('heli_pad');
    const pads = new Map<string, { x: number; y: number }>();
    for (const ip of this.map.interaction_points) {
      if (ip.kind !== 'heli_pad') continue;
      const cells = footprintCells(ip);
      const anchor = { x: Math.min(...cells.map((c) => c.x)), y: Math.max(...cells.map((c) => c.y)) };
      for (const c of cells) pads.set(`${c.x},${c.y}`, anchor);
    }
    // CR002.6: an ownership flag stands on its roof cell and is drawn with
    // that cell, so it shares the cell's place in the depth ordering.
    const flags = new Map(ownershipFlags(this.map, state?.structure_ownership ?? [], destroyedIds).map((f) => [`${f.x},${f.y}`, f.owner]));
    // One sprite per 2x2 wall block, sliced per cell like scenery so a nearer
    // block occludes it correctly (the art is a 2x2 element -- drawing it once
    // per 1x1 cell overlapped every neighbour and sat a cell too low).
    for (const wall of structureWalls) {
      const asset = structureWallAsset(wall.height)!;
      const origin = spriteOrigin(asset.asset, wall.anchor);
      for (const { slice, texture } of this.sceneryTexturesFor(asset.id, asset.asset)) {
        const s = new Sprite(texture);
        s.position.set(origin.x, origin.y);
        s.zIndex = sliceDepth(wall.anchor, slice);
        this.structureCells.push({ g: s, x: wall.anchor.x + slice.dx });
        this.scene.addChild(s);
      }
      // Heli-pad and flag overlays are per cell, so they are drawn for each
      // cell the block covers rather than once for the block.
      for (const c of wall.cells) {
        const cellKey = `${c.x},${c.y}`;
        const padAnchor = pads.get(cellKey);
        if (padAnchor) this.drawPadCell(padAsset, padAnchor, c);
        const flag = flags.get(cellKey);
        if (!flag) continue;
        const g = new Graphics();
        drawFlag(g, c.x, c.y, c.height, flag);
        g.zIndex = depthKey(c.x, c.y);
        this.structureCells.push({ g, x: c.x });
        this.scene.addChild(g);
      }
    }
    // A factory carries the piece it produces on its roof (the original's
    // decoration types 1-6, Lce56_decoration_sprite_indexes). Drawn after the
    // walls so it lands on the block it stands on.
    for (const factory of this.map.factories) {
      if (destroyedIds.has(factory.id)) continue;
      const dec = this.decorationAsset(`factory.${factory.factory_type}`);
      if (!dec) continue;
      const anchor = factoryDecorationAnchor(factory);
      if (!anchor) continue;
      for (const cell of footprintCellsOf(anchor, dec.asset.footprint)) {
        this.drawPadCell(dec, anchor, { ...cell, height: dec.asset.elevation ?? 0 });
      }
    }
    // Placeholder prisms: a destroyed structure's rubble, and any cell that is
    // not part of a whole 2x2 wall block (the war bases have a few).
    for (const { c, color, dead } of structureBlocks) {
      const g = new Graphics();
      if (dead) drawPrism(g, c.x, c.y, 0, RUBBLE_HEIGHT, shade(color, 0.3), 0.8);
      else {
        drawPrism(g, c.x, c.y, 0, c.height, color);
        const padAnchor = pads.get(`${c.x},${c.y}`);
        if (padAnchor) this.drawPadCell(padAsset, padAnchor, c);
        const flag = flags.get(`${c.x},${c.y}`);
        if (flag) drawFlag(g, c.x, c.y, c.height, flag);
      }
      g.zIndex = depthKey(c.x, c.y);
      this.structureCells.push({ g, x: c.x });
      this.scene.addChild(g);
    }
    for (const { c, color, dead, debris } of blocks) {
      const g = new Graphics();
      if (dead) drawPrism(g, c.x, c.y, 0, RUBBLE_HEIGHT, shade(color, 0.3), 0.8);
      // Fallback prism of unmapped debris: the map's rough-piece debris height (types 6/7, 3).
      else if (debris) drawPrism(g, c.x, c.y, 0, this.map.terrain.debris_height, shade(color, (c.x + c.y) % 2 ? 0.8 : 1));
      else drawPrism(g, c.x, c.y, 0, c.height, color);
      g.zIndex = depthKey(c.x, c.y);
      this.structureCells.push({ g, x: c.x });
      this.scene.addChild(g);
    }
    for (const p of scenery.placements) {
      const origin = spriteOrigin(p.asset, p.anchor);
      for (const { slice, texture } of this.sceneryTexturesFor(p.assetId, p.asset)) {
        const s = new Sprite(texture);
        s.position.set(origin.x, origin.y);
        s.zIndex = sliceDepth(p.anchor, slice);
        this.structureCells.push({ g: s, x: p.anchor.x + slice.dx });
        this.scene.addChild(s);
      }
    }
  }

  /** One texture per footprint-cell slice of a scenery asset, built once. */
  private sceneryTexturesFor(id: string, asset: SceneryAsset): { slice: SpriteSlice; texture: Texture }[] {
    let t = this.sceneryTextures.get(id);
    if (!t) {
      const ink = parseColor(asset.ink, PALETTE.black);
      const paper = asset.paper === 'none' ? null : parseColor(asset.paper, PALETTE.yellow);
      t = sliceSprite(asset).map((slice) => ({ slice, texture: pixelTexture(slice.rows, ink, paper) }));
      this.sceneryTextures.set(id, t);
    }
    return t;
  }

  /** A `structures.decorations` asset, when the manifest ships one with a decoded sprite. */
  private decorationAsset(kind: string): { id: string; asset: SceneryAsset } | null {
    const m = structureManifest();
    const id = m?.decorations?.[kind];
    const asset = id === undefined ? undefined : m!.assets[id];
    if (id === undefined || !asset || !spriteRows(asset.sprite)) return null;
    return { id, asset };
  }

  /**
   * Draw one footprint cell of a structure decoration standing on `cell`'s
   * top face. Without the asset it falls back to the flat marker the pad used
   * before the Spectrum sprite was decoded, so a manifest without the
   * decoration still shows where the pad is.
   */
  private drawPadCell(pad: { id: string; asset: SceneryAsset } | null, anchor: { x: number; y: number }, cell: { x: number; y: number; height: number }): void {
    if (!pad) {
      const g = new Graphics();
      drawDiamond(g, cell.x, cell.y, PALETTE.brightGreen, 0.9, PALETTE.white, cell.height);
      g.zIndex = depthKey(cell.x, cell.y);
      this.structureCells.push({ g, x: cell.x });
      this.scene.addChild(g);
      return;
    }
    const rows = spriteRows(pad.asset.sprite)!;
    const z = pad.asset.elevation ?? cell.height;
    const origin = spriteOriginFor(rows, pad.asset.footprint, anchor, z, pad.asset.offset ?? [0, 0]);
    for (const { slice, texture } of this.sceneryTexturesFor(pad.id, pad.asset)) {
      if (anchor.x + slice.dx !== cell.x || anchor.y + slice.dy !== cell.y) continue;
      const s = new Sprite(texture);
      s.position.set(origin.x, origin.y);
      // Same cell as the wall block under it, drawn after it, so it lands on
      // the roof rather than behind it.
      s.zIndex = depthKey(cell.x, cell.y);
      this.structureCells.push({ g: s, x: cell.x });
      this.scene.addChild(s);
    }
  }

  /** Skip drawing structure cells far outside the view (they stay in the ordering). */
  private cullStructures(): void {
    const span = this.viewSpanX();
    for (const { g, x } of this.structureCells) g.renderable = Math.abs(x - this.cam.x) <= span;
  }

  /**
   * Returns this frame's Graphics for `poolKey`, cleared and ready to redraw
   * (a reused object on every frame but the first) and records it as used.
   * `usedKeys` is swept against `this.dynamicPool` at the end of the frame
   * (see `render()`) so entities that left the snapshot get their Graphics
   * destroyed instead of leaking.
   */
  private pooledDynamic(poolKey: string, depthZ: number, usedKeys: Set<string>): Graphics {
    usedKeys.add(poolKey);
    let g: Graphics | undefined = this.dynamicPool.get(poolKey) as Graphics | undefined;
    if (!g || !(g instanceof Graphics)) {
      g = new Graphics();
      this.dynamicPool.set(poolKey, g);
      this.scene.addChild(g);
    } else {
      g.clear();
    }
    g.zIndex = depthZ;
    return g;
  }

  /**
   * Returns this frame's piece (Sprite for a textured slice, Graphics for a
   * procedural one, e.g. a shadow) for `baseKey:index` -- a reused object on
   * every frame but the first, pooled the same way as `pooledDynamic` (#256)
   * but keyed per visual piece so a multi-sprite entity (owner extension,
   * 2026-09-22: decoded Spectrum sprites for robots/commanders) doesn't
   * reallocate one `Sprite` per slice every frame.
   */
  private pooledPiece(baseKey: string, index: number, textured: boolean, usedKeys: Set<string>): Container {
    const key = `${baseKey}:${index}`;
    usedKeys.add(key);
    let g = this.dynamicPool.get(key);
    const wantsSprite = textured;
    if (!g || g instanceof Sprite !== wantsSprite) {
      // A piece index can switch kind mid-match (drawCommander emits its
      // shadow as piece 0 only while airborne, shifting every sprite slice
      // by one), so the replaced object must be destroyed here: leaving it
      // in `scene` painted a frozen copy of the commander at its lift-off
      // cell, and the frame sweep never saw it because the key stayed used.
      g?.destroy();
      g = wantsSprite ? new Sprite() : new Graphics();
      this.dynamicPool.set(key, g);
      this.scene.addChild(g);
    } else if (!wantsSprite) {
      (g as Graphics).clear();
    }
    return g;
  }

  private label(_id: string, comps: MapComponent[], text: string, color: number): void {
    const cx = comps.reduce((s, c) => s + c.x, 0) / comps.length;
    const cy = comps.reduce((s, c) => s + c.y, 0) / comps.length;
    const top = Math.max(...comps.map((c) => c.height));
    const p = project(cx, cy, top + 3);
    const t = new Text({ text, style: { fontFamily: 'monospace', fontSize: 10, fill: color } });
    t.anchor.set(0.5, 1);
    this.labelAt(t, p);
    this.structureLabels.addChild(t);
  }

  // ---- per-frame ----

  render(state: AppState, nowMs: number): void {
    const snap = state.latest;
    if (state.connection.resyncGeneration !== this.lastResync) {
      // Full replacement: drop any effect or diff memory derived from stale state.
      this.lastResync = state.connection.resyncGeneration;
      this.fx = [];
      this.prevSnapshot = null;
    }
    this.zoom = viewZoom(this.app.screen.width, this.app.screen.height);
    const text = textOverlays(state.ui);
    this.drawStructures(snap, text.structureNames);
    const usedDynamicKeys = new Set<string>();
    this.effects.clear();
    this.overlay.clear();
    this.overlayLabels.removeChildren().forEach((t) => t.destroy());
    if (!snap) {
      this.updateTerrain();
      this.sweepDynamicPool(usedDynamicKeys);
      return;
    }

    const frozen = state.lifecycle.phase !== 'active';
    const tick = displayTick(snap.tick, state.ui.latestSnapshotAtMs, nowMs, frozen);
    this.diffForEffects(snap, nowMs);

    const me = state.connection.session?.playerId ?? null;
    const destroyed = new Set([...snap.structure_destruction, ...snap.scenery_debris]);
    const robotPos = new Map<string, { x: number; y: number; top: number }>();

    for (const r of snap.robots) {
      const mv = isGridTransition(r.movement) ? r.movement : null;
      const p = interpolateGrid(r.x, r.y, mv, tick);
      // Robots stand on the terrain under them (CR002.25, `Lcee8`).
      const ground = robotGround(this.surface, r.x, r.y, mv, tick, destroyed);
      robotPos.set(r.entity_id, { x: p.x, y: p.y, top: ground + r.height });
      // Sliced one cell at a time (CR002.3/4), like structures and scenery,
      // so a 2×2 body that partly overlaps another one occludes correctly
      // cell-by-cell instead of by a single whole-body anchor key (#242).
      // No CO_LOCATED_TIE_BIAS here: robots are grounded, structure-like
      // bodies, so they win ties the same way a war base or factory does.
      // Sliced sprite piece (owner extension, 2026-09-22): drawRobotStack
      // already zIndexes each visual piece per its own sub-footprint offset
      // (slice.dx/dy), a finer-grained version of the per-footprint-cell
      // occlusion #242/#244 introduced, so one call per robot (pooled per
      // piece index, #256) replaces the outer per-cell loop.
      drawRobotStack(p.x, p.y, r.stack as ModuleId[], r.owner, { totalHeight: r.height, ground, facing: r.facing, mine: r.owner === me }, (i, textured) =>
        this.pooledPiece(`robot:${r.entity_id}`, i, textured, usedDynamicKeys),
      );
      if (r.owner !== me) {
        // Enemy marker ring so ownership stays readable at distance. Sliced
        // per footprint cell like the body (#248): this ring kept the old
        // whole-body, anchor-only key pattern the body fix (#242/#244)
        // replaced, so it could still draw over/under a structure or robot
        // the body itself now correctly occludes — flickering against it
        // frame to frame whenever the anchor cell's own comparison result
        // (correct) disagreed with the ring's (stale, anchor-only) one.
        unitFootprintCells(p.x, p.y).forEach((cell, i) => {
          const g = this.pooledDynamic(`ring:${r.entity_id}:${i}`, depthKey(cell.x, cell.y, ground), usedDynamicKeys);
          drawDiamond(g, cell.x, cell.y, ownerColor(r.owner), 0, ownerColor(r.owner), ground, 1);
        });
      }
      if (text.robotStrength) {
        const centre = unitCentre(p.x, p.y);
        const sp = project(centre.x, centre.y, ground + r.height + 3);
        const label = new Text({ text: `${r.strength}`, style: { fontFamily: 'monospace', fontSize: 9, fill: ownerColor(r.owner) } });
        label.anchor.set(0.5, 1);
        this.labelAt(label, sp);
        this.overlayLabels.addChild(label);
      }
    }

    for (const c of snap.commanders) {
      let x = c.x;
      let y = c.y;
      let alt = c.altitude;
      const docked = c.mode === 'docked' && !!c.docked_robot_id && robotPos.has(c.docked_robot_id);
      if (docked) {
        // Riding the robot: drawn on its drawn top (terrain + stack), which
        // is the authoritative altitude whenever the robot is at rest.
        const rp = robotPos.get(c.docked_robot_id!)!;
        x = rp.x;
        y = rp.y;
        alt = rp.top;
      } else {
        const ht = isGridTransition(c.horizontal_transition) ? c.horizontal_transition : null;
        const vt = isVerticalTransition(c.vertical_transition) ? c.vertical_transition : null;
        const p = interpolateGrid(c.x, c.y, ht, tick);
        x = p.x;
        y = p.y;
        alt = interpolateAltitude(c.altitude, vt, tick);
      }
      // Docked: resting on the robot top, so no separate shadow.
      // The shadow falls on the highest surface under the 2×2 body (CR002.4).
      const surfaceZ = docked ? alt : Math.min(alt, this.surface.underUnit(x, y, destroyed));
      // Sliced one cell at a time, like robots (#242): a single anchor-only
      // key (formerly biased +0.5 to always win ties) put the commander in
      // front even when it stood behind a robot or warbase it partly
      // overlapped by one cell. Per-cell keys let altitude decide the order
      // at a shared cell (e.g. a docked commander sits above the robot's
      // stack there because its altitude is higher); CO_LOCATED_TIE_BIAS
      // covers the remaining tie where the commander's altitude can't go
      // low enough to sort strictly under a co-located structure's base.
      // Sliced sprite piece (owner extension, 2026-09-22), same reasoning
      // as the robot body above: one call per commander, pooled per piece.
      drawCommander(
        x,
        y,
        alt,
        c.player_id,
        surfaceZ,
        CO_LOCATED_TIE_BIAS,
        (i, textured) => this.pooledPiece(`commander:${c.player_id}`, i, textured, usedDynamicKeys),
        c.player_id === me,
      );
      if (c.player_id === me) {
        // Locked to the interpolated position (CR003.5): no lag, no overshoot.
        const centre = unitCentre(x, y);
        this.cam.x = centre.x;
        this.cam.y = centre.y;
      }
    }

    for (const pr of snap.projectiles) {
      // Projectiles advance 2 cells per 4-tick engine cadence; between
      // cadence ticks we slide them toward their next authoritative cell.
      // Its (x, y) anchors a 2×2 body like a robot's (CR002.3): draw it at the
      // body centre, shadowed on the highest piece under the body.
      const { x, y } = interpolateProjectile(pr, snap.tick, tick);
      const centre = unitCentre(x, y);
      const sh = project(centre.x, centre.y, Math.min(pr.z, this.surface.underUnit(x, y, destroyed)));
      const key = `projectile:${pr.id}`;
      const depth = depthKey(x, y, pr.z);
      const g = this.pooledPiece(key, 0, false, usedDynamicKeys) as Graphics;
      g.zIndex = depth;
      g.circle(sh.x, sh.y, 1).fill({ color: 0x000000, alpha: 0.4 });
      // The Spectrum draws a bullet from its own art, one sprite per weapon
      // per travel axis (`Lcec3_draw_robot_or_bullet_internal`). A weapon with
      // no bullet art there -- the nuke, which detonates where it stands --
      // keeps the plain marker.
      const axis = bulletAxis(pr.dy);
      const texture = bulletTexture(pr.weapon, axis);
      if (texture) {
        const sprite = this.pooledPiece(key, 1, true, usedDynamicKeys) as Sprite;
        sprite.texture = texture;
        const origin = spriteOriginFor(bulletRows(pr.weapon, axis)!, BULLET_FOOTPRINT, { x, y }, pr.z);
        sprite.position.set(origin.x, origin.y);
        sprite.zIndex = depth;
      } else {
        const p = project(centre.x, centre.y, pr.z);
        g.circle(p.x, p.y, 2.5).fill(colorFor(`projectile.${pr.weapon}` as SemanticAsset));
      }
    }

    this.drawEffects(nowMs);
    if (state.ui.debugGrid) this.drawDebug(snap);
    this.applyCamera(menuColumnShown(state) ? menuColumnPx() : 0);
    this.updateTerrain();
    this.cullStructures();
    this.sweepDynamicPool(usedDynamicKeys);
  }

  /** Destroys and drops pooled dynamic Graphics for entities no longer in this frame's snapshot. */
  private sweepDynamicPool(usedKeys: Set<string>): void {
    for (const [key, g] of this.dynamicPool) {
      if (usedKeys.has(key)) continue;
      g.destroy();
      this.dynamicPool.delete(key);
    }
  }

  private diffForEffects(snap: SnapshotState, nowMs: number): void {
    const prev = this.prevSnapshot;
    this.prevSnapshot = snap;
    if (!prev || snap.tick <= prev.tick) return;
    for (const p of prev.projectiles) {
      if (!snap.projectiles.some((q) => q.id === p.id)) this.fx.push({ ...unitCentre(p.x + p.dx, p.y + p.dy), z: p.z, kind: 'hit', startMs: nowMs, durationMs: 250 });
    }
    for (const r of prev.robots) {
      if (!snap.robots.some((q) => q.entity_id === r.entity_id)) {
        const ground = this.surface.underUnit(r.x, r.y, new Set([...prev.structure_destruction, ...prev.scenery_debris]));
        this.fx.push({ ...unitCentre(r.x, r.y), z: ground + r.height / 2, kind: 'explosion', startMs: nowMs, durationMs: 600 });
      }
    }
    for (const id of snap.structure_destruction) {
      if (!prev.structure_destruction.includes(id)) {
        const comps = [...this.map.war_bases, ...this.map.factories].find((s) => s.id === id)?.components ?? [];
        if (comps.length) {
          const cx = comps.reduce((s, c) => s + c.x, 0) / comps.length;
          const cy = comps.reduce((s, c) => s + c.y, 0) / comps.length;
          this.fx.push({ x: cx, y: cy, z: 4, kind: 'nuclear', startMs: nowMs, durationMs: 1200 });
        }
      }
    }
  }

  private drawEffects(nowMs: number): void {
    this.fx = this.fx.filter((f) => nowMs - f.startMs < f.durationMs);
    for (const f of this.fx) {
      const t = (nowMs - f.startMs) / f.durationMs;
      const p = project(f.x, f.y, f.z);
      if (f.kind === 'hit') this.effects.circle(p.x, p.y, 2 + t * 4).fill({ color: PALETTE.brightWhite, alpha: 1 - t });
      else if (f.kind === 'explosion') this.effects.circle(p.x, p.y, 4 + t * 10).fill({ color: PALETTE.brightRed, alpha: 1 - t });
      else this.effects.circle(p.x, p.y, 10 + t * 8 * TILE_W).fill({ color: PALETTE.brightYellow, alpha: 0.7 * (1 - t) });
    }
  }

  private drawDebug(snap: SnapshotState): void {
    const g = this.overlay;
    const x0 = Math.max(0, Math.floor(this.cam.x) - 40);
    const x1 = Math.min(this.map.width, Math.floor(this.cam.x) + 40);
    for (let y = 0; y <= this.map.height; y++) {
      const a = project(x0, y);
      const b = project(x1, y);
      g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ color: 0xffffff, alpha: 0.25, width: 1 });
    }
    for (let x = x0; x <= x1; x++) {
      const a = project(x, 0);
      const b = project(x, this.map.height);
      g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ color: 0xffffff, alpha: 0.25, width: 1 });
      if (x % 4 === 0) {
        const t = new Text({ text: `${x}`, style: { fontFamily: 'monospace', fontSize: 8, fill: 0xffffff } });
        const p = project(x, -0.5);
        t.anchor.set(0.5, 1);
        this.labelAt(t, p);
        this.overlayLabels.addChild(t);
      }
    }
    for (const ip of this.map.interaction_points) {
      const col = ip.kind === 'heli_pad' ? PALETTE.brightGreen : ip.kind === 'exit' ? PALETTE.brightYellow : PALETTE.brightCyan;
      for (const c of footprintCells(ip)) drawDiamond(g, c.x, c.y, col, 0.5, undefined, surfaceHeightAt(this.map, c.x, c.y));
    }
    for (const cp of snap.capture_progress) {
      const comps = [...this.map.war_bases, ...this.map.factories].find((s) => s.id === cp.structure_id)?.components ?? [];
      for (const c of comps) drawDiamond(g, c.x, c.y, ownerColor(cp.capturing_player), 0.2 * (cp.elapsed_ticks / cp.required_ticks) + 0.1);
    }
  }

  /** `columnPx`: screen width the docked menu column covers on the right (CR003.11). */
  private applyCamera(columnPx: number): void {
    const p = project(this.cam.x, this.cam.y);
    const c = playViewCentre(this.app.screen.width, this.app.screen.height, columnPx);
    this.world.scale.set(this.zoom);
    // Centre slightly above the ground point so standing entities sit mid-view.
    this.world.position.set(Math.round(c.x - p.x * this.zoom), Math.round(c.y - (p.y - TILE_H) * this.zoom));
    this.labels.position.copyFrom(this.world.position);
  }

  /** Place unscaled text at a world-space point. */
  private labelAt(t: Text, p: ScreenPoint): void {
    t.position.set(p.x * this.zoom, p.y * this.zoom);
  }

  /** Cell under a screen point, for click-to-target aiming. */
  screenToCell(sx: number, sy: number): { x: number; y: number } {
    const w = unproject((sx - this.world.position.x) / this.zoom, (sy - this.world.position.y) / this.zoom);
    return { x: Math.round(w.x), y: Math.round(w.y) };
  }
}

/** Spectrum flag sprite at native size (world units are Spectrum pixels), pole foot on the roof centre. */
function drawFlag(g: Graphics, x: number, y: number, z: number, owner: FlagOwner): void {
  const rows = FLAG_SPRITES[owner];
  const foot = project(x, y, z);
  const left = Math.round(foot.x) - FLAG_POLE_COLUMN;
  const top = Math.round(foot.y) - rows.length;
  rows.forEach((row, r) => {
    for (let col = 0; col < row.length; col++) {
      if (row[col] === ' ') continue;
      g.rect(left + col, top + r, 1, 1).fill(row[col] === '#' ? PALETTE.black : ownerColor(owner));
    }
  });
}
