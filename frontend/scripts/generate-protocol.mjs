import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { compile } from 'json-schema-to-typescript';

const schemaDir = resolve(process.cwd(), '../protocol/schemas');
const outputDir = resolve(process.cwd(), '../protocol/generated');
const outputFile = resolve(outputDir, 'types.ts');
const files = (await readdir(schemaDir)).filter((name) => name.endsWith('.schema.json')).sort();

const PROTOCOL_ORIGIN = 'https://nether-earth.local/protocol/';

// Map each schema file's exported top-level type name to its $id. All schema files
// are compiled together in a single pass (below) so that shared/cross-file $ref
// targets (e.g. reconnect.schema.json's ServerResync referencing snapshot.schema.json)
// are only declared once in the generated output, instead of once per referencing file.
const rootDefs = {};
for (const file of files) {
  const schema = JSON.parse(await readFile(resolve(schemaDir, file), 'utf8'));
  const exportName = schema.title;
  if (!exportName) {
    throw new Error(`${file} has no top-level "title"; every schema file must declare one to name its generated TS export`);
  }
  rootDefs[exportName] = { $ref: schema.$id };
}

const bundleRoot = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  $id: `${PROTOCOL_ORIGIN}_generated_bundle.schema.json`,
  $defs: rootDefs,
};

// protocol/schemas/*.schema.json use $ids under https://nether-earth.local/protocol/,
// which is a namespace, not a real network location. Resolve those ourselves from disk
// instead of letting $RefParser attempt an HTTP request.
const netherEarthResolver = {
  order: 1,
  canRead: (file) => file.url.startsWith(PROTOCOL_ORIGIN),
  read: async (file) => {
    const relativePath = file.url.slice(PROTOCOL_ORIGIN.length);
    return readFile(resolve(schemaDir, relativePath), 'utf8');
  },
};

await mkdir(outputDir, { recursive: true });

const generated = await compile(bundleRoot, 'ProtocolBundle', {
  bannerComment: '// Generated from protocol/schemas/*.schema.json. Do not edit by hand.',
  unreachableDefinitions: true,
  $refOptions: {
    resolve: {
      http: false,
      netherEarth: netherEarthResolver,
    },
  },
});

// The synthetic bundle root itself has no meaningful shape (it only exists to hold
// $defs so cross-file $refs dedupe); drop its generated placeholder declaration
// (named after the bundle's own $id since it has no "title").
const withoutBundleRootType = generated
  .split(/\n(?=export )/)
  .filter((block) => !/^export (interface|type) (ProtocolBundle|HttpsNetherEarthLocalProtocol\w*BundleSchemaJson)\b/.test(block))
  .join('\n')
  // Strip provenance doc-comments pointing at the (now-removed) synthetic bundle root;
  // they're accurate but reference an internal name that no longer appears in the file.
  .replace(/\/\*\*\n( \*[^\n]*\n)*? \* This interface was referenced by `HttpsNetherEarthLocalProtocol\w*BundleSchemaJson`'s JSON-Schema\n \* via the `definition` "[^"]*"\.\n \*\/\n/g, '');

await writeFile(outputFile, withoutBundleRootType.trimEnd() + '\n', 'utf8');
console.log(`generated ${outputFile}`);
