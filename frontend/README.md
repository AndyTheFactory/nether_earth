# Nether Earth frontend (Milestone 8)

Vite + TypeScript + PixiJS browser client. It renders authoritative state,
interpolates visually, collects input, and talks to the backend only through
the generated protocol types. No gameplay rule lives here.

## Run

```bash
npm install
npm run dev           # http://localhost:5173
npm test              # vitest unit tests (store, interpolation, input, fixtures)
npm run build         # map:generate + typecheck + vite build
npm run live:check    # two-client protocol check against a running backend (NE_WS_URL)
```

Backend for live mode: `cd backend && uvicorn app.main:app --port 8010`, then
open `http://localhost:5173/?ws=ws://localhost:8010/ws`. In production the
client uses same-origin `/ws` (see `deploy/nginx.conf`).

URL parameters:

| param | effect |
|-------|--------|
| `fixture=<id>` | boot straight into a recorded fixture (no backend) |
| `until=<n>` | stop a fixture after n messages (freeze a mid-sequence screen) |
| `ws=<url>` | WebSocket URL override |
| `auto=create&nick=X` / `auto=join&code=C&nick=X` | skip the lobby form |
| `autoready=1` | send ready as soon as a session is bound |
| `resume` | reconnect with the session saved in `sessionStorage` |

## Layout

```
src/state/store.ts       authoritative store: latest/previous snapshot, lifecycle, connection, UI slots
src/net/client.ts        GameClient interface; WebSocketClient + RecordingClient (fixture sink)
src/net/commands.ts      CommandSender: generated envelopes, clientSequence
src/input/keyboard.ts    key state → explicit move/vertical/action intents; focus loss releases
src/app/controller.ts    intents + menu actions → protocol commands; live/fixture modes; reconnect
src/render/projection.ts 2:1 dimetric world→screen, one definition
src/render/interpolation.ts visual-only interpolation over engine transition records
src/render/renderer.ts   PixiJS layers: terrain, structures, entities, projectiles, effects, overlay
src/render/assets.ts     semantic-id asset pipeline with explicit placeholders
src/ui/*.ts              plain-DOM lobby, HUD, construction/robot/combat menus, overlays
src/fixtures/index.ts    typed recorded message streams; validated against protocol schemas in tests
src/generated/maps/*.json map YAML converted by scripts/generate-map.mjs (format only)
```

## Authority boundary

- Snapshots are stored verbatim (`SnapshotState` from `protocol/generated/types.ts`).
- A `resync` replaces state atomically, clears the previous snapshot, and bumps
  `resyncGeneration`; the renderer drops interpolation/effect memory on that signal.
- Interpolation reads `horizontal_transition` / `vertical_transition` / `movement`
  records and a display tick capped at `latest.tick + 1`; it never writes back.
- The game clock is derived from `tick` only and freezes whenever the lifecycle
  phase is not `active`.
- Input produces one explicit command per intent. The UI chooses which command
  *type* to offer from authoritative mode (FREE/DOCKED, construction session
  present), never whether the command is legal.
- Menus show authoritative order/strength/projectile-channel state; nothing is
  enforced locally (for example firing while a projectile is active still sends
  the command and lets the engine decide).

## Fixtures

`FIXTURES` in `src/fixtures/index.ts` cover: static world, commander moving/
rising, docked commander, construction (mixed spending, rejection, cancel,
launch), robot orders/navigation/capture, combat/projectiles/destruction/
nuclear, lobby waiting, paused, reconnect resync, victory, loss, forfeit,
no-contest. Every message validates against `protocol/schemas` in
`fixtures.test.ts`, so a fixture screen and a live screen consume identical
message shapes.

Fixtures are hand-authored against the real map coordinates rather than
recorded from the engine; the schema test is what keeps them aligned.

## Assets and fidelity

`public/assets/manifest.json` maps semantic ids to image files; missing
entries render as procedural Spectrum-palette prisms (see
`public/assets/README.md` for provenance rules). Presentation conventions:

- 2:1 dimetric projection, +x down-right, +y down-left, 4 px per height unit.
- Ownership: p1 cyan, p2 magenta, neutral white. Factories are yellow with a
  type label; destroyed structures collapse to a dark 1-unit slab.
- Robot stacks draw the snapshot's `stack` array bottom-up in the order the
  engine already canonicalised; module heights are scaled to the authoritative
  `height` so the docked commander sits on the true top.
- Camera follows the local commander (or its docked robot). `G` toggles the
  debug grid, interaction points and capture-progress overlay.

Known presentation limits (not gameplay): entities always draw above static
structures (no cross-layer occlusion), and there is no real art yet.

## Live check status

`npm run live:check` against the real backend passes 15/15 checks: create,
join, ready and start; commander move and rise; landing on the roof-top
heli-pad (altitude 15, open-questions §18) opens a construction session; build and launch at the war-base exit; an Advance order
starts autonomous movement; direct fire creates a projectile; disconnect
pauses with a frozen tick; reconnect resyncs and resumes; client state equals
the server snapshot byte for byte. The backend plays every match on the
canonical `pvp-v1` scenario and the original map (Milestone 9).

Module costs are not exposed by the protocol; the construction panel shows the
authoritative session buffer and committed pool instead of a second cost table.
Engine command rejections are not transmitted (snapshot-only broadcast policy),
so rejection feedback is limited to protocol `error` frames plus the absence of
a state change.
