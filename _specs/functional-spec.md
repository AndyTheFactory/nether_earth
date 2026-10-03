# Nether Earth Clone — Functional Specification

This document states the current gameplay and product rules. The reasons behind decided rules are in [resolved-questions.md](resolved-questions.md), the intentional differences from the ZX Spectrum in [deviations-from-original.md](deviations-from-original.md), and the questions still open in [open-questions.md](open-questions.md). How the engine implements each rule is explained in [docs/mechanics/](../docs/mechanics/README.md).

## 1. Purpose

Build a browser-based multiplayer clone of the ZX Spectrum release of **Nether Earth**, preserving the original mechanics, map, visual feeling, commander behavior, robot construction, terrain interaction, economy, autonomous orders, direct control, combat, and overall gameplay character.

Version 1 is **human-vs-human PvP**, plus single-player play against a computer opponent (§3.1).

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
- single-player match against a computer opponent, one difficulty (§3.1);
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
- horizontal scaling/multiple backend replicas;
- 3D rendering/models;
- modern RTS control redesign;
- a difficulty selector or AI tuning UI.

### 3.1 Single-player vs computer opponent

A single "Play vs computer" entry point creates a match whose second seat is computer-controlled; no second human joins it, and the match starts as soon as the human is ready (the client sends that automatically).

- **One difficulty.** No difficulty selector, no tuning UI.
- **No commander.** The computer seat has no commander, and none is shown. It orders robots and builds without landing on a heli-pad or occupying any cell on the map. In exchange it cannot capture on foot, drive a robot or fight in person. This asymmetry is deliberate.
- **Same rules.** Every action of the computer is an ordinary command validated exactly like a human's; it reads only what a player's own client shows (own resources, structure ownership, robot positions and builds), never the opponent's resources, orders or construction.
- **Fidelity.** The Spectrum's enemy computer player is a reference, not a contract; the goal is an opponent that plays better than the original.
- **Behaviour.** It builds the most valuable robot design its resources can pay for, raises its minimum weapon count as its army grows, keeps a reserve for a defender once it owns two robots, builds a nuclear robot once it owns six and has none, and rotates construction over every war base it owns. It sends robots after the neutral or enemy structures worth the most (production value, distance, how contested they are), sends nuclear robots only at opponent-owned structures, and diverts a defender when an enemy robot closes on one of its war bases or factories.
- **Disconnect.** If the human disconnects, the match pauses with the usual grace window (§18); letting it expire forfeits to the computer.
- Human-vs-human PvP is unaffected; everything above applies to the computer seat only.

## 4. PvP scenario and victory

The original map keeps all four war bases.

Default starting state:

- Player 1 owns the **extreme-left war base**;
- Player 2 owns the **extreme-right war base**;
- the two interior war bases start **neutral and capturable**;
- factories start neutral unless scenario data overrides them;
- each player starts with **20 general resources** and nothing in the type-specific pools.

Starting ownership is scenario-overlay data separate from immutable map geometry.

Victory condition:

> A player loses immediately when they own zero war bases.

Victory is evaluated in the same simulation step as any capture of a war base or nuclear destruction of a war base. At most one result is produced per step, and a result is final.

## 5. Match flow

1. A player creates a match with a nickname (or chooses "Play vs computer", §3.1).
2. The server creates an in-memory match and returns a join code (none for a solo match).
3. A second player joins with the code and a nickname.
4. Both become ready.
5. The server initializes map, scenario, resources, commanders, factories, and game clock, and the match starts.
6. Players capture structures, produce/spend resources, build robots, issue orders, dock, direct-control, and fight.
7. The match ends when one player owns zero war bases, or when the disconnect rules (§18) produce a forfeit or no-contest.
8. The result is shown and the replay/debug record is finalized.
9. In-memory match state is discarded a few minutes later.

No account is required.

### 5.1 Lobby and nicknames

