# Nether Earth Clone — Functional Specification

## 1. Purpose

Build a browser-based multiplayer clone of the ZX Spectrum release of **Nether Earth**, preserving the original mechanics, map, visual feeling, commander behavior, robot construction, terrain interaction, economy, autonomous orders, direct control, combat, and overall gameplay character.

Version 1 is **human-vs-human PvP**. AI is out of scope for v1, but the engine/match boundary must allow an AI controller to be added later without redesigning core rules.

## 2. Fidelity order

When a rule is uncertain, use this order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

The goal is faithful behavior, not modernization into a conventional RTS.

## 3. v1 scope

Included:

- browser game;
- two-player PvP;
- original long rectangular ZX Spectrum battlefield/map;
- original-style 2.5D presentation;
- commander movement, vertical motion, collision, docking, and direct robot control;
- robot construction from chassis, 1–3 weapons, optional electronics;
- factory/war-base ownership, production, capture, destruction, and victory;
- terrain-dependent movement;
- autonomous robot orders;
- direct control and combat control;
- projectile combat and nuclear detonation;
- guest-only nicknames + join code/link;
- in-memory active matches;
- deterministic replay/debug logging.

Out of scope for v1:

- accounts/profiles;
- database/Redis/message broker;
- AI opponent;
- horizontal scaling/multiple backend replicas;
- 3D rendering/models;
- modern RTS control redesign.

## 4. PvP scenario and victory

The original map keeps all four war bases.

Default PvP starting state:

- Player 1 owns the **extreme-left war base**;
- Player 2 owns the **extreme-right war base**;
- the two interior war bases start **neutral and capturable**;
- factories start neutral unless scenario data overrides them;
- each player starts with **20 general resources** by default.

Starting ownership is scenario-overlay data separate from immutable map geometry.

Victory condition:

> A player loses immediately when they own zero war bases.

Victory is evaluated after any authoritative event that changes war-base ownership or existence, including capture and nuclear destruction.

## 5. Match flow

1. Player creates match and chooses nickname.
2. Server creates in-memory match and returns join code/link.
3. Second player joins.
4. Both become ready.
5. Server initializes map, scenario, resources, commanders, factories, and game clock.
6. Players capture structures, produce/spend resources, build robots, issue orders, dock, direct-control, and fight.
7. Match ends when one player owns zero war bases or when runtime rules cause a forfeit.
8. Result is shown and replay/debug data finalized.
9. In-memory match state is discarded.

No account is required.

## 6. Game clock

Authoritative simulation rate: **20 Hz**.

Locked time scale:

- 1 real minute = 10 in-game hours;
- 6 real seconds = 1 in-game hour;
- 1 in-game day = 144 real seconds;
- 1 in-game hour = 120 ticks;
- 12 in-game hours = 1,440 ticks;
- 1 in-game day = 2,880 ticks.

Gameplay time is derived from simulation ticks, never wall-clock timers.

## 7. World model

### 7.1 Grid

- authoritative X/Y coordinates are integers;
- robots move cell-to-cell;
- client interpolation is visual only;
- 1 mile = **2 map cells**; 1 cell = 0.5 miles.

Derived distance defaults:

- cannon range 10 miles = 20 cells;
- missile range 14 miles = 28 cells;
- phaser range 10 miles = 20 cells;
- electronics nominal +3 miles = +6 cells;
- nuke radius 8 miles = 16 cells;
- Advance/Retreat 0–50 miles = 0–100 cells.

### 7.2 Terrain

Required terrain classes:

- normal;
- rough;
- ditch/ravine.

Terrain is a cell property rather than a generic solid entity.

### 7.3 Static geometry

Factories, war bases, and scenery are not modeled as one generic rectangular footprint.

- a war base has a canonical composition of explicit physical cells/components plus semantic metadata such as heli-pad, exit, capture zone, ownership, and resource behavior;
- a factory has its own canonical composition, production type, and capture zone;
- generic blockers/scenery use explicit evidence-backed occupied cells/components;
- component heights may differ inside one structure;
- exact original layouts come from Spectrum evidence and must not be guessed when uncertain.

Robot footprint is a separate robot-model concern.

## 8. Commander

