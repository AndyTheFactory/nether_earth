// Module icons for the ROBOT CONSTRUCTION screen (CR002.9), drawn as 1-bit
// pixel art in the Spectrum manner: one ink colour on black paper, 2:1
// isometric blocks, faces separated by paper-coloured seams. Every icon is
// generated here from a few iso primitives; no graphics data is copied from
// the original game (see frontend/public/assets/README.md). Pure: returns
// bitmaps, never touches the DOM.
import type { ModuleName } from './construction.ts';

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
  line(x0: number, y0: number, x1: number, y1: number, v: number): void {
    x0 = Math.round(x0);
    y0 = Math.round(y0);
    x1 = Math.round(x1);
    y1 = Math.round(y1);
    const dx = Math.abs(x1 - x0);
    const dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1;
    const sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      this.set(x0, y0, v);
      if (x0 === x1 && y0 === y1) return;
      const e2 = 2 * err;
      if (e2 >= dy) {
        err += dy;
        x0 += sx;
      }
      if (e2 <= dx) {
        err += dx;
        y0 += sy;
      }
    }
  }
  /** Fill a convex/concave polygon (pixel centres inside) with ink where `pattern` is true. */
  poly(pts: readonly [number, number][], pattern: (x: number, y: number) => boolean = () => true): void {
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    for (let y = Math.floor(Math.min(...ys)); y <= Math.ceil(Math.max(...ys)); y++) {
      for (let x = Math.floor(Math.min(...xs)); x <= Math.ceil(Math.max(...xs)); x++) {
        if (inside(pts, x + 0.5, y + 0.5)) this.set(x, y, pattern(x, y) ? 1 : 0);
      }
    }
    for (let i = 0; i < pts.length; i++) {
      const [ax, ay] = pts[i];
      const [bx, by] = pts[(i + 1) % pts.length];
      this.line(ax, ay, bx, by, 1);
    }
  }
  disc(cx: number, cy: number, rx: number, ry: number, v: number): void {
    for (let y = Math.floor(cy - ry); y <= Math.ceil(cy + ry); y++) {
      for (let x = Math.floor(cx - rx); x <= Math.ceil(cx + rx); x++) {
        const nx = (x + 0.5 - cx) / rx;
        const ny = (y + 0.5 - cy) / ry;
        if (nx * nx + ny * ny <= 1) this.set(x, y, v);
      }
    }
  }
  /** Copy the ink into a `size`x`size` bitmap, centred horizontally and bottom-aligned. */
  settle(size: number): Bitmap {
    let minX = this.w;
    let maxX = -1;
    let maxY = -1;
    for (let y = 0; y < this.h; y++) {
      for (let x = 0; x < this.w; x++) {
        if (this.get(x, y)) {
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          maxY = Math.max(maxY, y);
        }
      }
    }
    const out = new Bitmap(size, size);
    if (maxX < 0) return out;
    let minY = this.h;
    for (let y = 0; y < this.h && minY === this.h; y++) for (let x = 0; x < this.w; x++) if (this.get(x, y)) minY = y;
    if (maxX - minX >= size || maxY - minY >= size) throw new Error(`icon ink ${maxX - minX + 1}x${maxY - minY + 1} exceeds ${size}x${size}`);
    const dx = Math.floor((size - (maxX - minX + 1)) / 2) - minX;
    const dy = size - 1 - maxY;
    for (let y = 0; y < this.h; y++) for (let x = 0; x < this.w; x++) if (this.get(x, y)) out.set(x + dx, y + dy, 1);
    return out;
  }
}

