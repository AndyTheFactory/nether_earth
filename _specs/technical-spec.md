# Nether Earth Clone — Technical Specification

This document states the implemented architecture and its constraints. Gameplay rules are in [functional-spec.md](functional-spec.md); the engine algorithms are explained, module by module, in [docs/mechanics/](../docs/mechanics/README.md).

## 1. Architecture summary

Version 1 is a browser-based, server-authoritative multiplayer game running on one VPS with Docker Compose.

```text
Browser
  |
  | HTTP(S) / WS(S)
  v
gateway (Nginx, the only published port)
  |---------------------> frontend (Nginx serving the static app)
  |
  +--- /ws, /api/ ------> backend (FastAPI + uvicorn, one process)
                              |
                              +-- MatchManager (lobby, sessions, sweep)
                              |     +-- Match #1 --- MatchRuntime (20 Hz loop)
                              |     +-- Match #2 --- MatchRuntime
                              |     +-- ...
                              +-- ReconnectCoordinator (pause/grace/forfeit)
                              +-- pure-Python deterministic engine
                              +-- ReplayWriter (one worker thread) -> host replay directory
```

No database, Redis, broker, Kubernetes, or per-match container is required for v1. Active matches live in memory and end with the process.

## 2. Locked stack

Frontend:

- TypeScript, Vite, PixiJS
- plain HTML/CSS
- plain-JSON WebSocket client
- no React in v1
- built with Node 22; served as static files

Backend:

- Python 3.12
- FastAPI on uvicorn, asyncio, WebSockets
- one process hosting multiple matches

Engine:

- separate pure-Python package (`engine/src/nether_earth`)
- no FastAPI/network/I/O dependency
- deterministic fixed-tick simulation
- authoritative integer X/Y and integer altitude

Deployment:

- one VPS, Docker Compose, Nginx gateway
- host-mounted replay/debug directory

Map data:

- versioned YAML (`data/maps/zx-spectrum-original.yaml`), decoded from the ZX Spectrum map by `data/maps/decode_zx_terrain.py`; provenance in `data/maps/zx-spectrum-original.md`

Protocol:

- plain JSON over WebSocket
- shared JSON Schema source of truth (`protocol/schemas/`)
- generated TypeScript types (`protocol/generated/`)
- backend Pydantic validation

## 3. Repository responsibilities

The engine owns all gameplay rules. It must not depend on FastAPI, WebSockets, browser/rendering concerns, or deployment code.

API:

```python
world = map_overlay.apply_overlay(map.load_world_map(path), map_overlay.default_pvp_overlay(...))
state = scenario.create_initial_state(scenario, world, seed=seed)   # commanders, pools, ownership
state, events = engine.step(state, commands, world=world)
```

`engine.new_game(map_data, scenario, players, seed, commanders)` builds a bare tick-0 state for tests and fixtures. `world` is the scenario-overlaid `WorldMap`; `engine.step` derives every per-tick world view (ownership overrides, destruction, debris) from it and the state.

The match layer owns orchestration only:

- fixed-tick scheduling
- command queues and client sequence checks
- player sessions and the lobby
- ready/join lifecycle
- reconnect/pause runtime state
- snapshot broadcasts
- replay logging
- finalization

FastAPI owns transport/session APIs only.

The frontend owns rendering, interpolation, input collection, menus, and connection state. It must never decide gameplay legality or authoritative outcomes.

## 4. Simulation timing and determinism

Authoritative simulation: **20 Hz**.

```text
1 tick = 50 ms
1 Spectrum game cycle = 4 ticks
1 in-game hour = 120 ticks
12 in-game hours = 1,440 ticks
1 in-game day = 2,880 ticks
```

All gameplay timers derive from the tick count (`clock.py`); nothing in the engine reads wall-clock time.

Determinism requirement:

```text
same map/scenario/rules
+ same RNG seed
+ same accepted command stream
= same resulting state
```

All randomness comes from one match-local seed. Each consumer derives its own stream with `rng.derive_seed(...)` (a 64-bit mix of integers and CRC-32 of strings) and builds a fresh `rng.MatchRandom` from it: destination contention per tick, the non-electronic detour draw per robot and window, and the AI planner per seat and decision tick. No RNG state is stored in `GameState`. Never use wall-clock/process randomness for gameplay. Iteration that can influence outcomes uses canonical (id-sorted) order.

`engine.step` runs one tick in this fixed order (details in [docs/mechanics/timing-and-determinism.md](../docs/mechanics/timing-and-determinism.md)):

1. AI seats plan and append their commands to the batch; the batch is validated and sorted by `(player, sequence)`.
2. Commander horizontal moves that are due complete, then the tick's commander move and vertical-intent commands apply.
3. Robot moves and turns that are due complete.
4. Order assignments apply; every standing order is evaluated; order moves are gated on the robot update.
5. The tick's robot moves (orders, walk-outs, direct control) start as one deconflicted batch.
6. Combat: projectiles advance and damage, direct fire and nuclear detonation, autonomous fire.
7. Capture progress; victory check after a war-base capture.
8. Undocking, commander vertical physics, auto-docking, docked commanders follow their robots.
9. Heli-pad landing opens construction; construction commands apply (enter, select, deselect, cancel, launch).
10. Daily production at a day boundary; the tick counter advances.

## 5. Centralized game-rule configuration

