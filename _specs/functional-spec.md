# Nether Earth Clone — Functional Specification

## 1. Purpose

Build a browser-based multiplayer clone of the ZX Spectrum release of **Nether Earth**, preserving the original mechanics, map, visual feeling, robot construction rules, commander behavior, terrain interaction, and direct-control model.

Version 1 focuses on **human vs human PvP**. The architecture must allow an AI player to be added in v1.5 without changing the game engine contract, but AI is explicitly out of scope for v1.

## 2. Product goals

- Preserve the feel and mechanics of the ZX Spectrum version rather than modernizing it into a conventional RTS.
- Support two human players over the network.
- Keep the original long rectangular battlefield and grid-based movement model.
- Keep the commander as a physical in-world entity with height and collision behavior.
- Keep robot construction modular and faithful to the original component stack.
- Keep direct robot control and autonomous robot behavior as distinct gameplay modes.
- Make the authoritative simulation deterministic and replayable.

## 3. v1 scope

### Included

- Browser game.
- Two-player PvP.
- Original ZX Spectrum-inspired map and terrain layout.
- Original-style 2.5D presentation.
- Commander movement, vertical movement, collision, blocking, docking, and undocking.
- Robot construction from chassis, weapons, nuke, and electronics.
- Terrain-dependent robot movement.
- Direct robot control using keyboard input.
- Weapon firing and projectile lifecycle rules.
- Autonomous robot navigation and engagement rules required by the original game mechanics.
- Guest-only play with nickname and join code/link.
- In-memory active matches.
- Replay/debug logging to files.

### Explicitly out of scope for v1

- Accounts.
- Persistent player profiles.
- PostgreSQL or other database.
- Redis.
- AI opponent implementation.
- Horizontal scaling across multiple backend processes or servers.
- 3D rendering or 3D models.
- Modern RTS control redesign.

## 4. Match flow

1. Player creates a match and chooses a nickname.
2. Server creates an in-memory match and returns a join code/link.
3. Second player joins with a nickname.
4. Both players become ready.
5. Server starts the authoritative simulation.
6. Players control commanders, build robots, dock onto robots, and fight.
7. Match ends according to the original game victory conditions.
8. Result is shown to both players.
9. Match replay/debug data is finalized on disk.
10. Active in-memory match state is discarded.

No account is required at any point.

## 5. World model

### 5.1 Grid

The battlefield is a long rectangular grid.

Authoritative X and Y coordinates are integers. Robots and commanders move from one square to another; there are no authoritative intermediate horizontal positions.

The client may render smooth movement between squares, but that interpolation is visual only.

### 5.2 Terrain

Terrain is a property of a grid cell and is not considered a solid occupant.

Required terrain types include:

- normal terrain
- rough terrain
- ditch

Terrain has no elevation. The playing field itself is flat.

Elevated or tall elements are physical objects such as boxes, cubes, robots, factories, and the commander.

### 5.3 Solid occupancy

A grid cell may have at most one ground-level solid occupant, such as:

- robot
- building/factory
- cube/box
- other solid map object

Two robots cannot occupy the same cell.

A robot and a building cannot occupy the same cell.

Rough terrain may overlap with a robot because terrain is a cell property, not a solid occupant.

The commander is handled separately because it can fly above occupied cells if its height allows clearance.

## 6. Commander

### 6.1 Role

The commander is a physical, indestructible entity in the game world.

The commander:

- cannot be destroyed
- cannot be targeted by weapons
- cannot take damage
- can physically block robot movement
- can fly over robots and obstacles if high enough
- can dock onto robots
- can directly control a docked robot

The commander is not merely a cursor or camera proxy.

### 6.2 Horizontal movement

The commander moves on integer grid coordinates using arrow keys or WASD.

Horizontal movement is authoritative square-to-square movement.

The browser may animate the movement smoothly between cells.

### 6.3 Vertical movement

Holding Space makes the commander rise.

Releasing Space makes the commander descend.

Vertical position is represented as discrete integer Z levels in the authoritative simulation.

A vertical step from Z to Z+1 is animated smoothly on the client, but the authoritative Z value changes only when the vertical step completes.

Horizontal and vertical movement may happen simultaneously, matching the original game behavior.

### 6.4 Height collision

The commander may share X/Y coordinates with a ground solid only when its vertical position clears the solid object's height.

When the commander attempts horizontal movement, the engine checks whether the destination cell contains a solid object that intersects the commander's current authoritative vertical range.

If it does, movement is rejected.

A low-flying commander can therefore block robot movement.

### 6.5 Blocking robots

A commander can deliberately hinder or block enemy robots by occupying a square at an intersecting height.

Because the commander is untargetable and indestructible, robots cannot solve this by attacking it.

Smarter robot navigation may route around a blocking commander where the original mechanics allow it.

### 6.6 Docking

Docking is automatic and physical, not menu-driven.

When a descending commander is directly above a robot and reaches the robot's top, it docks automatically.

When docked:

- commander X/Y is derived from the robot X/Y
- the commander moves with the robot
- independent commander horizontal movement is disabled
- direct-control keyboard input is routed to the robot
- the commander's displayed vertical position is derived from the robot's assembled height

Undocking happens by rising away from the robot.

## 7. Robots

### 7.1 Robot composition

A robot is composed from a chassis plus optional components.

#### Chassis — exactly one

- legs / bipod
- tracks
- anti-gravity

#### Weapons

The robot may contain one of each supported weapon type. Weapon components are not freely reorderable by the player; the game derives a canonical physical stack order.

Supported weapons:

- cannon
- missiles
- phaser
- nuke

