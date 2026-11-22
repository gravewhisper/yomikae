import { adapterFor, parseSiteMode, type SiteMode } from './adapters';
import type { Site } from './sites';

const MODES_KEY = 'siteModes';

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
