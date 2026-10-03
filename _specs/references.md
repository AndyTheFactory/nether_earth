# Nether Earth Reference Sources

These references are the primary external sources used to verify gameplay rules, map layout, implementation details, and visual direction for the project.

## 1. Original game instructions / rules

**Source:** ESP32 Rainbow — Nether Earth instructions

https://www.esp32rainbow.com/games/3391

Use this as a primary reference for documented gameplay rules, including:

- battle objectives
- factory behavior and ownership
- resource production
- robot construction
- chassis and weapon characteristics
- electronics effects
- robot control modes and orders
- factory capture timing
- nuclear weapon behavior
- original control scheme

## 2. Reversed / annotated assembly

**Source:** `santiontanon/netherearth-disassembly`

https://github.com/santiontanon/netherearth-disassembly

Use this as a technical fidelity reference for mechanics that are ambiguous or insufficiently documented in the instructions, including:

- internal game-state representation
- movement and timing rules
- robot AI/navigation
- collision and height handling
- projectile behavior
- damage and targeting logic
- component dimensions/heights
- exact constants and limits used by the ZX Spectrum version

## 3. Full original map

**Source:** Speccy.cz — Nether Earth full map

https://maps.speccy.cz/maps/NetherEarth.png

Use this as the visual reference for reconstructing the original battlefield, including:

- overall map dimensions and long rectangular layout
- war-base locations
- factory locations
- terrain placement
- ravines / rough terrain
- static structures and obstacles

The authoritative in-game map will be represented as versioned YAML in the repository rather than derived dynamically from this image.

## 4. Asset inspiration

**Source:** NetherEarthVox 0.94 on ModDB

https://www.moddb.com/downloads/netherearthvox-094

Use this only as **visual and asset inspiration**, not as a gameplay-fidelity source.

It may be useful for:

- interpreting original robot/component silhouettes
- visualizing factories and battlefield objects
- ideas for recreating original assets cleanly at modern browser resolutions
- maintaining the original Nether Earth visual character while using a 2.5D sprite renderer

Gameplay rules should not be inferred from this source when they conflict with the ZX Spectrum version, original instructions, or disassembly.

## 5. Project research notes

These are derived from the sources above and kept in the repository:

- [docs/mechanics/](../docs/mechanics/README.md) — how the engine implements each rule, with the disassembly labels it relies on.
- [docs/cr004/spectrum-ai-notes.md](../docs/cr004/spectrum-ai-notes.md) — a reading of the original's enemy computer player and the verdict per mechanic.
- [data/maps/zx-spectrum-original.md](../data/maps/zx-spectrum-original.md) — provenance of every decoded map cell.
- [docs/reference-screens/](../docs/reference-screens/cr002/) — reference screenshots of the original (play view, construction screen, robot menu).

## Reference priority

When sources disagree, use the following priority unless a specific project decision explicitly overrides the original behavior:

1. observed ZX Spectrum game behavior
2. reversed/annotated ZX Spectrum assembly
3. original game instructions
4. original full-map reference
5. other ports, remakes, and visual references such as NetherEarthVox

Project-specific multiplayer adaptations documented in the functional and technical specs take precedence where the original single-player design cannot be applied directly.
