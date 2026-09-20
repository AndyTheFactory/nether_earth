// Deterministic recorded fixtures (M8.1/M8.11): ordered inbound message
// streams typed against the generated protocol, so every fixture screen can
// later be driven by identical live messages. Coordinates reference the real
// zx-spectrum-original map (warbase-1 at x18–27, warbase-4 at x490–499).
import type { ServerMessage, SnapshotMessage, SnapshotState, ServerResync } from '../../../protocol/generated/types';
import type { InboundMessage } from '../net/client.ts';

export interface Fixture {
  id: string;
  title: string;
  description: string;
  /** Which player this client plays as. */
  playerId: 'p1' | 'p2';
  messages: InboundMessage[];
}

const MATCH = 'fixture-match';
type Commander = SnapshotState['commanders'][number];
type Robot = SnapshotState['robots'][number];
type Pool = SnapshotState['resource_pools'][number];

function pool(player_id: string, general: number, extra: Partial<Pool> = {}): Pool {
  return { player_id, general, chassis: 0, electronics: 0, nuclear: 0, missile: 0, phaser: 0, cannon: 0, ...extra };
}

function commander(player_id: string, x: number, y: number, altitude: number, extra: Partial<Commander> = {}): Commander {
  return { player_id, mode: 'free', x, y, altitude, docked_robot_id: null, rising: false, horizontal_transition: null, vertical_transition: null, ...extra };
}

function robot(entity_id: string, owner: string, x: number, y: number, stack: Robot['stack'], extra: Partial<Robot> = {}): Robot {
  const chassis = stack[0];
  const weapons = stack.filter((m) => m === 'cannon' || m === 'missile' || m === 'phaser' || m === 'nuclear');
  const electronics = stack.includes('electronics') ? 'electronics' : null;
  const height = stack.reduce((h, m) => h + (m === 'bipod' || m === 'tracks' || m === 'anti_grav' ? 4 : 2), 0);
  return { entity_id, owner, x, y, build: { chassis, weapons, electronics }, stack, height, movement: null, order: null, active_projectile_id: null, strength: 100, ...extra };
}

function base(tick: number, patch: Partial<SnapshotState> = {}): SnapshotState {
  return {
    tick,
    players: ['p1', 'p2'],
    seed: 42,
    commanders: [commander('p1', 24, 10, 0), commander('p2', 494, 10, 0)],
    resource_pools: [pool('p1', 20), pool('p2', 20)],
    construction_sessions: [],
    robots: [],
    structure_ownership: [
      { structure_id: 'warbase-1', owner: 'p1' },
      { structure_id: 'warbase-4', owner: 'p2' },
    ],
    capture_progress: [],
    projectiles: [],
    structure_destruction: [],
    ...patch,
  };
}

function snapshot(state: SnapshotState): SnapshotMessage {
  return { protocolVersion: 1, type: 'snapshot', matchId: MATCH, tick: state.tick, state };
}

function resync(playerId: string, state: SnapshotState): ServerResync {
  return { protocolVersion: 1, type: 'resync', matchId: MATCH, playerId, snapshot: snapshot(state) };
}

const created: ServerMessage = { protocolVersion: 1, type: 'created', matchId: MATCH, joinCode: 'FIXT', playerId: 'p1', sessionToken: 'fixture-token' };
const joined: ServerMessage = { protocolVersion: 1, type: 'joined', matchId: MATCH, playerId: 'p2', sessionToken: 'fixture-token-2' };
const readyBoth: ServerMessage = {
  protocolVersion: 1,
  type: 'ready_state',
  matchId: MATCH,
  players: [
    { playerId: 'p1', nickname: 'Alpha', ready: true },
    { playerId: 'p2', nickname: 'Bravo', ready: true },
  ],
};
const started = (tick = 0): ServerMessage => ({ protocolVersion: 1, type: 'started', matchId: MATCH, tick });

/** Standard intro: created, both ready, started. */
function intro(playerId: 'p1' | 'p2' = 'p1'): InboundMessage[] {
  return [playerId === 'p1' ? created : joined, readyBoth, started()];
}

const movingP1 = commander('p1', 25, 10, 6, {
  rising: true,
  horizontal_transition: { from_x: 24, from_y: 10, to_x: 25, to_y: 10, started_tick: 8, duration_ticks: 4 },
  vertical_transition: { from_altitude: 4, to_altitude: 6, started_tick: 8, duration_ticks: 4 },
});

