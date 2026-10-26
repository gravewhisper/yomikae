import { execFileSync, spawn } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import { request } from 'node:https';
import { resolve } from 'node:path';
import { attach, center, check, chipNodes, extensionWorker, launchChromium, mouse, openTab, sleep, targets, waitChip } from './cdp.mjs';
import { loadMocks } from './local-adapters.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9455;
const MOCK_PORT = 7891;
const SITES_PORT = 8790;
const LOCAL_PORT = 8791;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const LOCAL = `http://127.0.0.1:${LOCAL_PORT}/`;
const localMocks = await loadMocks();
const MAPPED = ['x.com', 'pbs.twimg.com', 'www.pixiv.net', 'i.pximg.net', ...localMocks.flatMap((m) => m.hosts)];
const EXTRA_HOSTS = ['*://*.x.com/*', '*://pbs.twimg.com/*', '*://*.pixiv.net/*', '*://i.pximg.net/*', ...localMocks.flatMap((m) => m.permissions)];
const results = [];
const children = [];

for (const port of [MOCK_PORT, SITES_PORT, LOCAL_PORT]) {
  if (await fetch(`http://127.0.0.1:${port}/`).then(() => true, (e) => e.cause?.code !== 'ECONNREFUSED')) throw new Error(`port ${port} is already in use`);
}
await rm(`${out}/profile-sites`, { recursive: true, force: true });
await mkdir(`${out}/certs`, { recursive: true });
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=yomikae-mock', '-keyout', `${out}/certs/key.pem`, '-out', `${out}/certs/cert.pem`], { stdio: 'ignore' });
execFileSync(process.execPath, ['build.mjs'], { env: { ...process.env, ML_OUTDIR: 'dist-sites', ML_EXTRA_HOSTS: EXTRA_HOSTS.join(',') }, stdio: 'inherit' });

const startNode = (script, env) => {
  const p = spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' });
  children.push(p);
  return p;
};
startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '2500' });
startNode('./sites-mock.mjs', { PORT: String(SITES_PORT), CERT_DIR: `${out}/certs` });
startNode('./test-site.mjs', { PORT: String(LOCAL_PORT), PAGES: '3' });
const chromium = await launchChromium({
  profile: `${out}/profile-sites`,
  port: CDP_PORT,
  extension: resolve('dist-sites'),
  args: [
    ...(process.env.HEADED ? [] : ['--headless=new']),
    '--ignore-certificate-errors',
    `--host-resolver-rules=${MAPPED.map((h) => `MAP ${h} 127.0.0.1:${SITES_PORT}`).join(', ')}`,
  ],
});
await sleep(800);

const stats = async () => (await fetch(`${MOCK}/mock/stats`)).json();
const posted = async (ctx) => (await stats()).byContext[ctx] ?? [];
const siteRequests = () =>
  new Promise((ok, fail) => {
    request({ host: '127.0.0.1', port: SITES_PORT, path: '/__requests', headers: { host: 'x.com' }, rejectUnauthorized: false }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => ok(JSON.parse(body)));
    })
      .on('error', fail)
      .end();
  });
const rawStatus = (host, path, headers = {}) =>
  new Promise((ok, fail) => {
    request({ host: '127.0.0.1', port: SITES_PORT, path, headers: { host, ...headers }, rejectUnauthorized: false }, (res) => {
      res.resume();
      ok(res.statusCode);
    })
      .on('error', fail)
      .end();
  });
const extensionFetches = async (host) => (await siteRequests()).filter((r) => r.host === host && r.dest === 'empty');
const isBlob = (sel) => `(document.querySelector(${JSON.stringify(sel)})?.src ?? '').startsWith('blob:')`;
const centerOf = (page, expr) =>
  page.evaluate(`(() => { const e = ${expr}; const r = (e.closest('.ph') ?? e).getBoundingClientRect(); return { x: r.left + r.width / 2, y: Math.max(r.top, 0) + (Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) / 2 }; })()`);
