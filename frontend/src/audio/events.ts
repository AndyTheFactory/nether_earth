// Derives sound effects from consecutive authoritative snapshots (#272).
//
// The original raises its sounds inside the rules (`Lb70c` on firing,
// `Lb7db_robot_hit` on damage, and so on). Our engine owns the rules and does
// not report presentation events, so the frontend reads the same moments back
// out of the snapshot it already renders. This is presentation-only: nothing
// here decides legality, and a missed or doubled sound never affects the match.
import type { SnapshotState } from '../../../protocol/generated/types';
import type { SfxName } from './engine.ts';

/** Sounds raised by one snapshot step, in a fixed order so replays match. */
export function sfxForSnapshot(previous: SnapshotState | null, next: SnapshotState): SfxName[] {
  if (!previous || previous.tick >= next.tick) return [];
  const sounds: SfxName[] = [];

  // A nuclear blast is the only thing that turns scenery into debris, and it
  // is loud enough to stand in for everything else happening that tick.
  if (next.scenery_debris.length > previous.scenery_debris.length) {
    return ['nuclear'];
  }

  const wasFlying = new Set(previous.projectiles.map((p) => p.id));
  const flying = new Set(next.projectiles.map((p) => p.id));
  if (next.projectiles.some((p) => !wasFlying.has(p.id))) sounds.push('fire');

  const before = new Map(previous.robots.map((r) => [r.entity_id, r]));
  let destroyed = false;
  let damaged = false;
  for (const [id, was] of before) {
    const now = next.robots.find((r) => r.entity_id === id);
    if (!now || now.strength <= 0) {
      if (was.strength > 0) destroyed = true;
    } else if (now.strength < was.strength) {
      damaged = true;
    }
  }
  if (destroyed) sounds.push('destroyed');
  else if (damaged) sounds.push('hit');

  // A shot that vanished without damaging anything hit the ground or ran out
  // of range, which is the `Lb7ea_bullet_disappear` sound.
  if (!destroyed && !damaged && [...wasFlying].some((id) => !flying.has(id))) {
    sounds.push('bullet_gone');
  }

  return sounds;
}
