"""Shared "and-or-bitmap-with-size" sprite decoder for the
santiontanon/netherearth-disassembly graphic data (`netherearth-annotated-data.asm`).

Used by decode-scenery-sprites.py and decode-unit-sprites.py so both scripts
read the exact same byte layout the same way. Sprite format: height byte,
width-in-bytes byte, then per row (bottom row first) per byte an AND mask and
an OR byte. Output rows are top row first: '#' ink (OR bit set), '.' paper
(mask clears, no ink), ' ' transparent (mask keeps the background).
"""

import re


def data_bytes(lines: list[str], label: str) -> list[int]:
    out: list[int] = []
    on = False
    for line in lines:
        if line.startswith(label + ":"):
            on = True
            continue
        if on:
            if re.match(r"^L[0-9a-f]{4}", line):
                break
            out += [int(v[1:], 16) for v in re.findall(r"#[0-9a-f]{2}", line.split(";")[0])]
    if not out:
        raise SystemExit(f"label {label} not found")
    return out


def decode(lines: list[str], label: str) -> list[str]:
    b = data_bytes(lines, label)
    h, w, d = b[0], b[1], b[2:]
    rows = []
    for r in range(h):
        s = ""
        for c in range(w):
            mask, ink = d[(r * w + c) * 2], d[(r * w + c) * 2 + 1]
            for bit in range(7, -1, -1):
                if ink >> bit & 1:
                    s += "#"
                elif mask >> bit & 1:
                    s += " "
                else:
                    s += "."
        rows.append(s)
    return rows[::-1]
