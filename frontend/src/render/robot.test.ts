// Robots stand on the terrain under them (CR002.25): the Spectrum draws a
// robot at ROBOT_STRUCT_ALTITUDE (`Lcee8_draw_robot_to_buffer`), the highest
// map piece under its 2×2 body (`Lb5d6_map_altitude_2x2`), and the ship docks
// on height + altitude. Anchors match engine/tests/test_robot_terrain_height.py.
//
// Sprite textures need a real 2D canvas backend, which the test environment
// doesn't provide (happy-dom has no `canvas` package installed) -- the same
// reason scenery.test.ts never calls renderer.ts's sceneryTexturesFor(). We
// stub `pixelTexture` with a no-op Texture so drawRobotStack/drawCommander's
// real slicing, positioning and zIndex logic still runs end to end.
import { test, vi } from 'vitest';
import assert from 'node:assert/strict';
import { Graphics, Sprite, Texture } from 'pixi.js';
import { SurfaceMap } from './surface.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';

vi.mock('./sprite-slice.ts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./sprite-slice.ts')>()),
  pixelTexture: () => Texture.EMPTY,
}));

const { drawCommander, drawRobotStack, robotGround, UNIT_SIZE } = await import('./robot.ts');

const surface = new SurfaceMap(loadMap(DEFAULT_MAP_ID));

// Both draw functions now take pieces from a pool (#256/owner extension,
// 2026-09-22) instead of returning a fresh array each call. This collects
// every piece a call requests, mirroring the old array-return shape so the
// tests below can assert on it the same way.
function collect(): { objects: (Graphics | Sprite)[]; getPiece: (i: number, textured: boolean) => Graphics | Sprite } {
  const objects: (Graphics | Sprite)[] = [];
  return {
    objects,
    getPiece: (_i, textured) => {
      const o = textured ? new Sprite() : new Graphics();
      objects.push(o);
      return o;
    },
  };
}

test('a robot at rest stands on the terrain under its 2×2 body', () => {
  assert.equal(robotGround(surface, 30, 12, null, 0), 0); // flat
  assert.equal(robotGround(surface, 32, 12, null, 0), 2); // rough types 2-5
  assert.equal(robotGround(surface, 54, 14, null, 0), 3); // rough types 6/7
  assert.equal(robotGround(surface, 167, 9, null, 0), 6); // mountain
});

test('mid-move the ground blends from the origin body to the destination body', () => {
  // Rough-2 (56, 14) -> rough-3 (55, 14), authoritative anchor still the origin.
  const move = { from_x: 56, from_y: 14, to_x: 55, to_y: 14, started_tick: 10, duration_ticks: 4 };
  assert.equal(robotGround(surface, 56, 14, move, 10), 2);
  assert.equal(robotGround(surface, 56, 14, move, 12), 2.5);
  assert.equal(robotGround(surface, 56, 14, move, 14), 3);
});

test('the stack is drawn raised by the ground and its top is ground + height', () => {
  const { objects, getPiece } = collect();
  const top = drawRobotStack(167, 9, ['tracks', 'cannon'], 'p1', { totalHeight: 13, ground: 6 }, getPiece);
  assert.equal(top, 19);
  // The ground shadow diamond is the first object, drawn at elevation 6.
  const shadow = objects[0] as Graphics;
  assert.ok(shadow instanceof Graphics);
});

test('without a ground the stack stands on the map floor', () => {
  const { getPiece } = collect();
  const top = drawRobotStack(30, 12, ['bipod', 'cannon'], 'p1', { totalHeight: 17 }, getPiece);
  assert.equal(top, 17);
});

test('visual piece heights are the Spectrum Ld7b4 values, so the snapshot height needs no rescale', () => {
  // Tracks 7 + cannon 6 = 13 (CR003.3), the engine's derived height.
  assert.equal(drawRobotStack(30, 12, ['tracks', 'cannon'], 'p1', {}, collect().getPiece), 13);
  assert.equal(drawRobotStack(30, 12, ['bipod', 'missile', 'phaser', 'nuclear', 'electronics'], 'p1', {}, collect().getPiece), 38);
});

test('the stack is sliced one Sprite per footprint cell per piece, plus one shadow diamond', () => {
  const { objects, getPiece } = collect();
  drawRobotStack(30, 12, ['tracks', 'cannon'], 'p1', {}, getPiece);
  const sprites = objects.filter((o) => o instanceof Sprite);
  const shadows = objects.filter((o) => o instanceof Graphics);
  assert.equal(shadows.length, 1);
  // Each piece slices into footprint[0] * footprint[1] = UNIT_SIZE^2 pieces.
  assert.equal(sprites.length, 2 * UNIT_SIZE * UNIT_SIZE);
});

test('every object carries a finite zIndex for the shared scene painter order', () => {
  const { objects, getPiece } = collect();
  drawRobotStack(30, 12, ['bipod', 'electronics'], 'p2', { ground: 3 }, getPiece);
  for (const o of objects) assert.ok(Number.isFinite(o.zIndex));
});

test('the commander is sliced the same way, with a shadow only when airborne', () => {
  const g1 = collect();
  drawCommander(30, 12, 0, 'p1', 0, 0, g1.getPiece);
  assert.equal(g1.objects.filter((o) => o instanceof Graphics).length, 0);
  const g2 = collect();
  drawCommander(30, 12, 8, 'p1', 0, 0, g2.getPiece);
  assert.equal(g2.objects.filter((o) => o instanceof Graphics).length, 1);
  assert.equal(g2.objects.filter((o) => o instanceof Sprite).length, UNIT_SIZE * UNIT_SIZE);
});

test('a commander piece index keeps its kind across take-off and landing', () => {
  // Regression for the lift-off "trace": the renderer pools pieces by index,
  // so an index that switches Graphics <-> Sprite orphans the object it
  // replaces under a still-used key and it stays painted on the map.
  const kinds = (altitude: number) => {
    const seen: boolean[] = [];
    drawCommander(30, 12, altitude, 'p1', 0, 0, (i, textured) => {
      seen[i] = textured;
      return textured ? new Sprite(Texture.EMPTY) : new Graphics();
    });
    return seen;
  };
  const grounded = kinds(0);
  const airborne = kinds(8);
  for (let i = 0; i < grounded.length; i++) assert.equal(airborne[i], grounded[i]);
});
