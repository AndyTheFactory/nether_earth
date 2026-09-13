import { mkdir, readdir, writeFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { compileFromFile } from 'json-schema-to-typescript';

const schemaDir = resolve(process.cwd(), '../protocol/schemas');
const outputDir = resolve(process.cwd(), '../protocol/generated');
const outputFile = resolve(outputDir, 'types.ts');
const files = (await readdir(schemaDir)).filter((name) => name.endsWith('.schema.json')).sort();

await mkdir(outputDir, { recursive: true });
const sections = [];
for (const file of files) {
  const generated = await compileFromFile(resolve(schemaDir, file), {
    bannerComment: `// Generated from ${basename(file)}. Do not edit by hand.`,
  });
  sections.push(generated.trim());
}

await writeFile(outputFile, `${sections.join('\n\n')}\n`, 'utf8');
console.log(`generated ${outputFile}`);
