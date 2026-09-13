# Nether Earth Clone — Functional Specification

## 1. Purpose

Build a browser-based multiplayer clone of the ZX Spectrum release of **Nether Earth**, preserving the original mechanics, map, visual feeling, robot construction rules, commander behavior, terrain interaction, factory economy, autonomous robot orders, direct-control model, and combat rules.

Version 1 focuses on **human vs human PvP**. The architecture must allow an AI player to be added in v1.5 without changing the game-engine contract, but AI is explicitly out of scope for v1.

## 2. Fidelity principles

When a rule is uncertain, use this priority:

1. observed ZX Spectrum release behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

The goal is faithful behavior, not modernization into a conventional RTS.

## 3. v1 scope

### Included

- Browser game.
- Two-player PvP.
- One starting war base per player.
- Original long rectangular ZX Spectrum battlefield and map layout.
- Original-style 2.5D sprite presentation.
- Commander movement, vertical movement, collision, blocking, docking, and undocking.
- Robot construction from chassis, 1–3 weapons, and optional electronics.
- Factory ownership and production.
- Resource economy and game clock.
- Terrain-dependent robot movement.
- Direct robot control using keyboard input.
- Original robot autonomous orders.
- Weapon firing, projectile lifecycle, and nuclear detonation rules.
- Guest-only play with nickname and join code/link.
- In-memory active matches.
- Replay/debug logging to files.

### Explicitly out of scope for v1

- Accounts.
- Persistent player profiles.
- Database.
- Redis.
- AI opponent implementation.
- Horizontal scaling across multiple backend processes or servers.
- 3D rendering or 3D models.
- Modern RTS control redesign.

## 4. PvP match setup and victory

### 4.1 Starting state

- The original four-war-base map is retained.
- Player 1 starts with the **extreme-left war base**.
- Player 2 starts with the **extreme-right war base**.
- The two war bases between them start **neutral and capturable**.
- Factories are initially neutral unless the map definition explicitly specifies otherwise.
- Each player starts with the same initial resource rules.

Starting ownership is scenario data layered over the original map geometry. The original single-player campaign's asymmetric Kerberus-vs-three-bases setup remains reference material but is not the v1 PvP starting condition.

### 4.2 Victory condition

A player wins when the opponent owns **zero war bases**.

This rule applies regardless of whether the opponent's final war base was destroyed or captured.

The victory rule therefore remains valid if future scenarios start players with more than one war base.

## 5. Match flow

1. Player creates a match and chooses a nickname.
2. Server creates an in-memory match and returns a join code/link.
3. Second player joins with a nickname.
4. Both players become ready.
5. Server initializes the map, starting war bases, resources, commanders, factories, and game clock.
6. Players capture factories, accumulate resources, build robots, issue orders, dock onto robots, and fight.
7. The match ends when one player owns zero war bases.
8. Result is shown to both players.
9. Match replay/debug data is finalized on disk.
10. Active in-memory match state is discarded.

No account is required at any point.

## 6. Game clock

The game has a deterministic in-game clock derived exclusively from simulation ticks.

Locked time scale:

- 1 real minute = 10 in-game hours
- 1 real second = 1/6 in-game hour
- 6 real seconds = 1 in-game hour
- 1 in-game day = 24 hours = 144 real seconds = 2 minutes 24 seconds

At the locked 20 Hz simulation rate:

- 1 in-game hour = 120 simulation ticks
- 10 in-game hours = 1,200 ticks
- 12 in-game hours = 1,440 ticks
- 1 in-game day = 2,880 ticks

Game rules must derive time from the authoritative match tick counter rather than wall-clock timers.

## 7. World model

### 7.1 Grid

The battlefield is a very long rectangular grid.

Authoritative X and Y coordinates are integers. Robots and commanders move from one square to another; there are no authoritative intermediate horizontal positions.

The client may render smooth movement between squares, but interpolation is visual only.

