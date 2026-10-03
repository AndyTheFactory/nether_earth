# Specification and documentation restructure — Design

**Owner decisions (2026-10-03):** rewrite in place + split files; mechanics docs under `docs/mechanics/`; milestone specs folded into the specs and deleted; resolved questions condensed to decision + one-line evidence.

## 1. Why

`_specs/` grew by accretion over ten milestones and five change requests. `open-questions.md` is 1,170 lines of which two items are open; `functional-spec.md` and `technical-spec.md` still carry superseded text and point to milestone files for the current rule; the deliberate deviations from the ZX Spectrum original are scattered across `open-questions.md` ("Documented deviations"), the CR specs and `docs/cr004/`. Nothing documents the implemented algorithms end to end. An agent or a person starting today cannot tell current rule from history without reading everything.

## 2. Target state

### 2.1 `_specs/` (the product contract)

| File | After |
|---|---|
| `functional-spec.md` | Rewritten as the current gameplay/product rules. Every owner decision from CR001–CR005, CR004 (AI) and the 2026-09-30 security review stated inline as the rule (no "see milestone X"). Superseded statements removed. §20 "Remaining fidelity research" replaced by one pointer to `open-questions.md`. Lobby/nickname rules (invisible characters, creator-nickname clash, 30 s bind deadline, one live socket per session, abandonment grace) added to §5 match flow. |
| `technical-spec.md` | Updated to the implemented architecture: single-worker replay writer with bounded backlog; replay retention knob and `interrupted` status; lobby abandonment grace; bind deadline; close code 4000; loopback-only `/ready` counts; SHA/digest pins, hashed lock, Dependabot/audit workflow; nickname normalisation; AI planner (§28 kept, made current). §26 "Remaining fidelity research" removed (pointer to `open-questions.md`). Every `_specs/milestones/...` reference replaced by the section that now holds the rule or by a `docs/mechanics/` page. |
| `open-questions.md` | Only what is open: (1) combat accuracy, integer rounding, strength handling, electronics modifiers; (2) the 8/10/12-cell autonomous fire-decision scan distances. Plus the "Resolution process" section. Nothing else. |
| `resolved-questions.md` (new) | Every RESOLVED section and every "resolved during CRxxx" note from the old `open-questions.md`, condensed to: title, decision (the rule as locked), date/source (owner decision date, CR/issue), one-line evidence pointer (disassembly label or document). Grouped by area: world/map, commander, economy/construction, movement/navigation, orders/capture, combat/nuke, presentation, match/network, AI. Each entry links to the functional/technical spec section that carries the rule. |
| `deviations-from-original.md` (new) | The intentional differences from the Spectrum. Sources: the 18 "Documented deviations" in `open-questions.md`; CR specs' "Owner decisions" tables where the decision says "deliberate change" (e.g. CR003 descent speed); CR004 AI deviations; lobby/network behaviour that has no Spectrum counterpart is *not* a deviation and is excluded. Per entry: area, what the Spectrum does (label), what we do, why, decision date. Grouped by area. |
| `references.md` | Unchanged except a pointer to `docs/mechanics/` and `docs/cr004/spectrum-ai-notes.md`. |
| `agentic-programming-prd.md` | Process doc kept. Milestone-specific wording updated: milestones M0–M10 and CR001–CR005 are complete; new work arrives as change requests tracked as GitHub issues; the "work hierarchy" and planning-flow sections refer to change requests instead of milestone files. No other content change. |
| `milestones/` | Deleted (all `.md` files and the README). The images under `milestones/cr002/*.png` and `milestones/cr003/*.png` move to `docs/reference-screens/` (frontend READMEs cite them). |

### 2.2 `docs/mechanics/` (the explanation of the implemented algorithms)

Written from the engine/backend source and `rules.py`, not from the specs. One file per area, each with the same section order: **Purpose · State involved · Algorithm (step by step, in tick order) · Constants (name in `rules.py` and value) · Determinism notes · Spectrum evidence (labels) · Deviations (link into `deviations-from-original.md`) · Tests that pin it (file::test names)**.

| File | Covers (engine modules) |
|---|---|
| `README.md` | Index, the section convention, how the docs relate to `_specs/` (specs = contract, mechanics = explanation of the implementation), tick/clock glossary. |
| `timing-and-determinism.md` | `clock.py`, `rng.py`, `engine.py` step order, `replay.py`: 20 Hz tick, day/time derivation, per-tick phase order, RNG seeding, replay reproduction. |
| `world-and-map.md` | `map.py`, `map_overlay.py`, `terrain.py`, `structures.py`, `occupancy.py`, `heli_pad.py`, `scenario.py`: map data, heights, structure footprints, overlay, starting ownership. |
| `commander.md` | `commander.py`, `commander_movement.py`, `docking.py`, `collision.py`: movement cadence, vertical limits, 2×2 collision, docking/undocking, heli-pad landing. |
| `economy.md` | `resource_production.py`, `resource_pool.py`, `construction_economy.py`: production per structure per day, pool accounting, spending rules, general-pool cap. |
| `construction.md` | `construction_session.py`, `robot_build.py`, `robot_stack.py`, `robot_launch.py`, `robot.py`: construction screen session, legal designs, cost, component stack order and heights, launch walk-out. |
| `movement.md` | `movement.py`, `reservations.py`, `interactions.py`, `direct_control.py`: robot step cadence by chassis/terrain, destination reservation, blocking, direct control. |
| `navigation.md` | `navigation.py`: dumb vs electronic pathing, route planning, blocker indexing, the #296 optimisations. |
| `orders-and-capture.md` | `orders.py`, `capture.py`, `autonomous_combat.py`: each order type, target selection and exclusivity, retargeting, capture timing and interruption, Search & Destroy approach. |
| `combat.md` | `combat.py`, `destruction.py`, `victory.py`: fire eligibility, single-projectile channel, projectile stepping and collision/altitude gate, damage and strength, nuke shape and effects, structure destruction, victory. |
| `ai.md` | `ai/` package: decision cadence, construction design choice, order planning and threat response, what it may and may not read. |
| `match-runtime.md` | Backend: `MatchRuntime` tick loop, command ordering/sequence rule, disconnect/reconnect/forfeit policy, lobby lifecycle (abandonment, waiting timeout), replay writer and retention, transport limits (rate, size, bind deadline, one socket per session). |

