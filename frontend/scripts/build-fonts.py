"""Build the self-hosted Spectrum-style web fonts (CR002.10).

Two fonts are generated from one 8x8 bitmap glyph table below:

* ``NetherEarth8`` - the 8x8 status/menu lettering (square pixels);
* ``NetherEarthTall`` - the same glyphs with every row doubled (8x16), the
  tall lettering of the original's titles (ROBOT CONSTRUCTION, RADAR).

Every lit pixel becomes a square in the outline, snapped to a 128-unit grid,
so text is pixel-crisp at integer multiples of the design size (8px for the
8x8 font, 16px for the tall one). The glyphs are an original drawing made
for this project in the stencil style of the Nether Earth in-game lettering
(3-pixel stems cut by 1-pixel gaps); see frontend/public/assets/README.md.

Regenerate (fontTools + brotli required):

    uv run --no-project --with fonttools --with brotli python frontend/scripts/build-fonts.py
"""

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

OUT = Path(__file__).resolve().parent.parent / "public" / "fonts"

# 7x7 glyph bodies drawn in an 8x8 cell (column 7 and row 7 stay blank; the
# comma and semicolon tails use row 7). '#' is a lit pixel.
GLYPHS_SRC = r"""
A
.#####.
###.###
###.###
#######
###.###
###.###
###.###
B
######.
###.###
###.##.
#####..
###.##.
###.###
######.
C
.######
###...#
###....
###....
###....
###...#
.######
D
#####..
###.##.
###.###
###.###
###.###
###.##.
#####..
E
#######
###...#
###....
#####..
###....
###...#
#######
F
#######
###...#
###....
#####..
###....
###....
###....
G
.######
###...#
###....
###.###
###..##
###..##
.######
H
###.###
###.###
###.###
#######
###.###
###.###
###.###
I
.#####.
..###..
..###..
..###..
..###..
..###..
.#####.
J
....###
....###
....###
....###
#...###
###.###
.#####.
K
###..##
###.##.
###.#..
#####..
###.##.
###.###
###..##
L
###....
###....
###....
###....
###....
###...#
#######
M
##...##
###.###
#######
#######
###.###
###.###
###.###
N
##...##
###..##
####.##
#######
###.###
###..##
###..##
O
.#####.
###.###
###.###
###.###
###.###
###.###
.#####.
P
######.
###.###
###.###
######.
###....
###....
###....
Q
.#####.
###.###
###.###
###.###
###.###
###.##.
.####.#
R
######.
###.###
###.###
######.
###.##.
###.###
###.###
S
.######
###...#
###....
.#####.
....###
#...###
######.
T
#######
#.###.#
..###..
..###..
..###..
..###..
..###..
U
###.###
###.###
###.###
###.###
###.###
###.###
.#####.
V
###.###
###.###
###.###
###.###
.##.##.
..###..
...#...
W
###.###
###.###
###.###
#######
#######
###.###
##...##
X
###.###
###.###
.#####.
..###..
.#####.
###.###
###.###
Y
###.###
###.###
.#####.
..###..
..###..
..###..
..###..
Z
#######
#...###
...###.
..###..
.###...
###...#
#######
0
.#####.
###.###
###..##
###.#.#
##..###
###.###
.#####.
1
..###..
.####..
..###..
..###..
..###..
..###..
.#####.
2
.#####.
#...###
....###
.#####.
###....
###...#
#######
3
######.
....###
....###
..####.
....###
....###
######.
4
###.##.
###.##.
###.##.
#######
....##.
....##.
....##.
5
#######
###....
###....
######.
....###
#...###
.#####.
6
.#####.
###....
###....
######.
###.###
###.###
.#####.
7
#######
#...###
....###
...###.
..###..
..###..
..###..
8
.#####.
###.###
###.###
.#####.
###.###
###.###
.#####.
9
.#####.
###.###
###.###
.######
....###
....###
.#####.
.
.......
.......
.......
.......
.......
..##...
..##...
,
.......
.......
.......
.......
.......
..##...
..##...
.##....
:
.......
..##...
..##...
.......
..##...
..##...
.......
;
.......
..##...
..##...
.......
..##...
..##...
.##....
-
.......
.......
.......
.#####.
.......
.......
.......
_
.......
.......
.......
.......
.......
.......
#######
'
..##...
..##...
.##....
.......
.......
.......
.......
"
.##.##.
.##.##.
.......
.......
.......
.......
.......
!
..###..
..###..
..###..
..###..
.......
..###..
..###..
?
.#####.
#...###
....###
..###..
..###..
.......
..###..
(
...##..
..##...
.###...
.###...
.###...
..##...
...##..
)
..##...
...##..
...###.
...###.
...###.
...##..
..##...
[
.####..
.###...
.###...
.###...
.###...
.###...
.####..
]
..####.
...###.
...###.
...###.
...###.
...###.
..####.
/
......#
.....##
....##.
...##..
..##...
.##....
##.....
%
##...##
##..##.
...##..
..##...
.##....
##..##.
#...##.
+
.......
..##...
..##...
######.
..##...
..##...
.......
=
.......
.......
######.
.......
######.
.......
.......
<
...##..
..##...
.##....
##.....
.##....
..##...
...##..
>
.##....
..##...
...##..
....##.
...##..
..##...
.##....
*
.......
.#.#.#.
..###..
#######
..###..
.#.#.#.
.......
#
.##.##.
#######
.##.##.
.##.##.
.##.##.
#######
.##.##.
&
.###...
##.##..
.###...
####.##
##.###.
##..##.
.###.##
@
.#####.
##...##
##.####
##.#.##
##.####
##.....
.#####.
|
..##...
..##...
..##...
..##...
..##...
..##...
..##...
·
.......
.......
.......
..##...
..##...
.......
.......
→
.......
...##..
....##.
#######
....##.
...##..
.......
↑
...#...
..###..
.#####.
..###..
..###..
..###..
..###..
✕
.......
##...##
.##.##.
..###..
.##.##.
##...##
.......
…
.......
.......
.......
.......
.......
##.##.#
##.##.#
−
.......
.......
.......
.#####.
.......
.......
.......
✔
.......
......#
.....##
#...##.
##.##..
.###...
..#....
●
.......
..###..
.#####.
.#####.
.#####.
..###..
.......
⏎
......#
......#
..#...#
.##...#
#######
.##....
..#....
"""


