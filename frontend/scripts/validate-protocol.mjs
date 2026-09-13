import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';

const schemaDir = resolve(process.cwd(), '../protocol/schemas');
const files = (await readdir(schemaDir)).filter((name) => name.endsWith('.schema.json')).sort();
const ajv = new Ajv2020({ strict: true });

for (const file of files) {
  const schema = JSON.parse(await readFile(resolve(schemaDir, file), 'utf8'));
  ajv.compile(schema);
  console.log(`validated ${file}`);
}
