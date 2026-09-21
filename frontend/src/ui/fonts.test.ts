// CR002.10: the Spectrum fonts are self-hosted files served from 'self', so the
// gateway CSP (default-src 'self', no font-src data:) keeps working.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname, '../..');
const css = readFileSync(resolve(root, 'src/style.css'), 'utf8');

test('every @font-face source is a self-hosted woff2 in public/', () => {
  const urls = [...css.matchAll(/@font-face\s*{[^}]*url\('([^']+)'\)/g)].map((m) => m[1]);
  assert.deepEqual(urls.sort(), ['/fonts/nether-earth-8x8.woff2', '/fonts/nether-earth-tall.woff2']);
  for (const u of urls) {
    assert.ok(u.startsWith('/') && !u.startsWith('//'), u);
    const buf = readFileSync(resolve(root, 'public', u.slice(1)));
    assert.equal(buf.subarray(0, 4).toString('latin1'), 'wOF2', u);
  }
  assert.ok(!/url\(\s*['"]?(data:|https?:)/.test(css), 'no inline or remote font/image URLs');
});

test('font provenance and licence are recorded', () => {
  const readme = readFileSync(resolve(root, 'public/assets/README.md'), 'utf8');
  for (const f of ['nether-earth-8x8.woff2', 'nether-earth-tall.woff2']) assert.ok(readme.includes(f), f);
  assert.ok(existsSync(resolve(root, 'scripts/build-fonts.py')));
});