Each player controls one indestructible, untargetable anti-grav commander.

The commander:

- cannot be destroyed or damaged;
- is a physical collision entity;
- can fly over objects when vertically clear;
- can block robots and the opposing commander;
- docks automatically onto friendly robots;
- is used to enter construction by landing on the player's war-base heli-pad.

### 8.1 Horizontal movement

Commander X/Y movement is authoritative cell-to-cell movement. Rendering may interpolate between cells.

### 8.2 Vertical movement

Spectrum-compatible default vertical rules:

- minimum altitude: 0;
- maximum altitude: 48;
- vertical update every 4 simulation ticks;
- ascent step: +2;
- descent/gravity step: -1;
- holding Space ascends; releasing Space descends;
- horizontal and vertical movement may occur simultaneously;
- automatic elevation after exiting a robot/war base uses the same +2 semantics.

All numeric values are centralized gameplay configuration, with the Spectrum values above as defaults.

### 8.3 Commander collision

Commander collision is height-aware.

- commander may share X/Y with a physical object only when vertical ranges do not overlap;
- opposing commanders may share X/Y only when vertically separated;
- overlapping commanders block one another horizontally and vertically, including descent;
- a commander can block robot movement when collision volumes overlap.

### 8.4 Docking and enemy robots

Friendly docking is automatic when descending onto the top of a friendly robot.

When docked:

- commander follows the robot;
- independent commander movement is disabled;
- robot command/order/combat controls become available;
- rising away undocks.

Enemy robots are physical collision surfaces only. The commander may rest on top of one if geometry permits, but there is no docking, control transfer, or contact damage.

## 9. Ownership and capture

There are six factory production categories:

- chassis;
- electronics;
- nuclear;
- missile;
- phaser;
- cannon.

Neutral factories become owned by the first qualifying robot under the verified original behavior.

Enemy factory and war-base capture use continuous occupation by default:

- duration: 12 in-game hours = 1,440 ticks = 72 real seconds;
- if qualifying occupation breaks, progress resets immediately to zero;
- no partial progress is retained;
- ownership transfers immediately on completion.

Capture duration is centralized scenario/game-rule configuration.

## 10. Economy and construction resources

### 10.1 Resource pools

The engine distinguishes:

- general;
- chassis;
- electronics;
- cannon;
- missile;
- phaser;
- nuclear.

Production per in-game day:

- each owned factory: +2 units to its type-specific pool;
- each owned war base: +5 general resources.

Default starting general resources: **20**.

### 10.2 Original Spectrum construction costs

| Component | Default cost |
| --- | ---: |
| Bipod | 3 |
| Tracks | 5 |
| Anti-grav | 10 |
| Cannon | 2 |
| Missile | 4 |
| Phaser | 4 |
| Nuclear | 20 |
| Electronics | 3 |

All values are centralized gameplay configuration.

### 10.3 Spending semantics

Construction preserves original Spectrum behavior:

- spend the relevant type-specific resource pool first;
- general resources pay only the shortfall;
- reject selection when specific + general cannot cover the component cost;
- construction editing uses a temporary resource buffer;
- deselecting components reverses the mixed specific/general spending semantics;
- actual resources are committed atomically only when **Start Robot** succeeds;
- exiting/canceling before launch consumes no permanent resources.

## 11. Robot construction

Construction is entered from the player's war-base heli-pad.

A robot requires:

- exactly one chassis;
- 1–3 weapons;
- optional electronics;
- no duplicate component type.

The nuke may be the only weapon.

Construction cannot launch when:

- player already has 24 robots;
- war-base exit is blocked;
- build is invalid;
- resources are insufficient.

## 12. Canonical component stack

Bottom-to-top order:

1. chassis
2. cannon
3. missile
4. phaser
5. nuke
6. electronics
7. commander when docked

Missing components are omitted without changing relative order.

The same stack definition drives:

- rendering;
- robot height;
- collision height;
- projectile interaction;
- commander docking height;
- construction preview.

## 13. Chassis and terrain behavior

### Bipod

- normal: traversable;
- rough: traversable with severe slowdown;
- ditch/ravine: blocked.

### Tracks