const bipodRobot = robot('robot-1', 'p1', 30, 10, ['bipod', 'cannon']);
const fullRobot = robot('robot-2', 'p1', 34, 11, ['tracks', 'cannon', 'missile', 'phaser', 'nuclear', 'electronics'], {
  order: { kind: 'advance', distance_miles: 10, target_x: 54 },
  movement: { entity_id: 'robot-2', from_x: 33, from_y: 11, to_x: 34, to_y: 11, started_tick: 90, duration_ticks: 16 },
});
const enemyRobot = robot('robot-9', 'p2', 60, 8, ['anti_grav', 'phaser'], {
  order: { kind: 'search_destroy', target: 'robot' },
  strength: 45,
});

export const FIXTURES: Fixture[] = [
  {
    id: 'world-static',
    title: 'Static world (M2)',
    description: 'Full original map, both war bases owned, free commanders, no robots.',
    playerId: 'p1',
    messages: [...intro(), snapshot(base(0))],
  },
  {
    id: 'commander-moving',
    title: 'Commander moving and rising (M3)',
    description: 'Horizontal and vertical transitions in flight across consecutive ticks.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(base(8, { commanders: [commander('p1', 24, 10, 4), commander('p2', 494, 10, 0)] })),
      snapshot(base(9, { commanders: [movingP1, commander('p2', 494, 10, 0)] })),
      snapshot(base(10, { commanders: [movingP1, commander('p2', 494, 10, 0)] })),
    ],
  },
  {
    id: 'commander-docked',
    title: 'Commander docked on robot (M3/M5)',
    description: 'DOCKED mode: robot menu available; commander rides robot height.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(
        base(100, {
          commanders: [commander('p1', 30, 10, 6, { mode: 'docked', docked_robot_id: 'robot-1' }), commander('p2', 494, 10, 0)],
          robots: [bipodRobot, fullRobot, enemyRobot],
        }),
      ),
    ],
  },
  {
    id: 'construction',
    title: 'Construction session (M4)',
    description: 'Heli-pad session with a partial build, mixed specific/general spending, then cancel.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(
        base(200, {
          commanders: [commander('p1', 22, 9, 15), commander('p2', 494, 10, 0)],
          resource_pools: [pool('p1', 27, { cannon: 0, chassis: 1 }), pool('p2', 20)],
          construction_sessions: [
            {
              player_id: 'p1',
              war_base_id: 'warbase-1',
              entry_tick: 190,
              build: { chassis: 'tracks', weapons: ['cannon'], electronics: null },
              buffer: { general: 4, category: { chassis: 1, electronics: 0, nuclear: 0, missile: 0, phaser: 0, cannon: 2 } },
              entry_snapshot: { general: 30, category: { chassis: 2, electronics: 0, nuclear: 0, missile: 0, phaser: 0, cannon: 2 } },
            },
          ],
        }),
      ),
      { protocolVersion: 1, type: 'error', matchId: MATCH, error: { code: 'invalid_message', message: 'message failed protocol validation: 1 error(s)' } },
      snapshot(
        base(230, {
          commanders: [commander('p1', 22, 9, 15), commander('p2', 494, 10, 0)],
          resource_pools: [pool('p1', 30, { chassis: 2, cannon: 2 }), pool('p2', 20)],
        }),
      ),
      snapshot(
        base(260, {
          commanders: [commander('p1', 22, 9, 15), commander('p2', 494, 10, 0)],
          resource_pools: [pool('p1', 25, { chassis: 0, cannon: 0 }), pool('p2', 20)],
          robots: [robot('robot-1', 'p1', 22, 9, ['tracks', 'cannon'])],
        }),
      ),
    ],
  },
  {
    id: 'robots-orders',
    title: 'Robot orders, navigation, capture (M5)',
    description: 'Every order kind, an in-flight move transition and a capture in progress.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(
        base(1000, {
          commanders: [commander('p1', 34, 11, 12, { mode: 'docked', docked_robot_id: 'robot-2' }), commander('p2', 494, 10, 0)],
          robots: [
            bipodRobot,
            fullRobot,
            robot('robot-3', 'p1', 39, 7, ['tracks', 'missile'], { order: { kind: 'search_capture', target: 'neutral_factory' } }),
            robot('robot-4', 'p1', 28, 12, ['bipod', 'cannon'], { order: { kind: 'retreat', distance_miles: 4, target_x: 20 } }),
            robot('robot-5', 'p1', 45, 9, ['anti_grav', 'cannon', 'electronics'], { order: { kind: 'stop_and_defend' } }),
            enemyRobot,
          ],
          capture_progress: [{ structure_id: 'factory-1', capturing_player: 'p1', robot_id: 'robot-3', elapsed_ticks: 300, required_ticks: 1440 }],
          structure_ownership: [
            { structure_id: 'warbase-1', owner: 'p1' },
            { structure_id: 'warbase-4', owner: 'p2' },
            { structure_id: 'factory-2', owner: 'p1' },
            { structure_id: 'factory-3', owner: 'p2' },
          ],
        }),
      ),
    ],
  },
  {
    id: 'combat',
    title: 'Combat, projectiles, nuclear (M6)',
    description: 'In-flight projectiles, a damaged robot, then destruction and a nuked factory.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(
        base(2000, {
          commanders: [commander('p1', 30, 10, 6, { mode: 'docked', docked_robot_id: 'robot-1' }), commander('p2', 494, 10, 0)],
          robots: [
            { ...bipodRobot, active_projectile_id: 'proj-1' },
            { ...fullRobot, strength: 60 },
            { ...enemyRobot, x: 44, y: 10, strength: 20, active_projectile_id: 'proj-2' },
          ],
          projectiles: [
            { id: 'proj-1', owner: 'p1', source_robot_id: 'robot-1', weapon: 'cannon', x: 36, y: 10, z: 10, dx: 1, dy: 0, travelled_cells: 6, max_range_cells: 20, created_tick: 1976 },
            { id: 'proj-2', owner: 'p2', source_robot_id: 'robot-9', weapon: 'phaser', x: 40, y: 10, z: 10, dx: -1, dy: 0, travelled_cells: 4, max_range_cells: 20, created_tick: 1984 },
          ],
        }),
      ),
      snapshot(
        base(2040, {
          commanders: [commander('p1', 30, 10, 6, { mode: 'docked', docked_robot_id: 'robot-1' }), commander('p2', 494, 10, 0)],
          robots: [bipodRobot, { ...fullRobot, strength: 60 }],
          structure_destruction: ['factory-1'],
        }),
      ),
    ],
  },
  {
    id: 'lifecycle-waiting',
    title: 'Lobby: waiting for opponent (M7)',
    description: 'Match created, join code shown, one player ready.',
    playerId: 'p1',
    messages: [
      created,
      { protocolVersion: 1, type: 'ready_state', matchId: MATCH, players: [{ playerId: 'p1', nickname: 'Alpha', ready: true }] },
    ],
  },
  {
    id: 'lifecycle-paused',
    title: 'Paused: opponent disconnected (M7)',
    description: 'Active match then pause with grace deadline; clock must freeze.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(base(500, { robots: [bipodRobot] })),
      { protocolVersion: 1, type: 'paused', matchId: MATCH, disconnectedPlayerId: 'p2', graceDeadlineMs: Date.now() + 60000 },
    ],
  },
  {
    id: 'lifecycle-reconnect',
    title: 'Reconnect resync (M7)',
    description: 'Normal ticks, then a resync snapshot far ahead replaces state atomically.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(base(600, { robots: [fullRobot] })),
      snapshot(base(601, { robots: [fullRobot] })),
      resync('p1', base(900, { robots: [{ ...fullRobot, x: 50, movement: null, order: { kind: 'stop_and_defend' } }] })),
      { protocolVersion: 1, type: 'resumed', matchId: MATCH, tick: 900 },
    ],
  },
  {
    id: 'result-victory',
    title: 'Result: victory',
    description: 'Enemy war base destroyed; finished with this player as winner.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(base(5000, { structure_destruction: ['warbase-4'], structure_ownership: [{ structure_id: 'warbase-1', owner: 'p1' }] })),
      { protocolVersion: 1, type: 'finished', matchId: MATCH, winnerPlayerId: 'p1', tick: 5000 },
    ],
  },
  {
    id: 'result-loss',
    title: 'Result: loss',
    description: 'Finished with the opponent as winner.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(base(5000, { structure_ownership: [{ structure_id: 'warbase-1', owner: 'p2' }, { structure_id: 'warbase-4', owner: 'p2' }] })),
      { protocolVersion: 1, type: 'finished', matchId: MATCH, winnerPlayerId: 'p2', tick: 5000 },
    ],
  },
  {
    id: 'result-forfeit',
    title: 'Result: forfeit',
    description: 'Opponent grace period expired.',
    playerId: 'p1',
    messages: [
      ...intro(),
      snapshot(base(700)),
      { protocolVersion: 1, type: 'paused', matchId: MATCH, disconnectedPlayerId: 'p2', graceDeadlineMs: Date.now() + 60000 },
      { protocolVersion: 1, type: 'forfeit', matchId: MATCH, forfeitingPlayerId: 'p2', winnerPlayerId: 'p1', reason: 'disconnect_timeout' },
    ],
  },
  {
    id: 'result-no-contest',
    title: 'Result: no contest',
    description: 'Both players timed out.',
    playerId: 'p1',
    messages: [...intro(), snapshot(base(700)), { protocolVersion: 1, type: 'no_contest', matchId: MATCH, reason: 'disconnect_timeout_both' }],
  },
];

export function findFixture(id: string): Fixture | undefined {
  return FIXTURES.find((f) => f.id === id);
}
