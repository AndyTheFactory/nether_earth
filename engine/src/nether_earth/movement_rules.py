"""Canonical robot movement timing constants (ticks per cell, by chassis/terrain).

Issue #61 (M5.2, `_specs/milestones/05-orders-navigation-capture.md`,
`_specs/open-questions.md` §4 "Exact movement speeds and terrain
penalties -- PARTIALLY RESOLVED") is a research-only task: it does not
implement the movement executor itself (that is issue #60's `movement.py`,
landing independently), it only resolves the *values* that executor must
consume. This module is that single centralized home for those values, kept
separate from `rules.py`'s ``EngineRules`` dataclass so issue #60's branch
can adopt it (or fold it into `EngineRules`) without a merge conflict on a
large shared file; either module is acceptable per the milestone plan's
Task 2 scope note ("land the defaults as data in the movement rules/config
module Task 1 creates ... if concurrent, land as a small follow-up PR").

## Evidence trail

Fidelity priority per `_specs/functional-spec.md`/`_specs/references.md`:
(1) observed Spectrum behavior, (2) disassembly/code evidence, (3) original
manual, (4) gameplay recordings, (5) other ports (secondary only). No
observed-behavior or gameplay-recording evidence was available for this
task; the values below are grounded in (2) disassembly evidence, fetched
from `santiontanon/netherearth-disassembly` (`_specs/references.md` #2),
file ``netherearth-annotated.asm``, via automated web retrieval -- this
module's author did not read the raw file byte-for-byte, so the quoted
assembly below is mediated through a fetch/summarization tool. Confidence in
its accuracy is nonetheless reasonably high because it independently
reproduced *other* already-locked, independently-verified constants from
the same file on the same fetch (``INITIAL_PLAYER_RESOURCES: equ 20`` and
``MAX_ROBOTS_PER_PLAYER: equ 24``, matching `_specs/open-questions.md` §10's
locked ``starting_general_resources = 20`` and `rules.py`'s locked
``max_robots_per_player = 24``) -- i.e. the fetch is corroborated against
independently-verified ground truth elsewhere in this codebase, not taken on
faith. This is disclosed rather than presented as a first-hand disassembly
read.

Quoted evidence (``netherearth-annotated.asm``, labels as given by the
disassembly's own symbol names):

```
; How many cycles does the robot take to move in the different terrains,
; depending on its chassis:
Lb61d_robot_movement_speed_table:
    ;  bipod, tracks, anti-grav
    db #06, #04, #03  ; flat terrain
    db #08, #06, #03  ; rugged
    db #09, #07, #04  ; mountains
```

and, for the cadence at which the loaded per-move cycle count is consumed:

```
Lb154_robot_ai_update:
    ...
    dec (iy + ROBOT_STRICT_CYCLES_TO_NEXT_UPDATE)
    ret nz  ; if we do not yet need to update this robot, skip
```

decremented once per iteration of the game's main loop (``La69a_game_loop``),
which is itself rate-limited by:

```
; Game constants:
MIN_INTERRUPTS_PER_GAME_CYCLE: equ 10  ; game maximum speed is 5 frames per second.
```

i.e. one "game cycle" = ``MIN_INTERRUPTS_PER_GAME_CYCLE`` (10) raw Spectrum
50 Hz interrupts = 200 ms = 5 Hz, directly matching the comment's own "5
frames per second" statement (no separate 50 Hz-interrupt assumption is
needed -- the comment states the resulting cadence outright).

## Mapping disassembly "game cycles" onto this project's locked 20 Hz tick

`_specs/technical-spec.md` §5 locks ``1 tick = 50 ms`` (20 Hz). One
disassembly "game cycle" (200 ms) is therefore exactly
``200 / 50 = 4`` simulation ticks. ``ticks_per_cell = cycles_per_cell * 4``.
This conversion factor (4) is evidence-derived, not guessed, and is exact
(200 ms and 50 ms both divide evenly).

## Terrain-tier mapping caveat (flagged, not silently assumed)

The disassembly's speed table is keyed by a purely continuous per-cell
*altitude* tier (flat / rugged / mountains, split at altitude thresholds 0,
1-3, 4+), gated by a separate per-chassis altitude *ceiling* (bipod 8,
tracks 12, anti-grav 15) -- i.e. the original engine has no discrete
"impassable ditch" terrain category for bipod/tracks; it has a continuous
altitude limit. This project's terrain model (`terrain.py`) instead locks
three discrete categories (``NORMAL``/``ROUGH``/``DITCH``) with bipod/tracks
categorically forbidden from ``DITCH`` (`_specs/technical-spec.md` §13,
`_specs/open-questions.md` §4, locked). Mapping disassembly "flat" ->
``NORMAL`` and "rugged" -> ``ROUGH`` is direct and unambiguous (same
semantic: ordinary vs. increasingly broken passable ground). Mapping
"mountains" -> ``DITCH`` for anti-grav's ditch speed is an interpretive
judgment (closest available evidence for "the most extreme terrain tier"),
**not** a verified one-to-one correspondence, since the disassembly's
altitude-continuous model does not draw its own tier boundary at the same
place this project's categorical ``DITCH`` boundary sits. It is used here
only because it is the best available evidence for anti-grav's non-ordinary
terrain speed, and is flagged as such rather than silently presented as
exact-Spectrum-verified for our specific ``DITCH`` category.

## Rough-terrain severity ordering: a genuine evidence conflict (flagged for human review)

`_specs/open-questions.md` §4 and `plans/milestone-5-plan.md`'s Global
Constraints lock a *qualitative* requirement: rough terrain penalizes bipod
*more severely* than tracks. The raw disassembly numbers above do not
unambiguously support that ordering under either natural reading:

- **Absolute cycle increase** (flat -> rugged): bipod ``6 -> 8`` (+2),
  tracks ``4 -> 6`` (+2) -- tied, not "bipod more."
- **Proportional slowdown** (flat -> rugged): bipod ``8/6 = 1.33x``,
  tracks ``6/4 = 1.5x`` -- tracks' proportional slowdown is *larger*, the
  opposite of the locked claim.

This is a real conflict between disassembly evidence and a previously
locked design requirement, not a rounding artifact -- per `AGENTS.md`
("when documents conflict, stop and surface the conflict rather than
silently choosing an interpretation") it is recorded here and in
`_specs/open-questions.md` §4 rather than silently resolved either way.
Per this task's own instructions ("where ambiguous, preserve the locked
*relative* behavior ... mark the exact value explicitly as
configurable/unverified rather than inventing precision"), the rough-terrain
values below are therefore **not** the raw disassembly numbers verbatim;
they are documented placeholders chosen only to satisfy the still-locked
qualitative ordering (bipod's rough multiplier > tracks' rough multiplier),
pending a human decision on how to reconcile the conflict (e.g. accept the
disassembly numbers and revise the locked qualitative claim, or keep the
qualitative claim and treat the exact magnitude as permanently
configurable/non-Spectrum-exact). Ordinary-terrain and anti-grav/ditch
values above are NOT affected by this conflict and are evidence-backed as
described.
"""

