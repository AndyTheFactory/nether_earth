"""Decode the original ZX Spectrum map's terrain and scenery layers.

Reproduces the map buffer that `Lbc6f_initialize_map` builds in
`netherearth-annotated.asm` (santiontanon/netherearth-disassembly) and prints
either the ``terrain.cells`` YAML block (CR001.5, issue #152) or the
``blockers`` YAML section (CR002.1, issue #168) for
`zx-spectrum-original.yaml`. See `zx-spectrum-original.md`, "Terrain" and
"Blockers/scenery", for the evidence trail.

Each terrain cell carries its piece height from ``Ld7bc_map_piece_heights``
and the section carries ``debris_height`` (CR002.21, issue #203).

Usage::

    python data/maps/decode_zx_terrain.py /path/to/netherearth-annotated.asm [terrain|blockers]

Only the element *type index* (``and #1f``) matters here; the bit-5 "not the
bottom-left corner" and bit-6 "building decoration" flags are ignored, like
`Lb513_get_robot_movement_possibilities` and `Ld7bc_map_piece_heights` do.
"""

import re
import sys
from collections import Counter
from pathlib import Path

MAP_LENGTH = 512  # x
MAP_WIDTH = 16  # y

# `_specs/open-questions.md` §4: element type index -> terrain class.
TERRAIN_CLASS = {
    **{t: "normal" for t in (0, 1)},
    **{t: "rough" for t in range(2, 8)},
    **{t: "mountain" for t in range(8, 12)},
    **{t: "ditch" for t in range(12, 15)},
}

# Scenery element type index -> blocker ``kind``. Heights come from
# `Ld7bc_map_piece_heights` (17 -> 7, 18 -> 15, 21 -> 99). Only type 21 is
# named in the disassembly ("the fences that mark the end of the map", at
# `Lba44_robots_handled`); ``box_low``/``box_high`` are descriptive labels.
BLOCKER_KIND = {17: "box_low", 18: "box_high", 21: "fence"}
# `Ld7bc_map_piece_heights` (23 entries, indexed by element type): the height
# the ship collision/gravity (`Lb052_check_player_collision`), bullets and
# robots (`Lb5d6_map_altitude_2x2`) read for a map piece. Read from the
# disassembly by `_piece_heights`; this literal is the checked copy.
PIECE_HEIGHTS = (0, 0, 2, 2, 2, 2, 3, 3, 6, 6, 6, 6, 0, 0, 0, 7, 15, 7, 15, 0, 0, 99, 0)
# The nuclear blast writes element 6 or 7 (`Lba44_robots_handled`); both are
# 3 high, so the debris height does not depend on the random choice.
DEBRIS_TYPES = (6, 7)
# `Lba44_robots_handled` (the nuclear blast, CR002.18 #196) turns element types
# 17-20 into rough debris; type 21 (fence) survives. Emitted as
# ``destructible: true`` so the engine never has to branch on ``kind``.
DESTRUCTIBLE_TYPES = range(17, 21)


def _label_bytes(asm: str, label: str) -> list[int]:
    """Return the ``db`` bytes that follow ``label:`` up to the next label."""
    start = asm.index(f"\n{label}:")
    out: list[int] = []
    for line in asm[start + 1 :].splitlines()[1:]:
        code = line.split(";", 1)[0].strip()
        if re.match(r"^L[0-9a-f]{4}\w*:", code):
            break
        if code.startswith("db "):
            out.extend(int(tok.strip().lstrip("#"), 16) for tok in code[3:].split(","))
    return out


def _piece_heights(asm: str) -> tuple[int, ...]:
    """Return `Ld7bc_map_piece_heights`, checked against :data:`PIECE_HEIGHTS`."""
    heights = tuple(_label_bytes(asm, "Ld7bc_map_piece_heights"))
    if heights != PIECE_HEIGHTS:
        raise ValueError(f"Ld7bc_map_piece_heights changed: {heights}")
    return heights


