import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { attach, check, extensionWorker, launchChromium, openTab, sleep, targets } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9446;
const MOCK_PORT = 7879;
const SITE_PORT = 8765;
const SITE = `http://127.0.0.1:${SITE_PORT}/`;
const results = [];
const children = [];

const startNode = (script, env) => {
  const p = spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' });
  children.push(p);
  return p;
};

startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '1200' });
startNode('./test-site.mjs', { PORT: String(SITE_PORT), PAGES: '10' });
const chromium = await launchChromium({
  profile: `${out}/profile-local`,
  port: CDP_PORT,
  extension: resolve('dist'),
  args: process.env.HEADED ? [] : ['--headless=new'],
});
await sleep(800);

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: 'http://127.0.0.1:${MOCK_PORT}' })`);

  const pageTarget = await openTab(CDP_PORT, SITE);
  const page = await attach(pageTarget);
  await page.send('Page.enable');
  await page.waitFor(`document.readyState === 'complete' && document.images.length > 0 && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const rects = `JSON.stringify([...document.querySelectorAll('img')].map(i => { const r = i.getBoundingClientRect(); return [r.left, r.top + scrollY, r.width, r.height]; }))`;
  const rectsBefore = await page.evaluate(rects);
  await page.screenshot(`${out}/local-1-before.png`);

  const tabId = await sw.evaluate(`chrome.tabs.query({ url: '${SITE}*' }).then(t => t[0].id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`document.getElementById('server').dataset.state === 'ok' && !document.getElementById('site').hidden`);
  await popup.screenshot(`${out}/local-0-popup-before.png`);
  check(results, (await popup.evaluate(`document.getElementById('site-toggle').checked`)) === false, 'site starts disabled');
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled && !document.getElementById('site-mode').hidden`);
  check(results, (await popup.evaluate(`document.getElementById('site-mode').value`)) === 'on-demand', 'a generic site starts in on-demand mode');
  await sleep(1200);
  const idle = await (await fetch(`http://127.0.0.1:${MOCK_PORT}/mock/stats`)).json();
  check(results, idle.posts === 0, 'on-demand mode sends nothing on its own', idle.requests);
  await popup.evaluate(`(() => { const s = document.getElementById('site-mode'); s.value = 'auto'; s.dispatchEvent(new Event('change')); })()`);
  await page.send('Page.bringToFront');
  await sleep(400);
  await page.screenshot(`${out}/local-1b-translating.png`);
  const registered = await sw.evaluate(`chrome.scripting.getRegisteredContentScripts().then(s => s.map(x => x.id + ' ' + x.matches.join(',') + ' persist=' + x.persistAcrossSessions))`);
  check(results, registered.length === 1, 'popup toggle registers a persistent content script', registered);

  const blobs = `[...document.querySelectorAll('img')].map(i => i.src.startsWith('blob:'))`;
  await page.waitFor(`(${blobs})[0] === true`, 15000);
  await sleep(400);
  await page.screenshot(`${out}/local-2-after-first.png`);
  check(results, true, 'current page swapped to translation');

  await page.waitFor(`(${blobs}).slice(0, 4).every(Boolean)`, 20000);
  const readyBeforeScroll = await page.evaluate(`(${blobs}).filter(Boolean).length`);
  check(results, readyBeforeScroll >= 4, 'pages below the viewport translate before the reader scrolls', { readyBeforeScroll, scrollY: await page.evaluate('scrollY') });
  const order = await (await fetch(`http://127.0.0.1:${MOCK_PORT}/mock/stats`)).json();
  const firstPosts = Object.values(order.byContext)[0]?.slice(0, 4);
  check(results, JSON.stringify(firstPosts) === '[1,2,3,4]', 'queue posts pages in reading order (nearest first)', firstPosts);

  await page.evaluate(`document.querySelectorAll('img.page')[3].scrollIntoView()`);
  await sleep(50);
  check(results, await page.evaluate(`document.querySelectorAll('img.page')[3].src.startsWith('blob:')`), 'page 4 is already translated the moment it scrolls into view');
  await page.screenshot(`${out}/local-3-scrolled-p4.png`);

  const rectsAfter = await page.evaluate(`(scrollTo(0, 0), ${rects})`);
  check(results, rectsAfter === rectsBefore, 'no layout shift (every img keeps its box)', rectsAfter === rectsBefore ? undefined : { rectsBefore, rectsAfter });

  await page.key('keyDown', 'Alt', 'AltLeft', 18);
  await sleep(150);
  const peekSrcs = await page.evaluate(`[...document.querySelectorAll('img.page')].slice(0, 4).map(i => i.getAttribute('src'))`);
  check(results, peekSrcs.every((s) => s.includes('/pages/')), 'holding Alt shows the originals', peekSrcs);
  check(results, (await page.evaluate(rects)) === rectsBefore, 'peek causes no reflow');
  await page.screenshot(`${out}/local-4-peek.png`);
  await page.key('keyUp', 'Alt', 'AltLeft', 18);
  await sleep(150);
  check(results, await page.evaluate(`[...document.querySelectorAll('img.page')].slice(0, 4).every(i => i.src.startsWith('blob:'))`), 'releasing Alt restores translations');

  const reset = await page.evaluate(`(async () => { window.resetSrc(0); const sync = document.querySelectorAll('img.page')[0].getAttribute('src'); await new Promise(r => requestAnimationFrame(r)); return { sync, beforePaint: document.querySelectorAll('img.page')[0].src.startsWith('blob:') }; })()`);
  check(results, reset.beforePaint, 'framework src reset is re-applied before the next frame', reset);

  await page.evaluate(`document.getElementById('spa').scrollIntoView()`);
  await page.waitFor(`document.getElementById('spa').src.startsWith('blob:')`, 20000);
  const spaKnown = await page.evaluate(`(async () => { window.spaGo(3); await new Promise(r => requestAnimationFrame(r)); return document.getElementById('spa').src.startsWith('blob:'); })()`);
  check(results, spaKnown, 'SPA in-place src swap to an already-translated page shows it before the next frame');
  await page.evaluate(`window.spaGo(14)`);
  await page.waitFor(`document.getElementById('spa').src.startsWith('blob:')`, 20000);
  check(results, true, 'SPA in-place src swap to a new page gets translated');

  const swNow = (await targets(CDP_PORT)).find((t) => t.type === 'service_worker' && t.url.startsWith('chrome-extension://') && t.url.endsWith('/background.js'));
  await page.evaluate(`window.spaGo(15)`);
  await sleep(300);
  await page.send('ServiceWorker.enable');
  await page.send('ServiceWorker.stopAllWorkers');
  const swGone = !(await targets(CDP_PORT)).some((t) => t.id === swNow?.id);
  await page.waitFor(`document.getElementById('spa').src.startsWith('blob:')`, 25000);
  check(results, true, 'a job in flight when the service worker is stopped still completes', { swTargetGone: swGone });

  await popup.evaluate(`window.__stale = true; location.reload()`);
  await popup.waitFor(`!window.__stale && !document.getElementById('site').hidden && document.getElementById('site-toggle').checked`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`!document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  await page.waitFor(`[...document.querySelectorAll('img')].every(i => !i.src.startsWith('blob:'))`, 5000);
  check(results, (await sw.evaluate(`chrome.scripting.getRegisteredContentScripts().then(s => s.length)`).catch(() => 0)) === 0, 'disabling the site unregisters and restores originals');
} catch (err) {
  check(results, false, 'run completed', err.message);
  console.log('mock stats', JSON.stringify(await (await fetch(`http://127.0.0.1:${MOCK_PORT}/mock/stats`)).json()));
  const p = (await targets(CDP_PORT)).find((t) => t.url.startsWith(SITE));
  if (p) await (await attach(p)).screenshot(`${out}/local-failure.png`);
} finally {
  children.forEach((c) => c.kill());
  process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
