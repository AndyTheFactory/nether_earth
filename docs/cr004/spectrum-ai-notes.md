# The Spectrum enemy computer player: research notes (CR004.2, #283)

Source: `santiontanon/netherearth-disassembly`, file `netherearth-annotated.asm` (repo root,
branch master). Labels are cited as they appear there. These notes describe the original. They
are not a spec for CR004. The owner decided on 2026-09-25 that the Spectrum is a reference, not a
contract, and that the goal is a stronger AI. Each mechanic below ends with a verdict: **take
as-is**, **take and improve**, or **discard**. CR004.9 cites these verdicts for each deviation it
documents.

Evidence tags, following `_specs/open-questions.md`:

- **Recoverable**: read directly from the disassembled code, with the label cited.
- **Derived**: arithmetic on recoverable code, such as probabilities or timings. The nominal
  probabilities assume `Ld358_random` is uniform. It is a small shift/xor generator, so the real
  distribution is close to these figures but not exact.
- **Guessed**: not supported by the code, such as intent or play feel. Nothing in the planner
  should depend on a guessed item.

Timing uses the engine's mapping: 1 Spectrum game cycle = 4 ticks = 200 ms
(`open-questions.md` §"One game cycle"). 1 game day = 2880 ticks = 720 cycles.

---

## 1. Overall structure

**Recoverable.** The whole strategic layer is one routine, `Lb7f4_update_enemy_ai`. It is called
exactly once per game cycle, as the first call in `Lb0ca_update_robots_bullets_and_ai`, before
any robot or bullet is updated. It has **no memory**. Everything it decides is either written
into a robot struct (`ROBOT_STRUCT_ORDERS`, `ROBOT_STRUCT_ORDERS_ARGUMENT`) or spent from
`Lfd4a_player2_resource_counts`. No plan, intent, threat list or build queue carries over from
one cycle to the next.

Each cycle it does the following:

1. It draws `r = random & 0x1f`. If `r >= 24` (`MAX_ROBOTS_PER_PLAYER`), it **does nothing**
   (p = 1/4).
2. Otherwise `r` is taken as an AI robot slot in `Ldb80_player2_robots`:
   - an **occupied** slot goes to *order management* (`Lb920_enemy_ai_single_robot_control`, §4);
   - an **empty** slot goes to a *build attempt* into that slot (§2). The annotation mislabels this
     code `Lb0ca_enemy_ai_control_warbase`, the same address prefix as the robot update loop.

So each cycle does **at most one thing**: re-order one robot or attempt one build. Which one is
decided by a dice roll over the slots, not by any evaluation of the game.

**Derived.** Any given slot is examined with p = 3/4 · 1/24 = 1/32 per cycle, so on average once
every 32 cycles (6.4 s). The chance of a build attempt is proportional to the number of free
slots, so building slows down as the army grows.

**Verdict: discard** the dice-roll scheduler. Keep the fixed cadence: CR004 already runs the
planner every `ai_decision_interval_ticks` (default 4, which is one Spectrum cycle). Each planner
pass should evaluate the whole state, not one random slot. The 25 % idle roll only adds latency.

---

## 2. What it builds

**Recoverable** (`Lb81b_pick_random_warbase_loop` and the build code that follows it):

- **Which war base.** It walks the 4 war bases in index order and flips a coin at each. It takes
  the first base whose coin is 1 *and* whose type byte is exactly `0x20` (a war base owned by
  player 2 and not destroyed). If no base qualifies, it does nothing. With k AI-owned bases, the
  chance of picking one is 1 − (1/2)^k, and the choice is biased toward the lower index. No other
  factor, such as threat or distance to targets, is considered.
- **Exit check.** If any entrance cell of the base carries the robot mark (bit 6), it aborts.
- **Robot design.** The design is one random byte, stored straight into `ROBOT_STRUCT_PIECES`.
  Its bits are 0–2 chassis (bipod, tracks, anti-grav), 3–6 weapons (cannon, missiles, phasers,
  nuclear) and 7 electronics. The attempt returns and does nothing unless every rule below holds:
  - exactly one chassis bit is set;
  - the weapon nibble is not 0 and not 0xF, so there are at most 3 weapons;
  - the **weapon count is at least ⌊robot_count/8⌋ + 1**, where robot_count is
    `Lfd49_player2_robot_count`. The first 8 robots need at least 1 weapon, the next 8 at least
    2, and from the 17th robot on exactly 3. *The annotation says "at most", but the code
    (`call Lb505…; cp b; ret c`) rejects counts **below** the threshold, so it is a minimum.*
  - electronics (bit 7) is a free coin flip and is never constrained.
