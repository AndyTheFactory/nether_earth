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

// A distinctive, self-chosen name (not derived from $id-mangling, which is an internal
// json-schema-to-typescript detail that can change between versions) so we can
// structurally find and strip this placeholder's own declaration below.
const BUNDLE_ROOT_NAME = 'GeneratedProtocolBundleRoot';

const bundleRoot = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  $id: `${PROTOCOL_ORIGIN}_generated_bundle.schema.json`,
  title: BUNDLE_ROOT_NAME,
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

const generated = await compile(bundleRoot, BUNDLE_ROOT_NAME, {
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
// $defs so cross-file $refs dedupe); drop its generated placeholder declaration and
// any doc-comment json-schema-to-typescript attached elsewhere that references it by
// name. This is matched structurally against BUNDLE_ROOT_NAME (a name we chose), not
// against the tool's internal $id-mangling or exact comment wording, and the number of
// blocks touched is asserted below so a future json-schema-to-typescript version that
// changes its output shape fails the build loudly instead of silently leaking the
// placeholder (or stray internal-name comments) into committed generated/types.ts.
const rootNamePattern = BUNDLE_ROOT_NAME.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const blocks = generated.split(/\n(?=export )/);

const rootDeclarationBlocks = blocks.filter((block) =>
  new RegExp(`^export (interface|type) ${rootNamePattern}\\b`).test(block),
);
if (rootDeclarationBlocks.length !== 1) {
  throw new Error(
    `expected exactly 1 generated declaration named "${BUNDLE_ROOT_NAME}" to strip, found ${rootDeclarationBlocks.length}. ` +
      'json-schema-to-typescript output shape may have changed; update the bundle-root stripping logic in generate-protocol.mjs.',
  );
}

const nameReferencePattern = new RegExp(`\\b${rootNamePattern}\\b`);
let strippedCommentCount = 0;
const withoutBundleRootType = blocks
  .filter((block) => !rootDeclarationBlocks.includes(block))
  .map((block) => {
    if (!nameReferencePattern.test(block)) return block;
    // Blocks come from splitting on `\n(?=export )`, which consumes the newline as
    // part of the delimiter; a block whose comment ends right at that boundary (or at
    // end of file) won't have a trailing "\n" of its own, so it's optional here.
    const stripped = block.replace(/\/\*\*[\s\S]*?\*\/\n?/g, (comment) => {
      if (!nameReferencePattern.test(comment)) return comment;
      strippedCommentCount += 1;
      return '';
    });
    if (nameReferencePattern.test(stripped)) {
      throw new Error(
        `a reference to "${BUNDLE_ROOT_NAME}" survived comment stripping outside a doc comment; ` +
          'json-schema-to-typescript output shape may have changed; update generate-protocol.mjs.',
      );
    }
    return stripped;
  })
  .join('\n');

// Every one of the bundle root's direct $defs entries gets its own "referenced by
// <root>" provenance comment from json-schema-to-typescript; assert that held.
const expectedStrippedComments = Object.keys(rootDefs).length;
if (strippedCommentCount !== expectedStrippedComments) {
  throw new Error(
    `expected to strip ${expectedStrippedComments} "${BUNDLE_ROOT_NAME}" provenance comment(s) (one per bundled schema file), ` +
      `stripped ${strippedCommentCount}. json-schema-to-typescript output shape may have changed; update generate-protocol.mjs.`,
  );
}

// Cosmetic: stripping comments/blocks above can leave behind runs of blank lines.
const normalized = withoutBundleRootType.replace(/\n{3,}/g, '\n\n');

await writeFile(outputFile, normalized.trimEnd() + '\n', 'utf8');
console.log(`generated ${outputFile}`);