- **Nicknames.** Leading and trailing whitespace is trimmed; the result must be 1–32 characters and contain at least one visible character (a letter, number, punctuation or symbol). Control characters, invisible format characters (zero-width space, word joiner, byte-order mark and similar), bidirectional-text controls, private-use and unassigned code points, and characters that render blank are rejected. A zero-width joiner is allowed only between two emoji characters.
- **No clash with the creator.** A joining guest may not use the creator's nickname, compared case-insensitively after NFKC normalisation. The join is rejected with `invalid_nickname` and the guest can retry with another name.
- **Join codes.** Six characters, letters and digits. A connection that fails to join five times is closed.
- **Session binding.** A browser connection must create, join or resume a session within 30 seconds of connecting, or it is closed.
- **One live connection per session.** A newer connection for the same session replaces the older one, which is closed.
- **Abandoned lobbies.** A lobby that still waits for its second player is discarded 30 seconds after its last connection goes away (enough to survive a page refresh), and in any case 15 minutes after it was created. The creator is told the lobby expired.
- **Capacity.** The server holds a bounded number of matches; when full, creating a match is refused with a "server busy" error.

All timings above are deployment settings, not gameplay rules.

## 6. Game clock

Authoritative simulation rate: **20 Hz**.

Locked time scale:

- 1 real minute = 10 in-game hours;
- 6 real seconds = 1 in-game hour;
- 1 in-game day = 144 real seconds;
- 1 in-game hour = 120 ticks;
- 12 in-game hours = 1,440 ticks;
- 1 in-game day = 2,880 ticks;
- 1 Spectrum game cycle = 4 ticks.

Gameplay time is derived from simulation ticks, never wall-clock timers.

## 7. World model

### 7.1 Grid

- authoritative X/Y coordinates are integers; the original map is 512 × 16 cells;
- robots and the commander move cell-to-cell;
- robots, the commander and normal projectiles are **2×2 bodies**: a unit's `(x, y)` is the anchor of a body covering columns `x..x+1` and rows `y−1..y`. Every collision, landing, blocking and hit test uses the whole body; two bodies collide when they overlap. The whole body stays on the map;
- client interpolation is visual only;
- 1 mile = **2 map cells**; 1 cell = 0.5 miles.

Advance/Retreat distances are 0–50 miles = 0–100 cells.

Weapon ranges and the nuclear blast are defined directly in cells from the ZX Spectrum code, not converted from miles. The manual's "10/14 mile" weapon figures equal the code's cell counts:

- cannon range 10 cells;
- missile range 14 cells;
- phaser range 10 cells;
- electronics +2 cells;
- nuclear blast: per-kind shapes, see §17.3.

### 7.2 Terrain

Terrain classes and their Spectrum map element types and piece heights:

| Class | Element types | Piece height |
|---|---|---|
| normal | 0–1 | 0 |
| rough | 2–7 | 2 (types 2–5) or 3 (types 6–7) |
| mountain | 8–11 | 6 |
| ditch/ravine | 12–14 | 0 |

Debris (left by nuclear blasts and destroyed robots, §17.3) is rough terrain of height 3.

Terrain is a cell property rather than a generic solid entity. Piece heights are physical:

- the commander cannot fly into a piece below its height and rests on top of it;
- a robot stands on the terrain under it: its **altitude** is the highest surface (terrain piece, structure or scenery) under its 2×2 body, and its **top** is that altitude plus its stack height. The altitude changes when the robot arrives on a new cell. A new robot leaves its war base at altitude 0, because every war-base exit on the original map is flat;
- the commander lands, docks, rides and is ejected at a robot's top, collides with the robot up to its top, and a commander below a robot's top blocks that robot's moves;
- the damage formula's `ground_height` is the robot's altitude (§17.2);
- normal projectiles (altitude 10) always fly over terrain.

### 7.3 Static geometry

Factories, war bases, and scenery are not modeled as one generic rectangular footprint.

