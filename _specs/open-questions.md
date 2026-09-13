# Nether Earth Clone — Open Questions

This file tracks gameplay and implementation details that are **not yet locked**. It should be resolved before the functional specification is considered fully implementation-ready.

## 1. Exact normal-weapon stack order — RESOLVED

Locked bottom-to-top weapon order:

1. cannon
2. missile
3. phaser
4. nuke

Rules:

- Weapon placement is canonical and not player-defined.
- If a lower-order weapon is absent, remaining fitted weapons keep their relative order.
- The nuke, when fitted, is always the topmost weapon.
- Electronics, when fitted, is always above all weapons.
- The canonical stack must be defined once in engine code and reused for rendering, robot height, collision height, projectile interaction, commander docking height, and construction preview.

## 2. PvP treatment of the remaining war bases — RESOLVED

Locked v1 PvP scenario:

- The original four-war-base map is retained.
- Player 1 starts owning the **extreme-left war base**.
- Player 2 starts owning the **extreme-right war base**.
- The two war bases between them start **neutral**.
- Neutral war bases are capturable using the normal war-base capture rules.
- Starting ownership is scenario-overlay data; the underlying map geometry remains unchanged.
- Victory remains: a player loses when they own zero war bases.

This gives both players symmetric starting positions at opposite ends of the battlefield while keeping all four original war bases active in the scenario.

## 3. Miles-to-grid-cell conversion — RESOLVED

Locked conversion:

- **1 mile = 2 map tiles/cells**.
- Equivalently, **1 tile = 0.5 miles**.
- A robot is two tiles wide, so one robot width corresponds to one mile.

Derived defaults:

- cannon range 10 miles = **20 tiles**
- missile range 14 miles = **28 tiles**
- phaser range 10 miles = **20 tiles**
- electronics +3 miles = **+6 tiles**
- nuclear effect radius 8 miles = **16 tiles**
- Advance/Retreat 0–50 miles = **0–100 tiles**

This conversion must be represented in one shared game-rule/helper layer and reused consistently by movement/orders, combat, nuclear effects, UI, and replays rather than duplicating conversion arithmetic across systems.

## 4. Exact movement speeds and terrain penalties — PARTIALLY RESOLVED

Locked behavior:

- Relative chassis speed on ordinary traversable terrain is **bipod slowest, tracks faster, anti-grav fastest**.
- Bipod can traverse rough terrain but with a severe slowdown.
- Tracks can traverse rough terrain with a smaller slowdown than bipod.
- Bipod and tracks cannot cross ditches/ravines.
- Anti-grav can traverse all terrain, including ditches/ravines.
- Exact movement timing is represented as integer simulation ticks per tile/cell at the locked 20 Hz engine rate.
- Exact ticks-per-tile values and terrain multipliers/penalties must be isolated in configuration/game-rule constants so they can be tuned without changing movement logic.
- Until the exact Spectrum timing is established, implementation should preserve only the locked relative ordering/capabilities and use clearly marked configurable defaults.

Still open:

- Exact default ticks per tile for bipod, tracks, and anti-grav.
- Exact bipod rough-terrain penalty.
- Exact tracked rough-terrain penalty.
- Whether anti-grav uses exactly the same speed on every traversable terrain type.

## 5. Exact dumb vs electronic navigation behavior — RESOLVED

Locked behavior:

- Robots **without electronics** deliberately use limited/original-style local navigation rather than a globally optimal pathfinder.
- Non-electronic robots may fail to route around obstacles and may become blocked or stuck even when a longer valid path exists.
- Robots **with electronics** use a proper pathfinding/replanning layer and should actively route around obstacles when a valid chassis-compatible path exists.
- Electronics improves route selection and obstacle handling, not the robot's underlying terrain capability.
- A bipod or tracked robot with electronics still cannot traverse terrain forbidden to that chassis.
- Replanning/pathfinding behavior must remain deterministic and server-authoritative.
- Navigation strategy should be isolated behind a clear engine interface so original/dumb and electronic/smart routing are separate policies rather than scattered conditionals.

Implementation detail remains open only where needed to reproduce the exact original local-routing quirks; this is no longer a product decision.

## 6. War-base capture mechanics — RESOLVED

Locked behavior:

- War bases are capturable by qualifying enemy robots.
- War-base capture uses the same continuous-occupation rule as factory capture by default.
- Default capture duration is **12 in-game hours = 1,440 simulation ticks = 72 real seconds** at 20 Hz.
- Capture progress resets to zero if qualifying occupation is interrupted.
- Ownership transfers immediately when the configured capture duration completes.
- Victory is evaluated in that same authoritative simulation step after the ownership change.
- The capture duration must be easy to configure as game-rule/scenario data rather than being hard-coded separately into war-base logic.

The default preserves the original building-capture behavior while allowing scenario-specific tuning without changing engine code.

## 7. Capture interruption semantics — RESOLVED

Locked behavior:

- Capture requires continuous qualifying occupation.
- If the qualifying robot stops occupying the capture location before capture completes, capture progress resets immediately to zero.
- Partial capture progress is not preserved across an interruption.

