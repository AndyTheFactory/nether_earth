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
npm run rules:generate # re-export module costs from engine rules.py (CI checks drift)
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
src/render/projection.ts Spectrum-orientation world→screen, depth key and zoom, one definition
src/render/interpolation.ts visual-only interpolation over engine transition records
src/render/renderer.ts   PixiJS layers: terrain, one depth-sorted scene (structures, units, shots), effects, overlay
src/render/surface.ts    surface height under a footprint (roofs, heli-pad) for shadows
src/render/assets.ts     semantic-id asset pipeline with explicit placeholders
src/ui/*.ts              plain-DOM lobby, HUD, construction/robot/combat menus, overlays
src/fixtures/index.ts    typed recorded message streams; validated against protocol schemas in tests
src/generated/maps/*.json map YAML converted by scripts/generate-map.mjs (format only)
src/generated/rules/construction.json module costs read from engine rules.py by scripts/generate-rules.mjs
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
no-contest, occlusion behind a war base, roof/heli-pad shadows. Every message validates against `protocol/schemas` in
`fixtures.test.ts`, so a fixture screen and a live screen consume identical
message shapes.

Fixtures are hand-authored against the real map coordinates rather than
recorded from the engine; the schema test is what keeps them aligned.

## Assets and fidelity

`public/assets/manifest.json` maps semantic ids to image files; missing
entries render as procedural Spectrum-palette prisms (see
`public/assets/README.md` for provenance rules). Presentation conventions:

- Spectrum orientation (`_specs/milestones/cr002/main-screen.png`): the map
  runs lower-left to upper-right; one cell step is (8,-4) px along +x and
  (4,8) px along +y, 1 px per height unit, in Spectrum pixels.
- Zoom: the shorter side of the view shows `VIEW_SPAN_PX` (168) Spectrum
  pixels, the original's play-window size; the one tunable in `projection.ts`.
- Structures, scenery, robots, commanders and projectiles share one painter's
  order, so units behind a block are hidden by it. Shadows land on the
  surface under them (ground, roof, heli-pad).
- Ownership: p1 cyan, p2 magenta, neutral white. Factories are yellow;
  destroyed structures collapse to a dark 1-unit slab.
- Ownership flags (CR002.6, `render/flags.ts`): the Spectrum flag sprites on
  the roof, 4 (war base) or 2 (factory) cells behind the anchor and to the -x
  side for p1 (the Spectrum's human flag) or the +x side for p2 (the
  Insignian flag, checkered). Neutral and destroyed structures carry none.
  Structure name/owner text labels show only with labels on (`L`) or the
  debug grid (`G`).
  Fixture: `?fixture=ownership-flags` (`&until=4|5|6` holds neutral/p1/p2).
- Robot stacks draw the snapshot's `stack` array bottom-up in the order the
  engine already canonicalised; module heights are scaled to the authoritative
  `height` so the docked commander sits on the true top.
- Camera follows the local commander (or its docked robot). `G` toggles the
  debug grid, interaction points and capture-progress overlay.
- Labels (CR002.23): structure name labels and robot strength numbers are an
  optional overlay, **off by default**. `L` toggles them; the choice is saved
  per viewer in `localStorage` (`nether.labels`, ignored when storage is
  unavailable). `?labels=1` / `?labels=0` overrides the saved choice for that
  page load. The debug grid (`G`) still shows structure names, not strengths.
- Radar (CR002.22, `src/ui/radar.ts`): as on the Spectrum, a 128-column
  window of the map at one pixel per cell, all marks white. Structures,
  scenery boxes and fences are lit (debris and destroyed structures are not);
  every robot is a 2x2 mark (x..x+1, rows y-1..y); the local commander is a
  blinking 2x2 mark (its docked robot blinks). The window starts at column 0
  and scrolls 64 columns when the commander comes within 16 columns of an
  edge, clamped to the map; there is no view-window indicator. Source:
  `Lafe6_radar_scroll`, `Ld5f8_update_radar_buffers`,
  `Ld65a_flip_2x2_radar_area` in the disassembly.

Known presentation limits (not gameplay): text labels, when on, draw above
the scene (a hidden robot's strength stays visible), and there is no real art yet.

## Live check status

`npm run live:check` against the real backend passes 16/16 checks: create,
join, ready and start; commander move and rise; landing on the roof-top
heli-pad (altitude 15, open-questions §18) opens a construction session; build and launch at the war-base exit; an Advance order
starts autonomous movement; direct fire creates a projectile that moves 2
cells per advance with cannon range 10 (open-questions §8); disconnect
pauses with a frozen tick; reconnect resyncs and resumes; client state equals
the server snapshot byte for byte. The backend plays every match on the
canonical `pvp-v1` scenario and the original map (Milestone 9).

## Robot construction screen (CR002.9)

Landing on a war-base heli-pad opens the full-screen ROBOT CONSTRUCTION screen
(`src/ui/construction.ts`), laid out after the original (reference
`_specs/milestones/cr002/construction-screen.png`). RESOURCES AVAILABLE is the
authoritative session buffer; the module costs come from
`src/generated/rules/construction.json`, a format-only export of the engine's
`EngineRules.module_cost_*` defaults (the protocol does not carry costs), so
there is no second cost table in the UI. Controls follow the Spectrum
(disassembly `Lca0f`/`Lcb00`): the cursor starts on BIPOD; up/down walk the
module list, left/right move between the list, START ROBOT and EXIT MENU; a
held arrow repeats every 200 ms; Space or Enter fires on the cursor (toggle a
module, start the robot, or exit). Clicking an option moves the cursor there
and fires. Shortcuts: 1-3 chassis, 4-7 weapons, 8 electronics, Esc/C exit.
Fitted modules draw white, the others yellow (`Lcc1f`); the option under the
cursor is yellow. Every action is an existing command; the engine decides.
Engine command rejections are not transmitted (snapshot-only broadcast policy),
so rejection feedback is limited to protocol `error` frames plus the absence of
a state change.
