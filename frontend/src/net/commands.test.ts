import { test } from 'vitest';
import assert from 'node:assert/strict';
import { CommandSender } from './commands.ts';
import { RecordingClient } from './client.ts';

const session = { matchId: 'm', playerId: 'p1', sessionToken: 'tok', joinCode: null, nickname: 'a', vsComputer: false };

test('commands carry generated envelope and monotonic clientSequence', () => {
  const c = new RecordingClient();
  const s = new CommandSender(c);
  assert.equal(s.command({ kind: 'commander_move', dx: 1, dy: 0 }), null);
  s.bind(session);
  s.command({ kind: 'commander_move', dx: 1, dy: 0 });
  s.command({ kind: 'commander_set_vertical_intent', rising: true });
  const seqs = c.sent.map((m) => (m.type === 'command' ? m.clientSequence : -1));
  assert.deepEqual(seqs, [1, 2]);
  assert.equal(c.sent[0].type, 'command');
  assert.equal(c.sent[0].protocolVersion, 1);
});

test('sequence resets when a different session is bound', () => {
  const c = new RecordingClient();
  const s = new CommandSender(c);
  s.bind(session);
  s.command({ kind: 'launch_robot' });
  s.bind({ ...session, sessionToken: 'other' });
  s.command({ kind: 'launch_robot' });
  assert.deepEqual(c.sent.map((m) => (m.type === 'command' ? m.clientSequence : -1)), [1, 1]);
});

test('ready/reconnect/leave use bound session identity', () => {
  const c = new RecordingClient();
  const s = new CommandSender(c);
  s.bind(session);
  s.ready(true);
  s.reconnect();
  s.leave();
  assert.deepEqual(c.sent.map((m) => m.type), ['ready', 'reconnect', 'leave']);
});
