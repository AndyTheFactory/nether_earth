# Milestone 2 — Map & World Model

## Goal

Implement the authoritative battlefield representation, versioned YAML map loading, terrain model, structures, and deterministic occupancy/collision foundations required by later movement and combat systems.

## Spec references

- `_specs/functional-spec.md` §7 — world model
- `_specs/functional-spec.md` §9 — factories and war bases
- `_specs/technical-spec.md` §7 — grid and movement model
- `_specs/technical-spec.md` §8 — world state and occupancy
- `_specs/technical-spec.md` §10 — factories and war bases
- `_specs/open-questions.md` §2 and §15

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
- war-base capture location/footprint if the resolved game rules require one;
- structure height and physical footprint;
- scenario spawn/reference positions.

Later milestones must consume these interaction points rather than inventing layer-specific coordinates.

## Workstreams and candidate tasks

### Map schema and loader
Define the versioned YAML schema, validation, map identity/versioning, terrain cells, static entities, factory metadata, war-base metadata, spawn/reference positions, canonical interaction points, and scenario overlays.

### Terrain model
Implement at minimum `NORMAL`, `ROUGH`, and `DITCH` as cell properties distinct from solid occupancy.

### Static structures and geometry
Represent factories, war bases, cubes/boxes, and other blockers with explicit footprint and height metadata. The representation must support both single-cell and multi-cell structures so implementation does not prematurely answer the unresolved footprint question.

### Structure interaction metadata
Provide one canonical representation for structure interaction locations/footprints such as heli-pads, exits, and capture zones. These are part of the world/map contract and must be usable by commander, construction, capture, and frontend systems without duplicating coordinates.

### Occupancy service
Provide deterministic queries and updates for ground-level solid occupancy. Enforce at most one ground solid per occupied cell while keeping terrain separate.

### Scenario overlay
Allow PvP scenario ownership/spawn information to be layered over the base map without mutating the source map definition.

### Original map ingestion
Create or validate `data/maps/zx-spectrum-original.yaml` from the authoritative project references to the degree supported by verified source evidence.

## Parallelization

Map schema/loader, terrain model, generic structure models, and interaction-point modeling can proceed in parallel after a minimal shared coordinate/entity contract is stable. Original-map ingestion may proceed independently and is verified against the loader during integration.

## Acceptance criteria

- YAML map data is versioned and schema validated.
- Grid coordinates are authoritative integers.
- Terrain is distinct from solid occupancy.
- Solid footprint conflicts are rejected deterministically.
- Structure representation supports verified height and flexible footprints without guessing unresolved geometry.
- Factory production type and war-base metadata can be represented.
- Canonical heli-pad, exit, and capture interaction points can be represented and queried by later engine systems.
- Scenario-specific ownership can be applied independently of raw geometry.
- Loading the same map produces canonical-equivalent world state.

## Milestone integration scenario

Load a compact deterministic fixture map containing all terrain types, blockers, a factory, and two war bases. Verify occupancy/terrain queries, canonical heli-pad/exit/capture interaction points, and invalid overlaps. Then load the current original-map YAML and prove it validates and initializes reproducibly.

## Out of scope

- Movement timing and pathfinding.
- Commander collision behavior.
- Factory capture and economy processing.
- Combat/projectile collision.
- Final visual assets.

## Open questions / blockers

The exact treatment of the two remaining war bases in the PvP scenario (`open-questions.md` §2) must be resolved before the final PvP scenario is considered locked; the map model itself must support all candidate answers.

Static-object footprint details (`open-questions.md` §15) may require research for faithful original-map data. The engine representation must remain flexible until verified. Human review is required before committing inferred geometry as authoritative gameplay data.

If the exact capture footprint or interaction geometry is not yet verified, the schema must support it without silently selecting final authoritative coordinates.

## Definition of done

- All milestone issues are closed by merged PRs.
- Map/world integration fixtures pass deterministically.
- Original-map data has traceable evidence or clearly marked unresolved fields.
- Canonical structure interaction points are part of the world contract.
- No renderer-specific representation leaks into the engine map contract.
