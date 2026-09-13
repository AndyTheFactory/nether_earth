# Nether Earth Clone — Technical Specification

## 1. Architecture summary

Version 1 is a browser-based, server-authoritative multiplayer game running on a single VPS with Docker Compose.

The architecture is intentionally minimal:

```text
Browser
  |
  | HTTPS / WSS
  v
Nginx
  |---------------------> Frontend static app
  |
  +---------------------> FastAPI backend
                              |
                              +-- MatchManager
                              |     +-- Match #1
                              |     +-- Match #2
                              |     +-- ...
                              |
                              +-- Pure Python game engine
                              +-- Replay/debug writer
```

No database, Redis, message broker, Kubernetes, or per-match container is required for v1.

## 2. Locked technology stack

### Frontend

- TypeScript
- Vite
- PixiJS
- plain HTML/CSS
- WebSocket client using plain JSON

No React or other frontend framework in v1.

### Backend

- Python
- FastAPI
- asyncio
- WebSockets
- one process hosting many matches concurrently

### Game engine

- separate pure-Python package
- no FastAPI dependency
- no networking dependency
- deterministic fixed-tick simulation
- integer grid coordinates for authoritative X/Y
- discrete integer Z for commander altitude

### Deployment

- VPS
- Docker Compose
- Nginx reverse proxy
- mounted filesystem volume for replay/debug files

### Persistence

- active matches in memory
- completed/in-progress replay logs on filesystem
- no PostgreSQL in v1

### Map data

- YAML
- pre-saved ZX Spectrum map
- versioned format

### Protocol

- plain JSON over WebSockets
- shared JSON Schema source of truth
- generated TypeScript types
- backend validation using matching Python/Pydantic models

## 3. Repository layout

```text
nether_earth/
├── frontend/
│   ├── src/
│   │   ├── game/
│   │   │   ├── renderer/
│   │   │   ├── input/
│   │   │   ├── interpolation/
│   │   │   └── network/
│   │   ├── ui/
│   │   └── main.ts
│   ├── public/
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── websocket/
│   │   ├── matches/
│   │   │   ├── manager.py
│   │   │   ├── match.py
│   │   │   └── player_session.py
│   │   └── main.py
│   └── pyproject.toml
│
├── engine/
│   ├── nether_earth/
│   │   ├── state.py
│   │   ├── commands.py
│   │   ├── simulation.py
│   │   ├── clock.py
│   │   ├── economy.py
│   │   ├── ownership.py
│   │   ├── map.py
│   │   ├── terrain.py
│   │   ├── commander.py
│   │   ├── robots/
│   │   ├── orders/
│   │   ├── weapons/
│   │   ├── projectiles/
│   │   ├── collision.py
│   │   └── replay.py
│   ├── tests/
│   └── pyproject.toml
│
├── protocol/
│   ├── schemas/
│   │   ├── common.schema.json
│   │   ├── client_messages.schema.json
│   │   ├── server_messages.schema.json
│   │   └── snapshot.schema.json
│   ├── generated/
│   │   └── types.ts
│   └── README.md
│
├── data/
│   └── maps/
│       └── zx-spectrum-original.yaml
│
├── replays/
│
├── deploy/
│   ├── nginx.conf
│   └── docker-compose.yml
│
└── _specs/
```

## 4. Core separation of responsibilities

### 4.1 Game engine

The engine is the sole owner of game rules.

It must not import or depend on:

- FastAPI
- WebSockets
- asyncio networking code
- PixiJS/frontend concerns
- Docker/deployment code

Conceptually:

```python
state = engine.new_game(map_data, scenario, players, seed)
state, events = engine.step(state, commands)
```

All rule validation happens here.

### 4.2 Match layer

`Match` owns runtime orchestration around the engine:

- fixed-tick scheduling
- command queues
- player sessions
- joining/readiness
- state broadcasting
- replay logging
- match lifecycle

The match layer does not implement game rules.

### 4.3 FastAPI layer

FastAPI owns transport and lightweight session APIs only:

- create match
- join match
- ready state
- guest nickname/session token
- WebSocket endpoint
- health endpoint

FastAPI forwards validated commands to the appropriate `Match`.

### 4.4 Frontend

The frontend owns:

- rendering
- input collection
- interpolation/animation
- menus and overlays
- connection state

