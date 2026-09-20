# Milestone 6 — Combat, Damage, Destruction & Victory

## Goal

Implement the authoritative deterministic combat layer: weapon eligibility and firing, the single-active-normal-projectile channel, projectile simulation/collision/lifecycle, robot hit/damage/strength resolution, autonomous engagement consumption, nuclear detonation, structure destruction, and victory evaluation.

Milestone 6 consumes contracts established by earlier milestones rather than redefining them:

- M2 owns world geometry, component/cell heights, structure identity, interaction metadata, and occupancy;
- M3 owns commander physical collision and the rule that commanders are indestructible/untargetable;
- M4 owns canonical robot/module identity, construction cost, component stack, and robot height;
- M5 owns robot movement, target selection, navigation, capture, and engagement intent.

M6 must not create a second weapon/module catalog, a second target-selection system, or viewport-dependent gameplay rules.

## Planning status

This milestone is intentionally specified before M5 implementation is complete.

Implementation may start only where upstream contracts are stable. Research tasks may start earlier. Any task that depends on unresolved projectile/damage fidelity must preserve a clean rule interface and must not silently invent Spectrum behavior.

## Spec references

- `_specs/functional-spec.md` combat, damage, nuclear, and victory sections
- `_specs/technical-spec.md` weapon/projectile/damage/nuclear/victory sections
- `_specs/open-questions.md` §8 — projectile mechanics (partially resolved)
- `_specs/open-questions.md` §9 — damage/accuracy/electronics (partially resolved)
- `_specs/open-questions.md` §3 — miles/grid conversion (resolved)
- `https://github.com/santiontanon/netherearth-disassembly`

## Start dependencies

Architecture/contracts can be prepared when:

- M4 canonical module/build/height contract is stable;
- M5 engagement-intent contract is stable enough to consume without duplicating target selection.

Full completion requires M5 movement/orders/navigation/capture integration complete.

## Deliverable

A pure-engine combat subsystem that:

1. receives explicit human fire requests or M5 autonomous engagement intent;
2. validates fitted weapon, target/range/state, and firing-channel legality;
3. creates/advances authoritative normal projectiles or executes a nuclear detonation;
4. resolves world/height collision deterministically;
5. computes hit/damage/strength using centralized evidence-backed rules;
6. destroys robots/structures according to locked destruction rules;
7. clears occupancy/references/projectiles atomically;
8. evaluates victory in the same authoritative step when war-base existence/ownership reaches the loss condition;
9. emits deterministic events and is fully snapshot/replay-safe.

---

## Locked combat rules

### Canonical weapon identities

Consume the M4 weapon identities exactly:

- cannon
- missile
- phaser
- nuclear

Construction cost/resource metadata remains owned by M4. M6 adds/consumes combat metadata keyed by those identities.

### Miles-to-cells conversion

Locked shared conversion:

```text
1 mile = 2 cells
1 cell = 0.5 miles
```

Canonical default ranges/effects:

```text
cannon base range     = 10 miles = 20 cells
missile base range    = 14 miles = 28 cells
phaser base range     = 10 miles = 20 cells
electronics range add =  3 miles =  6 cells
nuclear radius        =  8 miles = 16 cells
```

M6 must call the same shared conversion/helper used by M5. No duplicate conversion arithmetic.

### Normal projectile altitude

Cannon, missile, and phaser projectiles use authoritative altitude:

```text
normal_projectile_altitude = 10
```

This is the original Spectrum default and must live in centralized game-rule configuration.

Normal projectile altitude:

- is identical for cannon/missile/phaser;
- does not depend on firing robot height;
- does not depend on which normal weapon fired;
- participates in height-aware collision against authoritative world/entity geometry.

### Single active normal projectile channel

Per robot:

- cannon/missile/phaser share one normal-projectile channel;
- a robot may have at most one active normal projectile;
- another normal fire request is rejected while that projectile remains active;
- there are no independent per-weapon cooldown channels unless later authoritative evidence explicitly requires one.

Nuclear detonation is separate from this travelling-projectile channel.

### Commander interaction

Commanders:

```text
can_be_targeted = false
can_take_damage = false
can_be_destroyed = false
```

Combat may use commander geometry as physical world geometry only where already defined by M3. M6 must never damage or destroy a commander.

### Damage formula

Locked normal-weapon base-damage rule:

```text
base_damage = (60 - (robot_height + ground_height)) / 4
```

Configured default weapon multipliers:

```text
cannon  = 2
missile = 3
phaser  = 4
```

The implementation must isolate this behind a named engine rule function. Exact integer arithmetic/truncation semantics remain fidelity research until verified.

