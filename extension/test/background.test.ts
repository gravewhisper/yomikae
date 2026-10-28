import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import type { ToBackground, ToContent, Want } from '../src/messages.ts';
import { bundle, fakeChrome } from './fake-chrome.ts';

type Request = { url: string; init?: RequestInit; resolve: (res: unknown) => void; reject: (err: unknown) => void };

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
  globalThis.fetch = ((url: string | URL, init?: RequestInit) => new Promise((resolve, reject) => (over ? resolve(gone()) : requests.push({ url: String(url), init, resolve, reject })))) as typeof fetch;
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
  return { connect, fake, requests, requested: () => requests.map((r) => r.url) };
}

const states = (received: ToContent[]) => received.map((m) => (m.type === 'progress' ? m.state : m.type));
const image = () => new Response(new Uint8Array([1]), { headers: { 'content-type': 'image/png' } });
const want = (url: string): Want => ({ key: url, url, candidates: [url], contextKey: 'c', pageIndex: 1, position: 1, priority: 0 });

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

  it('refuses images on the local network for a page on the web', async () => {
    const { connect, requested } = await worker();
    const tab = connect('https://reader.example/chapter/1');
    const lan = [
      'http://10.0.0.5/a.png',
      'http://172.20.1.1/a.png',
      'http://192.168.1.1/cgi-bin/cam.jpg',
      'http://169.254.169.254/a.png',
      'http://100.100.1.2/a.png',
      'http://[fd00::1]/a.png',
      'http://[fe80::1]/a.png',
      'http://[::ffff:192.168.1.1]/a.png',
      'http://3232235777/a.png',
      'http://nas/a.png',
      'http://printer.local/a.png',
      'http://router.lan/a.png',
      'http://box.home.arpa/a.png',
    ];
    tab.send({ type: 'want', jobs: lan.map(want) });
    await tick();
    assert.deepEqual(requested(), []);
    for (const url of lan) {
      const failed = tab.received.find((m) => m.key === url && m.type === 'failed');
      assert.ok(failed?.type === 'failed' && !failed.retryable && failed.error.includes('local network'), url);
    }
  });

  it('fetches local network images for a page on the local network, but not images on this computer', async () => {
    const { connect, requested } = await worker();
    const tab = connect('http://192.168.1.10:25600/book/1');
    tab.send({ type: 'want', jobs: [want('http://192.168.1.10:25600/page/1.jpg'), want('http://172.32.0.1/a.png'), want('http://127.0.0.1:7000/a.png')] });
    await tick();
    assert.deepEqual(requested(), ['http://192.168.1.10:25600/page/1.jpg', 'http://172.32.0.1/a.png']);
    const failed = tab.received.find((m) => m.key === 'http://127.0.0.1:7000/a.png' && m.type === 'failed');
    assert.ok(failed?.type === 'failed' && failed.error.includes('this computer'));
  });

  it("fetches any image for the extension's own pages", async () => {
    const { connect, requested } = await worker();
    connect('chrome-extension://fakeextensionid/popup.html?tab=3').send({ type: 'want', jobs: [want('http://127.0.0.1:8000/1.png'), want('http://192.168.1.4/2.png')] });
    await tick();
    assert.deepEqual(requested(), ['http://127.0.0.1:8000/1.png', 'http://192.168.1.4/2.png']);
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
  it('refuses an image that redirects closer to the user than the URL it started from', async () => {
    const { connect, requests, requested } = await worker(['https://img.example/*']);
    const tab = connect('https://reader.example/chapter/1');
    const pages = [want('https://img.example/1.png'), want('https://img.example/2.png'), want('https://img.example/3.png')];
    tab.send({ type: 'want', jobs: pages });
    await tick();
    const moved = (url: string) => Object.defineProperty(image(), 'url', { value: url });
    requests[0]?.resolve(moved('http://127.0.0.1:8080/cam.png'));
    requests[1]?.resolve(moved('http://192.168.1.1/cgi-bin/snapshot.jpg'));
    await tick();
    for (const [page, host, words] of [[pages[0], '127.0.0.1', 'this computer'], [pages[1], '192.168.1.1', 'your local network']] as const) {
      const failed = tab.received.find((m) => m.key === page?.key && m.type === 'failed');
      assert.ok(failed?.type === 'failed' && !failed.retryable && failed.error.includes(host) && failed.error.includes(words), JSON.stringify(failed));
    }
    assert.ok(!requested().includes('http://127.0.0.1:7878/v1/pages'));
    requests[2]?.resolve(moved('https://cdn.example/3.png'));
    await tick();
    assert.deepEqual(requested().slice(3), ['http://127.0.0.1:7878/v1/pages']);
  });

  it('follows a redirect that stays on the local network for a page on the local network', async () => {
    const { connect, requests, requested } = await worker();
    const tab = connect('http://192.168.1.10:25600/book/1');
    tab.send({ type: 'want', jobs: [want('http://192.168.1.10:25600/page/1.jpg')] });
    await tick();
    requests[0]?.resolve(Object.defineProperty(image(), 'url', { value: 'http://nas.local/cache/1.jpg' }));
    await tick();
    assert.deepEqual(requested().slice(1), ['http://127.0.0.1:7878/v1/pages']);
  });

  it('stops reading an image larger than the server accepts and fails it without Retry', async () => {
    const { connect, requests, requested } = await worker(['https://img.example/*']);
    const tab = connect('https://reader.example/chapter/1');
    const declared = want('https://img.example/declared.png');
    const streamed = want('https://img.example/streamed.png');
    tab.send({ type: 'want', jobs: [declared, streamed] });
    await tick();
    const mb = 1024 * 1024;
    let pulled = 0;
    const endless = new ReadableStream<Uint8Array>({ pull: (c) => void (pulled++ < 200 ? c.enqueue(new Uint8Array(mb)) : c.close()) });
    requests[0]?.resolve(new Response(new Uint8Array([1]), { headers: { 'content-type': 'image/png', 'content-length': String(65 * mb) } }));
    requests[1]?.resolve(new Response(endless, { headers: { 'content-type': 'image/png' } }));
    for (let i = 0; i < 20 && tab.received.filter((m) => m.type === 'failed').length < 2; i++) await tick();
    for (const page of [declared, streamed]) {
      const failed = tab.received.find((m) => m.key === page.key && m.type === 'failed');
      assert.ok(failed?.type === 'failed' && !failed.retryable && failed.error.includes('64 MB'), JSON.stringify(failed));
    }
    assert.ok(pulled <= 66, `read ${pulled} MB`);
    assert.deepEqual(requested(), [declared.url, streamed.url]);
  });

  it('fails a page the server refuses as forbidden, without Retry', async () => {
    const { connect, requests } = await worker(['https://img.example/*']);
    const tab = connect('https://reader.example/chapter/1');
    const page = want('https://img.example/1.png');
    tab.send({ type: 'want', jobs: [page] });
    await tick();
    requests[0]?.resolve(image());
    await tick();
    const detail = 'requests from chrome-extension://other are refused: only the Yomikae extension may call this server';
    requests[1]?.resolve(new Response(JSON.stringify({ error: 'forbidden', detail }), { status: 403 }));
    await tick();
    assert.deepEqual(tab.received.at(-1), { type: 'failed', key: page.key, error: detail, retryable: false });
  });

  it('fails without Retry when the server refuses the rendered page as forbidden', async () => {
    const { connect, requests, requested } = await worker(['https://img.example/*']);
    const tab = connect('https://reader.example/chapter/1');
    const page = want('https://img.example/1.png');
    tab.send({ type: 'want', jobs: [page] });
    await tick();
    requests[0]?.resolve(image());
    await tick();
    const translated = { page_id: 'p', width: 1, height: 1, rendered_url: '/v1/pages/p/rendered.webp', bubbles: [], timings_ms: {}, cached: false };
    requests[1]?.resolve(new Response(JSON.stringify(translated)));
    await tick();
    assert.equal(requested()[2], 'http://127.0.0.1:7878/v1/pages/p/rendered.webp');
    const detail = 'a cross-site request from a web page is refused: only the Yomikae extension may call this server';
    requests[2]?.resolve(new Response(JSON.stringify({ error: 'forbidden', detail }), { status: 403 }));
    await tick();
    const failed = tab.received.at(-1);
    assert.ok(failed?.type === 'failed' && !failed.retryable && failed.error.includes(detail), JSON.stringify(failed));
  });
});

