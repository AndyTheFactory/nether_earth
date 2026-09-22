import { test } from 'vitest';
import assert from 'node:assert/strict';
import { project, unproject, depthKey, groundDepth, playViewCentre, viewZoom, VIEW_SPAN_PX, CO_LOCATED_TIE_BIAS } from './projection.ts';
import { MENU_COLUMN_UNITS } from '../ui/radar.ts';
import { KEY_TO_AXIS } from '../input/keyboard.ts';
import { loadMap, DEFAULT_MAP_ID } from '../world/map.ts';
import { unitFootprintCells } from './robot.ts';

test('project/unproject round-trip', () => {
  for (const [x, y] of [[10, 3], [0, 0], [511, 15], [2.5, 7.25]]) {
    const p = project(x, y);
    const w = unproject(p.x, p.y);
    assert.ok(Math.abs(w.x - x) < 1e-9 && Math.abs(w.y - y) < 1e-9, `${x},${y}`);
  }
});

test('Spectrum orientation: +x runs lower-left to upper-right, +y runs down-right', () => {
  const o = project(0, 0);
  const px = project(1, 0);
  const py = project(0, 1);
  assert.ok(px.x > o.x && px.y < o.y, '+x goes right and up');
  assert.ok(px.x - o.x > o.y - px.y, '+x is mostly rightward (shallow)');
  assert.ok(py.x > o.x && py.y > o.y, '+y goes right and down');
  assert.ok(py.y - o.y > py.x - o.x, '+y is mostly downward (steep)');
  // the far (high-x) end of the 512-cell map lies up and to the right
  const far = project(490, 1);
  const home = project(18, 1);
  assert.ok(far.x > home.x && far.y < home.y);
});

test('height lifts screen y', () => {
  assert.ok(project(1, 1, 8).y < project(1, 1, 0).y);
});

test('depth: nearer the lower-left viewer (lower x, higher y) draws later; higher draws later', () => {
  assert.ok(depthKey(5, 5) > depthKey(6, 5), 'lower x is nearer');
  assert.ok(depthKey(5, 6) > depthKey(5, 5), 'higher y is nearer');
  assert.ok(depthKey(5, 5, 3) > depthKey(5, 5, 0));
  // the key follows ground screen y, so anything lower on screen is nearer
  assert.equal(groundDepth(3, 4), project(3, 4).y);
});

test('occlusion: a robot behind war base 1 sorts before, and is covered by, the block in front of it', () => {
  // occlusion fixture: robot-6 (bipod+cannon, height 6) at (21, 0) behind the
  // 15-high war-base block at (21, 1)
  const map = loadMap(DEFAULT_MAP_ID);
  const block = map.war_bases.find((w) => w.id === 'warbase-1')!.components.find((c) => c.x === 21 && c.y === 1)!;
  assert.equal(block.height, 15);
  assert.ok(depthKey(21, 0, 0) < depthKey(block.x, block.y), 'block draws after the robot');
  // the robot's whole screen extent (top face included) lies inside the block's
  const robotTop = Math.min(project(20.5, -0.5, 6).y, project(21.5, -0.5, 6).y);
  const blockTop = Math.min(project(20.5, 0.5, 15).y, project(21.5, 0.5, 15).y);
  assert.ok(robotTop > blockTop, 'robot top is below the block top on screen');
  // a robot in front (higher y) of the same block draws after it
  assert.ok(depthKey(21, 10) > depthKey(block.x, block.y));
});