### Nuclear destruction

Nuclear weapon behavior is a special discrete detonation, not a normal projectile.

Locked default radius:

```text
8 miles = 16 cells
```

On valid detonation:

- the carrier robot is destroyed;
- eligible robots inside the authoritative radius are destroyed;
- eligible factories inside the authoritative radius are destroyed;
- eligible war bases inside the authoritative radius are destroyed;
- factories and war bases cannot be destroyed by cannon/missile/phaser;
- commander entities are unaffected;
- destruction and victory consequences resolve deterministically in the same authoritative engine step/event sequence.

The exact radius boundary metric/inclusion rule must be evidence-backed or explicitly centralized/configurable if not yet verified.

### Victory

Locked loss condition:

```text
owned_warbase_count(player) == 0 -> player loses
```

Victory must be evaluated after every authoritative event that can change war-base ownership or existence, including:

- M5 war-base capture;
- M6 nuclear destruction.

M6 must reuse the same victory rule/function used by M5 capture integration, not create a competing definition.

---

## Proposed engine contracts

Names are illustrative; exact code naming may follow the implemented engine style.

### Combat rule data

One centralized rule object should expose combat-facing defaults, for example:

```python
CombatRules:
    normal_projectile_altitude: int = 10
    cannon_range_cells: int = 20
    missile_range_cells: int = 28
    phaser_range_cells: int = 20
    electronics_range_bonus_cells: int = 6
    nuclear_radius_cells: int = 16
    cannon_damage_multiplier: int = 2
    missile_damage_multiplier: int = 3
    phaser_damage_multiplier: int = 4
    # research-owned fields below once verified:
    projectile_step_ticks: ...
    projectile_step_distance: ...
    hit_accuracy_rules: ...
    electronics_accuracy_modifier: ...
    electronics_resistance_modifier: ...
```

Unverified values must not be assigned fake "Spectrum" defaults.

### Fire request/result boundary

Conceptually:

```python
FireRequest:
    robot_id
    weapon_type
    target/direction as required by verified control model

FireResult:
    accepted
    rejection_reason | None
    projectile_id | None
    events
```

Validation must be engine-owned and deterministic.

Expected rejection classes include at least:

- robot does not exist / destroyed;
- requester does not control robot;
- weapon not fitted;
- target/aim invalid under resolved rules;
- normal projectile channel already occupied;
- target/range invalid where range is checked at fire time;
- nuclear-specific invalid state.

### Normal projectile state

Conceptually:

```python
Projectile:
    id
    owner_player_id
    source_robot_id
    weapon_type
    x
    y
    z = normal_projectile_altitude
    direction / path state
    travelled_range
    max_range
    created_tick
```

Only fields required by authoritative rules should be stored. Derived values should remain derived when practical.

### Combat event order

For a projectile/hit step, preserve a stable deterministic ordering such as:

```text
validate fire
-> create projectile / reserve channel
-> advance projectile on eligible ticks
-> determine collision/termination
-> resolve hit/accuracy if applicable
-> calculate/apply damage
-> destroy entity if threshold reached
-> clear occupancy/references/channel
-> evaluate victory if war-base state changed
-> emit ordered events
```

Exact implementation may differ, but ordering must be explicit and tested because replay hashes/events depend on it.

### Destruction service

Destruction should be centralized so projectile damage and nuclear effects cannot leave different cleanup semantics.

A robot destruction path must consider:

- authoritative destroyed/alive state;
- world occupancy release;
- movement reservation release;
- active projectile ownership/channel semantics;
- current order/target/capture references;
- docked commander state if applicable (must resolve according to already locked commander safety rules, without damage);
- replay/event emission.

A structure destruction path must consider:

- structure destroyed/existence state;
- physical occupancy/components;
- ownership counts;
- capture state cleanup;
- production eligibility;
- victory evaluation for war bases.

No task should independently "delete an entity" without using the shared destruction semantics.

---

## Fidelity research boundaries

### Projectile mechanics — unresolved details

Issue #72 (M6.3) traced the Spectrum disassembly (`santiontanon/netherearth-disassembly`,
`netherearth-annotated.asm`) and resolved most of the items below directly
from code evidence; see `_specs/open-questions.md` §8 for the full citation
trail. Resolved items are removed from this list; remaining items are
refined to state precisely what is still unknown.

Resolved by issue #72 (see `_specs/open-questions.md` §8 for citations —
not repeated here):

- projectile advance cadence relative to original game cycles — resolved:
  once per `Lb0ca_update_robots_bullets_and_ai` invocation, unthrottled
  (unlike robots, which gate movement behind a per-robot cycle-skip
  counter);