describe('page order', () => {
  it('sends page_index only when the site adapter knows it', async () => {
    const { connect, requests } = await worker(['https://img.example/*']);
    const tab = connect('https://reader.example/chapter/1');
    const { pageIndex: _, ...unknown } = want('https://img.example/2.png');
    tab.send({ type: 'want', jobs: [want('https://img.example/1.png'), { ...unknown, position: 2, priority: 1 }] });
    await tick();
    requests[0]?.resolve(image());
    await tick();
    requests[1]?.resolve(image());
    await tick();
    const [known, missing] = [requests[2], requests[3]].map((r) => r?.init?.body as FormData);
    assert.equal(known?.get('page_index'), '1');
    assert.equal(missing?.get('context_key'), 'c');
    assert.equal(missing?.has('page_index'), false);
  });
});

const pageJson = (renderedUrl: string, cached = false) =>
  new Response(JSON.stringify({ page_id: 'p', width: 10, height: 10, rendered_url: renderedUrl, bubbles: [{ box: [0, 0, 5, 5], source: 'あ', translation: 'A', vertical: true }], timings_ms: {}, cached }), {
    headers: { 'content-type': 'application/json' },
  });
const rendered = () => new Response(new Uint8Array([2]), { headers: { 'content-type': 'image/webp' } });

