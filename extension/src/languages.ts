import type { Site } from './sites';

const CHOICE_PREFIX = 'sourceLang:';

export const SOURCE_LANGS = ['ja', 'ko'] as const;
export type SourceLang = (typeof SOURCE_LANGS)[number];
export const DEFAULT_SOURCE_LANG: SourceLang = 'ja';
export const parseSourceLang = (v: unknown): SourceLang | undefined => SOURCE_LANGS.find((l) => l === v);

const WORK_LANGUAGES: Record<SourceLang, string> = { ja: 'japanese', ko: 'korean' };

export const sourceLangOfWork = (language: string | undefined): SourceLang | undefined => SOURCE_LANGS.find((l) => WORK_LANGUAGES[l] === language);

export const sourceLangOfTag = (tag: string): SourceLang | undefined => parseSourceLang(tag.trim().toLowerCase().split('-')[0]);

export const resolveSourceLang = (choice: SourceLang | undefined, workLanguage: string | undefined, htmlLang: string): SourceLang =>
  choice ?? sourceLangOfWork(workLanguage) ?? sourceLangOfTag(htmlLang) ?? DEFAULT_SOURCE_LANG;

const choiceKey = (site: Site) => CHOICE_PREFIX + site.label;

export async function getSourceLangChoice(site: Site): Promise<SourceLang | undefined> {
  const key = choiceKey(site);
  return parseSourceLang((await chrome.storage.local.get(key))[key]);
}

export async function setSourceLangChoice(site: Site, choice: SourceLang | undefined): Promise<void> {
  if (choice) await chrome.storage.local.set({ [choiceKey(site)]: choice });
  else await chrome.storage.local.remove(choiceKey(site));
}

export function watchSourceLangChoice(site: Site, onChange: (choice: SourceLang | undefined) => void): () => void {
  const key = choiceKey(site);
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
    const change = changes[key];
    if (area === 'local' && change) onChange(parseSourceLang(change.newValue));
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}
