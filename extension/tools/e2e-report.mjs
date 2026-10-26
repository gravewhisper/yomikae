import { execFileSync, spawn } from 'node:child_process';
import { readdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import { attach, center, check, chipNodes, extensionWorker, launchChromium, mouse, openTab, sleep, targets, waitChip } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const SERVER_DIR = resolve('../server');
const CDP_PORT = 9447;
const SERVER_PORT = 7880;
const SITE_PORT = 8766;
const SERVER = `http://127.0.0.1:${SERVER_PORT}`;
const SITE = `http://127.0.0.1:${SITE_PORT}/`;
const REPORTS = `${out}/reports`;
const results = [];

const alive = await fetch(`${SERVER}/v1/health`).then(() => true, () => false);
if (alive) throw new Error(`port ${SERVER_PORT} is already in use; stop that dev server first`);
await rm(REPORTS, { recursive: true, force: true });
await rm(`${out}/report-cache`, { recursive: true, force: true });
await rm(`${out}/profile-report`, { recursive: true, force: true });

execFileSync(`${SERVER_DIR}/.venv/bin/python`, [`${SERVER_DIR}/tools/make_test_page.py`, `${out}/pages`, '--seed', '0', '--count', '1']);
const png = await readFile(`${out}/pages/page_0.png`);
const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1300"><rect width="900" height="1300" fill="#eee"/><text x="450" y="650" font-size="120" text-anchor="middle">SVG</text></svg>');
const site = createServer((req, res) => {
  if (req.url === '/pages/1.png') return res.writeHead(200, { 'content-type': 'image/png' }), res.end(png);
  if (req.url === '/pages/2.svg') return res.writeHead(200, { 'content-type': 'image/svg+xml' }), res.end(svg);
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(`<!doctype html><html><head><meta charset="utf-8"><title>report test</title>
<style>body{margin:0;background:#333} main{width:700px;margin:0 auto} img{display:block;width:700px;margin:0 0 24px}</style></head>
<body><main><img class="page" src="/pages/1.png" onclick="window.imgClicks=(window.imgClicks||0)+1"><img class="page" src="/pages/2.svg"></main>
<script>document.addEventListener('click', () => (window.docClicks = (window.docClicks || 0) + 1));</script></body></html>`);
}).listen(SITE_PORT, '127.0.0.1');

const server = spawn(`${SERVER_DIR}/.venv/bin/uvicorn`, ['yomikae.app:app', '--host', '127.0.0.1', '--port', String(SERVER_PORT)], {
  cwd: SERVER_DIR,
  env: { ...process.env, CUDA_VISIBLE_DEVICES: '', YOMIKAE_TRANSLATOR: 'fake', YOMIKAE_CACHE_DIR: `${out}/report-cache`, YOMIKAE_REPORTS_DIR: REPORTS },
  stdio: 'inherit',
});
for (let i = 0; ; i++) {
  if (await fetch(`${SERVER}/v1/health`).then((r) => r.ok, () => false)) break;
  if (i > 600 || server.exitCode !== null) throw new Error('dev server did not start');
  await sleep(300);
}

const chromium = await launchChromium({ profile: `${out}/profile-report`, port: CDP_PORT, extension: resolve('dist'), args: process.env.HEADED ? [] : ['--headless=new'] });
await sleep(800);

const imgRect = (page, i) => page.evaluate(`(() => { const r = document.querySelectorAll('img.page')[${i}].getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })()`);
const imgCenter = async (page, i) => page.evaluate(`(() => { const r = document.querySelectorAll('img.page')[${i}].getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + Math.min(r.height, innerHeight - r.top) / 2 }; })()`);

async function altR(page) {
  await page.key('keyDown', 'Alt', 'AltLeft', 18);
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'r', code: 'KeyR', windowsVirtualKeyCode: 82, modifiers: 1 });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'r', code: 'KeyR', windowsVirtualKeyCode: 82, modifiers: 1 });
  await page.key('keyUp', 'Alt', 'AltLeft', 18);
}

async function submitNote(page, text, shot) {
  await waitChip(page, '.note');
  if (text) await page.send('Input.insertText', { text });
  if (shot) await page.screenshot(shot);
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
}

