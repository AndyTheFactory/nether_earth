"""Decode the Spectrum map-element sprites used for scenery (CR002.5).

Reads the graphic data of santiontanon/netherearth-disassembly
(``netherearth-annotated-data.asm``) and prints
``frontend/src/render/scenery-sprites.ts``.

Evidence (``netherearth-annotated.asm``):

* ``Lcd18_draw_map_cell`` draws a map element (type = low 5 bits of the map
  byte) at the anchor cell of its 2x2 stamp with elevation 0, via
  ``Lcf2d_draw_sprite_to_buffer``; that routine looks up entry
  ``2 * type + (x & 1)`` of ``Ld6e8_additional_isometric_graphic_pointers``
  (the odd entry is the same sprite pre-shifted by 4 pixels).
* Element 17 -> ``La0e2_iso_additional_graphic_27``, 18 ->
  ``La2e6_iso_additional_graphic_29``, 21 -> ``La050_iso_additional_graphic_26``;
  the nuclear blast (``Lba44_robots_handled``) replaces boxes with element
  6 or 7 (debris) -> ``L9d9c_iso_additional_graphic_22`` /
  ``L9ef6_iso_additional_graphic_24``.
* Sprite format ("and-or-bitmap-with-size"): height, width in bytes, then
  per row (bottom row first) per byte an AND mask and an OR byte.

Output rows are top row first: '#' ink (OR bit set), '.' paper (mask
clears, no ink), ' ' transparent (mask keeps the background).

Usage:
    python frontend/scripts/decode-scenery-sprites.py <netherearth-annotated-data.asm> \
        > frontend/src/render/scenery-sprites.ts
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _netherearth_gfx import decode  # noqa: E402

SPRITES = [
    ("spectrum.box_low", 17, "La0e2_iso_additional_graphic_27"),
    ("spectrum.box_high", 18, "La2e6_iso_additional_graphic_29"),
    ("spectrum.fence", 21, "La050_iso_additional_graphic_26"),
    ("spectrum.debris_a", 6, "L9d9c_iso_additional_graphic_22"),
    ("spectrum.debris_b", 7, "L9ef6_iso_additional_graphic_24"),
    # CR-robots: the two wall-segment graphics war bases and factories are
    # built from (see decode-unit-sprites.py / warbase-sprites.ts docstring).
    ("spectrum.wall_low", 15, "L9914_iso_additional_graphic_18"),
    ("spectrum.wall_high", 16, "L9b18_iso_additional_graphic_20"),
    # Terrain (owner request, 2026-09-24). Terrain is stamped from the same
    # 2x2 map elements as everything else, so it decodes through this one
    # path too. Ld6e8_additional_isometric_graphic_pointers holds two entries
    # per type, and Lcf2d_draw_sprite_to_buffer picks between them with the
    # low bit of the sprite's screen x in nibbles, which works out to the
    # element's y parity (`l = e*2 + d - 24`, `sra l`, `adc a, a`, with d = y).
    # For most types both entries are the same pointer, or the second is the
    # first pre-shifted 4 px; the labels below are the even (y even) entry.
    # Classes follow decode_zx_terrain.py's TERRAIN_CLASS -- rough 2-7,
    # mountain 8-11, ditch 12-14. Types 6 and 7 are also the nuclear blast's
    # debris, decoded above, so they are not repeated.
    ("spectrum.rough_a", 2, "L9172_iso_additional_graphic_4"),
    ("spectrum.rough_b", 3, "L91f2_iso_additional_graphic_5"),
    ("spectrum.rough_c", 4, "L9278_iso_additional_graphic_6"),
    ("spectrum.rough_d", 5, "L92f8_iso_additional_graphic_7"),
    ("spectrum.mountain_a", 8, "L9372_iso_additional_graphic_8"),
    ("spectrum.mountain_b", 9, "L940a_iso_additional_graphic_9"),
    ("spectrum.mountain_c", 10, "L94a8_iso_additional_graphic_10"),
    ("spectrum.mountain_d", 11, "L9534_iso_additional_graphic_11"),
    # Ditches are the one class whose two entries are different drawings
    # rather than the same drawing pre-shifted: the even entry is the piece
    # for a ditch running along y (drawn "vertically" on screen) and the odd
    # entry the piece for one running along x. The original map places every
    # vertical ditch run on an even y and every horizontal run on an odd y
    # (all 51 ditch elements), so the y parity the Spectrum indexes with is
    # also the run's orientation.
    ("spectrum.ditch_v_a", 12, "L95c6_iso_additional_graphic_12"),
    ("spectrum.ditch_v_b", 13, "L96ea_iso_additional_graphic_14"),
    ("spectrum.ditch_v_c", 14, "L9820_iso_additional_graphic_16"),
    ("spectrum.ditch_h_a", 12, "L9640_iso_additional_graphic_13"),
    ("spectrum.ditch_h_b", 13, "L9776_iso_additional_graphic_15"),
    ("spectrum.ditch_h_c", 14, "L98ac_iso_additional_graphic_17"),
]

#: Decorations drawn on top of a structure rather than stamped into the map
#: (`Lce38_draw_decoration`). `Lce56_decoration_sprite_indexes` gives the
#: war-base "H" landing pad index #2c: doubled by `Lcf2d`'s `adc a, a` that
#: is an offset of 88 entries from Ld6e8_additional_isometric_graphic_pointers,
#: which is entry 44 of the Ld740_isometric_graphic_pointers that follow it.
#: The odd entry beside it is the same drawing pre-shifted 4 px, so only the
#: even one is decoded.
DECORATION_SPRITES = [
    ("spectrum.heli_pad", "L87f0_iso_graphic_44"),
]


def main() -> None:
    lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
    print("// GENERATED by frontend/scripts/decode-scenery-sprites.py from the")
    print("// santiontanon/netherearth-disassembly graphic data; do not edit by hand.")
    print("// Provenance and licensing: frontend/public/assets/README.md.")
    print("// Rows top first: '#' ink, '.' paper, ' ' transparent.")
    print("")
    print("export const SCENERY_SPRITES: Record<string, readonly string[]> = {")
    for sid, element, label in SPRITES:
        rows = decode(lines, label)
        print(f"  // map element {element}: {label} ({len(rows[0])}x{len(rows)})")
        print(f"  '{sid}': [")
        for row in rows:
            print(f"    '{row}',")
        print("  ],")
    for sid, label in DECORATION_SPRITES:
        rows = decode(lines, label)
        print(f"  // decoration: {label} ({len(rows[0])}x{len(rows)})")
        print(f"  '{sid}': [")
        for row in rows:
            print(f"    '{row}',")
        print("  ],")
    print("};")


if __name__ == "__main__":
    main()
