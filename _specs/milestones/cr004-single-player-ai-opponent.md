# CR004 — Single-player matches against an AI opponent

## Goal

Implement the owner change request of 2026-09-25: a player can start a match alone and play
against a computer opponent.

Today this is impossible by design. `functional-spec.md` §3 lists "AI opponent" under *Out of
scope for v1*, and the match layer enforces two human players: `Match.is_full` requires
`len(self.players) >= 2` (`backend/app/match/models.py`), `all_ready` requires both slots ready,
and the reconnect coordinator pauses the simulation whenever either player is disconnected
(`backend/app/match/reconnect.py`). A lone session stays in `WAITING` forever.

CR004 adds a second, computer-driven seat. The AI issues the same commands a human issues,
through the same validation path, so it gains no rule it does not share with a player.

## Owner decisions (2026-09-25)

| Topic | Decision |
|---|---|
| Scope | "AI opponent" moves out of the v1 non-goals. This CR is that scope change; it is recorded in `functional-spec.md` §3 as an owner decision, not inferred from a task. |
| Fidelity | The Spectrum's enemy computer player is a **reference, not a contract**. Use the disassembly wherever it usefully explains what the original does, then deliberately go beyond it: the target is an opponent that plays *better* than the original, not one that reproduces it. Every departure is documented as an intentional deviation, not as a fidelity gap. |
| Placement | The AI lives **in the engine**, as a pure deterministic planner. It is not a backend bot session. |
| Configurability | One difficulty level. No difficulty selector, no tuning UI. |
| Entry point | A single "Play vs computer" entry point that creates a match already filled with the AI in the second seat. |
| Unchanged | Human players and PvP multiplayer behave **exactly as today**. Everything below applies to the AI seat only. |
| Embodiment | The AI has **no commander**, and none is shown. It issues robot orders and construction without being physically present anywhere on the map. This is a deliberate asymmetry with the human seat, not a simplification to be revisited: the original's enemy has no ship the player ever meets either. |
| Solo disconnect | Same as multiplayer: if the human disconnects, the match pauses with the usual grace window. |

## Design

### The AI is a player, not a rule

`AGENTS.md` locks the engine as the owner of all gameplay rules and requires it to stay
deterministic. Both hold here, and they point the same way:

- The planner emits ordinary `Command` instances (`engine/src/nether_earth/commands.py`) for its
  `PlayerId`. Commands go through `validate_command` and the normal per-tick batch ordering
  (`order_commands`, sorted by `(player.value, sequence)`) exactly like a human's. A rejected AI
  command is rejected; there is no privileged path, no direct state mutation, and no new rule.
- Because it runs inside `engine.step`, a replay reproduces the match from scenario + map version
  + seed + the human's command stream alone. The AI's commands are *derived*, not recorded, so
  they cannot drift out of sync with the rules that produced them.

A backend bot session was rejected for the opposite reason: its decisions would depend on task
scheduling and would have to be logged into every replay to stay reproducible.

### The AI has no commander

The AI seat owns war bases, factories, resources and robots. It owns no commander and occupies no
cell on the map. It gives orders and builds remotely.

Two consequences, and they are not symmetrical:

- **Orders need no change.** The engine already does not gate order issuance on a commander being
  anywhere in particular; docking only *suppresses* a robot's autonomous order while a commander
  is driving it (`orders.py`, `_under_direct_control`). An AI with no commander simply never
  suppresses anything. Nothing is relaxed for the AI here — the rule as written never required
  presence.
- **Construction does need a change.** `construction_session.py` opens a session off
  `heli_pad.CommanderConstructionEntryEligible`, i.e. a commander landing on its own war base's
  heli-pad. A commanderless seat can never raise that event. CR004.4 therefore adds a second,
  explicit entry into the same session layer for AI seats, keyed on owning the war base rather
  than standing on it. The session, cost, legality and launch rules behind it are unchanged and
  shared; only the entry condition differs.

The resulting asymmetry is intentional and must be written down as such (CR004.9), because it
reads like a missing rule otherwise: the human pays a positioning cost to build and to drive a
robot directly, and the AI does not. In exchange the AI gives up everything a commander can do —
it cannot capture on foot, cannot take direct control of a robot, cannot fight in person, and has
no unit that can be destroyed. The construction freeze (`open-questions.md`, owner decision
2026-09-21) freezes a commander; with no commander to freeze, an AI build costs it no tempo.
Whether that balance is right is a playtest question for CR004.11, not a design question here.

Nothing in the victory rule depends on a commander (`victory.py` counts war bases), so a
commanderless seat wins and loses normally.

### Determinism