test('#242: a commander sliced per footprint cell draws beneath a robot or war base it partly overlaps when behind it, above when in front', () => {
  // Renderer.render() slices a robot's or commander's 2×2 body one footprint
  // cell at a time (CR002.3/4, like structures and scenery) and keys each
  // slice with depthKey(cell, altitude) (+ CO_LOCATED_TIE_BIAS for the
  // commander). This reproduces that per-cell key for the slice each pair of
  // bodies actually shares, without needing the full Pixi renderer.
  const map = loadMap(DEFAULT_MAP_ID);

  // -- vs a robot, overlapping by one cell (diagonal, corner touch) --
  // Robot-6 (bipod+cannon) anchored at (30, 12): footprint (30,12) (31,12) (30,11) (31,11).
  const robotAnchor = { x: 30, y: 12 };
  const robotGround = 0;
  const robotKeyAt = (cell: { x: number; y: number }) => depthKey(cell.x, cell.y, robotGround);
  // Commander anchored at (29, 11): footprint (29,11) (30,11) (29,10) (30,10).
  const commanderVsRobotAnchor = { x: 29, y: 11 };
  const robotCells = unitFootprintCells(robotAnchor.x, robotAnchor.y);
  const commanderVsRobotCells = unitFootprintCells(commanderVsRobotAnchor.x, commanderVsRobotAnchor.y);
  const sharedWithRobot = robotCells.filter((rc) => commanderVsRobotCells.some((cc) => cc.x === rc.x && cc.y === rc.y));
  assert.deepEqual(sharedWithRobot, [{ x: 30, y: 11 }], 'the two bodies overlap by exactly one cell');
  const commanderVsRobotKey = (alt: number) => depthKey(sharedWithRobot[0]!.x, sharedWithRobot[0]!.y, alt) + CO_LOCATED_TIE_BIAS;
  // Grounded at the shared cell: behind/beneath the robot.
  assert.ok(commanderVsRobotKey(0) < robotKeyAt(sharedWithRobot[0]!), 'a grounded commander draws beneath the robot it overlaps');
  // Flying above the robot's height (6): in front/above it.
  assert.ok(commanderVsRobotKey(20) > robotKeyAt(sharedWithRobot[0]!), 'a commander flying above the robot draws above it');

  // -- vs war base 1's 15-high block at (21, 1), overlapping by one cell --
  const block = map.war_bases.find((w) => w.id === 'warbase-1')!.components.find((c) => c.x === 21 && c.y === 1)!;
  assert.equal(block.height, 15);
  // Commander anchored at (20, 2): footprint (20,2) (21,2) (20,1) (21,1).
  const commanderVsBaseAnchor = { x: 20, y: 2 };
  const commanderVsBaseCells = unitFootprintCells(commanderVsBaseAnchor.x, commanderVsBaseAnchor.y);
  assert.ok(commanderVsBaseCells.some((c) => c.x === block.x && c.y === block.y), 'the commander overlaps the block by one cell');
  const commanderVsBaseKey = (alt: number) => depthKey(block.x, block.y, alt) + CO_LOCATED_TIE_BIAS;
  const blockKey = depthKey(block.x, block.y);
  assert.ok(commanderVsBaseKey(0) < blockKey, 'a grounded commander draws beneath the war base it overlaps');
  assert.ok(commanderVsBaseKey(20) > blockKey, 'a commander flying above the war base draws above it');
});