The frontend must not decide movement legality, production, capture progress, firing legality, docking, damage, ownership, or victory.

## 5. Simulation timing and game clock

### 5.1 Tick rate

Authoritative simulation runs at **20 Hz**.

```text
1 tick = 50 ms real time
```

### 5.2 In-game time scale

Locked conversion:

```text
1 real minute = 10 in-game hours
1 in-game hour = 6 real seconds = 120 ticks
12 in-game hours = 72 real seconds = 1,440 ticks
1 in-game day = 144 real seconds = 2,880 ticks
```

The game clock is derived from the authoritative tick number.

No gameplay system should schedule independent wall-clock timers for production, capture, orders, or combat.

Recommended helpers:

```python
TICKS_PER_GAME_HOUR = 120
TICKS_PER_GAME_DAY = 2880
FACTORY_CAPTURE_TICKS = 1440

def game_hours_elapsed(tick: int) -> float:
    return tick / TICKS_PER_GAME_HOUR
```

Core rule checks should use integer tick thresholds rather than floating-point hours.

### 5.3 Determinism

The engine should satisfy:

```text
same initial state
+ same scenario/map/version
+ same RNG seed
+ same accepted command stream
= same resulting state
```

Avoid wall-clock time and non-deterministic iteration where outcomes could change.

If randomness is required, use a match-local seeded RNG owned by the engine.

## 6. Scenario and victory model

PvP v1 uses a scenario definition separate from raw map geometry.

Recommended scenario fields:

```python
Scenario:
    id
    player_starting_warbases
    neutral_warbases
    starting_general_resources
    factory_initial_ownership
    victory_rule
```

Locked v1 values:

```text
player 1 starting war base = extreme-left war base
player 2 starting war base = extreme-right war base
two interior war bases = neutral and capturable
starting general resources = 30 per player
factories = neutral unless overridden
victory = opponent owns zero war bases
```

Starting ownership is scenario data layered over the immutable original-map geometry. The original single-player asymmetric campaign can later be represented as another scenario without changing engine rules.

Victory should be checked after any event capable of changing war-base ownership or destruction.

## 7. Grid and movement model

### 7.1 Authoritative coordinates

- X: integer
- Y: integer
- commander Z: integer

Robots do not have intermediate authoritative X/Y positions.

### 7.2 Horizontal transition

A move may require multiple ticks.

```python
GridTransition(
    from_x=10,
    from_y=5,
    to_x=11,
    to_y=5,
    started_tick=100,
    duration_ticks=4,
)
```

Until completion, authoritative occupancy remains on the source cell unless target reservation is required to resolve simultaneous claims deterministically.

At completion, occupancy changes atomically.

The frontend interpolates visually.

### 7.3 Terrain movement

Terrain is data-driven.

Required terrain types:

```python
NORMAL
ROUGH
DITCH
```

Locked capability rules:

```text
Bipod:     normal yes; rough yes with severe penalty; ditch no
Tracks:    normal yes; rough yes with smaller penalty; ditch no
Anti-grav: normal yes; rough yes; ditch yes
```

Exact speed/tick values must be derived from verified ZX Spectrum behavior.

## 8. World state and occupancy

Conceptual cell model:

```python
Cell:
    terrain: TerrainType
    solid_entity_id: EntityId | None
```

Solid entities include:

- robots
- factories/buildings
- war bases
- cubes/boxes
- other blockers

Commander occupancy is tracked separately because commanders may share X/Y with ground solids when vertically clear.

Only one ground-level solid entity may occupy a cell.

## 9. Commander model

Recommended authoritative model:

```python
Commander:
    player_id
    mode: FREE | DOCKED
    x: int | None
    y: int | None
    z: int | None
    docked_robot_id: EntityId | None
    horizontal_transition: GridTransition | None
    vertical_transition: VerticalTransition | None
```

When docked, effective X/Y and vertical placement are derived from the robot.

### 9.1 Vertical transition

```python
VerticalTransition(
    from_z=3,
    to_z=4,
    started_tick=100,
    duration_ticks=N,
)
```

Authoritative Z remains at the committed value until completion.

Horizontal and vertical transitions may be active simultaneously.

### 9.2 Collision

Commander collision is height-aware.

A destination occupied by a solid is allowed only if vertical ranges do not overlap.

The commander can block robots if its current vertical range overlaps the robot's movement volume.

