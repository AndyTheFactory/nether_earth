// PixiJS world renderer (M8.2/M8.3/M8.8). Reads the store; never writes it.
import { Application, Container, Graphics, Text } from 'pixi.js';
import type { SnapshotState } from '../../../protocol/generated/types';
import type { AppState } from '../state/store.ts';
import type { MapData, MapComponent } from '../world/map.ts';
import { surfaceHeightAt, terrainAt } from '../world/map.ts';
import { CELL_H, CELL_W, depthKey, project } from './projection.ts';
import { displayTick, interpolateAltitude, interpolateGrid, interpolateProjectile, isGridTransition, isVerticalTransition } from './interpolation.ts';
import { drawPrism, drawDiamond } from './prism.ts';
import { drawRobotStack, drawCommander, type ModuleId } from './robot.ts';
import { colorFor, ownerColor, PALETTE, shade, type SemanticAsset } from './assets.ts';

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
  private structures = new Graphics();
  private structureLabels = new Container();
  private entities = new Container();
  private projectiles = new Graphics();
  private effects = new Graphics();
  private overlay = new Graphics();
  private overlayLabels = new Container();
  private cam = { x: 24, y: 8 };
  private lastStructureKey = '';
  private lastResync = -1;
  private prevSnapshot: SnapshotState | null = null;
  private fx: Effect[] = [];

  constructor(
    private readonly app: Application,
    private readonly map: MapData,
  ) {
    this.world.addChild(this.terrain, this.structures, this.structureLabels, this.entities, this.projectiles, this.effects, this.overlay, this.overlayLabels);
    app.stage.addChild(this.world);
    this.drawTerrain();
  }

  // ---- static layers ----

  private drawTerrain(): void {
    const g = this.terrain;
    g.clear();
    for (let y = 0; y < this.map.height; y++) {
      for (let x = 0; x < this.map.width; x++) {
        const t = terrainAt(this.map, x, y);
        const id = `terrain.${t}` as SemanticAsset;
        const base = colorFor(id);
        drawDiamond(g, x, y, (x + y) % 2 ? base : shade(base, 1.12));
      }
    }
  }

  private drawStructures(state: SnapshotState | null): void {
    const key = state ? JSON.stringify([state.structure_ownership, state.structure_destruction]) : 'none';
    if (key === this.lastStructureKey) return;
    this.lastStructureKey = key;
    const g = this.structures;
    g.clear();
    this.structureLabels.removeChildren();
    const owner = (id: string) => state?.structure_ownership.find((o) => o.structure_id === id)?.owner ?? null;
    const destroyed = (id: string) => state?.structure_destruction.includes(id) ?? false;

    const blocks: { c: MapComponent; color: number; dead: boolean }[] = [];
    for (const wb of this.map.war_bases) {
      const col = ownerColor(owner(wb.id));
      for (const c of wb.components) blocks.push({ c, color: col, dead: destroyed(wb.id) });
      this.label(wb.id, wb.components, `WAR BASE ${owner(wb.id) ?? 'neutral'}${destroyed(wb.id) ? ' ✕' : ''}`, col);
    }
    for (const f of this.map.factories) {
      const col = shade(colorFor('structure.factory'), owner(f.id) ? 1.2 : 0.9);
      for (const c of f.components) blocks.push({ c, color: col, dead: destroyed(f.id) });
      this.label(f.id, f.components, `${f.factory_type.toUpperCase()} ${owner(f.id) ?? 'neutral'}${destroyed(f.id) ? ' ✕' : ''}`, ownerColor(owner(f.id)));
    }
    for (const b of this.map.blockers) {
      for (const c of b.components) blocks.push({ c, color: colorFor('structure.blocker'), dead: false });
    }
    // Heli-pads sit on the war-base roof (open-questions §18): mark the pad
    // cell's top face right after its prism so nearer blocks still occlude it.
    const pads = new Set(this.map.interaction_points.filter((ip) => ip.kind === 'heli_pad').map((ip) => `${ip.footprint.x},${ip.footprint.y}`));
    blocks.sort((a, b) => depthKey(a.c.x, a.c.y) - depthKey(b.c.x, b.c.y));
    for (const { c, color, dead } of blocks) {
      if (dead) drawPrism(g, c.x, c.y, 0, 1, shade(color, 0.3), 0.8);
      else {
        drawPrism(g, c.x, c.y, 0, c.height, color);
        if (pads.has(`${c.x},${c.y}`)) drawDiamond(g, c.x, c.y, PALETTE.brightGreen, 0.9, PALETTE.white, c.height);
      }
    }
  }

  private label(_id: string, comps: MapComponent[], text: string, color: number): void {
    const cx = comps.reduce((s, c) => s + c.x, 0) / comps.length;
    const cy = comps.reduce((s, c) => s + c.y, 0) / comps.length;
    const top = Math.max(...comps.map((c) => c.height));
    const p = project(cx, cy, top + 3);
    const t = new Text({ text, style: { fontFamily: 'monospace', fontSize: 10, fill: color } });
    t.anchor.set(0.5, 1);
    t.position.set(p.x, p.y);
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
    this.drawStructures(snap);
    this.entities.removeChildren();
    this.projectiles.clear();
    this.effects.clear();
    this.overlay.clear();
    this.overlayLabels.removeChildren();
    if (!snap) return;

    const frozen = state.lifecycle.phase !== 'active';
    const tick = displayTick(snap.tick, state.ui.latestSnapshotAtMs, nowMs, frozen);
    this.diffForEffects(snap, nowMs);

    const me = state.connection.session?.playerId ?? null;
    const items: { key: number; g: Graphics }[] = [];
    const robotPos = new Map<string, { x: number; y: number; height: number }>();

    for (const r of snap.robots) {
      const mv = isGridTransition(r.movement) ? r.movement : null;
      const p = interpolateGrid(r.x, r.y, mv, tick);
      robotPos.set(r.entity_id, { x: p.x, y: p.y, height: r.height });
      const g = new Graphics();
      drawRobotStack(g, p.x, p.y, r.stack as ModuleId[], r.owner, { totalHeight: r.height });
      if (r.owner !== me) {
        // enemy marker ring so ownership stays readable at distance
        drawDiamond(g, p.x, p.y, ownerColor(r.owner), 0, ownerColor(r.owner));
      }
      items.push({ key: depthKey(p.x, p.y), g });
      const sp = project(p.x, p.y, r.height + 3);
      const label = new Text({ text: `${r.strength}`, style: { fontFamily: 'monospace', fontSize: 9, fill: ownerColor(r.owner) } });
      label.anchor.set(0.5, 1);
      label.position.set(sp.x, sp.y);
      this.overlayLabels.addChild(label);
    }

    for (const c of snap.commanders) {
      let x = c.x;
      let y = c.y;
      let alt = c.altitude;
      if (c.mode === 'docked' && c.docked_robot_id && robotPos.has(c.docked_robot_id)) {
        const rp = robotPos.get(c.docked_robot_id)!;
        x = rp.x;
        y = rp.y;
      } else {
        const ht = isGridTransition(c.horizontal_transition) ? c.horizontal_transition : null;
        const vt = isVerticalTransition(c.vertical_transition) ? c.vertical_transition : null;
        const p = interpolateGrid(c.x, c.y, ht, tick);
        x = p.x;
        y = p.y;
        alt = interpolateAltitude(c.altitude, vt, tick);
      }
      const g = new Graphics();
      drawCommander(g, x, y, alt, c.player_id);
      items.push({ key: depthKey(x, y, alt) + 0.5, g });
      if (c.player_id === me) {
        this.cam.x += (x - this.cam.x) * 0.15;
        this.cam.y += (y - this.cam.y) * 0.15;
      }
    }

    items.sort((a, b) => a.key - b.key);
    for (const it of items) this.entities.addChild(it.g);

    for (const pr of snap.projectiles) {
      // Projectiles advance 2 cells per 4-tick engine cadence; between
      // cadence ticks we slide them toward their next authoritative cell.
      const { x, y } = interpolateProjectile(pr, snap.tick, tick);
      const p = project(x, y, pr.z);
      const col = colorFor(`projectile.${pr.weapon}` as SemanticAsset);
      this.projectiles.circle(p.x, p.y, pr.weapon === 'nuclear' ? 5 : 3).fill(col);
      const sh = project(x, y, 0);
      this.projectiles.circle(sh.x, sh.y, 2).fill({ color: 0x000000, alpha: 0.4 });
    }

    this.drawEffects(nowMs);
    if (state.ui.debugGrid) this.drawDebug(snap);
    this.applyCamera();
  }

  private diffForEffects(snap: SnapshotState, nowMs: number): void {
    const prev = this.prevSnapshot;
    this.prevSnapshot = snap;
    if (!prev || snap.tick <= prev.tick) return;
    for (const p of prev.projectiles) {
      if (!snap.projectiles.some((q) => q.id === p.id)) this.fx.push({ x: p.x + p.dx, y: p.y + p.dy, z: p.z, kind: 'hit', startMs: nowMs, durationMs: 250 });
    }
    for (const r of prev.robots) {
      if (!snap.robots.some((q) => q.entity_id === r.entity_id)) this.fx.push({ x: r.x, y: r.y, z: r.height / 2, kind: 'explosion', startMs: nowMs, durationMs: 600 });
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
      if (f.kind === 'hit') this.effects.circle(p.x, p.y, 4 + t * 8).fill({ color: PALETTE.brightWhite, alpha: 1 - t });
      else if (f.kind === 'explosion') this.effects.circle(p.x, p.y, 8 + t * 20).fill({ color: PALETTE.brightRed, alpha: 1 - t });
      else this.effects.circle(p.x, p.y, 20 + t * 16 * CELL_W).fill({ color: PALETTE.brightYellow, alpha: 0.7 * (1 - t) });
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
        t.position.set(p.x, p.y);
        this.overlayLabels.addChild(t);
      }
    }
    for (const ip of this.map.interaction_points) {
      const col = ip.kind === 'heli_pad' ? PALETTE.brightGreen : ip.kind === 'exit' ? PALETTE.brightYellow : PALETTE.brightCyan;
      drawDiamond(g, ip.footprint.x, ip.footprint.y, col, 0.5, undefined, surfaceHeightAt(this.map, ip.footprint.x, ip.footprint.y));
    }
    for (const cp of snap.capture_progress) {
      const comps = [...this.map.war_bases, ...this.map.factories].find((s) => s.id === cp.structure_id)?.components ?? [];
      for (const c of comps) drawDiamond(g, c.x, c.y, ownerColor(cp.capturing_player), 0.2 * (cp.elapsed_ticks / cp.required_ticks) + 0.1);
    }
  }

  private applyCamera(): void {
    const p = project(this.cam.x, this.cam.y);
    this.world.position.set(this.app.screen.width / 2 - p.x, this.app.screen.height / 2 - p.y - CELL_H * 2);
  }

  /** Cell under a screen point, for click-to-target aiming. */
  screenToCell(sx: number, sy: number): { x: number; y: number } {
    const lx = sx - this.world.position.x;
    const ly = sy - this.world.position.y;
    const a = lx / (CELL_W / 2);
    const b = ly / (CELL_H / 2);
    return { x: Math.round((a + b) / 2), y: Math.round((b - a) / 2) };
  }
}
