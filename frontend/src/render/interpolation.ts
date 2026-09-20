// Visual-only interpolation over authoritative transitions (M8.3).
// Input: engine transition records copied verbatim from the snapshot.
// Output: fractional display coordinates. Never written back to the store.

export interface GridTransition {
  from_x: number;
  from_y: number;
  to_x: number;
  to_y: number;
  started_tick: number;
  duration_ticks: number;
}

export interface VerticalTransition {
  from_altitude: number;
  to_altitude: number;
  started_tick: number;
  duration_ticks: number;
}

export const TICK_MS = 50;

/**
 * Fractional "display tick": latest authoritative tick plus the wall-clock
 * fraction elapsed since it arrived, capped at one tick so visuals never run
 * ahead of the server. `frozen` (paused/finished) pins it to the tick itself.
 */
export function displayTick(latestTick: number, arrivedAtMs: number, nowMs: number, frozen: boolean): number {
  if (frozen) return latestTick;
  const frac = Math.min(1, Math.max(0, (nowMs - arrivedAtMs) / TICK_MS));
  return latestTick + frac;
}

function alpha(started: number, duration: number, tick: number): number {
  if (duration <= 0) return 1;
  return Math.min(1, Math.max(0, (tick - started) / duration));
}

export function interpolateGrid(
  x: number,
  y: number,
  t: GridTransition | null | undefined,
  tick: number,
): { x: number; y: number } {
  if (!t) return { x, y };
  const a = alpha(t.started_tick, t.duration_ticks, tick);
  // Authoritative x/y already equal to_x/to_y once committed; while the
  // transition record is present we draw along from→to.
  return { x: t.from_x + (t.to_x - t.from_x) * a, y: t.from_y + (t.to_y - t.from_y) * a };
}

export function interpolateAltitude(altitude: number, t: VerticalTransition | null | undefined, tick: number): number {
  if (!t) return altitude;
  const a = alpha(t.started_tick, t.duration_ticks, tick);
  return t.from_altitude + (t.to_altitude - t.from_altitude) * a;
}

export function isGridTransition(v: unknown): v is GridTransition {
  return !!v && typeof v === 'object' && 'from_x' in v && 'to_x' in v && 'started_tick' in v;
}

export function isVerticalTransition(v: unknown): v is VerticalTransition {
  return !!v && typeof v === 'object' && 'from_altitude' in v && 'to_altitude' in v;
}
