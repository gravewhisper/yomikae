import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { getSourceLangChoice, parseSourceLang, resolveSourceLang, setSourceLangChoice, watchSourceLangChoice, type SourceLang } from '../src/languages.ts';
import type { Site } from '../src/sites.ts';

type Listener = (changes: Record<string, { newValue?: unknown }>, area: string) => void;

function fakeStorage() {
  const data: Record<string, unknown> = {};
  const listeners = new Set<Listener>();
  const notify = (key: string, newValue: unknown) => listeners.forEach((l) => l({ [key]: { newValue } }, 'local'));
  const local = {
    get: async (key: string) => (key in data ? { [key]: data[key] } : {}),
    set: async (items: Record<string, unknown>) => {
      for (const [k, v] of Object.entries(items)) {
        data[k] = v;
        notify(k, v);
      }
    },
    remove: async (key: string) => {
      delete data[key];
      notify(key, undefined);
    },
  };
  return { data, chrome: { storage: { local, onChanged: { addListener: (l: Listener) => listeners.add(l), removeListener: (l: Listener) => listeners.delete(l) } } } };
}

const site = (label: string): Site => ({ id: `site:${label}`, label, pattern: `*://*.${label}/*`, origins: [] });

describe('source language', () => {
  it("takes the reader's choice first, then the work's language, then the page's lang, then Japanese", () => {
    assert.equal(resolveSourceLang('ja', 'korean', 'ko'), 'ja');
    assert.equal(resolveSourceLang('ko', 'japanese', 'ja'), 'ko');
    assert.equal(resolveSourceLang(undefined, 'korean', 'ja'), 'ko');
    assert.equal(resolveSourceLang(undefined, 'japanese', 'ko'), 'ja');
    assert.equal(resolveSourceLang(undefined, 'english', 'ko-KR'), 'ko');
    assert.equal(resolveSourceLang(undefined, undefined, 'KO'), 'ko');
    assert.equal(resolveSourceLang(undefined, undefined, 'en'), 'ja');
    assert.equal(resolveSourceLang(undefined, 'chinese', ''), 'ja');
    assert.equal(resolveSourceLang(undefined, 'constructor', 'kok'), 'ja');
  });

  it('accepts only the languages the server reads', () => {
    assert.equal(parseSourceLang('ko'), 'ko');
    assert.equal(parseSourceLang('zh'), undefined);
    assert.equal(parseSourceLang(undefined), undefined);
  });
});

describe('source language choice', () => {
  let store: ReturnType<typeof fakeStorage>;
  beforeEach(() => {
    store = fakeStorage();
    (globalThis as { chrome?: unknown }).chrome = store.chrome;
  });
  afterEach(() => delete (globalThis as { chrome?: unknown }).chrome);

  it('keeps one key per site, and Auto removes it', async () => {
    const seen: (SourceLang | undefined)[] = [];
    const stop = watchSourceLangChoice(site('webtoons.example'), (c) => seen.push(c));
    await setSourceLangChoice(site('webtoons.example'), 'ko');
    await setSourceLangChoice(site('other.example'), 'ja');
    assert.deepEqual(store.data, { 'sourceLang:webtoons.example': 'ko', 'sourceLang:other.example': 'ja' });
    assert.equal(await getSourceLangChoice(site('webtoons.example')), 'ko');
    await setSourceLangChoice(site('webtoons.example'), undefined);
    assert.equal(await getSourceLangChoice(site('webtoons.example')), undefined);
    stop();
    await setSourceLangChoice(site('webtoons.example'), 'ja');
    assert.deepEqual(seen, ['ko', undefined]);
  });
});
