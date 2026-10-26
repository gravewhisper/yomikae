import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { attach, check, extensionWorker, launchChromium, openTab, sleep, targets } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9452;
const MOCK_PORT = 7887;
const SITE_PORT = 8771;
const MIRROR_PORT = 8772;
const SITE = `http://127.0.0.1:${SITE_PORT}/`;
const MIRROR = `http://127.0.0.1:${MIRROR_PORT}/`;
const results = [];
const children = [];

const startNode = (script, env) => {
  const p = spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' });
  children.push(p);
  return p;
};
const served = async (base) => (await fetch(`${base}served`)).json();

for (const port of [MOCK_PORT, SITE_PORT, MIRROR_PORT]) {
  if (await fetch(`http://127.0.0.1:${port}/`).then(() => true, () => false)) throw new Error(`port ${port} is already in use`);
}
startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '300' });
startNode('./test-site.mjs', { PORT: String(SITE_PORT), PAGES: '3', SLOW: '1=trickle,2=stall' });
startNode('./test-site.mjs', { PORT: String(MIRROR_PORT), PAGES: '1' });
const chromium = await launchChromium({ profile: `${out}/profile-slow`, port: CDP_PORT, extension: resolve(process.env.EXT ?? 'dist'), args: process.env.HEADED ? [] : ['--headless=new'] });
await sleep(800);

async function chipTitles(page) {
  const { root } = await page.send('DOM.getDocument', { depth: -1, pierce: true });
  const find = (n) => (n.nodeName === 'YOMIKAE-CHIPS' ? n : (n.children ?? []).map(find).find(Boolean));
  const shadowRoot = find(root)?.shadowRoots?.[0];
  if (!shadowRoot) return [];
  const { nodeIds } = await page.send('DOM.querySelectorAll', { nodeId: shadowRoot.nodeId, selector: '.chip' });
  return Promise.all(nodeIds.map(async (nodeId) => (await page.send('DOM.getOuterHTML', { nodeId })).outerHTML));
}

const wantVia = (popup, key, candidates) =>
  popup.evaluate(`new Promise((resolve) => {
    const port = chrome.runtime.connect({ name: 'yomikae' });
    const t0 = Date.now();
    port.onMessage.addListener((m) => {
      if (m.type !== 'ready' && m.type !== 'failed') return;
      port.disconnect();
      resolve({ type: m.type, error: m.error, retryable: m.retryable, seconds: (Date.now() - t0) / 1000 });
    });
    port.postMessage({ type: 'want', jobs: [{ key: ${JSON.stringify(key)}, url: ${JSON.stringify(candidates[0])}, candidates: ${JSON.stringify(candidates)}, contextKey: 'slow-test', pageIndex: 1, priority: 0 }] });
  })`);

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: 'http://127.0.0.1:${MOCK_PORT}', siteModes: { '127.0.0.1': 'auto' } })`);
  const page = await attach(await openTab(CDP_PORT, SITE));
  await page.send('Page.enable');
  await page.waitFor(`document.readyState === 'complete' && document.images.length > 0 && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const tabId = await sw.evaluate(`chrome.tabs.query({ url: '${SITE}*' }).then(t => t[0].id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`document.getElementById('server').dataset.state === 'ok' && !document.getElementById('site').hidden`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  await page.send('Page.bringToFront');

  const t0 = Date.now();
  const src = (i) => `document.querySelectorAll('img.page')[${i}].src.startsWith('blob:')`;
  const trickled = await page.waitFor(src(0), 60000).then(() => true, () => false);
  const trickleSeconds = (Date.now() - t0) / 1000;
  await page.evaluate(`document.querySelectorAll('img.page')[1].scrollIntoView()`);
  let failedChips = [];
  for (const end = Date.now() + 20000; Date.now() < end && !failedChips.length; await sleep(250)) {
    failedChips = (await chipTitles(page)).filter((c) => c.includes('Translation failed'));
  }
  const log = await served(SITE);
  const trickle = log.find((r) => r.mode === 'trickle');
  const stall = log.find((r) => r.mode === 'stall');
  check(results, trickle?.dest === 'empty' && trickle.done, 'the worker fetch of page 1 was trickled to the end at 20 KB/s', trickle);
  check(results, trickled, 'a page that trickles for about 20 s still gets translated', { seconds: trickleSeconds });
  check(results, stall?.dest === 'empty' && !stall.done, 'the worker fetch of page 2 stalled mid-body', stall);
  check(results, failedChips.length === 1 && failedChips[0].includes('Retry'), 'a page that stalls mid-body fails as retryable', failedChips.map((c) => c.match(/title="([^"]*)"/)?.[1]));
  await page.waitFor(src(2), 30000).then(() => check(results, true, 'the page queued behind the stall still translates'), (e) => check(results, false, 'the page queued behind the stall still translates', e.message));
  await page.screenshot(`${out}/slow-1-page.png`);

  const stallFirst = await wantVia(popup, 'stall-then-mirror', [`${SITE}pages/22.svg?mode=stall`, `${MIRROR}pages/22.svg`]);
  check(results, stallFirst.type === 'ready', 'a mirror that stalls mid-body falls back to the next mirror', stallFirst);
  const silentFirst = await wantVia(popup, 'silent-then-trickle', [`${SITE}pages/21.svg?mode=silent`, `${MIRROR}pages/21.svg?mode=trickle`]);
  const mirrorLog = await served(MIRROR);
  check(results, silentFirst.type === 'ready', 'a silent mirror is hedged and the slow mirror that answers is kept to the end', { ...silentFirst, mirror: mirrorLog.find((r) => r.page === 21) });
} catch (err) {
  check(results, false, 'run completed', err.message);
  const p = (await targets(CDP_PORT)).find((t) => t.url.startsWith(SITE));
  if (p) await (await attach(p)).screenshot(`${out}/slow-failure.png`);
} finally {
  children.forEach((c) => c.kill());
  process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
