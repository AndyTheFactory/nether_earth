// CR002.6: structure ownership flags, as the Spectrum draws them.
//
// Evidence (santiontanon/netherearth-disassembly):
// - Lbb61_assign_factory_to_player / Lbb86_assign_warbase_to_player remove
//   both potential flags, then add decoration 7 (owner 0, the human player)
//   or 8 (owner 1, the Insignian AI) on the roof, 2 (factory) or 4 (war base)
//   rows behind the structure's anchor cell and the same distance to its -x
//   side (human) or +x side (Insignian). Neutral (and destroyed) structures
//   carry no flag.
// - Owner 0 starts with war base 0 (leftmost), owner 1 with war base 3
//   (rightmost); this clone gives p1 the leftmost and p2 the rightmost base
//   (open-questions §2), so p1 ↔ human flag and p2 ↔ Insignian flag.
// - Decorations 7/8 draw sprites #2a/#2b (L86b4_iso_graphic_40 and
//   L8752_iso_graphic_42): a pole with its cloth to the right; the human
//   flag's cloth is striped, the Insignian one's checkered.
import type { MapData } from '../world/map.ts';

export type FlagOwner = 'p1' | 'p2';

/** Which side of the structure (along x) carries each owner's flag. */
export const FLAG_SIDE: Record<FlagOwner, -1 | 1> = { p1: -1, p2: 1 };

/** Cells from the anchor (behind it and to the side) per structure kind. */
const FLAG_OFFSET = { warbase_capture: 4, factory_capture: 2 } as const;

/**
 * The flag sprites, top row first ('#' ink, '.' paper, ' ' transparent).
 * Column 1 is the pole; its bottom row stands on the roof.
 */
export const FLAG_SPRITES: Record<FlagOwner, readonly string[]> = {
  p1: ['    ####', ' ####..#', ' #..####', ' #######', ' ####..#', ' #..### ', ' ###    ', ' #      ', ' #      ', '###     ', ' #      '],
  p2: ['    ####', ' ####.##', ' #.#.#.#', ' ##.#.##', ' #.#.#.#', ' ##.### ', ' ###    ', ' #      ', ' #      ', '###     ', ' #      '],
};
export const FLAG_POLE_COLUMN = 1;

export interface OwnershipFlag {
  structureId: string;
  owner: FlagOwner;
  /** Roof cell the flag stands on. */
  x: number;
  y: number;
}

/** Flags for every owned, standing war base and factory. */
export function ownershipFlags(
  map: MapData,
  ownership: readonly { structure_id: string; owner: string | null }[],
  destroyed: ReadonlySet<string> = new Set(),
): OwnershipFlag[] {
  const owners = new Map(ownership.map((o) => [o.structure_id, o.owner]));
  const flags: OwnershipFlag[] = [];
  for (const ip of map.interaction_points) {
    if (ip.kind !== 'warbase_capture' && ip.kind !== 'factory_capture') continue;
    const owner = owners.get(ip.structure_id);
    if ((owner !== 'p1' && owner !== 'p2') || destroyed.has(ip.structure_id)) continue;
    const off = FLAG_OFFSET[ip.kind];
    flags.push({ structureId: ip.structure_id, owner, x: ip.footprint.x + FLAG_SIDE[owner] * off, y: ip.footprint.y - off });
  }
  return flags;
}