describe('retranslating and stale pages', () => {
  async function translated() {
    const w = await worker(['https://img.example/*']);
    const tab = w.connect('https://reader.example/chapter/1');
    const other = w.connect('https://reader.example/chapter/1');
    const page = want('https://img.example/1.png');
    tab.send({ type: 'want', jobs: [page] });
    await tick();
    w.requests[0]?.resolve(image());
    await tick();
    w.requests[1]?.resolve(pageJson('/v1/pages/p/rendered.webp'));
    await tick();
    w.requests[2]?.resolve(rendered());
    await tick();
    assert.equal(tab.received.at(-1)?.type, 'ready');
    return { ...w, tab, other, page };
  }

  it('posts the same bytes and the picked bubbles, then swaps in the new render', async () => {
    const { tab, other, page, requests, requested, fake } = await translated();
    const names = [{ ja: '在藤', en: 'Arifuji', kind: 'name' as const }];
    tab.send({ type: 'retranslate', request: { want: page, bubbles: [0], hint: 'Lene is a girl', names } });
    await tick();
    const sha = Buffer.from(await crypto.subtle.digest('SHA-256', new Uint8Array([1]))).toString('hex');
    assert.equal(requested()[3], `http://127.0.0.1:7878/v1/pages/${sha}/retranslate`);
    const form = requests[3]?.init?.body as FormData;
    assert.deepEqual(new Uint8Array(await (form.get('image') as Blob).arrayBuffer()), new Uint8Array([1]));
    assert.deepEqual(JSON.parse(String(form.get('request'))), { context_key: 'c', page_index: 1, bubbles: [0], hint: 'Lene is a girl', names });
    requests[3]?.resolve(pageJson('/v1/pages/p/rendered.webp?v=1'));
    await tick();
    assert.equal(requested()[4], 'http://127.0.0.1:7878/v1/pages/p/rendered.webp?v=1');
    requests[4]?.resolve(rendered());
    await tick();
    assert.deepEqual(states(tab.received).slice(-3), ['stale', 'ready', 'retranslated']);
    const last = tab.received.at(-1);
    assert.ok(last?.type === 'retranslated' && last.outcome.ok);
    assert.deepEqual(other.received, [{ type: 'stale', contextKey: 'c' }]);
    assert.deepEqual(fake.sent, [{ type: 'names', contextKey: 'c' }]);
    other.send({ type: 'want', jobs: [page] });
    await tick();
    assert.equal(requested().length, 5);
    const served = other.received.at(-1);
    assert.ok(served?.type === 'ready' && served.page.rendered_url.endsWith('?v=1'));
  });

  it('marks a work stale when the side panel changes its names, so the next want asks the server again', async () => {
    const { tab, other, page, requested, fake } = await translated();
    const panel = fake.listeners.message as (msg: unknown) => unknown;
    panel({ type: 'names', contextKey: 'another work' });
    other.send({ type: 'want', jobs: [page] });
    await tick();
    assert.equal(other.received.at(-1)?.type, 'ready');
    assert.equal(requested().length, 3);
    panel({ type: 'names', contextKey: 'c' });
    assert.deepEqual(tab.received.at(-1), { type: 'stale', contextKey: 'c' });
    assert.deepEqual(other.received.at(-1), { type: 'stale', contextKey: 'c' });
    tab.send({ type: 'want', jobs: [page] });
    await tick();
    assert.deepEqual(requested().slice(3), ['http://127.0.0.1:7878/v1/pages']);
  });

  it('keeps the old render and says why when a retranslate fails', async () => {
    const { tab, other, page, requests } = await translated();
    tab.send({ type: 'retranslate', request: { want: page, bubbles: [7], hint: '', names: [] } });
    await tick();
    requests[3]?.resolve(new Response(JSON.stringify({ error: 'bad_request', detail: 'bubble 7 is out of range' }), { status: 400 }));
    await tick();
    assert.deepEqual(tab.received.at(-1), { type: 'retranslated', key: page.key, outcome: { ok: false, error: 'bubble 7 is out of range' } });
    assert.deepEqual(other.received, []);
    other.send({ type: 'want', jobs: [page] });
    await tick();
    assert.equal(other.received.at(-1)?.type, 'ready');
  });
});