- a war base has a canonical composition of explicit physical cells/components plus semantic interaction points: the heli-pad, the exit and the capture cell;
- a factory has its own composition, a production type, and a capture cell;
- component heights may differ inside one structure; war-base and factory blocks are 7 or 15 high;
- the original map's scenery is 165 decoded 2×2 elements: low boxes (height 7), high boxes (height 15) and the fences that close both ends of the map (height 99). No robot of any chassis can enter a scenery cell. The commander crosses a box only at or above its height and can rest on top of it; it can never cross a fence. Normal projectiles fly over low boxes and are stopped by high boxes and fences. A nuclear blast can turn boxes into debris (§17.3);
- exact original layouts come from Spectrum evidence and must not be guessed when uncertain.

## 8. Commander

Each human player controls one indestructible, untargetable anti-grav commander.

The commander:

- cannot be destroyed or damaged;
- is a physical collision entity;
- can fly over objects when vertically clear;
- can block robots and the opposing commander;
- docks automatically onto friendly robots;
- enters construction by landing on the heli-pad of a war base the player owns;
- is a 2×2 body, like a robot (§7.1).

The heli-pad is a 2×2 area on the war-base roof, anchored at (anchor.x, anchor.y − 4), where the anchor is the war base's capture cell. Landing means the commander's whole body is over the pad at an altitude equal to the highest component under it (15 on the original war base). A launched robot's anchor starts on the war-base anchor cell.

Starting positions: Player 1 starts at the extreme-left war-base anchor + (−5, +1), altitude 0 (from the Spectrum code). Player 2 starts at the extreme-right war-base anchor + (+5, +1), altitude 0, the mirror of Player 1.

### 8.1 Horizontal movement

Commander X/Y movement is authoritative cell-to-cell movement taking 4 ticks per cell; one move at a time. Rendering may interpolate between cells.

A held direction chains cells with no idle tick: a move command that arrives on the tick the current move completes starts on that tick.

A docked commander cannot move independently, and the building player's commander cannot move while its construction screen is open (§11).

### 8.2 Vertical movement

- minimum altitude: 0;
- maximum altitude: 48;
- vertical update every 4 simulation ticks;
- ascent step: +2 while the rise key is held;
- descent/gravity step: −2 when it is released. The ship still lands exactly on the surface under it: a surface at an odd altitude shortens the last step instead of being skipped;
- horizontal and vertical movement may occur simultaneously;
- **automatic lift:** after leaving the construction screen or a robot, the commander climbs +2 on each of the next **5** vertical updates, whatever the rise key does, and then normal rise/gravity applies. Moving does not shorten the lift. Neither construction entry nor auto-docking is checked while the lift runs. Afterwards, a commander that falls back onto the pad re-opens construction, and one that falls back onto the same friendly robot's anchor docks again.

All numeric values are centralized gameplay configuration, with the values above as defaults.

### 8.3 Commander collision

Commander collision is height-aware. The commander occupies a vertical range of 4 altitude units above its altitude; touching a surface is resting, not colliding.

- collision uses the commander's 2×2 body: the highest structure, scenery or terrain piece under any of its four cells, and every robot or commander whose body overlaps it;
- the commander cannot descend below the surface under its body and rests on it (rough 2–3, mountain 6);
- it may share X/Y with a physical object only when vertical ranges do not overlap;
- opposing commanders may share X/Y only when vertically separated; overlapping commanders block one another horizontally and vertically, including descent;
- a commander below a robot's top blocks that robot's movement into an overlapping body.

### 8.4 Docking and enemy robots

Friendly docking is automatic when the commander rests on the top of a friendly robot with its anchor on the robot's anchor. A commander held up by a robot whose body only partly overlaps its own rests on it but does not dock.

When docked:

- the commander follows the robot, resting on its top;
- independent commander movement is disabled;
- robot command/order/combat controls become available;
- the robot's standing order is suspended while the commander drives it, and resumes when the commander leaves;
- holding rise undocks, with the same 5-update lift as leaving the construction screen (§8.2).

If a robot is destroyed with the commander docked on it, the commander is freed at the robot's last top, unharmed.

Enemy robots are physical collision surfaces only. The commander may rest on top of one if geometry permits, but there is no docking, control transfer, or contact damage.

## 9. Ownership and capture