- **After a successful build**, the robot appears at once at the base's exit, with:
  - strength 100;
  - direction and desired direction set to "down", with 3 steps left to walk;
  - order **Stop & Defend**, argument 255;
  - height computed from `Ld7b4_piece_heights`.

  No construction screen and no commander are involved.

**Derived.** The chance that a random design byte passes the design rules is chassis 3/8 ×
weapons 14/16 (fewer than 8 robots), 10/16 (8–15 robots) or 4/16 (16 or more). That gives about
33 %, 23 % and 9 %, before the affordability check. A rejected roll wastes that cycle, and there
is no retry. About half of the accepted designs carry nuclear (bit 6), which costs 20. Most of
those fail the budget check early in the game, so the robots that actually get built lean toward
cheap designs.

**Guessed.** The minimum-weapon ramp looks like a deliberate "later robots are stronger" curve.
Nothing in the code says so.

**Verdicts:**

- Random design byte: **discard**. The planner should choose a design on purpose, based on role,
  affordability and what the opponent fields.
- "Later robots carry more weapons" ramp: **take and improve**. Scaling robot quality with the
  stage of the game is sound. Drive it by resources and production rate, not by robot count alone.
- Chassis chosen without regard to terrain or target distance: **discard**. Prefer a chassis that
  can reach the intended target.
- Exit-blocked abort: **take as-is**. The engine already refuses a blocked launch (`EXIT_BLOCKED`,
  `robot_launch.py`). The planner simply should not try.
- Instant build with no commander: **take as-is** (owner decision). In CR004 the build goes
  through the ordinary construction commands via CR004.4's commanderless session entry, not
  through a privileged spawn.

---

## 3. How it spends resources

**Recoverable** (`Lb890_check_resource_availability_loop` through
`Lb8c6_more_than_22_resources_left`, and the tables `Lcaf0_piece_costs` = 3, 5, 10, 2, 4, 4, 20,
3 and `Lcaf8_piece_factory_type`):

- Each piece is paid first from the pool of its own factory type. Any shortfall comes from the
  general pool (index 0). The three chassis all draw on the same chassis pool (type 6), one after
  another.
- **General-pool spending cap.** The total general shortfall `d` must satisfy
  `d <= max(general // 2, 11)` and `d <= general`. So one robot may use at most **half** of the
  general pool, unless it needs 11 general or less.
- If either check fails, the attempt is dropped and the resources are untouched. The spend is
  applied only on success, all at once.
- **Income is symmetric.** `Lae38_gain_day_resources` runs the same
  `Lae4e_gain_day_resources_player` for both players:
  - each war base gives 5 general per day;
  - each factory gives 2 of its own type per day;
  - every pool is capped at 99 (`Lae62_add_limit_100`).

  Both players start with 20 general (`INITIAL_PLAYER_RESOURCES`). There is no income bonus for
  the AI.

The engine already models the costs, the shortfall-from-general rule and production
(`rules.py` `module_cost_*`, `construction_economy.py`, `resource_production.py`,
`open-questions.md` §10). **The half-of-general cap is not an engine rule.** It is a limit the AI
places on itself, and it belongs in the planner.

**Verdicts:**

- Own pool first, then general: **take as-is**. It is already the engine rule. The planner only
  needs to predict it.