from dataclasses import dataclass

from .robot_build import ModuleIdentity
from .terrain import TerrainType

__all__ = [
    "DEFAULT_MOVEMENT_RULES",
    "GAME_CYCLE_TICKS",
    "MovementRules",
]

#: One original-game "cycle" (the unit `Lb61d_robot_movement_speed_table`
#: counts in) equals this many locked 20 Hz simulation ticks. Evidence-backed
#: (see module docstring): 200 ms / 50 ms = 4.
GAME_CYCLE_TICKS: int = 4


@dataclass(frozen=True, slots=True)
class MovementRules:
    """Centralized, overridable ticks-per-cell movement timing configuration.

    Fields are named ``<chassis>_<terrain>_ticks_per_cell`` (mirroring
    `rules.py`'s flat-scalar-field-per-identity convention rather than a
    dict-valued field, for the same "trivially hashable/equatable, no
    nondeterministic dict-iteration surface" reasons documented there).
    Bipod/tracks have no ``ditch`` field because ditch is categorically
    forbidden terrain for those chassis (locked, `terrain.py`,
    `_specs/technical-spec.md` §13) -- there is no legal duration to store.

    Evidence status per field (see module docstring for the full trail):

    - ``bipod_normal_ticks_per_cell`` / ``tracks_normal_ticks_per_cell`` /
      ``anti_grav_normal_ticks_per_cell``: evidence-backed (disassembly
      flat-terrain row, cross-corroborated against other locked constants
      from the same source).
    - ``anti_grav_ditch_ticks_per_cell``: evidence-backed for "anti-grav is
      slower on the most extreme terrain tier" but the ``DITCH`` mapping
      itself is an interpretive judgment, not an exact categorical match
      (see module docstring caveat).
    - ``bipod_rough_ticks_per_cell`` / ``tracks_rough_ticks_per_cell``:
      **not** verified Spectrum values -- documented placeholders chosen
      only to satisfy the locked qualitative ordering, pending human
      reconciliation of the evidence conflict described in the module
      docstring. Do not treat these two fields as fidelity-complete.
    """

    bipod_normal_ticks_per_cell: int = 24
    tracks_normal_ticks_per_cell: int = 16
    anti_grav_normal_ticks_per_cell: int = 12

    # NOT independently disassembly-verified for their relative severity —
    # see "Rough-terrain severity ordering" in the module docstring. Chosen
    # to keep bipod's rough multiplier (1.5x) strictly greater than tracks'
    # (1.375x), i.e. to satisfy the locked qualitative ordering, without
    # presenting either number as an exact recovered Spectrum constant.
    bipod_rough_ticks_per_cell: int = 36
    tracks_rough_ticks_per_cell: int = 22

    # Evidence-backed: disassembly "mountains" row, anti-grav column
    # (4 cycles * GAME_CYCLE_TICKS) — see the terrain-tier mapping caveat
    # above for why "mountains" -> DITCH is an interpretive, not exact,
    # correspondence.
    anti_grav_ditch_ticks_per_cell: int = 16

    # Evidence-backed: disassembly rugged-terrain row, anti-grav column is
    # identical to its flat-terrain value (3 cycles both), i.e. anti-grav is
    # NOT uniform across *every* traversable terrain type once ditch is
    # included, but it IS uniform across normal/rough specifically.
    anti_grav_rough_ticks_per_cell: int = 12

    def __post_init__(self) -> None:
        for field_name in (
            "bipod_normal_ticks_per_cell",
            "tracks_normal_ticks_per_cell",
            "anti_grav_normal_ticks_per_cell",
            "bipod_rough_ticks_per_cell",
            "tracks_rough_ticks_per_cell",
            "anti_grav_ditch_ticks_per_cell",
            "anti_grav_rough_ticks_per_cell",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be a positive integer")

    def ticks_per_cell(self, chassis: ModuleIdentity, terrain: TerrainType) -> int:
        """Return the ticks a single cell-to-cell move takes for this chassis/terrain.

        Raises ``ValueError`` for a chassis/terrain combination that is not
        legal terrain for that chassis (bipod/tracks on ``DITCH``) rather
        than returning a meaningless duration -- terrain *legality* is owned
        by issue #60's movement executor, not this module, but an illegal
        combination has no duration to report here either way.
        """
        if terrain is TerrainType.NORMAL:
            table = {
                ModuleIdentity.BIPOD: self.bipod_normal_ticks_per_cell,
                ModuleIdentity.TRACKS: self.tracks_normal_ticks_per_cell,
                ModuleIdentity.ANTI_GRAV: self.anti_grav_normal_ticks_per_cell,
            }
        elif terrain is TerrainType.ROUGH:
            table = {
                ModuleIdentity.BIPOD: self.bipod_rough_ticks_per_cell,
                ModuleIdentity.TRACKS: self.tracks_rough_ticks_per_cell,
                ModuleIdentity.ANTI_GRAV: self.anti_grav_rough_ticks_per_cell,
            }
        elif terrain is TerrainType.DITCH:
            if chassis is not ModuleIdentity.ANTI_GRAV:
                raise ValueError(f"{chassis} cannot enter DITCH terrain")
            return self.anti_grav_ditch_ticks_per_cell
        else:  # pragma: no cover - exhaustive over the locked TerrainType enum
            raise ValueError(f"unknown terrain type: {terrain}")

        try:
            return table[chassis]
        except KeyError as exc:
            raise ValueError(f"unknown chassis module identity: {chassis}") from exc


#: Canonical default movement-timing rule set. Calling code should reference
#: this shared instance rather than constructing ad hoc ``MovementRules()``
#: copies, matching `rules.py`'s ``DEFAULT_RULES`` convention.
DEFAULT_MOVEMENT_RULES = MovementRules()
