# Nether Earth Clone — Open Questions

Only questions that are still open are listed here. Decided questions are in [resolved-questions.md](resolved-questions.md); intentional differences from the ZX Spectrum are in [deviations-from-original.md](deviations-from-original.md).

An agent must not invent a value or behaviour for an open question (see `AGENTS.md`). Interfaces and configurable placeholders that do not choose an answer are allowed.

## 1. Combat accuracy, integer rounding, strength handling and electronics modifiers

The damage formula, its integer floor division, the 2/3/4 multipliers, starting strength 100 and destruction at strength ≤ 0 are implemented from the disassembly (`Lb7a7_potentially_hit_a_robot`; see [resolved-questions.md](resolved-questions.md#damage-accuracy-and-electronics-effects)). The research found no hit roll, no component damage and no defensive electronics effect. The owner has kept the area open for any further fidelity evidence on accuracy, rounding, strength semantics and electronics modifiers; until it is closed, these stay isolated in `combat.py` and `rules.py` and must not be extended by guesswork.

## 2. Autonomous fire-decision scan distances

The Spectrum's autonomous fire decision (`Lb626_check_directions_with_enemy_robots`) scans 8 cells in each direction, 10 in the facing direction and 12 facing with electronics, along the robot's lane and the lanes on either side. The engine instead engages any target within weapon range (Manhattan distance to the target's anchor; see [docs/mechanics/orders-and-capture.md](../docs/mechanics/orders-and-capture.md)). Whether to adopt the scan distances is not decided.

## 3. Owner decisions pending

Places where the specification and the implementation disagree, or where an operational default is undecided. The specs state the implemented behaviour and point here; nothing below may be changed in code until the owner decides.

### 3.1 Production during an open construction screen

- **Spec:** resources are committed only when START ROBOT succeeds; nothing about production arriving meanwhile.
- **Code:** `robot_launch.launch_robot` replaces the pool with the buffer copied when the screen opened, so a day's production credited while the screen is open is lost on launch (EXIT MENU keeps it).
- **Needed:** confirm the loss, or decide that the launch must add production received during the session (a rules-version change).

### 3.2 Direct fire from an undocked robot

- **Spec:** combat control (including manual detonation) is available after docking.
- **Code:** `FireCommand` is validated for ownership only, so any owned robot can fire; only the client restricts it to the docked robot.
- **Needed:** keep it as a client-only restriction, or require a docked commander in the engine.

### 3.3 Search & Capture target retention

- **Spec:** the technical spec said the order keeps its stored target while that target's ownership still matches.
- **Code:** selection re-runs on every evaluation (except mid-capture), per the 2026-09-23 decision already in the functional spec; the specs now say so.
- **Needed:** confirm the re-selection as the rule.

### 3.4 Both players disconnected

- **Spec:** if both grace deadlines expire without either returning, the match is a no-contest.
- **Code:** `_resolve_expiry` makes the first deadline to expire a forfeit while the other player is still within grace; no-contest only on an exact tie.
- **Needed:** keep the forfeit-first behaviour, or make "both eventually expire" a no-contest.

### 3.5 Search & Destroy (robots) with only a nuclear weapon

- **Spec:** the hunt falls back when the robot has no weapon capable against robots.
- **Code:** nuclear counts as capable against robots, but autonomous fire never uses it there, so a nuclear-only robot hunts forever without firing.
- **Needed:** decide whether such a robot should fall back to Stop & Defend.

### 3.6 Destroyed robots are removed immediately

- **Spec:** silent; the Spectrum blinks a destroyed robot for 4 cycles before removing it (`Lb7d7_robot_destroyed`).
- **Code:** `destruction.destroy_robot` removes it at once.
- **Needed:** accept as a deviation, or model the blink.

### 3.7 Nuclear building scan order

- **Spec:** war bases, then factories, "each in canonical order".
- **Code:** map declaration order (the Spectrum's building index), not id order; the specs now say map order.
- **Needed:** confirm map order.

### 3.8 Protocol and environment names in the technical spec

- **Spec:** message names (`commander_input`, `state_delta`, `event`, `command_rejected`, `match_paused`, …) and settings (`HOST`, `PORT`, `REPLAY_DIR`, `PUBLIC_BASE_URL`) that do not exist.
- **Code:** the schema message names and the `NETHER_EARTH_*` settings; the technical spec now lists those.
- **Needed:** confirm the implemented names as the contract.

### 3.9 Replay retention default in production

- **Spec/code:** `NETHER_EARTH_REPLAY_RETENTION_DAYS` unset keeps every finished/interrupted replay forever.
- **Needed:** whether production should prune by default, and after how many days (operational, not gameplay).

## Resolution process

Use this fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

When an item is decided, state the rule in `functional-spec.md` / `technical-spec.md`, move the entry to `resolved-questions.md` (and to `deviations-from-original.md` if it departs from the Spectrum) and remove it from this file, all in the same change.