- normal: traversable;
- rough: traversable with smaller slowdown than bipod;
- ditch/ravine: blocked.

### Anti-grav

- normal: traversable;
- rough: traversable;
- ditch/ravine: traversable;
- fastest chassis on ordinary terrain.

Relative ordinary-terrain speed is locked: **bipod < tracks < anti-grav**.

Exact ticks-per-cell and terrain penalties remain a fidelity research item and must remain centralized game-rule data.

## 14. Robot movement and destination reservation

Robots move cell-to-cell over an integer tick duration.

A move is invalid if terrain is forbidden, physical occupancy blocks it, or commander collision blocks it.

When a move starts, its destination cell is reserved. Other robots cannot claim that destination until the reservation completes or is canceled.

If multiple valid robots claim the same destination on the same authoritative tick:

- winner is selected using the match-local seeded deterministic RNG;
- two contenders are a 50/50 coin flip;
- more than two contenders are selected uniformly;
- losers remain outside the cell and may retry/replan.

This makes contention random to players but deterministic/replay-safe.

## 15. Robot control modes

Available after docking:

- command menu;
- direct control;
- orders menu;
- combat control.

Direct control uses exactly the same movement rules as autonomous movement.

Combat control lets the player choose fitted weapons and move using the same movement layer.

## 16. Autonomous orders

Supported orders:

- **Stop & Defend** — hold position and engage valid enemies;
- **Advance N** — move East 0–50 miles, then Stop & Defend;
- **Retreat N** — move West 0–50 miles, then Stop & Defend;
- **Search & Capture** — target neutral factories, enemy factories, or war bases;
- **Search & Destroy** — target robots, factories, or war bases.

Invalid/impossible orders fall back to Stop & Defend.

### Navigation intelligence

Without electronics:

- limited/original-style local routing;
- may fail to route around obstacles and become blocked/stuck.

With electronics:

- deterministic proper pathfinding/replanning;
- actively routes around obstacles when a valid chassis-compatible path exists.

Electronics never grants forbidden terrain traversal.

## 17. Combat

### 17.1 Normal projectile channel

Cannon, missile, and phaser use normal projectile rules.

A robot can have only one active normal projectile channel; it cannot fire another normal weapon until that projectile ends.

All normal projectiles use Spectrum default flight altitude **10**, independent of robot height and weapon type.

Exact projectile speed, tick cadence, collision profile, and termination behavior remain fidelity research items and must be authoritative world/game rules, not browser viewport rules.

### 17.2 Damage

Normal-weapon damage preserves the original formula:

`base_damage = (60 - (robot_height + ground_height)) / 4`

Default multipliers:

- cannon: 2;
- missile: 3;
- phaser: 4.

The formula is isolated behind one engine function; multipliers are centralized config.

Exact integer rounding, hit probability, strength semantics, and electronics resistance/accuracy effects remain fidelity research items.

### 17.3 Nuclear weapon

Nuclear detonation is separate from normal projectiles.

Default authoritative effect radius: **8 miles = 16 cells**.

On detonation:

- eligible robots in radius are destroyed;
- eligible factories in radius are destroyed;
- eligible war bases in radius are destroyed;
- carrier robot is destroyed.

Nuclear weapons are the only way to destroy factories and war bases.

## 18. Disconnect/reconnect

Runtime behavior in v1:

- match pauses immediately when either player disconnects;
- simulation ticks and gameplay timers stop while paused;
- default reconnect grace period: 60 seconds, configurable;
- reconnect receives current authoritative snapshot;
- match resumes only when both players are connected;
- grace expiry causes forfeit when an opponent remains eligible to win;
- if both players disconnect, each has its own grace deadline;
- if both deadlines expire without either returning, match ends abandoned/no-contest;
- no manual pause in v1.

Reconnect policy is runtime/session state and must not mutate deterministic engine state while paused.

## 19. Remaining fidelity research

Only these substantive areas remain unresolved:

1. exact chassis movement timing/rough-terrain penalties;
2. exact projectile speed/cadence/collision/lifetime;
3. exact combat accuracy/rounding/strength/electronics modifiers.

Until verified, these values/algorithms must remain isolated and configurable rather than silently guessed.