def parse_glyphs(src: str) -> dict[str, list[str]]:
    glyphs: dict[str, list[str]] = {}
    lines = [ln for ln in src.strip("\n").split("\n")]
    i = 0
    while i < len(lines):
        key = lines[i]
        ch = key.encode().decode("unicode_escape") if key.startswith("\\u") else key
        rows = []
        i += 1
        while i < len(lines) and set(lines[i]) <= {".", "#"} and len(lines[i]) == 7:
            rows.append(lines[i])
            i += 1
        assert 7 <= len(rows) <= 8, f"glyph {key!r} has {len(rows)} rows"
        glyphs[ch] = rows
    return glyphs


def glyph_name(ch: str) -> str:
    return "space" if ch == " " else f"uni{ord(ch):04X}"


def build(family: str, filename: str, px_w: int, px_h: int) -> None:
    """px_w/px_h: font units per bitmap pixel horizontally / per glyph row vertically."""
    upm = 1024
    ascent = 7 * px_h
    descent = upm - ascent
    advance = 8 * px_w
    glyphs = parse_glyphs(GLYPHS_SRC)

    fb = FontBuilder(upm, isTTF=True)
    order = [".notdef", "space"] + [glyph_name(c) for c in glyphs]
    fb.setupGlyphOrder(order)
    cmap = {32: "space", 0xA0: "space"}
    for c in glyphs:
        cmap[ord(c)] = glyph_name(c)
        if "A" <= c <= "Z":
            cmap[ord(c.lower())] = glyph_name(c)  # the original has capitals only
    fb.setupCharacterMap(cmap)

    outlines = {}
    metrics = {}

    def empty():
        return TTGlyphPen(None).glyph()

    outlines[".notdef"] = empty()
    outlines["space"] = empty()
    metrics[".notdef"] = (advance, 0)
    metrics["space"] = (advance, 0)
    for c, rows in glyphs.items():
        pen = TTGlyphPen(None)
        xmin = None
        for r, row in enumerate(rows):
            top = ascent - r * px_h
            x = 0
            while x < 7:
                if row[x] == "#":
                    start = x
                    while x < 7 and row[x] == "#":
                        x += 1
                    # one rectangle per horizontal run, clockwise (TrueType)
                    x0, x1, y1, y0 = start * px_w, x * px_w, top, top - px_h
                    pen.moveTo((x0, y0))
                    pen.lineTo((x0, y1))
                    pen.lineTo((x1, y1))
                    pen.lineTo((x1, y0))
                    pen.closePath()
                    xmin = x0 if xmin is None else min(xmin, x0)
                else:
                    x += 1
        name = glyph_name(c)
        outlines[name] = pen.glyph()
        metrics[name] = (advance, xmin or 0)
    fb.setupGlyf(outlines)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=-descent, lineGap=0)
    fb.setupNameTable({"familyName": family, "styleName": "Regular", "version": "Version 1.000"})
    fb.setupOS2(
        version=4,
        sTypoAscender=ascent,
        sTypoDescender=-descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=descent,
        fsSelection=0x40 | 0x80,  # REGULAR | USE_TYPO_METRICS
        xAvgCharWidth=advance,
    )
    fb.setupPost(isFixedPitch=1)
    fb.font.flavor = "woff2"
    OUT.mkdir(parents=True, exist_ok=True)
    fb.save(OUT / filename)
    print(f"wrote {OUT / filename} ({len(glyphs)} glyphs)")


if __name__ == "__main__":
    # 8x8: 1024 units = 8 px, so a pixel is 128 units in both directions.
    build("NetherEarth8", "nether-earth-8x8.woff2", 128, 128)
    # tall: 1024 units = 16 px tall; pixels are 64 units wide and rows 128 units (2 px) tall.
    build("NetherEarthTall", "nether-earth-tall.woff2", 64, 128)