### 7.2 Terrain

Terrain is a property of a grid cell and is not a solid occupant.

Required terrain types include:

- normal terrain
- rough terrain
- ditch/ravine

Terrain has no elevation. The playing field itself is flat.

Tall/elevated elements are physical objects such as boxes, cubes, robots, factories, war bases, and the commander.

### 7.3 Solid occupancy

A grid cell may have at most one ground-level solid occupant, such as:

- robot
- factory/building
- war base
- cube/box
- other solid map object

Two robots cannot occupy the same cell.

A robot and a building cannot occupy the same cell.

Rough terrain may overlap with a robot because terrain is a cell property, not a solid occupant.

The commander is handled separately because it can fly above occupied cells if its current height permits clearance.

## 8. Commander / anti-grav command vehicle

### 8.1 Role

Each player controls one indestructible anti-grav command vehicle ("commander").

The commander:

- cannot be destroyed
- cannot be targeted by weapons
- cannot take damage
- can physically block robot movement
- can fly over robots and obstacles if high enough
- can perform reconnaissance by flying over the map
- can dock automatically onto friendly robots
- is used to pass orders and directly control docked robots
- is used to enter robot construction by landing on the player's war-base heli-pad

The commander is a physical in-world entity, not merely a cursor or camera proxy.

### 8.2 Horizontal movement

The commander moves on integer grid coordinates using arrow keys or WASD.

Horizontal movement is authoritative square-to-square movement.

The browser may animate movement smoothly between cells.

### 8.3 Vertical movement

Holding Space makes the commander rise.

Releasing Space makes the commander descend.

Vertical position is represented as discrete integer Z levels in the authoritative simulation.

A vertical step from Z to Z+1 is animated smoothly on the client, but authoritative Z changes only when the step completes.

Horizontal and vertical movement may happen simultaneously.

### 8.4 Height collision

The commander may share X/Y coordinates with a ground solid only when its vertical range clears that object's height.

When the commander attempts horizontal movement, the engine checks whether a destination solid intersects the commander's current authoritative vertical range. If so, movement is rejected.

A low-flying commander can therefore block robot movement.

### 8.5 Blocking robots

A commander can deliberately hinder or block enemy robots by occupying a square at an intersecting height.

Because the commander is untargetable and indestructible, robots cannot remove it by attacking.

Autonomous navigation may route around a blocking commander where its intelligence and movement capabilities allow it.

### 8.6 Docking and undocking

Docking is automatic and physical, not menu-driven.

When a descending commander is directly above a friendly robot and reaches the robot's top, it docks automatically.

When docked:

- commander X/Y is derived from the robot X/Y
- the commander moves with the robot
- independent commander movement is disabled
- robot control/order/combat menus become available
- direct-control keyboard input can be routed to the robot
- displayed commander height is derived from the robot's assembled height

Undocking happens by rising away from the robot.

## 9. Factories, war bases, and ownership

### 9.1 Factory types

There are six production categories:

- chassis modules
- electronic support modules
- nuclear weapons
- missile weapons
- phaser weapons
- cannon weapons

The map stores each factory's production type.

### 9.2 Neutral factories

Factories begin neutral in the standard PvP scenario unless explicitly overridden by scenario data.

The first robot to reach/capture a neutral factory activates it for that player's production economy.

### 9.3 Capturing enemy factories

To capture an enemy-controlled factory, a robot must occupy the factory capture location continuously for **12 in-game hours**.

At 20 Hz this is:

- 1,440 simulation ticks
- 72 real seconds

If continuous occupation is broken before completion, capture progress resets unless later ZX Spectrum evidence establishes another behavior.

Ownership must be visibly distinguishable in the renderer.

### 9.4 War bases

War bases are strategic structures and may be player-owned or neutral depending on the scenario.

In the default v1 PvP scenario:

- the extreme-left war base belongs to Player 1;
- the extreme-right war base belongs to Player 2;
- the two interior war bases are neutral and capturable.

