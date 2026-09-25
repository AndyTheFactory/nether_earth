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

Deliberately **not** decided here, and therefore carried as open questions rather than guessed
(see CR004.1): whether the AI drives a commander around the map, and how a solo match behaves on
the human's disconnect. CR004.6 and CR004.7 state the proposed defaults and are blocked on the
owner confirming them.

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
  The AI is subject to the same freeze.

## Risks

- **The AI commander is the hard part.** Construction requires a commander on a war-base heli-pad
  (`heli_pad.py`, `docking.py`). Giving the AI a roaming commander means giving it commander
  pathfinding, collision and vertical physics decisions — a much larger surface than robot orders.
  CR004.6 proposes the smallest viable answer and flags it for the owner.
- **Strength is not testable by assertion.** "Plays better" cannot be a unit test. CR004.10 builds
  a headless harness so strength claims rest on measured win rates against fixed baselines.
- **Per-tick cost.** The planner runs inside the authoritative loop. A planner that scans the map
  every decision tick can starve the 20 Hz budget; #256 already showed the frame budget is not
  generous. Budget is part of CR004.3's acceptance.

## Tasks

Tracker: to be opened.

| ID | Task | Depends on |
|---|---|---|
| CR004.1 | Scope change: specs record the AI opponent decision and its open questions | — |
| CR004.2 | Research: the Spectrum enemy computer player, and what is worth taking | — |
| CR004.3 | Engine AI seat: scenario flag, planner hook, `AiMemory` in state/snapshot/replay | CR004.1 |
| CR004.4 | Economy and construction planner | CR004.3, CR004.2 |
| CR004.5 | Robot order planner: capture valuation, defence, composition response | CR004.3, CR004.2 |
| CR004.6 | AI commander behaviour | CR004.3, owner decision |
| CR004.7 | Backend: solo match lifecycle | CR004.3, owner decision |
| CR004.8 | Protocol and frontend: "Play vs computer" | CR004.7 |
| CR004.9 | Rules version bump, spec updates, fixture regeneration | CR004.3–CR004.6 |
| CR004.10 | Strength and determinism harness | CR004.4, CR004.5 |
| CR004.11 | CR004 acceptance gate | all |

Parallel groups: engine (CR004.3 → CR004.4, CR004.5, CR004.6 in parallel) and the session path
(CR004.7 → CR004.8), which only needs CR004.3's scenario flag. CR004.2 informs CR004.4–CR004.6
but does not block them; it must land before CR004.9 writes the deviations down.

### CR004.1 — Scope change and open questions

- `functional-spec.md` §3: remove "AI opponent" from *Out of scope for v1*; add a scoped in-scope
  line ("single-player match against a computer opponent, one difficulty") and a new section
  describing the seat at product level.
- `technical-spec.md`: record that the AI is an engine-side deterministic planner, and that its
  memory is part of the authoritative snapshot.
- `open-questions.md`: add entries for the two undecided items — **AI commander behaviour**
  (CR004.6) and **solo-match disconnect/pause semantics** (CR004.7) — each stating the proposed
  default and what depends on it. Add a third entry recording that the Spectrum enemy AI is a
  reference rather than a contract, so later fidelity passes do not read the difference as a bug.
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
- Respect the construction freeze: while its session is open the AI's own commander is frozen and
  the rest of the match keeps running.
- Tests: with a fixed seed and no opponent, the AI builds and launches a legal robot within a
  bounded number of ticks; it never opens a session it cannot afford to finish; a session
  interrupted by destruction of the war base leaves consistent state.

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

### CR004.6 — AI commander behaviour — **blocked on owner decision**

Construction needs a commander on a heli-pad, so the AI needs *some* commander behaviour. Two
answers, and the owner picks:

- **Proposed default (smallest viable).** The AI commander stays on its war-base heli-pad and
  only builds. It does not roam, capture on foot, or fight. Closest to the original, where the
  enemy has no ship the player ever meets. Documented as an intentional asymmetry: the human can
  do things with a commander that the AI does not attempt.
- **Full commander.** The AI drives its commander like a player — moves, docks into robots,
  captures. Requires commander path planning over `commander_movement.py` / `collision.py`, and it
  is a much larger task; it would likely become its own CR.

Implement the default only once the owner confirms it; do not pick one by writing code.

### CR004.7 — Backend: solo match lifecycle — **blocked on owner decision**

- `MatchManager.create_match` (`backend/app/match/manager.py`) gains a solo path: the second slot
  is created already occupied by the AI and already ready, so `is_full` and `all_ready` are
  satisfied without a second connection and the match starts immediately. Prefer an explicit AI
  slot over faking a human player, so nothing downstream broadcasts to a socket that does not
  exist.
- The AI seat is never "disconnected": the reconnect coordinator must not pause a solo match for
  it, and must not count it toward a both-disconnected resolution
  (`backend/app/match/reconnect.py`).
- **Owner decision needed:** what happens when the human disconnects from a solo match — pause and
  hold a grace window as PvP does, or end the match at once (nobody is waiting). Proposed default:
  pause with the same grace window, so a dropped connection does not throw away a match in
  progress.
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
- Resolve the CR004.1 open questions with whatever the owner decided for CR004.6 and CR004.7.
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
