import { execFileSync, spawn } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import { request } from 'node:https';
import { resolve } from 'node:path';
import { attach, center, check, chipNodes, extensionWorker, launchChromium, mouse, openTab, sleep, targets, waitChip } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9462;
const MOCK_PORT = 7894;
const SITES_PORT = 8792;
const READER_PORT = 8797;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const SITE = '*://*.pixiv.net/*';
const IMAGES = '*://i.pximg.net/*';
const NO_ACCESS = /No access to i\.pximg\.net\. Open the Yomikae popup and click Allow access\./;
const READER = 'alpha.test';
const READER_SITE = '*://*.alpha.test/*';
const CDN = 'media.imgcdn.test';
const CDN_ORIGIN = '*://media.imgcdn.test/*';
const CDN_BLOCKED = /No access to media\.imgcdn\.test\. Open the Yomikae popup and click Allow access\./;
const results = [];
const children = [];

for (const port of [MOCK_PORT, SITES_PORT, READER_PORT]) {
  if (await fetch(`http://127.0.0.1:${port}/`).then(() => true, (e) => e.cause?.code !== 'ECONNREFUSED')) throw new Error(`port ${port} is already in use`);
}
await rm(`${out}/profile-access`, { recursive: true, force: true });
await mkdir(`${out}/certs-access`, { recursive: true });
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=yomikae-mock', '-keyout', `${out}/certs-access/key.pem`, '-out', `${out}/certs-access/cert.pem`], { stdio: 'ignore' });
execFileSync(process.execPath, ['build.mjs'], { env: { ...process.env, ML_OUTDIR: 'dist-access', ML_EXTRA_HOSTS: [SITE, IMAGES, READER_SITE, CDN_ORIGIN].join(',') }, stdio: 'inherit' });

const startNode = (script, env) => children.push(spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' }));
startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '300' });
startNode('./sites-mock.mjs', { PORT: String(SITES_PORT), CERT_DIR: `${out}/certs-access` });
startNode('./test-site.mjs', { PORT: String(READER_PORT), PAGES: '2', IMAGE_ORIGIN: `http://${CDN}` });
const chromium = await launchChromium({
  profile: `${out}/profile-access`,
  port: CDP_PORT,
  extension: resolve('dist-access'),
  args: [
    ...(process.env.HEADED ? [] : ['--headless=new']),
    '--ignore-certificate-errors',
    `--host-resolver-rules=${[...['www.pixiv.net', 'i.pximg.net'].map((h) => `MAP ${h} 127.0.0.1:${SITES_PORT}`), ...[READER, CDN].map((h) => `MAP ${h} 127.0.0.1:${READER_PORT}`)].join(', ')}`,
  ],
});
await sleep(800);

const ctx = 'pixiv.net/artworks/5001';
const posted = async (key = ctx) => (await (await fetch(`${MOCK}/mock/stats`)).json()).byContext[key] ?? [];
const imageFetches = () =>
  new Promise((ok, fail) => {
    request({ host: '127.0.0.1', port: SITES_PORT, path: '/__requests', headers: { host: 'www.pixiv.net' }, rejectUnauthorized: false }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => ok(JSON.parse(body).filter((r) => r.dest === 'empty' && r.host === 'i.pximg.net')));
    })
      .on('error', fail)
      .end();
  });
