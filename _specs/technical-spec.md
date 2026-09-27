# Nether Earth Clone — Technical Specification

## 1. Architecture summary

Version 1 is a browser-based, server-authoritative multiplayer game running on one VPS with Docker Compose.

```text
Browser
  |
  | HTTPS / WSS
  v
Nginx
  |---------------------> frontend static app
  |
  +---------------------> FastAPI backend
                              |
                              +-- MatchManager
                              |     +-- Match #1
                              |     +-- Match #2
                              |     +-- ...
                              |
                              +-- pure-Python deterministic engine
                              +-- replay/debug writer
```

No database, Redis, broker, Kubernetes, or per-match container is required for v1.

## 2. Locked stack

Frontend:

- TypeScript
- Vite
- PixiJS
- plain HTML/CSS
- plain-JSON WebSocket client
- no React in v1

Backend:

- Python
- FastAPI
- asyncio
- WebSockets
- one process hosting multiple matches

Engine:

- separate pure-Python package
- no FastAPI/network dependency
- deterministic fixed-tick simulation
- authoritative integer X/Y
- authoritative integer altitude/Z

Deployment:

- VPS
- Docker Compose
- Nginx
- mounted filesystem replay/debug storage

Map data:

- versioned YAML
- pre-saved original ZX Spectrum map

Protocol:

- plain JSON over WebSocket
- shared JSON Schema source of truth
- generated TypeScript types
- backend Pydantic validation

## 3. Repository responsibilities

The engine owns all gameplay rules. It must not depend on FastAPI, WebSockets, browser/rendering concerns, or deployment code.

Conceptual API:

```python
state = engine.new_game(map_data, scenario, players, seed, rules)
state, events = engine.step(state, commands)
```

The match layer owns orchestration only:

- fixed-tick scheduling
- command queues
- player sessions
- ready/join lifecycle
- reconnect/pause runtime state
- snapshots/broadcasts
- replay logging
- finalization

FastAPI owns transport/session APIs only.

Frontend owns rendering, interpolation, input collection, menus, and connection state. It must never decide gameplay legality or authoritative outcomes.

## 4. Simulation timing and determinism

Authoritative simulation: **20 Hz**.

```text
1 tick = 50 ms
1 in-game hour = 120 ticks
12 in-game hours = 1,440 ticks
1 in-game day = 2,880 ticks
```

All gameplay timers derive from tick count.

Determinism requirement:

```text
same map/scenario/rules
+ same RNG seed
+ same accepted command stream
= same resulting state
```

All randomness must use one match-local seeded engine RNG. Never use wall-clock/process randomness for gameplay.

## 5. Centralized game-rule configuration

Spectrum defaults and tunable values must live in one engine-owned, versioned rules/config object so replays record exactly which values were used.

Representative fields:

```python
GameRules:
    simulation_hz = 20
    ticks_per_game_hour = 120
    ticks_per_game_day = 2880

    miles_to_cells = 2

    factory_capture_ticks = 1440
    warbase_capture_ticks = 1440

    max_robots_per_player = 24

    starting_general_resources = 20
    factory_daily_production = 2
    warbase_daily_general_production = 5

    component_costs = {
        "bipod": 3,
        "tracks": 5,
        "anti_grav": 10,
        "cannon": 2,
        "missile": 4,
        "phaser": 4,
        "nuclear": 20,
        "electronics": 3,
    }

    commander_min_altitude = 0
    commander_max_altitude = 48
    commander_vertical_update_ticks = 4
    commander_ascent_step = 2
    commander_descent_step = 2  # owner deviation from the Spectrum's 1 (CR003.1)
    commander_exit_elevate_updates = 5  # lift after construction exit and undock

    normal_projectile_altitude = 10
    projectile_advance_ticks = 4
    projectile_cells_per_advance = 2
    robot_fire_cycle_ticks = 4
    robot_launch_exit_steps = 5

    weapon_damage_multipliers = {
        "cannon": 2,
        "missile": 3,
        "phaser": 4,
    }

    reconnect_grace_seconds = 60  # runtime/server default, not engine tick state
```

Not every gameplay value should be an environment variable. Gameplay configuration is versioned game data. Environment configuration remains deployment-only.

Rules identity: `nether_earth.rules.RULES_VERSION` names the rule set, and `rules_content_hash()` hashes the `EngineRules` values. Both are recorded in every replay. Replay verification rejects an artifact whose version or hash differs from the running engine's (`ReplayRulesMismatchError`) before it replays anything. The hash catches value changes by itself; a change to rule logic or rule-bearing map data must bump `RULES_VERSION`. CR002 changed rule logic and map data, so it bumps the version to `cr002` once (CR002.16); `cr001` replays are rejected. CR003 changed rule logic and values (descent step, capture orders, piece heights, Search & Destroy approach, commander step order), so it bumps the version to `cr003` once (CR003.8); `cr002` replays are rejected.

