# Milestone 6 Plan — Combat, Damage, Destruction & Victory

Spec: `_specs/milestones/06-combat-damage-victory.md` (also `_specs/functional-spec.md` §17, `_specs/technical-spec.md` §§16-18, `_specs/open-questions.md` §§8-9).

GitHub tracking: issue #82 (plan), tasks #70-#81. Each task below IS the corresponding GitHub issue; close the issue with the task's PR.

## Dependency waves (per issue #82's dependency graph)

```
Wave 1: Task 1 (#70 combat metadata/rule interfaces)
        + Task 3 (#72 projectile research, fully independent)
        + Task 5 (#74 damage/electronics research, fully independent)
Wave 2: Task 2 (#71 fire validation/channel)          [needs #70]
        + Task 8 partial groundwork (#78 nuclear/destruction contract design) [needs #70]
Wave 3: Task 4 (#73 projectile simulation/collision)  [needs #71, #72 as far as resolved]
        + Task 6 (#76 damage/strength/destruction)    [needs #70, #73's hit boundary, #74 as far as resolved]
Wave 4: Task 7 (#77 autonomous engagement consumption) [needs M5 engagement intent + #71]
        + Task 8 (#78 nuclear detonation/structure destruction) [needs #70, #76's destruction service]
Wave 5: Task 9 (#79 victory unification)              [needs M5 capture + #78]
Wave 6: Task 10 (#80 snapshot/replay integration)     [needs #71,#73,#76,#77,#78,#79]
Wave 7: Task 11 (#81 integration scenario)            [needs everything above, final gate]
```

