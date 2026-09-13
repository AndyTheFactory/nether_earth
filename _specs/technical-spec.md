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
    commander_descent_step = 1

    normal_projectile_altitude = 10

    weapon_damage_multipliers = {
        "cannon": 2,
        "missile": 3,
        "phaser": 4,
    }

    reconnect_grace_seconds = 60  # runtime/server default, not engine tick state
```

Not every gameplay value should be an environment variable. Gameplay configuration is versioned game data. Environment configuration remains deployment-only.

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

Factory metadata includes production type and capture zone.

Physical component/cell heights may vary within one structure.

## 8. Occupancy and reservations

World state should distinguish:

- terrain;
- static physical occupancy/components;
- robots;
- commander collision volumes;
- robot destination reservations.

A robot move reserves its target cell as soon as the move is accepted.

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

If multiple valid robots contend for the same unreserved target during the same authoritative tick, choose the winner using the match-local deterministic RNG:

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
```

Vertical physics runs every `commander_vertical_update_ticks` simulation ticks.

Default Spectrum behavior:

```text
min altitude = 0
max altitude = 48
vertical cadence = every 4 ticks
ascent = +2
fall/gravity = -1
```

Horizontal and vertical movement may occur simultaneously.

### 9.1 Collision

Commander collision is a 3D-ish X/Y + vertical-range test.

- commanders collide with robots, structures, and each other;
- same X/Y is permitted only when vertical ranges are disjoint;
- overlapping opposing commanders block horizontal and vertical movement;
- commander is never targetable/damageable/destructible.

### 9.2 Docking

Descending onto the top of a friendly robot transitions:

```text
FREE -> DOCKED(robot_id)
```

Rising away undocks.

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
- future AI controller.

Locked terrain permissions:

```text
Bipod:     normal yes; rough yes/severe slowdown; ditch no
Tracks:    normal yes; rough yes/smaller slowdown; ditch no
Anti-grav: normal yes; rough yes; ditch yes
```

Relative ordinary-terrain speed:

```text
bipod < tracks < anti-grav
```

Exact ticks-per-cell and rough penalties remain a research/configuration item.

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

## 15. Robot orders

Recommended domain model:

```python
StopAndDefend
Advance(distance_miles)
Retreat(distance_miles)
SearchCapture(target_type)
SearchDestroy(target_type)
```

`Advance`/`Retreat`: 0–50 miles, converted using shared 2-cells-per-mile rule.

Impossible orders revert to Stop & Defend.

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
```

A robot has at most one active normal projectile channel. A new normal shot is rejected while its current projectile is active.

Default normal projectile altitude: **10** for cannon, missile, and phaser, independent of robot height.

Projectile lifecycle must be authoritative world logic. It must not depend on browser viewport dimensions.

Still research/configuration-bound:

- exact projectile speed;
- update cadence;
- collision profile;
- exact interaction with structure/components;
- exact termination rules.

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

Default radius: **8 miles = 16 cells**.

On detonation:

1. resolve eligible entities inside authoritative radius;
2. destroy eligible robots;
3. destroy eligible factories;
4. destroy eligible war bases;
5. destroy carrier robot;
6. update ownership/victory;
7. emit deterministic events.

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
- commander states;
- robots + transitions/reservations;
- ownership/capture progress;
- projectiles;
- map/scenario/rules versions;
- match result.

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

Only three substantive gameplay research areas remain unresolved:

1. exact Spectrum chassis movement timing and rough-terrain penalties;
2. exact normal-projectile speed/cadence/collision/lifetime rules;
3. exact combat accuracy, integer rounding, strength handling, and electronics modifiers.

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
- AI player implementation;
- client-authoritative movement;
- generic ECS migration without a concrete need.