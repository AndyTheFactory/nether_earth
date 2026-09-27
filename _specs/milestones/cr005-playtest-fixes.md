# CR005 — Playtest Fixes (2026-09-27)

## Goal

Implement the owner change request of 2026-09-27:

1. **CR005.1** — A commander parked in the door of an enemy war base let the base build a robot that could not leave; robot and commander were both stuck. A war base must not produce a robot while a commander occupies its door.
2. **CR005.2** — The lift of a robot walking over mountains is too small to notice. Make it more pronounced.
3. **CR005.3** — Where a robot is killed, the terrain becomes rubble; a nuked building's outline becomes rubble (check the Spectrum code).
4. **CR005.4** — The commander's shadow over a 2×2 of mixed elevations should fall on the cells actually under it.

## Decisions

| Item | Decision | Evidence |
|---|---|---|
| CR005.1 | Launch rejected (`EXIT_BLOCKED`) while a free commander overlaps the exit body below the new robot's top. Applies to players and the AI. Owner decision; departs from the Spectrum. | `La6c8` only tests robot marks (bit 6). |
| CR005.2 | Presentation only: ground heights drawn ×3 (`GROUND_LIFT`) under robots, commander, bullets and shadows. Engine altitudes unchanged. | Owner request. |
| CR005.3 | Combat kill on four plain cells → 2×2 rough debris (height 3). Nuked robots leave none. Every cell of a nuked building → rough debris; it no longer blocks. `RULES_VERSION` → `cr005`. | `Lb116_robot_destroyed`, `Lba44_robots_handled`, `Lbc27_replace_building_by_debris`. |
| CR005.4 | Shadow cut per cell, each part on its own cell's surface. | Owner request. |

See `open-questions.md` "CR005 playtest fixes".

## Acceptance

- Engine: `test_robot_launch.py` (commander in door), `test_robot_debris.py`; full engine/backend suite green.
- Frontend: `surface.test.ts`, `robot.test.ts`; typecheck, vitest, build green.
- Owner playtest of the four items.
