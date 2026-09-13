# Nether Earth Clone — Open Questions

This file tracks gameplay and implementation details that are **not yet locked**. It should be resolved before the functional specification is considered fully implementation-ready.

## 1. Exact normal-weapon stack order

We know that weapon placement is canonical and not player-defined, that the nuke is always the topmost weapon, and that electronics is always above the weapons.

What is still unresolved is the exact bottom-to-top order of:

- cannon
- missiles
- phaser

The conversation contained contradictory wording around this order, so this must be verified directly against ZX Spectrum behavior/disassembly before implementation.

## 2. PvP treatment of the remaining war bases

The original map contains four named war bases, while the v1 PvP scenario currently locks in:

- Player 1 starts with one war base
- Player 2 starts with one war base
- victory occurs when the opponent owns zero war bases

Open question:

- What happens to the two remaining war bases on the original four-base map?
  - neutral and capturable?
  - removed/disabled for the PvP scenario?
  - allocated to one of the players?
  - handled by a dedicated PvP map variant?

## 3. Miles-to-grid-cell conversion

Several rules are expressed in miles:

- cannon range: 10 miles
- missile range: 14 miles
- phaser range: 10 miles
- electronics: nominal +3 miles effective range/accuracy
- nuclear effect radius: 8 miles
- Advance/Retreat orders: 0–50 miles

Open question:

- What is the authoritative conversion between miles and grid cells on the ZX Spectrum map?

This conversion should be represented as game-rule data and used consistently by movement, combat, nuclear effects, orders, UI, and replays.

## 4. Exact movement speeds and terrain penalties

The relative chassis behavior is locked:

- bipod: normal terrain; rough terrain with poor/slow performance; no ditch/ravine crossing
- tracks: normal terrain; better rough-terrain handling; no ditch/ravine crossing
- anti-grav: traverses all terrain and can cross ditches/ravines

Open questions:

- How many simulation ticks per cell for each chassis on normal terrain?
- What is the bipod rough-terrain penalty?
- What is the tracked rough-terrain penalty?
- Does anti-grav have the same speed on all terrain?

All movement durations should resolve to integer tick counts at the locked 20 Hz simulation rate.

## 5. Exact dumb vs electronic navigation behavior

The distinction is locked conceptually:

- non-electronic robots use poorer/original-style autonomous navigation and combat decisions
- electronic robots use smarter routing, obstacle handling, and engagement decisions

Open questions:

- What exact algorithm reproduces the original non-electronic routing behavior?
- What situations can make a dumb robot become stuck?
- How much better should electronic routing be?
- Does electronics change only path selection, or also replanning frequency and target selection?
- Which autonomous behaviors are explicitly visible in the ZX Spectrum version and which would be modern interpretation?

## 6. War-base capture mechanics

Factory capture is locked at 12 continuous in-game hours.

Open questions:

- Can war bases be captured in exactly the same way as factories?
- If yes, is the required occupation time also 12 in-game hours?
- Are there extra prerequisites for capturing a war base?
- Does capture immediately transfer ownership and therefore affect victory in the same tick?

## 7. Capture interruption semantics

The current specs assume that enemy-factory capture requires continuous occupation and that progress resets if occupation is interrupted.

Open question:

- Does the ZX Spectrum game fully reset capture progress when occupation is broken, or is partial progress preserved?

This should be verified rather than inferred.

## 8. Exact projectile mechanics

The normal-weapon firing gate is locked: a robot cannot fire another normal weapon while its current projectile is active.

Open questions:

- Projectile speed for cannon, missiles, and phasers
- Projectile movement granularity in simulation ticks
- Exact projectile flight height
- Whether flight height differs by weapon
- Collision footprint/profile
- Interaction with robot/component height
- Interaction with buildings/boxes/static objects
- What exactly terminates a projectile in the Spectrum game
- How the original notion of a projectile leaving the visible screen should translate to a browser client whose viewport may differ from the original Spectrum view

Projectile lifetime must ultimately be defined by authoritative world/game rules, not browser viewport size.