Spectrum defaults and tunable values live in one engine-owned, frozen, versioned object, `rules.EngineRules` (`DEFAULT_RULES`), so replays record exactly which values were used. Every gameplay constant that governs legality is a field there, never a literal at its point of use. The full field list, with values, is in [docs/mechanics/README.md](../docs/mechanics/README.md#rule-constants). Groups:

- commander envelope and timing (`commander_min_altitude` 0, `commander_max_altitude` 48, `commander_vertical_update_ticks` 4, `commander_ascent_step` 2, `commander_descent_step` 2, `commander_height` 4, `commander_horizontal_move_ticks` 4, `commander_exit_elevate_updates` 5);
- module heights (`module_height_*`) and costs (`module_cost_*`), `starting_general_resources` 20, `factory_production_amount` 2, `war_base_production_amount` 5, `max_robots_per_player` 24;
- robot movement ticks per (chassis, terrain) (`robot_move_ticks_<chassis>_<terrain>`), `robot_turn_ticks` 4, `dumb_wander_commit_ticks` 16, `robot_hunt_replan_ticks` 20, `robot_launch_exit_steps` 5;
- capture (`capture_duration_ticks` 1440);
- weapons (`cannon_range_cells` 10, `missile_range_cells` 14, `phaser_range_cells` 10, `electronics_range_bonus_cells` 2, `normal_projectile_altitude` 10, damage multipliers 2/3/4, `projectile_advance_ticks` 4, `projectile_cells_per_advance` 2, `robot_fire_cycle_ticks` 4);
- nuclear blast shape (`nuclear_robot_window_row_widths`, `nuclear_building_dy_offset`, `nuclear_war_base_extra_dy_offset`, axis and sum limits per building kind);
- AI cadence (`ai_decision_interval_ticks` 4).

The mile is a unit definition, not a tunable: `rules.CELLS_PER_MILE = 2`, converted only through `rules.miles_to_cells`. Terrain permissions per chassis are legality tables in `movement.py`. AI strategy weights live in the `ai/` package, not in `EngineRules`: they change what the AI chooses, never what is legal.

Not every gameplay value should be an environment variable. Gameplay configuration is versioned game data. Environment configuration is deployment-only (§24).

Rules identity: `rules.RULES_VERSION` (currently `"blink"`) names the rule set, and `rules.rules_content_hash()` is the SHA-256 of the canonical JSON of the `EngineRules` values. Both are recorded in every replay. Replay verification rejects an artifact whose version or hash differs from the running engine's (`ReplayRulesMismatchError`) before it replays anything. The hash catches value changes by itself; a change to rule logic or rule-bearing map data must bump `RULES_VERSION`.

## 6. Scenario and victory model

Scenario data is separate from map geometry.

```python
Scenario:
    id
    map_id
    map_version
    player_starting_warbases
    starting_general_resources = 20
    factory_initial_ownership = "neutral"
    victory_rule = "zero_war_bases"
    player_one_controller = "human"   # "human" | "ai"
    player_two_controller = "human"
```

Default PvP scenario overlay (`map_overlay.default_pvp_overlay`):

```text
P1 war base = extreme-left (smallest min-x)
P2 war base = extreme-right (largest max-x)
two interior war bases = neutral/capturable
factories = neutral
P1 commander = P1 war-base capture cell + (-5, +1)
P2 commander = P2 war-base capture cell + (+5, +1)
starting general resources = 20, type-specific pools 0
victory = opponent owns zero war bases
```

A solo match uses the same scenario with `player_two_controller = "ai"`: that seat gets an `AiMemory` and no commander.

`victory.evaluate_victory` is called in the same step after a war-base capture or a nuclear destruction of a war base; it reports a winner when exactly one player still owns a war base. `engine.step` emits at most one victory event per tick.

## 7. Map/world representation

### 7.1 Grid

Authoritative X/Y are integers; the original map is 512 × 16. Rendering may interpolate visually.

Shared conversion:

```text
1 mile = 2 cells
1 cell = 0.5 miles
```

The conversion exists once (`rules.miles_to_cells`) and is reused wherever a mile distance appears (Advance/Retreat). Weapon ranges and blast shapes are cell values.

### 7.2 Terrain

Terrain types:

```python
NORMAL
ROUGH
MOUNTAIN
DITCH
```

Terrain is cell metadata (`terrain.TerrainGrid`), not a solid occupant. Each decoded terrain cell carries its Spectrum piece height (`Ld7bc_map_piece_heights`: normal and ditch 0, rough 2 or 3, mountain 6), and `terrain.debris_height` (3) applies to debris cells. `TerrainGrid.height_at` exposes them.

### 7.3 Static structures

Factories and war bases are not one generic rectangle.

```python
WarBase / Factory / Blocker:
    id
    components: tuple[Component(x, y, height), ...]
    owner            # war bases and factories; None = neutral
    factory_type     # factories only
    kind, destructible   # blockers only

InteractionPoint:
    id
    kind: HELI_PAD | EXIT | WARBASE_CAPTURE | FACTORY_CAPTURE
    structure_id
    footprint
```

The war-base heli-pad is the 2×2 area on the roof anchored at (anchor.x, anchor.y − 4); the map lists its four cells. The commander lands when its whole 2×2 body is over the pad at an altitude equal to the highest component under it (15). The exit and the capture cell are the anchor cell: a launched robot's anchor starts there.

Scenery is map data (`blockers`): one entry per 2×2 Spectrum element with its cells, height, an opaque `kind` (`box_low`, `box_high`, `fence`) and `destructible: true` for boxes. The engine treats blockers as static components and never branches on `kind`; the frontend maps `kind` to an asset.

Runtime changes never mutate the base `WorldMap`. They are recorded in `GameState` and layered on by memoized derivations:

- `capture.effective_world`: ownership overrides (`structure_ownership`);
- `destruction.effective_world`: ownership plus destruction — destroyed structures and debris blockers removed, their cells and every robot-debris 2×2 turned into rough terrain at `terrain.debris_height`;
- `destruction.scenery_world`: debris only, no ownership overrides; the physical world for robot move validation and commander collision.

One surface-height function, `collision.surface_height_at` (the highest component or terrain piece on a cell), with `collision.unit_surface_height` (its 2×2 maximum, the Spectrum's `Lb5d6_map_altitude_2x2`), serves commander collision, landing and gravity, projectile termination, the damage `ground_height`, robot altitude (`collision.robot_top` = altitude + stack height) and the heli-pad rest altitude. Frontend shadows read the same map heights.

## 8. Occupancy and reservations

World state distinguishes:

- terrain;
- static physical occupancy (structure and blocker components);
- robots;
- commander collision volumes;
- robot destination reservations.

Robots, the commander and projectiles are 2×2 bodies. A unit's `(x, y)` is the anchor of the body covering `x..x+1`, `y−1..y`; the whole body stays on the map. Two bodies collide when they overlap, which is the Spectrum's 3×3 anchor scan (`occupancy.unit_footprint_cells`, `unit_footprints_overlap`). Snapshots carry the anchor; clients derive the body.

Robot occupancy is not stored: `movement.folded_robot_occupancy` folds every live robot's body into the static grid. Reservations are not stored either: `reservations.reservations_from_state` derives them from every robot's in-flight `RobotMoveTransition` destination. A robot mid-move occupies its origin body and reserves its destination body.

```python
RobotMoveTransition:
    entity_id
    from_x, from_y
    to_x, to_y
    started_tick
    duration_ticks
```

A reserved destination blocks other robots from starting a conflicting move.

If several valid robots claim overlapping unreserved destination bodies on the same tick, `reservations.apply_robot_move_batch` groups them in canonical (destination anchor, entity id) order and picks each group's winner with `MatchRandom(derive_seed(match_seed, tick))`:

- 2 contenders: 50/50;
- N contenders: uniform choice.

The outcome is reproducible from the initial seed and deterministic execution order, and is also recorded as a `DestinationContentionResolvedEvent`.

## 9. Commander model

```python
Commander:
    player_id
    mode: FREE | DOCKED
    x, y
    altitude
    docked_robot_id
    rising                     # held rise intent
    horizontal_transition      # GridTransition | None
    vertical_transition        # for interpolation
    elevate_updates_remaining  # automatic lift, 0 when idle
```

Vertical physics runs on ticks that are positive multiples of `commander_vertical_update_ticks`.

Defaults:

```text
min altitude = 0
max altitude = 48
vertical cadence = every 4 ticks
ascent = +2
fall/gravity = -2
vertical extent (collision) = 4
horizontal move = 4 ticks per cell
```

Gravity descends one altitude unit at a time up to `commander_descent_step` and stops at the last legal altitude, so it lands exactly on a surface at an odd altitude (static component, terrain, robot top or another commander) rather than skipping past it or stopping a step above it (`commander_movement._gravity_landing_altitude`).

Horizontal and vertical movement may occur simultaneously.

Step order: `engine.step` resolves due commander horizontal transitions before it applies the tick's commander commands, so a `commander_move` on the completion tick starts at once and held moves take exactly `commander_horizontal_move_ticks` per cell with no idle tick. Robots also resolve due moves before starting new ones.

Automatic lift: leaving the construction screen (EXIT MENU or a successful START ROBOT, `construction_session.exit_construction`) and undocking (`docking.apply_undock`) set `elevate_updates_remaining = commander_exit_elevate_updates`; the ascent runs on the following cadence ticks. While it is above 0, each vertical update ascends by `commander_ascent_step` whatever the rise intent and consumes one, even if the ascent is clamped or blocked. Horizontal moves do not consume it. Neither construction entry nor auto-dock is checked while the lift runs; a commander that falls back onto the same friendly robot's anchor afterwards docks again.

Construction is modal per player: while a player has an open construction session, that player's commander moves and vertical physics are no-ops (rise intent is still recorded). Other players and all robots keep running.

A seat may have no commander (the AI seat): `state.commanders` holds only the commanders that exist, and every lookup (`commander_for`) tolerates there being none.

### 9.1 Collision

Commander collision is an X/Y + half-open vertical-range test (`collision.py`): touching is resting, overlapping is blocked.

- commanders collide with robots, structures, scenery and each other, using the 2×2 body: the highest surface (structure, scenery, terrain) under the four body cells, and every robot or commander whose body overlaps;
- a commander occupies `[altitude, altitude + commander_height)`; a robot occupies `[0, top)`; a component `[0, height)`;
- same X/Y is permitted only when vertical ranges are disjoint;
- overlapping opposing commanders block horizontal and vertical movement;
- the commander is never targetable/damageable/destructible.

### 9.2 Docking

A FREE commander whose altitude equals a friendly robot's top, with the same anchor, transitions:

```text
FREE -> DOCKED(robot_id)
```

While docked it follows the robot each tick and the robot's standing order is not evaluated. Holding rise undocks and starts the automatic lift (§9). Docking on a robot that is walking out of its war base ends the walk-out; docking also clears a cached hunt route.

Descending onto an enemy robot stops at the top of its physical stack. No docking, control transfer, or contact damage occurs. If a robot is destroyed with a commander docked on it, the commander is freed at the robot's last top.

## 10. Capture model

Factory and war-base capture use continuous occupation.

```python
CaptureProgress:
    structure_id
    capturing_player
    robot_id
    elapsed_ticks
```

`rules.capture_duration_ticks` defaults to 1440.

A robot qualifies when its anchor is on the structure's capture cell and it does not belong to the current owner (any robot qualifies for a neutral structure); with several, the lowest entity id counts (Spectrum `Ladb7_building_loop`).

If the qualifying robot changes or the cell is vacated, progress resets to zero.

On completion, in the same step:

1. change ownership (`StructureOwnership` override);
2. emit `StructureCapturedEvent`;
3. evaluate victory when a war base changed hands.

Production and every later step of the tick read the new ownership.

## 11. Economy model

```python
PlayerResourcePool:
    general
    chassis
    electronics
    cannon
    missile
    phaser
    nuclear
```

Start: `general = 20`, every type-specific pool 0. Pools have no cap.

At every day boundary (2,880 ticks, detected by integer day counts, `resource_production.apply_daily_production`):

- owned factory: +2 to its production pool;
- owned war base: +5 general.

### 11.1 Construction spending algorithm

The Spectrum algorithm (`construction_economy.py`):

```python
def spend_module(buffer, category, cost):
    if buffer[category] >= cost:
        buffer[category] -= cost
        return
    shortfall = cost - buffer[category]
    if buffer.general < shortfall:
        reject_selection()
    buffer[category] = 0
    buffer.general -= shortfall

def refund_module(buffer, category, cost, amount_at_entry):
    restore = min(cost, max(0, amount_at_entry - buffer[category]))
    buffer[category] += restore
    buffer.general += cost - restore
```

Construction uses a temporary resource buffer copied from the player's resources when the session opens; refunds are capped by the category amount recorded at that moment (`entry_snapshot`).

The buffer replaces the player's pool only when `LaunchRobotCommand` succeeds. `CancelConstructionCommand` (EXIT MENU) performs no spend.

## 12. Robot build model

```python
RobotBuild:
    chassis
    weapons: tuple[...]   # 1-3, canonical order cannon, missile, phaser, nuclear
    electronics | None
```

Validation:

- exactly 1 chassis;
- 1–3 weapons;
- at most one of each weapon;
- at most one electronics;
- nuke may be the only weapon.

Maximum robots alive per player: 24.

```python
Robot:
    entity_id, owner
    x, y                    # anchor
    build, stack, height
    strength = 100
    facing = SOUTH
    order
    movement | turning      # at most one in flight
    active_projectile_id
    last_fire_tick
    exit_steps_remaining
    hunt_route
```

Robot ids are `robot-<owner>-<n>`, where `n` comes from a monotonic per-owner launch counter (`GameState.robot_launches`), so an id is never reused within a match.

### 12.1 Canonical stack

One engine function (`robot_stack.derive_stack_and_height`) derives physical/render order and total height:

```text
chassis
cannon
missile
phaser
nuke
electronics
commander (when docked)
```

Missing components are omitted while preserving relative order. Renderer, collision, docking, construction preview, and projectile interaction consume the same stack metadata.

## 13. Robot movement

All movement sources call one shared validation and start point:

```python
movement.validate_robot_move(request, state, world, rules, destination_check) -> RobotMoveResult
movement.apply_robot_move(...)          # one move, used inside the batch
reservations.apply_robot_move_batch(requests, state, world, tick, rules, sequencer)
```

Used by:

- direct human control (`DirectRobotMoveCommand`, docked commander required);
- autonomous robot orders and launch walk-outs;
- the AI opponent's planner (§28), through the same ordinary `Command`s a human issues.

Ticks per cell (`-` = blocked):

```text
            normal  rough  mountain  ditch
Bipod:        24      32      -        -
Tracks:       16      24     28        -
Anti-grav:    12      12     16       12
```

These are per-(chassis, terrain) integer fields in `EngineRules`; the blocked pairs are `movement.CHASSIS_TERRAIN_PERMISSIONS`.

The terrain for a move is the highest-ranked class under the destination 2×2 body (`movement.unit_move_terrain`: mountain > rough > ditch > normal). A move is legal only when the robot has no move or turn in flight, every destination body cell is on the map and enterable by the chassis, no structure or blocker cell and no other robot is in it, no commander overlaps it below the robot's top, and no other robot's reservation overlaps it.

Turning: a step in a direction the robot does not face starts a `RobotTurnTransition` of `robot_turn_ticks` instead of a move (`RobotFacing.rotate_toward`: one 90-degree rotation per turn, a reversal takes two). The facing changes when the turn completes.

Robot updates: an order-driven robot acts only on its own update. It is at an update when no move is in flight and at least one period has passed since its `last_fire_tick`; the period is the move duration for the terrain under its body (`autonomous_combat.autonomous_update_due`, `autonomous_update_period_ticks`). On an update it fires if it has a shot (the move is dropped by `gate_order_requests`), and otherwise moves.

Launch walk-out: a launched robot holds Stop & Defend with `Robot.exit_steps_remaining = robot_launch_exit_steps`. On each of its updates, starting the tick after launch, it requests one step south (+y) through the normal move batch (`orders.walk_out_request`); the counter drops when the step starts. It ends at 0 when a step is illegal or loses contention, when the update fires, when a commander docks on the robot, or when an order is assigned (`autonomous_combat.settle_walk_outs`).

## 14. Navigation policies

Navigation is separated from movement legality: a policy proposes a step that `validate_robot_move` has already accepted, and the step is executed only through the tick's move batch.

```python
NavigationPolicy:
    next_step(robot, target_x, target_y, state, world, rules) -> NavigationDecision
    next_step_to_body(robot, target_x, target_y, state, world, rules) -> NavigationDecision
```

`navigation.navigation_policy_for` picks the policy from the build: electronics fitted → `ElectronicNavigation`, otherwise `NonElectronicNavigation`.

Non-electronic policy: candidate steps in order — the primary-axis step, the robot's current facing (momentum), the two perpendicular steps in an order shuffled by `MatchRandom(derive_seed(match_seed, tick // dumb_wander_commit_ticks, robot_id))`, then any other step. The first legal step is taken; `BLOCKED` only when no cardinal step is legal. It never reports `UNREACHABLE`.

Electronic policy: uniform-cost search (Dijkstra) over 2×2 body anchors with the move duration as edge cost, re-planned on every call; neighbours in fixed canonical order and ties broken by insertion order. `UNREACHABLE` when no route exists. The planner and the executor share one traversability predicate (`navigation.cell_is_enterable`).

Electronics never overrides terrain restrictions.

Robot targets: a Search & Destroy (robots) goal is another robot's occupied anchor, so navigation closes on the target's body (`navigation.next_hunt_step`). Robots plan to the anchors lane-aligned with the target's body, else to any anchor from which their 2×2 body touches it along an edge (`body_alignment_anchors`, `body_contact_anchors`, `plan_route_to_any`); non-electronic robots take greedy steps toward the nearest aligned anchor.

Hunt route cache: an electronic hunter stores its route on the robot (`Robot.hunt_route`: target id, planned tick, origin, one `E`/`W`/`S`/`N` letter per step, or no route) and follows it between re-plans. It re-plans every `robot_hunt_replan_ticks` ticks, and early when the route is exhausted, the robot is off its cached route, the next cell is no longer enterable (occupied, reserved or impassable terrain), or target selection picks a different robot. With no route it takes one greedy primary step toward the target's nearest aligned anchor through normal move legality, and waits when that step is illegal. Any order change, a fallback, or docking clears the cache. A Search & Destroy (robots) order never falls back because no route exists.

Performance: per-(state, world) traversal views, per-world static-blocked grids and per-(terrain, chassis, rules) step-cost tables are memoized by object identity; they are pure derivations and never change outcomes.

## 15. Robot orders

```python
StopAndDefend
Advance(distance_miles, target_x=None)      # target_x bound on first evaluation
Retreat(distance_miles, target_x=None)
SearchCapture(target, structure_id=None)    # target: neutral_factory | enemy_factory | enemy_war_base
SearchDestroy(target)                       # target: robot | factory | war_base
```

`Advance`/`Retreat`: 0–50 miles, converted with the shared 2-cells-per-mile rule; the goal column is bound on the first evaluation and clamped to the map.

`orders.evaluate_orders` evaluates every robot that holds an order and is not docked, in canonical robot order; evaluations are pure reads of one entry state, applied afterwards. Each yields the order to hold, a lifecycle status (`ACTIVE`, `COMPLETED`, `FALLBACK`), an optional move request and an optional engagement intent. Invalid or impossible orders revert to Stop & Defend.

`SearchCapture` never completes or falls back. Each evaluation re-selects (owner decision pending, see [open-questions.md](open-questions.md#3-owner-decisions-pending)) the nearest matching structure (by Manhattan distance to its capture cell, ties by structure id) that no other same-owner robot with the same target type holds (`orders.claimed_structures`; a robot that retargets earlier in the same tick is seen by later robots), except that a robot standing on its current target's capture cell keeps that target. On that cell it holds with the defensive intent, turning to face out of the structure (`capture.outward_facing`); with no match it holds. `structure_id` is serialized in snapshots/replays and cleared on order assignment; the order-command payload is unchanged.

`SearchDestroy` against structures requires a nuclear module and selects the nearest factory/war base not owned by the robot's owner; it completes on the tick the robot stands on the target's capture cell, with a structure intent that detonates (§18).

Engagement intents name a target and the robot's capable weapons; they fire nothing by themselves (§16).

## 16. Projectiles and firing

Normal projectiles:

```python
Projectile:
    id, owner, source_robot_id, weapon
    x, y                 # 2x2 body anchor
    z                    # normal_projectile_altitude
    dx, dy               # the firing robot's facing
    travelled_cells
    max_range_cells
    created_tick
    first_advance_tick
```

Fire validation (`combat.validate_fire`, then `combat.apply_fire`): the robot exists, belongs to the requesting player, has the weapon fitted; for a normal weapon, its channel is free (`CHANNEL_OCCUPIED`), it is not mid-turn (`TURNING`) and it has not fired in this fire cycle `tick // robot_fire_cycle_ticks` (`ALREADY_FIRED_THIS_CYCLE`). Nuclear passes straight to detonation. `FireCommand` is accepted for any robot the player owns; the client sends it only for the docked robot (owner decision pending, see [open-questions.md](open-questions.md#3-owner-decisions-pending)).

A robot has at most one active normal projectile. Default normal projectile altitude: **10** for cannon, missile, and phaser, independent of robot height. Projectile lifecycle is authoritative world logic and never depends on viewport dimensions.

A projectile advances `projectile_cells_per_advance = 2` cells on ticks that are positive multiples of `projectile_advance_ticks = 4`, and ends after 10 cells (cannon, phaser) or 14 cells (missile), +2 with electronics. Its first move is made by `apply_fire` on the fire tick, with the same checks as every later advance. Its next move is at `first_advance_tick`: the cadence tick that closes the fire cycle for an autonomous shot, one cycle later for a direct shot. Later advances keep the cadence, so the range is unchanged.

At each landing position it terminates, in order, when it is off the map, when the highest static surface under its body is `>= normal_projectile_altitude`, or when it hits the first robot (scan order rows y−1, y, y+1, west to east) whose body overlaps and whose top (`collision.robot_top`) is `>= normal_projectile_altitude`. Range exhaustion is checked before each move. Commanders and projectiles never stop it. Terminating releases the firing robot's channel; a hit applies damage (§17).

Autonomous fire (`autonomous_combat.consume_engagement_intents`) re-validates each intent on the robot's update: the source and target still exist, then the first capable normal weapon whose range (+ electronics bonus) reaches the target's anchor by Manhattan distance is chosen. If the robot does not face the target (dominant axis, east/west on a tie) it starts a turn instead; otherwise the shot goes through the same `apply_fire` path as direct fire. Nuclear is never chosen against a robot.

## 17. Damage and robot strength

Central engine function (`combat.calculate_weapon_damage`):

```python
def calculate_weapon_damage(weapon, robot_height, ground_height, rules):
    base = (60 - (robot_height + ground_height)) // 4
    return base * multiplier(weapon, rules)
```

Canonical multipliers:

```text
cannon = 2
missile = 3
phaser = 4
```

`ground_height` is `collision.unit_surface_height` under the target's body. Strength starts at 100; `combat.apply_damage` subtracts the damage and destroys the robot (`destruction.destroy_robot`) at ≤ 0. The robot is set to strength 0 with `destroyed_cycles_remaining = rules.robot_destroyed_blink_cycles` (4). `destruction.advance_destroyed_robots` counts this down on each game-cycle tick, first in `engine.step`. When the count is already 0 it removes the robot: first it records combat debris if the robot's four cells are plain ground, then `destruction.remove_robot` drops the robot's capture progress, frees a commander docked on it and removes it. A blinking robot is on the map (`Robot.present`) only while its count is even. It never acts by itself and is never fired at ([combat.md](../docs/mechanics/combat.md#destroyed-robots-destructiondestroy_robot-destructionadvance_destroyed_robots)). Its in-flight projectile keeps flying.

Do not duplicate combat math outside the engine. Remaining combat-fidelity research is tracked in [open-questions.md](open-questions.md).

## 18. Nuclear detonation

Blast shapes follow the Spectrum code (FS §17.3). They are not a uniform radius. `destruction.execute_nuclear_detonation`, measured from the carrier's position before any destruction:

1. robots: every other robot whose anchor is inside the carrier-centred window with row widths `nuclear_robot_window_row_widths`;
2. buildings: scan war bases, then factories, in map order (owner decision pending, see [open-questions.md](open-questions.md#3-owner-decisions-pending)); destroy the **first** one in range, measured to its capture cell (war base: dx<7, dy<7, dx+dy<10; factory: dx<5, dy<5, dx+dy<7; dy measured from carrier.y+1, plus 4 for war bases); at most one building per detonation;
3. scenery: every `destructible` blocker whose bottom-left (anchor) cell is inside the robot window becomes debris (`GameState.scenery_debris`); fences are not destructible;
4. destruction order: carrier, then robots in canonical order, then the building;
5. victory is evaluated when a war base was destroyed.

A destroyed structure is recorded in `GameState.structure_destruction`; the derived worlds drop it and turn its cells into debris. The base `WorldMap` is never mutated.

Autonomous detonation happens only on arrival at the target cell of a Search & Destroy factory/war-base order. No weapon-selection path may choose nuclear for any other autonomous order.

Nuclear is the only way to destroy factories/war bases.

## 19. Match runtime and command ordering

`MatchRuntime` (`backend/app/match/runtime.py`) runs one asyncio task per active match:

```python
class MatchRuntime:
    match            # Match: state, players, game_state, seed
    world            # scenario-overlaid WorldMap
    _pending         # queued commands for the next tick
    _last_accepted_sequence[player]
    on_tick_commands # replay recorder
    on_tick          # snapshot broadcast, then victory finalizer
```

Per active tick:

1. drain the queued commands;
2. call `engine.step` (which orders them by `(player, sequence)` and appends AI commands);
3. record the drained commands and events in the replay artifact;
4. broadcast a full authoritative snapshot to every connection of the match;
5. finalize when a victory event appears.

Command acceptance: `submit_command` rejects a command whose client sequence is not greater than the player's last accepted one (duplicates and replays); it makes no gameplay decision. Gameplay rejections happen in the engine and are silent (no extra event).

Scheduling: ticks are paced by the event-loop clock. When a tick overruns its 50 ms budget the next tick starts at once and the schedule is re-based to now (no catch-up burst); repeated overruns are logged. While a match is not `ACTIVE` (waiting, paused) the loop only polls. Scheduler drift never changes integer simulation-time semantics.

Match lifecycle (`MatchManager`): `WAITING` → `ACTIVE` when every human slot is ready (the engine state is created then) → `PAUSED_DISCONNECTED` ↔ `ACTIVE` → `FINISHED`. A sweep every 15 s disposes:

- finished matches after `finished_retention_s` (default 300 s);
- waiting lobbies after `waiting_timeout_s` (default 900 s);
- waiting lobbies with no attached socket after `abandoned_lobby_grace_s` (default 30 s);

and tells a disposed lobby's sockets `match_expired`. `ACTIVE`/`PAUSED` matches are never swept. At most `max_matches` (default 200) matches are held; beyond that `create` is refused (`ServerBusyError`).

Solo matches (`MatchManager.create_solo_match`): created `WAITING` with one human slot and no join code; the AI seat (`Match.ai_player_id`) is not a `PlayerSlot`, never appears in the roster, ready list or reconnect bookkeeping, and is always ready. The human's ready starts the match.

Nicknames (`match.manager._validate_nickname`): stripped; rejected when empty, when containing control (`Cc`), format (`Cf`, except U+200D between two `So`/`Sk`/`Mn` characters), surrogate, private-use or unassigned characters, bidirectional controls, or blank look-alikes (U+115F, U+1160, U+3164, U+FFA0, U+2800, U+034F), or when no character is a letter, number, punctuation or symbol. The schema limits length to 32. A join whose nickname equals the creator's after `unicodedata.normalize("NFKC", …).casefold()` is rejected with `invalid_nickname`. Join codes are 6 characters from `A–Z0–9` (`secrets.choice`); session tokens are `secrets.token_urlsafe(32)`.

## 20. Disconnect/reconnect runtime policy

This is runtime/session policy (`ReconnectCoordinator`), not deterministic game-state progression; it uses monotonic wall-clock deadlines, never engine ticks.

- any human player's disconnect pauses the match immediately;
- while paused, engine ticks/gameplay timers do not advance;
- default grace period = 60 wall-clock seconds, configurable per coordinator;
- reconnect (the `reconnect` message with the session token) cancels the player's deadline and sends a `resync` with the current authoritative snapshot;
- the simulation resumes only when every human player is connected;
- when a deadline expires, the deadline values are compared, not a fresh clock reading: if the opponent is connected or its deadline is later, the expiring player forfeits; if the opponent's deadline is equal or earlier, the match ends as no-contest (owner decision pending, see [open-questions.md](open-questions.md#3-owner-decisions-pending));
- the AI seat is never disconnected, so a solo human's expiry is a forfeit;
- no manual pause in v1.

The engine state is unchanged during a disconnect pause. A `leave` message is treated as a disconnect.

## 21. Protocol

JSON Schema (`protocol/schemas/*.schema.json`) is the protocol source of truth. The message names below follow the schemas (owner decision pending, see [open-questions.md](open-questions.md#3-owner-decisions-pending)). Generated TS types (`protocol/generated/types.ts`) and backend Pydantic models (`backend/app/protocol/`) must remain synchronized; CI regenerates the types and fails on drift. Every message carries `protocolVersion`.

Client messages:

```text
create      { nickname, opponent?: "human" | "computer" }
join        { joinCode, nickname }
ready       { matchId, playerId, sessionToken, ready }
leave       { matchId, playerId, sessionToken }
reconnect   { matchId, playerId, sessionToken }
command     { matchId, playerId, sessionToken, clientSequence, payload }
```

Command payloads (`kind` plus):

```text
commander_move                  { dx, dy }      one cardinal cell
commander_set_vertical_intent   { rising }
direct_robot_move               { dx, dy }      one cardinal cell
robot_fire                      { entityId, weapon }
set_robot_order                 { entityId, order: stop_and_defend | advance | retreat | search_capture | search_destroy }
select_module / deselect_module { module }
launch_robot
cancel_construction
```

Server messages:

```text
created      (joinCode null and opponent "computer" for a solo match)
joined
ready_state
started
snapshot     (full state, every tick)
paused / resumed / resync
finished / forfeit / no_contest
error        { code, message, details? }
```

Engine events are not transmitted; clients derive presentation (sounds, effects) from consecutive snapshots.

## 22. Snapshot/replay requirements

Snapshot (`nether_earth.snapshot.to_snapshot`) contents:

- tick and match seed;
- players, resource pools and open construction sessions;
- commander states (including transitions and `elevate_updates_remaining`);
- robots with transitions, turns, `facing`, `strength`, `last_fire_tick`, `exit_steps_remaining` and `hunt_route` (elided when `None`);
- structure ownership, capture progress, structure destruction;
- projectiles (including `first_advance_tick`);
- scenery debris and robot debris;
- robot launch counters (`robot_launches`, elided while empty);
- AI planner memory (`ai_memories`), when a seat is computer-controlled.

Additive keys are appended and elided while empty so older consumers keep working.

Replay artifact (`backend/app/replay/writer.py`), one directory per match:

```text
<replay_dir>/<match_id>/meta.json        header: schema version, rules version + hash,
                                         scenario, map id/version/size, seed, nicknames,
                                         seat controllers, status, final tick/result/snapshot
<replay_dir>/<match_id>/commands.jsonl   one line per tick: accepted human commands + event summary
<replay_dir>/<match_id>/lifecycle.jsonl  pause/resume/forfeit/no-contest with wall-clock time
```

Only human-submitted commands are persisted; AI commands are re-derived by stepping the engine. Session tokens are never written. `meta.json` is rewritten atomically (temp file + `os.replace`).

Writes run on a single worker thread so they never block the event loop and each match's lines stay in order. At most `MAX_PENDING_WRITES` (10,000) writes may be queued; beyond that a write is dropped and reported (`replay_write_failed`, action `backlog`), which `verify_replay` would then detect as a mismatch.

Artifact status: `in_progress` at start, `finished` at the end. At startup every artifact still `in_progress` is marked `interrupted` (its process died). `finished`/`interrupted` artifacts older than `NETHER_EARTH_REPLAY_RETENTION_DAYS` (by `meta.json` modification time) are deleted hourly. Default: 5 days in production, nothing deleted in development; `0` or `forever` disables pruning (owner decision 2026-10-03, see [resolved-questions.md](resolved-questions.md#replay-retention-default)).

Replay contract (`backend/app/replay/verify.py`):

```text
rules identity check
+ initial state from scenario, map, seed and seat controllers
+ accepted human commands per tick
=> same final snapshot and result
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

No client-authoritative movement or outcome prediction in v1. Held commander moves are scheduled so each next `commander_move` reaches the server by the tick the current move completes.

Renderer assets identify semantic types; they do not encode gameplay rules.

Spectrum presentation (FS §19):

- `render/projection.ts`: the Spectrum isometric axes (`AXIS_X` = (8, −4), `AXIS_Y` = (4, 8) px per cell, `Z_PX` = 1 px per height unit; world units are Spectrum pixels) and its exact inverse for picking. `VIEW_SPAN_PX` sets the zoom.
- One depth-sorted scene holds structure cells, scenery slices, robots, commanders and projectiles (occlusion). Units are drawn as 2×2 bodies from their snapshot anchor.
- `render/surface.ts` gives the highest surface under a footprint for shadows and robot elevation, from the same heights the engine uses; ground heights are drawn ×`GROUND_LIFT` (3).
- Sprites: `frontend/public/assets/manifest.json` maps each blocker `kind` (and `debris`) and structure wall to a sprite asset, with an optional pixel `offset`; changing the mapping needs no code change. A kind without a valid mapping draws as a placeholder prism. Robot, commander and wall sprites are decoded from the disassembly by scripts under `frontend/scripts/`.
- Ownership flags (`render/flags.ts`), the radar (`ui/radar.ts`), the full-screen construction screen (`ui/construction.ts`), the right-hand robot menu and the self-hosted Spectrum fonts are presentation only.
- Construction costs shown in the UI come from `src/generated/rules/construction.json`, a build-time export of the `EngineRules.module_cost_*` defaults (`npm run rules:generate`). CI fails on drift; costs are not in the protocol. The UI never decides spending, weapon caps or launch validity.
- Audio (`src/audio/`): the title music is lifted from the disassembly by `frontend/scripts/decode-music.py` and interpreted one 50 Hz frame at a time; the sound routines reproduce the original T-state timing; in-game sounds are derived from consecutive snapshots. The noise routines' ROM reads are replaced by a seeded PRNG. Mute is per viewer in `localStorage` (key `M`). No protocol, engine or backend involvement.
- Structure labels and robot strength numbers are an optional overlay, off by default (key `L`, saved per viewer in `localStorage`).

## 24. Deployment and supply chain

Environment-only settings (`backend/app/config.py`, names as implemented (owner decision pending, see [open-questions.md](open-questions.md#3-owner-decisions-pending))), all optional in development; `NETHER_EARTH_ENV=production` (set by the image) turns a missing required value into a startup failure:

```text
NETHER_EARTH_ENV                               production | development
NETHER_EARTH_PUBLIC_BASE_URL                   required in production; its origin is the only one allowed on /ws
NETHER_EARTH_REPLAY_DIR                        required in production
NETHER_EARTH_MAP_DIR                           map data directory (image default /opt/nether-earth/data/maps)
NETHER_EARTH_MAX_MATCHES                       default 200
NETHER_EARTH_FINISHED_MATCH_RETENTION_SECONDS  default 300
NETHER_EARTH_WAITING_MATCH_TIMEOUT_SECONDS     default 900
NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS     default 30
NETHER_EARTH_REPLAY_RETENTION_DAYS             default 5 in production, keep all in development; 0/forever = keep all
NETHER_EARTH_LOG_LEVEL                         default INFO
NETHER_EARTH_LOG_FORMAT                        json (production) | text
NETHER_EARTH_COMMIT                            baked into the image, logged at startup
```

Gameplay rules are versioned engine/scenario data, never environment variables.

Docker Compose (`deploy/docker-compose.yml`), one host:

```text
gateway          nginx-unprivileged; the only published port; TLS via docker-compose.tls.yml
frontend         static build served by nginx-unprivileged
backend          uvicorn app.main:app, uid 10001, /ws and /health, /ready
replay-dir-init  one-shot chown of the host replay directory
```

All containers run read-only with all capabilities dropped and `no-new-privileges`; logs are size-capped. The backend gets memory and CPU limits.

Gateway (`deploy/nginx/`): proxies `/ws` (WebSocket, 120 s idle timeout) and `/api/` (GET only) to the backend and everything else to the frontend; per-IP limits (30 WebSocket handshakes/min, 10 API requests/s, 32 concurrent connections); security headers including a strict Content-Security-Policy; `/healthz` for its own liveness.

Backend transport bounds (`backend/app/transport/`):

- the WebSocket handshake is refused (1008) when its `Origin` is present and not the configured public origin;
- inbound frames over 16 KiB close the socket (1009); uvicorn runs with the same `--ws-max-size`;
- 40 messages/s sustained with a burst of 80 per connection (token bucket), else close;
- a socket with no bound session after 30 s is closed (`bind_timeout`);
- a session violation (bad or foreign token, a second create/join) closes with 1008; a malformed message only gets an `error`;
- 5 failed joins close the connection;
- a newer socket for the same session replaces the old one, which is closed with 4000;
- a send that cannot complete in 5 s closes that socket, so the disconnect policy takes over instead of the tick loop blocking.

Health: `/health` is liveness. `/ready` returns 503 while shutting down or when the replay directory is not writable; match, runtime and connection counts are included outside production, and in production only for a loopback client. The image's health check calls `/ready`.

Supply chain:

- base images are pinned by SHA-256 digest; GitHub Actions are pinned by commit SHA;
- Python runtime dependencies are installed from `backend/requirements.lock` with `--require-hashes` (`make lock` regenerates it);
- Dependabot opens weekly update PRs for pip, npm, Docker, Compose and Actions;
- the "Dependency audit" workflow runs `pip-audit --strict` on the lock and `npm audit --audit-level=high` weekly and on dependency changes;
- CI (`.github/workflows/ci.yml`) runs ruff, mypy and pytest; schema validation, generated-artifact drift, typecheck, vitest and build; then the Compose/Nginx config check and a production-image deployment smoke test (`deploy/smoke.sh`).

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
- AI seat determinism and strength (`engine/tests/ai_harness.py`, `scripts/ai_strength.py`);
- replay determinism.

Backend/runtime tests:

- create/join/ready, solo matches;
- WebSocket ownership/session validation and transport limits;
- disconnect pause/reconnect snapshot/resume;
- grace-timeout forfeit;
- both-disconnected no-contest;
- multiple matches/process;
- sweep and cleanup;
- replay artifacts, retention and verification.

Protocol tests:

- schemas valid;
- generated TS current;
- representative messages validate.

An integration test runs a scripted deterministic match end-to-end and produces identical final replay hashes across repeated runs (`backend/tests/acceptance/test_m9_full_match.py`).

## 26. Open questions

The remaining open gameplay questions are listed in [open-questions.md](open-questions.md). Until one is decided, keep the value or algorithm it covers behind an isolated engine policy or rule field and do not treat a guess as canonical.

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

## 28. AI opponent

The computer-controlled seat is an engine-side deterministic planner, not a backend bot session.

- It runs inside `engine.step` (`ai.seat.issue_ai_commands`) on ticks that are multiples of `ai_decision_interval_ticks` (4), and emits ordinary `Command`s for its `PlayerId`, numbered after any command already submitted for that seat. They go through the same `validate_command_batch` and per-command engine rules as a human's: no privileged path, no direct state mutation, no new rule.
- Its carry-over state (`AiMemory`: last war base built at, defence assignments, enemy sightings) lives in `GameState` and round-trips through snapshots and replays; a replay never records the AI's commands, it re-derives them by re-simulating from scenario + map version + seed + the human's command stream.
- `ai.planner.plan(state, memory, world, rules, seed)` is a pure function. The engine derives its per-decision `seed` as `rng.derive_seed(match_seed, "ai", player_id, tick)`; `plan` derives one further seed per sub-planner (`derive_seed(seed, name)`). Iteration order is canonical, never set/dict order. The current sub-planners draw no random numbers.
- The AI seat has no entry in `state.commanders`. Every site that looks up a commander for a given seat tolerates there being none.

### 28.1 Planner structure

`ai.planner.plan` chains two sub-planners, construction first, each owning its slice of `AiMemory`:

- **`ai.construction`** — scores every legal design (`DESIGNS`, `design_value`) and picks the best the current pool can pay for (`choose_design`, using the engine's own spend rule), subject to a weapon-count floor that rises with army size (`min_weapons`), a defence reserve of general resources (`defence_reserve`), and a nuclear role (`wants_nuclear`). A threatened war base (`threatened_war_bases`) releases the floor and the reserve. It tries owned war bases threatened-first, then round robin (`war_base_order`), skipping one whose exit the launch rule refuses (`robot_launch.resolve_launch_exit`). It opens the session with `EnterConstructionRemotelyCommand` — a second, commander-less entry into the shared `construction_session` layer, accepted for AI seats only — then issues the same `SelectModuleCommand`s and `LaunchRobotCommand` a human's construction screen would, all in one tick. A session still open at the next decision is cancelled.
- **`ai.robot_orders`** — in priority order: defence (an enemy robot closing on an owned capture cell within the threat radius gets one defender, chosen by `matchup`), nuclear carriers to opponent-owned structures, capture allocation by value-per-distance score (`capture_score`, `structure_value`), and hunting with robots that win their matchup. It predicts what the engine's own `select_capture_target` / `select_destroy_target` / exclusivity rule (`orders.claimed_structures`) will choose and issues `SetRobotOrderCommand`s; it never picks a building directly. An order is re-issued only when it differs in kind (`same_order`).

Both planners read only information a player's own client renders (own resources, both sides' structure ownership and robot positions and builds); neither reads the opponent's resource pool, construction session or orders. AI strategy weights are module constants in `ai/`, outside `EngineRules` and the rules hash.

### 28.2 Match lifecycle and wire protocol

A solo match is described in §19. Wire protocol: `create.opponent: "human" | "computer"` (absent means `"human"`, so an existing PvP `create` is unchanged); `created.joinCode` is `null` for a solo match but still present; `created.opponent` is present only when it is `"computer"`. A replay artifact records which seats are AI-controlled (`meta.json` `seat_controllers`, e.g. `{"p1": "human", "p2": "ai"}`); an artifact without it is treated as all-human.
