// Every fixture message must validate against the canonical protocol schemas,
// so fixture-driven screens are guaranteed to accept identical live messages.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { FIXTURES } from './index.ts';
import { Store } from '../state/store.ts';
import { runFixtureMessage } from './harness.ts';

const schemaDir = resolve(__dirname, '../../../protocol/schemas');
const ajv = new Ajv2020({ strict: true });
for (const file of readdirSync(schemaDir).filter((f) => f.endsWith('.schema.json'))) {
  ajv.addSchema(JSON.parse(readFileSync(resolve(schemaDir, file), 'utf8')), file);
}
const validators = {
  server: ajv.getSchema('server_messages.schema.json')!,
  snapshot: ajv.getSchema('snapshot.schema.json')!,
  reconnect: ajv.getSchema('reconnect.schema.json')!,
};

test('all fixture messages validate against protocol schemas', () => {
  for (const f of FIXTURES) {
    for (const [i, msg] of f.messages.entries()) {
      const v = msg.type === 'snapshot' ? validators.snapshot : msg.type === 'resync' ? validators.reconnect : validators.server;
      assert.ok(v(msg), `${f.id}[${i}] ${msg.type}: ${JSON.stringify(v.errors)}`);
    }
  }
});

test('fixtures boot the store into the expected phase and hold a snapshot when active', () => {
  for (const f of FIXTURES) {
    const store = new Store();
    let t = 0;
    for (const msg of f.messages) runFixtureMessage(store, msg, (t += 50));
    const s = store.get();
    assert.notEqual(s.lifecycle.phase, 'idle', f.id);
    if (['active', 'paused', 'finished', 'forfeit', 'no_contest'].includes(s.lifecycle.phase)) {
      assert.ok(s.latest, `${f.id} has no snapshot`);
    }
  }
});

test('reconnect fixture replaces state atomically', () => {
  const f = FIXTURES.find((x) => x.id === 'lifecycle-reconnect')!;
  const store = new Store();
  for (const msg of f.messages) runFixtureMessage(store, msg, 0);
  assert.equal(store.get().latest?.tick, 900);
  assert.equal(store.get().previous, null);
  assert.equal(store.get().connection.resyncGeneration, 1);
});
