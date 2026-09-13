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
    starting_general_resources
    factory_initial_ownership
    victory_rule
```

Locked v1 values:

```text
player 1 starting war bases = 1
player 2 starting war bases = 1
starting general resources = 30 per player
factories = neutral unless overridden
victory = opponent owns zero war bases
```

The original single-player asymmetric campaign can later be represented as another scenario without changing engine rules.

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
2. cannon/missile/phaser in verified canonical Spectrum order
3. nuke if present, always topmost weapon
4. electronics if present, always topmost robot component

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
Nuclear:  radius 8, special lethality, cost 20
```

Miles-to-grid conversion must be one shared engine/map-scale rule.

## 19. Projectiles and normal firing

Cannon, missile, and phaser use the normal projectile/fire-channel model.

Recommended robot state:

```python
Robot:
    ...
    active_projectile_id: EntityId | None
```

A normal fire command is rejected while `active_projectile_id` is not `None`.

Projectile state should include at least:

```python
Projectile:
    id
    owner_robot_id
    player_id
    weapon_type
    x
    y
    flight_height
    direction
```

The projectile reference is cleared when the projectile:

- hits something
- crashes
- hits a robot
- exits its valid world/screen lifetime area according to original rules

Exact movement and collision behavior must follow ZX Spectrum mechanics.

## 20. Nuclear detonation

Nuclear is not modeled as a travelling projectile.

Recommended engine command:

```text
DetonateNuke(robot_id)
```

Resolution:

1. validate that robot contains a nuke
2. compute entities/structures within the 8-mile effect radius
3. destroy eligible robots
4. destroy eligible factories
5. destroy eligible war bases
6. destroy the carrying robot
7. update ownership and victory state
8. emit deterministic destruction/victory events

Factories and war bases are destroyable only by nuclear weapons.

## 21. Damage and strength

Robot state should expose strength/damage information required by the original UI.

Damage formulas should be isolated in engine combat code and use:

- weapon lethality
- hit/accuracy mechanics
- electronics range/accuracy benefit
- electronics defensive/resistance benefit

Do not encode combat formulas in frontend state or protocol handlers.

## 22. Match runtime architecture

### 22.1 MatchManager

One FastAPI process hosts many active matches.

```text
MatchManager
├── match A
├── match B
└── match C
```

No process/container is created per match.

### 22.2 Match object

Recommended responsibilities:

```python
class Match:
    engine
    players
    tick_number
    command_queues
    websocket_subscribers
    replay_writer
    task
```

Each match runs an independent 20 Hz scheduler.

### 22.3 Failure model

Active state is in memory.

If the backend process dies or restarts, active matches are lost in v1.

Replay data should be append-only so partial logs survive where possible.

## 23. Guest identity

No accounts are used.

A player has:

- nickname
- match-local player slot
- random opaque session/reconnect token

The server remains authoritative over slot and match membership.

## 24. WebSocket protocol

### 24.1 Transport

- WSS in production through Nginx
- plain JSON messages

### 24.2 Shared schema

JSON Schema under `/protocol/schemas` is the source of truth.

Generate TypeScript types from the shared schemas.

Backend payload models must remain compatible with the same definitions.

### 24.3 Client messages

Protocol must support, at minimum:

- commander directional input
- commander rise/descend state
- robot direct directional input
- enter/exit robot interaction modes
- select autonomous order
- order parameters/target category
- fire cannon
- fire missiles
- fire phaser
- detonate nuke
- construction module select/deselect
- start robot
- exit construction

All command messages carry a monotonic client sequence number.

### 24.4 Server messages

Authoritative messages carry the relevant server tick.

State deltas must be sufficient for a non-simulating client to render:

- movement/transitions
- ownership
- capture progress
- resources
- construction state where player-visible
- robot order/mode
- strength/damage
- projectiles
- destruction
- victory
- game clock

## 25. State synchronization

The client does not reconstruct the simulation from commands.

Use:

- state delta every simulation tick
- full snapshot every 20 ticks (once per second initially)
- immediate full snapshot on join/reconnect

Snapshots include at least:

