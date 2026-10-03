# Nether Earth Clone — Deviations from the ZX Spectrum Original

These are the places where the clone deliberately behaves differently from the ZX Spectrum game. Each one is an owner decision, not an open question and not a bug: a later fidelity pass that finds one of these differences should not "fix" it.

Behaviour that has no Spectrum counterpart at all (the lobby, network play, reconnect rules, the second commander and its start position, commander-versus-commander collision) is a PvP adaptation, not a deviation, and is not listed here. Its rules are in [functional-spec.md](functional-spec.md); the reasons are in [resolved-questions.md](resolved-questions.md).

Labels refer to `netherearth-annotated.asm` in `santiontanon/netherearth-disassembly`. AI labels and section numbers refer to [docs/cr004/spectrum-ai-notes.md](../docs/cr004/spectrum-ai-notes.md).

## Commander

### Descent speed

- **Spectrum:** gravity is −1 per game cycle (`Lafc3_gravity`) against +2 for ascent (`Lafb5_elevate`): 4.8 s from 0 to 48, 9.6 s back down.
- **Clone:** −2 per vertical update, so descent takes 4.8 s like ascent. Gravity still stops exactly on the surface under the ship.
- **Why:** playtesting found the descent too slow.
- **Decided:** 2026-09-22 (CR003.1, #216).

### Exit lift not shortened by movement

- **Spectrum:** holding up/down while the 5-update exit lift runs decrements the timer (`Laf11`), shortening the lift.
- **Clone:** the lift always runs its 5 updates.
- **Why:** owner choice; the quirk adds nothing to play.
- **Decided:** 2026-09-21.

## Construction

### Construction does not pause the match

- **Spectrum:** the whole game pauses while the construction screen is open (single-player).
- **Clone:** only the building player's commander is frozen; the match, the opponent and all robots keep running.
- **Why:** pausing a two-player match for one player's menu is not playable.
- **Decided:** 2026-09-21 (CR002.13, #180).

### Commander in the doorway blocks a launch

- **Spectrum:** `La6c8` tests only robot marks at the exit, so a robot could be built behind a ship standing in the doorway, and neither could leave.
- **Clone:** a free commander overlapping the exit body below the new robot's top refuses the launch (`EXIT_BLOCKED`), for players and the AI.
- **Why:** the original trapped both units.
- **Decided:** 2026-09-27 (CR005.1).

## Movement and navigation

### Dumb-robot momentum instead of the keep-walking counter

- **Spectrum:** a robot without electronics that cannot step toward its target picks a random possible direction and keeps walking that way for `rand & 3 + 3` = 3–6 game cycles (`Lb1f5`, `ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING`).
- **Clone:** no counter is stored. The robot prefers the direction it already faces after the primary step, and the perpendicular detour order is drawn once per 16-tick window.
- **Why:** a faithful port of the counter reached the goal in 9/32 obstacle runs; momentum reached 32/32 and stays stateless and replay-safe.
- **Decided:** detour fallback 2026-09-25; momentum 2026-09-27 (owner report of looping robots).

### Stop & Defend does not turn toward enemies

- **Spectrum:** a robot on Stop & Defend turns toward an enemy in sight (`Lb1d7`).
- **Clone:** a Stop & Defend robot turns only to fire at a target already in weapon range; it does not otherwise track enemies.
- **Why:** the navigation lock keeps the original's local routing qualitatively; its exact quirks are research detail, not product rules.
- **Decided:** recorded as kept 2026-09-21 (CR002); no separate owner decision.

## Orders and capture

### Neutral structures take the full capture time

- **Spectrum:** a neutral factory goes to the first robot that reaches it.
- **Clone:** neutral factories and war bases take the same 1,440 ticks of continuous occupation as enemy ones.
- **Why:** owner choice for pacing.
- **Decided:** 2026-09-23.

### Search & Capture re-selects its target every evaluation

- **Spectrum:** `Lb289` keeps the stored target until that target's ownership stops matching the order.
- **Clone:** every evaluation re-selects the nearest matching structure, so a structure that changes hands nearer the robot pulls it in; a robot already on its target's capture cell keeps that target.
- **Why:** on the 512-cell map the original made robots walk hundreds of cells past valid targets (observed in a live replay).
- **Decided:** 2026-09-23.

### Search & Capture war-base target includes neutral war bases

- **Spectrum:** `Lb3d5_prepare_robot_order_building_target_search` matches one exact ownership value, so the war-base target takes enemy-owned war bases only.
- **Clone:** it takes any war base not already the ordering player's.
- **Why:** there is no separate neutral-war-base order, and the two interior war bases start neutral.
- **Decided:** 2026-09-25.

### A robot on a capture cell faces out of the structure

- **Spectrum:** `Ladb7_building_loop` never changes the robot's direction, so a robot stays facing the wall it walked into.
- **Clone:** it turns to face away from the structure's body (`capture.outward_facing`).
- **Why:** shots travel in the facing, so a robot facing a wall cannot defend the capture.
- **Decided:** 2026-09-24.

## Combat

### Bullet scan: only robots, and only robots tall enough

- **Spectrum:** a bullet hits the first robot mark in its 3×3 scan with no height test, and a ship or bullet mark there ends the bullet with no damage.
- **Clone:** only robots stop a bullet, and only when the robot's top (terrain altitude + stack height) is at least the bullet altitude (10). Commanders and other projectiles never stop a bullet.
- **Why:** owner kept the engine's altitude gate; with the Spectrum piece heights every robot is at least 13 tall, so in practice every robot is hit.
- **Decided:** 2026-09-21; gate on the robot top 2026-09-22.

### One bullet channel per robot

- **Spectrum:** all of a side's autonomous robots share two bullet slots, plus one slot for the combat-mode robot (`Lb6b8_find_new_bullet_ptr`).
- **Clone:** every robot has its own channel and can have one projectile in flight.
- **Why:** owner choice; shared slots make one side's fire depend on unrelated robots.
- **Decided:** 2026-09-21.

### Robot update phase while standing still

- **Spectrum:** a robot acts only when its per-robot cycle counter reaches 0 (`Lb154_robot_ai_update`).
- **Clone:** a stationary robot that has not fired since it last moved counts as being at an update on every tick, so its first shot can come up to one period earlier. After a shot or a move its updates follow the period exactly.
- **Why:** the engine tracks no update phase for an idle robot.
- **Decided:** 2026-09-21 (CR002.19, #197).

### Debris variant

- **Spectrum:** debris is element type 6 or 7, chosen at random.
- **Clone:** no random draw; both types behave the same in play and the renderer uses one sprite.
- **Why:** the choice has no gameplay effect and would consume RNG.
- **Decided:** 2026-09-21 (CR002.18, #196).

## Presentation

### Radar shows only the viewer's commander, in white

- **Spectrum:** single-player, so the radar shows the one ship; marks flicker cyan/yellow on blue.
- **Clone:** the radar shows every robot and only the viewer's own commander, never the opponent's, white on black.
- **Why:** keeps the original's information model in a two-player match; colour simplified.
- **Decided:** 2026-09-21.

### Map-end fence post centred on its footprint

- **Spectrum:** the fence post is drawn on the footprint's −x column and 4 px further left (odd-parity shift), leaving a gap at the left map end and none at the right.
- **Clone:** the post is drawn centred on its 2×2 footprint. Data, collision and heights are unchanged.
- **Why:** playtest report that the two walls looked different.
- **Decided:** 2026-09-22 (CR003.7, #222).

### Exaggerated ground lift and per-cell shadows

- **Spectrum:** terrain is drawn flat; a robot is raised by its terrain altitude in pixels (`Lcee8_draw_robot_to_buffer`); the ship's shadow is one sprite.
- **Clone:** robots, the commander and projectiles use the decoded Spectrum sprites (procedural prisms only as a fallback), but ground heights under them are drawn 3× higher (`GROUND_LIFT`) and the commander's shadow is cut per cell onto each cell's own surface. Engine altitudes are unchanged.
- **Why:** owner request: the original lift is too small to notice at the clone's zoom.
- **Decided:** sprites 2026-09-22/2026-09-23; ground lift and shadows 2026-09-27 (CR005.2, CR005.4).

### Audio noise uses a seeded generator

- **Spectrum:** the noise routines read ROM bytes as their random source.
- **Clone:** the decoded sound routines use a seeded PRNG instead. Pitches and durations are otherwise derived from the original T-state timing.
- **Why:** the ROM is not shipped.
- **Decided:** 2026-09-25 (#272).

## AI opponent

### The Spectrum enemy AI is a reference, not a contract

- **Spectrum:** a shallow reactive loop.
- **Clone:** an opponent intended to play better than the original; the disassembly is used only where it usefully explains the original.
- **Why:** owner scope decision.
- **Decided:** 2026-09-25 (CR004, #282).

### The AI seat has no commander

- **Spectrum:** the enemy has no ship the player meets either, but the human pays a positioning cost to build.
- **Clone:** the AI orders robots and builds without landing on a heli-pad or occupying any cell. It cannot capture on foot, drive a robot directly or be ejected.
- **Why:** deliberate asymmetry; a build with no commander present is not a missing rule.
- **Decided:** 2026-09-25 (CR004, #282).

### Every decision evaluates the whole state

- **Spectrum:** one of 32 slots is drawn per game cycle and the AI idles a quarter of the time (`Lb7f4_update_enemy_ai`, notes §1).
- **Clone:** the planner runs every game cycle (4 ticks) and considers every owned war base and robot; it never idles by construction.
- **Why:** strength and determinism.
- **Decided:** 2026-09-25 (CR004, #282/#284).

### Affordable, purposeful designs

- **Spectrum:** a coin flip per war base and a random design byte, discarded when not legal or not affordable (`Lb81b_pick_random_warbase_loop`, notes §2); a weapon floor that grows by one per 8 robots (`Lb505`) and a cap of half the general pool (`Lb8c6`).
- **Clone:** the highest-value legal design the pool can pay for, opened only when the whole design is affordable; the weapon floor grows by one per 4 robots and a defence reserve replaces the half-pool cap; both are released when a war base is threatened.
- **Why:** no stalled or abandoned builds.
- **Decided:** 2026-09-25 (CR004, #282/#285).

### Value-ranked targets and a threat response

- **Spectrum:** targets are nearest-by-x only and nothing reacts to an approaching enemy (`Lb34d_find_capture_or_destroy_target`, notes §3–4).
- **Clone:** capture and destroy targets are ranked by production value, distance and contest; an enemy robot closing on an owned war base or factory draws a defender. Exclusive targets per order type (`Lb36c`) and reading only what a player's own client renders are kept as in the original.
- **Why:** strength.
- **Decided:** 2026-09-25 (CR004, #282/#286).

### The AI targets neutral interior war bases

- **Spectrum:** `Lb3d5`'s flags exclude the neutral interior war bases from the enemy's capture candidates (notes §3).
- **Clone:** the AI's war-base capture type includes them.
- **Why:** they decide victory.
- **Decided:** 2026-09-25 (CR004, #282/#286).

### A new AI robot gets a real order at once

- **Spectrum:** a new robot starts on Stop & Defend and its order is re-evaluated with probability 1/32 per cycle (`Lb920_enemy_ai_single_robot_control`, notes §5, §8), about 6.4 s of idling.
- **Clone:** the order planner assigns a productive order in the same decision the robot launches and re-plans only on events (target gone, new threat, threat ended).
- **Why:** strength.
- **Decided:** 2026-09-25 (CR004, #282/#285/#286).

### Composition response without an armour stat

- **Spectrum:** the enemy AI does not reason about matchups at all (notes §6).
- **Clone:** `ai/robot_orders.py`'s `matchup` ranks a pairing on reach, then per-hit damage (which folds in both heights). This is new design with no Spectrum evidence behind it.
- **Why:** strength; the engine has no armour value to key on.
- **Decided:** 2026-09-25 (CR004, #282/#286).
