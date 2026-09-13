# Milestone 4 — Robots, Construction & Economy

## Goal

Implement robot composition, canonical height/stack derivation, factory/war-base production, player resource pools, and robot construction at war bases without yet implementing autonomous navigation or full combat.

## Spec references

- `_specs/functional-spec.md` §9–§13 — structures, economy, construction, stack, chassis
- `_specs/technical-spec.md` §10–§14 — structures, capture state model, economy, robot build, construction
- `_specs/open-questions.md` §1 and §10

## Start dependencies

- Milestone 2 world/structures contract.
- Milestone 3 heli-pad/commander interaction contract.

## Completion dependencies

- Milestones 2 and 3 complete for the interaction/world behavior consumed by construction.

## Deliverable

An engine subsystem that represents valid robot builds, derives one canonical physical component stack and height, tracks player resources, processes production on game-day boundaries, validates construction eligibility, deducts resources according to the resolved spending policy, and launches a robot when the war-base exit is available.

This milestone owns the canonical robot component/module catalog for construction concerns. Later combat systems consume and extend this catalog with combat-specific metadata rather than defining a second weapon catalog.

## Workstreams and candidate tasks

### Canonical component/module catalog
Define the canonical chassis, weapon, and electronics module identities and construction-facing metadata. Construction costs and resource-category requirements belong here once resolved. Combat-specific properties such as range, lethality, and projectile behavior are added/consumed by Milestone 6 without duplicating module identity or construction cost.

### Robot build model
Implement chassis, weapon, and electronics module types and validation:

- exactly one chassis;
- one to three weapons;
- no duplicate module;
- zero or one electronics module.

### Canonical stack and height
Create one engine function that derives physical component order and total height. Rendering metadata may consume this output later; no client-side duplicate ordering logic is allowed.

### Resource pool and production
Track general and type-specific resources. Every 2,880 ticks, owned factories produce 2 type-specific units and owned war bases produce 5 general units.

### Construction state
Implement entering/exiting construction from a valid heli-pad state, selecting/deselecting modules, validation, scrap/cancel semantics where verified, and build launch.

### Robot count and war-base exit constraints
Enforce the 24-robot sector cap and blocked-exit rule using the canonical war-base exit metadata from Milestone 2.

### Initial robot entity
Create the authoritative robot entity fields needed by later movement/orders/combat without prematurely adding those behaviors.

## Parallelization

Component catalog/build/stack, economy, and construction-state work may proceed in parallel once shared module/resource types are agreed. The launch integration task owns resource deduction + robot creation + exit occupancy behavior.

## Acceptance criteria

- Invalid robot configurations are rejected deterministically.
- Canonical module identity and construction metadata have one engine source of truth.
- Canonical stack/height has one engine source of truth.
- Nuke/electronics top-placement rules are respected.
- Resource production is driven solely by authoritative ticks and ownership.
- Construction requires valid commander/war-base interaction.
- Robot cap and blocked exit prevent launch.
- Launch creates exactly one valid robot and applies the resolved resource rule atomically.
- Construction behavior can be replayed deterministically.
- Milestone 6 can attach/consume combat metadata without redefining weapon identities or construction costs.

## Milestone integration scenario

Initialize a player with a war base, factories, and known resources. Advance exactly one game day and verify production. Land the commander, build a legal robot, verify stack/height and resource changes, test an invalid build, then block the exit and verify launch rejection. Replay to identical results.

## Out of scope

- Robot movement/navigation.
- Factory/war-base capture.
- Direct robot control.
- Weapon firing/damage.
- Combat-specific weapon properties such as projectile behavior and damage formulas.
- Frontend construction UI.

## Open questions / blockers

Before final fidelity completion, resolve and update the authoritative specs for:

- exact cannon/missile/phaser stack order (`open-questions.md` §1);
- exact chassis/electronics costs and resource spending/substitution/scrap rules (`§10`).

These are explicit research/decision dependencies. Agents may build the data model around unresolved values but must not select them silently. Conflicting evidence is escalated to the project owner.

## Definition of done

- All milestone issues are closed by merged PRs.
- Economy/construction integration scenario passes deterministically.
- Canonical stack and resource policies are backed by resolved specs.
- One canonical component/module catalog exists for later systems to consume.
- Later movement/combat can consume robot state without restructuring the build model.