function inside(pts: readonly [number, number][], x: number, y: number): boolean {
  let c = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

/** 2:1 isometric: +a runs down-right, +b down-left, +z straight up (2 px per unit, like a and b). */
const SCRATCH = 48;
const OX = 24;
const OY = 24;
function iso(a: number, b: number, z: number): [number, number] {
  return [OX + 2 * a - 2 * b, OY + a + b - 2 * z];
}

type Face = 'solid' | 'paper' | 'stripes' | 'dots' | 'hatch';
const PATTERN: Record<Face, (x: number, y: number) => boolean> = {
  solid: () => true,
  paper: () => false,
  stripes: (x) => x % 2 === 0,
  dots: (x, y) => (x + y) % 2 === 0,
  hatch: (_x, y) => y % 2 === 0,
};

/**
 * An isometric block drawn opaque: ink edges, an open top, a dithered left
 * (+b) face and a striped right (+a) face unless overridden.
 */
function box(bm: Bitmap, a: number, b: number, z: number, w: number, d: number, h: number, faces: { top?: Face; left?: Face; right?: Face } = {}): void {
  const t0 = iso(a, b, z + h);
  const t1 = iso(a + w, b, z + h);
  const t2 = iso(a + w, b + d, z + h);
  const t3 = iso(a, b + d, z + h);
  const b1 = iso(a + w, b, z);
  const b2 = iso(a + w, b + d, z);
  const b3 = iso(a, b + d, z);
  bm.poly([t3, t2, b2, b3], PATTERN[faces.left ?? 'dots']);
  bm.poly([t1, t2, b2, b1], PATTERN[faces.right ?? 'stripes']);
  bm.poly([t0, t1, t2, t3], PATTERN[faces.top ?? 'paper']);
}

const DRAW: Record<ModuleName, (bm: Bitmap) => void> = {
  electronics: (bm) => {
    box(bm, 0, 0, 0, 4.5, 4.5, 3);
    // a radar dish on a short mast
    const [mx, my] = iso(2.25, 2.25, 3);
    bm.line(mx, my, mx, my - 4, 1);
    bm.line(mx + 1, my, mx + 1, my - 4, 1);
    bm.disc(mx + 0.5, my - 7, 6, 3.4, 0);
    bm.disc(mx + 0.5, my - 7, 5.2, 2.7, 1);
    bm.disc(mx + 0.5, my - 7.6, 3.6, 1.6, 0);
    bm.line(mx + 0.5, my - 7.6, mx + 3.5, my - 12, 1);
  },
  nuclear: (bm) => {
    box(bm, 0, 0, 0, 5, 5, 2.5, { right: 'hatch' });
    const [cx, top] = iso(2.5, 2.5, 2.5);
    const r = 6;
    const oy = top - r + 2;
    bm.disc(cx, oy, r + 1, r + 1, 0);
    bm.disc(cx, oy, r, r, 1);
    bm.disc(cx, oy, 1.4, 1.4, 0);
    bm.disc(cx, oy, 0.6, 0.6, 1);
    for (const ang of [Math.PI / 2, -Math.PI / 6, (-5 * Math.PI) / 6]) {
      for (let rr = 2; rr <= 4; rr += 0.4) {
        for (let da = -0.45; da <= 0.45; da += 0.08) bm.set(cx - 0.5 + Math.cos(ang + da) * rr, oy - 0.5 + Math.sin(ang + da) * rr, 0);
      }
    }
  },
  phaser: (bm) => {
    box(bm, 0, 0, 0, 5, 5, 2.5);
    box(bm, 0.8, 0.5, 2.5, 1, 4, 4, { left: 'hatch', right: 'paper', top: 'solid' });
    box(bm, 3.2, 0.5, 2.5, 1, 4, 4, { left: 'hatch', right: 'paper', top: 'solid' });
  },
  missile: (bm) => {
    box(bm, 0, 0, 0, 5, 5, 2.5);
    for (const b of [0.3, 2.7]) {
      box(bm, -0.5, b, 2.5, 6, 2, 1.6, { left: 'paper', top: 'hatch', right: 'solid' });
      const [x, y] = iso(5.5, b + 1, 3.3);
      bm.disc(x, y, 1.5, 1.5, 0);
      bm.set(x, y, 1);
    }
  },
  cannon: (bm) => {
    box(bm, 0, 0, 0, 5, 5, 3);
    box(bm, 0.8, 1.2, 3, 2.4, 2.4, 1.8, { top: 'solid' });
    box(bm, 3.2, 2, 3.8, 5, 0.9, 0.8, { top: 'solid', right: 'paper' });
  },
  anti_grav: (bm) => {
    for (const [a, b] of [
      [0.5, 0.5],
      [3.7, 0.5],
      [0.5, 3.7],
      [3.7, 3.7],
    ] as const) box(bm, a, b, 0, 1.5, 1.5, 1.5, { top: 'solid' });
    box(bm, 0, 0, 1.5, 5.6, 5.6, 1);
    for (const [a, b] of [
      [1.5, 1.5],
      [4.2, 1.5],
      [1.5, 4.2],
      [4.2, 4.2],
    ] as const) {
      const [x, y] = iso(a, b, 2.5);
      bm.disc(x, y, 2.4, 1.3, 1);
      bm.disc(x, y, 1.2, 0.6, 0);
    }
  },
  tracks: (bm) => {
    box(bm, 0, 0, 0, 6, 2.2, 2.5, { left: 'hatch', right: 'paper' });
    box(bm, 1, 2.2, 0.8, 4, 1.1, 1.5, { left: 'paper' });
    box(bm, 0, 3.3, 0, 6, 2.2, 2.5, { left: 'hatch', right: 'paper' });
    for (let a = 1; a <= 5; a += 2) {
      const [x, y] = iso(a, 5.5, 1.25);
      bm.disc(x, y, 1.4, 1.4, 0);
      bm.set(x, y, 1);
    }
  },
  bipod: (bm) => {
    box(bm, 0.3, 0.3, 0, 2.2, 2.2, 0.8, { top: 'solid' });
    box(bm, 2.8, 2.8, 0, 2.2, 2.2, 0.8, { top: 'solid' });
    box(bm, 1, 1, 0.8, 1, 1, 3.5, { left: 'hatch' });
    box(bm, 3.5, 3.5, 0.8, 1, 1, 3.5, { left: 'hatch' });
    box(bm, 0, 0, 4.3, 5.3, 5.3, 2, { top: 'hatch' });
  },
};

const cache = new Map<ModuleName, Bitmap>();

/** The 24x24 1-bit icon of one module (1 = ink). */
export function moduleIcon(m: ModuleName): Bitmap {
  let bm = cache.get(m);
  if (!bm) {
    const scratch = new Bitmap(SCRATCH, SCRATCH);
    DRAW[m](scratch);
    bm = scratch.settle(ICON_SIZE);
    cache.set(m, bm);
  }
  return bm;
}