const visible = async (page, selector) => (await chipNodes(page, selector)).some((n) => n.visible);
const SHOWN_AT = `(x, y) => {
  for (const el of document.elementsFromPoint(x, y)) {
    const cs = getComputedStyle(el);
    if (el instanceof HTMLImageElement && Number(cs.opacity) > 0) return el.currentSrc || el.src;
    const bg = cs.backgroundImage.match(/url\\("?(.*?)"?\\)/);
    if (bg) return bg[1];
  }
  return null;
}`;
const shownExpr = (expr) => `(() => { const r = (${expr}).getBoundingClientRect(); return (${SHOWN_AT})(r.left + r.width / 2, r.top + r.height / 2); })()`;
const shown = (page, expr) => page.evaluate(shownExpr(expr));
const rectOf = (page, expr) => page.evaluate(`(({ left, top, right, bottom }) => ({ left, top, right, bottom }))((${expr}).getBoundingClientRect())`);
const clip = async (page, expr) => {
  const r = await rectOf(page, expr);
  const { data } = await page.send('Page.captureScreenshot', { format: 'png', clip: { x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, scale: 1 } });
  return data;
};
const PHOTO = `document.querySelector('#modal img')`;
const photoReady = `!!document.querySelector('#modal img')?.complete && [...document.images].every(i => i.complete)`;

async function hoverNoOffer(page, expr) {
  await mouse(page, 'mouseMoved', await centerOf(page, expr));
  await sleep(500);
  return !(await visible(page, '.translate'));
}

async function clickTranslate(page, expr, shot) {
  await mouse(page, 'mouseMoved', await centerOf(page, expr));
  const button = await waitChip(page, '.translate', (n) => n.visible, 5000);
  if (shot) await page.screenshot(shot);
  const at = center(button.box);
  await mouse(page, 'mouseMoved', at);
  await mouse(page, 'mousePressed', at);
  await mouse(page, 'mouseReleased', at);
}

async function walkToTranslate(page, expr, click = true) {
  const start = await centerOf(page, expr);
  await mouse(page, 'mouseMoved', start);
  const first = await waitChip(page, '.translate', (n) => n.visible, 5000);
  const end = center(first.box);
  const steps = Math.ceil(Math.hypot(end.x - start.x, end.y - start.y) / 4);
  const lost = [];
  for (let i = 1; i <= steps; i++) {
    const at = { x: start.x + ((end.x - start.x) * i) / steps, y: start.y + ((end.y - start.y) * i) / steps };
    await mouse(page, 'mouseMoved', at);
    await sleep(35);
    const now = (await chipNodes(page, '.translate')).find((n) => n.visible);
    if (!now || Math.abs(center(now.box).x - end.x) > 1 || Math.abs(center(now.box).y - end.y) > 1) lost.push({ x: Math.round(at.x), y: Math.round(at.y), chip: now && center(now.box) });
  }
  if (click) {
    await mouse(page, 'mousePressed', end);
    await mouse(page, 'mouseReleased', end);
  }
  return lost;
}

async function enable(sw, extId, url) {
  const tabId = await sw.evaluate(`chrome.tabs.query({}).then(ts => ts.find(t => t.url?.startsWith(${JSON.stringify(url)})).id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`!document.getElementById('site').hidden`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled && !document.getElementById('site-mode').hidden`);
  return popup;
}

