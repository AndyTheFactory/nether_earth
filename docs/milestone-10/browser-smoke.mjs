// Two real headless Chromium browsers play through the Nginx gateway (M10.9 evidence).
// usage (outside the repo, playwright is not a project dependency):
//   mkdir /tmp/pw && cd /tmp/pw && npm i playwright@1.62.1 && npx playwright install chromium
//   cp <repo>/docs/milestone-10/browser-smoke.mjs . && BASE=http://localhost OUT=. node browser-smoke.mjs
import { chromium } from 'playwright';

const BASE = process.env.BASE ?? 'http://localhost:18080';
const OUT = process.env.OUT ?? '.';
const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
};

const browser = await chromium.launch();
const problems = [];
async function open(label) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') problems.push(`${label} console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => problems.push(`${label} pageerror: ${e.message}`));
  return page;
}
const tickOf = async (page) => {
  const text = await page.locator('.clock').first().textContent().catch(() => null);
  const m = text && text.match(/tick (\d+)/);
  return m ? Number(m[1]) : null;
};

try {
  const a = await open('A');
  const b = await open('B');
  await a.goto(BASE);
  await a.fill('#nick', 'Alpha');
  await a.click('#create');
  const code = (await a.locator('#lobby-status .code').first().textContent({ timeout: 10000 })).trim();
  check('player A created a match', /^[A-Z0-9]{6}$/.test(code), `join code ${code}`);

  await b.goto(`${BASE}/?code=${code}`);
  await b.fill('#nick', 'Bravo');
  await b.click('#join');
  await b.locator('#lobby-status li').nth(1).waitFor({ timeout: 10000 });
  check('player B joined via code link', true);

  for (const p of [a, b]) await p.click('#lobby-status [data-action="ready"]');
  await a.locator('#lobby').waitFor({ state: 'hidden', timeout: 15000 });
  await b.locator('#lobby').waitFor({ state: 'hidden', timeout: 15000 });
  check('both clients left the lobby into the match', true);
  check('game canvas rendered', (await a.locator('canvas').count()) > 0 && (await b.locator('canvas').count()) > 0);

  const t0 = await tickOf(a);
  await a.keyboard.down('ArrowRight');
  await a.waitForTimeout(1500);
  await a.keyboard.up('ArrowRight');
  const t1 = await tickOf(a);
  const tb = await tickOf(b);
  check('authoritative ticks advance over WebSocket', t0 !== null && t1 > t0, `A tick ${t0} -> ${t1}, B tick ${tb}`);
  await a.screenshot({ path: `${OUT}/browser-a.png` });
  await b.screenshot({ path: `${OUT}/browser-b.png` });

  // Reload B with ?resume: the tab reconnects with its stored session and the match resumes.
  await b.goto(`${BASE}/?resume`);
  await b.waitForTimeout(4000);
  const tb2 = await tickOf(b);
  const ta2 = await tickOf(a);
  check('reloaded client is back in the running match', tb2 !== null && tb2 > (tb ?? 0), `B tick ${tb2}, A tick ${ta2}`);
  // Complete the match: B closes its browser; after the 60 s grace A wins by forfeit.
  await b.context().close();
  await a.getByText('VICTORY BY FORFEIT').waitFor({ timeout: 90000 });
  check('match completed: A wins by forfeit after B leaves', true);
  await a.screenshot({ path: `${OUT}/browser-a-result.png` });
} catch (err) {
  check('browser flow', false, String(err));
}
const csp = problems.filter((p) => /Content Security Policy|CSP|unsafe-eval/i.test(p));
check('no Content-Security-Policy violations', csp.length === 0, csp.join(' | '));
check('no page errors', !problems.some((p) => p.includes('pageerror')), problems.filter((p) => p.includes('pageerror')).join(' | '));
if (problems.length) console.log('console:', problems.slice(0, 10));
await browser.close();
process.exit(results.every(Boolean) ? 0 : 1);
