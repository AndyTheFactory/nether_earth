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

Recommended monorepo layout:

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
│   │   ├── map.py
│   │   ├── terrain.py
│   │   ├── commander.py
│   │   ├── robots/
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
state = engine.new_game(map_data, players, seed)
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

FastAPI forwards validated player commands to the appropriate `Match`.

### 4.4 Frontend

The frontend owns:

- rendering
- input collection
- interpolation/animation
- menus and overlays
- connection state

The frontend must not decide whether movement, firing, docking, collisions, or damage are legal.

## 5. Simulation timing

### 5.1 Tick rate

Authoritative simulation runs at **20 Hz**.

```text
1 tick = 50 ms of game time
```

Game mechanics use tick counts rather than measured wall-clock deltas.

The server uses real time only to schedule when simulation ticks are executed.

### 5.2 Determinism

The engine should aim for:

```text
same initial state
+ same map/version
+ same RNG seed
+ same accepted command stream
= same resulting state
```

Avoid using wall-clock time inside game rules.

Avoid non-deterministic iteration/order where game outcomes could change.

If randomness is required, use a match-local seeded RNG owned by the engine.

## 6. Grid and movement model

### 6.1 Authoritative coordinates

- X: integer
- Y: integer
- commander Z: integer

Robots do not have intermediate authoritative X/Y positions.

### 6.2 Horizontal transition

A move from one square to another may require multiple ticks.

Represent a pending move explicitly, for example:

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

Until completion, authoritative occupancy remains on the source cell unless the final engine design requires a reserved-target state to prevent simultaneous claims.

At completion, occupancy changes atomically.

The client interpolates visually during the transition.

### 6.3 Terrain movement cost

Movement speed should resolve to an integer tick duration per cell.

Examples are data-driven rather than hard-coded into renderer/network code.

The exact values must be matched to ZX Spectrum behavior.

## 7. World state model

A grid cell contains terrain plus optional solid occupancy.

Conceptually:

```python
Cell:
    terrain: TerrainType
    solid_entity_id: EntityId | None
```

Terrain types include:

```python
NORMAL
ROUGH
DITCH
```

Solid entities include:

- robots
- factories/buildings
- cubes/boxes
- other static blockers

Commander occupancy is tracked separately because commanders may share X/Y with solids when vertically clear.

## 8. Commander model

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

When `DOCKED`, effective position is derived from the robot rather than independently updated.

### 8.1 Vertical transition

Example:

```python
VerticalTransition(
    from_z=3,
    to_z=4,
    started_tick=100,
    duration_ticks=N,
)
```

Authoritative `z` remains at the committed value until the transition completes.

The client interpolates the visible vertical position.

Horizontal and vertical transitions may be active simultaneously.

### 8.2 Collision

Commander collision is height-aware.

A destination X/Y occupied by a solid object is allowed only if vertical ranges do not overlap.

The commander itself can block robots if its current vertical range overlaps the robot's required movement volume.

### 8.3 Docking

Docking is automatic when a descending free commander reaches the top of a robot at the same X/Y.

On docking:

```text
FREE -> DOCKED(robot_id)
```

Direct input routing then switches from commander movement to robot direct-control movement/actions.

## 9. Robot model

Recommended build representation:

```python
RobotBuild:
    chassis: ChassisType
    cannon: bool
    missiles: bool
    phaser: bool
    nuke: bool
    electronics: bool
```

Do not persist arbitrary component order.

Generate physical stack order from one canonical function/table.

### 9.1 Chassis

```python
LEGS
TRACKS
ANTIGRAV
```

### 9.2 Terrain capability

The engine should expose rules through capability functions, for example:

```python
can_enter(robot, cell) -> bool
movement_duration_ticks(robot, cell) -> int
```

Expected rules:

- legs: normal yes; rough no; ditch no
- tracks: normal yes; rough yes with penalty; ditch no
- anti-gravity: normal yes; rough yes; ditch yes

