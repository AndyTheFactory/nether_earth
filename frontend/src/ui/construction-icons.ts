// Module icons for the ROBOT CONSTRUCTION screen: the robot's own decoded
// Spectrum piece sprites (robot-sprites.ts), facing west (owner request,
// 2026-09-24), so the screen shows the parts the robot is actually built
// from. They used to be hand-drawn here from iso primitives -- our own
// invention, like the per-module prisms the field sprites replaced -- which
// meant the construction screen and the battlefield disagreed about what a
// cannon looks like.
//
// A sprite row is '#' ink, '.' paper, ' ' transparent. The bitmap is 1-bit
// and `paint` fills it with one colour, so it carries the *paper* pixels:
// the body takes the module's colour and the ink lines stay unset, showing
// the black screen through them exactly as the Spectrum's two-colour cell
// does. Pure: returns bitmaps, never touches the DOM.
import { ROBOT_SPRITES } from '../render/robot-sprites.ts';
import type { ModuleName } from './construction.ts';

/** Sprite width in pixels; every robot piece is drawn 24 wide. */
export const ICON_SIZE = 24;

export class Bitmap {
  readonly px: Uint8Array;
  constructor(
    readonly w: number,
    readonly h: number,
  ) {
    this.px = new Uint8Array(w * h);
  }
  get(x: number, y: number): number {
    return x >= 0 && y >= 0 && x < this.w && y < this.h ? this.px[y * this.w + x] : 0;
  }
  set(x: number, y: number, v: number): void {
    x = Math.round(x);
    y = Math.round(y);
    if (x >= 0 && y >= 0 && x < this.w && y < this.h) this.px[y * this.w + x] = v;
  }
}

const cache = new Map<ModuleName, Bitmap>();

/**
 * The piece sprite for `m`, facing west.
 *
 * Height varies by piece (23 to 30 rows), unlike the old fixed square, so a
 * caller sizes its canvas from the returned bitmap rather than assuming
 * `ICON_SIZE` both ways.
 */
export function moduleIcon(m: ModuleName): Bitmap {
  let bm = cache.get(m);
  if (!bm) {
    const rows = ROBOT_SPRITES[m].west;
    bm = new Bitmap(ICON_SIZE, rows.length);
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) if (row[x] === '.') bm!.set(x, y, 1);
    });
    cache.set(m, bm);
  }
  return bm;
}