Within a wave, tasks touch disjoint modules and may run as parallel implementer dispatches; across waves, later tasks must not start until earlier-wave tasks are reviewed clean (or parked) so their interfaces are stable. Tasks 3 and 5 (#72, #74) are research issues: they update `_specs/open-questions.md` §8/§9 and this spec, and do not themselves touch `engine/src/nether_earth/*.py` combat code — they block *claiming exact Spectrum fidelity*, not implementation start (see each task's "fidelity gate").

## Global Constraints

- Consume M4's canonical weapon identities (`robot_build.ModuleIdentity.CANNON/MISSILE/PHASER/NUCLEAR`) exactly — no second weapon catalog.
- Reuse the single shared conversion `rules.miles_to_cells` (1 mile = 2 cells) — never a duplicate `* 2` literal.
- Locked numeric defaults, all as new named fields on `rules.EngineRules` (the existing single flat `@dataclass(frozen=True, slots=True)` rule object — there is no separate `CombatRules` class; extend `EngineRules` following its exact existing per-field-with-validation convention):
  - `cannon_range_cells = 20`, `missile_range_cells = 28`, `phaser_range_cells = 20`
  - `electronics_range_bonus_cells = 6`
  - `nuclear_radius_cells = 16`
  - `normal_projectile_altitude = 10`
  - `cannon_damage_multiplier = 2`, `missile_damage_multiplier = 3`, `phaser_damage_multiplier = 4`
- Locked base-damage formula: `base_damage = (60 - (robot_height + ground_height)) / 4` (exact integer truncation order is Task 5/#74's research output — do not invent it in Task 1/#70).
- Single active normal-projectile channel per robot: cannon/missile/phaser share one channel; a robot may have at most one active normal projectile; nuclear is a separate discrete detonation, never entering this channel.
- Commanders (`commander.Commander`): never targetable, damageable, or destroyable. Combat may read commander geometry via the existing `collision.py` contract only.
- Normal weapons (cannon/missile/phaser) can never destroy `structures.Factory`/`structures.WarBase` — only `ModuleIdentity.NUCLEAR` can.
- Victory: reuse `victory.evaluate_victory` (already implemented, M5.7/#66) — M6 must not create a second victory function; nuclear/structure destruction call the same one M5 capture already calls in `engine.py`.
- `GameState` is immutable (`frozen=True, slots=True`); every new field is a canonical-ordered tuple following `state.py`'s existing `with_robots`/`with_structure_ownership`/`with_capture_progress` pattern (validate, sort, return new `GameState`). Do not mutate `WorldMap` — it is loaded once and never touched at runtime; a destroyed war base/factory is a `GameState`-attached override, exactly mirroring `capture.py`'s `StructureOwnership` pattern (never a `WorldMap` field).
- Failed/rejected fire attempts cause zero state mutation (mirror `movement.py`'s `RobotMoveResult`/`LaunchResult` accept-or-reject invariant style).
- Everything must be deterministic/replay-safe: same seed + state + commands ⇒ identical projectiles/events/state/hash. No unordered dict/set iteration deciding an outcome; use `events.EventSequencer` for every emitted event, following `engine.py`'s existing per-step conventions.
- No FastAPI/WebSockets/frontend/network I/O in engine code.
- Add regression tests per locked rule surface, following `engine/tests/` existing patterns (`test_movement.py`, `test_capture.py`, `test_robot_launch.py` are the closest analogues for accept/reject + destruction-cleanup style tests).
- Where #72/#74 leave a mechanic unresolved, implement behind an explicitly named, documented `EngineRules` field marked as a non-canonical configured default (matching `rules.py`'s existing precedent for `commander_vertical_update_ticks`/`robot_rough_multiplier_bipod` — "documented default, not independently verified") — never a bare literal, never presented as verified Spectrum fidelity.

---

### Task 1: Canonical combat metadata and rule interfaces (issue #70)

Depends on: M4 `robot_build.ModuleIdentity` stable (already true). May start immediately.

Scope:
- Extend `engine/src/nether_earth/rules.py`'s `EngineRules` with the locked numeric fields listed under Global Constraints above, following the module's exact existing convention: one field per name (no dict-valued fields), a paragraph in the class docstring citing the spec section, and a `__post_init__` validation clause (positive-integer checks) alongside the existing ones.
- Add explicit placeholder/policy fields for still-unresolved projectile timing/collision and accuracy/electronics behavior (e.g. `projectile_step_cells_per_tick`, `projectile_max_range_cells_*` per weapon if range is expressed as authoritative lifetime rather than re-deriving from the range fields above — resolve the exact shape against #72's findings if #72 lands first in the same wave; otherwise land the interface now and let #73 supply the concrete field). Do not assign these unresolved fields a value presented as a verified Spectrum default; document them exactly like `rules.py`'s existing `robot_ditch_multiplier_anti_grav` precedent.
- Define stable engine-level types in a new module `engine/src/nether_earth/combat.py` (the natural home given this codebase's one-module-per-subsystem convention — mirrors `capture.py`, `docking.py`): `FireRequest`/`FireResult`-shaped dataclasses per the milestone spec's "Proposed engine contracts" section, `Projectile` state shape, and stable rejection-reason enum stubs. Task 2 fills in the actual validation logic; this task only defines the frozen/slotted dataclass shapes and `__all__` exports so later tasks import from one place.
- Keep M4's construction cost/resource metadata (`module_cost_*`, `starting_general_resources`, etc.) untouched — do not duplicate or move it.

Acceptance criteria:
- No second `ModuleIdentity`-equivalent catalog exists anywhere in `combat.py` or `rules.py`.
- Every locked numeric default above lives only in `EngineRules`, with `DEFAULT_RULES` picking it up automatically.
- `rules.miles_to_cells` is imported and reused wherever miles are converted for combat defaults' derivation/documentation — no `* 2` literal in `combat.py`.
- Unit tests (`engine/tests/test_rules.py`, extend the existing file) assert every new default's exact value and that `EngineRules(...)` overrides work per-field.
- `combat.py`'s new dataclasses have zero behavior yet (construction/validation only) — Task 2 implements `validate_fire`.

Out of scope: fire validation logic, projectile advancement, damage/accuracy math, nuclear detonation, frontend/network protocol.

---

### Task 2: Fire validation and single active normal-projectile channel (issue #71)

Depends on: Task 1 (#70) merged.

Scope:
- In `combat.py`, implement `validate_fire(request: FireRequest, state: GameState, ...) -> FireResult` as a pure function, following `movement.validate_robot_move`'s exact structure (fixed-order checks, each with its own named rejection reason, no side effects on rejection).
- Check order: (1) source robot exists in `state.robots` and is not destroyed; (2) requester controls the robot (reuse the existing ownership/direct-control convention from `direct_control.py`/`orders.py` — a command's `player` must equal `robot.owner`); (3) the named weapon is a member of `robot.build.weapons` or (for nuclear) `robot.build.weapons` contains `ModuleIdentity.NUCLEAR`; (4) dispatch: if the weapon is cannon/missile/phaser, enforce the single-active-normal-projectile-channel gate (new field needed — see below); if nuclear, route to a separate acceptance path with no channel check.
- Add the channel-tracking field. Because `Robot` (`robot.py`) is frozen/slotted with an established "one new optional field per subsystem" pattern (`movement`, `order`), add `Robot.active_projectile_id: EntityId | None = None` plus a `Robot.with_active_projectile(...)` method mirroring `with_movement`/`with_order` exactly (validate, return a new `Robot`).
- Define stable `FireRejectionReason(str, Enum)` values (mirror `movement.MovementRejectionReason`'s naming style): `NO_SUCH_ROBOT`, `ROBOT_DESTROYED`, `NOT_CONTROLLED_BY_PLAYER`, `WEAPON_NOT_FITTED`, `CHANNEL_OCCUPIED`, plus nuclear-specific ones from Task 8.
- `FireResult.accept(...)` hands off: for a normal weapon, the accepted result carries enough (`robot_id`, `weapon`, aim/target per whatever M5 engagement-intent shape Task 7 will consume, or explicit dx/dy direction for direct control) for Task 4 to create the `Projectile`; for nuclear, hands off to Task 8's detonation entry point. Do not create the `Projectile` or run detonation in this task — only the accept/reject boundary.

Acceptance criteria:
- Absent/unfitted weapon rejected with `WEAPON_NOT_FITTED`, zero state mutation.
- Destroyed/nonexistent robot rejected, zero state mutation.
- Second normal-weapon fire request rejected with `CHANNEL_OCCUPIED` while `robot.active_projectile_id is not None`.
- Channel free again once `active_projectile_id` is cleared (Task 4's terminal-path responsibility, but Task 2's tests may set/clear the field directly to prove the gate).
- Nuclear requests never touch `active_projectile_id`.
- Tests (`engine/tests/test_combat_fire.py`, new file) cover every rejection reason, fitted/unfitted, occupied/free channel, non-owner requester, and prove `state is` identical (not just equal) on every rejection path, matching `movement.py`'s existing "rejected call returns caller's `state` object unchanged" convention.

Out of scope: projectile advancement/collision (#73), damage (#76), nuclear area effects (#78).

---

### Task 3: Research and finalize Spectrum projectile mechanics (issue #72)

Fully independent research task — no engine code dependency, can run in Wave 1 alongside Task 1.

Scope:
- Trace `santiontanon/netherearth-disassembly`'s `netherearth-annotated.asm` bullet-update routines: `BULLET_STRUCT_*` fields (`BULLET_STRUCT_RANGE`, `BULLET_STRUCT_DIRECTION`, `BULLET_STRUCT_ALTITUDE`), `Lb6d6_weapon_fire` (fire/range assignment: `WEAPON_RANGE_DEFAULT = 5`, `WEAPON_RANGE_MISSILES = 7`, electronics `+1`), `Lb70d_bullet_update`/`Lb724_bullet_update_internal` (per-cycle advance: ±2 cells per axis per update, range decremented once per update cycle, disappearance on `range == 0`, out-of-bounds in Y, and altitude-vs-map-altitude collision via `Lb5d6_map_altitude_2x2`), and the robot-hit branch (`Lb7a7_potentially_hit_a_robot`) for collision-footprint/ordering evidence.
- Record each finding with the exact disassembly label/line as citation (following this milestone's own "record exact labels/constants/branches used as evidence" requirement) directly in `_specs/open-questions.md` §8.
- Resolve as far as evidence supports: projectile advance cadence (each bullet updates once per `Lb0ca_update_robots_bullets_and_ai` game cycle — same cadence as robot movement's own per-cycle model that issue #61 already mapped onto ticks at "1 cycle = 4 ticks"), cells advanced per update (2 cells, matching the game's coordinate-doubling convention already established for robot X), whether cannon/missile/phaser share speed (yes — only range differs, not per-update advance), collision footprint (2x2 map-pointer neighborhood check per `Lb77c_continue`'s eight-cell scan), and termination rule (range-exhaustion `BULLET_STRUCT_RANGE == 0`, Y-out-of-bounds, or collision — X is bounded by map-edge fences per the disassembly's own comment, not by explicit code).
- Explicitly flag what remains unresolved after tracing (e.g. exact real-world "mile" scaling for the disassembly's raw range-5/7 figures vs. this project's already-locked 20/28/20-cell defaults — these are different scales/eras of the port and must not be silently reconciled without an explicit note) and define the minimal configurable policy surface Task 4 needs for anything still open.
- Update `_specs/milestones/06-combat-damage-victory.md`'s "Fidelity research boundaries → Projectile mechanics" section and `_specs/open-questions.md` §8 to reflect exactly what got resolved vs. what remains explicitly configurable/non-canonical.

Acceptance criteria:
- Every canonical projectile behavior claimed resolved cites a specific disassembly label.
- No browser viewport dimension is ever proposed as authoritative.
- Remaining ambiguity (if any) is written down explicitly, not silently guessed.
- Task 4 can implement without inventing undocumented mechanics.

This issue does not block Task 4's architecture — only blocks Task 4's fidelity-complete closure and Task 11's final scenario language about exact Spectrum fidelity.

---

### Task 4: Deterministic normal projectile simulation and collision (issue #73)

Depends on: Task 1 (#70), Task 2 (#71) merged. Should incorporate Task 3's (#72) findings; may proceed on explicitly configured defaults if #72 is only partially resolved when this task starts.

Scope:
- In `combat.py`, define `Projectile` (frozen/slotted dataclass): `id: EntityId`, `owner: PlayerId`, `source_robot_id: EntityId`, `weapon: ModuleIdentity`, `x: int`, `y: int`, `z: int` (always `rules.normal_projectile_altitude`), `direction` (reuse the existing one-hot-style or dx/dy convention already used by `movement.RobotMoveRequest`/`commander_movement`), `travelled_cells: int`, `max_range_cells: int`, `created_tick: int`.
- Add `GameState.projectiles: tuple[Projectile, ...] = ()` following the exact `_canonical_robots`-style helper pattern in `state.py`: a `_canonical_projectiles` validator/sorter (unique `id`, sort by `id.value`), a `GameState.with_projectiles(...)` method, and a `GameState.projectile_for(id)` lookup — mirror `with_robots`/`robot_for` verbatim in structure.
- Implement `advance_projectiles(state, world, tick, rules, sequencer) -> tuple[GameState, tuple[Event, ...]]` as the per-tick advancement function `engine.py`'s `step` will call (new Step, placed after Step 2b/2c robot-move resolution and before order evaluation's engagement intent is consumed by Task 7, since a projectile in flight this tick should not block a fresh fire request from the same robot mid-flight-resolution — confirm exact placement against Task 7's needs during integration but do not let projectile advancement read post-order-evaluation state it hasn't produced yet).
- Per surviving-Task-3-evidence policy: advance position, increment `travelled_cells`, check termination in this fixed order (mirror `movement.validate_robot_move`'s "fixed order, same illegal case always same reason" discipline): (1) range exhausted (`travelled_cells >= max_range_cells`); (2) out of map bounds; (3) height-aware collision — query `collision.py`'s existing `component_vertical_range`/`robot_vertical_range` helpers against the projectile's fixed altitude (`rules.normal_projectile_altitude`) to determine hit-vs-passthrough, reusing M2's `structures.Component`/M4's `Robot.height` exactly as `collision.py` already exposes them; never re-derive height-range overlap math independently — call `collision.py`'s existing functions or extend that module with a narrowly-scoped `projectile_intersects(...)` helper if the exact call shape doesn't fit, following that module's own documented "reserved for later callers" precedent (see `commander_blocks_cell`'s docstring).
- On a robot-hit collision, do not apply damage here — return the identified target `Robot.entity_id` to Task 6's damage layer via the event/result Task 6 defines; this task's job ends at "collision identified, channel released."
- On every terminal path (range/bounds/static-collision/robot-hit/source-destroyed-mid-flight), clear the source robot's `active_projectile_id` exactly once (the field Task 2 added) — this is the acceptance criterion's "channel released exactly once" guarantee; write this as one shared internal `_terminate_projectile(...)` helper so no terminal branch can forget it, mirroring `movement.cancel_robot_move`'s "one cancellation point" discipline.
- Handle safe cleanup if the source robot is destroyed while its projectile is still in flight (Task 6 destroys the robot; this task's projectile-advancement pass must not crash on a dangling `source_robot_id` — treat a missing source robot as "channel already released, no owner to notify" and let the projectile continue/terminate normally under geometry rules alone).

Acceptance criteria:
- `Projectile.z` is always exactly `rules.normal_projectile_altitude` (10) regardless of firing robot height/weapon type.
- Collision uses `collision.py`'s existing M2/M4 geometry/height contracts, never independent height math.
- Termination never reads or depends on any frontend/viewport value — only `world`/`GameState`/`rules`.
- The source robot's channel clears exactly once per projectile lifecycle, verified by a test that fires, lets the projectile expire/collide, and asserts `active_projectile_id is None` afterward with no double-clear side effects.
- Multiple same-tick potential collisions resolve in one stable order (e.g. canonical `state.robots` `entity_id.value` order for candidate targets, `world.war_bases`/`factories`/`blockers` id order for static geometry) — no dict/set iteration deciding outcome.
- Tests (`engine/tests/test_combat_projectile.py`, new file) cover: clear flight to range expiry, static-obstacle collision at varying heights, robot-height collision (hit vs. passthrough over a short robot), out-of-bounds termination, source-robot-destroyed-mid-flight cleanup, and channel-release-exactly-once.

Out of scope: accuracy/damage resolution (#76), nuclear detonation (#78), rendering/effects.

---

### Task 5: Research and finalize Spectrum damage, accuracy, strength, and electronics rules (issue #74)

Fully independent research task — no engine code dependency, can run in Wave 1 alongside Tasks 1 and 3.

Scope:
- Trace `netherearth-annotated.asm`'s damage/strength routines: `ROBOT_STRUCT_STRENGTH` (initialized to `100` at `ld (iy + ROBOT_STRUCT_STRENGTH), 100`), the collision-branch damage calculation at `Lb7a7_potentially_hit_a_robot`/`Lb7c8_damage_calculation_loop` (`a = 60 - height - altitude`, right-shifted twice — i.e. `>> 2`, an unsigned integer floor-divide-by-4 — then added to itself `weapon_type` times via a `djnz` loop, which is exactly "base × multiplier" with cannon/missile/phaser = 1/2/3 additional adds on top of the base, i.e. multiplier values 2/3/4 as already locked, confirming integer semantics as: `base = (60 - robot_height - altitude) >> 2` then `damage = base * multiplier` with no rounding beyond the initial `>> 2` floor), and destruction (`ROBOT_STRUCT_STRENGTH` reaching `<= 0`, with a negative-strength "blink before removal" grace state per `Lb0fa_robot_update`/`Lb116_robot_destroyed`).
- Record: no separate hit/miss accuracy roll exists in the traced collision path — a projectile that geometrically collides with a robot always deals damage (no probability gate found in `Lb7a7_potentially_hit_a_robot`); damage is applied to one aggregate `ROBOT_STRUCT_STRENGTH` value only — no evidence of individual component damage; electronics' effect is range-only (`Lb6e1_not_missiles`/`Lb6e8_not_electronics`: `+1` to bullet range if the firing robot's electronics bit is set) — no separate damage-resistance modifier was found in the collision/damage branch.
- Document these findings with exact label citations directly in `_specs/open-questions.md` §9, and explicitly flag remaining unknowns: the disassembly's raw `>> 2` (floor toward zero for non-negative operands, which `a` always is here since height/altitude are bounded well under 60) is the truncation rule — record this as resolved; the exact starting-strength constant `100` should be recorded as a candidate default but cross-checked against whether this project's `robot_height`/`ground_height` scale matches the disassembly's raw units before being adopted verbatim (flag if scale reconciliation is needed, mirroring Task 3's mile/cell scale caution).
- Update `_specs/milestones/06-combat-damage-victory.md`'s "Fidelity research boundaries → Damage / accuracy / strength / electronics" section and `_specs/open-questions.md` §9.

Acceptance criteria:
- Every canonical damage/strength/electronics rule claimed resolved cites a specific disassembly label.
- No component-damage system or electronics resistance modifier is invented (evidence found none).
- Exact integer arithmetic (`>> 2`, i.e. floor division by 4, times an integer multiplier, no further rounding) is documented precisely enough for Task 6's regression tests.
- Remaining ambiguity (starting-strength scale reconciliation) is explicit, not silently resolved.

This blocks claiming exact-Spectrum-fidelity for the robot damage model; structural destruction/nuclear/victory work (Tasks 8/9) may proceed independently.

---

### Task 6: Robot hit, damage, strength, and destruction semantics (issue #76)

Depends on: Task 1 (#70), Task 4's (#73) hit boundary. Should incorporate Task 5's (#74) findings; may proceed on an explicitly configured default if #74 is only partially resolved.

Scope:
- Add `Robot.strength: int` (default matching Task 5's resolved starting value, e.g. `100`) to `robot.py`, following the exact same "add one field, extend `__post_init__`, extend every `with_*` copy method" pattern already used for `movement`/`order`/`active_projectile_id`. Add `Robot.destroyed: bool = False` alongside it (or represent "destroyed" purely by removal from `GameState.robots` — decide against `capture.py`'s existing "absence means gone" convention for structure ownership vs. `commander.py`'s explicit-mode-field convention; recommended: represent destruction by **removal from `state.robots`**, matching this codebase's existing "absence is the terminal state" pattern for `capture_progress`/`construction_sessions`, rather than a lingering `destroyed=True` zombie entity — this avoids every other subsystem needing a `if not robot.destroyed` guard).
- In `combat.py`, implement `calculate_base_damage(robot_height: int, ground_height: int) -> int` as one named function isolating the locked formula exactly per Task 5's resolved integer semantics (`(60 - (robot_height + ground_height)) // 4`, never negative-safe-guarded beyond what evidence supports — document the floor behavior inline).
- Implement `calculate_weapon_damage(weapon: ModuleIdentity, robot_height: int, ground_height: int, rules: EngineRules) -> int` applying `rules.cannon_damage_multiplier`/`missile_damage_multiplier`/`phaser_damage_multiplier` via a small lookup dict keyed by `ModuleIdentity`, mirroring `robot_build.MODULE_RESOURCE_CATEGORY`'s existing "one dict, one place" convention.
- Implement `apply_damage(state, target_robot_id, weapon, rules, tick, sequencer) -> tuple[GameState, Event | None]`: read `robot.height` and the ground height at `robot.x, robot.y` (reuse `collision.py`'s or `structures.py`'s existing component-height lookup — do not re-derive it), compute damage, subtract from `robot.strength`, and if `strength <= 0` route to `destroy_robot`.
- Create `engine/src/nether_earth/destruction.py` (new module — the milestone spec's "Destruction service" is explicitly meant to be shared by both robot and structure destruction and by both projectile damage and nuclear effects, so it deserves its own module rather than living inside `combat.py`) implementing `destroy_robot(state, entity_id, tick, rules, sequencer) -> tuple[GameState, Event | None]`:
  - Guard idempotency: if `entity_id` is not in `state.robots`, no-op (already destroyed) — return `state` unchanged, no event.
  - Remove the robot from `state.robots` (releases world occupancy for free, since `movement.folded_robot_occupancy` derives occupancy from live `state.robots` — no separate occupancy-release call needed, exactly as `capture.py`'s destruction-adjacent code already benefits from this derived-not-stored pattern).
  - If the robot had an in-flight `movement` transition, no explicit reservation release call is needed either, for the same derived-projection reason (`reservations.reservations_from_state` derives from `state.robots`) — removing the robot from the tuple already clears it.
  - If the robot owned an `active_projectile_id`, remove the corresponding `Projectile` from `state.projectiles` too (a destroyed robot's in-flight projectile has no owner to notify on termination, but the projectile itself should not silently orphan-linger — decide and document: recommended is to let the projectile continue independently per Task 4's "missing source robot" handling, NOT delete it here, since the projectile is still a physical object in flight per the locked rules; only clear the *channel*, which is moot once the robot itself is gone).
  - Clear any `capture_progress`/`structure_ownership`-adjacent reference naming this robot (`state.capture_progress` entries where `robot_id == entity_id` must be dropped, mirroring `capture.py`'s own interruption-resets-progress-to-zero logic — call or replicate that exact removal).
  - If a commander is currently `DOCKED` to this robot (`commander.docked_robot_id == entity_id`), force an undock to `FREE` at the robot's last position/height-appropriate altitude (reuse `docking.py`'s existing transition shape — do not invent new commander-safety rules; the commander itself is never damaged, only relocated to a safe `FREE` state, matching the locked "docking safety semantics" requirement).
  - Emit a new `RobotDestroyedEvent(entity_id, owner, x, y, tick)` in `destruction.py` or `combat.py` (`events.Event` subclass, following every other event's exact shape convention).
- Guard against double-destruction: calling `destroy_robot` twice on the same already-removed id must be a safe no-op (the idempotency guard above), matching `movement.cancel_robot_move`'s "cancelling twice is a safe no-op" precedent.

Acceptance criteria:
- `calculate_base_damage`/`calculate_weapon_damage` are the only place this arithmetic exists; both are unit-tested against Task 5's documented worked examples (e.g. the module docstring's own example: weakest robot height 13, phaser damage = `((60-13)//4)*4 = 44`).
- Damage multipliers default to 2/3/4 exactly, sourced from `EngineRules`.
- Robot height is read from `Robot.height` (M4's `robot_stack.derive_height`) — never a second height computation.
- `destroy_robot` leaves no stale entry in `state.robots`/`state.capture_progress`/reservations (derived, so automatically clean)/any docked-commander reference.
- Commander is never damaged/destroyed even when the robot it was docked to is destroyed underneath it.
- Repeated identical damage sequences produce identical final strength/destruction outcome and event sequence (property test or explicit repeated-run assertion, matching this codebase's existing determinism-test style).
- Tests (`engine/tests/test_combat_damage.py`, new file) cover: damage arithmetic at multiple height/multiplier combinations, survival vs. exact-threshold destruction, double-destruction no-op, docked-commander safe relocation, capture-progress cleanup, and full-cycle replay via `replay.run_fixture`.

Out of scope: structure destruction (#78), nuclear area selection (#78), target selection/navigation (M5, unchanged).

---

### Task 7: Consume M5 engagement intent for autonomous firing (issue #77)

Depends on: M5 `orders.EngagementIntent`/`engagement_intent_for` (already implemented, M5.5/#64), Task 2 (#71), Task 1 (#70).

Scope:
- In `engine.py`'s `step`, add a new sub-step (placed after Step 2b2's existing `orders.evaluate_orders`/`apply_order_evaluations` call, which already produces `OrderEvaluation.intent: EngagementIntent | None` per robot every tick) that, for every `OrderEvaluation` carrying a non-`None` `intent`, re-validates only the combat-time facts that may have changed since M5 computed the intent: source robot still exists/alive (`state.robot_for(intent.robot_id)`), target still exists/valid (re-look-up by `intent.target_id`/kind against current `state`/`effective_world`), the named weapon is still fitted (it always is — build doesn't change post-launch — but check defensively), range (`intent.distance_cells` against the appropriate `rules.*_range_cells` + `electronics_range_bonus_cells` if the robot carries `ModuleIdentity.ELECTRONICS`), and channel availability (`active_projectile_id is None` for normal weapons).
- Select exactly one weapon from `intent.weapons` (already in `robot_build.py`'s canonical cannon/missile/phaser/nuclear order) to fire — pick the first one that is both fitted and in range under the check above (deterministic, no randomness); if none qualify, no fire this tick (not an error, not a fallback order change — M5's order lifecycle is untouched, only this tick produces no combat action).
- Convert a qualifying intent into the exact same `FireRequest`/`validate_fire` call Task 2 built for direct control — do not write a parallel firing path. If the target is a robot, aim/target shape matches whatever Task 2 defined (target `x`/`y` or target id, per Task 2's `FireRequest` shape); if the target is a structure and the weapon is nuclear, route through Task 8's detonation entry point via the identical `FireRequest`/`FireResult` boundary.
- Do not re-run navigation, do not re-select a different target, and do not touch `Order`/`OrderStatus` lifecycle — this task only consumes the intent boundary M5 already produces and either fires or silently does nothing this tick.

Acceptance criteria:
- Autonomous fire and direct fire share the identical `validate_fire`/`FireResult` code path — verified by a test asserting no `combat.py` function branches on "was this autonomous or direct".
- A target that disappeared/moved out of range/became invalid between M5's intent computation and this step's re-validation produces deterministic no-fire, never a stale-target hit.
- Single-channel gating applies identically to autonomous fire (a robot with an active projectile does not fire again via engagement intent either).
- Replay reproduces the exact same autonomous fire/no-fire decision every run (test via `replay.run_fixture` with a Search & Destroy order in range of a target).
- Tests (`engine/tests/test_combat_autonomous.py`, new file) cover: Stop & Defend intent firing, Search & Destroy intent firing, target destroyed mid-tick before fire, out-of-range intent producing no fire, channel-occupied intent producing no fire.

Out of scope: target ranking/selection (M5, unchanged), navigation/repositioning (M5, unchanged), order lifecycle transitions beyond consuming the intent.

---

### Task 8: Nuclear detonation and structure destruction (issue #78)

Depends on: Task 1 (#70), Task 6's (#76) `destruction.py` module/`destroy_robot` (or an agreed shared destruction interface built in parallel with Task 6 if scheduling requires — coordinate the module boundary early since both write to `destruction.py`).

Scope:
- In `combat.py`, extend `validate_fire`'s nuclear branch (from Task 2) with `validate_nuclear_fire(request, state, ...) -> FireResult`: source robot exists/alive, requester controls it, `ModuleIdentity.NUCLEAR` is in `robot.build.weapons`. No channel check (nuclear is not the normal-projectile channel).
- Implement `execute_nuclear_detonation(state, world, carrier_id, tick, rules, sequencer) -> tuple[GameState, tuple[Event, ...]]` in `destruction.py`:
  - Read the carrier robot's `(x, y)` before destroying it.
  - Enumerate every robot in `state.robots` (excluding the carrier itself, handled separately) within `rules.nuclear_radius_cells` using Manhattan distance (matching the codebase's existing `orders._manhattan` convention — reuse or replicate that exact helper; do not invent Euclidean/Chebyshev distance) — sort candidates by `entity_id.value` for deterministic destruction-event order.
  - Enumerate every `world.factories`/`world.war_bases` (via `capture.effective_world` so current runtime ownership is visible) whose nearest occupied cell (`structures.occupied_cells`) is within the radius — sort by `structure.id.value`.
  - Destroy the carrier first (always, unconditionally, via `destruction.destroy_robot`), then every eligible robot in canonical id order (via the same `destroy_robot`), then every eligible structure in canonical id order (via a new `destroy_structure` — see below). This fixed order (carrier → robots → structures) is this task's own documented deterministic-ordering decision, matching the milestone's "define stable rejection/ordering" requirement.
  - Commanders are never in `state.robots`/`world.factories`/`world.war_bases` — they are structurally excluded already; no special-case skip needed, just don't add one.
- Implement `destroy_structure(state, structure_id, structure_kind, tick, rules, sequencer) -> tuple[GameState, Event | None]` in `destruction.py`:
  - Add `GameState.structure_destruction: tuple[EntityId, ...] = ()` (a new field, following the exact `structure_ownership`/`capture_progress` convention: canonical sorted tuple, a `_canonical_structure_destruction` validator, a `with_structure_destruction` method, a `structure_destroyed(structure_id) -> bool` query) — this is the `GameState`-attached override analogous to `capture.StructureOwnership`, since `WorldMap` is never mutated at runtime (see Global Constraints).
  - Idempotency guard: if `structure_id` is already in `state.structure_destruction`, no-op.
  - Add `structure_id` to `state.structure_destruction`.
  - Clear any `state.capture_progress`/`state.structure_ownership` entry naming this structure (a destroyed structure can no longer be captured or owned — remove both, mirroring `capture.py`'s own removal-on-interruption pattern).
  - `capture.effective_world`/`effective_owner` and every other structure-ownership-aware subsystem (`resource_production.apply_daily_production`, `heli_pad`/`construction_session` entry, `orders.select_capture_target`/`select_destroy_target`) must now also skip a destroyed structure — extend `capture.effective_world` (or a new wrapper `destruction.effective_world` that layers on top of `capture.effective_world`) so a destroyed war base/factory is excluded from `world.war_bases`/`world.factories` for every downstream consumer, exactly as ownership overrides are already layered without mutating the source map. This is the single integration point other M6 tasks and `engine.py`'s Step 7/8/9 must be updated to call instead of the raw `capture.effective_world` once this task lands — flag this explicitly in the PR description since it touches `engine.py`.
  - Emit `StructureDestroyedEvent(structure_id, structure_kind, tick)` (new `Event` subclass in `destruction.py`, mirroring `capture.StructureCapturedEvent`'s shape).
- Enforce structurally that `destroy_structure` is only ever reachable from the nuclear detonation path — `apply_damage`/normal-weapon code (Task 6) must never call it; this is a code-review-verified invariant, not a runtime check, since Task 6's damage path only ever operates on `Robot` targets by construction (weapon capability for structures is nuclear-only, already enforced by `orders._STRUCTURE_CAPABLE_WEAPONS`).

Acceptance criteria:
- Radius uses `rules.nuclear_radius_cells` (16) exactly, Manhattan-distance measured, boundary/metric choice documented inline (flag as configured-not-independently-verified if Task 3/#72-adjacent evidence doesn't cover nuclear radius shape specifically — the disassembly evidence gathered in Task 3's research pass, e.g. `Lb99f_fire_nuclear_bomb`'s "maximum distance in each axis 7, maximum sum of distances 10" shape for war bases, suggests the *original* radius metric is not simple Manhattan distance; this task must decide whether to adopt that shape or keep the milestone's locked simple-radius description and document the choice explicitly per the "Fidelity note" in issue #78).
- Carrier is always destroyed exactly once on a valid detonation.
- Every eligible robot/factory/war base inside the boundary is destroyed exactly once; outside-boundary entities are untouched (boundary test with entities placed exactly at/just-outside the radius).
- Commanders are never in the affected set (structural, verified by test that a commander at the epicenter survives).
- `destroy_structure` cleans capture/ownership state with no stale references (test: a structure with active `CaptureProgress` gets destroyed mid-capture, progress record disappears).
- Normal weapons cannot invoke `destroy_structure` (test: attempt to route a cannon/missile/phaser hit against a factory/war base and assert it is rejected/impossible at the `orders`/`combat.py` boundary, not merely "untested").
- Tests (`engine/tests/test_combat_nuclear.py`, new file) cover: carrier-only detonation with nothing else in range, mixed robot+factory+war-base detonation, radius boundary inclusion/exclusion, capture-progress cleanup, replay determinism of the affected-set enumeration/event order.

Out of scope: normal projectile simulation (#73), frontend explosion effects/sound.

---

### Task 9: Unify war-base destruction/capture victory evaluation (issue #79)

Depends on: M5 `capture.py`'s existing war-base-capture-triggers-victory hook (already wired in `engine.py`'s Step 2d), Task 8 (#78).

Scope:
- Do not create a new victory function — `victory.evaluate_victory` (already implemented) is the single authority; this task's job is wiring, not new logic.
- In `engine.py`'s `step`, extend the existing Step 2d pattern (currently: after `capture.advance_capture`, if any `NeutralStructureAcquiredEvent`/`StructureCapturedEvent` names a war base, call `victory.evaluate_victory`) to also trigger after Task 8's nuclear detonation step: if `execute_nuclear_detonation` destroyed any war base (`StructureDestroyedEvent` with `structure_kind is CapturableStructureKind.WAR_BASE`), call `victory.evaluate_victory` against the post-destruction `effective_world` (using Task 8's extended effective-world-that-excludes-destroyed-structures) in that same step.
- `victory.evaluate_victory` already reads `world.war_bases` and only counts entries still present with a live owner; ensure Task 8's `destroy_structure`-aware effective-world wrapper is what gets passed in here (a destroyed war base must not be counted as anyone's, satisfying the loss condition naturally — verify `evaluate_victory`'s existing `owned_counts` logic handles "war base entirely absent from the effective world" correctly, since it currently iterates `world.war_bases` directly; if Task 8's wrapper filters destroyed structures out of the returned `WorldMap.war_bases` tuple entirely, `evaluate_victory` needs zero code changes — confirm this during implementation and only patch `victory.py` if the wrapper shape doesn't naturally produce that).
- If a single nuclear detonation destroys multiple war bases in one step (e.g. the opponent's last two), ensure `evaluate_victory` is called exactly once after all of that step's destructions are applied (not once per destroyed war base) — call it once, after Task 8's full affected-set loop completes, mirroring the existing Step 2d's "call once after the batch" pattern.
- `victory.VictoryEvent` is already "emitted at most once per `evaluate_victory` call" by construction (it either returns one event or `None`) — no additional de-duplication logic needed as long as callers only invoke it once per authoritative step, which the above wiring guarantees.

Acceptance criteria:
- `victory.evaluate_victory` remains the only victory function in the engine — no `combat.py`/`destruction.py` competing definition.
- Final-war-base destruction produces the `VictoryEvent` on the exact same tick/step the destruction happens.
- Final-war-base capture (M5, unchanged) still produces it on the exact same step it always did.
- A single nuclear detonation destroying multiple war bases (including both remaining war bases of the losing side at once) still emits exactly one `VictoryEvent`.
- Tests (`engine/tests/test_engine_combat_integration.py` or extend `test_victory.py`) cover: capture-triggered loss (already exists from M5, must remain green), nuclear-destruction-triggered loss, multi-war-base-single-detonation loss producing one event, and replay ordering of the event relative to the destruction events that caused it.

Out of scope: match runtime cleanup/network notification (M7), new victory conditions.

---

### Task 10: Integrate combat state into snapshots, events, and replay (issue #80)

Depends on: Tasks 2 (#71), 4 (#73), 6 (#76), 7 (#77), 8 (#78), 9 (#79) merged. Reflects Tasks 3/5's (#72/#74) research findings as implemented in `rules.py` by that point.

Scope:
- Extend `snapshot.py`'s `to_snapshot`/per-entity snapshot helpers, following the file's own strict "new keys appended after existing keys, never reordered" convention (see its module docstring's precedent for `movement`/`order` additions):
  - `_robot_snapshot`: append `active_projectile_id` and `strength` after the existing `order` key.
  - Add `_projectile_snapshot(projectile) -> dict` and a top-level `"projectiles"` key in `to_snapshot`'s returned dict (appended after `capture_progress`, the current last key).
  - Add a top-level `"structure_destruction"` key serializing `state.structure_destruction` (list of structure id strings), appended last.
  - If Task 5's research concluded any hit/accuracy roll consumes the match RNG (current evidence from Task 5 suggests it does not — no probability gate found), no RNG-state snapshot key is needed beyond the existing `seed` field per `rng.py`'s existing "derive fresh from seed+tick" convention (matching `reservations.py`'s `derive_contention_seed` precedent); if Task 5's findings changed by this point and a roll *is* required, add the minimal per-tick-derived seed input the same way `reservations.py` already does — never a stored mutable RNG object on `GameState`.
- Extend `replay.py`'s `ReplayFixture`/`run_fixture` only if a new constructor-level need appears (e.g. an `initial_projectiles` field analogous to `initial_robots`) — check whether any Task 11 scenario needs to seed a match with an in-flight projectile at tick 0; if not, no `replay.py` changes are needed since combat state emerges entirely from in-tick fire commands.
- Verify every new event type from Tasks 2/4/6/7/8/9 (`RobotDestroyedEvent`, `StructureDestroyedEvent`, plus any `FireAcceptedEvent`/`FireRejectedEvent`/`ProjectileHitEvent` Task 2/4 introduced) follows `events.py`'s existing `Event`/`EventSequencer`/`order_events` contract exactly — every event assigned a sequence via the tick's shared `EventSequencer`, no ad hoc `sequence=0` outside isolated single-event tests.
- Run the full existing `engine/tests/` suite and fix any snapshot-shape assertions broken by the new appended keys (search for any test asserting `to_snapshot(...)`'s exact key set/order and extend rather than break them, following `snapshot.py`'s own stated additive-only precedent).

Acceptance criteria:
- Snapshot/restore of a `GameState` mid-projectile-flight, when driven back through `engine.step`, produces the same subsequent collision/damage/destruction outcome as the un-snapshotted original run.
- `active_projectile_id` and `state.projectiles` both restore correctly and clear on the same terminal tick in a restored run as in the original.
- `Robot.strength`/robot-destruction (removal from `state.robots`) restores exactly.
- Nuclear affected-set enumeration and event order replay identically after a snapshot/restore round-trip.
- `VictoryEvent`/match result restore/replay deterministically.
- Every existing M1-M5 test in `engine/tests/` remains green (no regression from the new appended snapshot keys or the new `engine.step` sub-steps).
- Verification: snapshots taken before fire, mid-projectile-flight, immediately before a destruction-triggering hit, and immediately before nuclear/victory resolution, each round-tripped and re-run to assert identical subsequent state/events/hash.

Out of scope: M7 wire protocol schemas, M8 presentation/effects.

---

### Task 11: Deterministic combat, nuclear, and victory integration scenario (issue #81)

Final M6 gate. Depends on: Tasks 1-10 (#70-#80) all merged, plus M5's own integration scenario (#68, already merged) as the upstream fixture baseline.

Scope — build one deterministic fixture battlefield and one integration test module (`engine/tests/test_m6_integration.py`, following the exact pattern of `test_m4_integration.py`/`test_m5_integration.py`: a dedicated fixture YAML under `engine/tests/fixtures/`, e.g. `world_map_m6_integration.yaml`, laid out in independent non-overlapping "lanes" per the M5 fixture's own documented convention):
- Lane A: robots built with cannon, missile, phaser, electronics, and nuclear modules (M4 `RobotBuild`/`robot_launch`), verifying fitted-weapon validation and rejection of absent weapons (Task 2).
- Lane B: fire a normal projectile, assert a second normal shot is rejected while the channel is active, then assert it becomes available again after the projectile's lifecycle completes (Tasks 2/4).
- Lane C: verify `Projectile.z == 10` always, and configured range limits (20/28/20 cells) terminate flight correctly (Task 4).
- Lane D: clear-path vs. obstructed-path projectile termination against multiple static component heights and a robot of varying height (Task 4).
- Lane E: direct fire (a docked commander issuing a fire command through whatever direct-fire command Task 2 defined, mirroring `direct_control.DirectRobotMoveCommand`'s shape) and M5 autonomous Search & Destroy engagement both routed through the identical firing path (Tasks 2/7) — assert via a shared assertion helper that both produce the same `FireResult`/event shape for equivalent inputs.
- Lane F: apply repeated normal damage to a robot through survival and past the exact destruction threshold, verifying `destroy_robot`'s cleanup of occupancy/reservations/orders/capture references (Task 6).
- Lane G: a commander positioned near/on active combat geometry proven untargetable/undamaged/indestructible throughout (Tasks 2/6/8).
- Lane H: attempt (and assert rejection/impossibility of) normal weapons destroying a factory/war base (Task 8's structural enforcement).
- Lane I: detonate a nuclear weapon near a mix of robots/factories/war bases, verifying radius boundary inclusion/exclusion, carrier destruction, deterministic affected-set/event ordering, and cleanup (Task 8).
- Lane J: destroy the opponent's final owned war base (via nuclear detonation) and assert `VictoryEvent` fires on that exact authoritative step (Task 9).
- Snapshot/restore mid-projectile-flight partway through the scenario and assert the restored run reproduces the identical subsequent result (Task 10).
- Replay the entire scenario's identical initial state/seed/command stream repeatedly (following `test_m5_integration.py`'s existing repeated-replay-hash-equality pattern via `replay.run_fixture` + `snapshot.snapshot_to_json_string`) and assert byte-identical events/state/hash across runs.

Acceptance criteria:
- Every locked M6 product rule listed in this plan's Global Constraints is exercised at least once in this scenario.
- No test-only combat path bypasses `combat.py`/`destruction.py`/`collision.py`/M2-M5 contracts — the scenario drives everything through real `engine.step` calls, matching `test_m5_integration.py`'s own "everything runs through the real `engine.new_game`/`engine.step` pipeline" discipline.
- All outcomes are deterministic across repeated runs (explicit hash-equality assertion).
- If Task 3 (#72) or Task 5 (#74) left any mechanic partially unresolved, this scenario's test names/comments explicitly name the configured policy/default in use and do not claim exact Spectrum fidelity for it.
- Full engine test suite (`engine/tests/` in its entirety) remains green.

Close this task only when Tasks 1-10 are merged and this integration/replay scenario passes repeatedly.