The nuke is always the topmost weapon component and may be the only weapon on the robot.

#### Electronics

Electronics is optional and visually represented as a small antenna.

Electronics is always the last visible component at the top of the robot stack.

### 7.2 Canonical stack

Physical rendering and robot height are derived from a canonical component stack:

1. chassis
2. normal weapon components in the original ZX Spectrum order
3. nuke, if present
4. electronics antenna, if present
5. commander, when docked

The exact canonical order of cannon/missile/phaser must match the ZX Spectrum reference behavior.

The stack order is not stored as arbitrary player data; it is derived from selected components.

### 7.3 Robot height

Robot total height is derived from the assembled component stack.

The same stack definition must drive:

- visual rendering
- collision height
- projectile interaction
- docking height
- factory preview

## 8. Chassis and terrain behavior

Terrain interaction depends on chassis.

### Legs / bipod

- can traverse normal terrain
- cannot traverse rough terrain
- cannot traverse ditches

### Tracks

- can traverse normal terrain
- can traverse rough terrain with a speed penalty
- cannot traverse ditches

### Anti-gravity

- can traverse normal terrain
- can traverse rough terrain
- can cross ditches

Movement speed and terrain penalties are game-rule data and should be tuned to match the ZX Spectrum behavior.

## 9. Robot movement

Robots move from one grid square to another with no authoritative intermediate X/Y position.

Movement duration is represented as an integer number of simulation ticks.

Different chassis and terrain combinations may require different numbers of ticks per square.

The client visually interpolates the robot between source and target cells during that time.

A robot cannot enter a cell if:

- the terrain is not traversable for its chassis
- the cell already has a solid occupant
- a commander occupies the destination at a colliding height

## 10. Robot navigation and intelligence

There are two navigation/behavior classes based on electronics.

### 10.1 Robot without electronics

Uses deliberately limited, original-style navigation and combat behavior.

Characteristics may include:

- simple local movement decisions
- poor routing around obstacles
- possibility of becoming blocked or stuck
- simple engagement behavior

### 10.2 Robot with electronics

Electronics improves both navigation and combat decision-making.

Expected behavior includes:

- smarter route selection
- terrain-aware routing
- avoidance or rerouting around obstacles and blocking commanders
- better enemy engagement decisions

Electronics does not change the chassis' physical terrain capabilities. For example, an electronic bipod still cannot enter rough terrain or a ditch.

## 11. Direct robot control

A player can dock the commander onto a robot and control that robot directly.

The direct-control interface preserves the original menu concept and exposes actions including:

- Nuclear Bomb
- Fire Phasers
- Fire Missiles
- Fire Cannon
- Move Robot
- Stop Combat
- Strength indicator

When Move Robot/direct movement is active, arrow keys or WASD control the robot one grid square at a time.

Direct control uses the same low-level movement validation as autonomous movement.

Terrain, occupancy, commander blocking, and chassis restrictions still apply.

## 12. Weapons and projectiles

Weapons are fired one at a time.

Once a robot fires a weapon, that robot cannot fire another weapon until its active projectile has finished its lifecycle.

The active projectile is cleared when it:

- hits something
- crashes into an obstacle
- hits a robot
- otherwise exits its valid play/screen area according to the original mechanics

There are no independent per-weapon cooldowns in v1 unless required by the original Spectrum rules.

A robot with multiple weapons may only have one active projectile/fire channel at a time.

Projectiles have their own height/flight rules and use height-aware collision against physical objects.

## 13. Visual presentation

The game uses 2.5D sprite-based rendering only.

No 3D modeling or 3D physics engine is required.

The goal is to preserve the original Nether Earth feeling, map layout, silhouettes, stacking, palette/style, and interface character while allowing modern smooth animation and browser scaling.

Smooth animation is presentation only; authoritative game state remains discrete.

## 14. Map

The reference map is the ZX Spectrum map.

The map is stored as a pre-saved YAML data file in the repository.

The map file contains terrain and static object placement.

Dynamic entities such as player commanders and robots are created by match setup unless required by a specific scenario.

The map format must be versioned.

Example concept:

```yaml
format_version: 1
id: zx-spectrum-original
name: Nether Earth Original Map
width: <integer>
height: <integer>

terrain:
  default: normal
  cells: []

objects: []
```

## 15. Multiplayer behavior

The server is authoritative.

Players send commands/input; clients do not determine legal game outcomes.

The server decides:

- whether movement is legal
- when a grid transition completes
- collisions
- docking
- projectile movement
- damage
- weapon availability
- robot navigation decisions
- victory conditions

The browser only displays authoritative state and interpolates visual transitions.

## 16. AI roadmap

AI is planned for v1.5.

No AI opponent is implemented in v1.

The architecture must allow an AI controller to observe the allowed authoritative state and submit the same commands available to a human player.

AI must not bypass normal game rules or directly mutate engine state.

This enables:

- human vs human in v1
- human vs AI in v1.5
- possible AI vs AI testing later

## 17. Replay requirements

Every match should produce an append-only replay/debug log on disk.

At minimum, deterministic replay requires:

- format version
- map identifier/version
- initial state
- random seed, if randomness exists
- accepted commands with tick numbers
- match result

During development, additional engine events may also be logged for debugging.

Replays do not require PostgreSQL.

## 18. Fidelity reference

When resolving uncertain game rules, use this priority:

1. ZX Spectrum release behavior
2. ZX Spectrum disassembly/code evidence
3. original Spectrum documentation/manual
4. observed Spectrum gameplay
5. other ports or remakes only as secondary references

The goal is faithful behavior, not modernization of the mechanics.