- Cap at half of general (never below 11): **take and improve**. It is a crude reserve. Replace it
  with an explicit reserve tied to threat (global context, improvement 1: "keep a reserve for a
  defensive build").
- Roll a design, check whether it is affordable, give up if not: **discard**. This is the
  stalling behaviour CR004 targets. The planner should pick a design that is affordable now, or
  one that its production rate will make affordable soon.

---

## 4. How it assigns orders

**Recoverable** (`Lb920_enemy_ai_single_robot_control`, `Lb95d_assign_new_orders`,
`Lb34d_find_capture_or_destroy_target`, `Lb3d5_prepare_robot_order_building_target_search`).

A robot on **Stop & Defend** gets new orders the first time its slot is picked. In practice only
freshly built robots are on Stop & Defend.

A robot on **any other order**:

- keeps its orders and returns with p = 31/32 (`and 0x1f; ret nz`). *The annotation's comment says
  "1/32 chance they will be kept", which is backwards. The code keeps them with 31/32.*
- in the remaining 1/32:
  - Advance, Retreat and Destroy Robots orders are replaced with new orders.
  - A building order is checked against its current target:
    - If the target **still matches** the order's owner flags, the robot gets **new** orders. The
      annotator believes this is an inverted branch (`jr z` where `jr nz` was meant), and the
      code supports that reading.
    - Otherwise the robot keeps its order only if it stands exactly on the target's x and y.
      Anywhere else, it gets new orders.

**Picking the new order:**

- A robot **with nuclear** (bit 6) gets Destroy Enemy War Bases (p = 3/4) or Destroy Enemy
  Factories (p = 1/4).
- A robot without nuclear gets Capture Neutral Factories (p = 1/2), Capture Enemy Factories
  (p = 1/4) or Capture Enemy War Bases (p = 1/4).
- **Target selection.** The target is the building nearest *by x only* (`Lb3b3`, `Lb3ca`: |Δx|,
  y ignored) whose owner flags match the order:
  - "enemy" means owned by player 1 (flag `0x40`);
  - "neutral" means flags 0, and that search covers factories only;
  - a building is skipped when **another AI robot with the same order** already targets it
    (`Lb36c`). This is the same exclusivity rule player robots use.
- If no target is found, the robot falls back to **Destroy Enemy Robots**.

**Orders layer (`Lb289`), recoverable.** While a building order runs, the robot searches again if
its stored target stops matching. If nothing is found, a robot owned by the AI switches to Destroy
Enemy Robots. This is the only branch in the shared robot code that tests
`ROBOT_CONTROL_ENEMY_AI` (`bit 7, (iy + ROBOT_STRUCT_CONTROL)`). A player robot in the same
situation just stops.

**Other recoverable facts:**

- The AI **never issues** Advance, Retreat or Stop & Defend on purpose.
- Its Capture Enemy War Bases matches only war bases that player 1 owns, so the AI **never targets
  the two interior neutral war bases**. The engine's `ENEMY_WAR_BASE` already includes neutral
  bases (`orders.py`, owner decision 2026-09-25).
- A Destroy Robots target is dropped and picked again once it is 50 or more x-cells away
  (`Lb222` to `Lb258`).
- The nuclear bomb goes off when the robot arrives exactly on its destroy target (`Lb2e8` →
  `Lb99f_fire_nuclear_bomb`). This happens in the shared robot code, so it is not an AI decision.

**Derived.**

- A robot keeps its order for about 1024 cycles on average (1/32 × 1/32 per cycle). That is about
  205 s, or about 1.4 game days.
- Once re-evaluated, a robot is almost always given a new random order, often one that sends it
  back to a nearby target.
- A fresh robot waits at its base for 32 cycles (6.4 s) on average before it gets any order.

**Verdicts:**

- Role decided by loadout (nuclear robots destroy, the rest capture): **take and improve**. The
  split is sensible. Choose the target by its value (production type, distance, how contested it
  is), not by a fixed probability split.
- Nearest target by x only: **take and improve**. The engine already has its own nearest-match
  selection (CR003.2, `orders.py`). The planner should rank candidates, then issue ordinary
  orders.
- Exclusivity between robots with the same order: **take as-is**. It is already the engine rule
  (CR003.2). The planner should also avoid sending robots with *different* orders to the same
  building when that wastes them.
- Fallback to Destroy Robots when there is no target: **take and improve**, but in the planner
  only. `open-questions.md` (CR003 amendment, 2026-09-23) deliberately did **not** make this an
  engine rule. The planner may give an idle robot a Destroy Robots order through a normal command
  instead.
- Re-evaluation by a 1-in-1024 roll, and the inverted branch: **discard**. Re-plan when something
  happens (a target changes owner or is gone, a threat appears), not by dice.
- Never targeting neutral war bases: **discard**. Taking the interior bases is the direct route to
  winning under the victory rule (`victory.py`).
- Never using Stop & Defend or Retreat: **discard**. These are the tools for defence (§5).

---

## 5. Does it react to threats?

**Recoverable: no, not at the strategic level.** `Lb7f4` reads no enemy position, no damage and no
distance to its own bases.

The only reactive behaviour is per robot (`Lb154_robot_ai_update`,
`Lb626_check_directions_with_enemy_robots`). A robot scans its four axis lines, 8 cells each or
10 in the direction it faces. If it sees an opponent, it turns toward it and fires one of its
normal weapons at random. This code is **shared with the player's robots**, and the engine
already has it in `autonomous_combat.py`.

The AI never:

- pulls robots back to defend a base or factory under attack;
- builds in response to an incursion;
- keeps track of its losses.

**Verdicts:**

- No defence: **discard**. Defence is CR004 improvement 4.
- Per-robot reactions: **take as-is**. They are engine behaviour, not the planner's concern.

---

## 6. What information it reads, and whether it cheats

**Recoverable.** This is everything the strategic layer reads:

| What it reads | Where | Can a human player see it? |
|---|---|---|
| Its own robot slots: occupied or not, orders, target, x/y, pieces | `Lb7f4`, `Lb920` | own units, yes |
| Its own robot count `Lfd49` | weapon ramp | yes (HUD) |
| Its own resource pools `Lfd4a` | budget | own resources, yes |
| War base and factory owners and positions | `Lb81b`, `Lb3d5`, `Lb34d` | yes (map flags, radar) |
| Map cells at its war-base entrance | exit check | yes |
| x position of player robots (Destroy Robots targeting, `Lb41d`) | shared robot code | yes (radar shows every robot) |

It **never** reads the player's resources, the player's commander position or state, a
construction the player has in progress, or anything else a player cannot see. **It does not
cheat on information.** Its asymmetries are structural:

- **In the AI's favour:**
  - it builds instantly, with no trip by a commander and no time spent in the construction screen;
  - an AI robot left without a target hunts robots, where a player robot would stop.
- **Against the AI:**
  - it does no planning;
  - it sits idle 25 % of the time;
  - its designs are random;
  - it does not defend;
  - it ignores neutral war bases.

**Verdict: take as-is** the rule of reading no hidden information. It matches the global-context
rule "read off the visible snapshot only". The planner may read any `GameState` field that a
player's client renders, and nothing else. Hidden fields to avoid:

- the opponent's resource pool (check whether the snapshot exposes it before reading it);
- a construction session in progress;
- the opponent commander's position where the frontend hides it (the radar never shows it).

---

## 7. Summary table (for CR004.9)

| Mechanic | Evidence | Verdict | Reason |
|---|---|---|---|
| One action per cycle, random slot, idle 25 % of cycles | `Lb7f4` | discard | adds latency, no evaluation |
| Runs once per game cycle | `Lb0ca_update_robots_bullets_and_ai` | take as-is | same as `ai_decision_interval_ticks` = 4 |
| Random war base (a coin flip per base) | `Lb81b` | discard | choose the base by threat or front line |
| Skip the build when the exit is blocked | build code after `Lb81b` | take as-is | the engine already refuses |
| Random design byte, dropped if it fails | build code after `Lb81b` | discard | causes stalls and odd designs |
| Minimum weapon count grows with robot count | `Lb505` call site | take and improve | scale quality by economy, not count |
| Own pool first, shortfall from general | `Lb890` | take as-is | already an engine rule |
| General spend capped at max(G/2, 11) | `Lb8c6` | take and improve | turn into a threat-driven reserve |
| Same income for both sides, no bonus | `Lae38`/`Lae4e` | take as-is | fairness |
| New robot starts on Stop & Defend, waits about 6.4 s | build code, `Lb920` | discard | give the order at launch |
| Nuclear robots destroy (war base 3/4, factory 1/4); others capture (neutral factory 1/2, enemy factory 1/4, enemy war base 1/4) | `Lb95d` | take and improve | keep the role split; rank targets by value |
| Target is nearest by x only | `Lb34d`/`Lb3b3` | take and improve | value-ranked selection |
| No two robots with the same order share a target | `Lb36c` | take as-is | already an engine rule |
| No target → Destroy Robots | `Lb289` (AI-only branch), `Lb989` | take and improve | a planner command, not an engine rule |
| Keeps orders with 31/32; the 1/32 re-roll has an inverted check | `Lb920` | discard | re-plan on events |
| Never targets neutral war bases | `Lb3d5` flags | discard | the interior bases decide victory |
| No threat response, no defence | nothing in `Lb7f4` | discard | CR004 improvement 4 |
| Reads only information a player can see | §6 | take as-is | no cheating |

## 8. Notes for the planner implementer

- The annotation's comments are wrong in two places. The weapon ramp is a **minimum**, and orders
  are **kept** with p = 31/32, not 1/32. Trust the code.
- Nothing in the original reasons about which weapon beats which. The engine has no armour stat:
  robot strength is fixed, damage is a per-weapon multiplier (`rules.py`
  `*_damage_multiplier`), and missiles have a longer range. Any composition response is new
  design with no Spectrum evidence behind it. Base it on differences the engine really has
  (range, damage, height), not on an "armour" idea the engine does not model.
- None of this needs a new gameplay rule. Every action the Spectrum AI takes maps onto an existing
  engine command: a construction session (select modules, launch) or an order.