There are six factory production categories:

- chassis;
- electronics;
- nuclear;
- missile;
- phaser;
- cannon.

Every factory and war base has one capture cell. A robot occupies it when its **anchor** is on that cell; a body that merely covers the cell does not count.

Factory and war-base capture use continuous occupation by a robot that does not belong to the current owner, whether the structure is enemy-owned or neutral. A neutral structure differs only in that any player's robot qualifies; it earns no discount on the duration. When several qualifying robots stand on the cell, the one with the lowest id counts.

Capture rules:

- duration: 12 in-game hours = 1,440 ticks = 72 real seconds;
- if the occupying robot leaves, is destroyed, or is replaced by a different robot, progress resets to zero immediately;
- no partial progress is retained;
- ownership transfers on the tick the duration completes.

Capture duration is centralized game-rule configuration.

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

Production at each in-game day boundary (every 2,880 ticks):

- each owned factory: +2 units to its type-specific pool;
- each owned war base: +5 general resources.

Default starting general resources: **20**. Pools have no upper limit.

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

All values are centralized gameplay configuration. The construction screen shows these costs from a build-time copy of the engine defaults, which CI checks for drift; the costs are not sent in the protocol.

### 10.3 Spending semantics

Construction preserves original Spectrum behavior:

- spend the relevant type-specific resource pool first (all three chassis draw on the chassis pool);
- general resources pay only the shortfall;
- reject a selection when specific + general cannot cover the component cost;
- construction editing uses a temporary resource buffer copied from the player's resources when the screen opens;
- deselecting a component refunds its cost into its type-specific pool up to that pool's amount when the screen opened, and the rest into general resources;
- picking a different chassis while one is fitted swaps it: the fitted chassis is refunded and removed first, then the new chassis is paid for; if the new chassis is unaffordable even after that refund, it is rejected and the robot is left with no chassis (the Spectrum does not restore the old one); weapons and electronics are unaffected;
- resources are committed only when **Start Robot** succeeds;
- exiting/canceling before launch consumes no resources.

## 11. Robot construction

Construction is entered by landing on the heli-pad of a war base the player owns (§8); the computer seat opens the same screen remotely (§3.1). A player has at most one construction screen open at a time.

The construction screen is **modal** for the building player: while it is open, that player's commander cannot move, rise or fall. Besides fitting and removing modules, it offers two actions:

- **EXIT MENU** closes the screen and discards the build; no resources are spent;
- **START ROBOT** launches the build when it is valid (see below), commits the resources and closes the screen. When the build cannot launch, the screen stays open and nothing changes.

Closing the screen either way starts the commander's automatic lift (§8.2). The match, the opponent and every robot keep running while the screen is open.

A robot requires:

- exactly one chassis;
- 1–3 weapons;
- optional electronics;
- no duplicate component type.

The nuke may be the only weapon.

Construction cannot launch when:

- the player already has 24 robots alive;
- the war-base exit is blocked: the new robot's 2×2 body in the doorway would be off the map, or overlap a structure, a robot or another robot's reserved destination, or a commander that is not docked is in the doorway below the new robot's top;
- the build is invalid.

A launched robot starts on Stop & Defend, facing south, and **walks out** of the war base: on each of its own robot updates (§16.1) it takes one step south (+y), up to 5 steps, with normal movement legality and terrain timing. The walk-out ends early when a step is blocked or lost to a contending robot, when the robot fires instead, when a commander docks on it, or when any order is given to it.

Robot ids are never reused within a match, even after a robot is destroyed.

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

Component heights (Spectrum piece heights):

| Component | Height |
|---|---:|
| Bipod | 11 |
| Tracks | 7 |
| Anti-grav | 8 |
| Cannon | 6 |
| Missile | 6 |
| Phaser | 7 |
| Nuclear | 7 |
| Electronics | 7 |

A robot's stack height is the sum of its components: 13 (tracks + cannon) to 38 (bipod + missile + phaser + nuclear + electronics). The tallest robot on the highest walkable ground has its top at 44, below the commander's ceiling, so the commander can rest on every robot.

