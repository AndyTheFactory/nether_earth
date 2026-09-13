# Nether Earth Clone — Open Questions

This file tracks gameplay/implementation details that are still unresolved after the current design and ZX Spectrum disassembly review. Resolved items remain listed so their locked outcome is easy to find.

## 1. Exact normal-weapon stack order — RESOLVED

Locked bottom-to-top order:

1. chassis
2. cannon, if fitted
3. missile, if fitted
4. phaser, if fitted
5. nuke, if fitted
6. electronics, if fitted
7. commander, when docked

Missing components are simply omitted; relative order is preserved.

## 2. PvP treatment of the remaining war bases — RESOLVED

- Original four-war-base map retained.
- Player 1 owns the extreme-left war base.
- Player 2 owns the extreme-right war base.
- Two interior war bases start neutral and capturable.
- Starting ownership belongs to scenario overlay data, not raw map geometry.
- Victory: opponent owns zero war bases.

## 3. Miles-to-grid-cell conversion — RESOLVED

- 1 mile = 2 map tiles/cells.
- 1 tile = 0.5 miles.
- Cannon 10 miles = 20 tiles.
- Missile 14 miles = 28 tiles.
- Phaser 10 miles = 20 tiles.
- Electronics +3 miles = +6 tiles.
- Nuclear radius 8 miles = 16 tiles.
- Advance/Retreat 0–50 miles = 0–100 tiles.

The conversion must exist once in shared game-rule/helper code.

## 4. Exact movement speeds and terrain penalties — PARTIALLY RESOLVED

Locked:

- bipod is slowest, tracks faster, anti-grav fastest on ordinary terrain;
- bipod crosses rough terrain with severe slowdown;
- tracks cross rough terrain with a smaller slowdown;
- bipod and tracks cannot cross ditches/ravines;
- anti-grav can traverse every terrain type including ditches/ravines;
- authoritative movement timing is integer ticks-per-tile at 20 Hz;
- values live in centralized game-rule configuration.

Still open:

- exact default ticks per tile for each chassis;
- exact bipod rough-terrain penalty;
- exact tracked rough-terrain penalty;
- whether anti-grav speed is identical on every traversable terrain type.

## 5. Dumb vs electronic navigation — RESOLVED

- Non-electronic robots use deliberately limited/original-style local routing and may become blocked even if a longer route exists.
- Electronic robots use proper deterministic pathfinding/replanning around obstacles.
- Electronics improves routing only; it does not change chassis terrain permissions.
- Navigation strategies must be isolated behind an engine policy/interface.

Exact historical quirks of the dumb algorithm remain research detail, not a product decision.

## 6. War-base capture mechanics — RESOLVED

- Enemy robots can capture war bases.
- War-base capture uses the same continuous-occupation rule as factory capture by default.
- Default duration: 12 in-game hours = 1,440 simulation ticks = 72 real seconds at 20 Hz.
- Ownership changes immediately when the duration completes.
- Victory is evaluated in the same authoritative simulation step.
- Capture duration is configurable game-rule/scenario data.

## 7. Capture interruption semantics — RESOLVED

If qualifying occupation breaks before capture completes, progress resets immediately to zero. Partial progress is not retained.

## 8. Exact projectile mechanics — PARTIALLY RESOLVED

Locked:

- a robot cannot fire another normal weapon while its current normal projectile is active;
- cannon, missile, and phaser projectile flight altitude defaults to the Spectrum value 10;
- projectile altitude is independent of robot height;
- projectile gameplay is authoritative world/game logic, not browser viewport logic.

Still open:

- exact projectile speed/cadence;
- collision footprint/profile;
- exact component/height collision semantics;
- interaction with buildings/static objects;
- exact lifecycle termination rules from the Spectrum implementation.

## 9. Damage, accuracy, and electronics effects — PARTIALLY RESOLVED

Locked normal-weapon damage:

`base_damage = (60 - (robot_height + ground_height)) / 4`

Default multipliers:

- cannon = 2
- missile = 3
- phaser = 4

The damage formula is isolated behind one engine function; multipliers are centralized game-rule configuration.

Still open:

- exact integer truncation/rounding path;
- exact hit-probability/accuracy formula;
- exact range effect on accuracy;
- exact strength representation/reduction;
- whether individual components can be damaged separately;
- exact electronics resistance modifier;
- exact electronics accuracy/range behavior.

## 10. Resource spending rules — RESOLVED