async function popupFor(sw, extId, url) {
  const tabId = await sw.evaluate(`chrome.tabs.query({}).then(ts => ts.find(t => t.url?.startsWith(${JSON.stringify(url)})).id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`!document.getElementById('site').hidden`);
  return popup;
}

async function setMode(popup, mode) {
  await popup.evaluate(`(() => { const s = document.getElementById('site-mode'); s.value = '${mode}'; s.dispatchEvent(new Event('change')); })()`);
}

async function open(url, ready) {
  const page = await attach(await openTab(CDP_PORT, url));
  await page.send('Page.enable');
  await page.send('DOM.enable');
  await page.waitFor(ready, 20000);
  return page;
}

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${MOCK}' })`);
  check(results, (await rawStatus('i.pximg.net', '/img-original/img/2026/10/24/20/00/54/5001_p0.png')) === 403, 'mock image host refuses a request without the pixiv Referer');
  check(results, (await rawStatus('i.pximg.net', '/img-original/img/2026/10/24/20/00/54/5001_p0.png', { referer: 'https://www.pixiv.net/' })) === 200, 'mock image host answers with the pixiv Referer');

  const local = await open(LOCAL, `document.readyState === 'complete' && document.images.length > 0 && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const localImg = (i) => `document.querySelectorAll('img.page')[${i}]`;
  await local.evaluate(`document.querySelector('main').insertAdjacentHTML('afterbegin', '<img id="icon" src="/pages/9.svg" style="width:150px;height:150px">')`);
  await local.waitFor(`document.getElementById('icon').complete`);
  const localPopup = await enable(sw, extId, LOCAL);
  check(results, (await localPopup.evaluate(`document.getElementById('site-mode').value`)) === 'on-demand', 'a site with no adapter defaults to on-demand');
  await local.send('Page.bringToFront');
  await sleep(1500);
  check(results, (await stats()).posts === 0, 'on-demand sends nothing before a click', (await stats()).requests);
  check(results, await hoverNoOffer(local, `document.getElementById('icon')`), 'an image under 200 px never offers Translate');
  await clickTranslate(local, localImg(0));
  await local.waitFor(`${localImg(0)}.src.startsWith('blob:')`, 20000);
  await sleep(1000);
  const localPosts = (await stats()).posts;
  check(results, localPosts === 1, 'one click sends exactly one page', (await stats()).requests);
  check(results, !(await local.evaluate(`${localImg(1)}.src.startsWith('blob:')`)), 'the image below stays untouched');
  await local.screenshot(`${out}/sites-local-translated.png`);
  await mouse(local, 'mouseMoved', await centerOf(local, localImg(0)));
  await waitChip(local, '.report', (n) => n.visible, 5000);
  check(results, !(await visible(local, '.translate')), 'a translated image offers Report instead of Translate');
  await local.key('keyDown', 'Alt', 'AltLeft', 18);
  await sleep(200);
  check(results, (await local.evaluate(`${localImg(0)}.getAttribute('src')`)).includes('/pages/1.svg'), 'Alt shows the original of an on-demand translation');
  await local.key('keyUp', 'Alt', 'AltLeft', 18);
  await sleep(200);
  check(results, await local.evaluate(`${localImg(0)}.src.startsWith('blob:')`), 'releasing Alt restores it');
  await setMode(localPopup, 'auto');
  await local.waitFor(`[...document.querySelectorAll('img.page, #spa')].every(i => i.src.startsWith('blob:'))`, 30000);
  check(results, !(await local.evaluate(`document.getElementById('icon').src.startsWith('blob:')`)), 'auto mode still skips the small image');
  check(results, true, 'switching the site to auto in the popup translates the rest without a reload', (await stats()).requests);
  await localPopup.evaluate(`window.__stale = true; location.reload()`);
  await localPopup.waitFor(`!window.__stale && !document.getElementById('site').hidden && document.getElementById('site-mode').value === 'auto'`);
  check(results, true, 'the popup remembers the chosen mode');
  localPopup.close();

  const postsBeforeX = (await stats()).posts;
  const feed = await open('https://x.com/home', `[...document.querySelectorAll('article img[alt=Image]')].length > 0 && [...document.images].every(i => i.complete)`);
  const slot = (i) => `window.slotFor(${i}).querySelector('.media img')`;
  const feedPopup = await enable(sw, extId, 'https://x.com/');
  check(results, (await feedPopup.evaluate(`document.getElementById('site-mode').value`)) === 'on-demand', 'x.com defaults to on-demand');
  feedPopup.close();
  await feed.send('Page.bringToFront');
  await sleep(1500);
  check(results, (await extensionFetches('pbs.twimg.com')).length === 0 && (await posted('x.com/i/status/9000')).length === 0, 'x.com: no image fetch and no translation before a click');
  check(results, await hoverNoOffer(feed, `window.slotFor(0).querySelector('.avatar')`), 'an avatar never offers Translate');
  await feed.screenshot(`${out}/sites-x-offer.png`);
  const feedLost = await walkToTranslate(feed, slot(0));
  check(results, feedLost.length === 0, 'on the timeline the Translate chip stays put while the pointer walks from the photo onto it', feedLost.slice(0, 5));
  await feed.waitFor(`${slot(0)}.src.startsWith('blob:')`, 20000);
  check(results, JSON.stringify(await posted('x.com/i/status/9000')) === '[1]', 'the clicked tweet image is sent once, keyed by its tweet', await posted('x.com/i/status/9000'));
  const firstFetch = (await extensionFetches('pbs.twimg.com')).find((r) => r.path.includes('Gfeed00'));
  check(results, firstFetch?.path === '/media/Gfeed00AAx_q?format=jpg&name=orig' && firstFetch.status === 200, 'the original resolution is fetched', firstFetch);
  await feed.screenshot(`${out}/sites-x-feed-translated.png`);

  const nextLost = await walkToTranslate(feed, slot(1));
  check(results, nextLost.length === 0, 'on the timeline the chip of the next tweet stays put while the pointer walks onto it', nextLost.slice(0, 5));
  await sleep(150);
  await feed.evaluate(`scrollTo(0, 520 * 12)`);
  await sleep(300);
  check(results, (await feed.evaluate(`window.recycles`)) > 0 && !(await feed.evaluate(`!!window.slotFor(1)`)), 'scrolling recycles the element of the in-flight tweet');
  const end = Date.now() + 20000;
  while (Date.now() < end && !(await posted('x.com/i/status/9001')).length) await sleep(200);
  await sleep(1500);
  const wrong = await feed.evaluate(`[...document.querySelectorAll('article')].filter(a => a.querySelector('.media img').src.startsWith('blob:')).map(a => a.dataset.tweet)`);
  check(results, wrong.length === 0, 'a result that lands after its element was recycled is not shown on the new tweet', wrong);
  const pngFetches = (await extensionFetches('pbs.twimg.com')).filter((r) => r.path.includes('Gfeed01')).map((r) => `${r.path} ${r.status}`);
  check(results, pngFetches[0]?.endsWith('format=jpg&name=orig 404') && pngFetches[1]?.endsWith('format=png&name=orig 200'), 'a PNG original is found after the JPEG original 404s', pngFetches);
  await feed.evaluate(`scrollTo(0, 0)`);
  await feed.waitFor(`${slot(0)}.src.startsWith('blob:') && ${slot(1)}.src.startsWith('blob:')`, 5000);
  check(results, (await stats()).posts === postsBeforeX + 2, 'scrolling back shows both translations from cache without new requests', (await stats()).requests);
  const before = await feed.evaluate(`window.slotFor(0).dataset.tweet`);
  await feed.evaluate(`window.unmount(0)`);
  await feed.evaluate(`window.remount()`);
  await feed.waitFor(`window.slotFor(0)?.dataset.tweet === '${before}' && ${slot(0)}.src.startsWith('blob:')`, 5000);
  check(results, true, 'a newly mounted element for a translated image shows the translation');
  feed.close();

  const thread = await open('https://x.com/artist/status/100', `document.querySelectorAll('.grid img').length === 4 && [...document.images].every(i => i.complete)`);
  check(results, await thread.evaluate(`getComputedStyle(document.querySelector('.grid img')).opacity === '0' && ${shownExpr(`document.querySelector('.grid img')`)}.includes('GthrA1AAx_q')`), 'mock: an X photo is drawn by a background layer over a transparent img');
  const grid = (i) => `document.querySelectorAll('.grid img')[${i}]`;
  const gridRects = `JSON.stringify([...document.querySelectorAll('.grid img')].map(i => { const r = i.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }))`;
  const gridBefore = await thread.evaluate(gridRects);
  await sleep(1000);
  check(results, (await posted('x.com/i/status/100')).length === 0, 'a newly opened thread sends nothing on its own');
  const lowerLost = await walkToTranslate(thread, grid(2), false);
  check(results, lowerLost.length === 0, 'on a tweet page the Translate chip of a lower grid photo stays put while the pointer walks onto it', lowerLost.slice(0, 5));
  const threadLost = await walkToTranslate(thread, grid(0));
  check(results, threadLost.length === 0, 'on a tweet page the Translate chip stays put while the pointer walks from the photo onto it', threadLost.slice(0, 5));
  await thread.waitFor(`[0, 1, 2, 3].every(i => document.querySelectorAll('.grid img')[i].src.startsWith('blob:'))`, 40000);
  check(results, JSON.stringify(await posted('x.com/i/status/100')) === '[1,2,3,4]', 'translating one photo of a post prefetches the rest of that post', await posted('x.com/i/status/100'));
  const gridShown = await thread.evaluate(`[0, 1, 2, 3].map(i => ${shownExpr(`document.querySelectorAll('.grid img')[i]`)})`);
  check(results, gridShown.every((u) => u?.startsWith('blob:')), 'the translated photos of a tweet are what the reader sees', gridShown);
  await sleep(600);
  const gridAfter = await thread.evaluate(gridRects);
  check(results, gridAfter === gridBefore, 'the photo grid keeps its layout after the swaps', gridAfter === gridBefore ? undefined : { gridBefore, gridAfter });
  await thread.screenshot(`${out}/sites-x-thread.png`);
  const modal = await thread.evaluate(`new Promise((resolve) => {
    const mo = new MutationObserver(() => {
      const img = document.querySelector('#modal img');
      if (img) { mo.disconnect(); requestAnimationFrame(() => resolve({ src: img.src.slice(0, 5), shown: ${shownExpr(`img`)}, path: location.pathname })); }
    });
    mo.observe(document.body, { childList: true, subtree: true });
    document.querySelectorAll('.grid a')[1].click();
  })`);
  check(results, modal.src === 'blob:' && modal.path.endsWith('/photo/2'), 'opening the photo modal swaps its img on the first frame', modal);
  check(results, modal.shown?.startsWith('blob:'), 'opening the photo modal shows the translation on its first frame', modal);
  await thread.key('keyDown', 'Alt', 'AltLeft', 18);
  await sleep(200);
  const peeked = await shown(thread, PHOTO);
  check(results, peeked?.includes('GthrA2AAx_q?format=jpg&name=medium'), 'Alt in the modal shows its original', peeked);
  await thread.key('keyUp', 'Alt', 'AltLeft', 18);
  await sleep(200);
  const restored = await shown(thread, PHOTO);
  check(results, restored?.startsWith('blob:'), 'releasing Alt in the modal shows the translation again', restored);
  await thread.screenshot(`${out}/sites-x-modal.png`);
  check(results, JSON.stringify(await posted('x.com/i/status/100')) === '[1,2,3,4]', 'the modal needed no new translation');
  thread.close();

  const solo = await open('https://x.com/artist/status/200', `!!document.querySelector('.media img')?.complete`);
  await solo.evaluate(`document.querySelector('.media a').click()`);
  await solo.waitFor(photoReady);
  await sleep(300);
  const under = await rectOf(solo, `document.querySelector('.media img')`);
  const over = await rectOf(solo, PHOTO);
  const both = { x: (Math.max(under.left, over.left) + Math.min(under.right, over.right)) / 2, y: (Math.max(under.top, over.top) + Math.min(under.bottom, over.bottom)) / 2 };
  check(results, both.x > over.left && both.x < over.right && both.y > over.top && both.y < over.bottom && both.y > under.top && both.y < under.bottom, 'mock: the photo view covers the tweet photo under it', { under, over });
  const beforeShot = await clip(solo, PHOTO);
  await mouse(solo, 'mouseMoved', both);
  const offer = await waitChip(solo, '.translate', (n) => n.visible, 5000).catch(() => undefined);
  check(results, !!offer, 'hovering the photo view offers Translate');
  const chipAt = offer && center(offer.box);
  check(results, chipAt && chipAt.x > over.left && chipAt.x < over.right && chipAt.y > over.top && chipAt.y < over.top + 60, 'the chip sits on the photo view, not on the photo under it', { chipAt, over });
  if (offer) {
    await mouse(solo, 'mouseMoved', chipAt);
    await mouse(solo, 'mousePressed', chipAt);
    await mouse(solo, 'mouseReleased', chipAt);
    await solo.waitFor(`${PHOTO}.src.startsWith('blob:')`, 20000);
    await sleep(600);
    const fresh = await shown(solo, PHOTO);
    check(results, fresh?.startsWith('blob:'), 'a translation started in the photo view replaces what the reader sees', fresh);
    check(results, (await clip(solo, PHOTO)) !== beforeShot, 'the photo view looks different after the translation lands');
    check(results, JSON.stringify(await posted('x.com/i/status/200')) === '[1]', 'the photo view sends its photo once, keyed by its tweet', await posted('x.com/i/status/200'));
    await solo.key('keyDown', 'Alt', 'AltLeft', 18);
    await sleep(200);
    const soloPeek = await shown(solo, PHOTO);
    check(results, soloPeek?.includes('GsolA1AAx_q?format=jpg&name=medium') && (await clip(solo, PHOTO)) === beforeShot, 'Alt in the photo view shows the original again', soloPeek);
    await solo.key('keyUp', 'Alt', 'AltLeft', 18);
    await sleep(200);
    check(results, (await shown(solo, PHOTO))?.startsWith('blob:'), 'releasing Alt in the photo view shows the translation again');
  }
  await solo.screenshot(`${out}/sites-x-photo.png`);
  solo.close();

  const direct = await open('https://x.com/artist/status/300/photo/1', photoReady);
  await sleep(500);
  await mouse(direct, 'mouseMoved', await centerOf(direct, PHOTO));
  check(results, !!(await waitChip(direct, '.translate', (n) => n.visible, 5000).catch(() => undefined)), 'a photo view opened by URL offers Translate on hover');
  direct.close();

  const TALL_IMG = `document.querySelector('.tall-frame img')`;
  const tallTweet = await open('https://x.com/artist/status/400', `!!${TALL_IMG}?.complete`);
  const tallShape = await tallTweet.evaluate(`(() => {
    const img = ${TALL_IMG};
    const photo = img.parentElement;
    const r = img.getBoundingClientRect();
    const cs = getComputedStyle(img);
    return { z: cs.zIndex, opacity: cs.opacity, testid: photo.dataset.testid, transform: getComputedStyle(photo).transform, siblings: [...photo.children].map((c) => c.tagName).join(), link: !!img.closest('a[href$="/photo/1"]'), hit: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === img, size: [Math.round(r.width), Math.round(r.height)] };
  })()`);
  check(results, tallShape.z === '-1' && tallShape.opacity === '0' && tallShape.testid === 'tweetPhoto' && tallShape.transform !== 'none' && tallShape.siblings === 'DIV,IMG' && tallShape.link && tallShape.hit, 'mock: a tall X photo is a transparent z-index -1 img in a transformed tweetPhoto frame and wins the hit test', tallShape);
  await mouse(tallTweet, 'mouseMoved', await centerOf(tallTweet, TALL_IMG));
  check(results, !!(await waitChip(tallTweet, '.translate', (n) => n.visible, 5000).catch(() => undefined)), 'hovering a tall single photo on a tweet page offers Translate');

  const postsBeforeCached = (await stats()).posts;
  const cached = await attach(await openTab(CDP_PORT, 'about:blank'));
  await cached.send('Page.enable');
  await cached.send('DOM.enable');
  await cached.send('Page.addScriptToEvaluateOnNewDocument', {
    source: `window.__ghosts = []; new MutationObserver((rs) => rs.forEach((r) => r.addedNodes.forEach((n) => n.getAttribute?.('aria-hidden') === 'true' && n.parentNode === document.documentElement && window.__ghosts.push((({ left, top, right, bottom }) => ({ left, top, right, bottom }))(n.getBoundingClientRect()))))).observe(document, { childList: true, subtree: true });`,
  });
  await cached.send('Page.navigate', { url: 'https://x.com/artist/status/100/photo/3' });
  await cached.waitFor(photoReady);
  const cachedShown = await cached.waitFor(`(() => { const u = ${shownExpr(PHOTO)}; return u?.startsWith('blob:') ? { ok: true, u } : u; })()`, 8000).catch((e) => e.message.split('\n').pop());
  check(results, cachedShown?.ok, 'a photo view opened by URL shows an earlier translation without a click', cachedShown);
  check(results, (await stats()).posts === postsBeforeCached, 'showing an earlier translation sends nothing new', (await stats()).requests.slice(-3));
  const photoBox = await rectOf(cached, PHOTO);
  const ghosts = await cached.evaluate(`window.__ghosts`);
  check(results, ghosts.length > 0 && ghosts.every((g) => Math.abs(g.left - photoBox.left) < 2 && Math.abs(g.top - photoBox.top) < 2 && Math.abs(g.right - photoBox.right) < 2), 'only the photo view fades in; the covered tweet photos do not flash over it', { ghosts, photoBox });
  await cached.screenshot(`${out}/sites-x-photo-cached.png`);
  cached.close();

  const art = await open('https://www.pixiv.net/en/artworks/5001', `[...document.images].length >= 7 && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const pg = (p) => `document.querySelector('img[alt="5001_p${p}"]')`;
  const artPopup = await enable(sw, extId, 'https://www.pixiv.net/');
  check(results, (await artPopup.evaluate(`document.getElementById('site-mode').value`)) === 'on-demand', 'pixiv.net defaults to on-demand');
  artPopup.close();
  await art.send('Page.bringToFront');
  await sleep(1500);
  check(results, (await extensionFetches('i.pximg.net')).length === 0 && (await posted('pixiv.net/artworks/5001')).length === 0, 'pixiv: no image fetch and no translation before a click');
  check(results, await hoverNoOffer(art, `document.querySelector('.author img')`), 'the author avatar never offers Translate');
  await clickTranslate(art, pg(0));
  await art.waitFor(`${pg(0)}.src.startsWith('blob:')`, 20000);
  const original = (await extensionFetches('i.pximg.net')).find((r) => r.path.includes('5001_p0'));
  check(results, original?.path === '/img-original/img/2026/10/24/20/00/54/5001_p0.png' && original.referer === 'https://www.pixiv.net/' && original.status === 200, 'the original is fetched with the pixiv Referer', original);
  const ahead = Date.now() + 40000;
  while (Date.now() < ahead && (await posted('pixiv.net/artworks/5001')).length < 4) await sleep(300);
  check(results, JSON.stringify((await posted('pixiv.net/artworks/5001')).slice(0, 4)) === '[0,1,2,3]', 'translating p0 prefetches p1 to p3 of the same work', await posted('pixiv.net/artworks/5001'));
  await art.waitFor(`${pg(1)}.src.startsWith('blob:')`, 20000);
  await art.evaluate(`${pg(2)}.scrollIntoView()`);
  const later = Date.now() + 30000;
  while (Date.now() < later && !(await posted('pixiv.net/artworks/5001')).includes(4)) await sleep(300);
  check(results, (await posted('pixiv.net/artworks/5001')).includes(4), 'the prefetch window follows the reader through a work they started', await posted('pixiv.net/artworks/5001'));
  await art.screenshot(`${out}/sites-pixiv.png`);
  art.close();

  const stripImg = `document.querySelector('img[alt="5002_p0"]')`;
  const strip = await open('https://www.pixiv.net/artworks/5002', `!!${stripImg}?.complete`);
  const box = `JSON.stringify((({ width, height }) => ({ width, height }))(${stripImg}.getBoundingClientRect()))`;
  const boxBefore = await strip.evaluate(box);
  await clickTranslate(strip, stripImg);
  await strip.waitFor(`${stripImg}.src.startsWith('blob:') && ${stripImg}.complete`, 20000);
  const tall = await strip.evaluate(`({ w: ${stripImg}.naturalWidth, h: ${stripImg}.naturalHeight, box: ${box} })`);
  check(results, tall.w === 800 && tall.h === 9000 && tall.box === boxBefore, 'a very tall strip is sent whole and shown at full height without a layout shift', { ...tall, boxBefore });
  await strip.screenshot(`${out}/sites-pixiv-strip.png`);

  const workPg = (id, p) => `document.querySelector('img[alt="${id}_p${p}"]')`;
  const loaded = (id) => `[...document.querySelectorAll('img[alt^="${id}_p"]')].every(i => i.complete && i.naturalWidth > 0)`;
  const work = await open('https://www.pixiv.net/artworks/5001', loaded(5001));
  const autoPopup = await popupFor(sw, extId, 'https://www.pixiv.net/artworks/5001');
  await setMode(autoPopup, 'auto');
  autoPopup.close();
  await work.send('Page.bringToFront');
  await work.waitFor(`${workPg(5001, 0)}.src.startsWith('blob:')`, 20000);
  const offPopup = await popupFor(sw, extId, 'https://www.pixiv.net/artworks/5001');
  await offPopup.waitFor(`!document.getElementById('work').hidden`, 5000);
  check(results, await offPopup.evaluate(`document.getElementById('work-toggle').checked && document.getElementById('work-english').hidden`), 'the popup offers a switch for the open artwork, on by default');
  await offPopup.evaluate(`document.getElementById('work-toggle').click()`);
  offPopup.close();
  await work.send('Page.bringToFront');
  await work.waitFor(`!${workPg(5001, 0)}.src.startsWith('blob:')`, 10000);
  check(results, true, 'turning the artwork off puts its original pages back');
  const offPosts = (await posted('pixiv.net/artworks/5001')).length;
  await work.send('Page.reload');
  await work.waitFor(loaded(5001), 20000);
  await sleep(4000);
  check(
    results,
    !(await work.evaluate(`[...document.querySelectorAll('img[alt^="5001_p"]')].some(i => i.src.startsWith('blob:'))`)) && (await posted('pixiv.net/artworks/5001')).length === offPosts,
    'an artwork turned off stays untranslated after a reload, even with its translations cached',
    await posted('pixiv.net/artworks/5001'),
  );
  const other = await open('https://www.pixiv.net/artworks/5003', loaded(5003));
  await other.waitFor(`${workPg(5003, 0)}.src.startsWith('blob:')`, 20000);
  check(results, (await posted('pixiv.net/artworks/5003')).includes(0), 'another artwork on the same site still translates automatically', await posted('pixiv.net/artworks/5003'));
  other.close();
  const onPopup = await popupFor(sw, extId, 'https://www.pixiv.net/artworks/5001');
  await onPopup.waitFor(`!document.getElementById('work').hidden && !document.getElementById('work-toggle').checked`, 5000);
  await onPopup.evaluate(`document.getElementById('work-toggle').click()`);
  await setMode(onPopup, 'on-demand');
  onPopup.close();
  await work.send('Page.bringToFront');
  await work.waitFor(`${workPg(5001, 0)}.src.startsWith('blob:')`, 20000);
  check(results, true, 'turning the artwork back on shows its translations again');
  work.close();

  for (const m of localMocks.filter((m) => m.checks)) {
    await m.checks({ sw, extId, out, sleep, open, enable, popup: (url) => popupFor(sw, extId, url), posted, siteRequests, rawStatus, check: (ok, name, detail) => check(results, ok, name, detail) });
  }

  const hosts = () => tallTweet.evaluate(`document.querySelectorAll('yomikae-chips').length`);
  check(results, (await hosts()) === 1, 'an enabled tab runs one content script instance');
  const tallTab = await sw.evaluate(`chrome.tabs.query({}).then(ts => ts.find(t => t.url?.startsWith('https://x.com/artist/status/400')).id)`);
  const restartExtension = async () => {
    const extensions = await attach(await openTab(CDP_PORT, 'chrome://extensions/'));
    await extensions.waitFor(`typeof chrome.management?.setEnabled === 'function'`);
    await extensions.evaluate(`chrome.management.setEnabled('${extId}', false)`);
    await sleep(500);
    await extensions.evaluate(`chrome.management.setEnabled('${extId}', true)`);
    extensions.close();
    await extensionWorker(CDP_PORT, 'Yomikae');
  };
  await restartExtension();
  const again = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tallTab}`));
  await again.waitFor(`!document.getElementById('site').hidden`);
  if (await again.evaluate(`document.getElementById('site-toggle').checked`)) {
    await again.evaluate(`document.getElementById('site-toggle').click()`);
    await again.waitFor(`!document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  }
  await again.evaluate(`document.getElementById('site-toggle').click()`);
  await again.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  again.close();
  await sleep(800);
  check(results, (await hosts()) === 1, 'turning a site back on after the extension was turned off and on leaves one instance in a tab that was open', await hosts());
  await tallTweet.send('Page.bringToFront');
  await mouse(tallTweet, 'mouseMoved', await centerOf(tallTweet, TALL_IMG));
  check(results, !!(await waitChip(tallTweet, '.translate', (n) => n.visible, 5000).catch(() => undefined)), 'the new instance offers Translate after it takes over');
  await restartExtension();
  await tallTweet.send('Page.bringToFront');
  await mouse(tallTweet, 'mouseMoved', { x: 1, y: 1 });
  await mouse(tallTweet, 'mouseMoved', await centerOf(tallTweet, TALL_IMG));
  await sleep(500);
  check(results, (await hosts()) === 0 && !(await visible(tallTweet, '.translate')), 'an instance left behind by a turned-off extension removes itself instead of offering Translate', await hosts());
} catch (err) {
  check(results, false, 'run completed', err.message);
  console.log('mock stats', JSON.stringify(await stats().catch(() => null)));
  for (const t of (await targets(CDP_PORT)).filter((t) => t.type === 'page' && !t.url.startsWith('chrome-extension://'))) {
    const shot = attach(t).then((s) => s.screenshot(`${out}/sites-failure-${new URL(t.url).hostname}.png`));
    await Promise.race([shot.catch(() => undefined), sleep(3000)]);
  }
} finally {
  children.forEach((c) => c.kill());
  if (!process.env.KEEP) process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
