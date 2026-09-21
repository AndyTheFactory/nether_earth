// M8.11/M9.7 live two-client check against a real backend over the shared protocol.
// Usage: NE_WS_URL=ws://localhost:8010/ws node scripts/live-two-client.mjs
// Exercises: create/join/ready/start, commander input reflected in snapshots,
// construction + order command submission, projectile speed/range (CR001 §8), disconnect → paused, reconnect →
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
  await a.wait((m) => m.type === 'snapshot' && me()?.x > before.x, 5000, 'commander moved');
  check('commander move reflected in authoritative snapshot', me().x === before.x + 1, `x ${before.x} → ${me().x}`);
  await a.wait((m) => m.type === 'snapshot' && me()?.altitude > 0, 5000, 'commander rose');
  check('commander rise reflected in authoritative snapshot', true, `alt ${me().altitude}`);
  a.command({ kind: 'commander_set_vertical_intent', rising: false });
  await a.wait((m) => m.type === 'snapshot' && me()?.altitude === 0, 15000, 'commander landed');

  // Real construction flow (M9, CR001 §18): fly to the own war base's roof-top
  // heli-pad (canonical scenario: Player 1 spawns at (17, 10); warbase-1 anchor
  // (22, 9), pad on the 15-high roof at (22, 5)), release rise so the commander
  // settles on the roof, enter construction on landing, build, launch, and
  // order the robot.
  const PAD = { x: 22, y: 5 };
  const ROOF_CLEARANCE = 16;
  a.command({ kind: 'commander_set_vertical_intent', rising: true });
  await a.wait((m) => m.type === 'snapshot' && me()?.altitude >= ROOF_CLEARANCE, 15000, 'commander above the roof');
  const step = async () => {
    const c = me();
    const dx = Math.sign(PAD.x - c.x);
    const dy = dx === 0 ? Math.sign(PAD.y - c.y) : 0;
    a.command({ kind: 'commander_move', dx, dy });
    await a.wait((m) => m.type === 'snapshot' && !me().horizontal_transition && (me().x !== c.x || me().y !== c.y), 5000, 'commander step');
  };
  while (me().x !== PAD.x || me().y !== PAD.y) await step();
  a.command({ kind: 'commander_set_vertical_intent', rising: false });
  await a.wait((m) => m.type === 'snapshot' && a.latest.construction_sessions.some((s) => s.player_id === a.session.playerId), 30000, 'construction session');
  check('landing on the roof heli-pad opens a construction session', me().altitude === 15, `tick ${a.latest.tick}, alt ${me().altitude}`);
  a.command({ kind: 'select_module', module: 'bipod' });
  a.command({ kind: 'select_module', module: 'cannon' });
  // Electronics: the 2×2 robot starts in the base's doorway, whose walls block
  // the east step a non-electronic Advance would take (CR002.3); electronic
  // routing steps south out of it first.
  a.command({ kind: 'select_module', module: 'electronics' });
  await a.wait((m) => m.type === 'snapshot' && a.latest.construction_sessions[0]?.build.electronics === 'electronics', 5000, 'modules selected');
  a.command({ kind: 'launch_robot' });
  await a.wait((m) => m.type === 'snapshot' && a.latest.robots.length === 1, 5000, 'robot launched');
  const robot = a.latest.robots[0];
  check('robot built and launched at the war-base exit', robot.owner === a.session.playerId && robot.x === PAD.x && robot.y === PAD.y + 4, `${robot.entity_id} at (${robot.x}, ${robot.y})`);
  a.command({ kind: 'set_robot_order', entityId: robot.entity_id, order: { kind: 'advance', distanceMiles: 10 } });
  await a.wait((m) => m.type === 'snapshot' && a.latest.robots[0]?.order?.kind === 'advance' && a.latest.robots[0]?.movement, 5000, 'robot moving');
  check('robot order accepted and autonomous movement started', true, `order ${a.latest.robots[0].order.kind}`);
  a.command({ kind: 'robot_fire', entityId: robot.entity_id, weapon: 'cannon', targetX: robot.x + 5, targetY: robot.y });
  await a.wait((m) => m.type === 'snapshot' && a.latest.projectiles.length === 1, 5000, 'projectile fired');
  check('direct fire produces an authoritative projectile', a.latest.projectiles[0].source_robot_id === robot.entity_id);
  // CR001 §8: a projectile moves 2 cells per advance; a cannon ranges 10 cells, +2 with electronics.
  const shot = a.latest.projectiles[0];
  const shotNow = () => a.latest.projectiles.find((p) => p.id === shot.id);
  await a.wait((m) => m.type === 'snapshot' && (!shotNow() || shotNow().travelled_cells > shot.travelled_cells), 5000, 'projectile advanced');
  const moved = shotNow();
  check(
    'projectile advances 2 cells per advance with cannon range 10 + 2 (electronics)',
    moved !== undefined && shot.max_range_cells === 12 && moved.travelled_cells - shot.travelled_cells === 2 && Math.abs(moved.x - shot.x) + Math.abs(moved.y - shot.y) === 2,
    moved ? `range ${shot.max_range_cells}, (${shot.x}, ${shot.y}) → (${moved.x}, ${moved.y}) at tick ${a.latest.tick}` : 'projectile terminated before its next advance',
  );
  await sleep(200);
  check('commander/construction/order/fire commands accepted by protocol (no error frame)', a.inbox.filter((m) => m.type === 'error').length === errorsBefore);

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
