# Milestone 2 — Map & World Model

## Goal

Implement the authoritative battlefield representation, versioned YAML map loading, terrain model, structures, and deterministic occupancy/collision foundations required by later movement and combat systems.

## Spec references

- `_specs/functional-spec.md` §7 — world model
- `_specs/functional-spec.md` §9 — factories and war bases
- `_specs/technical-spec.md` §7 — grid and movement model
- `_specs/technical-spec.md` §8 — world state and occupancy
- `_specs/technical-spec.md` §10 — factories and war bases
- resolved `_specs/open-questions.md` §2 and §15

## Start dependencies

- Milestone 1 deterministic engine contract complete.

## Completion dependencies

- No additional milestone completion dependency beyond Milestone 1.

## Deliverable

A deterministic engine world that loads a versioned YAML map, exposes integer X/Y cells and terrain, represents structures and static blockers, validates map data, and provides one authoritative occupancy API for all later systems.

The map/world contract must also expose canonical structure interaction metadata needed by later milestones, including:

- war-base heli-pad location/footprint;
- war-base exit location/footprint;
- factory capture location/footprint;
- war-base capture location/footprint;
- structure/component height and physical occupied cells;
- scenario spawn/reference positions.

Later milestones must consume these interaction points rather than inventing layer-specific coordinates.

## Locked structure-composition model

Static geometry is not modeled as one generic rectangular `building footprint`.

- War bases and factories are separate semantic world entities with separate composition definitions.
- A war base is composed from explicit physical map components/cells. Its semantic metadata includes heli-pad, exit, capture zone where applicable, ownership, and later resource behavior.
- A factory is composed separately from explicit physical map components/cells. Its semantic metadata includes production type, capture zone, ownership, and later production behavior.
- Generic boxes/cubes/scenery/blockers use explicit evidence-backed occupied cells/components.
- Physical height is component/cell-aware; the model must not require a single uniform height across a complete war base or factory.
- Interaction zones are explicit semantic metadata and are not inferred from a generic physical footprint.
- Exact ZX Spectrum war-base/factory component layouts are data-reconstruction work. Unsupported geometry must remain clearly unresolved rather than guessed.

Robot geometry is a separate robot-model concern and must not be inferred from this static-structure representation.

## Locked v1 PvP scenario overlay

The original four-war-base map remains intact. Starting ownership is applied by scenario overlay:

- Player 1 owns the **extreme-left war base**.
- Player 2 owns the **extreme-right war base**.
- The two war bases between them start **neutral**.
- Neutral war bases remain active and capturable under the normal war-base capture rules.
- The overlay changes ownership/spawn state only and must not mutate raw map geometry.

## Workstreams and candidate tasks

### Map schema and loader
Define the versioned YAML schema, validation, map identity/versioning, terrain cells, static entities, factory metadata, war-base metadata, spawn/reference positions, canonical interaction points, scenario overlays, and compositional structure geometry.

### Terrain model
Implement at minimum `NORMAL`, `ROUGH`, and `DITCH` as cell properties distinct from solid occupancy.

### Static structures and geometry
Represent factories, war bases, cubes/boxes, and other blockers as semantic entities backed by explicit physical components/cells and height metadata. War-base and factory composition must be modeled separately rather than forced through one universal building footprint.

### Structure interaction metadata
Provide one canonical representation for structure interaction locations/footprints such as heli-pads, exits, and capture zones. These are part of the world/map contract and must be usable by commander, construction, capture, and frontend systems without duplicating coordinates or deriving them from generic geometry.

### Occupancy service
Provide deterministic queries and updates for ground-level solid occupancy. Enforce at most one ground solid per occupied cell while keeping terrain separate. Multi-cell structures reserve every occupied physical cell in their component layout.

### Scenario overlay
Allow PvP scenario ownership/spawn information to be layered over the base map without mutating the source map definition. For the default v1 PvP scenario, bind the extreme-left and extreme-right war bases to the two players and initialize the two interior war bases as neutral.

### Original map ingestion
Create or validate `data/maps/zx-spectrum-original.yaml` from the authoritative project references. Reconstruct war-base and factory compositions explicitly from evidence, including component cells, heights, and semantic interaction locations where verifiable.

## Parallelization

Map schema/loader, terrain model, generic structure models, and interaction-point modeling can proceed in parallel after a minimal shared coordinate/entity contract is stable. Original-map research may proceed independently, while authoritative ingestion is verified against the finalized structure-composition contract during integration.

## Acceptance criteria

- YAML map data is versioned and schema validated.
- Grid coordinates are authoritative integers.
- Terrain is distinct from solid occupancy.
- Solid occupied-cell conflicts are rejected deterministically.
- War bases and factories are distinct semantic structure types with separately defined compositions.
- Static geometry supports explicit multi-cell/component composition and per-component/cell height without relying on one universal building footprint.
- Factory production type and war-base metadata can be represented.
- Canonical heli-pad, exit, and capture interaction points can be represented and queried by later engine systems independently of physical geometry.
- Scenario-specific ownership can be applied independently of raw geometry.
- The default v1 scenario assigns the extreme-left war base to Player 1, the extreme-right war base to Player 2, and both interior war bases to neutral ownership.
- Loading the same map produces canonical-equivalent world state.

## Milestone integration scenario

Load a compact deterministic fixture map containing all terrain types, generic blockers, a composed factory, and two composed war bases. Verify occupancy/terrain queries, component/cell heights, canonical heli-pad/exit/capture interaction points, and invalid overlaps. Then load the current original-map YAML and prove it validates and initializes reproducibly. Verify the default PvP overlay applies opposite-end starting ownership and leaves the two interior war bases neutral without mutating base geometry.

## Out of scope

- Movement timing and pathfinding.
- Commander collision behavior.
- Robot footprint/composition rules.
- Factory capture and economy processing.
- Combat/projectile collision.
- Final visual assets.

## Open questions / blockers

The v1 PvP treatment of the four original war bases is resolved: players start at the extreme-left and extreme-right war bases, while the two interior war bases are neutral and capturable. M2 owns representation and deterministic scenario initialization; capture behavior itself remains implemented in the later owning gameplay milestone.

Static-object representation (`open-questions.md` §15) is resolved at the model level: war bases and factories use separate semantic entities composed from explicit physical cells/components. What remains for M2 research is the exact evidence-backed composition of each original-map structure, not the representation strategy itself.

If exact capture footprint or interaction geometry is not yet verified, the schema must support it without silently selecting final authoritative coordinates.

## Definition of done

- All milestone issues are closed by merged PRs.
- Map/world integration fixtures pass deterministically.
- Original-map data has traceable evidence or clearly marked unresolved fields.
- Canonical war-base and factory compositions are represented separately.
- Canonical structure interaction points are part of the world contract.
- Default PvP war-base ownership/spawns are represented in the scenario overlay as locked above.
- No renderer-specific representation leaks into the engine map contract.
