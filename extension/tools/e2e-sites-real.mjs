import { execFileSync, spawn } from 'node:child_process';
import { rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { attach, center, check, extensionWorker, launchChromium, mouse, openTab, sleep, waitChip } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9456;
const MOCK_PORT = 7892;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const ARTWORK = process.env.ARTWORK ?? '151225632';
const TWEET = process.env.TWEET ?? 'https://x.com/NASA/status/2113655288687431819';
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';
const results = [];

if (await fetch(`${MOCK}/`).then(() => true, () => false)) throw new Error(`port ${MOCK_PORT} is already in use`);
await rm(`${out}/profile-sites-real`, { recursive: true, force: true });
execFileSync(process.execPath, ['build.mjs'], {
  env: { ...process.env, ML_OUTDIR: 'dist-sites', ML_EXTRA_HOSTS: '*://*.x.com/*,*://pbs.twimg.com/*,*://*.pixiv.net/*,*://i.pximg.net/*' },
  stdio: 'inherit',
});
const mock = spawn(process.execPath, [new URL('./mock-server.mjs', import.meta.url).pathname], { env: { ...process.env, PORT: String(MOCK_PORT), MOCK_DELAY_MS: '500' }, stdio: 'inherit' });
const chromium = await launchChromium({
  profile: `${out}/profile-sites-real`,
  port: CDP_PORT,
  extension: resolve('dist-sites'),
  args: [...(process.env.HEADED ? [] : ['--headless=new']), `--user-agent=${UA}`],
});
await sleep(800);
const posted = async (ctx) => (await (await fetch(`${MOCK}/mock/stats`)).json()).byContext[ctx] ?? [];

const target = `document.querySelector('[data-e2e-target]')`;
const shownAt = (expr) => `(() => {
  const r = (${expr}).getBoundingClientRect();
  for (const el of document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2)) {
    const cs = getComputedStyle(el);
    if (el instanceof HTMLImageElement && Number(cs.opacity) > 0) return el.currentSrc || el.src;
    const bg = cs.backgroundImage.match(/url\\("?(.*?)"?\\)/);
    if (bg) return bg[1];
  }
  return null;
})()`;

async function translateFirst(page, selector) {
  await page.evaluate(`(() => { const img = document.querySelector(${JSON.stringify(selector)}); img.dataset.e2eTarget = '1'; img.scrollIntoView({ block: 'center' }); })()`);
  await sleep(500);
  const at = await page.evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await mouse(page, 'mouseMoved', at);
  const button = await waitChip(page, '.translate', (n) => n.visible, 8000);
  await mouse(page, 'mouseMoved', center(button.box));
  await mouse(page, 'mousePressed', center(button.box));
  await mouse(page, 'mouseReleased', center(button.box));
}

async function enable(sw, extId, prefix) {
  const tabId = await sw.evaluate(`chrome.tabs.query({}).then(ts => ts.find(t => t.url?.startsWith(${JSON.stringify(prefix)})).id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`!document.getElementById('site').hidden`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-mode').hidden`);
  const mode = await popup.evaluate(`document.getElementById('site-mode').value`);
  popup.close();
  return mode;
}

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${MOCK}' })`);
  const fetches = [];
  sw.on((m) => {
    if (m.method === 'Network.responseReceived' && /pximg|twimg/.test(m.params.response.url)) fetches.push(`${m.params.response.status} ${m.params.response.url}`);
  });
  await sw.send('Network.enable');

  const page0 = `img[src*="/img-master/"][src*="${ARTWORK}_p0_"]`;
  const art = await attach(await openTab(CDP_PORT, `https://www.pixiv.net/en/artworks/${ARTWORK}`));
  await art.send('Page.enable');
  await art.send('DOM.enable');
  await art.waitFor(`!!document.querySelector(${JSON.stringify(page0)})?.complete`, 45000);
  check(results, (await enable(sw, extId, 'https://www.pixiv.net/')) === 'on-demand', 'pixiv.net is enabled in on-demand mode');
  await art.send('Page.bringToFront');
  await sleep(1500);
  check(results, (await posted(`pixiv.net/artworks/${ARTWORK}`)).length === 0 && fetches.length === 0, 'nothing is fetched or sent before the click', fetches);
  await translateFirst(art, page0);
  await art.waitFor(`${target}.src.startsWith('blob:')`, 60000);
  const original = fetches.find((f) => f.includes('/img-original/'));
  check(results, original?.startsWith('200 '), 'the worker fetched the original from the real i.pximg.net', fetches);
  check(results, (await posted(`pixiv.net/artworks/${ARTWORK}`))[0] === 0, 'page 0 was sent with the artwork context', await posted(`pixiv.net/artworks/${ARTWORK}`));
  await art.screenshot(`${out}/real-pixiv.png`);
  art.close();

  const tweet = await attach(await openTab(CDP_PORT, TWEET));
  await tweet.send('Page.enable');
  await tweet.send('DOM.enable');
  const media = `article img[src*="pbs.twimg.com/media/"]`;
  await tweet.waitFor(`!!document.querySelector(${JSON.stringify(media)})?.complete`, 45000).catch(() => undefined);
  const rendered = await tweet.evaluate(`!!document.querySelector(${JSON.stringify(media)})`);
  check(results, rendered, 'a logged-out tweet page renders its images', await tweet.evaluate('location.href'));
  if (rendered) {
    check(results, (await enable(sw, extId, 'https://x.com/')) === 'on-demand', 'x.com is enabled in on-demand mode');
    await tweet.send('Page.bringToFront');
    await translateFirst(tweet, media);
    await tweet.waitFor(`${target}.src.startsWith('blob:')`, 60000);
    const status = TWEET.match(/status\/(\d+)/)[1];
    check(results, (await posted(`x.com/i/status/${status}`)).length === 1, 'the tweet image was sent once with the thread context', await posted(`x.com/i/status/${status}`));
    check(results, fetches.some((f) => f.startsWith('200 ') && f.includes('name=orig')), 'the worker fetched the original size from pbs.twimg.com', fetches.filter((f) => f.includes('twimg')));
    await sleep(600);
    const seen = await tweet.evaluate(shownAt(target));
    check(results, seen?.startsWith('blob:'), 'the translation is what the reader sees on the tweet page', seen);
    await tweet.key('keyDown', 'Alt', 'AltLeft', 18);
    await sleep(200);
    const peek = await tweet.evaluate(shownAt(target));
    check(results, peek?.includes('pbs.twimg.com/media/'), 'Alt shows the original on the tweet page', peek);
    await tweet.key('keyUp', 'Alt', 'AltLeft', 18);
    await tweet.screenshot(`${out}/real-x.png`);
    const photo = `document.querySelector('[aria-modal="true"] img[src*="pbs.twimg.com/media/"], [aria-modal="true"] img[src^="blob:"]')`;
    await tweet.send('Page.navigate', { url: `${TWEET}/photo/1` });
    const inPhoto = await tweet.waitFor(`(() => { const u = !!${photo} && ${shownAt(photo)}; return u?.startsWith?.('blob:') ? { ok: true, u } : u; })()`, 30000).catch((e) => e.message.split('\n').pop());
    check(results, inPhoto?.ok, 'a photo view opened by URL shows the earlier translation', inPhoto);
    await tweet.screenshot(`${out}/real-x-photo.png`);
  }
} catch (err) {
  check(results, false, 'run completed', err.message);
} finally {
  mock.kill();
  if (!process.env.KEEP) process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
