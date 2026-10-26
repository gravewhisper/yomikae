import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { attach, check, extensionWorker, launchChromium, openTab, sleep, targets } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9450;
const MOCK_PORT = 7886;
const SITE_PORT = 8770;
const SITE = `http://127.0.0.1:${SITE_PORT}/`;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const results = [];
const children = new Set();

const startNode = (script, env) => {
  const p = spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' });
  children.add(p);
  p.on('exit', () => children.delete(p));
  return p;
};
const startMock = async (env = {}) => {
  const p = startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '1500', ...env });
  for (let i = 0; !(await fetch(`${MOCK}/mock/stats`).then((r) => r.ok, () => false)); i++) {
    if (i > 50) throw new Error('mock server did not start');
    await sleep(100);
  }
  return p;
};

if (await fetch(`${MOCK}/mock/stats`).then(() => true, () => false)) throw new Error(`port ${MOCK_PORT} is already in use`);
let mock = await startMock();
startNode('./test-site.mjs', { PORT: String(SITE_PORT), PAGES: '8' });
const chromium = await launchChromium({ profile: `${out}/profile-outage`, port: CDP_PORT, extension: resolve('dist'), args: process.env.HEADED ? [] : ['--headless=new'] });
await sleep(800);

async function chipStatuses(page) {
  const { root } = await page.send('DOM.getDocument', { depth: -1, pierce: true });
  const find = (n) => (n.nodeName === 'YOMIKAE-CHIPS' ? n : (n.children ?? []).map(find).find(Boolean));
  const shadowRoot = find(root)?.shadowRoots?.[0];
  if (!shadowRoot) return [];
  const { nodeIds } = await page.send('DOM.querySelectorAll', { nodeId: shadowRoot.nodeId, selector: '.chip' });
  return Promise.all(nodeIds.map(async (nodeId) => (await page.send('DOM.getOuterHTML', { nodeId })).outerHTML));
}

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${MOCK}', siteModes: { '127.0.0.1': 'auto' } })`);
  const page = await attach(await openTab(CDP_PORT, SITE));
  await page.send('Page.enable');
  await page.waitFor(`document.readyState === 'complete' && document.images.length > 0 && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const tabId = await sw.evaluate(`chrome.tabs.query({ url: '${SITE}*' }).then(t => t[0].id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`document.getElementById('server').dataset.state === 'ok' && !document.getElementById('site').hidden`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  await page.send('Page.bringToFront');

  const blobs = `[...document.querySelectorAll('img.page')].map(i => i.src.startsWith('blob:'))`;
  await page.waitFor(`(${blobs})[0] === true`, 15000);
  const readyBefore = await page.evaluate(`(${blobs}).filter(Boolean).length`);
  mock.kill('SIGKILL');
  await new Promise((r) => mock.once('exit', r));
  check(results, readyBefore < 3, 'the server dies mid-read with pages still pending', { readyBefore });
  await page.evaluate(`document.querySelectorAll('img.page')[2].scrollIntoView()`);

  const sawFailed = [];
  let waitingChips = [];
  for (const end = Date.now() + 15000; Date.now() < end; await sleep(200)) {
    const chips = await chipStatuses(page);
    sawFailed.push(...chips.filter((c) => c.includes('failed') || c.includes('Can’t translate')));
    waitingChips = chips.filter((c) => c.includes('Waiting for server…'));
    if (waitingChips.length) break;
  }
  check(results, waitingChips.length > 0, 'pending pages show the waiting chip', { waiting: waitingChips.length });
  await page.screenshot(`${out}/outage-1-waiting.png`);
  for (const end = Date.now() + 5000; Date.now() < end; await sleep(250)) {
    sawFailed.push(...(await chipStatuses(page)).filter((c) => c.includes('failed') || c.includes('Can’t translate')));
  }
  check(results, sawFailed.length === 0, 'no page shows a failed chip during the outage', sawFailed.slice(0, 2));
  check(results, (await page.evaluate(`(${blobs}).filter(Boolean).length`)) >= readyBefore, 'translated pages stay translated during the outage');

  mock = await startMock({ MOCK_INTERNAL_PAGE: '5' });
  await page.waitFor(`(${blobs}).slice(0, 4).every(Boolean)`, 60000);
  check(results, true, 'every pending page recovers after the server restarts, without a click');
  const stats = await (await fetch(`${MOCK}/mock/stats`)).json();
  check(results, stats.requests[0] === 'health', 'the worker polls health before resending pages', stats.requests.slice(0, 4));
  const resent = stats.requests.filter((r) => r.startsWith('page ')).map((r) => Number(r.slice(5)));
  const distance = resent.filter(Number.isFinite).map((i) => Math.abs(i - 3));
  check(results, distance.every((d, n) => n === 0 || d >= distance[n - 1]), 'resumed pages post nearest the current page first', resent);
  check(results, (await chipStatuses(page)).every((c) => !c.includes('Waiting for server')), 'waiting chips clear after recovery');
  await page.screenshot(`${out}/outage-2-recovered.png`);
  await page.evaluate(`document.querySelectorAll('img.page')[4].scrollIntoView()`);
  let internal = [];
  for (const end = Date.now() + 10000; Date.now() < end && !internal.length; await sleep(200)) {
    internal = (await chipStatuses(page)).filter((c) => c.includes('Translation failed · Retry'));
  }
  check(results, internal.length === 1, 'a page-specific internal error still shows the failed chip with Retry');
  await page.screenshot(`${out}/outage-3-internal-error.png`);
} catch (err) {
  check(results, false, 'run completed', err.message);
  console.log('mock stats', await fetch(`${MOCK}/mock/stats`).then((r) => r.text(), () => 'down'));
  const p = (await targets(CDP_PORT)).find((t) => t.url.startsWith(SITE));
  if (p) await (await attach(p)).screenshot(`${out}/outage-failure.png`);
} finally {
  children.forEach((c) => c.kill());
  process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
