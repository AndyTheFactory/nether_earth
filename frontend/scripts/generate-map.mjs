// Convert data/maps/<id>.yaml (the engine's versioned map source of truth) into
// JSON the browser can import. Pure format conversion: no cell, height, or
// interaction value is derived, filtered, or reinterpreted here. The engine's
// `nether_earth.map.load_map` remains the only authority on map semantics.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import yaml from 'js-yaml';

const MAP_IDS = ['zx-spectrum-original'];
const srcDir = resolve(process.cwd(), '../data/maps');
const outDir = resolve(process.cwd(), 'src/generated/maps');
await mkdir(outDir, { recursive: true });

for (const id of MAP_IDS) {
  const raw = yaml.load(await readFile(resolve(srcDir, `${id}.yaml`), 'utf8'));
  if (raw.id !== id) throw new Error(`${id}.yaml declares id ${raw.id}`);
  const out = {
    id: raw.id,
    version: raw.version,
    width: raw.width,
    height: raw.height,
    terrain: {
      default: raw.terrain?.default ?? 'normal',
      debris_height: raw.terrain?.debris_height ?? 0,
      cells: raw.terrain?.cells ?? [],
    },
    war_bases: raw.war_bases ?? [],
    factories: raw.factories ?? [],
    blockers: raw.blockers ?? [],
    interaction_points: raw.interaction_points ?? [],
  };
  await writeFile(resolve(outDir, `${id}.json`), JSON.stringify(out) + '\n');
  console.log(`wrote src/generated/maps/${id}.json`);
}
