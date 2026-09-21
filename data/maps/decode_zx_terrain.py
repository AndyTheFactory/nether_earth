"""Decode the original ZX Spectrum map's terrain layer (CR001.5, issue #152).

Reproduces the map buffer that `Lbc6f_initialize_map` builds in
`netherearth-annotated.asm` (santiontanon/netherearth-disassembly) and prints
the resulting ``terrain.cells`` YAML block for `zx-spectrum-original.yaml`.
See `zx-spectrum-original.md`, "Terrain", for the evidence trail.

Usage::

    python data/maps/decode_zx_terrain.py /path/to/netherearth-annotated.asm

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


def _signed(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def decode(asm: str) -> list[list[int]]:
    """Return the map buffer as ``grid[y][x] = type index`` (0 = empty)."""
    grid = [[0] * MAP_LENGTH for _ in range(MAP_WIDTH)]
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
        for dy in (0, -1):
            for dx in (0, 1):
                cx, cy = x + dx, y + dy
                if not (0 <= cx < MAP_LENGTH and 0 <= cy < MAP_WIDTH):
                    raise ValueError(f"element {element:#x} stamps out of bounds at {(cx, cy)}")
                grid[cy][cx] = element & 0x1F

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
    return grid


def main() -> None:
    grid = decode(Path(sys.argv[1]).read_text(encoding="utf-8"))
    per_type = Counter(t for row in grid for t in row)
    per_class = Counter(TERRAIN_CLASS.get(t, "structure/scenery") for row in grid for t in row)
    print(f"# element type index counts: {dict(sorted(per_type.items()))}", file=sys.stderr)
    print(f"# class counts: {dict(sorted(per_class.items()))}", file=sys.stderr)
    print("  cells:")
    for x in range(MAP_LENGTH):
        for y in range(MAP_WIDTH):
            cls = TERRAIN_CLASS.get(grid[y][x])
            if cls not in (None, "normal"):
                print(f"    - {{x: {x}, y: {y}, type: {cls}}}")


if __name__ == "__main__":
    main()