- cells advanced per update on the X axis — resolved: 2 raw units (1
  logical cell under the established coordinate-doubling convention) per
  update, one axis at a time;
- whether cannon/missile/phaser share speed — resolved: yes, only
  `BULLET_STRUCT_RANGE` differs by weapon type; movement code is identical;
- projectile collision footprint/profile and collision ordering — resolved:
  an ordered, first-hit-wins scan of up to 9 cells (3x3 neighborhood, not a
  flat 2x2 or generic 8-cell shape), row-by-row with map-edge short-circuits;
- what terminates projectile life in the original — resolved: range
  exhaustion, Y-axis out-of-bounds, altitude/height collision via a 2x2
  max-altitude probe, or robot-strike collision (four independent paths, all
  cited in `_specs/open-questions.md` §8);
- whether maximum range or visible-screen departure is the primary expiry
  rule — resolved: range exhaustion (a per-cycle decrement counter) and
  in-bounds/collision checks are the only expiry mechanisms found; there is
  no visible-screen-departure check in the traced code, and the disassembly
  explicitly relies on physical map-edge fence objects (its own code
  comment) rather than an explicit X-bounds check.

Still unresolved after issue #72's pass (refined per findings — see
`_specs/open-questions.md` §8's "Still open" and "Recommended engine policy
surface" for the precise scope Task 4 should treat as configurable/
non-canonical):

- exact Y-axis coordinate-doubling status: whether the ±2-per-update Y step
  represents the same "2 raw units = 1 logical cell" convention as X, given
  Y's much smaller map extent (`MAP_WIDTH = 16` vs. `MAP_LENGTH = 512`) —
  not independently verified by this pass;
- how projectile Z=10 intersects robots of varying stack height and static
  components — the traced altitude-collision check (`Lb5d6_map_altitude_2x2`)
  folds terrain, robots, and decorations into one "map altitude" figure and
  does not distinguish object categories; no separate per-component
  collision rule was found;
- whether projectiles collide with all static objects or only particular
  map/object classes — resolved as "all objects at or above the probed
  altitude, uniformly," per the same altitude-folding evidence above; there
  is no object-class-specific collision branch in the traced code;
- how original screen-relative logic should map to the browser/world model
  without making viewport size authoritative — no screen-relative expiry
  logic was found in the traced code at all (see the resolved "primary
  expiry rule" item above), so this item is now moot rather than open;
- the raw disassembly range constants (`WEAPON_RANGE_DEFAULT = 5`,
  `WEAPON_RANGE_MISSILES = 7`, +1 for electronics) describe the executable's
  actual per-shot travel-cycle limit and are a different, non-interchangeable
  figure from this project's locked mile-derived `cannon_range_cells` /
  `missile_range_cells` / `phaser_range_cells` / `electronics_range_bonus_cells`
  defaults (which derive from the instruction manual's stated mile ranges).
  This discrepancy is recorded, not reconciled — see
  `_specs/open-questions.md` §8's "Raw disassembly range figures vs. this
  project's locked mile-derived ranges" note. The locked `rules.py` values
  are not to be changed by this finding.

Until fully resolved, projectile architecture may be implemented behind
configurable policies per `_specs/open-questions.md` §8's "Recommended
engine policy surface" note, but browser viewport dimensions must never
influence engine projectile lifetime.

### Damage / accuracy / strength / electronics — unresolved details

Issue #74 (M6.5) traced the Spectrum disassembly (`santiontanon/netherearth-disassembly`,
`netherearth-annotated.asm`) and resolved all items below directly from code
evidence; see `_specs/open-questions.md` §9 for the full citation trail.
Resolved items are removed from this list.

Resolved by issue #74 (see `_specs/open-questions.md` §9 for citations —
not repeated here):

- exact integer arithmetic for `(60 - (robot_height + ground_height)) / 4` —
  resolved: two consecutive `srl a` instructions on a non-negative operand,
  i.e. unsigned floor-division by 4, no separate rounding step;
- order of multiplier application and truncation — resolved: base damage is
  computed once (`d`), then accumulated via `b`-times repeated addition
  (`b` = bullet type 1/2/3), giving `base * (b + 1)` = `base * 2/3/4` for
  cannon/missile/phaser — arithmetically identical to the already-locked
  multipliers, no further rounding after the initial floor-division;
