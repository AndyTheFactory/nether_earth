// Bullet presentation from the decoded Spectrum art (bullet-sprites.ts).
//
// `Lcec3_draw_robot_or_bullet_internal` picks a bullet's sprite from its type
// (1 cannon, 2 missiles, 3 phasers) and one bit of its direction: `cp 3 / ccf`
// puts "travelling south or north" into the low bit before `ld a, 43 / add a, c`.
// So there is one sprite per weapon per travel axis and no finer facing, and a
// weapon the Spectrum has no bullet for -- the nuke, which detonates where it
// stands rather than firing -- resolves to nothing here.
import { Texture } from 'pixi.js';
import { PALETTE } from './assets.ts';
import { pixelTexture } from './sprite-slice.ts';
import { BULLET_SPRITES, type BulletAxis } from './bullet-sprites.ts';

export type { BulletAxis };

/**
 * A bullet's sprite covers a 2x2 body like the robot that fired it (CR002.3),
 * so it is positioned by the same rule as every other decoded sprite.
 */
export const BULLET_FOOTPRINT: readonly [number, number] = [2, 2];

/** Travel axis of a step: along y for south/north, along x otherwise. */
export function bulletAxis(dy: number): BulletAxis {
  return dy !== 0 ? 'y' : 'x';
}

/** Decoded rows for a weapon's bullet, or `undefined` when it has no bullet art. */
export function bulletRows(weapon: string, axis: BulletAxis): readonly string[] | undefined {
  return BULLET_SPRITES[weapon]?.[axis];
}

const textures = new Map<string, Texture>();

/** Cached texture of a bullet sprite: black ink, transparent everywhere else. */
export function bulletTexture(weapon: string, axis: BulletAxis): Texture | undefined {
  const rows = bulletRows(weapon, axis);
  if (!rows) return undefined;
  const key = `${weapon}:${axis}`;
  let t = textures.get(key);
  if (!t) {
    t = pixelTexture(rows, PALETTE.black, null);
    textures.set(key, t);
  }
  return t;
}