The planner is a pure function of `(GameState, AiMemory, EngineRules)`. Any randomness comes from
`MatchRandom` seeded via `rng.derive_seed(match_seed, "ai", player_id)`, never from a global RNG.
Iteration over structures, robots and candidate targets uses canonical order, never set or dict
iteration order that could vary. The planner's own carry-over state (`AiMemory`: current build
intent, per-robot assignments, threat bookkeeping) lives **in `GameState`** and round-trips
through `snapshot.py` and `replay.py`; a planner that kept state in a Python object outside the
snapshot would break mid-match reconnect and replay verification.

### Cadence

The original updates its AI once per game cycle (`Lb0ca_update_robots_bullets_and_ai`, four engine
ticks at 20 Hz — `open-questions.md` §"One game cycle"). CR004 keeps a fixed decision cadence in
the same spirit: the planner runs every `ai_decision_interval_ticks` ticks (default 4), never
every tick. This bounds per-tick cost and makes the cadence a rules constant rather than an
emergent property of how fast the loop happens to run.

### What "better than the original" means

The original's enemy is a shallow reactive loop. The measurable improvements this CR targets, in
priority order:

1. **Economic discipline.** Build what the current resource pool and production rate actually
   support, instead of stalling on an unaffordable robot. Keep a reserve for a defensive build
   when the war base is threatened.
2. **Valued targets.** Rank capture targets by what they are worth (production type, distance,
   how contested they are), not merely by proximity. Reuse the exclusivity rule already
   established in CR003.2 so two AI robots do not converge on one factory.
3. **Composition response.** Bias the weapon mix against what the opponent actually fields
   (armour-heavy → missiles, swarm → phaser), read off the visible snapshot only — the AI reads
   the same `GameState` any player's client renders, never hidden information.
4. **Defence.** Detect an enemy robot closing on an owned war base or factory and divert or build
   a defender, rather than pushing forward unconditionally.
5. **Not losing on the clock.** Track the victory rule (`victory.py`) and switch to denying the
   opponent's last war base when ahead on material.

These are engine-side planning heuristics. None of them changes a gameplay rule; if one appears
to require a rule change, that is a blocker to surface, not to implement.

## Dependencies

- CR003 rules (`RULES_VERSION = "cr003"`) are merged. CR004 bumps to `cr004`.
- CR004 does not change gameplay rules by intent. It does add `AiMemory` to the snapshot and an AI
  seat to the scenario, so existing fixtures and replays must be regenerated (CR004.9).
- The order planner builds on the CR003.2 capture-order semantics (persistent orders, retargeting,
  exclusive targets) and the existing autonomous behaviour in `orders.py` /
  `autonomous_combat.py`. It must not fork that logic.
- The construction planner drives the existing `construction_session.py` /
  `construction_commands.py` flow. Note the locked PvP adaptation in `open-questions.md`
  (owner decision 2026-09-21): during construction only the building player's commander is frozen.
  The AI has no commander, so the freeze has nothing to act on for it (see *The AI has no
  commander*).

## Risks

- **A seat with no commander is a shape the code has not seen.** `state.commanders` has so far
  held one commander per player. Every site that looks one up for the AI seat must tolerate
  `None`; `commander_for` already returns `None`, but callers that assume otherwise will fail at
  the worst time. CR004.6 is an audit task for exactly this, and it is not optional.
- **The build asymmetry is a balance risk, not a correctness one.** The AI builds without paying
  the positioning and freeze cost a human pays. That may make it too strong or simply feel unfair;
  CR004.10's measured win rate and the CR004.11 playtest are where that is caught.
- **Strength is not testable by assertion.** "Plays better" cannot be a unit test. CR004.10 builds
  a headless harness so strength claims rest on measured win rates against fixed baselines.
- **Per-tick cost.** The planner runs inside the authoritative loop. A planner that scans the map
  every decision tick can starve the 20 Hz budget; #256 already showed the frame budget is not
  generous. Budget is part of CR004.3's acceptance.

## Tasks

Tracker: #293.

