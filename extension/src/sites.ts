import { getDomain, getPublicSuffix } from 'tldts';
import { adapterFor, isExtraOrigin, parseSiteMode, type SiteMode } from './adapters';

const SCRIPT_PREFIX = 'site:';
const HOSTS_PREFIX = 'imageHosts:';
const MODES_KEY = 'siteModes';
const SEEDED_KEY = 'sitesSeeded';
const SERVER_ORIGINS = ['http://127.0.0.1/*', 'http://localhost/*'];
const SUFFIXES = { allowPrivateDomains: true };

export type Site = { id: string; label: string; pattern: string; origins: string[] };

export type SiteAccess = { state: 'off' } | { state: 'on' } | { state: 'partial'; missing: string[] };

function site(label: string, pattern: string): Site {
  return { id: SCRIPT_PREFIX + label, label, pattern, origins: [pattern, ...(adapterFor(label).extraOrigins ?? [])] };
}

export function siteForUrl(rawUrl: string): Site | undefined {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return undefined;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return undefined;
  const host = url.hostname;
  const domain = getDomain(host, SUFFIXES);
  if (!domain) return site(host, `${url.protocol}//${host}/*`);
  return site(domain, `*://*.${domain}/*`);
}

export function siteForPattern(pattern: string): Site | undefined {
  const m = pattern.match(/^(\*|https?):\/\/(\*\.)?([^/*]+)\/\*$/);
  if (!m?.[3]) return undefined;
  return site(m[3], pattern);
}

const imageOrigin = (host: string) => `*://${host}/*`;

export function coversHost(pattern: string, host: string): boolean {
  const m = pattern.match(/^(?:\*|https?):\/\/(\*\.)?([^/*]+)\/\*$/);
  return !!m?.[2] && (host === m[2] || (!!m[1] && host.endsWith(`.${m[2]}`)));
}

const hostsKey = (site: Site) => HOSTS_PREFIX + site.label;

const asHosts = (v: unknown): string[] => (Array.isArray(v) ? v.filter((h): h is string => typeof h === 'string') : []);

async function storedImageHosts(): Promise<string[][]> {
  const all = await chrome.storage.local.get(null);
  return Object.entries(all).flatMap(([key, hosts]) => (key.startsWith(HOSTS_PREFIX) ? [asHosts(hosts)] : []));
}

async function siteOrigins(site: Site): Promise<string[]> {
  const key = hostsKey(site);
  const hosts = asHosts((await chrome.storage.local.get(key))[key]);
  return [...new Set([...site.origins, ...hosts.filter((h) => !site.origins.some((o) => coversHost(o, h))).map(imageOrigin)])];
}

let hostWrites = Promise.resolve();

export function addImageHost(site: Site, host: string): Promise<void> {
  hostWrites = hostWrites
    .then(async () => {
      const key = hostsKey(site);
      const stored = await chrome.storage.local.get([site.id, key]);
      const hosts = asHosts(stored[key]);
      if (stored[site.id] === undefined || hosts.includes(host) || site.origins.some((o) => coversHost(o, host))) return;
      await chrome.storage.local.set({ [key]: [...hosts, host] });
    })
    .catch(() => undefined);
  return hostWrites;
}

async function revokeOrigins(site: Site): Promise<void> {
  const origins = await siteOrigins(site);
  await chrome.storage.local.remove(hostsKey(site));
  const kept = new Set((await Promise.all((await storedSites()).map(siteOrigins))).flat());
  await chrome.permissions.remove({ origins: origins.filter((o) => !kept.has(o)) }).catch(() => false);
}

export async function sitesForGrants(origins: string[]): Promise<Site[]> {
  const images = new Set((await storedImageHosts()).flat().map(imageOrigin));
  return origins.flatMap((o) => (SERVER_ORIGINS.includes(o) || isExtraOrigin(o) || images.has(o) ? [] : (siteForPattern(o) ?? [])));
}

const asModes = (v: unknown): Record<string, unknown> => (typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {});

async function storedModes(): Promise<Record<string, unknown>> {
  return asModes((await chrome.storage.local.get(MODES_KEY))[MODES_KEY]);
}

const modeOf = (site: Site, modes: Record<string, unknown>): SiteMode => parseSiteMode(modes[site.label]) ?? adapterFor(site.label).defaultMode;

export async function getSiteMode(site: Site): Promise<SiteMode> {
  return modeOf(site, await storedModes());
}

export async function setSiteMode(site: Site, mode: SiteMode): Promise<void> {
  await chrome.storage.local.set({ [MODES_KEY]: { ...(await storedModes()), [site.label]: mode } });
}

export function watchSiteMode(site: Site, onChange: (mode: SiteMode) => void): () => void {
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
    const change = changes[MODES_KEY];
    if (area !== 'local' || !change) return;
    onChange(modeOf(site, asModes(change.newValue)));
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

export async function isSiteEnabled(site: Site): Promise<boolean> {
  const scripts = await chrome.scripting.getRegisteredContentScripts({ ids: [site.id] });
  return scripts.length > 0;
}

export async function siteAccess(site: Site): Promise<SiteAccess> {
  if (!(await isSiteEnabled(site))) return { state: 'off' };
  const origins = await siteOrigins(site);
  const granted = await Promise.all(origins.map((o) => chrome.permissions.contains({ origins: [o] })));
  const missing = origins.filter((_, i) => !granted[i]);
  return missing.length ? { state: 'partial', missing } : { state: 'on' };
}

export async function enableSite(site: Site): Promise<void> {
  if (!(await isSiteEnabled(site))) {
    await chrome.scripting
      .registerContentScripts([{ id: site.id, matches: [site.pattern], js: ['content.js'], runAt: 'document_idle', persistAcrossSessions: true }])
      .catch(async (err) => {
        if (!(await isSiteEnabled(site))) throw err;
      });
  }
  await chrome.storage.local.set({ [site.id]: site.pattern });
  const tabs = await chrome.tabs.query({ url: site.pattern });
  await Promise.all(
    tabs.map((t) => t.id !== undefined && chrome.scripting.executeScript({ target: { tabId: t.id }, files: ['content.js'] }).catch(() => undefined)),
  );
}

export async function disableSite(site: Site): Promise<void> {
  await chrome.storage.local.remove(site.id);
  if (await isSiteEnabled(site)) await chrome.scripting.unregisterContentScripts({ ids: [site.id] });
  await revokeOrigins(site);
}

const coversPublicSuffix = (site: Site) => site.pattern.startsWith('*://*.') && getPublicSuffix(site.label, SUFFIXES) === site.label;

async function storedSites(): Promise<Site[]> {
  const all = await chrome.storage.local.get(null);
  return Object.entries(all).flatMap(([key, pattern]) => {
    const stored = key.startsWith(SCRIPT_PREFIX) && typeof pattern === 'string' ? siteForPattern(pattern) : undefined;
    return stored?.id === key ? [stored] : [];
  });
}

async function seedFromGrants(): Promise<void> {
  if ((await chrome.storage.local.get(SEEDED_KEY))[SEEDED_KEY]) return;
  const { origins = [] } = await chrome.permissions.getAll();
  const granted = await sitesForGrants(origins);
  await chrome.storage.local.set(Object.fromEntries(granted.map((s) => [s.id, s.pattern])));
}

export async function reconcileSites(recoverLegacy = false): Promise<void> {
  if (recoverLegacy) await seedFromGrants();
  await chrome.storage.local.set({ [SEEDED_KEY]: true });
  for (const s of await storedSites()) {
    if (coversPublicSuffix(s)) await disableSite(s);
    else if (!(await chrome.permissions.contains({ origins: [s.pattern] }))) {
      await chrome.storage.local.remove(s.id);
      await revokeOrigins(s);
    } else if (!(await isSiteEnabled(s))) await enableSite(s);
  }
  const scripts = await chrome.scripting.getRegisteredContentScripts();
  const stale: string[] = [];
  for (const s of scripts) {
    if (!s.id.startsWith(SCRIPT_PREFIX)) continue;
    const granted = await chrome.permissions.contains({ origins: s.matches ?? [] });
    if (!granted) stale.push(s.id);
  }
  if (stale.length) await chrome.scripting.unregisterContentScripts({ ids: stale });
}
