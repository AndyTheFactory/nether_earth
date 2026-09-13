# Milestone 8 — Frontend Rendering, Controls & Game UI

## Goal

Implement the browser client that renders authoritative game state, interpolates motion visually, collects player input, exposes construction/order/combat controls, and communicates exclusively through the shared protocol without duplicating engine legality.

## Spec references

- `_specs/functional-spec.md` browser/client interaction requirements across commander, robots, construction, orders, and combat
- `_specs/technical-spec.md` §2–§4 — locked frontend stack and responsibility boundary
- `_specs/technical-spec.md` frontend interpolation/networking guidance

## Start dependencies

- Milestone 0 frontend/protocol bootstrap.
- Stable snapshot/protocol fixtures from Milestone 1/Milestone 7 groundwork.
- Milestone 2 world/map contract for real-map rendering work.

Renderer and UI development may begin against deterministic recorded fixtures before live multiplayer is complete.

## Completion dependencies

- Milestone 7 stable live protocol/snapshot/event transport.
- Engine behavior from Milestones 2–6 complete for every v1 action exposed by the UI.

## Deliverable

A Vite + TypeScript + PixiJS client that can create/join a match, render the original-style battlefield and entities, interpolate authoritative transitions, send explicit commands, display game state and interaction menus, and recover from authoritative snapshots.

## Workstreams and candidate tasks

### Network client
Implement create/join/ready flows, WebSocket lifecycle, generated protocol types, command submission, snapshot/event handling, and reconnect UI state.

### World renderer
Render map terrain, structures, robots, commanders, ownership distinctions, projectiles/effects, and camera/view behavior using authoritative state plus visual-only interpolation.

### Commander controls
Map keyboard input to explicit commander commands, including horizontal movement and rise/descend intent. Client animation may interpolate between authoritative states but cannot change legality.

### Robot and order controls
Implement docking-aware command menus, Direct Control, autonomous-order selection, distance/target choices, and Combat Control using engine-supported commands.

### Construction UI
Present chassis/weapons/electronics selection, resource availability, build validation feedback from authoritative state, launch/scrap/exit actions, and canonical stack preview derived from shared state/metadata.

### HUD and match UI
Show nickname/join code where relevant, connection/readiness state, game clock, resources, robot strength, ownership/victory/result information, and meaningful validation/error feedback.

### Visual fidelity and assets
Create the original-style 2.5D presentation using project reference material and approved assets. Rendering should preserve the game's visual character rather than redesign it into a modern RTS.

## Parallelization

Renderer work can proceed against deterministic recorded snapshots while network/client UI work proceeds against the evolving versioned protocol. Commander, construction, robot-control, combat UI, and HUD tasks can run in parallel once their command/state contracts are stable. Final M8 integration connects fixture-driven screens to representative live-match flows without requiring a complete game from start to victory.

## Acceptance criteria

- No React or other unapproved UI framework is introduced.
- Frontend uses generated/shared protocol types rather than handwritten duplicate message models.
- Client never decides movement, firing, capture, resource, docking, or victory legality.
- Authoritative X/Y/Z and transitions render correctly with visual interpolation only.
- Input produces explicit protocol commands.
- Construction, orders, direct control, and combat controls expose all v1 actions supported by the engine.
- Ownership, strength, resources, game time, match state, and victory/result states can be rendered correctly.
- Snapshot/reconnect replaces or resynchronizes client state safely.
- Production build succeeds and major screens/states can be exercised from deterministic fixtures.
- Representative live interactions work through the real backend/protocol.

## Milestone integration scenario

Use deterministic recorded fixtures to exercise all major rendered states and interaction surfaces: commander movement, docking, construction, robot movement/orders, combat/projectiles, ownership changes, resources, reconnect snapshot, and victory/result rendering.

Then run a shorter live two-client integration flow through the real backend that proves create/join/ready, representative commander input, at least one construction/robot-control interaction, authoritative updates, and reconnect/resynchronization. A complete real match from creation through victory is intentionally reserved for Milestone 9.

## Out of scope

- Full end-to-end PvP acceptance match; owned by Milestone 9.
- New gameplay rules or client-side prediction that alters authority.
- Accounts/social features.
- Mobile-specific control redesign unless later explicitly added.
- Major visual modernization away from the specified original-style presentation.

## Open questions / blockers

Frontend work must consume resolved engine rules rather than resolve gameplay questions itself. Any ambiguity discovered through UI implementation is routed back to the relevant engine/spec milestone.

Asset/reference conflicts or a visual interpretation that materially changes gameplay readability/fidelity require owner review. Routine rendering implementation details remain autonomous.

## Definition of done

- All milestone issues are closed by merged PRs.
- Fixture-driven rendering/interaction verification covers the full v1 client surface.
- Representative live frontend/backend interactions and reconnect pass.
- Complete full-match acceptance remains unclaimed and is deferred to Milestone 9.
- No gameplay authority has leaked into the browser.
