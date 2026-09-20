import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';

const schemaDir = resolve(process.cwd(), '../protocol/schemas');
const fixtureDir = resolve(process.cwd(), '../protocol/fixtures');

const schemaFiles = (await readdir(schemaDir)).filter((name) => name.endsWith('.schema.json')).sort();
const ajv = new Ajv2020({ strict: true });

// Register every schema file before compiling any of them, since schemas
// cross-reference each other via relative $ref (e.g. client_messages.schema.json
// refs common.schema.json). Ajv resolves $ref against already-registered $ids.
const schemas = [];
for (const file of schemaFiles) {
  const schema = JSON.parse(await readFile(resolve(schemaDir, file), 'utf8'));
  ajv.addSchema(schema, file);
  schemas.push({ file, schema });
}

const validators = new Map();
for (const { file, schema } of schemas) {
  const validate = ajv.getSchema(schema.$id) ?? ajv.getSchema(file);
  if (!validate) {
    throw new Error(`failed to resolve compiled schema for ${file}`);
  }
  validators.set(schemaBaseName(file), validate);
  console.log(`validated ${file}`);
}

function schemaBaseName(file) {
  return file.replace(/\.schema\.json$/, '');
}

// Fixture-based contract tests: protocol/fixtures/<schema-base-name>/{valid,invalid}/*.json
let fixtureDirs = [];
try {
  fixtureDirs = await readdir(fixtureDir, { withFileTypes: true });
} catch (err) {
  if (err.code !== 'ENOENT') throw err;
}

let passed = 0;
let failed = 0;

for (const entry of fixtureDirs) {
  if (!entry.isDirectory()) continue;
  const baseName = entry.name;
  const validate = validators.get(baseName);
  if (!validate) {
    throw new Error(`fixture directory "${baseName}" does not match any schema file (expected one of: ${[...validators.keys()].join(', ')})`);
  }

  for (const [expectation, expectedValid] of [['valid', true], ['invalid', false]]) {
    const dir = resolve(fixtureDir, baseName, expectation);
    let files;
    try {
      files = (await readdir(dir)).filter((name) => name.endsWith('.json')).sort();
    } catch (err) {
      if (err.code === 'ENOENT') continue;
      throw err;
    }

    for (const file of files) {
      const data = JSON.parse(await readFile(resolve(dir, file), 'utf8'));
      const isValid = validate(data);
      const label = `${baseName}/${expectation}/${file}`;
      if (isValid === expectedValid) {
        console.log(`  ok   ${label}`);
        passed += 1;
      } else {
        console.error(`  FAIL ${label}: expected ${expectation}, got ${isValid ? 'valid' : 'invalid'}`);
        if (isValid !== true) {
          console.error(`       errors: ${ajv.errorsText(validate.errors)}`);
        }
        failed += 1;
      }
    }
  }
}

console.log(`fixtures: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
