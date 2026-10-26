import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { defaultAdapter, type SiteAdapter } from '../src/adapter.ts';
import { loadWork, parseWork, setWorkChoice, tabMode, watchWorkChoice, workChoice, type Work } from '../src/works.ts';

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
  return { data, listeners, chrome: { storage: { local, onChanged: { addListener: (l: Listener) => listeners.add(l), removeListener: (l: Listener) => listeners.delete(l) } } } };
}

const loc = (href: string) => new URL(href) as unknown as Location;
const work = (over: Partial<Work> = {}): Work => ({ id: 'reader.example/chapter/1', language: undefined, choice: undefined, ...over });
const reader: SiteAdapter = {
  ...defaultAdapter,
  workId: (l) => {
    const n = l.pathname.match(/^\/chapter\/(\d+)/)?.[1];
    return n ? `reader.example/chapter/${n}` : undefined;
  },
  workLanguage: async (l) => (l.search === '?en' ? 'english' : 'japanese'),
};

describe('work choice', () => {
  it('translates a work unless the reader turned it off or it is already in English', () => {
    assert.equal(tabMode('auto', undefined), 'auto');
    assert.equal(tabMode('auto', work()), 'auto');
    assert.equal(tabMode('on-demand', work({ language: 'japanese' })), 'on-demand');
    assert.equal(tabMode('auto', work({ choice: 'off' })), 'off');
    assert.equal(tabMode('auto', work({ language: 'english' })), 'off');
    assert.equal(tabMode('on-demand', work({ language: 'english' })), 'off');
    assert.equal(tabMode('auto', work({ language: 'english', choice: 'on' })), 'auto');
    assert.equal(workChoice(work({ language: 'english' })), 'off');
  });

  it('reads a work sent by the content script and rejects anything else', () => {
    assert.deepEqual(parseWork({ id: 'a/1', language: 'english', choice: 'off' }), { id: 'a/1', language: 'english', choice: 'off' });
    assert.deepEqual(parseWork({ id: 'a/1', language: 3, choice: 'maybe' }), { id: 'a/1', language: undefined, choice: undefined });
    assert.equal(parseWork(null), undefined);
    assert.equal(parseWork({ id: '' }), undefined);
  });
});

describe('work storage', () => {
  let store: ReturnType<typeof fakeStorage>;
  const realChrome = (globalThis as { chrome?: unknown }).chrome;
  beforeEach(() => {
    store = fakeStorage();
    (globalThis as { chrome?: unknown }).chrome = store.chrome;
  });
  afterEach(() => {
    (globalThis as { chrome?: unknown }).chrome = realChrome;
  });

  it('remembers a work turned off and turned back on', async () => {
    await setWorkChoice(work(), 'off');
    assert.deepEqual(store.data, { 'work:reader.example/chapter/1': 'off' });
    await setWorkChoice(work(), 'on');
    assert.deepEqual(store.data, { 'work:reader.example/chapter/1': 'on' });
  });

  it('keeps an English work turned off even when a later language lookup fails', async () => {
    await setWorkChoice(work({ language: 'english' }), 'off');
    const broken: SiteAdapter = { ...reader, workLanguage: () => Promise.reject(new Error('offline')) };
    const later = await loadWork(broken, loc('https://reader.example/chapter/1/2'));
    assert.ok(later);
    assert.equal(workChoice(later), 'off');
  });

  it('loads the work of the address with its language and stored choice', async () => {
    store.data['work:reader.example/chapter/7'] = 'off';
    assert.deepEqual(await loadWork(reader, loc('https://reader.example/chapter/7/2')), { id: 'reader.example/chapter/7', language: 'japanese', choice: 'off' });
    assert.deepEqual(await loadWork(reader, loc('https://reader.example/chapter/8/1?en')), { id: 'reader.example/chapter/8', language: 'english', choice: undefined });
    assert.equal(await loadWork(reader, loc('https://reader.example/')), undefined);
    assert.equal(await loadWork(defaultAdapter, loc('https://reader.example/chapter/7/2')), undefined);
  });

  it('treats a failing language lookup as unknown', async () => {
    const broken: SiteAdapter = { ...reader, workLanguage: () => Promise.reject(new Error('offline')) };
    assert.deepEqual(await loadWork(broken, loc('https://reader.example/chapter/7/2')), { id: 'reader.example/chapter/7', language: undefined, choice: undefined });
  });

  it('tells a watcher about changes to its own work only', async () => {
    const seen: unknown[] = [];
    const stop = watchWorkChoice('reader.example/chapter/1', (c) => seen.push(c));
    await setWorkChoice(work(), 'off');
    await setWorkChoice(work({ id: 'reader.example/chapter/2' }), 'off');
    await setWorkChoice(work(), 'on');
    stop();
    await setWorkChoice(work(), 'off');
    assert.deepEqual(seen, ['off', 'on']);
    assert.equal(store.listeners.size, 0);
  });
});