- exact robot strength representation and starting strength — resolved: a
  single signed byte (`ROBOT_STRUCT_STRENGTH`), initialized to exactly `100`
  at both robot-spawn sites; see §9's scale-reconciliation note (no unit
  conversion needed, unlike the mile/cell range discrepancy in §8, but Task 6
  should confirm `robot_height`/`ground_height` inputs are wired on the same
  raw 13–38 disassembly scale before treating `100` as final);
- hit/miss probability calculation — resolved: none exists. No RNG call
  (`Ld358_random` or otherwise) appears anywhere in
  `Lb7a7_potentially_hit_a_robot`; a geometric collision always deals damage;
- range contribution to hit probability — moot, since no hit-probability
  roll exists at all in the traced collision-damage path;
- whether a successful projectile collision can still miss through an
  accuracy roll — resolved: no, there is no accuracy roll after collision;
- whether components are damaged individually or only aggregate robot
  strength — resolved: aggregate only. The full `ROBOT_STRUCT_*` layout (16
  bytes) has exactly one strength field and no per-piece/per-weapon health
  field;
- electronics damage-resistance modifier — resolved: none found. The
  damage-calculation path was searched specifically for a second electronics
  check beyond the already-documented firing-time range bonus (§8's
  `Lb6d6_weapon_fire` finding) and none exists;
- electronics accuracy/effective-range behavior beyond the already locked
  +3-mile nominal range statement — moot, since no accuracy roll exists to
  be affected, and range's only disassembly-verified effect remains the
  already-documented +1 raw-unit bonus to `BULLET_STRUCT_RANGE`;
- any weapon-specific accuracy behavior — resolved: none found; weapon type
  only selects the damage multiplier (and, at fire time, projectile range),
  never an accuracy factor.

No component-damage system or electronics resistance modifier was invented;
the evidence found none, confirming the pre-existing constraint on this
section.

---

## Workstreams

### 1. Canonical combat metadata and rule interfaces

Extend/associate combat metadata with M4 canonical weapon identities. Centralize ranges, projectile altitude, multipliers, nuclear radius, and research-owned policy hooks.

### 2. Fire validation and projectile-channel state

Implement explicit/direct and autonomous fire eligibility, fitted-weapon checks, normal projectile channel ownership, stable rejection reasons, and projectile creation boundary.

### 3. Projectile fidelity research

Trace Spectrum disassembly and supporting evidence; update open questions/spec with resolved projectile timing/collision/expiry semantics.

### 4. Projectile simulation and collision

Implement deterministic advancement, height-aware world collision, range/lifetime termination, and active-channel release using the resolved/configured projectile policy.

### 5. Damage/accuracy/electronics fidelity research

Trace and document arithmetic, strength, hit probability, electronics effects, and any component-damage semantics.

### 6. Robot damage, strength, and destruction

Implement centralized damage application and robot destruction using only resolved rules. Consume M4 canonical robot height and M2/M5 state cleanup contracts.

### 7. M5 engagement-intent consumption

Translate Stop & Defend/Search & Destroy engagement intent into fire attempts without moving target selection/navigation into M6.

### 8. Nuclear detonation and structure destruction

Implement the area effect, carrier destruction, eligible entity enumeration, structure destruction, occupancy cleanup, and deterministic event ordering.

### 9. Victory/destruction integration

Ensure war-base destruction/capture uses one loss/victory function and resolves on the exact authoritative step.

### 10. Snapshot/replay integration

Serialize/restore projectiles, fire-channel state, strength/damage state, destruction state, and any authoritative combat RNG/policy state required for exact replay.

### 11. Milestone integration scenario

Exercise direct and autonomous combat, obstruction/height cases, projectile gating, normal damage/destruction, nuclear destruction, and final-war-base victory with repeated replay/hash equality.

---

## Parallelization

Safe early parallel work once M4/M5 identities/contracts are stable enough:

```text
A: combat metadata/rule interfaces
B: projectile fidelity research
C: damage/electronics fidelity research
D: victory/destruction service contract
```

After combat metadata/fire interface stabilizes:

```text
fire/channel ──► projectile simulation
              └► autonomous engagement integration

research damage ──► damage/strength/destruction

nuclear/destruction can proceed against shared destruction/victory contracts
```

Snapshot/replay integration is the convergence point. The deterministic M6 scenario is the final gate.

Avoid parallel branches independently editing canonical module identity, robot stack/height, target selection, or entity destruction semantics.

---

## Acceptance criteria

### Fire/channel

- Only fitted and eligible weapons may be fired.
- A robot cannot create a second normal projectile while its active normal projectile exists.
- Nuclear behavior does not accidentally occupy/use the normal travelling-projectile lifecycle unless verified evidence requires it.
- Rejected fire attempts have no partial state mutation.