def _signed(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


Element = tuple[int, list[tuple[int, int]]]


def _decode(asm: str) -> tuple[list[list[int]], dict[tuple[int, int], int], list[Element]]:
    """Return ``(grid, owner, elements)``.

    ``grid[y][x]`` is the final type index (0 = empty), ``elements`` lists every
    stamped ``(type, cells)`` element in stamping order, and ``owner`` maps a
    cell to the index of the element that wrote it last.
    """
    grid = [[0] * MAP_LENGTH for _ in range(MAP_WIDTH)]
    owner: dict[tuple[int, int], int] = {}
    elements: list[Element] = []
    templates = {
        0: _label_bytes(asm, "Lbfb2_warbase"),
        1: _label_bytes(asm, "Lbfe2_factory"),
        **{
            i: _label_bytes(asm, name)
            for i, name in enumerate(
                ["Lbff4", "Lc018", "Lc03c", "Lc048", "Lc054", "Lc060", "Lc06c", "Lc078", "Lc084"],
                start=2,
            )
        },
    }

    def add_element(x: int, y: int, element: int) -> None:
        # Lbd91_add_element_to_map: a 2x2 block at x..x+1, y-1..y.
        cells: list[tuple[int, int]] = []
        for dy in (0, -1):
            for dx in (0, 1):
                cx, cy = x + dx, y + dy
                if not (0 <= cx < MAP_LENGTH and 0 <= cy < MAP_WIDTH):
                    raise ValueError(f"element {element:#x} stamps out of bounds at {(cx, cy)}")
                grid[cy][cx] = element & 0x1F
                owner[(cx, cy)] = len(elements)
                cells.append((cx, cy))
        elements.append((element & 0x1F, cells))

    def add_complex(x: int, y: int, index: int) -> None:
        # Lbd61_add_complex_structure_to_map.
        data = templates[index & 0x7F]
        for i in range(0, len(data), 3):
            element, ox, oy = data[i], data[i + 1], data[i + 2]
            if element:
                add_element(x, y, element)
            if ox == 0 and oy == 0:
                return
            x, y = x + _signed(ox), y + _signed(oy)
        raise ValueError(f"complex structure {index:#x} has no terminator")

    # Lbcd6_add_elements_to_map (terminated by 0; msb set = complex structure).
    for label, high in (("Lbda9_map_elements_part1", 0), ("Lbe79_map_elements_part2", 256)):
        data = _label_bytes(asm, label)
        i = 0
        while data[i] != 0:
            element, x, y = data[i], data[i + 1] + high, data[i + 2]
            (add_complex if element & 0x80 else add_element)(x, y, element)
            i += 3
    # Lbcf9_add_warbases_and_factories_to_map (terminated by msb set).
    for label, high in (("Lbf46_warbases_factories_part1", 0), ("Lbf6e_warbases_factories_part2", 256)):
        data = _label_bytes(asm, label)
        i = 0
        while not data[i] & 0x80:
            kind, x, y = data[i], data[i + 1] + high, data[i + 2]
            add_complex(x, y, 0x80 if kind == 0 else 0x81)
            i += 3
    return grid, owner, elements


def decode(asm: str) -> list[list[int]]:
    """Return the map buffer as ``grid[y][x] = type index`` (0 = empty)."""
    return _decode(asm)[0]


def decode_blockers(asm: str) -> list[tuple[str, list[tuple[int, int]]]]:
    """Return one ``(kind, cells)`` per scenery element, sorted by its cells.

    Each scenery element is one 2x2 stamp. Cells a later stamp overwrote are
    dropped (none are on the original map; every scenery element survives
    whole).
    """
    _, owner, elements = _decode(asm)
    out = []
    for index, (element, cells) in enumerate(elements):
        if element not in BLOCKER_KIND:
            continue
        kept = sorted(c for c in cells if owner[c] == index)
        if kept:
            out.append((BLOCKER_KIND[element], kept))
    return sorted(out, key=lambda b: b[1])


def main() -> None:
    asm = Path(sys.argv[1]).read_text(encoding="utf-8")
    if len(sys.argv) > 2 and sys.argv[2] == "blockers":
        blockers = decode_blockers(asm)
        kinds = Counter(kind for kind, _ in blockers)
        print(f"# blockers per kind: {dict(sorted(kinds.items()))}", file=sys.stderr)
        heights = {kind: _piece_heights(asm)[t] for t, kind in BLOCKER_KIND.items()}
        destructible = {kind for t, kind in BLOCKER_KIND.items() if t in DESTRUCTIBLE_TYPES}
        print("blockers:")
        for n, (kind, cells) in enumerate(blockers, start=1):
            print(f"  - id: blocker-{n}")
            print(f"    kind: {kind}")
            if kind in destructible:
                print("    destructible: true")
            print("    components:")
            for x, y in cells:
                print(f"      - {{x: {x}, y: {y}, height: {heights[kind]}}}")
        return
    piece_heights = _piece_heights(asm)
    (debris_height,) = {piece_heights[t] for t in DEBRIS_TYPES}
    grid = decode(asm)
    per_type = Counter(t for row in grid for t in row)
    per_class = Counter(TERRAIN_CLASS.get(t, "structure/scenery") for row in grid for t in row)
    print(f"# element type index counts: {dict(sorted(per_type.items()))}", file=sys.stderr)
    print(f"# class counts: {dict(sorted(per_class.items()))}", file=sys.stderr)
    print(f"  debris_height: {debris_height}")
    print("  cells:")
    for x in range(MAP_LENGTH):
        for y in range(MAP_WIDTH):
            cls = TERRAIN_CLASS.get(grid[y][x])
            if cls not in (None, "normal"):
                height = piece_heights[grid[y][x]]
                print(f"    - {{x: {x}, y: {y}, type: {cls}, height: {height}}}")


if __name__ == "__main__":
    main()
