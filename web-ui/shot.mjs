import { firefox } from './web/node_modules/playwright/index.mjs';

const URL_ = process.env.UPSCALE_UI_URL || 'http://100.122.63.6:8790';
const OUT = process.env.SHOT_DIR || '/tmp/upscale-ui';
const only = process.argv.slice(2);

const STATES = {
  main: async () => {},
  machines: async (p) => p.getByRole('button', { name: /Machines/ }).click(),
  start: async (p) => p.getByRole('button', { name: /Start/ }).first().click(),
  source: async (p) => p.getByTitle('Pick the source directory').click(),
};

const b = await firefox.launch();
const p = await b.newPage({ viewport: { width: 1400, height: 900 } });
const problems = [];
p.on('pageerror', (e) => problems.push(`pageerror: ${e}`));
p.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); });
p.on('requestfailed', (r) => problems.push(`request failed: ${r.url()}`));

const shots = [];
for (const [name, open] of Object.entries(STATES)) {
  if (only.length && !only.includes(name)) continue;
  await p.goto(URL_, { waitUntil: 'networkidle' });
  await p.waitForTimeout(1200);
  await open(p);
  await p.waitForTimeout(700);
  const path = `${OUT}/${name}.png`;
  await p.screenshot({ path });
  shots.push(path);
}
await b.close();

console.log(shots.join('\n'));
if (problems.length) {
  console.error('\n' + problems.join('\n'));
  process.exit(1);
}