| ID | Task | Depends on |
|---|---|---|
| CR004.1 (#282) | Scope change: specs record the AI opponent decision and its open questions | — |
| CR004.2 (#283) | Research: the Spectrum enemy computer player, and what is worth taking | — |
| CR004.3 (#284) | Engine AI seat: scenario flag, planner hook, `AiMemory` in state/snapshot/replay | CR004.1 |
| CR004.4 (#285) | Economy and construction planner | CR004.3, CR004.2 |
| CR004.5 (#286) | Robot order planner: capture valuation, defence, composition response | CR004.3, CR004.2 |
| CR004.6 (#287) | Commanderless seat: audit every commander assumption | CR004.3 |
| CR004.7 (#288) | Backend: solo match lifecycle | CR004.3 |
| CR004.8 (#289) | Protocol and frontend: "Play vs computer" | CR004.7 |
| CR004.9 (#290) | Rules version bump, spec updates, fixture regeneration | CR004.3–CR004.6 |
| CR004.10 (#291) | Strength and determinism harness | CR004.4, CR004.5 |
| CR004.11 (#292) | CR004 acceptance gate | all |

Parallel groups: engine (CR004.3 → CR004.4, CR004.5, CR004.6 in parallel) and the session path
(CR004.7 → CR004.8), which only needs CR004.3's scenario flag. CR004.2 informs CR004.4–CR004.6
but does not block them; it must land before CR004.9 writes the deviations down.

### CR004.1 — Scope change and open questions

- `functional-spec.md` §3: remove "AI opponent" from *Out of scope for v1*; add a scoped in-scope
  line ("single-player match against a computer opponent, one difficulty") and a new section
  describing the seat at product level.
- `technical-spec.md`: record that the AI is an engine-side deterministic planner, and that its
  memory is part of the authoritative snapshot.
- `open-questions.md`: add an entry
  recording that the Spectrum enemy AI is a reference rather than a contract, so later fidelity
  passes do not read the difference as a bug. Add a second recording the commanderless AI seat, so
  the AI building without a heli-pad landing is not later read as a bug.
- No code in this task.

### CR004.2 — Research: the Spectrum enemy computer player

- Read the enemy-AI path in `santiontanon/netherearth-disassembly` and write
  `docs/cr004/spectrum-ai-notes.md`: what the original decides, when, and on what information.
  Cover at minimum how the enemy chooses what to build, how it spends resources, how it assigns
  orders, whether it reacts to threats, and what information it reads (including whether it cheats
  by reading state a player cannot see).
- Explicitly separate **recoverable** behaviour from **guessed** behaviour, the way
  `open-questions.md` already does elsewhere.
- Output a short verdict per mechanic: take as-is / take and improve / discard, with the reason.
  This is the evidence CR004.9 cites for each documented deviation.
- Research only. No engine change.

### CR004.3 — Engine AI seat

- `Scenario` gains a controller per seat (for example `player_two_controller: "human" | "ai"`,
  defaulting to `"human"` so every existing scenario and fixture is unchanged). Do not overload
  `PlayerId`; a seat is a human or an AI, and nothing else about the player changes.
- New engine package `nether_earth/ai/` with a pure entry point
  `plan(state, memory, rules, random) -> tuple[Command, ...]`. This task lands the seat, the hook
  and an empty planner that returns no commands; CR004.4–CR004.6 fill it.
- `engine.step` invokes the planner for each AI seat on decision ticks
  (`rules.ai_decision_interval_ticks = 4`), before the tick's command batch is ordered, and feeds
  its commands into the same batch with sequence numbers assigned deterministically.
- `AiMemory` is a serializable dataclass in `state.py`, round-tripped by `snapshot.py` and
  `replay.py`.
- Tests: a scenario with an AI seat produces a byte-identical snapshot sequence across two runs
  with the same seed; the planner never runs on a non-decision tick; an AI command that violates a
  rule is rejected like a human's, and the rejection does not stall the planner; an all-human
  scenario is unchanged (existing fixtures still verify).
- Budget check: measure planner time per decision tick on the full map and record it in the PR.

### CR004.4 — Economy and construction planner

- Drive the real construction flow (`construction_session.py`, `construction_commands.py`,
  `robot_build.py`, `robot_launch.py`) via commands. No shortcut that creates a robot directly.
- Decide: when to open a construction session, which chassis/weapons/electronics to pick within
  the current pool (`resource_pool.py`, `resource_production.py`), when to launch, and when to
  hold resources back for a defensive build.
- **Commanderless entry.** Add an explicit AI-seat entry into the construction session layer,
  keyed on the seat owning the war base, alongside the existing
  `heli_pad.CommanderConstructionEntryEligible` path. It opens the same session type and goes
  through the same cost, legality and launch rules; do not duplicate the session logic. The
  entry is available to AI seats only — a human seat still has to land, and a test pins that.
- The session exit must not assume a commander: today it lifts the player's commander
  (`commander_exit_elevate_updates`) and already returns early when `commander_for` is `None`.
  Keep that path covered by a test for the AI seat.
- One open session per war base, as for a human. The AI may build at every war base it owns.
- Tests: with a fixed seed and no opponent, the AI builds and launches a legal robot within a
  bounded number of ticks; it never opens a session it cannot afford to finish; a session
  interrupted by destruction of the war base leaves consistent state; a human seat cannot use the
  commanderless entry; an AI seat builds at a war base it owns and cannot at one it does not.

### CR004.5 — Robot order planner

- Assign autonomous orders through the existing order commands. Reuse the CR003.2 semantics
  (orders persist, retarget after capture, targets exclusive per order) rather than
  re-implementing selection inside the AI.
- Capture valuation, defence reaction and weapon-mix response as described under *What "better
  than the original" means*. Each heuristic is a small pure function with its own test.
- The AI reads only what is in `GameState` and visible to a player in that seat. If any heuristic
  needs information a human cannot see, stop and raise it — do not implement it.
- Tests: with two neutral factories at different distances and values, the AI splits its robots
  the intended way; an enemy robot closing on the AI war base produces a defensive response; ties
  break deterministically.

### CR004.6 — Commanderless seat: audit every commander assumption

Owner decision (2026-09-25): the AI has no commander and acts without being anywhere on the map.
CR004.3 creates the seat without one; this task makes the rest of the stack agree.

- Scenario init (`scenario.py`) creates no commander for an AI seat. No placeholder commander,
  parked or hidden — an entity that exists but never acts would still collide, occupy cells and
  appear in snapshots.
- Audit every read of `state.commanders` / `commander_for` in the engine, snapshot, backend and
  frontend. Each site either already tolerates `None` or gets fixed and a test. Known sites to
  check first: construction exit, collision (commander-vs-commander), radar and camera (frontend
  follows *the viewer's* commander, which still exists), HUD, snapshot schema (a player with no
  commander must validate).
- Protocol: `protocol/schemas/snapshot.schema.json` must allow a player without a commander.
  Regenerate types; backend validation and frontend types stay aligned.
- Tests: a full AI-seat match runs to a result with no commander for that seat; the snapshot of
  such a match validates against the schema and renders in the frontend without errors.

### CR004.7 — Backend: solo match lifecycle

- `MatchManager.create_match` (`backend/app/match/manager.py`) gains a solo path: the second slot
  is created already occupied by the AI and already ready, so `is_full` and `all_ready` are
  satisfied without a second connection and the match starts immediately. Prefer an explicit AI
  slot over faking a human player, so nothing downstream broadcasts to a socket that does not
  exist.
- The AI seat is never "disconnected": the reconnect coordinator must not pause a solo match for
  it, and must not count it toward a both-disconnected resolution
  (`backend/app/match/reconnect.py`).
- The human disconnecting pauses the match with the same grace window as PvP (owner decision
  2026-09-25).
- No join code is exposed for a solo match; it cannot be joined by a second player.
- Tests: a solo match reaches `ACTIVE` with one connection; the AI seat never triggers a pause;
  a solo match is swept and finished like any other.

### CR004.8 — Protocol and frontend

- `protocol/schemas/client_messages.schema.json`: extend `createMatch` with an optional opponent
  mode (defaulting to the current human-vs-human behaviour, so existing clients are unaffected).
  Regenerate `protocol/generated/` and the frontend types; backend validation and the generated
  types must stay aligned.
- `frontend/src/ui/lobby.ts`: a "Play vs computer" button next to create/join. It creates the
  match and goes straight to the game; no join code, no waiting screen, no ready step.
- The HUD names the opponent as the computer rather than showing an empty or guest nickname.
- Frontend adds no rule logic: it renders an AI-driven opponent exactly as it renders a human one.
- Tests: schema validation round-trip; a controller test that the solo path skips the waiting and
  ready states.

### CR004.9 — Rules version, specs, fixtures

- `RULES_VERSION = "cr004"`. Regenerate the M9 full-match fixture and the replay fixtures.
- Fold CR004.2's verdicts into `open-questions.md` as documented deviations, each with its
  evidence, so a later fidelity pass does not read "the AI does not behave like the Spectrum's"
  as a defect.
- Record the commanderless AI seat as an intentional AI-only rule; human and PvP rules are unchanged.
- Update `functional-spec.md` and `technical-spec.md` to describe what actually shipped.

### CR004.10 — Strength and determinism harness

- A headless harness that runs a full match with no frontend and no backend, straight against the
  engine: AI vs AI, and AI vs a fixed scripted baseline.
- Determinism: the same seed produces an identical snapshot sequence and an identical outcome
  across runs and across processes.
- Strength: a measured win rate against the scripted baseline over a fixed set of seeds, recorded
  in the PR. This is the only evidence that will be accepted for "better than the original".
- Keep it deterministic and fast enough to run in CI; it is a regression guard for the planner,
  not a benchmark suite.

### CR004.11 — Acceptance gate

- All engine, backend and frontend checks pass. Existing PvP fixtures verify unchanged apart from
  the intended rules-version regeneration.
- The M9 scripted full match and the live two-client check still pass: CR004 must not regress PvP.
- The CR004.10 harness passes, with its determinism result and win rate recorded.
- Owner playtest: start a solo match from the lobby, play it to a conclusion, and confirm the
  opponent is a credible one.
