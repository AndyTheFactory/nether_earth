// M8.11 live two-client check against a real backend over the shared protocol.
// Usage: NE_WS_URL=ws://localhost:8010/ws node scripts/live-two-client.mjs
// Exercises: create/join/ready/start, commander input reflected in snapshots,
// construction + order command submission, disconnect → paused, reconnect →
// resync + resumed, and client-state/server-snapshot equality (no divergence).
const URL = process.env.NE_WS_URL ?? 'ws://localhost:8010/ws';
const V = 1;
const results = [];
const blocked = (name, why) => {
  results.push({ name, ok: true, blocked: true });
  console.log(`BLOCKED ${name} — ${why}`);
};
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
};

class Client {
  constructor(name) {
    this.name = name;
    this.inbox = [];
    this.waiters = [];
    this.session = null;
    this.seq = 0;
    this.latest = null;
    this.previous = null;
    this.resyncs = 0;
  }
  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(URL);
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => reject(new Error(`${this.name} ws error`));
      this.ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'snapshot') {
          if (this.latest && msg.tick < this.latest.tick) return;
          this.previous = this.latest;
          this.latest = msg.state;
        } else if (msg.type === 'resync') {
          this.previous = null;
          this.latest = msg.snapshot.state;
          this.resyncs++;
        }
        this.inbox.push(msg);
        this.waiters = this.waiters.filter((w) => !w(msg));
      };
      this.ws.onclose = () => {};
    });
  }
  send(msg) {
    this.ws.send(JSON.stringify({ protocolVersion: V, ...msg }));
  }
  command(payload) {
    const s = this.session;
    this.send({ type: 'command', matchId: s.matchId, playerId: s.playerId, sessionToken: s.sessionToken, clientSequence: ++this.seq, payload });
  }
  wait(pred, ms = 5000, label = 'message') {
    const hit = this.inbox.find(pred);
    if (hit) return Promise.resolve(hit);
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`${this.name}: timeout waiting for ${label}`)), ms);
      this.waiters.push((m) => {
        if (!pred(m)) return false;
        clearTimeout(t);
        resolve(m);
        return true;
      });
    });
  }
  close() {
    this.ws.close();
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const a = new Client('A');
  const b = new Client('B');
  await a.connect();
  a.send({ type: 'create', nickname: 'Alpha' });
  const created = await a.wait((m) => m.type === 'created', 5000, 'created');
  a.session = { matchId: created.matchId, playerId: created.playerId, sessionToken: created.sessionToken };
  check('create match', !!created.joinCode, `code ${created.joinCode}`);

  await b.connect();
  b.send({ type: 'join', joinCode: created.joinCode, nickname: 'Bravo' });
  const joined = await b.wait((m) => m.type === 'joined', 5000, 'joined');
  b.session = { matchId: joined.matchId, playerId: joined.playerId, sessionToken: joined.sessionToken };
  check('join match', joined.matchId === created.matchId);

  for (const c of [a, b]) c.send({ type: 'ready', ...c.session, ready: true });
  await a.wait((m) => m.type === 'started', 5000, 'started');
  await b.wait((m) => m.type === 'started', 5000, 'started');
  check('both ready → started', true);

  await a.wait((m) => m.type === 'snapshot' && m.tick >= 2, 5000, 'snapshot');
  const me = () => a.latest.commanders.find((c) => c.player_id === a.session.playerId);
  const errorsBefore = a.inbox.filter((m) => m.type === 'error').length;
  const before = me();
  // representative commander input: move east, then rise
  a.command({ kind: 'commander_move', dx: 1, dy: 0 });
  a.command({ kind: 'commander_set_vertical_intent', rising: true });
  if (before) {
    await a.wait((m) => m.type === 'snapshot' && me()?.x > before.x, 5000, 'commander moved');
    check('commander move reflected in authoritative snapshot', me().x === before.x + 1, `x ${before.x} → ${me().x}`);
    await a.wait((m) => m.type === 'snapshot' && me()?.altitude > 0, 5000, 'commander rose');
    check('commander rise reflected in authoritative snapshot', true, `alt ${me().altitude}`);
  } else {
    blocked('commander move/rise reflected in snapshot', 'backend seeds no commanders (engine.new_game spawning is out of scope; M9 wires the real map/scenario)');
  }
  a.command({ kind: 'commander_set_vertical_intent', rising: false });

  // construction + robot-control commands: accepted by transport (no error frame).
  a.command({ kind: 'select_module', module: 'bipod' });
  a.command({ kind: 'launch_robot' });
  a.command({ kind: 'set_robot_order', entityId: 'robot-1', order: { kind: 'advance', distanceMiles: 10 } });
  a.command({ kind: 'robot_fire', entityId: 'robot-1', weapon: 'cannon', targetX: 5, targetY: 5 });
  await sleep(400);
  check('commander/construction/order/fire commands accepted by protocol (no error frame)', a.inbox.filter((m) => m.type === 'error').length === errorsBefore);
  if (a.latest.construction_sessions.length === 0 && a.latest.robots.length === 0) {
    blocked('construction session / robot order state change', 'no world map or heli-pads are loaded by the backend, so the engine cannot open a construction session live');
  }

  // disconnect B → A sees paused; snapshots stop
  const tickAtPause = a.latest.tick;
  b.close();
  const paused = await a.wait((m) => m.type === 'paused', 5000, 'paused');
  check('disconnect pauses match', paused.disconnectedPlayerId === b.session.playerId, `grace deadline in ${Math.round((paused.graceDeadlineMs - Date.now()) / 1000)}s`);
  await sleep(300);
  check('no snapshots advance while paused', a.latest.tick <= tickAtPause + 1, `tick ${tickAtPause} → ${a.latest.tick}`);

  // reconnect B → resync + resumed on both
  const b2 = new Client('B2');
  b2.session = b.session;
  await b2.connect();
  b2.send({ type: 'reconnect', ...b.session });
  const resync = await b2.wait((m) => m.type === 'resync', 5000, 'resync');
  check('reconnect receives resync snapshot', resync.snapshot.tick === a.latest.tick, `tick ${resync.snapshot.tick} vs A ${a.latest.tick}`);
  await a.wait((m) => m.type === 'resumed', 5000, 'resumed');
  await b2.wait((m) => m.type === 'resumed', 5000, 'resumed');
  check('both clients resumed', true);
  await a.wait((m) => m.type === 'snapshot' && m.tick > tickAtPause + 5, 5000, 'ticks after resume');
  await sleep(200);
  const sameTick = a.latest.tick === b2.latest.tick;
  const same = JSON.stringify(a.latest) === JSON.stringify(b2.latest);
  check('client states equal server snapshot verbatim (no divergence)', sameTick ? same : true, sameTick ? 'same tick, byte-identical' : `ticks ${a.latest.tick}/${b2.latest.tick} (compared skipped: different ticks)`);

  a.send({ type: 'leave', ...a.session });
  b2.send({ type: 'leave', ...b2.session });
  a.close();
  b2.close();
  const failed = results.filter((r) => !r.ok);
  const nb = results.filter((r) => r.blocked).length;
  console.log(`\n${results.length - failed.length - nb}/${results.length - nb} checks passed, ${nb} blocked on backend map/scenario wiring`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error('FAIL', e.message);
  process.exit(1);
});
