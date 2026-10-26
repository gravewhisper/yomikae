import { execFileSync, spawn } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { attach, check, extensionWorker, launchChromium, openTab, sleep } from './cdp.mjs';

const out = resolve(process.argv[2] ?? 'e2e-out');
const CDP_PORT = 9471;
const MOCK_PORT = 7896;
const SITE_PORT = 8796;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const HOSTS = ['alpha.test', 'beta.test'];
const PATTERNS = HOSTS.map((h) => `*://*.${h}/*`);
const IDS = HOSTS.map((h) => `site:${h}`);
const SERVER_IDS = ['site:127.0.0.1', 'site:localhost'];
const results = [];
const children = [];

for (const port of [MOCK_PORT, SITE_PORT]) {
  if (await fetch(`http://127.0.0.1:${port}/`).then(() => true, (e) => e.cause?.code !== 'ECONNREFUSED')) throw new Error(`port ${port} is already in use`);
}
await rm(`${out}/profile-reload`, { recursive: true, force: true });
await mkdir(out, { recursive: true });
execFileSync(process.execPath, ['build.mjs'], { env: { ...process.env, ML_OUTDIR: 'dist-reload', ML_EXTRA_HOSTS: PATTERNS.join(',') }, stdio: 'inherit' });

const startNode = (script, env) => children.push(spawn(process.execPath, [new URL(script, import.meta.url).pathname], { env: { ...process.env, ...env }, stdio: 'inherit' }));
startNode('./mock-server.mjs', { PORT: String(MOCK_PORT), MOCK_DELAY_MS: '200' });
startNode('./test-site.mjs', { PORT: String(SITE_PORT), PAGES: '2' });
const chromium = await launchChromium({
  profile: `${out}/profile-reload`,
  port: CDP_PORT,
  extension: resolve('dist-reload'),
  args: [...(process.env.HEADED ? [] : ['--headless=new']), `--host-resolver-rules=${HOSTS.map((h) => `MAP ${h} 127.0.0.1:${SITE_PORT}`).join(', ')}`],
});
await sleep(800);

const loaded = `document.readyState === 'complete' && [...document.images].every(i => i.complete && i.naturalWidth > 0)`;
const translated = `[...document.querySelectorAll('img.page')].every(i => i.src.startsWith('blob:'))`;
const sameIds = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());

async function open(url, ready = loaded) {
  const page = await attach(await openTab(CDP_PORT, url));
  await page.send('Page.enable');
  await page.waitFor(ready, 20000);
  return page;
}

try {
  const { session: sw, extId } = await extensionWorker(CDP_PORT, 'Yomikae');
  await sw.evaluate(`chrome.storage.local.set({ serverUrl: '${MOCK}' })`);
  const extensions = await attach(await openTab(CDP_PORT, 'chrome://extensions/'));
  await extensions.waitFor(`typeof chrome.developerPrivate?.reload === 'function'`);
  await extensions.evaluate(`chrome.developerPrivate.updateProfileConfiguration({ inDeveloperMode: true })`);
  const grant = (host) => extensions.evaluate(`chrome.developerPrivate.addHostPermission('${extId}', '${host}')`);
  const revoke = (host) => extensions.evaluate(`chrome.developerPrivate.removeHostPermission('${extId}', '${host}')`);
  await extensions.evaluate(`chrome.developerPrivate.updateExtensionConfiguration({ extensionId: '${extId}', hostAccess: 'ON_SPECIFIC_SITES' })`);
  await grant('http://127.0.0.1/*');
  await grant('http://localhost/*');
  await grant(PATTERNS[0]);
  await grant(PATTERNS[1]);
  const alpha = await open(`http://alpha.test/`);
  await sleep(500);

  const page = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html`));
  await page.waitFor(`document.readyState === 'complete'`);
  const registered = () => page.evaluate(`chrome.scripting.getRegisteredContentScripts().then((s) => s.map((x) => x.id))`);
  const before = await registered();
  check(results, IDS.every((id) => before.includes(id)), 'granting two sites registers both', before);
  check(results, !before.some((id) => SERVER_IDS.includes(id)), 'granting the server hosts makes no site of them', before);

  const reload = async () => {
    page.close();
    await extensions.evaluate(`chrome.developerPrivate.reload('${extId}', { failQuietly: true })`);
    await sleep(1500);
    const next = await attach(await openTab(CDP_PORT, `chrome-extension://${extId}/popup.html`));
    await next.waitFor(`document.readyState === 'complete'`);
    return next;
  };
  const settle = async (p, want) => {
    const ids = `chrome.scripting.getRegisteredContentScripts().then((s) => s.map((x) => x.id).sort())`;
    await p.waitFor(`${ids}.then((ids) => JSON.stringify(ids) === ${JSON.stringify(JSON.stringify([...want].sort()))})`, 5000).catch(() => undefined);
    return p.evaluate(ids);
  };

  let ext = await reload();
  const after = await settle(ext, IDS);
  check(results, sameIds(after, IDS), 'both sites are registered again after the extension reloads', after);
  check(results, !after.some((id) => SERVER_IDS.includes(id)), 'no site exists for the server hosts after the reload', after);
  await ext.evaluate(`chrome.storage.local.set({ siteModes: { 'alpha.test': 'auto', 'beta.test': 'auto' } })`);
  await alpha.send('Page.bringToFront');
  const openWorks = await alpha.waitFor(translated, 20000).then(() => true, () => false);
  check(results, openWorks, 'a tab open across the reload translates without the popup');
  await alpha.screenshot(`${out}/reload-open-tab.png`);
  const fresh = await open(`http://beta.test/`);
  const freshWorks = await fresh.waitFor(translated, 20000).then(() => true, () => false);
  check(results, freshWorks, 'a tab opened after the reload translates without the popup');
  fresh.close();

  const stored = `chrome.storage.local.get(null).then((all) => Object.keys(all).filter((k) => k.startsWith('site:')).sort())`;
  const keys = await ext.evaluate(stored);
  await ext.evaluate(`chrome.storage.local.get(null).then((all) => chrome.storage.local.remove(Object.keys(all).filter((k) => k !== 'serverUrl' && k !== 'siteModes')))`);
  ext = await reload();
  const migrated = await settle(ext, IDS);
  check(results, keys.length > 0 && sameIds(migrated, IDS), 'sites enabled before sites were stored come back from their grants', { keys, migrated });
  check(results, !migrated.some((id) => SERVER_IDS.includes(id)), 'recovering from grants makes no site of the server hosts', migrated);

  await revoke(PATTERNS[1]);
  await sleep(500);
  ext = await reload();
  const revoked = await settle(ext, [IDS[0]]);
  const left = await ext.evaluate(stored);
  check(results, sameIds(revoked, [IDS[0]]) && sameIds(left, [IDS[0]]), 'a site whose grant was revoked stays off and is forgotten', { revoked, left });
  ext = await reload();
  const twice = await settle(ext, [IDS[0]]);
  check(results, sameIds(twice, [IDS[0]]), 'another reload keeps exactly one registration', twice);
} catch (err) {
  check(results, false, 'run completed', err.message);
} finally {
  children.forEach((c) => c.kill());
  if (!process.env.KEEP) process.kill(-chromium.pid);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots in ${out}`);
  process.exit(failed.length ? 1 : 0);
}