Preserve the original Spectrum construction economy, with all tunable numeric values in centralized game configuration.

Canonical defaults from the disassembly:

- starting general resources: 20
- bipod cost: 3
- tracks cost: 5
- anti-grav cost: 10
- cannon cost: 2
- missile cost: 4
- phaser cost: 4
- nuclear cost: 20
- electronics cost: 3

Resource categories:

- general
- chassis
- electronics
- cannon
- missile
- phaser
- nuclear

Spending behavior:

- spend the relevant type-specific resource pool first;
- general resources pay only the shortfall;
- reject the selection if specific + general resources cannot cover the cost;
- construction editing uses a temporary resource buffer;
- deselecting a component reverses the original mixed specific/general spending semantics;
- actual player resources are committed atomically only when `Start Robot` succeeds;
- leaving/canceling unlaunched construction consumes no permanent resources.

## 11. Simultaneous destination-cell claims — RESOLVED

- A destination cell is reserved when a robot move is accepted/started.
- A reserved destination is unavailable to other robots until completion/cancellation.
- Same-tick contention is resolved randomly using the match-local seeded deterministic RNG.
- Two contenders are a 50/50 coin flip; more contenders are chosen uniformly.
- Losing contenders remain out of the destination and may retry/replan.

This is random to players but replay-safe for identical seed + commands + state.

## 12. Commander-versus-commander collision — RESOLVED

- Commanders physically collide with each other.
- They may share X/Y only if their vertical collision ranges do not overlap.
- If vertical ranges overlap, they block horizontal and vertical movement, including descent.
- Commanders remain indestructible, untargetable, and immune to damage.

## 13. Commander vertical limits and speed — RESOLVED

Spectrum-compatible defaults at the locked 20 Hz engine rate:

- minimum altitude: 0
- maximum altitude: 48
- vertical update cadence: every 4 simulation ticks (5 updates/sec)
- ascent step: +2
- descent/gravity step: -1

Ascent/descent are intentionally asymmetric. Approximate unobstructed times are 4.8 s from 0→48 and 9.6 s from 48→0.

Configuration keys/defaults:

- `commander_min_altitude = 0`
- `commander_max_altitude = 48`
- `commander_vertical_update_ticks = 4`
- `commander_ascent_step = 2`
- `commander_descent_step = 1`

Horizontal and vertical movement may occur simultaneously. Automatic elevation after exiting a robot/war base uses the same +2 elevation semantics.

## 14. Landing on an enemy robot — RESOLVED

- Enemy robots are physical collision surfaces for the commander.
- Descending stops at the top of the enemy robot stack.
- The commander may rest there while collision geometry permits it.
- No docking, control transfer, or contact damage occurs.
- Docking/control remains restricted to friendly robots.

## 15. Static-object composition and footprints — RESOLVED

- Static geometry is explicit occupied map cells/components, not one universal building rectangle.
- War bases and factories are separate semantic entity types with their own canonical compositions.
- War-base metadata includes heli-pad, exit, capture zone, ownership/resource behavior.
- Factory metadata includes production type and capture zone.
- Physical height may vary by component/cell.
- Interaction zones are explicit semantic metadata.
- Exact original layouts are reconstructed from Spectrum evidence during map ingestion; uncertain cells/heights must not be guessed.
- Robot footprint is a separate robot-model concern.

## 16. Disconnect and reconnect rules — RESOLVED

- Match pauses immediately when either player disconnects.
- Simulation ticks and gameplay timers stop while paused.
- Default reconnect grace period: 60 seconds, configurable at match/server runtime level.
- Reconnect receives the current authoritative snapshot.
- Match resumes only when both players are connected.
- Grace expiry causes the disconnected player to forfeit when an opponent remains eligible to win.
- If both are disconnected, each has an independent grace deadline; if both expire without either returning, end as abandoned/no-contest rather than inventing a gameplay winner.
- No manual pause in v1.
- Reconnect/deadline state belongs to the runtime layer and must not mutate deterministic engine state while paused.

## Remaining research

Only three substantive fidelity areas remain:

1. **Movement timing** — exact Spectrum chassis ticks-per-tile and rough-terrain penalties (#4).
2. **Projectile mechanics** — exact speed/cadence/collision/lifetime behavior (#8).
3. **Combat detail** — exact accuracy, rounding, strength, and electronics modifiers (#9).

## Resolution process

Use this fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

When one of the remaining items is verified, update the functional/technical specs and this file in the same change.