The commander is physically collidable but:

```text
can_be_targeted = false
can_take_damage = false
can_be_destroyed = false
```

### 9.3 Docking

Docking is automatic when a descending free commander reaches the top of a friendly robot at the same X/Y.

On docking:

```text
FREE -> DOCKED(robot_id)
```

The commander then follows the robot and gains access to robot control/order/combat menus.

### 9.4 War-base heli-pad interaction

Landing on the player's war-base heli-pad opens construction unless construction is blocked by game rules.

## 10. Factories and war bases

Recommended structure model:

```python
Structure:
    id
    kind: FACTORY | WARBASE
    owner: PlayerId | None
    x
    y
    height
    destroyed: bool
```

Factory-specific fields:

```python
Factory:
    production_type: FactoryType
    capture_state: CaptureState | None
```

Factory types:

```python
CHASSIS
ELECTRONICS
NUCLEAR
MISSILE
PHASER
CANNON
```

War-base-specific fields should include heli-pad/exit metadata and ownership.

## 11. Factory capture

Enemy factory capture requires continuous occupation for **1,440 ticks**.

Recommended state:

```python
CaptureProgress:
    capturing_player_id
    robot_id
    started_tick
    required_ticks = 1440
```

On each tick, the engine verifies that the same qualifying robot still occupies the capture location.

If continuous occupation is broken, reset progress unless later verified ZX Spectrum behavior dictates otherwise.

Neutral-factory acquisition should use the verified original behavior; the first qualifying robot to reach the site activates ownership/production for its player.

Ownership change should emit an engine event and update snapshot/delta state.

## 12. Economy model

Recommended player resource state:

```python
ResourcePool:
    general: int
    chassis: int
    electronics: int
    cannon: int
    missile: int
    phaser: int
    nuclear: int
```

Starting value:

```text
general = 30
```

Every 2,880 ticks:

- each owned factory adds 2 units to its production category
- each owned war base adds 5 general units

Economy processing belongs in the engine.

Exact spending precedence between type-specific resources and general resources should be represented by one engine rule once verified, not spread across UI/backend code.

## 13. Robot build model

Recommended representation:

```python
RobotBuild:
    chassis: ChassisType
    cannon: bool
    missiles: bool
    phaser: bool
    nuke: bool
    electronics: bool
```

Validation rules:

```text
exactly 1 chassis
1 <= weapon_count <= 3
at most 1 of each weapon
at most 1 electronics module
```

Do not persist arbitrary component order.

### 13.1 Canonical stack

One engine-level function derives stack and height:

```python
def build_component_stack(build: RobotBuild) -> list[Component]:
    ...
```

Order:

1. chassis
2. cannon, if present
3. missile, if present
4. phaser, if present
5. nuke, if present; always topmost weapon
6. electronics, if present; always topmost robot component

A docked commander is positioned above the resulting stack.

The same component metadata drives renderer identifiers and simulation heights.

## 14. Robot construction service inside the engine

Construction may begin only when the commander is correctly landed on the player's war-base heli-pad.

A build cannot be started/launched when:

- player robot count is already 24
- war-base exit is blocked
- build violates module constraints
- required resources are unavailable

Recommended engine commands:

```text
construction_select_module
construction_deselect_module
construction_start_robot
construction_exit
```

Construction legality and resource deduction belong in the engine; the frontend only presents the menu.

## 15. Robot orders and control architecture

Low-level movement execution must be shared by all control sources:

```python
try_move_robot(robot_id, direction, state) -> MoveResult
```

Control sources include:

- direct human control
- autonomous robot orders
- future AI player controller at Match layer

### 15.1 Robot order types

Recommended engine model:

```python
RobotOrder = (
    StopAndDefend
    | Advance
    | Retreat
    | SearchCapture
    | SearchDestroy
)
```

Fields:

```python
Advance(distance_miles: int)
Retreat(distance_miles: int)
SearchCapture(target_type: CaptureTargetType)
SearchDestroy(target_type: DestroyTargetType)
```

Distance input for Advance/Retreat is constrained to 0–50 miles.

### 15.2 Order semantics

`STOP_AND_DEFEND`
- hold position
- engage enemy robots in range

`ADVANCE`
- move East requested distance
- then switch to Stop & Defend

`RETREAT`
- move West requested distance
- then switch to Stop & Defend