War bases:

- contribute general resource units when owned
- provide the heli-pad used to enter robot construction
- have an exit that can be blocked by another object
- may be captured or destroyed according to game rules
- determine victory through current ownership count

A player loses immediately when their owned war-base count becomes zero.

## 10. Resource economy

### 10.1 Initial resources

Each player begins with **30 general resource units** unless a scenario overrides this value.

### 10.2 Production

Every in-game day:

- each owned factory contributes 2 resource units related to its factory type
- each owned war base contributes 5 general resource units

At 20 Hz, one production day occurs every 2,880 ticks.

### 10.3 Resource categories

The engine must distinguish:

- general resources
- chassis resources
- electronics resources
- cannon resources
- missile resources
- phaser resources
- nuclear resources

The exact consumption precedence between type-specific resources and general resources must match verified ZX Spectrum behavior. Until implementation evidence is confirmed, this should remain data/rule driven rather than hard-coded into UI logic.

## 11. Robot construction

### 11.1 Access

Robot construction is available when the commander lands on the player's war-base heli-pad.

Construction is not available when:

- the player already has 24 robots in the sector, or
- the war-base exit is blocked, typically by another robot or solid object

### 11.2 Construction constraints

Every robot must contain:

- exactly one chassis module
- between one and three weapon modules total
- zero or one electronic support module

No robot can contain two of the same module.

A construction may be scrapped before launch, returning to the battle sector according to original behavior.

Starting/launching a robot sends the completed robot into the sector to await orders.

### 11.3 Chassis — exactly one

- bipod / legs
- tracks
- anti-gravity

### 11.4 Weapons — one to three total

Available weapon modules:

- cannon
- missiles
- phaser
- nuke

Each weapon type may appear at most once.

The nuke may be the robot's only weapon.

### 11.5 Electronics

Electronics is optional and may appear at most once.

It is visually represented by a small antenna and is always the final visible component at the top of the robot stack.

## 12. Canonical robot stack and height

Weapon/components are not freely reorderable by the player.

Physical rendering and robot height are derived from one canonical stack function:

1. chassis
2. cannon, if fitted
3. missile, if fitted
4. phaser, if fitted
5. nuke, if fitted; always topmost weapon
6. electronics antenna, if fitted; always topmost visible robot component
7. commander, when docked

If an intermediate weapon is absent, the remaining fitted weapons retain this relative bottom-to-top order.

The same stack definition must drive:

- visual rendering
- total robot height
- collision height
- projectile interaction
- commander docking height
- construction preview

## 13. Chassis and terrain behavior

### Bipod

- normal terrain: traversable
- rough terrain: traversable, but poorly/slowly ("at a pinch")
- ditch/ravine: not traversable
- intended primarily for flat ground

### Tracks

- normal terrain: traversable
- rough terrain: traversable and handled better than bipods
- ditch/ravine: not traversable

### Anti-gravity

- normal terrain: traversable
- rough terrain: traversable
- ditch/ravine: traversable
- only chassis able to span ravines/ditches

Exact movement speeds and penalties are game-rule data and must be tuned from ZX Spectrum behavior.

## 14. Robot movement

Robots move from one grid square to another with no authoritative intermediate X/Y position.

Movement duration is represented as an integer number of simulation ticks.

A robot cannot enter a cell if:

- terrain is not traversable for its chassis
- the cell already has a solid occupant
- a commander occupies the destination at a colliding height

The client interpolates visually between source and target cells during the move duration.

## 15. Robot control states

Robot interaction distinguishes three user-facing modes:

### 15.1 Robot command menu

Available after the commander docks onto a friendly robot.

The player can choose between direct control, autonomous orders, and combat control.

### 15.2 Direct Control

Direction keys / WASD move the robot directly according to chassis, terrain, occupancy, and collision rules.

The Fire/confirm control stops direct movement and returns to menu selection.