The same stack definition drives rendering, robot height, collision height, projectile interaction, commander docking height and the construction preview.

## 13. Chassis and terrain behavior

Ticks per cell at 20 Hz (1 Spectrum game cycle = 4 ticks), from the Spectrum speed table. The terrain that sets a move's speed is the highest-ranked class under the destination 2×2 body (mountain > rough > ditch > normal), and a move is legal only when the chassis may enter all four cells:

| Chassis | Normal | Rough | Mountain | Ditch/ravine |
|---|---|---|---|---|
| Bipod | 24 | 32 | blocked | blocked |
| Tracks | 16 | 24 | 28 | blocked |
| Anti-grav | 12 | 12 | 16 | 12 |

Tracks stay faster than bipod on every terrain both can cross; both lose the same 8 ticks per cell on rough terrain. Anti-grav is unaffected by rough terrain and ditches.

Relative ordinary-terrain speed is locked: **bipod < tracks < anti-grav**.

These values are centralized game-rule data.

## 14. Robot movement and destination reservation

Robots move cell-to-cell, one cardinal step at a time, over an integer tick duration (§13). A robot stays on its origin cell until the move completes.

A move is invalid if any cell of the destination 2×2 body is off the map, is terrain the chassis cannot enter, or is a structure or scenery cell, or if another robot, a commander below the robot's top, or another robot's reserved destination overlaps that body. A robot with a move or a turn in flight cannot start another.

When a move starts, its whole destination body is reserved. Other robots cannot claim a destination that overlaps it until the move completes or is canceled.

If several valid robots claim overlapping destinations on the same authoritative tick:

- the winner is selected using the match-local seeded deterministic RNG;
- two contenders are a 50/50 coin flip;
- more than two contenders are selected uniformly;
- losers stay where they are and may retry/replan.

This makes contention random to players but deterministic/replay-safe.

A robot that wants to step in a direction it does not face turns instead of moving (§16.3).

## 15. Robot control modes

Available after docking:

- command menu;
- direct control;
- orders menu;
- combat control.

Direct control moves the docked robot one cell at a time with exactly the same movement rules as autonomous movement, including turning. Combat control fires a chosen fitted weapon in the robot's facing; a nuclear weapon detonates on the spot. The client offers orders, direct control and combat control only for the robot the commander is docked on. The engine accepts order and fire commands for any robot the player owns (the computer seat gives orders this way); only direct control requires a docked commander.

## 16. Autonomous orders

Supported orders:

- **Stop & Defend** — hold position and engage enemy robots in range (a newly launched robot first walks out of its war base, §11). It is the fallback for every other order.
- **Advance N** — move East 0–50 miles, then Stop & Defend. The goal column is fixed when the order starts and clamped to the map; detours north or south do not change it.
- **Retreat N** — move West 0–50 miles, then Stop & Defend, as for Advance.
- **Search & Capture** — target neutral factories, enemy factories, or war bases. The war-base target takes **any war base not already the ordering player's**, neutral ones included; the factory targets keep the original's split. The order never completes and never falls back: the robot walks to the nearest matching structure that no other friendly robot with the same order already targets, holds its capture cell (still defending) until the structure changes hands, then retargets and leaves. Selection is re-run on every evaluation, so a structure that changes hands nearer to the robot than its current target pulls it in — except while the robot stands on its target's capture cell, where the capture in progress is never abandoned. With no matching structure it keeps the order and holds position until one appears.
- **Search & Destroy** — target enemy robots, or factories or war bases not owned by the player (neutral ones included).

Order fallbacks:

- an order that is invalid when given becomes Stop & Defend;
- Advance/Retreat fall back when the robot already stands at the map edge it was told to head for, or when an electronic robot finds no route at all;
- Search & Destroy against robots falls back only when no hostile robot remains (a robot whose only weapon is nuclear keeps hunting but never fires, §16.2). While a hostile robot exists, the robot closes on the nearest one and engages it, even when no route exists right now (it then steps directly toward it, or waits). It closes to a *lane-aligned* position — the two 2×2 bodies facing each other along a full edge, never corner to corner or one cell off the lane — because a shot travels along the firing robot's facing; when no lane-aligned position is reachable, any edge-touching position will do;
- Search & Destroy against a factory or war base requires a nuclear weapon; a robot without one falls back. It also falls back when no such structure exists or an electronic robot finds no route.