`SEARCH_CAPTURE`
- valid targets: neutral factories, enemy factories, war bases
- choose nearest valid target under original rules
- move and attempt capture

`SEARCH_DESTROY`
- valid targets: robots, factories, war bases
- choose nearest valid target under original rules
- factories/war bases require nuclear capability for destruction

If an order is impossible, the robot reverts to Stop & Defend.

## 16. Electronics and autonomous intelligence

Navigation and physical movement capability are separate.

### 16.1 Non-electronic robot

Uses deliberately limited original-style navigation/combat decision logic.

### 16.2 Electronic robot

Electronics may improve:

- routing/path selection
- obstacle avoidance
- engagement decisions
- weapon accuracy/effective range by nominal +3 miles
- resistance to incoming damage

Electronics does not alter terrain traversal permissions.

The exact ZX Spectrum algorithms should be reproduced where practical rather than replaced by generic modern pathfinding if that changes gameplay character.

## 17. Direct control and combat-control state

Recommended player/robot interaction state:

```python
RobotInteractionMode:
    COMMAND_MENU
    DIRECT_CONTROL
    ORDERS_MENU
    COMBAT_CONTROL
```

In Direct Control:

- directional input moves the robot
- Fire/confirm stops direct movement and returns to menu selection

In Combat Control:

- the player chooses a fitted weapon
- fire command is submitted explicitly
- Move Robot enters the same movement behavior as Direct Control

## 18. Weapon data

Weapon rules should be data-driven:

```python
WeaponSpec:
    type
    range_miles
    lethality
    resource_cost
    behavior
```

Locked source values:

```text
Cannon:   range 10, lethality 2, cost 8
Missiles: range 14, lethality 3, cost 4
Phasers:  range 10, lethality 4, cost 4
Nuclear:  radius 8, special, cost 20
```

Miles-to-grid conversion is an engine rule/data constant and must not live in frontend code.

## 19. Projectile/fire-channel model

Normal projectiles:

```python
Projectile:
    owner_robot_id
    weapon_type
    x
    y
    z
    direction
    remaining_range
```

A robot has at most one active normal projectile channel.

A fire request is rejected while the robot's normal projectile is active.

The projectile ends when its lifecycle termination condition occurs.

Projectile lifetime must not depend on the browser's viewport dimensions.

Nuclear detonation is modeled separately from travelling projectiles.

## 20. Damage and robot strength

Damage belongs entirely in the engine.

Robot state should expose strength sufficient for combat UI and replay snapshots.

Exact accuracy, resistance, and damage formulas must be centralized once verified from ZX Spectrum behavior.

Do not duplicate combat math in the frontend or backend transport layer.

## 21. Nuclear detonation

Nuclear detonation is a discrete engine event.

When triggered:

1. determine eligible entities inside the authoritative radius
2. destroy eligible robots
3. destroy eligible factories
4. destroy eligible war bases
5. destroy the carrier robot
6. update ownership/victory state
7. emit deterministic events

Only the engine decides affected entities.

## 22. Victory evaluation

Victory is a rule over current authoritative ownership state.

After every event that can alter war-base ownership or existence:

```python
if owned_warbase_count(player_id) == 0:
    match_result = LOSS
```

The match layer observes the resulting engine event/state and handles transport/finalization.

## 23. Match runtime

Recommended runtime shape:

```python
class Match:
    engine_state
    command_queue
    players
    tick_task
    replay_writer
```

The match loop:

1. wakes according to the 20 Hz scheduler
2. drains commands eligible for the next tick
3. orders commands deterministically
4. calls the engine step
5. records accepted commands/events
6. broadcasts authoritative state/deltas
7. terminates when the engine enters a completed state

The scheduler may compensate for drift, but simulation time always advances by integer ticks, never variable delta time.

## 24. WebSocket protocol

### 24.1 Client commands

Representative commands:

```text
ready
commander_input
construction_action
robot_control_action
robot_order
robot_fire
```

Every command should contain:

```text
match_id
player_id/session association
client_sequence
command payload
```

The server validates identity/session and then submits a domain command to the engine.

### 24.2 Server messages

Representative messages:

```text
match_joined
match_started
snapshot
state_delta
event
command_rejected
match_ended
```

### 24.3 JSON Schema

JSON Schema files are the protocol source of truth.

