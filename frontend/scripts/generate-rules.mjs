// Export the construction module costs from the engine's single source of
// truth (engine/src/nether_earth/rules.py, `EngineRules.module_cost_*`
// defaults) into JSON the construction screen can import (CR002.9). Pure
// format conversion: the values are read, never derived. The snapshot does
// not carry costs, so the UI must not hard-code them; CI regenerates this
// file and fails on drift, like the map export.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const MODULES = ['bipod', 'tracks', 'anti_grav', 'cannon', 'missile', 'phaser', 'nuclear', 'electronics'];
const src = await readFile(resolve(process.cwd(), '../engine/src/nether_earth/rules.py'), 'utf8');
const moduleCosts = {};
for (const m of MODULES) {
  const hits = [...src.matchAll(new RegExp(`^\\s+module_cost_${m}: int = (\\d+)\\s*$`, 'gm'))];
  if (hits.length !== 1) throw new Error(`rules.py: expected one module_cost_${m} default, found ${hits.length}`);
  moduleCosts[m] = Number(hits[0][1]);
}
const outDir = resolve(process.cwd(), 'src/generated/rules');
await mkdir(outDir, { recursive: true });
const out = { source: 'engine/src/nether_earth/rules.py EngineRules.module_cost_*', module_costs: moduleCosts };
await writeFile(resolve(outDir, 'construction.json'), JSON.stringify(out, null, 2) + '\n');
console.log('wrote src/generated/rules/construction.json');