## 6. Scenario and victory model

Scenario data is separate from map geometry.

```python
Scenario:
    id
    player_starting_warbases
    neutral_warbases
    factory_initial_ownership
    rules_overrides
    victory_rule
```

Default PvP scenario:

```text
P1 war base = extreme-left
P2 war base = extreme-right
two interior war bases = neutral/capturable
factories = neutral unless overridden
starting general resources = 20
victory = opponent owns zero war bases
```

Victory is checked in the same engine step after any war-base capture/destruction event.

## 7. Map/world representation

### 7.1 Grid

Authoritative X/Y are integers. Rendering may interpolate visually.

Shared conversion:

```text
1 mile = 2 cells
1 cell = 0.5 miles
```

The conversion must exist once in engine helper/rule code and be reused by orders, combat, nuclear effects, UI serialization, and replays.

### 7.2 Terrain

Required terrain types:

```python
NORMAL
ROUGH
MOUNTAIN
DITCH
```

Terrain is cell metadata, not a solid occupant.

### 7.3 Static structures

Do not model factories/war bases as one generic rectangle.

Recommended model:

```python
Structure:
    id
    kind
    owner
    components: list[StructureComponent]
    interaction_zones
    destroyed

StructureComponent:
    relative_x
    relative_y
    height
    blocks_movement
    semantic_role | None
```

War-base semantic metadata includes heli-pad, exit, capture zone, ownership/resource behavior.

The war-base heli-pad is the 2×2 area on the roof anchored at (anchor.x, anchor.y − 4); the map lists its four cells. The commander lands when its whole 2×2 body is over the pad at an altitude equal to the highest component under it (15). The exit is the anchor cell: a launched robot's anchor starts there (`open-questions.md` §18, §21).

Scenery is map data (`blockers` in `data/maps/zx-spectrum-original.yaml`, generated by `data/maps/decode_zx_terrain.py`): one entry per 2×2 Spectrum element with its cells, height, an opaque `kind` (`box_low`, `box_high`, `fence`) and `destructible: true` for boxes. The engine treats blockers as static components and never branches on `kind`; the frontend maps `kind` to an asset. A nuclear blast records destroyed boxes in `GameState.scenery_debris`; a robot killed in combat on plain ground records its anchor in `GameState.robot_debris` (CR005.3). `destruction.scenery_world`/`effective_world` derive a world where debris blockers and destroyed buildings are gone, and their cells and each robot-debris 2×2 are rough at `terrain.debris_height`, memoized, without mutating the base `WorldMap` (§18).

