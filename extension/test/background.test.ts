import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import type { ToBackground, ToContent, Want } from '../src/messages.ts';
import { bundle, fakeChrome } from './fake-chrome.ts';

type Request = { url: string; resolve: (res: unknown) => void; reject: (err: unknown) => void };

const real = { chrome: (globalThis as { chrome?: unknown }).chrome, fetch: globalThis.fetch };
let requests: Request[] = [];
let over = false;
const gone = () => new Response(null, { status: 404 });
const tick = () => new Promise((r) => setTimeout(r, 10));

afterEach(async () => {
  over = true;
  for (const r of requests) r.resolve(gone());
  await tick();
  (globalThis as { chrome?: unknown }).chrome = real.chrome;
  globalThis.fetch = real.fetch;
});

async function worker(granted: string[] = []) {
  const fake = fakeChrome({}, granted);
  requests = [];
  over = false;
  (globalThis as { chrome?: unknown }).chrome = fake.chrome;
  globalThis.fetch = ((url: string | URL) => new Promise((resolve, reject) => (over ? resolve(gone()) : requests.push({ url: String(url), resolve, reject })))) as typeof fetch;
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
  return { connect, requests, requested: () => requests.map((r) => r.url) };
}

const states = (received: ToContent[]) => received.map((m) => (m.type === 'progress' ? m.state : m.type));
const image = () => new Response(new Uint8Array([1]), { headers: { 'content-type': 'image/png' } });
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
    const report = { want: want(local[0] ?? ''), pageUrl: 'https://reader.example/chapter/1', imageUrl: local[0] ?? '', note: '', jobState: 'failed', jobError: null, areas: [] };
    tab.send({ type: 'report', report });
    await tick();
    assert.deepEqual(requested(), []);
    const reported = tab.received.find((m) => m.type === 'reported');
    assert.ok(reported?.type === 'reported' && !reported.outcome.ok);
  });

  it('still fetches images on the web, and images on this computer for a page on this computer', async () => {
    const { connect, requested } = await worker();
    connect('https://reader.example/chapter/1').send({ type: 'want', jobs: [want('https://img.example/1.png')] });
    connect('http://127.0.0.1:8000/read').send({ type: 'want', jobs: [want('http://127.0.0.1:8000/2.png')] });
    await tick();
    assert.deepEqual(requested(), ['https://img.example/1.png', 'http://127.0.0.1:8000/2.png']);
  });

  for (const [name, granted] of [['fails', ['https://img.example/*']], ['is blocked', []]] as const) {
    it(`runs a page wanted again while its dropped job's fetch ${name}`, async () => {
      const { connect, requests, requested } = await worker([...granted]);
      const tab = connect('https://reader.example/chapter/1');
      const page = want('https://img.example/1.png');
      tab.send({ type: 'want', jobs: [page] });
      await tick();
      tab.send({ type: 'drop', keys: [page.key] });
      tab.send({ type: 'want', jobs: [page] });
      await tick();
      assert.deepEqual(requested(), [page.url, page.url]);
      requests[0]?.reject(new TypeError('Failed to fetch'));
      await tick();
      requests[1]?.resolve(image());
      await tick();
      assert.deepEqual(states(tab.received), ['queued', 'fetching', 'queued', 'fetching', 'translating']);
      assert.equal(requested()[2], 'http://127.0.0.1:7878/v1/pages');
    });
  }
});