```json
{
  "type": "snapshot",
  "protocol_version": 1,
  "tick": 500,
  "map_id": "zx-spectrum-original",
  "scenario_id": "pvp-v1",
  "payload": {}
}
```

Deltas identify spawned, changed/moved, ownership-changed, and removed entities without requiring client-side rule execution.

## 26. Frontend rendering

PixiJS renders authoritative state using original-style 2.5D sprites.

The renderer may interpolate:

- X/Y movement between cells
- commander vertical movement between Z levels
- sprite transitions/effects

Interpolation never feeds back into game-rule decisions.

Use pixel-appropriate/nearest-neighbor rendering where required to preserve the original visual character.

No client-side physics engine is needed.

## 27. Map and scenario format

Map geometry is stored at:

```text
data/maps/zx-spectrum-original.yaml
```

The YAML file must be versioned and should define:

- dimensions
- terrain cells/regions
- static blockers
- object heights
- factories and production types
- war bases
- heli-pad cells
- war-base exit cells
- any verified display/map metadata

Example shape:

```yaml
format_version: 1
id: zx-spectrum-original
name: Nether Earth Original Map
width: <integer>
height: <integer>

terrain: {}
objects: []
factories: []
warbases: []
```

PvP-specific starting ownership should preferably live in scenario data rather than mutating the canonical map definition.

## 28. Replay/debug format

Use filesystem persistence only.

Preferred format: JSONL, one append-only record per line.

Minimum deterministic replay data:

- replay format version
- engine/game version
- protocol version if useful
- map identifier/version
- scenario identifier/version
- initial state or deterministic setup inputs
- seed
- accepted command stream and ticks
- match result

Development builds may additionally log:

- game-clock events
- production events
- ownership changes
- capture start/reset/complete events
- state hashes

Mount replay directory as a Docker volume.

## 29. AI extension point for v1.5

AI is not implemented in v1.

Reserve a controller boundary at the Match layer:

```text
HumanController --\
                  +--> commands --> Match --> GameEngine
AIController -----/   (v1.5)
```

AI receives only allowed state and submits the same legal commands as a human player. It must not mutate game state directly.

## 30. Deployment

Locked deployment:

```text
VPS
└── Docker Compose
    ├── nginx
    ├── frontend
    └── backend
```

Nginx terminates HTTPS/WSS and proxies WebSocket traffic correctly.

No database or Redis service is required for v1.

## 31. Required engine tests

At minimum, add deterministic tests for:

- same seed + same commands => same result
- game-hour/day tick conversion
- production every 2,880 ticks
- enemy factory capture after exactly 1,440 continuous ticks
- capture reset when occupation breaks
- one-solid-per-cell occupancy
- commander height collision and robot blocking
- automatic commander docking
- bipod/tracks/anti-grav terrain capability
- build validation: 1 chassis, 1–3 weapons, no duplicate modules
- 24-robot construction cap
- blocked war-base exit prevents launch
- autonomous order transitions and invalid-order fallback
- Advance/Retreat completion returns to Stop & Defend
- normal one-active-projectile firing rule
- nuclear AoE includes carrying robot
- nuclear destruction of factories/war bases
- non-nuclear attacks cannot destroy factories/war bases
- electronics range/resistance modifier behavior
- victory when opponent war-base count reaches zero

## 32. Locked v1 architecture decisions

- Browser client.
- TypeScript + Vite + PixiJS + plain HTML/CSS.
- Python + FastAPI backend.
- Pure Python deterministic engine.
- 20 Hz authoritative simulation.
- Integer X/Y; integer commander Z.
- Plain JSON over WebSockets.
- Shared JSON Schema protocol.
- State deltas every tick; snapshot every 20 ticks.
- Many matches per backend process.
- Active state in memory.
- JSONL replay/debug files on disk.
- YAML canonical map plus scenario configuration.
- Nginx + Docker Compose on VPS.
- No accounts.
- No database.
- No Redis.
- PvP: one starting war base each.
- Victory: opponent owns zero war bases.
- Game time: 1 real minute = 10 game hours.
- AI planned for v1.5 only.