const shown = `document.querySelector('img[alt="5001_p0"]')`;

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${MOCK}' })`);
  const extensions = await attach(await openTab(CDP_PORT, 'chrome://extensions/'));
  await extensions.waitFor(`typeof chrome.developerPrivate?.addHostPermission === 'function'`);
  const grant = (host) => extensions.evaluate(`chrome.developerPrivate.addHostPermission('${extId}', '${host}')`);
  const registered = () => sw.evaluate(`chrome.scripting.getRegisteredContentScripts().then((s) => s.map((x) => x.id))`);
  await extensions.evaluate(`chrome.developerPrivate.updateExtensionConfiguration({ extensionId: '${extId}', hostAccess: 'ON_SPECIFIC_SITES' })`);
  await grant('http://127.0.0.1/*');
  await grant('http://localhost/*');
  await grant(SITE);
  await sleep(500);
  const granted = await sw.evaluate(`chrome.permissions.getAll().then((p) => p.origins)`);
  const scripts = await registered();
  check(results, granted.includes(SITE) && !granted.includes(IMAGES) && scripts.includes('site:pixiv.net'), 'granting only pixiv.net enables the site without its image host', { granted, scripts });
  await extensions.evaluate(`chrome.developerPrivate.removeHostPermission('${extId}', 'http://localhost/*')`);
  await sleep(500);
  const kept = await registered();
  check(results, kept.includes('site:pixiv.net'), 'reconciling sites keeps one that is missing only its image host', kept);

  await sw.evaluate(`chrome.storage.local.set({ siteModes: { 'pixiv.net': 'auto' } })`);
  const reader = await attach(await openTab(CDP_PORT, 'https://www.pixiv.net/artworks/5001'));
  await reader.send('Page.enable');
  await reader.send('DOM.enable');
  await reader.waitFor(`!!${shown}?.complete && ${shown}.naturalWidth > 0`, 20000);
  const timeOrigin = await reader.evaluate(`performance.timeOrigin`);
  const chip = await waitChip(reader, '.status', (n) => n.visible && NO_ACCESS.test(n.outerHTML), 20000).catch(async () => (await chipNodes(reader, '.status')).map((n) => n.outerHTML));
  check(results, NO_ACCESS.test(chip?.outerHTML ?? ''), 'the chip says which host is missing and where to allow it', chip?.outerHTML ?? chip);
  check(results, !JSON.stringify(await chipNodes(reader, '.status')).includes('Translation failed'), 'the chip does not show the generic failure');
  check(results, (await posted()).length === 0, 'nothing is sent to the server without the image');
  await reader.screenshot(`${out}/access-blocked.png`);
  await reader.send('Page.bringToFront');
  await reader.key('keyDown', 'Alt', 'AltLeft', 18);
  await reader.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'r', code: 'KeyR', windowsVirtualKeyCode: 82, modifiers: 1 });
  await reader.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'r', code: 'KeyR', windowsVirtualKeyCode: 82, modifiers: 1 });
  await reader.key('keyUp', 'Alt', 'AltLeft', 18);
  await waitChip(reader, '.note', (n) => n.visible, 5000);
  await reader.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  await reader.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  const toast = await waitChip(reader, '.toast', (n) => n.outerHTML.includes('Report failed'), 10000).catch(() => undefined);
  check(results, NO_ACCESS.test(toast?.outerHTML ?? ''), 'a report from a blocked page says why it could not include the image', toast?.outerHTML);

  const tabId = await sw.evaluate(`chrome.tabs.query({}).then((ts) => ts.find((t) => t.url?.startsWith('https://www.pixiv.net/')).id)`);
  await reader.send('ServiceWorker.enable');
  await reader.send('ServiceWorker.stopAllWorkers');
  await sleep(500);
  check(results, !(await targets(CDP_PORT)).some((t) => t.type === 'service_worker' && t.url.includes(extId)), 'the service worker is stopped before the grant');
  const popup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${tabId}`));
  await popup.waitFor(`!document.getElementById('site').hidden`);
  const warning = await popup.evaluate(`({ toggle: document.getElementById('site-toggle').checked, hidden: document.getElementById('site-access').hidden, text: document.getElementById('site-access-text').textContent })`);
  check(results, warning.toggle && !warning.hidden && warning.text.includes('i.pximg.net'), 'the popup shows the site on and warns about the missing image host', warning);
  await popup.screenshot(`${out}/access-popup.png`);
  await popup.evaluate(`chrome.permissions.request = (p) => new Promise((r) => { window.__requested = p.origins; window.__answer = r; })`);
  await popup.evaluate(`document.getElementById('site-grant').click()`);
  await popup.waitFor(`!!window.__requested`);
  const requested = await popup.evaluate(`window.__requested`);
  check(results, JSON.stringify(requested) === JSON.stringify([IMAGES]), 'Allow access asks for exactly the missing origin', requested);
  const fetchedBefore = (await imageFetches()).length;
  await grant(IMAGES);
  await popup.evaluate(`window.__answer(true)`);
  await popup.waitFor(`document.getElementById('site-access').hidden && document.getElementById('site-toggle').checked`, 5000);
  check(results, true, 'the popup warning clears once access is granted');
  popup.close();

  await reader.send('Page.bringToFront');
  const swapped = await reader.waitFor(`${shown}.src.startsWith('blob:')`, 30000).then(() => true, () => false);
  check(results, swapped && (await reader.evaluate(`performance.timeOrigin`)) === timeOrigin, 'the open page translates after the grant without a reload');
  const fetched = (await imageFetches()).slice(fetchedBefore);
  check(results, fetched.some((r) => r.status === 200 && r.referer === 'https://www.pixiv.net/'), 'after the grant the image host answers because the Referer rule applies', fetched.map((r) => `${r.host} ${r.status} ${r.referer ?? '-'}`));
  check(results, (await posted()).includes(0), 'the first page is sent to the server after the grant', await posted());
  await reader.screenshot(`${out}/access-granted.png`);

  await grant(READER_SITE);
  await sleep(500);
  const alpha = await attach(await openTab(CDP_PORT, `http://${READER}/`));
  await alpha.send('Page.enable');
  await alpha.send('DOM.enable');
  const page1 = `document.querySelector('img.page')`;
  await alpha.waitFor(`!!${page1}?.complete && ${page1}.naturalWidth > 0`, 20000);
  const alphaOrigin = await alpha.evaluate(`performance.timeOrigin`);
  await alpha.send('Page.bringToFront');
  await mouse(alpha, 'mouseMoved', await alpha.evaluate(`(() => { const r = ${page1}.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + (Math.min(r.bottom, innerHeight) - r.top) / 2 }; })()`));
  const offer = await waitChip(alpha, '.translate', (n) => n.visible, 5000);
  const at = center(offer.box);
  await mouse(alpha, 'mouseMoved', at);
  await mouse(alpha, 'mousePressed', at);
  await mouse(alpha, 'mouseReleased', at);
  const cdnChip = await waitChip(alpha, '.status', (n) => n.visible && CDN_BLOCKED.test(n.outerHTML), 20000).catch(async () => (await chipNodes(alpha, '.status')).map((n) => n.outerHTML));
  check(results, CDN_BLOCKED.test(cdnChip?.outerHTML ?? ''), 'Translate on a generic site whose images come from another host ends blocked and names that host', cdnChip?.outerHTML ?? cdnChip);
  await alpha.screenshot(`${out}/image-host-blocked.png`);

  const alphaTab = await sw.evaluate(`chrome.tabs.query({}).then((ts) => ts.find((t) => t.url?.startsWith('http://${READER}/')).id)`);
  const hostPopup = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${alphaTab}`));
  await hostPopup.waitFor(`!document.getElementById('site').hidden`);
  await hostPopup.waitFor(`!document.getElementById('site-access').hidden`, 3000).catch(() => undefined);
  const hostOffer = await hostPopup.evaluate(`({ toggle: document.getElementById('site-toggle').checked, hidden: document.getElementById('site-access').hidden, text: document.getElementById('site-access-text').textContent, button: document.getElementById('site-grant').textContent })`);
  check(results, hostOffer.toggle && !hostOffer.hidden && hostOffer.text === `Images on this site come from ${CDN}.` && hostOffer.button === 'Allow access', 'the popup names the image host and offers Allow access', hostOffer);
  const beforeClick = await hostPopup.evaluate(`chrome.permissions.getAll().then((p) => p.origins)`);
  check(results, !beforeClick.includes(CDN_ORIGIN), 'nothing is granted for the image host before the click', beforeClick);
  await hostPopup.screenshot(`${out}/image-host-popup.png`);
  await hostPopup.evaluate(`chrome.permissions.request = (p) => new Promise((r) => { window.__requested = p.origins; window.__answer = r; })`);
  await hostPopup.evaluate(`document.getElementById('site-grant').click()`);
  await hostPopup.waitFor(`!!window.__requested`);
  const hostRequested = await hostPopup.evaluate(`window.__requested`);
  check(results, JSON.stringify(hostRequested) === JSON.stringify([CDN_ORIGIN]), 'Allow access asks for exactly the image host', hostRequested);
  await grant(CDN_ORIGIN);
  await hostPopup.evaluate(`window.__answer(true)`);
  await hostPopup.waitFor(`document.getElementById('site-access').hidden && document.getElementById('site-toggle').checked`, 5000).then(
    () => check(results, true, 'the popup offer clears once the image host is granted'),
    () => check(results, false, 'the popup offer clears once the image host is granted'),
  );
  hostPopup.close();

  await alpha.send('Page.bringToFront');
  const alphaSwapped = await alpha.waitFor(`${page1}.src.startsWith('blob:')`, 30000).then(() => true, () => false);
  check(results, alphaSwapped && (await alpha.evaluate(`performance.timeOrigin`)) === alphaOrigin, 'the blocked image translates after the grant without a reload');
  check(results, (await posted(READER)).length > 0, 'the image from the other host is sent to the server', await posted(READER));
  await alpha.screenshot(`${out}/image-host-granted.png`);
  const noCdnSite = (ids) => !ids.some((id) => id.includes('imgcdn'));
  const afterGrant = await registered();
  const siteKeys = `chrome.storage.local.get(null).then((all) => Object.keys(all).filter((k) => k.startsWith('site:')))`;
  const keysAfterGrant = await sw.evaluate(siteKeys);
  check(results, afterGrant.includes(`site:${READER}`) && noCdnSite(afterGrant) && noCdnSite(keysAfterGrant), 'granting the image host registers no site for it', { afterGrant, keysAfterGrant });

  await sw.evaluate(`chrome.storage.local.get('siteModes').then(({ siteModes }) => chrome.storage.local.set({ siteModes: { ...siteModes, '${READER}': 'auto' } }))`);
  const bounce = await attach(await openTab(CDP_PORT, `http://${READER}/bounce`));
  await bounce.send('Page.enable');
  await bounce.send('DOM.enable');
  const bounced = await bounce.waitFor(`document.getElementById('to-web').src.startsWith('blob:')`, 30000).then(() => true, () => false);
  check(results, bounced, 'an image that redirects to another web host still translates');
  const LOCAL_REFUSED = /Refused to fetch 127\.0\.0\.1: media\.imgcdn\.test redirected to this computer\./;
  const refusedChip = await waitChip(bounce, '.status', (n) => n.visible && LOCAL_REFUSED.test(n.outerHTML), 20000).catch(async () => (await chipNodes(bounce, '.status')).map((n) => n.outerHTML));
  check(results, LOCAL_REFUSED.test(refusedChip?.outerHTML ?? ''), 'an image that redirects to this computer is refused and the chip says so', refusedChip?.outerHTML ?? refusedChip);
  const sent = (await (await fetch(`${MOCK}/mock/stats`)).json()).requests;
  check(results, sent.includes('page 8') && !sent.includes('page 7') && !(await bounce.evaluate(`document.getElementById('to-local').src.startsWith('blob:')`)), 'only the image that stays on the web is sent to the server', sent);
  await bounce.screenshot(`${out}/redirect-refused.png`);
  bounce.close();

  await extensions.evaluate(`chrome.developerPrivate.updateProfileConfiguration({ inDeveloperMode: true })`);
  await extensions.evaluate(`chrome.developerPrivate.reload('${extId}', { failQuietly: true })`);
  await sleep(1500);
  const reloaded = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html?tab=${alphaTab}`));
  await reloaded.waitFor(`!document.getElementById('site').hidden`);
  const scriptIds = `chrome.scripting.getRegisteredContentScripts().then((s) => s.map((x) => x.id))`;
  await reloaded.waitFor(`${scriptIds}.then((ids) => ids.includes('site:${READER}'))`, 5000).catch(() => undefined);
  const afterReload = await reloaded.evaluate(`Promise.all([${scriptIds}, chrome.permissions.getAll().then((p) => p.origins), ${siteKeys}]).then(([ids, origins, keys]) => ({ ids, origins, keys, toggle: document.getElementById('site-toggle').checked, offer: !document.getElementById('site-access').hidden }))`);
  check(
    results,
    afterReload.ids.includes(`site:${READER}`) && afterReload.origins.includes(CDN_ORIGIN) && afterReload.toggle && !afterReload.offer && noCdnSite(afterReload.ids) && noCdnSite(afterReload.keys),
    'after the extension reloads the site is still on with its image host and no site exists for the image host',
    afterReload,
  );

  await reloaded.evaluate(`chrome.permissions.remove = (p) => { window.__removed = p.origins; return Promise.resolve(true); }`);
  await reloaded.evaluate(`document.getElementById('site-toggle').click()`);
  await reloaded.waitFor(`!document.getElementById('site-toggle').checked && !document.getElementById('site-toggle').disabled`, 5000);
  const removed = await reloaded.evaluate(`window.__removed ?? []`);
  check(results, [READER_SITE, CDN_ORIGIN].every((o) => removed.includes(o)) && !removed.includes(SITE) && !removed.includes(IMAGES), 'turning the site off removes its image host grant too', removed);
  for (const o of removed) await extensions.evaluate(`chrome.developerPrivate.removeHostPermission('${extId}', '${o}')`);
  await sleep(800);
  const afterDisable = await reloaded.evaluate(`Promise.all([${scriptIds}, chrome.permissions.getAll().then((p) => p.origins), chrome.storage.local.get(null).then((all) => Object.keys(all))]).then(([ids, origins, keys]) => ({ ids, origins, keys }))`);
  check(
    results,
    !afterDisable.origins.includes(CDN_ORIGIN) && !afterDisable.ids.includes(`site:${READER}`) && !afterDisable.keys.some((k) => k.endsWith(READER)) && afterDisable.ids.includes('site:pixiv.net'),
    'after the site is off nothing of it or its image host is left, and other sites stay on',
    afterDisable,
  );
} catch (err) {
  check(results, false, 'run completed', err.message);
  for (const t of (await targets(CDP_PORT)).filter((t) => t.type === 'page' && t.url.includes('pixiv.net'))) {
    await Promise.race([attach(t).then((s) => s.screenshot(`${out}/access-failure.png`)).catch(() => undefined), sleep(3000)]);
  }
} finally {
  children.forEach((c) => c.kill());
  if (!process.env.KEEP) process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