### 15.3 Combat Control

Combat Control allows the player to select any fitted weapon and fire it independently.

The Move Robot option uses the same movement behavior as Direct Control.

The combat UI includes the robot's strength indicator.

## 16. Autonomous robot orders

Autonomous orders are first-class engine state, not UI-only behavior.

Supported orders:

### 16.1 Stop & Defend

- robot remains at its current position
- robot fires on enemy robots within range according to its combat intelligence and weapon rules

### 16.2 Advance N miles

- distance selectable from 0 to 50 miles
- robot moves East by the requested distance
- after completing the movement, robot reverts to Stop & Defend

### 16.3 Retreat N miles

- distance selectable from 0 to 50 miles
- robot moves West by the requested distance
- after completing the movement, robot reverts to Stop & Defend

### 16.4 Search & Capture

Target category is one of:

- neutral factories
- enemy factories
- war bases

The robot moves toward the nearest valid target and attempts to capture it.

### 16.5 Search & Destroy

Target category is one of:

- robots
- factories
- war bases

The robot moves toward the nearest valid target and attempts to destroy it.

War bases can only be destroyed with nuclear weapons.

### 16.6 Invalid orders

If an order cannot be carried out — for example no valid target exists or the robot lacks the equipment required — the robot reverts to Stop & Defend.

## 17. Robot navigation and electronics

Movement execution and autonomous decision-making are separate concerns.

### 17.1 Robot without electronics

Uses deliberately limited original-style routing and combat behavior.

Expected characteristics include:

- simpler local movement decisions
- poorer routing around obstacles
- possibility of becoming blocked or stuck
- less effective target engagement

### 17.2 Robot with electronics

Electronics improves robot intelligence while respecting the same physical movement rules.

Expected benefits include:

- smarter route selection
- better obstacle avoidance/rerouting
- better engagement decisions
- improved weapon accuracy/effective range equivalent to a nominal +3 miles
- slightly increased resistance to enemy fire

Electronics never changes chassis terrain capability. For example, it cannot make a bipod or tracked chassis cross a ravine.

## 18. Weapon data and combat

### 18.1 Base weapon data

| Weapon | Base range | Lethality | Resource cost |
| --- | ---: | ---: | ---: |
| Cannon | 10 miles | 2 | 8 |
| Missiles | 14 miles | 3 | 4 |
| Phasers | 10 miles | 4 | 4 |
| Nuclear | 8-mile effect radius | special | 20 |

The miles-to-grid-cell conversion must match the ZX Spectrum map scale and must be represented as game-rule data.

### 18.2 Normal weapon firing

Cannon, missiles, and phasers use the projectile/fire-channel rules.

A robot may fire only one normal weapon at a time.

Once a robot fires, it cannot fire another normal weapon until its active projectile finishes its lifecycle.

The active projectile ends when it:

- hits something
- crashes into an obstacle
- hits a robot
- exits its valid play/screen area according to the original mechanics

There are no independent per-weapon cooldowns unless required by verified Spectrum behavior.

Projectiles have height/flight rules and use height-aware collision against physical objects.

### 18.3 Nuclear weapon

The nuke is a special detonation, not a normal travelling projectile.

On detonation:

- all destroyable robots within an 8-mile radius are destroyed
- all destroyable factories within an 8-mile radius are destroyed
- eligible war bases within the radius are destroyed
- the robot carrying the nuke is destroyed

Nuclear weapons are the only way to destroy factories and war bases.

## 19. Damage and strength

Normal weapon damage preserves the original ZX Spectrum formula through one isolated engine rule function:

`base_damage = (60 - (robot_height + ground_height)) / 4`

Configured default multipliers are:

- cannon: 2
- missile: 3
- phaser: 4

The multipliers are game-rule configuration, not literals embedded in projectile/firing code. Exact integer truncation and remaining accuracy/electronics-resistance behavior follow verified original semantics.
