// Bullet sprite selection (Lcec3_draw_robot_or_bullet_internal): one sprite
// per weapon per travel axis, nothing finer, and nothing for the nuke.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { bulletAxis, bulletRows } from './bullets.ts';

test('the travel axis is y for south/north and x otherwise', () => {
  assert.equal(bulletAxis(1), 'y');
  assert.equal(bulletAxis(-1), 'y');
  assert.equal(bulletAxis(0), 'x');
});

test('each firing weapon has a distinct sprite per axis', () => {
  const seen = new Set<string>();
  for (const weapon of ['cannon', 'missile', 'phaser']) {
    for (const axis of ['x', 'y'] as const) {
      const rows = bulletRows(weapon, axis);
      assert.ok(rows && rows.length > 0, `no bullet sprite for ${weapon} along ${axis}`);
      assert.ok(rows!.some((r) => r.includes('#')), `${weapon}/${axis} sprite is blank`);
      seen.add(rows!.join('\n'));
    }
  }
  assert.equal(seen.size, 6, 'two bullet sprites are the same pixels');
});

test('the nuke has no bullet sprite: it detonates where it stands', () => {
  assert.equal(bulletRows('nuclear', 'x'), undefined);
  assert.equal(bulletRows('nuclear', 'y'), undefined);
});
