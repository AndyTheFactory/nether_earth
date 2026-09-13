# Milestone 9 — Full PvP Vertical Slice

## Goal

Integrate all v1 gameplay and client/runtime systems into one complete two-player match that exercises the real map, economy, construction, movement, orders, combat, capture, victory, replay, and browser UX as one coherent product.

## Spec references

- `_specs/functional-spec.md` — complete v1 gameplay scope and match flow
- `_specs/technical-spec.md` — complete v1 architecture
- all prior milestone specifications
- `_specs/open-questions.md` — all questions required by the playable v1 path

## Dependencies

- Milestones 0–8 complete to the degree required by the v1 flow.

## Deliverable

A locally runnable, end-to-end playable two-player PvP game using the intended original-map scenario and production architecture boundaries, with a reproducible deterministic backend simulation and browser clients capable of completing a match.

## Workstreams and candidate tasks

### Scenario finalization
Lock the v1 PvP scenario data, including starting war bases, treatment of remaining war bases, starting resources, factory ownership, spawn positions, and victory rule.

### Original-map fidelity pass
Validate the map, collision geometry, terrain, structures, capture points, heli-pads, exits, and ownership presentation against authoritative references.

### End-to-end gameplay flow
Verify the complete loop:

1. create/join/ready;
2. commanders enter the battlefield;
3. factories and resources function;
4. robots are constructed;
5. direct/autonomous movement works;
6. capture and ownership work;
7. combat/destruction work;
8. final war-base loss ends the match.

### Cross-system edge cases
Exercise blocked exits, commander obstruction, simultaneous movement claims, capture interruption, projectile/height collision, nuke destruction, disconnect/reconnect, and victory during ownership/destruction transitions.

### Replay and determinism validation
Record representative complete matches and replay them through the engine to prove final state/result equivalence.

### User-facing flow polish
Resolve critical usability issues in match creation, controls, menus, game state feedback, reconnect handling, and result presentation without changing game rules.

## Parallelization

Fidelity review, end-to-end scenario testing, and UX polish can proceed in parallel against an integration branch. Cross-system bugs must be assigned back to the subsystem that owns the rule rather than patched in the wrong layer.

## Acceptance criteria

- Two fresh browser clients can create and complete a full PvP match without developer intervention.
- Both players begin with the locked scenario state.
- The full gameplay loop is reachable and consistent with the functional spec.
- Final victory occurs when one player owns zero war bases.
- Browser clients stay consistent with authoritative server state.
- Representative complete matches replay deterministically from seed + accepted command stream.
- No unresolved question remains in a code path required to complete a normal v1 match.
- Critical gameplay/UX defects discovered in vertical-slice testing are resolved in their owning subsystem.

## Milestone integration scenario

Run at least one scripted/replayable deterministic full-match scenario plus one human-driven two-browser acceptance match. Store the deterministic scenario as a regression fixture. The scripted match must traverse economy, construction, movement/orders, capture or strategic ownership change, combat, structure destruction where applicable, and victory.

## Out of scope

- AI opponent.
- Accounts/profiles/rankings.
- Horizontal scaling.
- Additional maps/scenarios beyond what is required for v1.
- Post-v1 gameplay enhancements.

## Open questions / blockers

By the end of this milestone, every item in `_specs/open-questions.md` that affects the standard v1 PvP path must either:

- be resolved and reflected in the authoritative specs; or
- be explicitly proven irrelevant to the v1 completion path.

Any unresolved source conflict that changes gameplay remains a mandatory owner-review gate.

## Definition of done

- All milestone issues are closed by merged PRs.
- Full scripted and human-driven acceptance matches pass.
- Deterministic replay reproduces representative complete matches.
- No known blocker prevents a normal v1 PvP match from starting, progressing, and ending correctly.