### 9.3 Component stack

One engine-level function must derive stack and total height.

Conceptually:

```python
def build_component_stack(build: RobotBuild) -> list[Component]:
    ...
```

Order:

1. chassis
2. cannon/missile/phaser according to verified ZX Spectrum canonical order
3. nuke if present
4. electronics if present

The docked commander is rendered/positioned above the resulting robot stack.

The same component metadata should drive both rendering identifiers and simulation heights.

## 10. Navigation architecture

Movement execution and route/decision logic must be separated.

Low-level movement should be shared by all control sources:

```python
try_move_robot(robot_id, direction, state) -> MoveResult
```

Control sources include:

- direct human control
- basic autonomous controller
- electronic autonomous controller
- future AI player commands at the Match layer

### 10.1 Basic navigation

Non-electronic robots use deliberately limited original-style movement logic.

### 10.2 Electronic navigation

Electronic robots use improved route planning and combat decisions while respecting exactly the same physical movement rules.

Do not make the pathfinder itself own chassis rules; query movement capabilities/costs from the robot/game rules.

## 11. Direct robot control

When the commander is docked, player directional input may control the robot.

Direct movement uses exactly the same movement validation as autonomous movement.

The protocol should support explicit robot actions such as:

```text
move/input direction
fire cannon
fire missiles
fire phaser
fire nuke
stop combat
```

Input routing belongs to Match/player-session state; legality belongs to the engine.

## 12. Weapons and projectiles

A robot has one active projectile/fire channel.

Recommended state:

```python
Robot:
    ...
    active_projectile_id: EntityId | None
```

A fire command is rejected while `active_projectile_id` is not `None`.

The reference is cleared when the projectile ends its lifecycle.

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

Exact movement and collision behavior must follow ZX Spectrum mechanics.

## 13. Match runtime architecture

### 13.1 MatchManager

One FastAPI process hosts many active matches.

```text
MatchManager
├── match A
├── match B
└── match C
```

No process/container is created per match.

### 13.2 Match object

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

### 13.3 Failure model

Active state is in memory.

If the backend process dies or restarts, active matches are lost in v1.

This is accepted for the initial low-user deployment.

Replay data should be append-only so partial logs survive process failure where possible.

## 14. Guest identity

No accounts are used.

A player has:

- nickname
- match-local player slot
- random opaque session/reconnect token

The token must not encode trust-sensitive game state.

The server remains authoritative over player slot and match membership.

## 15. WebSocket protocol

### 15.1 Transport

- WSS in production through Nginx
- plain JSON messages

### 15.2 Shared schema

JSON Schema under `/protocol/schemas` is the source of truth.

Generate TypeScript types from the shared schemas.

Backend payload models must remain compatible with the same schema definitions.

### 15.3 Client message envelope

Example:

```json
{
  "type": "move_input",
  "seq": 1842,
  "payload": {
    "direction": "left"
  }
}
```

`seq` is a client-issued monotonic sequence number used for diagnostics, ordering checks, and possible duplicate handling.

### 15.4 Server message envelope

Example:

```json
{
  "type": "state_delta",
  "tick": 912,
  "payload": {}
}
```

All authoritative state-changing messages should carry the relevant server tick.

## 16. State synchronization

The client does not reconstruct the simulation from commands.

Use:

- state delta every simulation tick
- full snapshot every 20 ticks (initially once per second)
- immediate full snapshot on join/reconnect

Example lifecycle:

```text
tick 100 -> full snapshot
tick 101 -> delta
...
tick 119 -> delta
tick 120 -> full snapshot
```

A snapshot should contain at least:

```json
{
  "type": "snapshot",
  "protocol_version": 1,
  "tick": 500,
  "map_id": "zx-spectrum-original",
  "payload": {}
}
```

A delta should identify spawned, changed/moved, and removed entities without requiring the client to execute game rules.

## 17. Frontend rendering