Terrain piece heights (CR002.21 #203): each terrain cell in the map data carries the height of its Spectrum map piece (`Ld7bc_map_piece_heights`: normal and ditch 0, rough 2 or 3, mountain 6), and `terrain.debris_height` (3) applies to debris cells. `TerrainGrid.height_at` exposes them. One surface-height function, `collision.surface_height_at` (the highest component or terrain piece on a cell) with `collision.unit_surface_height` (its 2×2 maximum), serves commander collision, landing and gravity, projectile termination, the damage `ground_height` (`combat.ground_height_at`) and the heli-pad rest altitude. Frontend shadows read the same map heights; terrain is drawn flat.

Robots on terrain (CR002.25 #214): `collision.robot_top(world, robot)` is the static surface under the robot's 2×2 body at its authoritative anchor (`unit_surface_height` in `scenery_world`) plus its stack height. The anchor moves when a move completes, so the altitude follows it then. `RobotFixture.altitude`/`top` (`engine._robot_fixtures`) carry the top to commander collision and landing, `docking.attempt_auto_dock` and the docked commander's riding (`follow_docked_robot`). The same top is used by `movement.commander_blocks_robot_cell` (a commander below a robot's top blocks its move, `Lb513`), by `destruction.destroy_robot` (the ejected commander is left at the top) and by the undock lift start. The projectile hit gate compares the top (§16). No schema change: the frontend derives the altitude from map heights and the anchor (`render/robot.ts` `robotGround`), draws the robot raised by it and draws a docked commander on the drawn top.

Factory metadata includes production type and capture zone.

Physical component/cell heights may vary within one structure.

## 8. Occupancy and reservations

World state should distinguish:

- terrain;
- static physical occupancy/components;
- robots;
- commander collision volumes;
- robot destination reservations.

Robots, the commander and projectiles are 2×2 bodies (`open-questions.md` §21). A unit's `(x, y)` is the anchor of the body covering `x..x+1`, `y−1..y`; the whole body stays on the map. Two bodies collide when they overlap, which is the Spectrum's 3×3 anchor scan (`occupancy.unit_footprint_cells`, `unit_footprints_overlap`). Snapshots carry the anchor; clients derive the body.

A robot move reserves its whole destination body as soon as the move is accepted.

Recommended state:

```python
GridTransition:
    entity_id
    from_cell
    to_cell
    started_tick
    duration_ticks

Reservation:
    cell
    entity_id
```

Target reservation blocks other robots from starting a conflicting move.

If multiple valid robots contend for overlapping unreserved destination bodies during the same authoritative tick, choose the winner of each contention group (in canonical destination-anchor, entity-id order) using the match-local deterministic RNG:

- 2 contenders: 50/50;
- N contenders: uniform choice.

The RNG decision must be replay-recordable through initial seed + deterministic execution order.

## 9. Commander model

Recommended authoritative state:

```python
Commander:
    player_id
    mode: FREE | DOCKED
    x
    y
    altitude
    docked_robot_id
    elevate_updates_remaining  # automatic lift, 0 when idle
```

Vertical physics runs every `commander_vertical_update_ticks` simulation ticks.

Default behavior (Spectrum-compatible except gravity):

```text
min altitude = 0
max altitude = 48
vertical cadence = every 4 ticks
ascent = +2
fall/gravity = -2   (Spectrum: -1; owner decision CR003.1 #216, open-questions.md §13)
```

Gravity descends one altitude unit at a time up to `commander_descent_step` and stops at the last legal altitude, so it lands exactly on a surface at an odd altitude (static component, terrain, robot top or another commander) rather than skipping past it or stopping a step above it (`commander_movement._gravity_landing_altitude`).

Horizontal and vertical movement may occur simultaneously.

Step order (CR003.10 #232, `open-questions.md` §23): `engine.step` resolves due commander horizontal transitions before it applies the tick's commander commands, so a `commander_move` on the completion tick starts at once and held moves take exactly `commander_horizontal_move_ticks` (4) per cell with no idle tick. Robots already resolve due moves before starting new ones.

Automatic lift: leaving the construction screen (EXIT MENU or a successful START ROBOT, `construction_session.exit_construction`) and undocking from a robot (CR002.24 #207) set `elevate_updates_remaining = commander_exit_elevate_updates` (5); `docking.apply_undock` sets it without moving the commander, and the ascent runs on the following cadence ticks. While it is above 0, each vertical update ascends by `commander_ascent_step` whatever the rise intent, and consumes one, even if the ascent is clamped or blocked (Spectrum `Lfd30_player_elevate_timer`). Horizontal moves do not consume it (owner decision 2026-09-21). Neither construction entry nor auto-dock (`docking.attempt_auto_dock`, `engine.step` Step 7) is checked while the lift runs; a commander that falls back onto the same friendly robot's anchor after it docks again.

Construction is modal per player: while a player has an open construction session, that player's commander moves and vertical physics are no-ops (rise intent is still recorded). Other players and all robots keep running (PvP adaptation of the Spectrum's whole-game pause, owner decision 2026-09-21).

### 9.1 Collision

Commander collision is a 3D-ish X/Y + vertical-range test.

- commanders collide with robots, structures, scenery and each other, using the 2×2 body: the highest surface (structure, scenery, terrain) under the four body cells, and every robot or commander whose body overlaps;
- same X/Y is permitted only when vertical ranges are disjoint;
- overlapping opposing commanders block horizontal and vertical movement;
- commander is never targetable/damageable/destructible.

### 9.2 Docking

Descending onto the top of a friendly robot, with the commander's anchor on the robot's anchor, transitions:

```text
FREE -> DOCKED(robot_id)
```

Rising away undocks and starts the automatic lift (§9). Docking on a robot that is walking out of its war base ends the walk-out.

Descending onto an enemy robot stops at the top of its physical stack. No docking, control transfer, or contact damage occurs.

## 10. Capture model

Factory and war-base capture use continuous occupation by default.

```python
CaptureProgress:
    structure_id
    capturing_player_id
    robot_id
    elapsed_ticks
    required_ticks
```

Default `required_ticks = 1440`.

A robot qualifies when its anchor is on the structure's capture cell (Spectrum `Ladb7_building_loop`).

If the qualifying occupation condition becomes false, reset immediately to zero.

On completion:

1. change ownership;
2. emit ownership event;
3. recompute relevant resource/ownership counts;
4. evaluate victory in the same simulation step.

## 11. Economy model

Player state:

```python
ResourcePool:
    general
    chassis
    electronics
    cannon
    missile
    phaser
    nuclear
```

Default start:

```text
general = 20
all specific pools = scenario/original defaults
```

Production every 2,880 ticks:

- owned factory: +2 to its production pool;
- owned war base: +5 general.

### 11.1 Construction spending algorithm

Preserve the Spectrum algorithm:

```python
def spend_for_component(buffer, resource_type, cost):
    specific_used = min(buffer[resource_type], cost)
    buffer[resource_type] -= specific_used
    remaining = cost - specific_used

    if buffer.general < remaining:
        reject_selection()

    buffer.general -= remaining
```

Construction uses a temporary resource buffer copied from actual player resources.

Deselection must restore resources using reversible original semantics: restore the specific pool up to its original amount, and return any excess to general resources.

Actual resources are copied/committed back only when `construction_start_robot` succeeds. `construction_exit` before launch performs no permanent spend.

## 12. Robot build model

```python
RobotBuild:
    chassis
    cannon: bool
    missile: bool
    phaser: bool
    nuke: bool
    electronics: bool
```

Validation:

- exactly 1 chassis;
- 1–3 weapons;
- at most one of each weapon;
- at most one electronics;
- nuke may be the only weapon.

Maximum robots/player: 24.

### 12.1 Canonical stack

One engine function derives physical/render order and total height:

```text
chassis
cannon
missile
phaser
nuke
electronics
commander (when docked)
```

Missing components are omitted while preserving relative order.

Renderer, collision, docking, construction preview, and projectile interaction consume the same stack metadata.

## 13. Robot movement

All movement sources call one shared low-level movement layer:

```python
try_move_robot(robot_id, direction, state, rules) -> MoveResult
```

Used by:

- direct human control;
- autonomous robot orders;
- the AI opponent's planner (§28), through the same ordinary `Command`s a human issues.

Locked terrain permissions:

Locked ticks per cell (`-` = blocked), from `open-questions.md` §4:

```text
            normal  rough  mountain  ditch
Bipod:        24      32      -        -
Tracks:       16      24     28        -
Anti-grav:    12      12     16       12
```

These are per-(chassis, terrain) integer tick fields in `EngineRules`, not multipliers. Terrain classes: `NORMAL`, `ROUGH`, `MOUNTAIN`, `DITCH`.

Relative ordinary-terrain speed:

```text
bipod < tracks < anti-grav
```

The terrain for a move is the highest piece under the destination 2×2 body (`movement.unit_move_terrain`: mountain > rough > ditch > normal). A move is legal only when every destination body cell is on the map and enterable by the chassis, no structure or blocker cell is in it, and no other robot, commander or reservation overlaps it.

Robot updates (CR002.19 #197): an order-driven robot acts only on its own update. It is at an update when no move is in flight and at least one period has passed since its `last_fire_tick`; the period is `move_duration_ticks` for the terrain under its body (`autonomous_combat.autonomous_update_due`, `autonomous_update_period_ticks`). On an update it fires if it has a shot (the move is dropped by `gate_order_requests`), and otherwise moves.

Launch walk-out: a launched robot holds Stop & Defend with `Robot.exit_steps_remaining = robot_launch_exit_steps` (5). On each of its updates, starting the tick after launch, it requests one step south (+y) through the normal move batch (`orders.walk_out_request`); the counter drops when the step starts. It ends at 0 when a step is illegal or loses contention, when the update fires, when a commander docks on the robot, or when an order is assigned (`autonomous_combat.settle_walk_outs`).

## 14. Navigation policies

Navigation is separated from movement legality.

```python
NavigationPolicy:
    choose_next_move(robot, world) -> Direction | None
```

Non-electronic policy:

- deliberately limited/original-style local routing;
- may get stuck despite an available longer path.

Electronic policy:

- deterministic proper pathfinding/replanning;
- routes around obstacles when a chassis-compatible path exists.

Electronics never overrides terrain restrictions.

Robot targets (CR003.4 #219): a Search & Destroy (robots) goal is another robot's occupied anchor, so navigation closes on the target's body (`navigation.next_hunt_step`). Electronic robots plan to the anchors lane-aligned with the target's body, else to any anchor from which their 2×2 body touches it (`body_alignment_anchors`, `body_contact_anchors`, `plan_route_to_any`), instead of reporting the occupied goal `UNREACHABLE`; non-electronic robots keep greedy steps and stop beside the target when the overlapping step is refused.

Hunts keep their order (CR004.13 #299, owner decision 2026-09-27): a Search & Destroy (robots) order never falls back because no route exists; its only fallbacks are "no enemy robot" and "no weapon capable against robots". An electronic hunter caches its route on the robot (`Robot.hunt_route`: target id, planned tick, origin, one `E`/`W`/`S`/`N` letter per step, or no route) and follows it between re-plans. It re-plans every `EngineRules.robot_hunt_replan_ticks` ticks (default 20), and early when the route is exhausted, the robot is off its cached route, the next cell is no longer enterable (occupied, reserved or impassable terrain), or target selection picks a different robot. With no route it takes one greedy primary step toward the target's nearest aligned anchor through normal move legality, and waits when that step is illegal. Any order change, a fallback, or docking clears the cache.

## 15. Robot orders

Recommended domain model:

```python
StopAndDefend
Advance(distance_miles)
Retreat(distance_miles)
SearchCapture(target_type, structure_id)  # structure_id: engine-bound current target
SearchDestroy(target_type)
```

`Advance`/`Retreat`: 0–50 miles, converted using shared 2-cells-per-mile rule.

Impossible orders revert to Stop & Defend.

`SearchCapture` never completes or falls back (CR003.2 #217, Spectrum `Lb289`). Each evaluation keeps `structure_id` while its live ownership still matches the order, and otherwise selects the nearest matching structure that no other same-owner robot with the same `SearchCapture` target type holds (`Lb36c`), ties broken by structure id. On an uncaptured target's capture cell the robot holds with the Stop & Defend intent while `capture.py` counts the occupation; once captured it retargets and leaves. With no match it holds and resumes when a structure matches again. `structure_id` is serialized in snapshots/replays and cleared on order assignment; the order-command payload is unchanged.

## 16. Projectiles and firing

Normal projectiles:

```python
Projectile:
    owner_robot_id
    weapon_type
    x
    y
    altitude
    direction
    remaining_range
    first_advance_tick
```

A robot has at most one active normal projectile channel. A new normal shot is rejected while its current projectile is active. The channel is per robot, a documented deviation from the Spectrum's per-side bullet slots (`open-questions.md` §8).

Default normal projectile altitude: **10** for cannon, missile, and phaser, independent of robot height.

Projectile lifecycle must be authoritative world logic. It must not depend on browser viewport dimensions.

Resolved from the Spectrum code (`open-questions.md` §8): a projectile advances `projectile_cells_per_advance = 2` cells every `projectile_advance_ticks = 4` ticks and ends after 10 cells (cannon, phaser) or 14 cells (missile), +2 with electronics. Buildings use the generic altitude collision; there is no separate building rule.

Decided (`open-questions.md` §8, CR002.2 #169): as in the Spectrum, a projectile makes its first move on the fire tick, with the same checks as every later advance. A robot fires at most one normal weapon per game cycle (`robot_fire_cycle_ticks = 4`). An autonomous shot moves again at the cadence tick that closes its fire cycle; a direct shot is held one cycle longer (`Projectile.first_advance_tick`). Later advances keep the cadence above, so the range is unchanged.

A projectile is a 2×2 body. Each advance walks up to 2 cells; at each landing position it is stopped by bounds, then when the highest static surface under its body is `>= normal_projectile_altitude`, then it hits the first robot (scan order rows y−1, y, y+1, west to east) whose body overlaps and whose top (`collision.robot_top`: terrain altitude + stack height) is `>= normal_projectile_altitude` (owner decision 2026-09-22). Commanders and projectiles never stop it (documented deviation 1 in `open-questions.md`).

Nuclear detonation is modeled separately.

## 17. Damage and robot strength

Central engine function:

```python
def compute_normal_weapon_damage(robot_height, ground_height, weapon, rules):
    base = spectrum_integer_semantics((60 - (robot_height + ground_height)) / 4)
    return base * rules.weapon_damage_multipliers[weapon]
```

Canonical multipliers:

```text
cannon = 2
missile = 3
phaser = 4
```

Remaining research:

- exact Z80 truncation/rounding path;
- hit probability/accuracy;
- range impact on accuracy;
- strength representation;
- component damage, if any;
- electronics resistance/accuracy/range details.

Do not duplicate combat math outside the engine.

## 18. Nuclear detonation

Blast shapes follow the Spectrum code (`functional-spec.md` §17.3, `open-questions.md` §20). They are not a uniform radius.

On detonation:

1. robots: destroy every robot inside the carrier-centred 9×9 window with trimmed corners (row widths 5/7/9/9/9/9/9/7/5);
2. buildings: scan war bases, then factories, in canonical order; destroy the **first** one in range (war base: dx<7, dy<7, dx+dy<10; factory: dx<5, dy<5, dx+dy<7; dy measured from carrier.y+1, plus 4 for war bases); at most one building per detonation;
3. destroy carrier robot;
4. scenery: every map blocker marked `destructible` whose bottom-left (anchor) cell is inside the robot window becomes debris. `GameState.scenery_debris` records its id (canonical order, snapshotted). The effective world drops the blocker and makes its cells rough terrain, and the base `WorldMap` is not mutated. Fences are not `destructible`;
5. update ownership/victory;
6. emit deterministic events.

The shape parameters are `EngineRules` data.

Autonomous detonation happens only on arrival at the target cell of a Search & Destroy factory/war-base order (`functional-spec.md` §16). No weapon-selection path may choose nuclear for any other autonomous order.

Nuclear is the only way to destroy factories/war bases.

## 19. Match runtime and command ordering

Recommended runtime:

```python
class Match:
    engine_state
    command_queue
    players
    tick_task
    replay_writer
    connection_state
```

Per active tick:

1. drain commands eligible for next tick;
2. apply deterministic command ordering;
3. call engine step;
4. record accepted commands/events;
5. broadcast authoritative snapshot/delta;
6. finalize when completed.

Scheduler drift compensation must never change integer simulation-time semantics.

## 20. Disconnect/reconnect runtime policy

This is runtime/session policy, not deterministic game-state progression.

- any player disconnect pauses the match immediately;
- while paused, engine ticks/gameplay timers do not advance;
- default grace period = 60 wall-clock seconds, configurable server/match value;
- reconnect sends current authoritative snapshot;
- simulation resumes only when both players are connected;
- grace expiry causes forfeit when the opponent remains eligible to win;
- if both disconnect, each gets an independent deadline;
- if both expire without either returning, finalize as abandoned/no-contest;
- no manual pause in v1.

The engine state remains unchanged during disconnect pause.

## 21. Protocol

Client commands include:

```text
ready
commander_input
construction_action
robot_control_action
robot_order
robot_fire
```

Server messages include:

```text
match_joined
match_started
snapshot
state_delta
event
command_rejected
match_paused
match_resumed
match_ended
```

JSON Schema is the protocol source of truth. Generated TS types and backend Pydantic validation must remain synchronized.

## 22. Snapshot/replay requirements

Snapshot minimum:

- tick/game clock;
- players/resources;
- commander states (including `elevate_updates_remaining`);
- robots + transitions/reservations (including `last_fire_tick` and `exit_steps_remaining`);
- ownership/capture progress;
- projectiles (including `first_advance_tick`);
- scenery debris (`scenery_debris`);
- robot launches (`robot_launches`, per-owner monotonic id counters, §28.2);
- robot hunt routes (`hunt_route` on a robot, CR004.13 §14): additive, elided when `None`;
- map/scenario/rules versions;
- match result;
- AI planner memory (`AiMemory`), when a seat is computer-controlled (§28).

Replay/debug log minimum:

- map version;
- scenario version;
- game-rules version/content hash;
- RNG seed;
- accepted commands in authoritative order;
- important engine events;
- final result.

Replay contract:

```text
initial state + rules + accepted commands + RNG seed => same result
```

## 23. Frontend rendering/state

Frontend keeps:

```text
latest_authoritative_snapshot
previous_authoritative_snapshot
visual_interpolation_state
local_menu_state
connection_state
```

No client-authoritative movement or outcome prediction in v1.

PixiJS scene layers may include:

```text
terrain
static structures
robots
commanders
projectiles/effects
selection/highlights
HUD/menus
```

Renderer assets identify semantic types; they do not encode gameplay rules.

Spectrum presentation (CR002; `functional-spec.md` §19):

- `render/projection.ts`: the Spectrum isometric axes (`AXIS_X` = (8, −4), `AXIS_Y` = (4, 8) px per cell, `Z_PX` = 1 px per height unit; world units are Spectrum pixels) and its exact inverse for picking. `VIEW_SPAN_PX` sets the zoom.
- One depth-sorted scene holds structure cells, scenery slices, robots, commanders and projectiles (occlusion). Units are drawn as 2×2 bodies from their snapshot anchor.
- `render/surface.ts` gives the highest surface under a footprint for shadows, from the same heights the engine uses.
- Scenery: `frontend/public/assets/manifest.json` maps each blocker `kind` (and `debris`) to a sprite asset; changing the mapping needs no code change. A kind without a valid mapping draws as a placeholder prism.
- Ownership flags (`render/flags.ts`), the radar (`ui/radar.ts`: 128-column scrolling window, white only, own commander only), the full-screen construction screen (`ui/construction.ts`) and the self-hosted Spectrum fonts are presentation only.
- Construction costs shown in the UI come from `src/generated/rules/construction.json`, a build-time export of the `EngineRules.module_cost_*` defaults (`npm run rules:generate`). CI fails on drift; costs are not in the protocol. The UI never decides spending, weapon caps or launch validity.
- Audio (`src/audio/`, #272): `title-score.ts` is the title music lifted from the disassembly by `frontend/scripts/decode-music.py`; `score.ts` interprets that bytecode one 50 Hz frame at a time; `spectrum.ts` and `sfx.ts` reproduce the original routines' T-state timing, so pitches and durations are derived rather than invented; `engine.ts` plays them through WebAudio; `events.ts` derives the in-game sounds from consecutive snapshots. No protocol, engine or backend change: audio never crosses the boundary. The noise routines' ROM reads are replaced by a seeded PRNG, the one deliberate deviation. Mute is per viewer in `localStorage` (key `M`).
- Structure labels and robot strength numbers are an optional overlay, off by default (`ui.labels`, key `L`, saved per viewer in `localStorage`).

## 24. Deployment configuration

Environment-only settings may include:

```text
HOST
PORT
REPLAY_DIR
PUBLIC_BASE_URL
```

Gameplay rules are versioned engine/scenario data, not arbitrary environment variables.

Single-host Docker Compose:

```text
nginx
frontend
backend
```

Nginx terminates HTTPS and proxies API/WebSocket traffic.

## 25. Testing priorities

Engine tests are highest priority.

Required coverage includes:

- deterministic ticks/clock/RNG;
- map/static composition;
- terrain permissions;
- commander ascent/descent cadence and collision;
- commander-vs-commander collision;
- friendly docking/enemy-robot contact;
- construction validation and original spending/refunds/atomic commit;
- canonical stack/height;
- production/capture/reset/victory;
- destination reservations and seeded contention;
- dumb/electronic navigation;
- direct control/orders;
- projectile firing gate/altitude/lifecycle;
- damage/nuclear destruction;
- replay determinism.

Backend/runtime tests:

- create/join/ready;
- WebSocket ownership/session validation;
- disconnect pause/reconnect snapshot/resume;
- grace-timeout forfeit;
- both-disconnected no-contest;
- multiple matches/process;
- cleanup.

Protocol tests:

- schemas valid;
- generated TS current;
- representative messages validate.

Integration test should run a scripted deterministic match end-to-end and produce identical final replay hashes across repeated runs.

## 26. Remaining fidelity research

Remaining gameplay research areas:

1. exact combat accuracy, integer rounding, strength handling, and electronics modifiers;
2. the autonomous fire-decision scan distance (`open-questions.md` §8).

Movement timing and scenery blockers (§4), projectile speed, range, lifetime and fire-cycle timing (§8), and the 2×2 bodies (§21) are resolved: projectiles advance 2 cells every 4 ticks, with the first move on the fire tick; ranges are 10/14/10 cells, +2 with electronics. The deviations from the original that the owner kept are recorded in `open-questions.md`.

Until verified from the fidelity evidence chain, keep these behind isolated engine policies/configuration and do not silently treat guesses as canonical defaults.

## 27. v1 non-goals

Do not add unless scope explicitly changes:

- PostgreSQL;
- Redis;
- Celery/RQ;
- Kubernetes;
- multiple backend replicas;
- account system;
- persistent matchmaking;
- client-authoritative movement;
- generic ECS migration without a concrete need;
- a backend-hosted or difficulty-configurable AI (§28 is the only AI in scope).

## 28. AI opponent (CR004)

The computer-controlled seat is an engine-side deterministic planner, not a backend bot session.

- It runs inside `engine.step` and emits ordinary `Command`s for its `PlayerId`, through the same
  `validate_command`/`order_commands` path a human's commands take. No privileged path, no direct
  state mutation, no new rule.
- Its carry-over state (`AiMemory`: build intent, per-robot assignments, threat bookkeeping) lives
  in `GameState` and round-trips through `snapshot.py` and `replay.py` (§22); a replay never
  records the AI's commands, it re-derives them by re-simulating from scenario + map version +
  seed + the human's command stream alone.
- `nether_earth.ai.planner.plan(state, memory, world, rules, seed)` is a pure function of
  `(GameState, AiMemory, EngineRules)`. The engine derives its per-decision `seed` as
  `rng.derive_seed(match_seed, "ai", player_id, tick)`; `plan` derives one further seed per
  sub-planner (`derive_seed(seed, name)`) from it, so each sub-planner's `MatchRandom` stream is
  independent. Iteration order is canonical, never set/dict order.
- It decides on a fixed cadence, `ai_decision_interval_ticks` (default 4), a centralized rules
  constant rather than an emergent property of loop speed.
- It has no commander: the AI seat simply has no entry in `state.commanders` (a tuple of the
  commanders that exist), not a `None` placeholder standing in for one. Every site that looks up a
  commander for a given seat must tolerate there being none.

### 28.1 Planner structure (shipped)

`nether_earth.ai.planner.plan(state, memory, world, rules, seed)` chains two sub-planners, each
owning its own slice of `AiMemory` and drawing from its own seeded `MatchRandom`
(`derive_seed(seed, name)`, so their draws never interfere with each other):

- **`ai.construction`** — economic/build policy. Picks the best design the current pool can
  afford (`DESIGNS`, `choose_design`), keeps a weapon-count floor and a defence reserve of
  general resources, builds a nuclear robot on a fixed army-size cadence, and rotates over every
  owned war base (skipping one whose exit is blocked). It enters construction through
  `EnterConstructionRemotelyCommand` — a second, commander-less entry into the shared
  `construction_session` layer alongside the human's heli-pad entry — then issues the same
  `SelectModule`/`LaunchRobot` commands a human's construction screen would, so cost, the spend
  rule, and the robot cap are all enforced by the existing engine code, never duplicated.
- **`ai.robot_orders`** — per-robot order policy. Predicts what the engine's own
  `select_capture_target`/`select_destroy_target`/exclusivity rule (`orders.claimed_structures`,
  CR003.2) will choose, and issues `SetRobotOrderCommand`s that value-rank capture targets
  (production value, distance, contestation), detect an enemy robot closing on an owned war base
  or factory and divert or hold a defender, and send nuclear carriers only at opponent-owned
  targets. It never picks a specific building directly; the engine's own nearest-unclaimed rule
  still does that.

Both planners read only information a player's own client would render (own resources, both
sides' structure ownership and robot positions); neither reads the opponent's resource pool,
construction session or orders. Neither planner changes what any command does — every command it
issues is validated exactly like a human's, so no new gameplay rule was added to support the AI.

### 28.2 Match lifecycle (shipped)

A solo match (`MatchManager.create_solo_match`) is created `WAITING` with one human slot and no
join code; the AI seat (`Match.ai_player_id`) is not a `PlayerSlot` and never appears in the
roster, ready list, or reconnect bookkeeping, so it can never itself pause the match or contribute
to a no-contest. It is always ready, so the human's own `ClientSetReady` is the only step left
before the match starts. Reconnect behaves exactly like PvP for the human: a disconnect pauses
with the usual grace window, and letting it expire forfeits to the AI.

Robot ids (CR004.12, #295 — a pre-existing PvP bug pulled into CR004 because
solo matches crash without it): each robot's id is `robot-<owner>-<n>`, where
`n` comes from a monotonic per-owner counter (`RobotLaunchCount`, carried in
`GameState.robot_launches`) rather than the owner's live robot count. The
counter only ever grows — a robot's death never lowers it — so an id is never
reused within a match, even after every robot a player ever launched has
died (the AI, which can rebuild its whole army repeatedly in one match, hits
this far more than PvP ever did). `robot_launches` is an additive snapshot
key, appended last: it is elided from the wire while no player has launched
a robot yet (empty), and present once one has, the same elision convention
as `ai_memories`. Because it is additive, a replay
recorded before CR004.12 has no `robot_launches` entries; the engine rejects
it as incompatible via the recorded rules version (`RULES_VERSION`) rather
than guessing a counter for it.

Wire protocol: `createMatch.opponent: "human" | "computer"` (absent means `"human"`, so an
existing PvP `create` is byte-for-byte unchanged); `created.joinCode` is nullable (`null` for a
solo match, since there is no second human slot to join) but still required as a key, in its
declared position; `created.opponent` mirrors the request only when it is `"computer"`. Only
human-submitted commands are persisted to `commands.jsonl`; a replay artifact records which seats
are AI-controlled (`meta.json`'s `seat_controllers`, e.g. `{"p1": "human", "p2": "ai"}`) and the
AI's commands are re-derived by stepping the engine, never recorded. An artifact from before
`seat_controllers` existed is treated as all-human.