Stop & Defend engages the nearest hostile robot; so does Search & Capture while it holds position (on its capture cell, or with nothing to take). Search & Destroy (robots) engages its chosen target. Advance, Retreat, a Search & Capture robot on its way, and a Search & Destroy robot heading for a structure do not engage. A robot engages with the first fitted normal weapon (cannon, missile, phaser, in that order) whose range, plus the electronics bonus, reaches the target's anchor (Manhattan distance).

### 16.1 Robot updates

An order-driven robot acts only on its own **robot update**, as in the Spectrum. A robot is at an update when it has no move in flight and at least one update period has passed since its last shot; the period is its move duration for the terrain under its body (§13). On an update it fires when it has a shot, and otherwise moves; a firing update does not move. A robot that has neither moved nor fired since it was last idle is treated as being at an update on every tick. Direct fire (§17.1) is not tied to the robot update.

### 16.2 Autonomous nuclear use

An autonomous robot detonates its nuclear weapon only when it holds a Search & Destroy order against a factory or war base and arrives on its target structure's capture cell; the order completes with the detonation. No other order ever detonates it: Stop & Defend, Advance, Retreat, Search & Capture, and Search & Destroy against robots use normal weapons only. A player can still detonate manually (§15).

### 16.3 Facing and turning

A robot faces one of the four cardinal directions. It is launched facing south — the direction it walks out of its war base's doorway — and turns to face each step it takes.

Facing selects which of the four per-piece sprites is drawn, and it decides where a shot goes: a projectile always travels in the firing robot's facing. There is no aiming — to shoot a different way a robot must turn.

Turning costs time. A robot that wants to step or shoot in a direction it does not face spends 4 ticks (one Spectrum game cycle) rotating 90 degrees toward it and does not move. A 180-degree reversal therefore takes two rotations, passing through a perpendicular direction, and a robot mid-turn can neither move nor fire.

An autonomous robot with an enemy in range but the wrong facing turns toward it (the dominant axis of the offset; east/west on a tie) and fires once the turn lands. A robot standing on a capture cell never turns for combat, because an interrupted capture resets to zero.

A robot that holds a capture cell turns to face **out** of the structure — away from the building's body — rather than staying pointed at the wall it walked into. "Out" is derived from the structure's own components, so it is a property of the map rather than of the route the robot took. Turning does not move the robot, so the capture keeps counting.

### 16.4 Navigation intelligence

Without electronics (original-style local routing), on each update the robot tries, in order:

1. the step that closes the larger remaining distance (east/west on a tie);
2. the direction it is already facing (momentum);
3. the two perpendicular steps, in an order drawn for that robot and held for 16 ticks (4 game cycles);
4. any other legal step.

It is erratic and slow around obstacles and prone to walking into pockets, but immobile only where no legal step at all exists. It never gives up an order for lack of a route.

With electronics:

- deterministic shortest-time pathfinding, re-planned every update;
- actively routes around obstacles when a valid chassis-compatible path exists;
- a Search & Destroy (robots) hunter follows its planned route and re-plans every 20 ticks, or earlier when the route runs out, it is off the route, its next cell is no longer enterable, or the target changes.

Electronics never grants forbidden terrain traversal.

## 17. Combat

### 17.1 Normal projectile channel

Cannon, missile, and phaser use normal projectile rules.

A robot has one normal projectile channel; it cannot fire another normal weapon until its projectile ends. A robot fires at most one normal weapon per game cycle (4 ticks).

All normal projectiles fly at altitude **10**, independent of robot height and weapon type.

