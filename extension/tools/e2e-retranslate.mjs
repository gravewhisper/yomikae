import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { attach, center, check, chipNodes, extensionWorker, launchChromium, mouse, openTab, sleep, targets, waitChip } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9481;
const MOCK_PORT = 7898;
const SITE_PORT = 8798;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const SITE = `http://127.0.0.1:${SITE_PORT}/`;
const CONTEXT = `127.0.0.1:${SITE_PORT}`;
const results = [];
const children = [];

for (const port of [CDP_PORT, MOCK_PORT, SITE_PORT]) {
  if (await fetch(`http://127.0.0.1:${port}/`).then(() => true, () => false)) throw new Error(`port ${port} is already in use`);
}

const startNode = (script, env) => {
  const p = spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' });
  children.push(p);
  return p;
};

startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '600' });
startNode('./test-site.mjs', { PORT: String(SITE_PORT), PAGES: '4' });
const chromium = await launchChromium({ profile: `${out}/profile-retranslate`, port: CDP_PORT, extension: resolve('dist'), args: process.env.HEADED ? [] : ['--headless=new'] });
await sleep(800);

const stats = async () => (await fetch(`${MOCK}/mock/stats`)).json();
const srcOf = (page, i) => page.evaluate(`document.querySelectorAll('img.page')[${i}].src`);
const imgRect = (page, i) => page.evaluate(`(() => { const r = document.querySelectorAll('img.page')[${i}].getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })()`);
const click = async (page, at, modifiers = 0) => {
  await mouse(page, 'mouseMoved', at, { modifiers });
  await mouse(page, 'mousePressed', at, { modifiers });
  await mouse(page, 'mouseReleased', at, { modifiers });
};
const press = async (page, key, code, keyCode, modifiers = 0) => {
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode, modifiers });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode, modifiers });
};

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${MOCK}', siteModes: { '127.0.0.1': 'auto' } })`);
  const manifest = JSON.parse(await readFile(resolve('dist/manifest.json'), 'utf8'));
  const warnings = await sw.evaluate(`(async () => {
    const m = ${JSON.stringify(manifest)};
    if (!chrome.management?.getPermissionWarningsByManifest) return null;
    const without = { ...m, permissions: m.permissions.filter((p) => p !== 'sidePanel') };
    return { with: await chrome.management.getPermissionWarningsByManifest(JSON.stringify(m)), without: await chrome.management.getPermissionWarningsByManifest(JSON.stringify(without)) };
  })()`);
  check(results, warnings === null || JSON.stringify(warnings.with) === JSON.stringify(warnings.without), 'the sidePanel permission adds no install warning', warnings ?? 'chrome.management is not available to this extension');

  const page = await attach(await openTab(CDP_PORT, SITE));
  await page.send('Page.enable');
  await page.send('DOM.enable');
  await page.waitFor(`document.readyState === 'complete' && [...document.images].every(i => i.complete && i.naturalWidth > 0)`);
  const tabId = await sw.evaluate(`chrome.tabs.query({ url: '${SITE}*' }).then(t => t[0].id)`);
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`document.getElementById('server').dataset.state === 'ok' && !document.getElementById('site').hidden`);
  await popup.evaluate(`document.getElementById('site-toggle').click()`);
  await popup.waitFor(`document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`);
  check(results, await popup.evaluate(`!document.getElementById('open-cast').hidden`), 'the popup offers the cast sheet once the site is on');
  await page.send('Page.bringToFront');
  await page.waitFor(`[...document.querySelectorAll('img.page')].slice(0, 2).every(i => i.src.startsWith('blob:'))`, 20000);

  const img = await imgRect(page, 0);
  const at = (fx, fy) => ({ x: img.left + fx * img.width, y: img.top + fy * img.height });
  await mouse(page, 'mouseMoved', { x: 5, y: 5 });
  await sleep(200);
  check(results, (await chipNodes(page, '.retranslate')).every((n) => !n.visible), 'no Retranslate item without hover');
  await mouse(page, 'mouseMoved', at(0.3, 0.45));
  const item = await waitChip(page, '.retranslate');
  await page.screenshot(`${out}/retranslate-1-hover.png`);
  check(results, item.outerHTML.includes('Alt+T'), 'the Retranslate item names its shortcut');

  await page.key('keyDown', 'Alt', 'AltLeft', 18);
  await press(page, 't', 'KeyT', 84, 1);
  await page.key('keyUp', 'Alt', 'AltLeft', 18);
  await waitChip(page, '.fix');
  check(results, (await chipNodes(page, '.note')).every((n) => !n.visible), 'Alt+T opens the retranslate box, not the report note');
  await press(page, 'Enter', 'Enter', 13);
  await waitChip(page, '.toast', (n) => n.outerHTML.includes('Click the bubbles'));
  check(results, (await stats()).retranslates.length === 0, 'Enter with nothing picked sends nothing and says why');

  await click(page, at(0.72, 0.55));
  await waitChip(page, '.area', (n) => n.visible && n.outerHTML.includes('bubble 1'));
  const drag = [at(0.1, 0.05), at(0.3, 0.12)];
  await mouse(page, 'mouseMoved', drag[0]);
  await mouse(page, 'mousePressed', drag[0]);
  for (let i = 1; i <= 4; i++) await mouse(page, 'mouseMoved', { x: drag[0].x + ((drag[1].x - drag[0].x) * i) / 4, y: drag[0].y + ((drag[1].y - drag[0].y) * i) / 4 }, { held: true });
  await mouse(page, 'mouseReleased', drag[1]);
  await sleep(200);
  check(results, (await chipNodes(page, '.area')).filter((n) => n.visible).length === 1, 'a drag adds no free box in retranslate mode');
  await page.send('Input.insertText', { text: 'Lene is a girl' });
  const ja = await waitChip(page, '.ja');
  await click(page, center(ja.box));
  await page.send('Input.insertText', { text: '在藤' });
  const en = await waitChip(page, '.en');
  await click(page, center(en.box));
  await page.send('Input.insertText', { text: 'Arifuji' });
  await mouse(page, 'mouseMoved', at(0.3, 0.45));
  await sleep(150);
  await page.screenshot(`${out}/retranslate-2-box.png`);
  const before = { p1: await srcOf(page, 0), p2: await srcOf(page, 1) };
  await press(page, 'Enter', 'Enter', 13);
  await waitChip(page, '.spin', (n) => n.visible, 5000);
  await page.screenshot(`${out}/retranslate-3-busy.png`);
  await page.waitFor(`document.querySelectorAll('img.page')[0].src !== ${JSON.stringify(before.p1)} && document.querySelectorAll('img.page')[0].src.startsWith('blob:')`, 15000);
  await waitChip(page, '.toast', (n) => n.outerHTML.includes('Retranslated'));
  await sleep(300);
  await page.screenshot(`${out}/retranslate-4-done.png`);
  const sent = (await stats()).retranslates[0]?.request;
  check(
    results,
    JSON.stringify(sent) === JSON.stringify({ context_key: CONTEXT, page_index: 1, bubbles: [1], hint: 'Lene is a girl', names: [{ ja: '在藤', en: 'Arifuji', kind: 'name' }] }),
    'the box sends the picked bubble, the hint and the name fix',
    sent,
  );
  check(results, (await srcOf(page, 1)) === before.p2, 'a page out of view is not redone right away');
  const quiet = await stats();
  check(results, !quiet.requests.slice(quiet.requests.indexOf('retranslate 1')).some((r) => r.startsWith('page ')), 'nothing is requested for pages that are not viewed', quiet.requests);

  await page.evaluate(`document.querySelectorAll('img.page')[1].scrollIntoView()`);
  await page.waitFor(`document.querySelectorAll('img.page')[1].src !== ${JSON.stringify(before.p2)}`, 15000);
  const viewed = await stats();
  const since = viewed.requests.slice(viewed.requests.indexOf('retranslate 1') + 1);
  check(results, viewed.updated === 1 && since.includes('page 2'), 'page 2 is asked for again when it comes into view and swaps in place', since);
  check(results, !since.includes('page 3') && !since.includes('page 4'), 'pages 3 and 4, still out of view, are not asked for again', since);
  await page.screenshot(`${out}/retranslate-5-page2-redone.png`);

  const panel = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/sidepanel.html?tab=${tabId}`));
  await panel.send('Emulation.setDeviceMetricsOverride', { width: 380, height: 720, deviceScaleFactor: 1, mobile: false });
  await panel.waitFor(`document.querySelectorAll('.entry').length >= 3`);
  const rows = `[...document.querySelectorAll('.group')].map(g => [g.dataset.kind, [...g.querySelectorAll('.entry')].map(e => e.querySelector('.en').value + ':' + e.querySelector('.src').textContent)])`;
  check(results, (await panel.evaluate(`document.getElementById('work').textContent`)) === CONTEXT, 'the cast sheet shows the tab’s work');
  check(results, JSON.stringify(await panel.evaluate(rows)) === JSON.stringify([['name', ['Arifuji:yours', 'Kazune:furigana']], ['term', ['Gekko Style:furigana']]]), 'the cast sheet lists characters and terms with where each came from', await panel.evaluate(rows));
  await panel.screenshot(`${out}/castsheet-1.png`);

  const p2 = await srcOf(page, 1);
  const puts = (await stats()).namePuts;
  await panel.evaluate(`(() => { const i = [...document.querySelectorAll('.entry .en')].find(i => i.value === 'Arifuji'); i.value = 'Arifugi'; i.dispatchEvent(new Event('change')); })()`);
  await panel.waitFor(`document.getElementById('status').textContent.startsWith('Saved')`);
  check(results, (await stats()).namePuts === puts + 1, 'editing a spelling saves the names');
  await page.waitFor(`document.querySelectorAll('img.page')[1].src !== ${JSON.stringify(p2)}`, 15000);
  check(results, (await stats()).updated === 2, 'a page in view is redone after a spelling changes in the cast sheet');

  await panel.evaluate(`[...document.querySelectorAll('.entry')].find(e => e.querySelector('.en').value === 'Kazune').querySelector('.del').click()`);
  await panel.waitFor(`![...document.querySelectorAll('.entry .en')].some(i => i.value === 'Kazune')`);
  await panel.evaluate(`[...document.querySelectorAll('.entry')].find(e => e.querySelector('.en').value === 'Gekko Style').querySelector('.flip').click()`);
  await panel.waitFor(`[...document.querySelectorAll('.group[data-kind="name"] .entry .en')].some(i => i.value === 'Gekko Style')`);
  await panel.evaluate(`(() => { document.getElementById('add-ja').value = '凛恋'; document.getElementById('add-en').value = 'Rinren'; document.getElementById('add-reading').value = 'りんれん'; document.getElementById('add').requestSubmit(); })()`);
  await panel.waitFor(`[...document.querySelectorAll('.entry .en')].some(i => i.value === 'Rinren')`);
  const names = (await (await fetch(`${MOCK}/v1/names?context_key=${encodeURIComponent(CONTEXT)}`)).json()).names;
  check(
    results,
    JSON.stringify(names.map((n) => `${n.ja}:${n.en}:${n.source}:${n.kind}`)) === JSON.stringify(['在藤:Arifugi:user:name', '月光流:Gekko Style:user:name', '凛恋:Rinren:user:name']),
    'delete, flip and add reach the server',
    names,
  );
  await panel.screenshot(`${out}/castsheet-2-edited.png`);

  await page.evaluate(`history.pushState(null, '', '/other/')`);
  await page.evaluate(`window.scrollBy(0, 40)`);
  await panel.waitFor(`document.getElementById('work').textContent === '${CONTEXT}/other'`, 10000);
  check(results, true, 'the cast sheet follows the tab to another work');
  const follower = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/sidepanel.html`));
  await follower.waitFor(`document.readyState === 'complete'`);
  await page.send('Page.bringToFront');
  await follower.waitFor(`document.getElementById('work').textContent === '${CONTEXT}/other'`, 10000);
  check(results, true, 'without a forced tab the cast sheet follows the tab that becomes active');
} catch (err) {
  check(results, false, 'run completed', err.message);
  console.log('mock stats', JSON.stringify(await stats().catch(() => null)));
  const p = (await targets(CDP_PORT)).find((t) => t.url.startsWith(SITE));
  if (p) await (await attach(p)).screenshot(`${out}/retranslate-failure.png`);
} finally {
  children.forEach((c) => c.kill());
  process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