## 9. Damage, accuracy, and resistance formulas

Known data includes weapon lethality values and the fact that electronics improves weapon accuracy/effective range and gives slightly increased resistance to damage.

Open questions:

- Exact hit-probability or accuracy formula
- How range affects hit probability
- How weapon lethality is converted to damage
- How robot strength is reduced
- Whether individual components can be damaged/destroyed separately
- Exact electronics damage-resistance modifier
- Whether electronics changes accuracy, maximum range, or both

## 10. Resource spending rules

Locked:

- general resources exist
- type-specific resources exist
- factories produce type-specific resources
- war bases produce general resources
- weapon costs are known

Open questions:

- Exact costs for chassis modules
- Exact cost for electronics
- How general resources substitute for type-specific resources
- Whether spending prefers type-specific resources first or general resources first
- Whether a module requires both general and specific resources or either/or
- What happens to spent/selected resources when construction is scrapped

## 11. Simultaneous destination-cell claims

Movement can span multiple ticks while authoritative positions remain grid based.

Open question:

If two robots begin transitions toward the same currently-empty destination cell, what is the deterministic rule?

Possible models include:

- reserve the destination when movement begins
- resolve competing claims by tick/order/player/entity ID
- allow both to move until completion and resolve at the final tick

This must be deterministic and replay-safe.

## 12. Commander-versus-commander collision

Commander collision with robots and static objects is locked.

Open questions:

- Can two opposing commanders occupy the same X/Y coordinate?
- If they are at overlapping Z ranges, do they block each other?
- Can they pass through each other?
- Can one commander prevent the other from descending or moving?

Commanders remain indestructible and untargetable regardless of the answer.

## 13. Commander vertical limits and speed

Locked:

- authoritative Z is integer/discrete
- rendering interpolates between Z levels
- horizontal and vertical movement can happen simultaneously
- holding Space raises the commander and releasing it causes descent

Open questions:

- Minimum Z
- Maximum Z
- Number of simulation ticks per Z-level transition
- Whether ascent and descent use the same speed
- Whether there is any special ceiling or altitude rule near map objects

## 14. Landing on an enemy robot

Friendly-robot docking is locked and automatic when descending onto the robot.

Open question:

- What happens if the commander descends onto an enemy robot?

Likely possibilities:

- physical collision only; descent stops at the robot top
- commander may rest above it without docking
- some other original behavior

Docking/control must remain restricted to friendly robots unless verified otherwise.

## 15. Static-object composition and footprints — RESOLVED

Locked model:

- Static geometry is represented as explicit occupied map cells/components rather than by one universal building rectangle.
- War bases and factories are distinct semantic world entities with their own canonical composition definitions.
- A war base is composed from explicit physical components/cells; semantic metadata such as heli-pad, exit, capture zone, ownership, and resource behavior belongs to the war-base entity.
- A factory is composed separately from explicit physical components/cells; semantic metadata such as production type and capture zone belongs to the factory entity.
- Generic scenery/blockers use evidence-backed explicit occupied cells/components.
- Physical height may vary by component/cell; the model must not require one uniform height for an entire war base or factory.
- Interaction zones are semantic metadata and are not inferred from a generic footprint.
- Exact original-map war-base and factory compositions must be reconstructed from ZX Spectrum evidence during M2 map ingestion; uncertain cells/heights must remain explicitly unresolved rather than guessed.

Robot footprint is a separate robot-model concern and must not be inferred from the static-structure representation.

## 16. Disconnect and reconnect rules

The technical architecture supports reconnect snapshots, but gameplay policy is not locked.

Open questions:

- Does the match continue when one player disconnects?
- Does it pause?
- How long is the reconnect grace period?
- When is a disconnected player considered to have abandoned the match?
- Does abandonment immediately award victory?
- Is there an explicit surrender action?
- What happens if both players disconnect?

These rules should be simple for v1 and should not require persistent accounts or database state.

## Resolution process

When resolving an open question, use the project fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

Once a question is resolved, update the functional and/or technical specification and remove or mark the corresponding item as resolved here.