A projectile advances 2 cells every 4 ticks along the firing robot's facing and travels 10 cells (cannon, phaser) or 14 cells (missile), +2 with electronics. The first move happens on the fire tick, so a target 1–2 cells away is hit at once. A robot's autonomous shot moves 4 cells in the game cycle it is fired; a player's direct shot moves 2. Projectile behavior is an authoritative world/game rule, not a browser viewport rule.

A projectile is a 2×2 body. After each 2-cell move it stops when it leaves the map, or when the highest piece under its body (structure, scenery or terrain) is at or above its altitude, so a bullet passing right beside a high box or fence stops. Otherwise it hits the first robot whose body overlaps its own (scan order: rows y−1, y, y+1, each west to east) and whose top (§7.2) is at least the projectile altitude. Buildings use this generic altitude collision; there is no separate building rule. Commanders and other projectiles never stop a projectile. A projectile whose firing robot is destroyed keeps flying.

### 17.2 Damage

Normal-weapon damage preserves the original formula:

`damage = ((60 − (robot_height + ground_height)) // 4) × multiplier`

with integer floor division, and default multipliers:

- cannon: 2;
- missile: 3;
- phaser: 4.

`robot_height` is the target's stack height (§12). `ground_height` is the highest surface (terrain piece, structure or scenery) under the target's 2×2 body: 2–3 on rough, 6 on mountains, 3 on debris, 0 on normal and ditch cells. A robot on high ground therefore takes less damage. A phaser hit on a tracks + cannon robot on flat ground deals 44.

Robots start with strength 100. A hit that brings strength to 0 or below destroys the robot at once. Every hit that connects deals damage: there is no hit roll, no component damage, and electronics affects range only.

The formula is isolated behind one engine function; multipliers are centralized config. Further combat-fidelity research is tracked in [open-questions.md](open-questions.md).

### 17.3 Nuclear weapon

Nuclear detonation is separate from normal projectiles. It creates no projectile and does not use the projectile channel.

Blast shapes, from the Spectrum code, measured from the carrier before anything is destroyed:

- **Robots:** every robot, of either player, whose anchor is inside a 9×9 window centred on the carrier with trimmed corners (row widths 5, 7, 9, 9, 9, 9, 9, 7, 5) is destroyed.
- **Buildings:** at most **one** building is destroyed per detonation. War bases are checked first, then factories, each in map order; the first building in range is destroyed, whoever owns it. Distances are measured to the building's capture cell: dx = |carrier.x − building.x|, dy = |carrier.y + 1 − building.y| (a war base adds 4 to carrier.y first).
  - A war base is in range when dx < 7, dy < 7, and dx + dy < 10.
  - A factory is in range when dx < 5, dy < 5, and dx + dy < 7.
- **Carrier:** always destroyed.
- **Scenery:** every scenery box whose bottom-left cell is inside the robot window becomes rough debris: the whole 2×2 box turns into rough terrain (height 3) that robots cross at rough speed. Fences are never destroyed.
- **Destroyed building:** every cell of the destroyed war base or factory becomes rough debris (height 3). It no longer blocks robots or the commander, cannot be captured, produces nothing and no longer counts for victory.

Robots killed by the blast leave no debris of their own. A robot killed in combat does: when all four cells of its 2×2 body are plain normal ground with no structure, scenery or earlier debris, they become rough debris (height 3). All debris lasts for the rest of the match.

Nuclear weapons are the only way to destroy factories and war bases.

## 18. Disconnect and reconnect

Runtime behavior in v1:

- the match pauses immediately when a human player disconnects (closing the page or leaving counts);
- simulation ticks and gameplay timers stop while paused;
- default reconnect grace period: 60 seconds, configurable;
- a reconnecting player receives the current authoritative snapshot;
- the match resumes only when every human player is connected;
- a player whose grace expires while the opponent is still eligible to win — connected, or disconnected with a later grace deadline — forfeits;
- if both players are disconnected, each has its own grace deadline; the one that expires first forfeits, and only when both expire at the same moment does the match end as no-contest;
- in a solo match the computer seat never disconnects, so the human's expiry is always a forfeit;
- no manual pause in v1.