PixiJS renders the authoritative state using original-style 2.5D sprites.

The renderer may interpolate:

- X/Y movement between cells
- commander vertical movement between Z levels
- sprite transitions/effects

Interpolation must never feed back into game-rule decisions.

Use nearest-neighbor/pixel-appropriate rendering rules where required to preserve the original visual style.

No client-side physics engine is needed.

## 18. Map format

Store the map at:

```text
data/maps/zx-spectrum-original.yaml
```

The YAML format must begin with a version field:

```yaml
format_version: 1
id: zx-spectrum-original
name: Nether Earth Original Map
width: <integer>
height: <integer>
```

The file should define:

- terrain cells/regions
- static solid objects
- object type/variant
- object height where applicable
- factories and other game-relevant metadata
- match spawn/setup points if useful

Do not embed map layout in Python source code.

## 19. Replay/debug format

Use filesystem persistence only.

Preferred format: JSONL, one append-only record per line.

Example:

```json
{"type":"match_start","format_version":1,"match_id":"A7K4","map":"zx-spectrum-original","seed":238194}
{"type":"command","tick":12,"player":1,"command":{"type":"move_input","direction":"left"}}
{"type":"command","tick":17,"player":2,"command":{"type":"fire_weapon","weapon":"cannon"}}
{"type":"match_end","tick":8123,"winner":1}
```

Minimum deterministic replay data:

- replay format version
- engine/game version
- map identifier/version
- initial state or deterministic setup inputs
- seed
- accepted command stream and ticks
- result

Development builds may additionally write selected engine events/state hashes for debugging determinism.

Mount the replay directory as a Docker volume so logs survive container replacement.

## 20. AI extension point for v1.5

AI is not implemented in v1.

Reserve a controller boundary at the Match layer:

```text
HumanController --\
                  +--> commands --> Match --> GameEngine
AIController -----/       (v1.5)
```

An AI controller must:

- observe only state it is allowed to observe
- emit the same command types as a human player
- never directly mutate `GameState`

No AI-specific hooks should be added inside core rules unless required to expose legal observations/actions generically.

## 21. Docker deployment

Recommended v1 Compose services:

```text
nginx
frontend
backend
```

No database service.

### Nginx responsibilities

- TLS termination
- serve or proxy frontend assets
- proxy HTTP API requests to FastAPI
- proxy WebSocket upgrade requests to FastAPI

### Backend volume

Mount a persistent host volume for:

```text
/replays
```

## 22. Testing strategy

### Engine tests

Prioritize exhaustive deterministic unit tests for:

- grid occupancy
- legs/tracks/anti-gravity terrain rules
- movement duration
- collision
- commander height clearance
- commander blocking
- docking/undocking
- component stack ordering
- derived robot height
- weapon gating while projectile exists
- projectile collision/lifecycle
- basic vs electronic navigation behavior

### Determinism tests

Given a fixed initial state, seed, and command stream:

- run the match twice
- compare final state
- optionally compare per-tick state hashes

### Protocol tests

- schema validation
- generated TypeScript compatibility
- invalid command rejection
- sequence/tick handling

### Match integration tests

- two guest players join
- readiness/start lifecycle
- command routing
- WebSocket state delta delivery
- periodic snapshot delivery
- disconnect/reconnect snapshot
- replay output

## 23. v1 non-goals and deferred infrastructure

Do not add the following until a demonstrated need exists:

- PostgreSQL
- Redis
- RabbitMQ/Kafka
- Kubernetes
- per-match worker/container processes
- binary network protocol
- server-side horizontal scaling
- durable active-match recovery
- accounts/authentication system
- AI opponent

The current architecture must expose seams that allow these to be added later without coupling them into the core engine.

## 24. Reference-fidelity rule

When implementation details are uncertain, prioritize the ZX Spectrum version.

Game data and rule constants should be externalized or centralized wherever practical so they can be corrected after comparison with the reference game/disassembly without rewriting architecture.