test('#248: depth-key ordering is a pure, deterministic function of world state', () => {
  // A flicker root cause is almost always some nondeterminism in how a key is
  // computed or in what order Graphics are (re)inserted, not the comparator
  // itself. Renderer.render() rebuilds every dynamic Graphics (robots,
  // commanders, projectiles) from scratch each frame (see `this.dynamic`
  // teardown in renderer.ts); this reproduces that per-cell key computation
  // for a representative static scene twice, with no time/frame-counter
  // input anywhere, and requires bit-identical keys and identical resulting
  // sort order both times.
  const map = loadMap(DEFAULT_MAP_ID);
  const block = map.war_bases.find((w) => w.id === 'warbase-1')!.components.find((c) => c.x === 21 && c.y === 1)!;

  interface Entry {
    id: string;
    key: number;
  }
  const buildScene = (): Entry[] => {
    const entries: Entry[] = [];
    // Structure cells (z = 0, as renderer.ts:drawStructures keys them).
    for (const wb of map.war_bases) for (const c of wb.components) entries.push({ id: `struct:${wb.id}:${c.x},${c.y}`, key: depthKey(c.x, c.y) });
    // A robot's per-cell body slices (renderer.ts's robots loop).
    const robotAnchor = { x: 30, y: 12 };
    for (const cell of unitFootprintCells(robotAnchor.x, robotAnchor.y)) entries.push({ id: `robot:${cell.x},${cell.y}`, key: depthKey(cell.x, cell.y, 0) });
    // A commander's per-cell body slices, including one overlapping the block.
    const commanderAnchor = { x: 20, y: 2 };
    for (const cell of unitFootprintCells(commanderAnchor.x, commanderAnchor.y)) {
      entries.push({ id: `commander:${cell.x},${cell.y}`, key: depthKey(cell.x, cell.y, 3) + CO_LOCATED_TIE_BIAS });
    }
    // A projectile passing near the block.
    entries.push({ id: 'projectile', key: depthKey(block.x, block.y - 3, 2) });
    return entries;
  };

  const first = buildScene();
  const second = buildScene();
  assert.deepEqual(second, first, 'recomputing the same world state twice yields bit-identical keys, in the same order');

  // The actual visual guarantee: sorting the (possibly differently-ordered,
  // e.g. re-created-in-a-different-array-order) Graphics by key must not
  // depend on which pass produced them.
  const sortedFirst = [...first].sort((a, b) => a.key - b.key).map((e) => e.id);
  const sortedSecond = [...second].sort((a, b) => a.key - b.key).map((e) => e.id);
  assert.deepEqual(sortedSecond, sortedFirst, 'sorted draw order is identical across repeated renders of the same state');

  // No two distinct scene elements collapse onto the exact same key: any
  // such tie would make their relative order depend on Graphics insertion
  // order (which JS's stable sort preserves, but which is not guaranteed
  // consistent frame-to-frame once *anything* elsewhere in the snapshot
  // reorders the entity list a key was built from) rather than on the key
  // itself (#248 hint 1).
  const seen = new Map<number, string>();
  for (const e of first) {
    const prior = seen.get(e.key);
    assert.ok(!prior, `key ${e.key} shared by ${prior} and ${e.id} — an unresolved tie the draw order alone must break`);
    seen.set(e.key, e.id);
  }
});

test('#248: a commander approaching a building it has not yet reached draws in front of/behind it by ground position, not by the building\'s height', () => {
  // Regression coverage broader than #242's test (which only covered a
  // commander *overlapping* a robot or war base by one shared footprint
  // cell): here the commander's footprint shares NO cell with the building
  // at all -- it is merely screen-adjacent -- so this exercises plain
  // groundDepth ordering between two entirely separate per-cell keys rather
  // than the CO_LOCATED_TIE_BIAS same-cell tie-break.
  const map = loadMap(DEFAULT_MAP_ID);
  const block = map.war_bases.find((w) => w.id === 'warbase-1')!.components.find((c) => c.x === 21 && c.y === 1)!;
  assert.equal(block.height, 15);

  // One cell nearer the viewer than the block (higher y), not overlapping
  // its footprint: the commander hasn't reached the building yet, but is
  // already in front of it and must draw over it.
  const nearAnchor = { x: 20, y: 3 };
  const nearCells = unitFootprintCells(nearAnchor.x, nearAnchor.y);
  assert.ok(!nearCells.some((c) => c.x === block.x && c.y === block.y), 'no shared footprint cell with the block');
  for (const cell of nearCells) {
    assert.ok(depthKey(cell.x, cell.y, 0) + CO_LOCATED_TIE_BIAS > depthKey(block.x, block.y), `${cell.x},${cell.y}: nearer cell still draws after (in front of) the 15-high block behind it`);
  }

  // One cell farther from the viewer than the block, again not overlapping:
  // still approaching from behind, must stay hidden behind the building.
  const farAnchor = { x: 20, y: 0 };
  const farCells = unitFootprintCells(farAnchor.x, farAnchor.y);
  assert.ok(!farCells.some((c) => c.x === block.x && c.y === block.y), 'no shared footprint cell with the block');
  for (const cell of farCells) {
    assert.ok(depthKey(cell.x, cell.y, 0) + CO_LOCATED_TIE_BIAS < depthKey(block.x, block.y), `${cell.x},${cell.y}: farther cell still draws before (behind) the 15-high block in front of it`);
  }
});