### 2.3 Deleted or moved outside `_specs/`

- `plans/milestone-5-plan.md`, `plans/milestone-6-plan.md`, `plans/milestone-7-plan.md` deleted (historical plans; git history keeps them).
- `docs/cr004/spectrum-ai-notes.md` kept as evidence; `docs/cr002/` kept.
- `docs/milestone-9/`, `docs/milestone-10/`, `docs/release/`, `docs/reviews/`, `docs/operations/` kept (reports, not specs).

### 2.4 References outside `_specs/`

- `AGENTS.md`: source-of-truth list becomes functional-spec, technical-spec, open-questions, resolved-questions, deviations-from-original, references, agentic-programming-prd, `docs/mechanics/`. "Milestone specifications are planning documents…" paragraph rewritten for change requests. Fidelity priority list unchanged.
- `frontend/README.md`, `frontend/public/assets/README.md`: image paths repointed to `docs/reference-screens/`.
- Source-code docstrings that cite `_specs/milestones/...` (about 25 files in `engine/src`, `backend/app`, `frontend/src`) are **out of scope for this change** because the NE-15 comment rewrite is editing the same files concurrently; a follow-up repoints them after both merge. The acceptance grep below therefore excludes `engine/src`, `backend/app`, `frontend/src`.

## 3. Rules for the rewrite

1. **Source of truth for the current rule is the code**, then the latest owner decision. Where a spec sentence and `rules.py`/engine behaviour disagree, the doc states the implemented behaviour and flags the disagreement in the PR description for the owner (do not silently pick).
2. **Nothing resolved is lost**: every RESOLVED heading in the old `open-questions.md` has a row in `resolved-questions.md`; every "Documented deviation" has an entry in `deviations-from-original.md`; every CR "Owner decisions" row lands in one of: functional spec, technical spec, resolved-questions, deviations.
3. **No history narration in the specs**: no milestone or task numbers, no issue numbers, no "superseded by" text. Dates and issue numbers live only in `resolved-questions.md` and `deviations-from-original.md`.
4. **Mechanics docs describe what the code does**, with the function/module names, not what the spec wished; constants are quoted from `rules.py` with their names so they can be grepped.
5. Markdown: ATX headings, one sentence per line is not required, tables for constant lists, relative links.

## 4. Acceptance checks (all must hold)

```bash
# no dangling milestone references outside source code
! grep -rn "_specs/milestones" --include=*.md . | grep -v "^./docs/superpowers/" | grep -v "^./docs/reviews/" | grep -v "^./docs/milestone-" 
test ! -d _specs/milestones && test ! -d plans
# every old RESOLVED heading has a home
git show main:_specs/open-questions.md | grep -E "^## .*RESOLVED" | sed -E 's/^## ([0-9]+\. )?//; s/ — RESOLVED.*//' > /tmp/old.txt
while read -r t; do grep -qF "$t" _specs/resolved-questions.md || echo "MISSING: $t"; done < /tmp/old.txt   # prints nothing
# every rules.py constant named in docs/mechanics exists
grep -rhoE '`[a-z_]+`' docs/mechanics/*.md | tr -d '`' | sort -u | while read -r n; do grep -q "\b$n\b" engine/src/nether_earth/rules.py engine/src/nether_earth/*.py backend/app -r || echo "UNKNOWN NAME: $n"; done   # review the list; names must be real identifiers
# relative links resolve
python - <<'EOF'
import re,pathlib,sys
bad=[]
for p in list(pathlib.Path("_specs").rglob("*.md"))+list(pathlib.Path("docs/mechanics").rglob("*.md"))+[pathlib.Path("AGENTS.md")]:
    for m in re.finditer(r"\]\(([^)#]+)(#[^)]*)?\)", p.read_text()):
        t=m.group(1)
        if t.startswith(("http","mailto")): continue
        if not (p.parent/t).exists() and not pathlib.Path(t).exists(): bad.append(f"{p}: {t}")
print("\n".join(bad) or "links ok"); sys.exit(1 if bad else 0)
EOF
pytest -q   # unchanged: the change is docs only
```

## 5. Review

One PR on branch `docs/spec-restructure`. Reviewer checks: rule 2 (nothing lost) by diffing the old `open-questions.md` headings against the new files; three randomly chosen mechanics pages against the engine source; AGENTS.md still names a reachable source of truth for every rule class.
