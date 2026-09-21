// Robots stand on the terrain under them (CR002.25): the Spectrum draws a
// robot at ROBOT_STRUCT_ALTITUDE (`Lcee8_draw_robot_to_buffer`), the highest
// map piece under its 2×2 body (`Lb5d6_map_altitude_2x2`), and the ship docks
// on height + altitude. Anchors match engine/tests/test_robot_terrain_height.py.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import type { Graphics } from 'pixi.js';
import { drawRobotStack, robotGround } from './robot.ts';
import { SurfaceMap } from './surface.ts';
import { project } from './projection.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';

const surface = new SurfaceMap(loadMap(DEFAULT_MAP_ID));

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

interface Poly {
  points: number[];
}

function recorder(): { g: Graphics; polys: Poly[] } {
  const polys: Poly[] = [];
  const g = {
    poly(points: number[]) {
      polys.push({ points });
      return g;
    },
    fill() {
      return g;
    },
    stroke() {
      return g;
    },
  };
  return { g: g as unknown as Graphics, polys };
}

test('the stack is drawn raised by the ground and its top is ground + height', () => {
  const { g, polys } = recorder();
  const top = drawRobotStack(g, 167, 9, ['tracks', 'cannon'], 'p1', { totalHeight: 6, ground: 6 });
  assert.equal(top, 12);
  // The owner tile under the robot lies on the mountain, not on the map floor:
  // its front-left corner is (x - 0.5, y + 0.5) projected at z = 6.
  const corner = project(166.5, 9.5, 6);
  const tile = polys[0].points;
  assert.deepEqual([tile[6], tile[7]], [corner.x, corner.y]);
});

test('without a ground the stack stands on the map floor', () => {
  const { g } = recorder();
  assert.equal(drawRobotStack(g, 30, 12, ['bipod', 'cannon'], 'p1', { totalHeight: 6 }), 6);
});