Workflow:

1. edit schema
2. validate schemas
3. generate TypeScript definitions
4. backend Pydantic models match the same schema
5. run protocol compatibility tests

Do not independently hand-maintain conflicting client/server protocol types.

## 25. Snapshots and replay

Snapshots must contain enough state for reconnect and deterministic debugging.

At minimum:

- tick
- game clock
- players/resources
- commander state
- robot states
- ownership
- factory capture progress
- projectiles
- terrain/map reference
- match result if completed

Replay/debug logs should record:

- map/scenario version
- RNG seed
- accepted commands in authoritative order
- important engine events
- final result

For deterministic replay:

```text
initial state + accepted commands + seed => same result
```

## 26. Reconnection

A reconnecting player receives a current authoritative snapshot.

The engine itself does not care whether commands come from an uninterrupted or reconnected network session.

Match-layer reconnection policy — grace period, abandonment, pause/continue behavior — remains a separate explicit product rule.

## 27. Frontend state strategy

The frontend keeps:

```text
latest_authoritative_snapshot
previous_authoritative_snapshot
visual_interpolation_state
local_menu_state
connection_state
```

The frontend never commits predicted gameplay outcomes as authoritative state.

For v1, commander and robot movement may use interpolation without client-side prediction.

## 28. Frontend rendering

PixiJS renders the original-style 2.5D battlefield.

Suggested scene layers:

```text
terrain
static structures
robots
commander
projectiles/effects
selection/highlights
HUD
menus
```

Simulation Z/height affects visual placement/occlusion but remains an engine concept.

Renderer assets should identify semantic component types rather than encode gameplay rules.

## 29. Configuration

Configuration categories:

### Environment configuration

Examples:

```text
HOST
PORT
REPLAY_DIR
PUBLIC_BASE_URL
```

### Gameplay constants

Engine-owned constants/configurable values include:

```text
SIMULATION_HZ = 20
TICKS_PER_GAME_HOUR = 120
FACTORY_CAPTURE_TICKS = 1440
MAX_ROBOTS_PER_PLAYER = 24
```

Do not expose every game constant as an environment variable. Gameplay configuration should be versioned with the engine/scenario so replay determinism is preserved.

## 30. Deployment

Single-host Docker Compose topology:

```text
nginx
frontend
backend
```

Nginx:

- terminates HTTPS
- serves/routes frontend
- proxies `/api/*` to backend
- proxies `/ws/*` with WebSocket upgrade

No sticky-session or shared-state infrastructure is required because v1 uses one backend process.

## 31. Testing strategy

### Engine tests

Highest priority.

Test:

- deterministic clock/ticks
- movement legality
- terrain traversal
- collision/height
- docking/undocking
- construction validation
- canonical stack/height
- production
- capture timing
- autonomous orders
- direct-control movement
- projectile firing gate
- projectile lifecycle
- damage
- nuclear destruction
- victory

### Backend tests

Test:

- create/join/ready flow
- invalid nickname/session handling
- WebSocket connect/disconnect/reconnect
- command ownership validation
- multiple matches in one process
- match cleanup

### Protocol tests

Test:

- JSON Schema validity
- generated TS types are current
- representative client messages validate
- representative server messages validate

### Frontend tests

Focus on:

- protocol decoding
- interpolation math
- input-to-command mapping
- critical menu state transitions

### Integration tests

At least one deterministic scripted match should:

1. create two players
2. initialize map
3. capture factory
4. produce resources
5. build robots
6. issue autonomous orders
7. dock/direct-control
8. exchange fire
9. capture/destroy final war base
10. produce identical final replay hash across repeated runs

## 32. Implementation priorities

Recommended order:

1. pure engine state + clock
2. map + terrain + occupancy
3. commander movement/collision/docking
4. robot construction + economy
5. robot movement + orders
6. combat/projectiles/nuke
7. match runtime + WebSocket protocol
8. frontend renderer/input
9. integration + deployment

The engine should be testable headlessly before substantial frontend work begins.

## 33. v1 non-goals

Do not add unless the scope is explicitly changed:

- PostgreSQL
- Redis
- Celery/RQ
- Kubernetes
- multiple backend replicas
- account system
- persistent matchmaking
- AI player implementation
- client-authoritative movement
- generic ECS migration unless concrete complexity requires it
