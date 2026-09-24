import { describe, expect, it } from 'vitest';
import type { SnapshotState } from '../../../protocol/generated/types';
import { sfxForSnapshot } from './events.ts';

function state(tick: number, extra: Partial<SnapshotState> = {}): SnapshotState {
  return {
    tick,
    players: ['p1', 'p2'],
    seed: 1,
    commanders: [],
    resource_pools: [],
    construction_sessions: [],
    robots: [],
    structure_ownership: [],
    capture_progress: [],
    projectiles: [],
    structure_destruction: [],
    scenery_debris: [],
    ...extra,
  };
}

function robot(id: string, strength: number): SnapshotState['robots'][number] {
  return {
    entity_id: id,
    owner: 'p1',
    x: 0,
    y: 0,
    build: {},
    stack: ['bipod'],
    height: 1,
    movement: null,
    order: null,
    active_projectile_id: null,
    strength,
    last_fire_tick: null,
    exit_steps_remaining: 0,
    facing: 'east',
    turning: null,
  };
}

function shot(id: string): SnapshotState['projectiles'][number] {
  return {
    id,
    owner: 'p1',
    source_robot_id: 'r1',
    weapon: 'cannon',
    x: 0,
    y: 0,
    z: 10,
    dx: 1,
    dy: 0,
    travelled_cells: 0,
    max_range_cells: 3,
    created_tick: 1,
    first_advance_tick: 2,
  };
}

describe('snapshot sound events', () => {
  it('says nothing without a previous snapshot', () => {
    expect(sfxForSnapshot(null, state(1))).toEqual([]);
  });

  it('ignores a repeated or older tick', () => {
    const now = state(5, { projectiles: [shot('b1')] });
    expect(sfxForSnapshot(state(5), now)).toEqual([]);
    expect(sfxForSnapshot(state(6), now)).toEqual([]);
  });

  it('fires on a new projectile', () => {
    expect(sfxForSnapshot(state(1), state(2, { projectiles: [shot('b1')] }))).toEqual(['fire']);
  });

  it('plays the hit sound when a robot loses strength', () => {
    const before = state(1, { robots: [robot('r1', 10)] });
    const after = state(2, { robots: [robot('r1', 6)] });
    expect(sfxForSnapshot(before, after)).toEqual(['hit']);
  });

  it('plays the destroyed sound when a robot goes to zero or vanishes', () => {
    const before = state(1, { robots: [robot('r1', 2)] });
    expect(sfxForSnapshot(before, state(2, { robots: [robot('r1', -4)] }))).toEqual(['destroyed']);
    expect(sfxForSnapshot(before, state(2))).toEqual(['destroyed']);
  });

  it('plays the miss sound for a shot that ended without damage', () => {
    const before = state(1, { robots: [robot('r1', 10)], projectiles: [shot('b1')] });
    const after = state(2, { robots: [robot('r1', 10)] });
    expect(sfxForSnapshot(before, after)).toEqual(['bullet_gone']);
  });

  it('does not add the miss sound to the shot that landed', () => {
    const before = state(1, { robots: [robot('r1', 10)], projectiles: [shot('b1')] });
    const after = state(2, { robots: [robot('r1', 4)] });
    expect(sfxForSnapshot(before, after)).toEqual(['hit']);
  });

  it('lets the nuclear blast take over the tick', () => {
    const before = state(1, { robots: [robot('r1', 10)], projectiles: [shot('b1')] });
    const after = state(2, { scenery_debris: ['s1'] });
    expect(sfxForSnapshot(before, after)).toEqual(['nuclear']);
  });
});
