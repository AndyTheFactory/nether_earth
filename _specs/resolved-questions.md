# Nether Earth Clone — Resolved Questions

This file records every gameplay or implementation question that was once open and is now decided. Each entry gives the decision as locked, where the decision came from, one evidence pointer, and the specification section that now carries the rule. The specifications state the rules; this file explains why they are what they are.

Evidence labels refer to `netherearth-annotated.asm` in `santiontanon/netherearth-disassembly` (commit `762e33e` unless noted). Intentional differences from the ZX Spectrum are listed separately in [deviations-from-original.md](deviations-from-original.md). Questions that are still open are in [open-questions.md](open-questions.md).

Abbreviations: FS = [functional-spec.md](functional-spec.md), TS = [technical-spec.md](technical-spec.md), CRnnn = change request, #n = GitHub issue.

## World and map

### PvP treatment of the remaining war bases

- **Decision:** the original four-war-base map is kept. Player 1 owns the extreme-left war base, Player 2 the extreme-right one, and the two interior war bases start neutral and capturable. Starting ownership is scenario-overlay data, not map geometry. A player loses when they own zero war bases.
- **Source:** initial specification lock (PvP adaptation of a single-player original).
- **Evidence:** the Spectrum map has four war bases (`data/maps/zx-spectrum-original.yaml`); ownership comes from `map_overlay.default_pvp_overlay`.
- **Rule:** [FS §4](functional-spec.md#4-pvp-scenario-and-victory).

### Miles-to-grid-cell conversion

- **Decision:** 1 mile = 2 map cells; one coordinate unit is one map cell on both axes. The conversion exists once (`rules.miles_to_cells`) and is used for Advance/Retreat (0–50 miles = 0–100 cells). Weapon ranges and the nuclear blast are defined directly in cells from the code, not converted from miles.
- **Source:** initial lock; weapon/nuke lines replaced by CR001 (owner decision 2026-09-21).
- **Evidence:** `Laab8_miles_selected` (`rlca`: 1 mile = 2 coordinate units); the map buffer is 512 × 16 bytes, one byte per cell.
- **Rule:** [FS §7.1](functional-spec.md#71-grid).

### Static-object composition and footprints

- **Decision:** static geometry is explicit occupied cells/components, not one rectangle per building. War bases and factories are separate entity types with their own compositions and semantic interaction points (heli-pad, exit, capture cell); component heights may differ within one structure; uncertain layouts are not guessed. The robot footprint is a separate concern.
- **Source:** initial specification lock.
- **Evidence:** `Lbd61_add_complex_structure_to_map` builds war bases (`Lbfb2_warbase`) and factories (`Lbfe2_factory`) from element triples.
- **Rule:** [FS §7.3](functional-spec.md#73-static-geometry), [TS §7.3](technical-spec.md#73-static-structures).

### Scenery blockers

- **Decision:** the 165 decoded 2×2 scenery elements are map `blockers` (low box 7, high box 15, fence 99; 660 cells). No chassis can enter them; the commander crosses a box only at or above its height and rests on top of it, and never crosses a fence; projectiles fly over low boxes and are stopped by high boxes and fences. The engine never branches on the blocker `kind`; the frontend picks a sprite per kind through configuration (v1 ships the Spectrum sprites only).
- **Source:** owner decision 2026-09-21 (found in CR001.5); implemented by CR002.1 (#168), assets CR002.5 (#172).
- **Evidence:** `Lb513_get_robot_movement_possibilities` / `Lb5cd_robot_map_collision_internal` (types ≥ 15 block every chassis), `Lb052_check_player_collision` (ship vs piece height), `Lb724_bullet_update_internal` (height ≥ bullet altitude stops the bullet).
- **Rule:** [FS §7.3](functional-spec.md#73-static-geometry), [FS §19](functional-spec.md#19-presentation-spectrum-look-and-feel).

### Map version gap

- **Decision:** `zx-spectrum-original` stays at map `version: 1` while terrain, heights and blockers were added to it; matching the Spectrum map more closely is separate future work.
- **Source:** owner decision 2026-09-21 (CR002).
- **Evidence:** `data/maps/zx-spectrum-original.yaml` header.
- **Rule:** [TS §7](technical-spec.md#7-mapworld-representation).

### Commander starting positions

- **Decision:** Player 1 starts at the extreme-left war-base anchor + (−5, +1) = (17, 10), altitude 0. Player 2 starts at the extreme-right anchor + (+5, +1) = (499, 9), altitude 0, the mirror of Player 1. Spawns are overlay data.
- **Source:** owner decision 2026-09-21 (CR001.7, #154).
- **Evidence:** `La600_start` sets the ship to x = 17, y = 10, altitude 0; Player 2 has no Spectrum counterpart (locked PvP adaptation).
- **Rule:** [FS §8](functional-spec.md#8-commander).

### War-base heli-pad location and landing height

- **Decision:** the heli-pad is the 2×2 "H" area on the war-base roof anchored at (anchor.x, anchor.y − 4). Landing means the commander's whole body is over the pad at an altitude equal to the highest component under it (15 on the original war base). The launched robot's anchor starts on the war-base anchor cell.
- **Source:** owner decision 2026-09-21, option 2 of two put to the owner (CR001.6, #153).
- **Evidence:** `Lbb86_assign_warbase_to_player` places the "H" decoration at (anchor.x, anchor.y − 4); the game loop enters construction only at altitude exactly 15 (`cp 15`); `Lcb52_construction_screen_start_robot` starts the robot 4 cells below the pad.
- **Rule:** [FS §8](functional-spec.md#8-commander), [TS §7.3](technical-spec.md#73-static-structures).

### Terrain piece heights

- **Decision:** terrain pieces have their Spectrum heights map-wide: rough 2 (types 2–5) or 3 (types 6/7), mountain 6, normal and ditch 0, debris 3. One surface-height function serves commander collision, landing and gravity, projectile termination, the damage `ground_height` and the heli-pad rest altitude. Terrain is still drawn flat.
- **Source:** owner decision 2026-09-21 (found in CR002.18); CR002.21 (#203).
- **Evidence:** `Ld7bc_map_piece_heights`; `Lb5d6_map_altitude_2x2`.
- **Rule:** [FS §7.2](functional-spec.md#72-terrain), [TS §7.3](technical-spec.md#73-static-structures).

### Robots on terrain height

- **Decision:** a robot stands on the terrain under it. Its altitude is the highest surface under its 2×2 body at its authoritative anchor, and changes when a move completes. Its top (altitude + stack height) is where the commander rests, docks, rides and is ejected, and a commander below that top blocks the robot. The robot is drawn raised by its altitude. A newly launched robot starts at 0 (every war-base exit is flat).
- **Source:** owner decisions 2026-09-21 and 2026-09-22 (found in CR002.21); CR002.25 (#214).
- **Evidence:** `Lb495` stores `Lb5d6_map_altitude_2x2` in `ROBOT_STRUCT_ALTITUDE` after each step; `Lb099_get_robot_or_decoration_altitude`, `La720_land_on_robot`, `Lcee8_draw_robot_to_buffer` add it to the robot height.
- **Rule:** [FS §7.2](functional-spec.md#72-terrain).

### 2×2 robots, commander, projectiles and heli-pad

- **Decision:** robots, the commander, projectiles and the heli-pad are 2×2 bodies. A unit's `(x, y)` is the anchor of a body covering `x..x+1`, `y−1..y`; every collision, landing, blocking, reservation and hit test uses the whole body. Two bodies collide when they overlap (the Spectrum's 3×3 anchor scan). Capture, docking and the nuclear robot window test the anchor only. The whole body stays on the map.
- **Source:** owner decisions 2026-09-21 (CR002 footprints); CR002.3 (#170), CR002.4 (#171).
- **Evidence:** `Lb5d6_map_altitude_2x2` reads `(x, y)`, `(x+1, y)`, `(x+1, y−1)`, `(x, y−1)`; robots are marked only at the anchor (`bit 6`) and every robot/ship test scans the 3×3 anchor window (`Lb052`, `Lb724`, `Lb557`–`Lb5b1`).
- **Rule:** [FS §7.1](functional-spec.md#71-grid), [TS §8](technical-spec.md#8-occupancy-and-reservations).

## Commander

### Commander-versus-commander collision

- **Decision:** commanders collide with each other. They may share X/Y only when their vertical ranges do not overlap; overlapping commanders block each other horizontally and vertically, including descent. Commanders stay indestructible, untargetable and immune to damage.
- **Source:** initial specification lock (PvP extension; the original has one ship).
- **Evidence:** no Spectrum counterpart; uses the same 2×2 overlap as `Lb052_check_player_collision`.
- **Rule:** [FS §8.3](functional-spec.md#83-commander-collision).

### Commander vertical limits and speed

- **Decision:** altitude 0–48; one vertical update every 4 ticks; ascent +2; descent −2 per update, stopping exactly on any surface under the ship (the last step is shortened at an odd-height surface). Leaving the construction screen or a robot gives a 5-update automatic lift (+2 each) whatever the rise key does; moving does not shorten it, and neither construction nor docking is checked while it runs. Horizontal and vertical movement may happen together.
- **Source:** initial lock (+2/−1); descent −2 by owner decision 2026-09-22 (CR003.1, #216); exit lift by owner decisions 2026-09-21 (CR002.12/#179, CR002.13/#180, CR002.24/#207).
- **Evidence:** `Lafb5_elevate` (+2), `Lafc3_gravity` (−1), `Lfd30_player_elevate_timer` set to 5 by `Lcb8e_construction_screen_exit` and the robot HUD EXIT option (`#a7fd`–`#a80f`).
- **Rule:** [FS §8.2](functional-spec.md#82-vertical-movement). The descent step and the unshortened lift are deviations ([deviations](deviations-from-original.md#commander)).

### Landing on an enemy robot

- **Decision:** enemy robots are physical surfaces for the commander. Descent stops at the robot's top (terrain altitude + stack height); the commander may rest there, but there is no docking, control transfer or contact damage. Docking is for friendly robots only.
- **Source:** initial specification lock; robot top refined by CR002.25.
- **Evidence:** `Lb052_check_player_collision` treats every robot top as a surface; `La69a` docks only on the player's own robots.
- **Rule:** [FS §8.4](functional-spec.md#84-docking-and-enemy-robots).

### Idle tick between commander cells

- **Decision:** a held direction moves the commander 4 ticks per cell with no idle tick: commander move completions are resolved before the tick's commander commands, so a move arriving on the completion tick starts at once. Robot timing is unchanged.
- **Source:** found in CR003.5 (#220); owner decision 2026-09-22 to fix it in the engine (CR003.10, #232). The CR003 decision "commander movement is a frontend-only fix" was superseded by this one.
- **Evidence:** `engine.step` step order; verified on a live backend (cells start at ticks 1, 5, 9, …).
- **Rule:** [FS §8.1](functional-spec.md#81-horizontal-movement), [TS §4](technical-spec.md#4-simulation-timing-and-determinism).

## Economy and construction

### Resource spending rules

- **Decision:** start with 20 general resources; costs bipod 3, tracks 5, anti-grav 10, cannon 2, missile 4, phaser 4, nuclear 20, electronics 3. Pools: general, chassis, electronics, cannon, missile, phaser, nuclear. A module is paid from its own pool first and general pays only the shortfall; a selection that cannot be covered is rejected. Editing uses a temporary buffer; deselecting reverses the mixed spend; resources are committed only when START ROBOT succeeds; leaving without launching costs nothing. Owned factories produce 2 of their type per in-game day, owned war bases 5 general.
- **Source:** initial specification lock from the disassembly.
- **Evidence:** `INITIAL_PLAYER_RESOURCES: equ 20`; `Lca57_construction_add_piece`, `Lcac1_update_resources_buffer_when_removing_a_piece`.
- **Rule:** [FS §10](functional-spec.md#10-economy-and-construction-resources).

### Exact normal-weapon stack order

- **Decision:** bottom to top: chassis, cannon, missile, phaser, nuclear, electronics, then the commander when docked. Missing components are omitted and the relative order is kept.
- **Source:** initial specification lock.
- **Evidence:** initial specification; no disassembly label was recorded.
- **Rule:** [FS §12](functional-spec.md#12-canonical-component-stack).

### Robot piece heights

- **Decision:** module heights are the Spectrum piece heights: bipod 11, tracks 7, anti-grav 8, cannon 6, missile 6, phaser 7, nuclear 7, electronics 7. Robot heights therefore range 13–38, the damage formula runs on its native scale, and the tallest robot on the highest walkable ground (44) stays below the commander ceiling (48). This also settled the earlier caveat about the starting-strength scale.
- **Source:** owner decision 2026-09-22 (CR003.3, #218); replaced placeholder heights (chassis 4, others 2).
- **Evidence:** `Ld7b4_piece_heights`, summed by `Lb904_robot_height_loop`; the disassembly header's worked example (phaser on tracks + cannon at ground 0 deals 44).
- **Rule:** [FS §12](functional-spec.md#12-canonical-component-stack).

### Construction screen exit and modality

- **Decision:** the construction screen is modal for the building player only. EXIT MENU discards the build at no cost; START ROBOT launches a valid build, commits resources and closes the screen; a refused launch leaves the screen open and changes nothing. Closing either way starts the exit lift. Picking another chassis swaps it: the fitted chassis is refunded and removed first, and if the new one is then unaffordable the robot is left with no chassis. In PvP only the building player's commander is frozen.
- **Source:** owner decisions 2026-09-21 (CR002.12/#179, CR002.13/#180); chassis swap CR002.20 (#198).
- **Evidence:** `Lc85d_robot_construction` / `Lca0f_waiting_for_key_press_loop` (modal loop), `Lcb8e_construction_screen_exit`, `Lcb52_construction_screen_start_robot`, `Lca48`/`Lcac1` (chassis swap).
- **Rule:** [FS §11](functional-spec.md#11-robot-construction), [FS §10.3](functional-spec.md#103-spending-semantics). The PvP freeze is a deviation ([deviations](deviations-from-original.md#construction)).

### Launched robots stuck in the doorway

- **Decision:** a launched robot holds Stop & Defend and walks out south (+y) up to 5 steps, one step per robot update, with normal movement legality. The walk-out ends when a step is blocked or lost to contention, when the update fires instead, when a commander docks on the robot, or when any order is given.
- **Source:** found in CR002.3; owner decision 2026-09-21 (match the original).
- **Evidence:** `La6c8` after `Lc849_robot_construction_if_possible` sets the desired direction down and `ROBOT_STRUCT_NUMBER_OF_STEPS_TO_KEEP_WALKING` to 5.
- **Rule:** [FS §11](functional-spec.md#11-robot-construction).

### Construction costs in the UI

- **Decision:** the construction screen shows costs from a build-time copy of the engine defaults, checked for drift by CI; costs are not sent in the protocol.
- **Source:** owner decision 2026-09-21 (CR002).
- **Evidence:** `frontend/src/generated/rules/construction.json`, `npm run rules:generate`.
- **Rule:** [FS §10.2](functional-spec.md#102-original-spectrum-construction-costs), [TS §23](technical-spec.md#23-frontend-renderingstate).

### Robot ids are never reused

- **Decision:** a robot's id is `robot-<owner>-<n>`, where `n` comes from a per-owner launch counter that only grows, so an id is never issued twice in a match. Applies to PvP and solo.
- **Source:** owner decision 2026-09-25 (CR004.12, #295; a pre-existing PvP bug that solo matches hit reliably).
- **Evidence:** `GameState.robot_launches`; `robot_launch._next_robot_id`.
- **Rule:** [TS §12](technical-spec.md#12-robot-build-model).

### CR005 playtest fixes

- **Decision:** (1) a war base does not produce a robot while a free commander is in its doorway below the new robot's top; the launch is refused as `EXIT_BLOCKED` for players and the AI. (2) Ground heights are drawn ×3 under robots, the commander, bullets and shadows (presentation only; engine altitudes unchanged). (3) A robot killed in combat on four plain cells leaves 2×2 rough debris (height 3); a robot killed by a nuke leaves none; every cell of a nuked building becomes rough debris and no longer blocks. (4) The commander's shadow is cut per cell, each part on its own cell's surface.
- **Source:** owner request 2026-09-27 (CR005.1–CR005.4); rules version `cr005`.
- **Evidence:** `La6c8` tests only robot marks (item 1 departs from it); `Lb116_robot_destroyed`, `Lba44_robots_handled`, `Lbc27_replace_building_by_debris` (item 3).
- **Rule:** [FS §11](functional-spec.md#11-robot-construction), [FS §17.3](functional-spec.md#173-nuclear-weapon), [FS §19](functional-spec.md#19-presentation-spectrum-look-and-feel). Item 1 is a deviation ([deviations](deviations-from-original.md#construction)).

## Movement and navigation

### Exact movement speeds and terrain penalties

- **Decision:** four terrain classes (normal = types 0–1, rough = 2–7, mountain = 8–11, ditch = 12–14) and one tick value per (chassis, terrain) pair: bipod 24/32/–/–, tracks 16/24/28/–, anti-grav 12/12/16/12 (normal/rough/mountain/ditch; – = blocked). The terrain that sets a move's speed is the highest piece under the destination 2×2 body. Tracks stay faster than bipod; both lose 8 ticks per cell on rough. Anti-grav's ditch speed equal to its flat speed is correct.
- **Source:** owner decision 2026-09-21 (CR001.4, #151; map terrain decoded by CR001.5, #152). This replaced the earlier multiplier placeholders and the qualitative "tracks slow down less than bipod on rough"; the open conflict recorded by the movement research (#61) is closed by it.
- **Evidence:** `Lb61d_robot_movement_speed_table` (cycles per move, 1 cycle = 4 ticks), `Lb5f3_determine_speed_based_on_terrain`, `Lb513_get_robot_movement_possibilities` (chassis limits 8/12/15).
- **Rule:** [FS §13](functional-spec.md#13-chassis-and-terrain-behavior), [TS §13](technical-spec.md#13-robot-movement).

### Dumb vs electronic navigation

- **Decision:** robots without electronics use original-style local routing and may get stuck even when a longer route exists; electronic robots plan a deterministic shortest route and re-plan. Electronics never changes terrain permissions; the strategies sit behind one engine interface. "Blocked" means no legal step at all: a dumb robot tries the primary-axis step, then the direction it already faces (momentum), then the two perpendicular steps in an order drawn once per 16-tick window, then any legal step.
- **Source:** initial lock; detour fallback adopted on owner request 2026-09-25; momentum added after the owner's report of robots stuck in loops (2026-09-27). The owner's proposed alternative (more randomness over all four directions) was tested and rejected on the evidence.
- **Evidence:** `Lb222_choose_direction_to_move`, `Lb326`/`Lb33e_pick_direction_at_random`, `Lb1f5` (`rand & 3 + 3` cycles of keep-walking). Measured: momentum took arrivals from 27/32 to 32/32 over four obstacle fixtures × 8 seeds.
- **Rule:** [FS §16.4](functional-spec.md#164-navigation-intelligence), [docs/mechanics/navigation.md](../docs/mechanics/navigation.md). The momentum rule stands in for the Spectrum's counter ([deviations](deviations-from-original.md#movement-and-navigation)).

### Simultaneous destination-cell claims

- **Decision:** a move reserves its whole destination body when it starts; the reservation lasts until the move completes or is cancelled. Same-tick claims whose destination bodies overlap are resolved with the match-local seeded RNG: two contenders are a 50/50 draw, more are uniform. Losers stay put and may retry.
- **Source:** initial specification lock; extended to 2×2 bodies by CR002.3.
- **Evidence:** project rule (the single-player original has no simultaneous claims).
- **Rule:** [FS §14](functional-spec.md#14-robot-movement-and-destination-reservation).

## Orders and capture

### War-base capture mechanics

- **Decision:** enemy robots can capture war bases with the same continuous-occupation rule as factories. Neutral structures use that rule too: a neutral factory is not acquired instantly. Duration 1,440 ticks (12 in-game hours, 72 s); ownership changes the tick it completes and victory is evaluated in the same step. The duration is configurable rule data.
- **Source:** initial lock; neutral structures by owner decision 2026-09-23 (replacing the earlier "first qualifying robot acquires a neutral factory at once").
- **Evidence:** `Ladb7_building_loop` counts while a robot mark is on the building's cell.
- **Rule:** [FS §9](functional-spec.md#9-ownership-and-capture). Neutral capture time is a deviation ([deviations](deviations-from-original.md#orders-and-capture)).

### Capture interruption semantics

- **Decision:** when qualifying occupation breaks before completion, progress resets to zero immediately; no partial progress is kept.
- **Source:** initial specification lock.
- **Evidence:** project rule; `capture.advance_capture`.
- **Rule:** [FS §9](functional-spec.md#9-ownership-and-capture).

### Capture order lifecycle

- **Decision:** Search & Capture never completes and never falls back. Each evaluation selects the nearest matching structure that no other same-owner robot with the same order targets; the robot holds the capture cell until the structure changes hands, then retargets; with nothing to take it keeps the order and holds. A robot already standing on its target's capture cell keeps that target.
- **Source:** owner decision 2026-09-22 (CR003.2, #217: full Spectrum behaviour), amended 2026-09-23 (selection re-runs every evaluation). The enemy AI's switch to Destroy Enemy Robots when no target exists was not adopted for player robots.
- **Evidence:** `Lb289_choose_direction_orders_with_building_targets`, `Lb34d_find_capture_or_destroy_target`, `Lb36c_check_if_building_is_available_and_nearest_than_current_nearest`.
- **Rule:** [FS §16](functional-spec.md#16-autonomous-orders). The re-selection is a deviation ([deviations](deviations-from-original.md#orders-and-capture)).

### Search & Capture war-base target

- **Decision:** the war-base target takes any war base not already the ordering player's, neutral ones included.
- **Source:** owner decision 2026-09-25.
- **Evidence:** `Lb3d5_prepare_robot_order_building_target_search` matches one exact ownership value (enemy only).
- **Rule:** [FS §16](functional-spec.md#16-autonomous-orders); deviation ([deviations](deviations-from-original.md#orders-and-capture)).

### Search & Destroy approach position

- **Decision:** a Search & Destroy (robots) hunter closes to a position lane-aligned with its target: the two 2×2 bodies face each other along a full edge (anchor two cells away on one axis, level on the other). When no aligned position is reachable it falls back to any edge-touching position. The order keeps hunting while a hostile robot exists: "no route" is not a fallback; with no route the robot takes one greedy step toward the nearest aligned anchor or waits. An electronic hunter caches its route and re-plans every 20 ticks, or earlier when the route runs out, it is off the route, the next cell is no longer enterable, or the target changes.
- **Source:** owner request 2026-09-25 (playtest bug); hunts keep their order by owner decision 2026-09-27 (CR004.13, #299). Engagement of occupied targets from CR003.4 (#219).
- **Evidence:** a shot travels along the firing robot's facing (`Lb6d6_weapon_fire`) and connects only while the bodies overlap on the off axis.
- **Rule:** [FS §16](functional-spec.md#16-autonomous-orders), [TS §14](technical-spec.md#14-navigation-policies).

### Robot facing and turning

- **Decision:** a robot faces one cardinal direction; it is launched facing south and turns to face each step. A shot travels in the facing; there is no aiming. A step or shot in another direction costs one update turning 90 degrees (a reversal costs two) and a robot mid-turn neither moves nor fires. An autonomous robot turns toward its target before firing, except on a capture cell. A robot holding a capture cell turns to face out of the structure.
- **Source:** facing wired up on owner request 2026-09-23; facing drives combat and turning costs time by owner decision 2026-09-23; outward facing by owner decision 2026-09-24.
- **Evidence:** `Lb6d6_weapon_fire` copies `ROBOT_STRUCT_DIRECTION` into `BULLET_STRUCT_DIRECTION`; `Lb471_move_robot_one_step_in_desired_direction` rotates and returns without moving. The one-hot direction bit order (east 1, west 2, south 4, north 8) was read from `Lb724`'s `rrca` chain and `Lcf08_direction_loop`.
- **Rule:** [FS §16.3](functional-spec.md#163-facing-and-turning). Outward facing is a deviation ([deviations](deviations-from-original.md#orders-and-capture)).

## Combat and nuclear weapon

### Exact projectile mechanics

- **Decision:** a projectile advances 2 cells every 4 ticks on either axis and travels 10 cells (cannon, phaser) or 14 cells (missile), +2 with electronics. Its first move happens on the fire tick, so a target 1–2 cells away is hit at once. A robot fires at most one normal weapon per 4-tick game cycle; an autonomous shot moves again at the cadence tick that closes its fire cycle, a direct shot one cycle later. An order-driven robot fires only on its own robot update (its move period for the terrain under it). Buildings use the generic altitude collision; there is no separate building rule. Projectile altitude is 10, independent of robot height. One normal projectile per robot at a time.
- **Source:** CR001 (owner decision 2026-09-21; #150) replaced the mile-derived 20/28/20/+6 ranges; fire-cycle timing by owner decisions 2026-09-21 (CR002.2, #169); AI fire on its own update by owner decision 2026-09-21 (CR002.19, #197). Earlier research: #72.
- **Evidence:** `Lb6d6_weapon_fire` (range counter 5 or 7, +1 electronics, first move before returning), `Lb724_bullet_update_internal` (2 cells per update), `Lb0ca_update_robots_bullets_and_ai` (one game cycle), `Lb154_robot_ai_update` (fires only when its counter reaches 0).
- **Rule:** [FS §17.1](functional-spec.md#171-normal-projectile-channel), [FS §16.1](functional-spec.md#161-robot-updates). The per-robot channel and the update-phase simplification are deviations ([deviations](deviations-from-original.md#combat)); the scan distances remain open ([open-questions](open-questions.md)).

### Damage, accuracy, and electronics effects

- **Decision:** `damage = ((60 − (robot_height + ground_height)) // 4) × multiplier` with multipliers cannon 2, missile 3, phaser 4; the division is integer floor. `ground_height` is the highest surface under the robot's 2×2 body (2–3 on rough, 6 on mountains, 3 on debris). Robots start at strength 100 and are destroyed at strength ≤ 0. There is no hit roll, no component damage and no defensive electronics effect; electronics only adds range.
- **Source:** formula locked initially; research #74 confirmed rounding, multiplier order, strength 100 and the ≤ 0 threshold; `ground_height` from terrain by owner decision 2026-09-21 (CR002.21, #203); input scale settled by CR003.3 (#218).
- **Evidence:** `Lb7a7_potentially_hit_a_robot` (`ld a, 60` / `sub` / `srl a` / `srl a`), `Lb7c8_damage_calculation_loop` (repeated addition), strength `100` at both spawn sites.
- **Rule:** [FS §17.2](functional-spec.md#172-damage). The research items listed as open in [open-questions.md](open-questions.md) remain open by owner choice. What happens to a destroyed robot: [Destroyed-robot blink](#destroyed-robot-blink).

### Destroyed-robot blink

- **Decision:** RESOLVED (owner decision 2026-10-03): match the Spectrum. A robot whose strength reaches 0 is destroyed at the hit and shows 0%, then blinks for 4 game cycles: hidden, shown, hidden, shown, one change per cycle on positive multiples of 4 ticks. It is removed on the next cycle. While blinking it does nothing by itself (no move, autonomous fire or order evaluation). It occupies its cells, stops bullets without taking damage, blocks the commander, can be landed on and can be caught in a nuclear blast, but only on its shown cycles; capture progress on its cell advances only on shown cycles and resets on hidden ones. A robot that moved onto its cells while it was hidden simply overlaps it when it reappears. It is never fired at, though a Search & Destroy (robots) hunt may still head for it. It counts toward its owner's robots and the robot cap. A docked player stays docked and can still fire. Debris, release of a docked commander and the end of the radar mark all happen at removal. A nuclear blast removes robots at once, with no blink.
- **Source:** owner decision 2026-10-03 (open question §3.6); rules version `blink`.
- **Evidence:** `Lb7d7_robot_destroyed` (`ld a, -4`), `Lb0fa_robot_update` (`inc` strength; `and 1` → `res 6`/`set 6`), `Lb116_robot_destroyed` (debris, `Ld65a_flip_2x2_radar_area`, `(iy + 1) = 0`, `Lbb40_count_robots`), `Lb0ca_update_robots_bullets_and_ai` (once per game cycle), `Lb7a7` (`jp m, Lb7de_collision_handled`), `Lb68e_object_found`, `Lb41d_find_nearest_opponent_robot`, `Lba33` (bit 6), `Ladb7_building_loop` (bit 6), `Lace2` (exit only when the slot is empty), `Lac99`/`Lacb3_regular_weapon_fire` (no strength check), `Lcd18_draw_map_cell` (draws bit 6 only), `Ld632` (radar from slots).
- **Rule:** [FS §17.2](functional-spec.md#172-damage); algorithm in [combat.md](../docs/mechanics/combat.md#destroyed-robots-destructiondestroy_robot-destructionadvance_destroyed_robots).

### Bullet altitude gate

- **Decision:** a projectile hits a robot only when the robot's top (terrain altitude under its body + stack height) is at least the projectile altitude (10). With the Spectrum piece heights every robot on the original map is at least 13 tall, so every robot is hit; the gate matters only for a raised bullet altitude or overridden heights.
- **Source:** owner decision 2026-09-22 (CR002.25 follow-up; PR #215). Not evidence-derived.
- **Evidence:** the original has no robot-height test for bullets.
- **Rule:** [FS §17.1](functional-spec.md#171-normal-projectile-channel); part of the bullet-scan deviation ([deviations](deviations-from-original.md#combat)).

### Autonomous use of the nuclear weapon

- **Decision:** match the original. An autonomous robot detonates only when it holds Search & Destroy against a factory or war base and stands on the target structure's target cell (the cell a Search & Capture order navigates to). No other order ever detonates. A robot without a nuclear module cannot hold Search & Destroy against a structure (it falls back to Stop & Defend). Manual detonation stays available.
- **Source:** found by the M9.4 scripted match; owner decision 2026-09-21 (CR001.1, #148). Options put to the owner: never detonate autonomously; detonate within range; keep the old self-destruct policy.
- **Evidence:** `Lb99f_fire_nuclear_bomb` is reached only from the direct-control menu and from `Lb2e8_target_directions_calculated`; `Labc8_capture_or_destroy_order_selected` turns a destroy-structure order into Stop & Defend without a nuke.
- **Rule:** [FS §16.2](functional-spec.md#162-autonomous-nuclear-use).

### Nuclear blast shape

- **Decision:** robots: every robot of either side inside a 9×9 window centred on the carrier with trimmed corners (row widths 5, 7, 9, 9, 9, 9, 9, 7, 5). Buildings: at most one, war bases checked before factories, the first in range destroyed whoever owns it; dx = |carrier.x − b.x|, dy = |carrier.y + 1 − b.y| (+4 to carrier.y for war bases); war base in range when dx < 7, dy < 7, dx + dy < 10; factory when dx < 5, dy < 5, dx + dy < 7. The carrier is always destroyed. The shape parameters are rule data.
- **Source:** found while researching the autonomous-nuke question; owner decision 2026-09-21 (CR001.2, #149) replaced the uniform 16-cell radius.
- **Evidence:** `Lb99f_fire_nuclear_bomb` (`ld de, #070a`, `ld de, #0507`, `ld bc, #0909`), "A nuclear bomb will only destroy at most one building".
- **Rule:** [FS §17.3](functional-spec.md#173-nuclear-weapon).

### Nuclear blast vs. scenery

- **Decision:** every destructible box (element types 17–20) whose bottom-left cell is inside the robot window becomes 2×2 rough debris (height 3) for the rest of the match; fences survive. The random choice between debris types 6 and 7 is not modelled.
- **Source:** found in CR002.1; owner decision 2026-09-21 (CR002.18, #196); debris height by CR002.21 (#203).
- **Evidence:** `Lba02_look_for_robots_in_range_of_nuclear_bomb` / `Lba44_robots_handled` (skips cells with map bit 5, types < 17 and ≥ 21; stamps type 6 or 7).
- **Rule:** [FS §17.3](functional-spec.md#173-nuclear-weapon).

## Presentation

### Map-end fence placement

- **Decision:** the renderer centres the map-end fence post on its 2×2 footprint, so both walls leave the same half-cell gap to a unit at the movement limit. Presentation only; data, collision and heights are unchanged, and the Spectrum's 4-px odd-parity shift is not applied.
- **Source:** playtest report; owner decision 2026-09-22 (CR003.7, #222).
- **Evidence:** fences cover columns 12–13 and 503–504; the fence sprite's post stands on the footprint's −x column (`La050_iso_additional_graphic_26`); `MIN_PLAYER_X`/`MAX_PLAYER_X` 14/501 give the original the same asymmetry. Template matching against [docs/reference-screens/cr002/main-screen.png](../docs/reference-screens/cr002/main-screen.png) confirmed the 4-px shift.
- **Rule:** [FS §19](functional-spec.md#19-presentation-spectrum-look-and-feel); deviation ([deviations](deviations-from-original.md#presentation)).

### Commander/robot/structure sprites

- **Decision:** the commander, robot pieces and war-base/factory walls are drawn with decoded Spectrum sprites instead of procedural prisms; an unmapped asset falls back to a placeholder prism. The commander has one frame; each robot piece has four facing sprites. The factory piece-on-top and war-base "H" decoration overlays are not implemented (the flag is). Presentation only.
- **Source:** owner-directed 2026-09-22; robot facing sprites on owner request 2026-09-23.
- **Evidence:** `Lcd83_render_player` (graphic 0, no facing), `Lcefd_draw_robot_piece_to_buffer` / `Ld6c8_piece_direction_graphic_indices`, `Lbfb2_warbase` / `Lbfe2_factory` (element types 15/16, heights 7/15), `Lce56_decoration_sprite_indexes`.
- **Rule:** [FS §19](functional-spec.md#19-presentation-spectrum-look-and-feel).

### Radar

- **Decision:** a 128 × 16 px strip, one pixel per cell, white on black, showing a 128-column window that scrolls in 64-column steps to keep the viewer's commander inside. It shows every robot and only the viewer's own commander.
- **Source:** owner decision 2026-09-21 (CR002.11 #178, CR002.22 #205).
- **Evidence:** `Ld65a_flip_2x2_radar_area`; the original has one ship.
- **Rule:** [FS §19](functional-spec.md#19-presentation-spectrum-look-and-feel).

### Original artwork

- **Decision:** decoded original artwork may be used while the repository and deployments are private; it must be licensed or replaced before any public release or deployment.
- **Source:** owner decision 2026-09-21 (CR002; release gate #201, release checklist item 16).
- **Evidence:** `frontend/public/assets/README.md`, "Provenance and licensing".
- **Rule:** [FS §19](functional-spec.md#19-presentation-spectrum-look-and-feel).

## Match and network

### Disconnect and reconnect rules

- **Decision (as locked):** a disconnect pauses the match at once and the simulation stops while paused. Grace period 60 s (configurable). A reconnect receives the current snapshot; the match resumes only when both players are connected. Grace expiry makes the disconnected player forfeit when an opponent remains eligible to win. If both are disconnected, each has an independent deadline, and if both expire without either returning the match ends as no-contest. No manual pause. Reconnect state never mutates engine state.
- **Implemented:** `ReconnectCoordinator._resolve_expiry` (`backend/app/match/reconnect.py`) compares deadline values: the player whose deadline expires first forfeits while the other is still within grace, even if that player never returns; no-contest only on an exact tie of the deadlines.
- **Status:** the both-disconnected case is pending owner decision (spec/code disagreement 4, [open-questions.md](open-questions.md#3-owner-decisions-pending)).
- **Source:** initial specification lock; solo matches added by CR004 (owner decision 2026-09-25: same as PvP for the human).
- **Evidence:** project rule; `backend/app/match/reconnect.py`.
- **Rule:** [FS §18](functional-spec.md#18-disconnect-and-reconnect), [TS §20](technical-spec.md#20-disconnectreconnect-runtime-policy).

### Nickname policy: duplicates

- **Decision:** a joining guest may not use the creator's nickname (compared case-insensitively after NFKC normalisation); the join is rejected with `invalid_nickname` and the guest can retry with another name.
- **Source:** owner decision 2026-10-03 (security review 2026-09-30 follow-up).
- **Evidence:** `match.manager._nickname_key`.
- **Rule:** [FS §5.1](functional-spec.md#51-lobby-and-nicknames).

### Release

- **Decision:** `v1.0.0-rc1` was tagged; the production human two-player check (release checklist item 11) is postponed; the first release has no rollback target.
- **Source:** owner decision 2026-09-21 (CR002).
- **Evidence:** `docs/release/`.
- **Rule:** operational; [TS §24](technical-spec.md#24-deployment-and-supply-chain).

### Replay retention default

- **Decision:** RESOLVED (owner decision 2026-10-03): production keeps finished/interrupted replays 5 days (`NETHER_EARTH_REPLAY_RETENTION_DAYS`, `0`/`forever` disables), development keeps everything.
- **Rule:** [TS §22](technical-spec.md), [match-runtime](../docs/mechanics/match-runtime.md#replay-writer-and-retention).

## AI opponent

### AI opponent scope

- **Decision:** a single-player match against a computer opponent is in v1 scope, with one difficulty and a single "Play vs computer" entry point that creates a match with the computer in the second seat. The AI lives in the engine as a deterministic planner that issues ordinary commands; it has no commander. Human players and PvP behave exactly as before. If the human disconnects, the match pauses with the usual grace window. The Spectrum's enemy AI is a reference, not a contract: the target is an opponent that plays better.
- **Source:** owner change request 2026-09-25 (CR004, #282–#292).
- **Evidence:** `docs/cr004/spectrum-ai-notes.md` (the original enemy AI and the verdict per mechanic).
- **Rule:** [FS §3.1](functional-spec.md#31-single-player-vs-computer-opponent), [TS §28](technical-spec.md#28-ai-opponent). Every departure from the Spectrum's enemy AI is listed in [deviations](deviations-from-original.md#ai-opponent).