Reconnect policy is runtime/session state and must not mutate deterministic engine state while paused.

## 19. Presentation (Spectrum look and feel)

The browser client reproduces the look of the ZX Spectrum original (reference screenshots in [docs/reference-screens/](../docs/reference-screens/cr002/)). None of this is a gameplay rule; the engine decides every outcome.

- **Orientation:** the Spectrum's isometric grid. One step along +x is 8 px right and 4 px up, one step along +y is 4 px right and 8 px down, and height lifts straight up at 1 px per unit. The map's long axis runs from lower-left to upper-right. Each arrow key moves along the world axis closest to its on-screen direction.
- **Zoom:** the view shows about as much of the map as the original's play window (about 19 cells along the map and its full 16-cell width). The camera follows the local commander and centres the play view beside the menu column.
- **Fonts:** Spectrum-style 8×8 lettering for all HUD, menu and overlay text, and a double-height form for titles. The fonts were drawn for this project and render pixel-crisp.
- **Radar:** a 128 × 16 px strip, one pixel per cell, white on black. It shows a 128-column window of the map that scrolls in 64-column steps to keep the local commander inside it. It lights structure and scenery cells (not debris or destroyed structures) and marks every robot of both players with a 2×2 mark. It shows only the viewer's **own commander**, blinking (when docked, the robot's mark blinks instead). The opponent's commander is never shown.
- **Robot menu:** when docked, a right-hand HUD column shows DAY/TIME, the DIRECT CONTROL / GIVE ORDERS / COMBAT MODE / LEAVE ROBOT options with the selected one highlighted, the current order and the robot's strength.
- **Construction screen:** a full-screen ROBOT CONSTRUCTION screen laid out after the original. It shows the resources available, the module list with costs, a preview of the robot stack, EXIT MENU and START ROBOT, and uses the Spectrum cursor and highlight colours: the option under the cursor in yellow, fitted module icons white, unfitted ones yellow.
- **Ownership flags:** an owned factory or war base carries a flag on its roof, on the −x side for Player 1 and on the +x side for Player 2. Neutral and destroyed structures have no flag.
- **Sprites:** robots (one sprite per piece and facing), the commander (one frame), war-base and factory walls, boxes, fences and debris are drawn with decoded 2×2 Spectrum sprites. The sprite for each scenery kind is chosen through a configurable asset mapping; a kind without a valid mapping is drawn as a placeholder prism. The map-end fence post is drawn centred on its footprint.
- **Heights:** a robot is drawn raised by its terrain altitude; ground heights (terrain pieces and debris) are drawn exaggerated ×3 under robots, the commander and projectiles so climbs read. Terrain itself is drawn flat, as in the original.
- **Occlusion:** structures, scenery, robots, commanders and projectiles are depth-sorted, so a unit behind a structure is hidden.
- **Shadows:** the commander's and projectiles' shadows fall on the surface under them — building roofs, the heli-pad, scenery and terrain tops. The commander's shadow is cut per cell, each part on the surface of the cell it covers.
- **Labels:** structure names and robot strength numbers are optional and **off by default**. The player can toggle them.
- **Sound:** the Spectrum's beeper audio, decoded from the disassembly. The title music plays on the lobby/title screen and stops when the match view opens. The game sounds are the original's: a robot firing, a shot hitting, a robot destroyed, a shot expiring, and the nuclear blast. Menu and construction-cursor actions beep as they do in the original. Sound is presentation only, derived from the snapshots the client already renders; it never decides or reports a gameplay outcome, and a missed or repeated sound has no effect on the match. The player can mute, and the choice is remembered per viewer. Browsers only start audio inside a user gesture, so the client stays silent until the first click or key press.

The sprites and sounds decoded from the game are original artwork. They may be used only while the repository and deployments are private, and must be licensed or replaced before any public release or deployment.

## 20. Open questions

The rules still open are listed in [open-questions.md](open-questions.md). Until one is decided, the value or algorithm it covers stays isolated and configurable in the engine and must not be silently guessed.
