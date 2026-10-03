# Nether Earth Clone — Open Questions

Only questions that are still open are listed here. Decided questions are in [resolved-questions.md](resolved-questions.md); intentional differences from the ZX Spectrum are in [deviations-from-original.md](deviations-from-original.md).

An agent must not invent a value or behaviour for an open question (see `AGENTS.md`). Interfaces and configurable placeholders that do not choose an answer are allowed.

## 1. Combat accuracy, integer rounding, strength handling and electronics modifiers

The damage formula, its integer floor division, the 2/3/4 multipliers, starting strength 100 and destruction at strength ≤ 0 are implemented from the disassembly (`Lb7a7_potentially_hit_a_robot`; see [resolved-questions.md](resolved-questions.md#damage-accuracy-and-electronics-effects)). The research found no hit roll, no component damage and no defensive electronics effect. The owner has kept the area open for any further fidelity evidence on accuracy, rounding, strength semantics and electronics modifiers; until it is closed, these stay isolated in `combat.py` and `rules.py` and must not be extended by guesswork.

## 2. Autonomous fire-decision scan distances

The Spectrum's autonomous fire decision (`Lb626_check_directions_with_enemy_robots`) scans 8 cells in each direction, 10 in the facing direction and 12 facing with electronics, along the robot's lane and the lanes on either side. The engine instead engages any target within weapon range (Manhattan distance to the target's anchor; see [docs/mechanics/orders-and-capture.md](../docs/mechanics/orders-and-capture.md)). Whether to adopt the scan distances is not decided.

## Resolution process

Use this fidelity order:

1. observed ZX Spectrum behavior
2. ZX Spectrum disassembly/code evidence
3. original ZX Spectrum instructions/manual
4. observed gameplay recordings
5. other ports/remakes only as secondary references

When an item is decided, state the rule in `functional-spec.md` / `technical-spec.md`, move the entry to `resolved-questions.md` (and to `deviations-from-original.md` if it departs from the Spectrum) and remove it from this file, all in the same change.