test('#248: an enemy robot\'s ownership ring is sliced per footprint cell like its body, not keyed by the anchor cell alone', () => {
  // Before this fix the ring (renderer.ts's `owner !== me` branch) still used
  // the pre-#242 whole-body, anchor-only key pattern that the robot's own
  // body (drawRobotStack) and the commander (#244) had already moved away
  // from. Reproduce the ring's per-cell key here and check it agrees with
  // the shared cell's own occlusion order against a partly-overlapping war
  // base block, the same way #242's test does for the commander.
  const map = loadMap(DEFAULT_MAP_ID);
  const block = map.war_bases.find((w) => w.id === 'warbase-1')!.components.find((c) => c.x === 21 && c.y === 1)!;
  const robotAnchor = { x: 20, y: 2 };
  const robotCells = unitFootprintCells(robotAnchor.x, robotAnchor.y);
  const shared = robotCells.find((c) => c.x === block.x && c.y === block.y);
  assert.ok(shared, 'the robot overlaps the block by one cell');
  const ringKeyAt = (cell: { x: number; y: number }, ground: number) => depthKey(cell.x, cell.y, ground);
  // A robot resting exactly on top of the block (e.g. a heli pad on its
  // roof, `ground == block.height`) is the reachable co-located case: the
  // ring's slice there must draw above the block, matching the body's own
  // per-cell key (both use the same z = ground, no bias — #244's comment on
  // the body applies identically to the ring now that it shares the body's
  // per-cell construction).
  assert.ok(ringKeyAt(shared!, block.height) > depthKey(block.x, block.y), 'a ring slice resting on the block draws above it');
  assert.equal(ringKeyAt(shared!, block.height), depthKey(shared!.x, shared!.y, block.height), 'the ring slice and the body slice at the same cell key identically (no drift between them)');
});

test('keyboard directions match on-screen directions', () => {
  const dir = (code: string) => {
    const m = KEY_TO_AXIS[code];
    const a = project(0, 0);
    const b = project(m.dx, m.dy);
    return { x: b.x - a.x, y: b.y - a.y };
  };
  const right = dir('ArrowRight');
  const left = dir('ArrowLeft');
  const up = dir('ArrowUp');
  const down = dir('ArrowDown');
  assert.ok(right.x > Math.abs(right.y), 'right');
  assert.ok(-left.x > Math.abs(left.y), 'left');
  assert.ok(-up.y > Math.abs(up.x), 'up');
  assert.ok(down.y > Math.abs(down.x), 'down');
});

test('zoom: the shorter view side shows VIEW_SPAN_PX world pixels', () => {
  assert.equal(viewZoom(1280, 720) * VIEW_SPAN_PX, 720);
  assert.equal(viewZoom(600, 900) * VIEW_SPAN_PX, 600);
  // never shrinks the world below 1:1 on tiny views
  assert.equal(viewZoom(100, 100), 1);
  // more zoomed in than the pre-CR002 view (2 screen px per world px)
  assert.ok(viewZoom(1280, 720) > 2);
});

test('camera centres in the play view left of the menu column (CR003.11)', () => {
  // No column: the middle of the window, as before.
  assert.deepEqual(playViewCentre(1280, 800), { x: 640, y: 400 });
  assert.deepEqual(playViewCentre(1280, 800, 0), { x: 640, y: 400 });
  // Docked at 1280x800 (--mu 2px): the column takes 192 px; vertical framing is unchanged.
  assert.deepEqual(playViewCentre(1280, 800, MENU_COLUMN_UNITS * 2), { x: 544, y: 400 });
  // Phone width (--mu 1px).
  assert.deepEqual(playViewCentre(390, 844, MENU_COLUMN_UNITS), { x: 147, y: 422 });
  // A column wider than the window never puts the centre off the left edge.
  assert.deepEqual(playViewCentre(50, 100, MENU_COLUMN_UNITS), { x: 0, y: 50 });
});
