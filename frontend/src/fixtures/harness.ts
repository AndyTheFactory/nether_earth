// Routes one inbound message into the store. Shared by the live client and
// the fixture player so both paths are byte-identical at the store boundary.
import type { InboundMessage } from '../net/client.ts';
import type { Store } from '../state/store.ts';
import type { Fixture } from './index.ts';

export function runFixtureMessage(store: Store, msg: InboundMessage, nowMs: number): void {
  if (msg.type === 'snapshot') {
    store.applySnapshot(msg, nowMs);
  } else if (msg.type === 'resync') {
    store.replaceSnapshot(msg.snapshot, nowMs);
  } else {
    store.applyServerMessage(msg);
  }
}

/** Plays a fixture's messages with a fixed inter-message delay (tick cadence). */
export function playFixture(store: Store, fixture: Fixture, stepMs = 50, now: () => number = () => performance.now(), limit = Infinity): () => void {
  store.reset();
  store.setConnection({
    status: 'fixture',
    session: { matchId: 'fixture-match', playerId: fixture.playerId, sessionToken: 'fixture-token', joinCode: 'FIXT', nickname: 'Alpha', vsComputer: false },
  });
  let i = 0;
  const timer = setInterval(() => {
    if (i >= Math.min(limit, fixture.messages.length)) {
      clearInterval(timer);
      return;
    }
    runFixtureMessage(store, fixture.messages[i++], now());
  }, stepMs);
  return () => clearInterval(timer);
}
