import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import type { ToBackground, ToContent, Want } from '../src/messages.ts';
import { bundle, fakeChrome } from './fake-chrome.ts';

type Request = { url: string; resolve: (res: unknown) => void; reject: (err: unknown) => void };

const real = { chrome: (globalThis as { chrome?: unknown }).chrome, fetch: globalThis.fetch };
let requests: Request[] = [];
let over = false;
const tick = () => new Promise((r) => setTimeout(r, 10));

afterEach(async () => {
  over = true;
  for (const r of requests) r.reject(new TypeError('test over'));
  await tick();
  (globalThis as { chrome?: unknown }).chrome = real.chrome;
  globalThis.fetch = real.fetch;
});

async function worker() {
  const fake = fakeChrome();
  requests = [];
  over = false;
  (globalThis as { chrome?: unknown }).chrome = fake.chrome;
  globalThis.fetch = ((url: string | URL) => new Promise((resolve, reject) => (over ? reject(new TypeError('test over')) : requests.push({ url: String(url), resolve, reject })))) as typeof fetch;
  await bundle('src/background.ts');
  const connect = (tabUrl: string) => {
    const port = {
      name: 'yomikae',
      sender: { tab: { url: tabUrl } },
      received: [] as ToContent[],
      onMessage: { addListener: (f: (msg: ToBackground) => void) => (port.send = f) },
      onDisconnect: { addListener: () => undefined },
      postMessage: (msg: ToContent) => port.received.push(msg),
      send: (_: ToBackground) => undefined as void,
    };
    (fake.listeners.connect as (p: unknown) => void)(port);
    return port;
  };
  return { connect, requested: () => requests.map((r) => r.url) };
}

const want = (url: string): Want => ({ key: url, url, candidates: [url], contextKey: 'c', pageIndex: 1, priority: 0 });

describe('image fetching', () => {
  it('refuses images on this computer for a page on the web', async () => {
    const { connect, requested } = await worker();
    const tab = connect('https://reader.example/chapter/1');
    const local = ['http://127.0.0.1:8080/admin.png', 'http://localhost:3000/a.png', 'http://[::1]/a.png', 'http://0.0.0.0:9000/a.png', 'http://127.1.2.3/a.png'];
    tab.send({ type: 'want', jobs: local.map(want) });
    await tick();
    assert.deepEqual(requested(), []);
    for (const url of local) {
      const failed = tab.received.find((m) => m.key === url && m.type === 'failed');
      assert.ok(failed?.type === 'failed' && !failed.retryable && failed.error.includes(new URL(url).hostname), url);
    }
  });

  it('still fetches images on the web, and images on this computer for a page on this computer', async () => {
    const { connect, requested } = await worker();
    connect('https://reader.example/chapter/1').send({ type: 'want', jobs: [want('https://img.example/1.png')] });
    connect('http://127.0.0.1:8000/read').send({ type: 'want', jobs: [want('http://127.0.0.1:8000/2.png')] });
    await tick();
    assert.deepEqual(requested(), ['https://img.example/1.png', 'http://127.0.0.1:8000/2.png']);
  });
});
