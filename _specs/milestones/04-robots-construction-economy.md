# Milestone 4 — Robots, Construction & Economy

## Goal

Implement robot composition, canonical height/stack derivation, factory/war-base production, player resource pools, and robot construction at war bases without yet implementing autonomous navigation or full combat.

## Spec references

- `_specs/functional-spec.md` §9–§13 — structures, economy, construction, stack, chassis
- `_specs/technical-spec.md` §10–§14 — structures, capture state model, economy, robot build, construction
- `_specs/open-questions.md` §1 and §10
- `https://github.com/santiontanon/netherearth-disassembly` — authoritative construction/resource behavior evidence

## Start dependencies

- Milestone 2 world/structures contract.
- Milestone 3 heli-pad/commander interaction contract.

## Completion dependencies

- Milestones 2 and 3 complete for the interaction/world behavior consumed by construction.

## Deliverable

An engine subsystem that represents valid robot builds, derives one canonical physical component stack and height, tracks player resources, processes production on game-day boundaries, validates construction eligibility, deducts resources according to the resolved spending policy, and launches a robot when the war-base exit is available.

This milestone owns the canonical robot component/module catalog for construction concerns. Later combat systems consume and extend this catalog with combat-specific metadata rather than defining a second weapon catalog.

## Locked original Spectrum economy/construction values

Use the original ZX Spectrum values as the default game configuration:

- starting general resources: **20** per player;
- bipod cost: **3**;
- tracks cost: **5**;
- anti-grav cost: **10**;
- cannon cost: **2**;
- missile cost: **4**;
- phaser cost: **4**;
- nuclear cost: **20**;
- electronics cost: **3**.

All numeric costs and starting-resource values must live in one clearly defined game configuration/rules object rather than being spread through construction logic. The Spectrum values above are the canonical defaults, but they must be easy to tune without changing engine code.

Resource categories follow the original mapping:

- bipod/tracks/anti-grav consume **chassis** resources;
- cannon consumes **cannon** resources;
- missile consumes **missile** resources;
- phaser consumes **phaser** resources;
- nuclear consumes **nuclear** resources;
- electronics consumes **electronics** resources.

## Locked original Spectrum spending rule

Construction spending must reproduce the original behavior:

1. For a selected component, spend from its type-specific resource pool first.
2. If the type-specific pool is insufficient, reduce that pool to zero and pay only the shortfall from general resources.
3. If general resources cannot cover the shortfall, selection/build is rejected.
4. During construction editing, deselecting/removing a component restores resources according to the original reversible-buffer behavior: restore its type-specific pool up to the amount available before entering construction, then return any remainder to general resources.
5. Selecting/deselecting modules operates on a temporary construction resource state.
6. Actual player resources are committed atomically only when **Start Robot** succeeds.
7. Exiting/scrapping an unlaunched construction does not permanently consume resources.

This policy should be isolated behind one engine-level construction/economy service or function so alternate balancing rules could be introduced later without rewriting UI or robot-build logic.

## Workstreams and candidate tasks

### Canonical component/module catalog
Define the canonical chassis, weapon, and electronics module identities and construction-facing metadata. Construction costs and resource-category requirements use the locked Spectrum defaults above and are loaded from game-rule configuration. Combat-specific properties such as range, lethality, and projectile behavior are added/consumed by Milestone 6 without duplicating module identity or construction cost.

### Robot build model
Implement chassis, weapon, and electronics module types and validation:

- exactly one chassis;
- one to three weapons;
- no duplicate module;
- zero or one electronics module.

### Canonical stack and height
Create one engine function that derives physical component order and total height. Rendering metadata may consume this output later; no client-side duplicate ordering logic is allowed.

Locked bottom-to-top weapon order is cannon, missile, phaser, nuke, followed by electronics when fitted.

### Resource pool and production
Track general and type-specific resources. Every 2,880 ticks, owned factories produce 2 type-specific units and owned war bases produce 5 general units.

### Construction state
Implement entering/exiting construction from a valid heli-pad state, selecting/deselecting modules, temporary resource-buffer behavior, validation, scrap/cancel semantics, and build launch according to the locked Spectrum rule above.

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
- Spectrum-default starting resources are 20 general units per player.
- Spectrum-default component costs are 3/5/10/2/4/4/20/3 for bipod/tracks/anti-grav/cannon/missile/phaser/nuclear/electronics.
- Component costs and starting resources are configurable game-rule data.
- Resource production is driven solely by authoritative ticks and ownership.
- Construction spends type-specific resources first and general resources only for the shortfall.
- Removing a selected component reverses temporary spending consistently with the original construction buffer behavior.
- Exiting an unlaunched build does not permanently consume resources.
- Player resources are committed atomically only on successful robot launch.
- Construction requires valid commander/war-base interaction.
- Robot cap and blocked exit prevent launch.
- Launch creates exactly one valid robot and applies the resolved resource rule atomically.
- Construction behavior can be replayed deterministically.
- Milestone 6 can attach/consume combat metadata without redefining weapon identities or construction costs.

## Milestone integration scenario

Initialize a player with the default 20 general resources plus known type-specific pools. Advance exactly one game day and verify production. Land the commander, build legal robots that exercise both pure type-specific spending and general-resource substitution, remove/re-add components and verify reversible temporary accounting, verify the original component costs, launch and verify atomic resource commit, exit an unlaunched build and verify no permanent deduction, test an invalid build, then block the exit and verify launch rejection. Replay to identical results.

## Out of scope

- Robot movement/navigation.
- Factory/war-base capture.
- Direct robot control.
- Weapon firing/damage.
- Combat-specific weapon properties such as projectile behavior and damage formulas.
- Frontend construction UI.

## Open questions / blockers

The construction-cost and resource-spending questions previously tracked in `_specs/open-questions.md` §10 are resolved by the original Spectrum disassembly and project-owner confirmation.

No product decision remains for spending precedence, module costs, starting resources, or construction refund/commit behavior. Implementation details must preserve determinism and the locked behavior above.

## Definition of done

- All milestone issues are closed by merged PRs.
- Economy/construction integration scenario passes deterministically.
- Canonical stack and resource policies are backed by resolved specs.
- One canonical component/module catalog exists for later systems to consume.
- Later movement/combat can consume robot state without restructuring the build model.