### Range/geometry

- Shared `1 mile = 2 cells` conversion is reused.
- Default normal ranges are 20/28/20 cells for cannon/missile/phaser.
- Electronics nominal range addition is 6 cells where the resolved combat rule applies.
- Nuclear default radius is 16 cells.
- Normal projectile altitude defaults to 10.
- Collision consumes M2/M4 canonical geometry/height data.

### Projectile lifecycle

- Projectile simulation is fixed-tick and deterministic.
- Projectile lifetime is a world/game rule, never browser viewport size.
- Collision/termination releases the source robot's normal-projectile channel exactly once.
- Snapshot/replay can restore an in-flight projectile exactly.

### Damage/destruction

- The locked base-damage expression and 2/3/4 multipliers are centralized.
- Integer semantics and all unresolved modifiers are evidence-backed before being called Spectrum-faithful.
- Destroyed robots release/clean all authoritative references consistently.
- Normal weapons cannot destroy factories/war bases.
- Commander entities remain untargetable and indestructible.

### Nuclear/victory

- Nuclear detonation destroys the carrier and eligible entities inside the authoritative radius.
- Structure destruction cleans occupancy/capture/production state deterministically.
- War-base destruction immediately updates the owned-war-base count.
- Victory is evaluated in the same engine step and emits one stable result.

### Autonomous integration

- M5 engagement intent can trigger combat without M6 reselecting targets or replanning movement.
- Target loss/invalidity produces deterministic no-fire/rejection behavior rather than stale damage.

### Determinism

- Same initial state + seed + accepted commands produces identical projectiles, hit/damage results, destruction order, events, match result, and final hash.

---

## Milestone integration scenario

On a compact deterministic M2–M5 fixture:

1. create robots with cannon, missile, phaser, electronics, and nuclear configurations;
2. verify fitted-weapon validation and rejection of absent weapons;
3. fire a normal projectile and verify a second normal shot is rejected until lifecycle completion;
4. verify projectile altitude 10 and configured range limits;
5. exercise clear-path and obstructed-path projectile termination at several static/robot heights;
6. verify direct fire and M5 autonomous engagement use the same combat path;
7. apply normal damage to robots and exercise destruction cleanup;
8. verify commanders are unaffected/unselectable as damage targets;
9. verify normal weapons cannot destroy factories/war bases;
10. detonate a nuke near multiple robots/structures and verify radius boundary, carrier destruction, cleanup, and ordered events;
11. destroy the opponent's final owned war base and verify victory occurs on the exact same authoritative step;
12. replay identical initial state/seed/commands repeatedly and assert identical events/state/hash.

If projectile or damage fidelity research remains partially unresolved, the scenario must explicitly name the configured policy/default used and must not label it exact Spectrum behavior.

---

## Out of scope

- Construction/resource spending (M4).
- Robot movement/navigation/target-selection policy (M5).
- Browser combat controls, VFX, sound, HUD, interpolation (M8).
- Network transport/latency/reconciliation (M7).
- Commander damage/destruction (does not exist in v1 rules).
- Modernized cooldown systems, ammo systems, splash damage, critical hits, status effects, or other combat mechanics not supported by original evidence.

---

## Human review / hard-stop rules

Human review is required before merging behavior that:

- chooses an unresolved projectile speed/collision/expiry rule and labels it canonical;
- chooses unresolved hit probability, strength, electronics resistance, or component-damage semantics;
- changes the locked `1 mile = 2 cells` conversion;
- changes projectile altitude 10;
- changes normal projectile single-channel behavior;
- allows normal weapons to destroy factories/war bases;
- makes commanders damageable/targetable;
- changes nuclear radius from the configured 8-mile default as the canonical Spectrum rule;
- changes the victory condition;
- duplicates M4 weapon identity or M5 target-selection/navigation logic;
- introduces viewport-dependent projectile expiry.

Research findings that contradict current locked rules must be surfaced to the owner before code/spec changes.

---

## Definition of done

- All M6 task issues are closed by merged PRs.
- Combat consumes M2–M5 contracts without duplicating their responsibilities.
- Fire/channel/projectile/damage/destruction/nuclear/victory paths are deterministic pure-engine code.
- All behavior-defining fidelity questions used as canonical defaults are resolved in authoritative specs, or explicitly documented as configurable/non-canonical if still uncertain.
- Snapshot/replay reproduces in-flight projectiles and combat outcomes exactly.
- M6 integration scenario passes repeatedly with identical state/events/hash.
- M7 can expose combat commands/events without implementing combat rules.
- M8 can render combat state/effects without calculating authoritative outcomes.
