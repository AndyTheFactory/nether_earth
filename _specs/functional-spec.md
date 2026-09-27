# Nether Earth Clone — Functional Specification

## 1. Purpose

Build a browser-based multiplayer clone of the ZX Spectrum release of **Nether Earth**, preserving the original mechanics, map, visual feeling, commander behavior, robot construction, terrain interaction, economy, autonomous orders, direct control, combat, and overall gameplay character.

Version 1 is **human-vs-human PvP**, plus single-player play against a computer opponent (CR004, owner decision 2026-09-25; §3.1).

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
- deterministic replay/debug logging;
- single-player match against a computer opponent, one difficulty (CR004; §3.1).

Out of scope for v1:

- accounts/profiles;
- database/Redis/message broker;
- horizontal scaling/multiple backend replicas;
- 3D rendering/models;
- modern RTS control redesign.

### 3.1 Single-player vs AI opponent (CR004, owner decision 2026-09-25)

A single "Play vs computer" entry point creates a match already filled with a computer-controlled
second seat; no second human joins it.

- **One difficulty.** No difficulty selector, no tuning UI.
- **No commander.** The AI seat has no commander, and none is shown. It orders robots and builds
  without a commander landing on its own heli-pad or occupying any cell on the map. This is a
  deliberate asymmetry with the human seat, not a missing rule (`open-questions.md`, "Documented
  deviations").
- **Fidelity.** The Spectrum's enemy computer player is a reference for this AI, not a contract it
  must reproduce; the goal is an opponent that plays better than the original (`open-questions.md`,
  "Documented deviations").
- **Disconnect.** Unchanged from PvP: if the human disconnects, the match pauses with the usual
  grace window (§18).
- Human-vs-human PvP is unaffected; everything above applies to the AI seat only.
- **What it does (shipped).** The AI builds an affordable, purposeful robot design rather than a
  random one, keeps a defence reserve once its army reaches a minimum size, and rotates its
  builds over every war base it owns. It sends robots after whichever neutral or enemy structure
  is worth the most (production value, distance, how contested it already is) rather than
  simply the nearest one, keeps a nuclear robot in its army once it is established, and diverts
  or holds a defender when an enemy robot closes on one of its own war bases or factories. See
  `technical-spec.md` §28 for the implementation and `open-questions.md` "Documented deviations"
  for each place this departs from the Spectrum's own enemy AI.

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
- robots, the commander and normal projectiles are **2×2 bodies**: a unit's `(x, y)` is the anchor of a body covering columns `x..x+1` and rows `y−1..y` (the min-x/max-y cell, the same corner scenery elements use). Every collision, landing and blocking test uses the whole body; see `open-questions.md` §21;
- client interpolation is visual only;
- 1 mile = **2 map cells**; 1 cell = 0.5 miles.

Derived distance defaults:

- Advance/Retreat 0–50 miles = 0–100 cells.

Weapon ranges and the nuclear blast are defined directly in cells from the ZX Spectrum code, not converted from miles (see `open-questions.md` §8 and §20). The manual's "10/14 mile" weapon figures equal the code's cell counts:

- cannon range 10 cells;
- missile range 14 cells;
- phaser range 10 cells;
- electronics +2 cells;
- nuclear blast: per-kind shapes, see §17.3.

### 7.2 Terrain

Required terrain classes:

- normal;
- rough;
- mountain;
- ditch/ravine.

These correspond to the Spectrum map element types: normal = types 0–1 (height 0), rough = types 2–7 (height 2–3), mountain = types 8–11 (height 6), ditch = types 12–14 (height 0). See `open-questions.md` §4.

Terrain is a cell property rather than a generic solid entity.

Terrain pieces have these Spectrum heights (CR002.21 #203; nuclear debris counts as rough, height 3). The commander cannot fly into a piece below its height and rests on top of it. The damage formula's `ground_height` is the highest piece under the robot (§17.2). Normal projectiles (altitude 10) always fly over terrain. Terrain is drawn flat, as in the original. Robots stand on the terrain under them (CR002.25 #214, owner decision 2026-09-21: match the original). A robot's altitude is the highest surface under its 2×2 body, and its top is that altitude plus its stack height. The altitude changes when the robot arrives on a new cell. The commander lands, docks, rides and is ejected at the robot's top, it collides with the robot up to its top, and a commander below a robot's top blocks that robot's moves. The robot is drawn raised by its altitude; ground heights (terrain pieces and debris) are drawn exaggerated ×3 under robots, the commander and bullets so climbs read (presentation only, CR005.2). A new robot leaves its war base at altitude 0, because every war-base exit on the original map is flat.

### 7.3 Static geometry

Factories, war bases, and scenery are not modeled as one generic rectangular footprint.

- a war base has a canonical composition of explicit physical cells/components plus semantic metadata such as heli-pad, exit, capture zone, ownership, and resource behavior;
- a factory has its own canonical composition, production type, and capture zone;
- generic blockers/scenery use explicit evidence-backed occupied cells/components;
- the original map's scenery is 165 decoded 2×2 elements (CR002.1): low boxes (height 7), high boxes (height 15) and the fences that close both ends of the map (height 99). No robot of any chassis can enter a scenery cell. The commander crosses a box only at or above its height and can rest on top of it; it can never cross a fence. Normal projectiles (altitude 10) fly over low boxes and are stopped by high boxes and fences. A nuclear blast can turn boxes into rough debris (§17.3). See `open-questions.md` §4;
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
- is used to enter construction by landing on the player's war-base heli-pad;
- is a 2×2 body, like a robot (§7.1).

The heli-pad is a 2×2 area on the war-base roof, anchored at (anchor.x, anchor.y − 4). Landing means the commander's whole body is over the pad at an altitude equal to the pad's component height (15 on the original war base). The launched robot exits with its anchor on the war-base anchor cell. See `open-questions.md` §18 and §21.

Starting positions: Player 1 starts at the extreme-left war-base anchor + (−5, +1), altitude 0 (from the Spectrum code). Player 2 starts at the extreme-right war-base anchor + (+5, +1), altitude 0. This mirror is a locked PvP adaptation (`open-questions.md` §17).

### 8.1 Horizontal movement

Commander X/Y movement is authoritative cell-to-cell movement. Rendering may interpolate between cells.

A held direction chains cells with no idle tick: a move command that arrives on the tick the current move completes starts on that tick (CR003.10 #232, owner decision 2026-09-22; `open-questions.md` §23).

### 8.2 Vertical movement

Default vertical rules (Spectrum-compatible except the descent step):

- minimum altitude: 0;
- maximum altitude: 48;
- vertical update every 4 simulation ticks;
- ascent step: +2;
- descent/gravity step: -2 (owner deviation from the Spectrum's -1, CR003.1 #216, 2026-09-22; see `open-questions.md` §13). The ship still lands exactly on the surface under it: a surface at an odd altitude shortens the last step instead of being skipped;
- holding Space ascends; releasing Space descends;
- horizontal and vertical movement may occur simultaneously;
- automatic lift after leaving the construction screen or a robot: the commander climbs +2 on each of the next **5** vertical updates, whatever the rise key does, and then normal rise/gravity applies (Spectrum `Lfd30_player_elevate_timer`, set to 5 by `Lcb8e_construction_screen_exit` and by `La7fd` when leaving a robot). Moving along the map's y axis during the lift does not shorten it (owner decision 2026-09-21: the Spectrum quirk where held up/down keys shorten it is ignored). Neither construction nor auto-docking is checked while the lift runs. Afterwards, a commander that falls back onto the pad re-opens construction, and one that falls back onto the same friendly robot's anchor docks again. See `open-questions.md` §13.

All numeric values are centralized gameplay configuration, with the values above as defaults.

### 8.3 Commander collision

Commander collision is height-aware.

- collision uses the commander's 2×2 body: the highest structure, scenery or terrain piece under any of its four cells, and every robot or commander whose body overlaps it (`open-questions.md` §21);
- the commander cannot descend below the terrain piece height under its body and rests on it (rough 2–3, mountain 6; `open-questions.md` §4, CR002.21 #203);
- commander may share X/Y with a physical object only when vertical ranges do not overlap;
- opposing commanders may share X/Y only when vertically separated;
- overlapping commanders block one another horizontally and vertically, including descent;
- a commander can block robot movement when collision volumes overlap.

### 8.4 Docking and enemy robots

Friendly docking is automatic when descending onto the top of a friendly robot with the commander's anchor on the robot's anchor. A commander held up by a robot whose body only partly overlaps its own rests on it but does not dock.

When docked:

- commander follows the robot;
- independent commander movement is disabled;
- robot command/order/combat controls become available;
- rising away undocks, with the same 5-update lift as leaving the construction screen (§8.2).

Enemy robots are physical collision surfaces only. The commander may rest on top of one if geometry permits, but there is no docking, control transfer, or contact damage.

## 9. Ownership and capture

There are six factory production categories:

- chassis;
- electronics;
- nuclear;
- missile;
- phaser;
- cannon.

A robot occupies a structure's capture cell when its **anchor** is on that cell; a body that merely covers the cell does not count.

Factory and war-base capture use continuous occupation, whether the structure is enemy-owned or neutral (owner decision, 2026-09-23). A neutral structure differs only in that any player's robot qualifies as an occupier; it earns no discount on the duration. This deliberately departs from the verified original behavior, under which a neutral factory was acquired instantly by the first qualifying robot.

Capture rules:

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

All values are centralized gameplay configuration. The construction screen shows these costs from a build-time copy of the engine defaults, which CI checks for drift; the costs are not sent in the protocol (owner decision 2026-09-21).

### 10.3 Spending semantics

Construction preserves original Spectrum behavior:

- spend the relevant type-specific resource pool first;
- general resources pay only the shortfall;
- reject selection when specific + general cannot cover the component cost;
- construction editing uses a temporary resource buffer;
- deselecting components reverses the mixed specific/general spending semantics;
- picking a different chassis while one is fitted swaps it (CR002.20, disassembly `Lca0f`): the fitted chassis is refunded and removed first, then the new chassis is paid for; if the new chassis is unaffordable even after that refund, it is rejected and the robot is left with no chassis (the Spectrum does not restore the old one); weapons and electronics are unaffected;
- actual resources are committed atomically only when **Start Robot** succeeds;
- exiting/canceling before launch consumes no permanent resources.

## 11. Robot construction

Construction is entered by landing on the player's war-base heli-pad (§8).

The construction screen is **modal** for the building player: while it is open, that player's commander cannot move, rise or fall. Besides fitting and removing modules, it offers two actions:

- **EXIT MENU** closes the screen and discards the build; no resources are spent;
- **START ROBOT** launches the build when it is valid (see below), commits the resources and closes the screen. When the build cannot launch, the screen stays open and nothing changes.

Closing the screen either way starts the commander's exit lift (§8.2).

In the Spectrum the whole game pauses while the construction screen is open. In PvP only the building player's commander is frozen; the match, the opponent and every robot keep running (locked PvP adaptation, owner decision 2026-09-21).

A robot requires:

- exactly one chassis;
- 1–3 weapons;
- optional electronics;
- no duplicate component type.

The nuke may be the only weapon.

Construction cannot launch when:

- player already has 24 robots;
- war-base exit is blocked (the new robot's 2×2 body in the doorway would be off the map or overlap a robot or a reserved destination, or a commander below the new robot's top is in the doorway — owner decision CR005.1; the original only checked robots, so robot and commander trapped each other);
- build is invalid;
- resources are insufficient.

A launched robot starts on Stop & Defend and **walks out** of the war base: on each of its own robot updates (§16) it takes one step south (+y), up to 5 steps, with normal movement legality and terrain timing (Spectrum `La6c8`, owner decision 2026-09-21). The walk-out ends early when a step is blocked or lost to a contending robot, when the robot fires instead, when a commander docks on it, or when any order is given to it.

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

Ticks per cell at 20 Hz (1 Spectrum game cycle = 4 ticks), from the Spectrum speed table (`open-questions.md` §4). The terrain that sets a robot's speed is the highest piece under its 2×2 body (mountain > rough > ditch > normal):

| Chassis | Normal | Rough | Mountain | Ditch/ravine |
|---|---|---|---|---|
| Bipod | 24 | 32 | blocked | blocked |
| Tracks | 16 | 24 | 28 | blocked |
| Anti-grav | 12 | 12 | 16 | 12 |

Tracks stay faster than bipod on every terrain both can cross; both lose the same 8 ticks per cell on rough terrain. Anti-grav is unaffected by rough terrain and ditches.

Relative ordinary-terrain speed is locked: **bipod < tracks < anti-grav**.

These values are centralized game-rule data.

## 14. Robot movement and destination reservation

Robots move cell-to-cell over an integer tick duration.

A move is invalid if any cell of the destination 2×2 body is off the map, is terrain the chassis cannot enter, or is a structure or scenery cell, or if another robot, a commander or a reserved destination overlaps that body.

When a move starts, its whole destination body is reserved. Other robots cannot claim a destination that overlaps it until the reservation completes or is canceled.

If multiple valid robots claim overlapping destinations on the same authoritative tick:

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

- **Stop & Defend** — hold position and engage valid enemies (a newly launched robot first walks out of its war base, §11);
- **Advance N** — move East 0–50 miles, then Stop & Defend;
- **Retreat N** — move West 0–50 miles, then Stop & Defend;
- **Search & Capture** — target neutral factories, enemy factories, or war bases. The war-base target takes **any war base not already the ordering player's**, neutral ones included, since there is no separate neutral-war-base target and the two interior war bases start neutral (owner decision, 2026-09-25). This is a deliberate deviation from the Spectrum, whose `Lb3d5_prepare_robot_order_building_target_search` builds one exact ownership-flag value to match on and so takes enemy-owned war bases only; the factory targets keep the original's split. The order never completes: the robot walks to the nearest matching structure that no other friendly robot with the same order already targets, holds its capture cell until the structure changes hands, then retargets and leaves. Selection is re-run on every evaluation (owner decision, 2026-09-23), so a structure that changes hands nearer to the robot than its current target pulls it in — except while the robot is already standing on its target's capture cell, where the capture in progress is never abandoned. With no matching structure it keeps the order and holds position (still defending) until one appears (CR003.2 #217, Spectrum `Lb289`/`Lb36c`; `open-questions.md` "Capture order lifecycle");
- **Search & Destroy** — target robots, factories, or war bases.

Invalid/impossible orders fall back to Stop & Defend. Search & Capture never falls back (above). Search & Destroy against robots falls back only when no hostile robot remains; while one exists the robot closes on its body and engages it (CR003.4 #219). It closes to a *lane-aligned* position — the two 2×2 bodies facing each other along a full edge, never corner to corner or one cell off the lane — because a shot travels along the firing robot's cardinal facing and would otherwise pass its target by (owner request 2026-09-25; see `open-questions.md` "Search & Destroy approach position").

An order-driven robot acts only on its own **robot update**, as in the Spectrum (`Lb154_robot_ai_update`, CR002.19 #197). Its update period is its move duration for the terrain under its body (§13). On an update it fires when it has a shot, and otherwise moves; a firing update does not move. Direct fire (§17.1) is not tied to the robot update.

Search & Destroy against factories or war bases requires a nuclear weapon. A robot without one cannot take that order and falls back to Stop & Defend.

### Autonomous nuclear use

An autonomous robot detonates its nuclear weapon only when it is on a Search & Destroy order against a factory or war base and arrives on its target structure's target cell (the same cell a Search & Capture order navigates to). No other order ever detonates it: Stop & Defend, Advance, Retreat, Search & Capture, and Search & Destroy against robots use normal weapons only. A player can still detonate manually under direct control. See `open-questions.md` §19.

### Facing

A robot faces one of the four cardinal directions. It is launched facing south — the direction it walks out of its war base's doorway — and turns to face each step it takes; a step that is rejected, or a tick in which it fires instead of moving, does not turn it.

Facing selects which of the four per-piece sprites the original encodes, and it decides where a shot goes (owner decision, 2026-09-23): a projectile always travels in the firing robot's facing, matching `Lb6d6_weapon_fire`, which copies the robot's direction into the bullet's. There is no aiming — to shoot a different way a robot must turn.

Turning costs time. A robot that wants to step or shoot in a direction it does not face spends one update rotating 90 degrees toward it and does not move that update (`Lb471`). A 180-degree reversal therefore takes two rotations, passing through a perpendicular direction on the way, and a robot mid-turn can neither move nor fire. The duration is centralized rule data (`robot_turn_ticks`, one Spectrum game cycle).

An autonomous robot with an enemy in range but the wrong facing turns toward it first and fires once the turn lands. A robot standing on a capture cell is the exception: it never turns for combat, because an interrupted capture resets to zero.

A robot that takes a capture cell turns to face **out** of the structure — away from the building's body — rather than staying pointed at the wall it walked into (owner decision, 2026-09-24). "Out" is derived from the structure's own components, so it is a property of the map rather than of the route the robot took. The original has no such rule (`Ladb7_building_loop` never touches the robot's direction); it follows from shots travelling in the robot's facing, which makes a robot facing a wall defenceless. Turning does not move the robot, so the capture keeps counting.

The original's facing-direction bonus to the autonomous fire-decision *scan* (10 cells ahead rather than 8) is still not adopted; see `open-questions.md` §8.

### Navigation intelligence

Without electronics:

- limited/original-style local routing: the step that closes the larger
  remaining axis; else carry on the way the robot is already walking; else a
  detour along the obstacle in a direction drawn for the robot and held for a
  few game cycles; else any step still legal (`open-questions.md` §5 "Detour
  fallback" and "Momentum", the original's `Lb326`/`Lb33e`/`Lb1f5`);
- erratic and slow around obstacles, and prone to walking into pockets, but
  immobile only where no legal step at all exists.

With electronics:

- deterministic proper pathfinding/replanning;
- actively routes around obstacles when a valid chassis-compatible path exists.

Electronics never grants forbidden terrain traversal.

## 17. Combat

### 17.1 Normal projectile channel

Cannon, missile, and phaser use normal projectile rules.

A robot can have only one active normal projectile channel; it cannot fire another normal weapon until that projectile ends. This per-robot channel is a documented deviation: the Spectrum shares two bullet slots among all of a side's autonomous robots, plus one slot for the player's combat-mode robot (owner decision 2026-09-21, `open-questions.md` §8).

All normal projectiles use Spectrum default flight altitude **10**, independent of robot height and weapon type.

Projectile speed, cadence, and range are resolved from the Spectrum code (`open-questions.md` §8): a projectile advances 2 cells every 4 ticks (one Spectrum game cycle) and travels 10 cells (cannon, phaser) or 14 cells (missile), +2 with electronics. Buildings use the generic altitude collision; there is no separate building rule. As in the Spectrum, the first move happens on the fire tick, so a target 1–2 cells away is hit at once. A robot fires at most once per game cycle (4 ticks). A robot's autonomous shot moves 4 cells in the cycle it is fired; a player's direct shot moves 2 (`open-questions.md` §8, CR002.2 #169). Projectile behavior is an authoritative world/game rule, not a browser viewport rule.

A projectile is a 2×2 body. After each 2-cell move, it stops when the highest piece under its body (structure, scenery or terrain) is at or above its altitude, so a bullet passing right beside a high box or fence stops. Otherwise it hits the first robot whose body overlaps its own (scan order: rows y−1, y, y+1, each west to east) and whose top (terrain altitude plus stack height, §7.2) is at least the projectile altitude (owner decision 2026-09-22; the original has no robot-height test for bullets). Commanders and other projectiles never stop a projectile (documented deviation 1 in `open-questions.md`).

### 17.2 Damage

Normal-weapon damage preserves the original formula:

`base_damage = (60 - (robot_height + ground_height)) / 4`

Default multipliers:

- cannon: 2;
- missile: 3;
- phaser: 4.

The formula is isolated behind one engine function; multipliers are centralized config.

`ground_height` is the highest surface (terrain piece, structure or scenery) under the robot's 2×2 body, as in the Spectrum (`ROBOT_STRUCT_ALTITUDE`): 2–3 on rough, 6 on mountains, 3 on debris, 0 on normal and ditch cells (CR002.21 #203).

Exact integer rounding, hit probability, strength semantics, and electronics resistance/accuracy effects remain fidelity research items.

### 17.3 Nuclear weapon

Nuclear detonation is separate from normal projectiles.

Blast shapes, from the Spectrum code (`open-questions.md` §20):

- **Robots:** every robot, of either player, inside a 9×9 window centred on the carrier with trimmed corners (row widths 5, 7, 9, 9, 9, 9, 9, 7, 5) is destroyed.
- **Buildings:** at most **one** building is destroyed per detonation. War bases are checked first, then factories, each in canonical order; the first building in range is destroyed, whoever owns it. Distances: dx = |carrier.x − building.x|, dy = |carrier.y + 1 − building.y| (a war base adds 4 to carrier.y first).
  - A war base is in range when dx < 7, dy < 7, and dx + dy < 10.
  - A factory is in range when dx < 5, dy < 5, and dx + dy < 7.
- **Carrier:** always destroyed.
- **Scenery:** every scenery box (element types 17–20) whose bottom-left cell is inside the robot window becomes rough debris: the whole 2×2 box turns into rough terrain that robots can cross at rough speed. Fences are never destroyed. The debris lasts for the rest of the match.
- **Destroyed building:** every cell of the destroyed war base or factory becomes rough debris (height 3), as in `Lbc27_replace_building_by_debris` (CR005.3). Robots cross it at rough speed; the commander rests on it at 3.

Robots killed by the blast leave no debris of their own. A robot killed in combat does (CR005.3, `Lb116_robot_destroyed`): when all four cells of its 2×2 body are plain ground (no terrain piece, structure, scenery or earlier debris), they become rough debris (height 3) for the rest of the match.

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

## 19. Presentation (Spectrum look and feel)

The browser client reproduces the look of the ZX Spectrum original (CR002; reference screenshots in `_specs/milestones/cr002/`). None of this is a gameplay rule; the engine decides every outcome.

- **Orientation:** the Spectrum's isometric grid. One step along +x is 8 px right and 4 px up, one step along +y is 4 px right and 8 px down, and height lifts straight up at 1 px per unit. The map's long axis runs from lower-left to upper-right. Each arrow key moves along the world axis closest to its on-screen direction.
- **Zoom:** the view shows about as much of the map as the original's play window (about 19 cells along the map and its full 16-cell width). The camera follows the local commander.
- **Fonts:** Spectrum-style 8×8 lettering for all HUD, menu and overlay text, and a double-height form for titles. The fonts were drawn for this project and render pixel-crisp.
- **Radar:** a 128 × 16 px strip, one pixel per cell, white on black. It shows a 128-column window of the map that scrolls in 64-column steps to keep the local commander inside it. It lights structure and scenery cells (not debris or destroyed structures) and marks every robot of both players with a 2×2 mark. It shows only the viewer's **own commander**, blinking (when docked, the robot's mark blinks instead). The opponent's commander is never shown (owner decision 2026-09-21, as in the original).
- **Construction screen:** a full-screen ROBOT CONSTRUCTION screen laid out after the original. It shows the resources available, the module list with costs, a preview of the robot stack, EXIT MENU and START ROBOT, and uses the Spectrum cursor and highlight colours: the option under the cursor in yellow, fitted module icons white, unfitted ones yellow.
- **Ownership flags:** an owned factory or war base carries a flag on its roof, on the −x side for Player 1 and on the +x side for Player 2. Neutral and destroyed structures have no flag.
- **Scenery:** boxes, fences and nuclear debris are drawn with 2×2 Spectrum sprites. The sprite for each blocker kind is chosen through a configurable asset mapping; v1 ships only the Spectrum assets.
- **Units:** robots, the commander and projectiles are drawn as 2×2 bodies.
- **Occlusion:** structures, scenery, robots, commanders and projectiles are depth-sorted, so a unit behind a structure is hidden.
- **Shadows:** the commander's and projectiles' shadows fall on the highest surface under them: building roofs, the heli-pad, scenery and terrain tops.
- **Labels:** structure names and robot strength numbers are optional and **off by default**. The player can toggle them.
- **Sound:** the Spectrum's beeper audio, decoded from the disassembly (owner decision 2026-09-25, #272). The title music plays on the lobby/title screen and stops when the match view opens. The game sounds are the original's: a robot firing, a shot hitting, a robot destroyed, a shot expiring, and the nuclear blast. Menu and construction-cursor actions beep as they do in the original. Sound is presentation only, derived from the snapshots the client already renders; it never decides or reports a gameplay outcome, and a missed or repeated sound has no effect on the match. The player can mute, and the choice is remembered per viewer. Browsers only start audio inside a user gesture, so the client stays silent until the first click or key press.

The flag and scenery sprites are original artwork decoded from the game. They may be used only while the repository and deployments are private, and must be licensed or replaced before any public release or deployment (#201, release checklist).

## 20. Remaining fidelity research

Only these substantive areas remain unresolved:

1. exact combat accuracy/rounding/strength/electronics modifiers;
2. the autonomous fire-decision scan distance (the Spectrum scans 8 cells, 10 in the facing direction, 12 with electronics; see `open-questions.md` §8).

Movement timing and scenery blockers (`open-questions.md` §4), projectile speed, range, lifetime and fire-cycle timing (§8), and the 2×2 bodies (§21) are resolved. `open-questions.md` also records the deviations from the original that the owner chose to keep.

Until verified, these values/algorithms must remain isolated and configurable rather than silently guessed.