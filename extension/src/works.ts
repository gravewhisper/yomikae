import type { SiteAdapter, SiteMode } from './adapter.ts';

const WORK_PREFIX = 'work:';
const LANGUAGE_TIMEOUT_MS = 3000;

export const WORK_CHOICES = ['on', 'off'] as const;
export type WorkChoice = (typeof WORK_CHOICES)[number];
export const parseWorkChoice = (v: unknown): WorkChoice | undefined => WORK_CHOICES.find((c) => c === v);

export type Work = { id: string; language: string | undefined; choice: WorkChoice | undefined };
export type TabMode = SiteMode | 'off';

export const defaultChoice = (language: string | undefined): WorkChoice => (language === 'english' ? 'off' : 'on');

export const workChoice = (work: Work): WorkChoice => work.choice ?? defaultChoice(work.language);

export const tabMode = (mode: SiteMode, work: Work | undefined): TabMode => (work && workChoice(work) === 'off' ? 'off' : mode);

export function parseWork(v: unknown): Work | undefined {
  const o = v as Partial<Record<keyof Work, unknown>> | null | undefined;
  if (typeof o?.id !== 'string' || !o.id) return undefined;
  return { id: o.id, language: typeof o.language === 'string' ? o.language : undefined, choice: parseWorkChoice(o.choice) };
}

const workKey = (id: string) => WORK_PREFIX + id;

const within = <T>(pending: Promise<T> | undefined, ms: number): Promise<T | undefined> =>
  Promise.race([(pending ?? Promise.resolve(undefined)).catch(() => undefined), new Promise<undefined>((done) => setTimeout(done, ms, undefined))]);

export async function loadWork(adapter: SiteAdapter, loc: Location): Promise<Work | undefined> {
  const id = adapter.workId?.(loc);
  if (!id) return undefined;
  const key = workKey(id);
  const [stored, language] = await Promise.all([chrome.storage.local.get(key), within(adapter.workLanguage?.(loc), LANGUAGE_TIMEOUT_MS)]);
  return { id, language, choice: parseWorkChoice(stored[key]) };
}

export async function setWorkChoice(work: Work, choice: WorkChoice): Promise<void> {
  const key = workKey(work.id);
  if (choice === defaultChoice(work.language)) await chrome.storage.local.remove(key);
  else await chrome.storage.local.set({ [key]: choice });
}

export function watchWorkChoice(id: string, onChange: (choice: WorkChoice | undefined) => void): () => void {
  const key = workKey(id);
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
    const change = changes[key];
    if (area === 'local' && change) onChange(parseWorkChoice(change.newValue));
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}
