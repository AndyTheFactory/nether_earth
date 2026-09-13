# Milestone 6 — Combat, Damage, Destruction & Victory

## Goal

Implement authoritative weapon firing, projectile lifecycle, nuclear detonation, damage/strength handling, destruction, and victory evaluation.

Milestone 6 consumes the canonical robot component/module catalog from Milestone 4 and the target-selection/engagement-intent contract from Milestone 5. It must not create a duplicate weapon/component catalog or reimplement navigation decisions.

## Spec references

- `_specs/functional-spec.md` §18–§19 and victory rules
- `_specs/technical-spec.md` weapon/projectile/damage/victory sections
- `_specs/open-questions.md` §3, §8, §9

## Start dependencies

- Milestone 4 canonical robot composition, component identity, and height contract.
- Milestone 5 movement/range, ownership, target-selection, and engagement-intent contracts.

## Completion dependencies

- Milestones 4 and 5 complete for all combat-facing behavior consumed by this milestone.

## Deliverable

A deterministic combat subsystem that validates fire commands, enforces the single-active-normal-projectile gate, advances projectiles by authoritative world rules, performs height-aware collisions, resolves damage/destruction, executes nuclear area effects, updates ownership/war-base counts where applicable, consumes autonomous engagement intent, and declares victory when a player owns zero war bases.

## Workstreams and candidate tasks

### Combat metadata on canonical modules
Attach or consume combat properties for the weapon identities established in Milestone 4, including range, lethality, projectile behavior, and electronics modifiers. Construction costs/resource requirements remain owned by Milestone 4 and must not be duplicated.

### Fire command and active projectile gate
Validate fitted weapons, interaction state, firing legality, autonomous engagement intent, and the rule that only one normal projectile may remain active for a robot at a time.

### Projectile simulation
Implement deterministic projectile position/lifecycle, world/height collision, hit/crash/expiry semantics, and ordered events.

### Damage and strength
Implement the verified accuracy, damage, resistance, and robot-strength rules. Any component-level damage behavior must be evidence-backed.

### Autonomous combat resolution
Connect Milestone 5 target/engagement decisions to firing eligibility and combat resolution. Navigation and target-selection policy remain owned by Milestone 5.

### Nuclear detonation
Implement the nuke as a special area detonation: destroy eligible entities in the verified 8-mile radius and destroy the carrier. Factories/war bases are destroyable only by nuclear weapons.

### Destruction and victory
Remove/destroy entities deterministically and evaluate victory immediately after any event capable of reducing a player's owned war-base count to zero.

## Parallelization

Combat metadata/fire validation, damage model, autonomous combat integration, and nuclear rules can be built in parallel after shared combat/event contracts are agreed. Projectile integration depends on resolved movement/range/world collision data. Victory logic may be developed independently against ownership fixtures.

## Acceptance criteria

- Only fitted/eligible weapons can fire.
- Weapon identities and construction costs come from the Milestone 4 canonical component catalog rather than a duplicate catalog.
- A robot cannot fire a second normal weapon while its active projectile exists.
- Projectiles use authoritative simulation/world rules, never browser viewport dimensions.
- Height-aware collision uses canonical entity/component heights.
- Resolved accuracy/damage/electronics resistance rules are centralized and deterministic.
- Stop & Defend/Search & Destroy engagement intent from Milestone 5 can resolve into actual deterministic firing/combat.
- Nuclear detonation applies the verified radius and destroys its carrier.
- Non-nuclear weapons cannot destroy factories/war bases.
- Destruction emits deterministic events and leaves valid occupancy/state.
- Victory triggers exactly when the opponent owns zero war bases.

## Milestone integration scenario

Construct robots with several weapon configurations on a fixed fixture map. Exercise direct fire and Milestone 5 autonomous engagement intent, fire normal weapons through clear and obstructed paths, verify the active-projectile gate and damage, then trigger a nuke near robots/structures. Finally destroy/capture the last enemy war base and assert the exact victory tick/event. Replay to identical state/events.

## Out of scope

- Browser combat UI/effects.
- Navigation/pathfinding or target-selection policy already owned by Milestone 5.
- Construction cost/resource policy already owned by Milestone 4.
- Network latency/reconciliation.
- Match creation/join/reconnect.

## Open questions / blockers

Final implementation depends on resolving:

- miles-to-grid conversion if not already resolved (§3);
- exact projectile mechanics (§8);
- damage, accuracy, strength, and electronics-resistance formulas (§9).

These are hard decision boundaries. Research agents may gather evidence and recommendations; implementation agents must not invent formulas or viewport-derived projectile expiry. Conflicting evidence requires owner review and a spec update before merge.

## Definition of done

- All milestone issues are closed by merged PRs.
- Combat/nuclear/victory scenario passes deterministically.
- All behavior-defining combat questions used by code are resolved in authoritative specs.
- Regression fixtures cover representative firing, autonomous engagement, destruction, and victory edge cases.
- Combat consumes M4/M5 contracts without duplicating component or navigation logic.