async function receipt(page, seen) {
  const toast = await waitChip(page, '.toast', (n) => /Reported · (\S+)</.test(n.outerHTML) && !seen.has(n.outerHTML.match(/Reported · (\S+)</)[1]));
  const id = toast.outerHTML.match(/Reported · (\S+)</)[1];
  seen.add(id);
  const dir = `${REPORTS}/${id}`;
  const files = (await readdir(dir)).sort();
  const meta = JSON.parse(await readFile(`${dir}/report.json`, 'utf8'));
  return { id, dir, files, meta };
}

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${SERVER}', siteModes: { '127.0.0.1': 'auto' } })`);
  const page = await attach(await openTab(CDP_PORT, SITE));
  await page.send('Page.enable');
  await page.send('DOM.enable');
  await page.waitFor(`document.readyState === 'complete' && document.images.length > 0 && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const tabId = await sw.evaluate(`chrome.tabs.query({ url: '${SITE}*' }).then(t => t[0].id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`document.getElementById('server').dataset.state === 'ok' && !document.getElementById('site').hidden`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  await page.send('Page.bringToFront');
  const seen = new Set();

  await waitChip(page, '.spin', (n) => n.visible, 15000);
  await mouse(page, 'mouseMoved', await imgCenter(page, 0));
  await altR(page);
  await submitNote(page, '');
  const pending = await receipt(page, seen);
  check(results, ['queued', 'fetching', 'translating'].includes(pending.meta.job_state), 'Alt+R reports a page that is still translating', pending.meta.job_state);
  check(results, pending.files.includes('original.png') && pending.files.includes('report.json'), 'pending report has the original and report.json', pending.files);
  check(results, (await readFile(`${pending.dir}/original.png`)).equals(png), 'pending report original matches the page bytes');

  await page.waitFor(`document.querySelectorAll('img.page')[0].src.startsWith('blob:')`, 120000);
  await mouse(page, 'mouseMoved', { x: 5, y: 5 });
  await sleep(200);
  check(results, (await chipNodes(page, '.report')).every((n) => !n.visible), 'no Report item on a translated page without hover');
  await mouse(page, 'mouseMoved', await imgCenter(page, 0));
  const button = await waitChip(page, '.report');
  await page.screenshot(`${out}/report-1-hover.png`);
  await mouse(page, 'mouseMoved', center(button.box));
  await mouse(page, 'mousePressed', center(button.box));
  await mouse(page, 'mouseReleased', center(button.box));
  await waitChip(page, '.pick');
  const cached = await (await fetch(`${SERVER}/v1/pages`, { method: 'POST', body: (() => { const f = new FormData(); f.append('image', new Blob([png]), 'p.png'); return f; })() })).json();
  const { width: W, height: H, bubbles } = cached;
  const img = await imgRect(page, 0);
  const toScreen = (x, y) => ({ x: img.left + (x / W) * img.width, y: img.top + (y / H) * img.height });
  const target = 0;
  const [bx0, by0, , by1] = bubbles[target].box;
  const nearBubble = toScreen(bx0, (by0 + by1) / 2);
  nearBubble.x -= 10;
  await mouse(page, 'mouseMoved', nearBubble);
  await mouse(page, 'mousePressed', nearBubble);
  await mouse(page, 'mouseReleased', nearBubble);
  await waitChip(page, '.area');
  const from = { x: img.left + 0.56 * img.width, y: img.top + 0.03 * img.height };
  const to = { x: img.left + 0.74 * img.width, y: img.top + 0.12 * img.height };
  await mouse(page, 'mouseMoved', from);
  await mouse(page, 'mousePressed', from);
  for (let i = 1; i <= 5; i++) await mouse(page, 'mouseMoved', { x: from.x + ((to.x - from.x) * i) / 5, y: from.y + ((to.y - from.y) * i) / 5 }, { held: true });
  await mouse(page, 'mouseReleased', to);
  await waitChip(page, '.area', (n) => n.visible && n.outerHTML.includes('area 1'));
  await mouse(page, 'mouseMoved', { x: img.left + 0.3 * img.width, y: img.top + 0.6 * img.height });
  const clicksDuringPick = await page.evaluate(`({ img: window.imgClicks ?? 0, doc: window.docClicks ?? 0 })`);
  check(results, clicksDuringPick.img === 0 && clicksDuringPick.doc === 0, "pick clicks never reach the site's image or document click handlers", clicksDuringPick);
  check(results, JSON.stringify(await imgRect(page, 0)) === JSON.stringify(img), 'pick mode causes no layout shift');
  await submitNote(page, 'second bubble reads wrong', `${out}/report-2-pick.png`);
  const ready = await receipt(page, seen);
  await sleep(250);
  await page.screenshot(`${out}/report-3-toast.png`);
  check(results, JSON.stringify(ready.files) === JSON.stringify(['marked.png', 'original.png', 'rendered.webp', 'report.json', 'result.json']), 'a translated page report with picks saves all five files', ready.files);
  const [snapped, dragged] = ready.meta.areas ?? [];
  check(results, ready.meta.areas?.length === 2 && snapped.bubble_index === target && JSON.stringify(snapped.box) === JSON.stringify(bubbles[target].box), 'a click just outside a bubble snaps to that bubble', snapped);
  const expected = [0.56 * W, 0.03 * H, 0.74 * W, 0.12 * H];
  check(results, dragged?.bubble_index === null && dragged.box.every((v, i) => Math.abs(v - expected[i]) <= 3) && ready.meta.areas_units === 'pixels', 'a dragged box lands in original image pixels', { got: dragged?.box, expected: expected.map(Math.round) });
  check(results, ready.meta.marked === 'marked.png', 'report.json names marked.png');
  const plain = { x: img.left + 0.3 * img.width, y: img.top + 0.6 * img.height };
  await mouse(page, 'mousePressed', plain);
  await mouse(page, 'mouseReleased', plain);
  const clicksAfter = await page.evaluate(`({ img: window.imgClicks ?? 0, doc: window.docClicks ?? 0 })`);
  check(results, clicksAfter.img === 1 && clicksAfter.doc === 1, 'outside pick mode the same click reaches the site handlers', clicksAfter);
  const result = JSON.parse(await readFile(`${ready.dir}/result.json`, 'utf8'));
  check(results, result.bubbles.length > 0 && result.page_id === ready.meta.page_id && 'context_key' in result, 'result.json is the cached TranslatedPage', { bubbles: result.bubbles.length });
  check(
    results,
    ready.meta.note === 'second bubble reads wrong' && ready.meta.page_url === SITE && ready.meta.image_url === `${SITE}pages/1.png` && ready.meta.job_state === 'ready' && ready.meta.result === 'result.json',
    'report.json carries the note, URLs and state',
    ready.meta,
  );

  await page.evaluate(`document.querySelectorAll('img.page')[1].scrollIntoView()`);
  await waitChip(page, '.status', (n) => n.outerHTML.includes('Can’t translate'), 60000);
  await page.send('ServiceWorker.enable');
  await page.send('ServiceWorker.stopAllWorkers');
  await sleep(300);
  await mouse(page, 'mouseMoved', await imgCenter(page, 1));
  await waitChip(page, '.report');
  await page.screenshot(`${out}/report-4-failed-hover.png`);
  await altR(page);
  await submitNote(page, 'svg page fails');
  const failed = await receipt(page, seen);
  await sleep(250);
  await page.screenshot(`${out}/report-5-failed-toast.png`);
  check(results, JSON.stringify(failed.files) === JSON.stringify(['original.svg', 'report.json']), 'a failed page report keeps the original after the worker restarted', failed.files);
  check(results, failed.meta.result === null && failed.meta.job_state === 'failed' && !!failed.meta.job_error, 'failed report has result null and the failure reason', { job_error: failed.meta.job_error });
  check(results, JSON.stringify(failed.meta.areas) === '[]' && failed.meta.marked === null, 'submitting with nothing picked reports the whole page');
  check(results, (await readFile(`${failed.dir}/original.svg`)).equals(svg), 'failed report original matches the page bytes');

  const sw2 = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw2.session.evaluate(`chrome.storage.local.set({ serverUrl: 'http://127.0.0.1:9' })`);
  await altR(page);
  await submitNote(page, '');
  const err = await waitChip(page, '.toast', (n) => n.outerHTML.includes('Report failed'));
  check(results, err.outerHTML.includes('error') && err.outerHTML.includes('not reachable'), 'a report with the server down shows an error toast', err.outerHTML);
  check(results, (await readdir(REPORTS)).filter((n) => !n.startsWith('.')).length === 3 && !(await readdir(REPORTS)).some((n) => n.startsWith('.')), 'exactly three report dirs and no temp dirs on disk');
} catch (e) {
  check(results, false, 'run completed', e.message);
  const p = (await targets(CDP_PORT)).find((t) => t.url.startsWith(SITE));
  if (p) await (await attach(p)).screenshot(`${out}/report-failure.png`);
} finally {
  server.kill();
  site.close();
  process.kill(-chromium.pid);
  const bad = results.filter((r) => !r.ok);
  console.log(`\n${results.length - bad.length}/${results.length} passed. Screenshots and reports in ${out}`);
  process.exit(bad.length ? 1 : 0);
}
