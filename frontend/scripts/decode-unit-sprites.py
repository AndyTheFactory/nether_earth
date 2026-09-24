"""Decode the Spectrum robot-piece and commander sprites (owner-directed
extension of the CR002.5 scenery pipeline, 2026-09-22).

Reads the graphic data of santiontanon/netherearth-disassembly
(``netherearth-annotated-data.asm``) and prints either
``frontend/src/render/robot-sprites.ts`` or
``frontend/src/render/commander-sprites.ts``.

Evidence (``netherearth-annotated.asm``):

Robot pieces
------------
``Lcefd_draw_robot_piece_to_buffer`` draws each selected piece (bit test of
``ROBOT_STRUCT_PIECES``) by looking up ``Ld6c8_piece_direction_graphic_indices``
at ``4 * piece + direction`` (piece 0 = bipod .. 7 = electronics; direction is
one of 4 cardinal directions decoded from the robot's one-hot
``ROBOT_STRUCT_DIRECTION``), adding 22 to skip past the 44-entry
``Ld6e8_additional_isometric_graphic_pointers`` table, and drawing the sprite
at (that index - 22) * 2 of the following ``Ld740_isometric_graphic_pointers``
table (58 pointers, confirmed by its own inline per-piece comments: "tracks",
"bipod", "antigrav", "cannon", "missiles", "phaser", "nuclear"). This script decodes
all 4 columns of ``Ld6c8_piece_direction_graphic_indices`` per piece (owner
request, 2026-09-23; the snapshot protocol now carries a ``facing`` field,
which closes the gap ``_specs/open-questions.md`` logged under "Known gap,
not resolved here: robot facing").

The column order below -- east, west, south, north -- is read off the code,
not assumed. ``Lcefd_draw_robot_piece_to_buffer``'s ``Lcf08_direction_loop``
shifts the one-hot ``ROBOT_STRUCT_DIRECTION`` right until carry, counting
the set bit's position into ``b``, and indexes the table at
``4 * piece + b``; ``Lb724_bullet_update_internal``'s ``rrca`` chain walks
the same bits as right, left, down, up, i.e. ``+x``, ``-x``, ``+y``, ``-y``.

Full 4-direction table (piece: dir0, dir1, dir2, dir3), each entry is a label
in ``Ld740_isometric_graphic_pointers``:

* bipod:       L6c8a, L6c8a, L6e32, L6e32
* tracks:      L6980, L6980, L6afe, L6afe
* anti_grav:   L6fcc, L6fcc, L6fcc, L6fcc
* cannon:      L7112, L729e, L7446, L75ee
* missile:     L7750, L7750, L78ce, L78ce
* phaser:      L7a5a, L7be6, L7d80, L7f1a
* nuclear:     L808a, L808a, L808a, L808a
* electronics: L8224, L8348, L846c, L8590

Bullets
-------
``Lcec3_draw_robot_or_bullet_internal`` builds the sprite index from the
bullet's type (``BULLET_STRUCT_TYPE``: 1 cannon, 2 missiles, 3 phasers) and
its direction: ``cp 3 / ccf / rl c`` shifts "the direction is south or north"
into the low bit of the type, and ``ld a, 43 / add a, c`` gives the index.
So a bullet has two sprites, one for travel along x (east/west) and one for
travel along y (south/north), and no per-direction art beyond that. Resolved
the same way as the robot pieces, the six entries are:

* cannon:  L88e2_iso_graphic_46 (x), L8966_iso_graphic_48 (y)
* missile: L8a06_iso_graphic_50 (x), L8b3e_iso_graphic_52 (y)
* phaser:  L8c76_iso_graphic_54 (x), L8d46_iso_graphic_56 (y)

Commander
---------
``Lcd83_render_player`` (the on-foot commander figure, distinct from a robot)
always draws graphic index 0 (``xor a``) of
``Ld6e8_additional_isometric_graphic_pointers``, i.e.
``L8e3a_iso_additional_graphic_0`` -- one frame, no directional facing, no
selectable pieces.

Usage:
    python frontend/scripts/decode-unit-sprites.py robot <data.asm> \\
        > frontend/src/render/robot-sprites.ts
    python frontend/scripts/decode-unit-sprites.py commander <data.asm> \\
        > frontend/src/render/commander-sprites.ts
    python frontend/scripts/decode-unit-sprites.py bullet <data.asm> \\
        > frontend/src/render/bullet-sprites.ts
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _netherearth_gfx import decode  # noqa: E402

#: Facing order of the 4 ``Ld6c8_piece_direction_graphic_indices`` columns.
#: See the module docstring: this ordering is read off ``Lcf08_direction_loop``'s
#: set-bit index and ``Lb724``'s ``rrca`` chain (east 1, west 2, south 4, north 8).
FACINGS = ("east", "west", "south", "north")

#: One label per (piece, facing), in ``FACINGS`` order. Read straight off
#: ``Ld6c8_piece_direction_graphic_indices``; repeats are the table's own
#: (anti_grav and nuclear reuse one sprite for all 4 directions,
#: bipod/tracks/missile one per pair).
ROBOT_PIECE_SPRITES = [
    ("bipod", ("L6c8a_iso_graphic_4", "L6c8a_iso_graphic_4", "L6e32_iso_graphic_6", "L6e32_iso_graphic_6")),
    ("tracks", ("L6980_iso_graphic_0", "L6980_iso_graphic_0", "L6afe_iso_graphic_2", "L6afe_iso_graphic_2")),
    ("anti_grav", ("L6fcc_iso_graphic_8",) * 4),
    ("cannon", ("L7112_iso_graphic_10", "L729e_iso_graphic_12", "L7446_iso_graphic_14", "L75ee_iso_graphic_16")),
    ("missile", ("L7750_iso_graphic_18", "L7750_iso_graphic_18", "L78ce_iso_graphic_20", "L78ce_iso_graphic_20")),
    ("phaser", ("L7a5a_iso_graphic_22", "L7be6_iso_graphic_24", "L7d80_iso_graphic_26", "L7f1a_iso_graphic_28")),
    ("nuclear", ("L808a_iso_graphic_30",) * 4),
    ("electronics", ("L8224_iso_graphic_32", "L8348_iso_graphic_34", "L846c_iso_graphic_36", "L8590_iso_graphic_38")),
]

COMMANDER_SPRITE = ("spectrum.commander", "L8e3a_iso_additional_graphic_0")

#: One label per (bullet weapon, travel axis); see the module docstring.
BULLET_SPRITES = [
    ("cannon", ("L88e2_iso_graphic_46", "L8966_iso_graphic_48")),
    ("missile", ("L8a06_iso_graphic_50", "L8b3e_iso_graphic_52")),
    ("phaser", ("L8c76_iso_graphic_54", "L8d46_iso_graphic_56")),
]

BULLET_AXES = ("x", "y")


def emit_robot(lines: list[str]) -> None:
    print("// GENERATED by frontend/scripts/decode-unit-sprites.py (robot) from the")
    print("// santiontanon/netherearth-disassembly graphic data; do not edit by hand.")
    print("// Provenance and licensing: frontend/public/assets/README.md.")
    print("// Rows top first: '#' ink, '.' paper, ' ' transparent.")
    print("// One sprite per (piece, facing). Several pieces reuse one sprite for")
    print("// more than one facing -- that repetition is the disassembly's own table,")
    print("// not a decode shortcut. The facing order is read off Lcf08_direction_loop's")
    print("// set-bit index and Lb724's rrca chain (east 1, west 2, south 4, north 8).")
    print("import type { ModuleId, RobotFacing } from './robot.ts';")
    print("")
    print("export const ROBOT_SPRITES: Record<ModuleId, Record<RobotFacing, readonly string[]>> = {")
    for module_id, labels in ROBOT_PIECE_SPRITES:
        print(f"  {module_id}: {{")
        for facing, label in zip(FACINGS, labels, strict=True):
            rows = decode(lines, label)
            print(f"    // {label}, {len(rows[0])}x{len(rows)}")
            print(f"    {facing}: [")
            for row in rows:
                print(f"      '{row}',")
            print("    ],")
        print("  },")
    print("};")


def emit_commander(lines: list[str]) -> None:
    sid, label = COMMANDER_SPRITE
    rows = decode(lines, label)
    print("// GENERATED by frontend/scripts/decode-unit-sprites.py (commander) from the")
    print("// santiontanon/netherearth-disassembly graphic data; do not edit by hand.")
    print("// Provenance and licensing: frontend/public/assets/README.md.")
    print("// Rows top first: '#' ink, '.' paper, ' ' transparent.")
    print("// One frame, no directional facing (Lcd83_render_player always draws")
    print("// graphic index 0; see this script's module docstring).")
    print("")
    print("export const COMMANDER_SPRITES: Record<string, readonly string[]> = {")
    print(f"  // {label} ({len(rows[0])}x{len(rows)})")
    print(f"  '{sid}': [")
    for row in rows:
        print(f"    '{row}',")
    print("  ],")
    print("};")


def emit_bullets(lines: list[str]) -> None:
    print("// GENERATED by frontend/scripts/decode-unit-sprites.py (bullet) from the")
    print("// santiontanon/netherearth-disassembly graphic data; do not edit by hand.")
    print("// Provenance and licensing: frontend/public/assets/README.md.")
    print("// Rows top first: '#' ink, '.' paper, ' ' transparent.")
    print("// One sprite per (weapon, travel axis): the Spectrum indexes bullet art")
    print("// by type and by whether the bullet travels along x or along y only")
    print("// (Lcec3_draw_robot_or_bullet_internal; see the module docstring).")
    print("")
    print("export type BulletAxis = 'x' | 'y';")
    print("")
    print("export const BULLET_SPRITES: Record<string, Record<BulletAxis, readonly string[]>> = {")
    for weapon, labels in BULLET_SPRITES:
        print(f"  {weapon}: {{")
        for axis, label in zip(BULLET_AXES, labels, strict=True):
            rows = decode(lines, label)
            print(f"    // {label}, {len(rows[0])}x{len(rows)}")
            print(f"    {axis}: [")
            for row in rows:
                print(f"      '{row}',")
            print("    ],")
        print("  },")
    print("};")


def main() -> None:
    kind = sys.argv[1]
    lines = open(sys.argv[2], encoding="utf-8").read().split("\n")
    if kind == "robot":
        emit_robot(lines)
    elif kind == "commander":
        emit_commander(lines)
    elif kind == "bullet":
        emit_bullets(lines)
    else:
        raise SystemExit(f"unknown kind {kind!r}; expected 'robot', 'commander' or 'bullet'")


if __name__ == "__main__":
    main()