This matches the original ZX Spectrum building-capture timer path, which clears the building timer when the qualifying occupation condition is no longer satisfied.

## 8. Exact projectile mechanics — PARTIALLY RESOLVED

Locked behavior:

- The normal-weapon firing gate remains: a robot cannot fire another normal weapon while its current projectile is active.
- Cannon, missiles, and phasers all use the original ZX Spectrum projectile flight altitude of **10** authoritative altitude units.
- Flight altitude does not vary by normal weapon type and is independent of robot height.
- This original value should be preserved as the default authoritative rule. It may be exposed as one clear engine constant/configurable rule value, but gameplay should use the original value by default.

Still open:

- Projectile speed for cannon, missiles, and phasers.
- Projectile movement granularity in simulation ticks.
- Collision footprint/profile.
- Interaction with robot/component height.
- Interaction with buildings/boxes/static objects.
- What exactly terminates a projectile in the Spectrum game.
- How the original notion of a projectile leaving the visible screen should translate to a browser client whose viewport may differ from the original Spectrum view.

Projectile lifetime must ultimately be defined by authoritative world/game rules, not browser viewport size.

## 9. Damage, accuracy, and resistance formulas — PARTIALLY RESOLVED

Locked damage behavior:

- Preserve the original ZX Spectrum normal-weapon damage formula.
- Compute base damage as:

  `base_damage = (60 - (robot_height + ground_height)) / 4`

- Apply a weapon-specific multiplier:
  - cannon: **2**
  - missiles: **3**
  - phaser: **4**
- The damage calculation must be isolated behind one clearly named engine function so the formula can be changed without touching firing/projectile code.
- Weapon multipliers must live in configuration/game-rule data rather than being hard-coded inside the damage function.
- The original values above are the default configuration.
- Integer rounding/truncation must reproduce verified ZX Spectrum behavior once the exact arithmetic path is implemented.

Still open:

- Exact hit-probability or accuracy formula.
- How range affects hit probability.
- Exact integer rounding/truncation semantics of the base-damage expression if not already evident from the implementation trace.
- How robot strength is reduced/represented after damage.
- Whether individual components can be damaged/destroyed separately.
- Exact electronics damage-resistance modifier.
- Whether electronics changes accuracy, maximum range, or both.

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

## 11. Simultaneous destination-cell claims — RESOLVED

Locked behavior:

- A robot reserves its destination cell when a move is accepted/started.
- While reserved, that destination is unavailable to other robots until the move completes or the reservation is released/cancelled.
- If two or more robots contend for the same destination in the same authoritative simulation tick, the winner is chosen by a **50/50 coin flip** for a two-robot claim.
- Random contention resolution must use the match-local seeded deterministic RNG owned by the engine, never wall-clock/process randomness.
- Therefore the outcome is random from the players' perspective but fully replay-safe: identical seed + commands + state produce the same winner.
- For more than two simultaneous claimants, use the same seeded RNG to choose uniformly among the valid contenders.
- Losing contenders do not enter the cell and may retry/replan according to their control/navigation policy.

## 12. Commander-versus-commander collision — RESOLVED

Locked behavior:

- Commanders are physical/collidable with each other.
- Opposing commanders may share the same X/Y only when their vertical collision ranges do **not** overlap.
- If their vertical ranges overlap, they block each other's horizontal and vertical movement.
- A commander therefore cannot pass through another commander at an overlapping height.
- A commander may prevent the other from descending through its occupied vertical range.
- If vertically separated with no overlap, both may occupy the same X/Y.
- Commanders remain indestructible, untargetable, and immune to damage; this rule affects collision only.

## 13. Commander vertical limits and speed — PARTIALLY RESOLVED

Locked behavior:

- Authoritative altitude uses the original ZX Spectrum integer altitude units directly.
- Minimum altitude is **0**.
- Default maximum altitude is **48**.
- The maximum altitude is an engine constant/configurable game-rule value, not a magic number spread through movement code.
- Rendering may interpolate between authoritative altitude values, but simulation altitude remains integer/discrete.
- Horizontal and vertical movement may happen simultaneously.
- Holding Space raises the commander and releasing Space causes descent.
- Original ascent behavior changes altitude by **2 units per elevation update**; this should be preserved as the default behavior unless later timing analysis shows a different authoritative update cadence.

Still open:

- Exact simulation-tick cadence of ascent/descent updates at the locked 20 Hz engine rate.
- Whether descent uses exactly the same cadence/step as ascent.
- Whether there are any special local ceiling/altitude rules around map objects beyond normal collision clearance.

## 14. Landing on an enemy robot — RESOLVED

Locked behavior:

- Enemy robots are treated as physical collision surfaces for the commander.
- When the commander descends onto an enemy robot, descent stops when the commander contacts the top of the robot's physical stack.
- The commander may remain/rest on top of the enemy robot while collision geometry continues to permit it.
- No docking occurs.
- No control transfer occurs.
- No damage is dealt to either the commander or the robot as a consequence of this contact.
- The commander can leave by rising or moving away when normal collision rules permit.
- Docking/control remains restricted to friendly robots only.

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
