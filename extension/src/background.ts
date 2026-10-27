import { imageReferers } from './adapters';
import { getServerUrl, parseHealth, parseReportReceipt, parseServerError, parseTranslatedPage, type TranslatedPage } from './contract';
import { noAccessText, PORT_NAME, type Broadcast, type ReportRequest, type RetranslateRequest, type TabCommand, type ToBackground, type ToContent, type Want } from './messages';
import { addImageHost, enableSite, reconcileSites, siteForUrl, sitesForGrants } from './sites';

const MAX_IN_FLIGHT = 2;
const IMAGE_FIRST_BYTE_TIMEOUT_MS = 8_000;
const IMAGE_STALL_TIMEOUT_MS = 8_000;
const MIRROR_HEDGE_MS = 2_000;
const TRANSLATE_TIMEOUT_MS = 180_000;
const RESULT_CACHE_LIMIT = 120;
const ORIGINALS_LIMIT = 40;
const REPORT_TIMEOUT_MS = 30_000;
const SLOW_HOST_PENALTY_MS = 10 * 60_000;
const HEALTH_BACKOFF_MS = [2_000, 4_000, 8_000, 16_000, 30_000];
const HEALTH_TIMEOUT_MS = 5_000;
const SERVER_DOWN_STATUSES = new Set([502, 503, 504]);
const LOOPBACK = /^(localhost|.+\.localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1?\]|\[::ffff:(7f[0-9a-f]{2}|0):[0-9a-f]+\])$/;

type Entry = {
  want: Want;
  state: 'queued' | 'fetching' | 'translating' | 'waiting';
  ports: Set<chrome.runtime.Port>;
};
type Outage = { attempt: number; detail: string };
type Ready = { page: TranslatedPage; mime: string; base64: string; contextKey: string };

class JobError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
  }
}

class ServerDown extends JobError {
  constructor(message: string) {
    super(message, true);
  }
}

class NoAccess extends JobError {
  constructor(readonly host: string) {
    super(noAccessText(host), false);
  }
}

const entries = new Map<string, Entry>();
const results = new Map<string, Ready>();
const originals = new Map<string, Blob>();
const slowHosts = new Map<string, number>();
const ports = new Set<chrome.runtime.Port>();
let inFlight = 0;
let outage: Outage | undefined;
const referersReady = installImageReferers().catch(() => undefined);

async function installImageReferers(): Promise<void> {
  const dnr = chrome.declarativeNetRequest;
  const addRules = imageReferers().map(({ domain, referer }, i) => ({
    id: i + 1,
    priority: 1,
    action: { type: dnr.RuleActionType.MODIFY_HEADERS, requestHeaders: [{ header: 'referer', operation: dnr.HeaderOperation.SET, value: referer }] },
    condition: { requestDomains: [domain], tabIds: [chrome.tabs.TAB_ID_NONE], resourceTypes: [dnr.ResourceType.XMLHTTPREQUEST] },
  }));
  const removeRuleIds = (await dnr.getSessionRules()).map((r) => r.id);
  await dnr.updateSessionRules({ removeRuleIds, addRules }).catch(() => undefined);
}

function send(port: chrome.runtime.Port, msg: ToContent): void {
  try {
    port.postMessage(msg);
  } catch {
    detach(port);
  }
}

function broadcast(entry: Entry, msg: ToContent): void {
  for (const port of entry.ports) send(port, msg);
}

function detach(port: chrome.runtime.Port): void {
  ports.delete(port);
  for (const [key, entry] of entries) {
    entry.ports.delete(port);
    if (entry.ports.size === 0 && (entry.state === 'queued' || entry.state === 'waiting')) entries.delete(key);
  }
}

function sendCached(port: chrome.runtime.Port, key: string): boolean {
  const done = results.get(key);
  if (!done) return false;
  results.delete(key);
  results.set(key, done);
  send(port, { type: 'ready', key, ...done });
  return true;
}

function isLoopback(url: string): boolean {
  try {
    return LOOPBACK.test(new URL(url).hostname.replace(/\.$/, ''));
  } catch {
    return false;
  }
}

function loopbackRefusal(port: chrome.runtime.Port, w: Want): string | undefined {
  if (isLoopback(port.sender?.tab?.url ?? '')) return undefined;
  const url = w.candidates.find(isLoopback);
  return url && `Refused to fetch ${new URL(url).hostname}: a web page cannot load images from this computer.`;
}

function want(port: chrome.runtime.Port, w: Want): void {
  const refusal = loopbackRefusal(port, w);
  if (refusal) return send(port, { type: 'failed', key: w.key, error: refusal, retryable: false });
  if (sendCached(port, w.key)) return;
  const existing = entries.get(w.key);
  if (existing) {
    existing.want = { ...existing.want, priority: w.priority };
    existing.ports.add(port);
    send(port, progress(existing));
    return;
  }
  const entry: Entry = { want: w, state: outage ? 'waiting' : 'queued', ports: new Set([port]) };
  entries.set(w.key, entry);
  send(port, progress(entry));
}

function drop(port: chrome.runtime.Port, key: string): void {
  const entry = entries.get(key);
  if (!entry) return;
  entry.ports.delete(port);
  if (entry.ports.size === 0 && entry.state !== 'translating') entries.delete(key);
}

function progress(entry: Entry): ToContent {
  const { key } = entry.want;
  return entry.state === 'waiting' ? { type: 'progress', key, state: 'waiting', detail: outage?.detail ?? '' } : { type: 'progress', key, state: entry.state };
}

function setState(entry: Entry, state: Entry['state']): void {
  entry.state = state;
  broadcast(entry, progress(entry));
}

function serverDown(detail: string): void {
  const first = !outage;
  outage = { attempt: outage?.attempt ?? 0, detail };
  for (const e of entries.values()) if (e.state === 'queued' || e.state === 'waiting') setState(e, 'waiting');
  if (first) pollHealth();
}

function pollHealth(): void {
  if (!outage) return;
  const delay = HEALTH_BACKOFF_MS[Math.min(outage.attempt, HEALTH_BACKOFF_MS.length - 1)];
  setTimeout(async () => {
    if (!outage) return;
    if (![...entries.values()].some((e) => e.state === 'waiting')) {
      outage = undefined;
      return;
    }
    const problem = await healthProblem();
    if (problem === null) {
      outage = undefined;
      for (const e of entries.values()) if (e.state === 'waiting') setState(e, 'queued');
      pump();
      return;
    }
    outage.attempt++;
    outage.detail = problem;
    pollHealth();
  }, delay);
}

async function healthProblem(): Promise<string | null> {
  try {
    const res = await fetch(`${await getServerUrl()}/v1/health`, { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS) });
    const health = parseHealth(await res.json());
    return health.ok ? null : health.detail;
  } catch {
    return 'Local server not reachable.';
  }
}

function pump(): void {
  while (inFlight < MAX_IN_FLIGHT && !outage) {
    let next: Entry | undefined;
    for (const e of entries.values()) {
      if (e.state === 'queued' && (!next || e.want.priority < next.want.priority)) next = e;
    }
    if (!next) return;
    inFlight++;
    void run(next).finally(() => {
      inFlight--;
      pump();
    });
  }
}

function forget(entry: Entry): void {
  if (entries.get(entry.want.key) === entry) entries.delete(entry.want.key);
}

async function run(entry: Entry): Promise<void> {
  const { key } = entry.want;
  try {
    let image = originals.get(key);
    if (!image) {
      setState(entry, 'fetching');
      image = await fetchImage(entry.want.candidates);
    }
    remember(originals, key, image, ORIGINALS_LIMIT);
    if (entries.get(key) !== entry) return;
    setState(entry, 'translating');
    const ready = await translate(image, entry.want);
    remember(results, key, ready, RESULT_CACHE_LIMIT);
    broadcast(entry, { type: 'ready', key, ...ready });
  } catch (err) {
    if (err instanceof ServerDown && entry.ports.size > 0) {
      entry.state = 'queued';
      return serverDown(err.message);
    }
    if (err instanceof NoAccess) {
      const sites = [...entry.ports].flatMap((p) => siteForUrl(p.sender?.tab?.url ?? '') ?? []);
      await Promise.all(sites.map((s) => addImageHost(s, err.host)));
      broadcast(entry, { type: 'blocked', key, host: err.host });
      forget(entry);
      return;
    }
    const retryable = err instanceof JobError ? err.retryable : true;
    const error = err instanceof Error ? err.message : String(err);
    broadcast(entry, { type: 'failed', key, error, retryable });
  }
  forget(entry);
}

function remember<V>(map: Map<string, V>, key: string, value: V, limit: number): void {
  map.delete(key);
  map.set(key, value);
  while (map.size > limit) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) break;
    map.delete(oldest);
  }
}

function orderByHostHealth(urls: string[]): string[] {
  const now = Date.now();
  const slow = (u: string) => (slowHosts.get(new URL(u).host) ?? 0) > now;
  return [...urls.filter((u) => !slow(u)), ...urls.filter(slow)];
}

async function blockedHost(url: string): Promise<NoAccess | undefined> {
  const { protocol, hostname } = new URL(url);
  const allowed = await chrome.permissions.contains({ origins: [`${protocol}//${hostname}/*`] }).catch(() => true);
  return allowed ? undefined : new NoAccess(hostname);
}

async function fetchImage(candidates: string[]): Promise<Blob> {
  await referersReady;
  const urls = orderByHostHealth(candidates);
  const all = new AbortController();
  return new Promise((resolve, reject) => {
    let started = 0;
    let running = 0;
    let last: JobError | undefined;
    const fail = (err: JobError) => {
      if (!last || err instanceof NoAccess || (err.retryable && !(last instanceof NoAccess))) last = err;
      if (started < urls.length) startNext();
      else if (running === 0) reject(last);
    };
    const startNext = (): void => {
      const url = urls[started++];
      if (!url) return;
      running++;
      const hedge = setTimeout(() => {
        slowHosts.set(new URL(url).host, Date.now() + SLOW_HOST_PENALTY_MS);
        startNext();
      }, MIRROR_HEDGE_MS);
      const own = new AbortController();
      let deadline = setTimeout(() => own.abort(new DOMException('No response from image host.', 'TimeoutError')), IMAGE_FIRST_BYTE_TIMEOUT_MS);
      const stallGuard = () => {
        clearTimeout(deadline);
        deadline = setTimeout(() => own.abort(new DOMException('Image download stalled.', 'TimeoutError')), IMAGE_STALL_TIMEOUT_MS);
      };
      fetch(url, { signal: AbortSignal.any([all.signal, own.signal]), credentials: 'include' })
        .then(async (res) => {
          clearTimeout(hedge);
          stallGuard();
          if (res.status === 404 || res.status === 410) throw new JobError(`Image not found (${res.status}).`, false);
          if (!res.ok) throw new JobError(`Image fetch failed (${res.status}).`, true);
          const chunks: Uint8Array<ArrayBuffer>[] = [];
          const reader = res.body?.getReader();
          for (let r = await reader?.read(); r && !r.done; r = await reader?.read()) {
            chunks.push(r.value);
            stallGuard();
          }
          clearTimeout(deadline);
          all.abort();
          resolve(new Blob(chunks, { type: res.headers.get('content-type') ?? '' }));
        })
        .catch(async (err: unknown) => {
          clearTimeout(hedge);
          clearTimeout(deadline);
          const blocked = all.signal.aborted ? undefined : await blockedHost(url);
          running--;
          if (all.signal.aborted) return;
          fail(blocked ?? (err instanceof JobError ? err : new JobError(`Image fetch failed: ${(err as Error).message}`, true)));
        });
    };
    if (urls.length) startNext();
    else reject(new JobError('No image URL.', false));
  });
}

async function postToServer(server: string, path: string, form: FormData, timeoutMs: number): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${server}${path}`, { method: 'POST', body: form, signal: AbortSignal.timeout(timeoutMs) });
  } catch (err) {
    if ((err as Error).name === 'TimeoutError') throw new JobError('Local server timed out.', true);
    throw new ServerDown('Local server not reachable.');
  }
  if (!res.ok) {
    const { code, detail } = parseServerError(await res.json().catch(() => null));
    if (SERVER_DOWN_STATUSES.has(res.status)) throw new ServerDown(detail);
    throw new JobError(detail, code !== 'bad_image' && code !== 'bad_request');
  }
  return res;
}

async function translate(image: Blob, w: Want): Promise<Ready> {
  const server = await getServerUrl();
  const form = new FormData();
  form.append('image', image, 'page');
  form.append('context_key', w.contextKey);
  form.append('page_index', String(w.pageIndex));
  return readyFrom(server, await postToServer(server, '/v1/pages', form, TRANSLATE_TIMEOUT_MS), w.contextKey);
}

async function readyFrom(server: string, res: Response, contextKey: string): Promise<Ready> {
  let page: TranslatedPage;
  try {
    page = parseTranslatedPage(await res.json());
  } catch (err) {
    throw new JobError(`Bad server response: ${(err as Error).message}`, false);
  }
  const rendered = await fetch(new URL(page.rendered_url, server + '/')).catch(() => {
    throw new ServerDown('Local server not reachable.');
  });
  if (SERVER_DOWN_STATUSES.has(rendered.status)) throw new ServerDown(`Rendered page fetch failed (${rendered.status}).`);
  if (!rendered.ok) throw new JobError(`Rendered page fetch failed (${rendered.status}).`, true);
  const bytes = new Uint8Array(await rendered.arrayBuffer());
  return { page, mime: rendered.headers.get('content-type') ?? 'image/webp', base64: toBase64(bytes), contextKey };
}

async function pageId(image: Blob): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', await image.arrayBuffer()));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function namesChanged(contextKey: string): void {
  for (const [key, done] of results) if (done.contextKey === contextKey) results.delete(key);
  for (const port of ports) send(port, { type: 'stale', contextKey });
}

async function retranslate(port: chrome.runtime.Port, r: RetranslateRequest): Promise<void> {
  const { key, contextKey, pageIndex } = r.want;
  const refusal = loopbackRefusal(port, r.want);
  if (refusal) return send(port, { type: 'retranslated', key, outcome: { ok: false, error: refusal } });
  try {
    const image = originals.get(key) ?? (await fetchImage(r.want.candidates));
    remember(originals, key, image, ORIGINALS_LIMIT);
    const server = await getServerUrl();
    const form = new FormData();
    form.append('image', image, 'page');
    form.append('request', JSON.stringify({ context_key: contextKey, page_index: pageIndex, bubbles: r.bubbles, hint: r.hint, names: r.names }));
    const res = await postToServer(server, `/v1/pages/${await pageId(image)}/retranslate`, form, TRANSLATE_TIMEOUT_MS);
    const ready = await readyFrom(server, res, contextKey);
    if (r.names.length) {
      namesChanged(contextKey);
      chrome.runtime.sendMessage({ type: 'names', contextKey } satisfies Broadcast).catch(() => undefined);
    }
    remember(results, key, ready, RESULT_CACHE_LIMIT);
    send(port, { type: 'ready', key, ...ready });
    send(port, { type: 'retranslated', key, outcome: { ok: true } });
  } catch (err) {
    send(port, { type: 'retranslated', key, outcome: { ok: false, error: err instanceof Error ? err.message : String(err) } });
  }
}

async function report(port: chrome.runtime.Port, r: ReportRequest): Promise<void> {
  const { key } = r.want;
  const refusal = loopbackRefusal(port, r.want);
  if (refusal) return send(port, { type: 'reported', key, outcome: { ok: false, error: refusal } });
  try {
    const image = originals.get(key) ?? (await fetchImage(r.want.candidates));
    remember(originals, key, image, ORIGINALS_LIMIT);
    const form = new FormData();
    form.append('image', image, 'page');
    form.append('page_url', r.pageUrl);
    form.append('image_url', r.imageUrl);
    form.append('context_key', r.want.contextKey);
    form.append('page_index', String(r.want.pageIndex));
    form.append('note', r.note);
    form.append('job_state', r.jobState);
    if (r.jobError) form.append('job_error', r.jobError);
    form.append('areas', JSON.stringify(r.areas.map((x) => ({ box: x.box, bubble_index: x.bubbleIndex }))));
    const res = await postToServer(await getServerUrl(), '/v1/reports', form, REPORT_TIMEOUT_MS);
    const receipt = parseReportReceipt(await res.json());
    send(port, { type: 'reported', key, outcome: { ok: true, ...receipt } });
  } catch (err) {
    send(port, { type: 'reported', key, outcome: { ok: false, error: err instanceof Error ? err.message : String(err) } });
  }
}

function toBase64(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PORT_NAME) return;
  ports.add(port);
  port.onMessage.addListener((msg: ToBackground) => {
    if (msg.type === 'want') {
      for (const w of msg.jobs) want(port, w);
      pump();
    } else if (msg.type === 'lookup') {
      for (const key of msg.keys) sendCached(port, key);
    } else if (msg.type === 'drop') {
      for (const key of msg.keys) drop(port, key);
    } else if (msg.type === 'report') {
      void report(port, msg.report);
    } else if (msg.type === 'retranslate') {
      void retranslate(port, msg.request);
    }
  });
  port.onDisconnect.addListener(() => detach(port));
});

chrome.runtime.onMessage.addListener((msg: Broadcast) => {
  if (msg.type === 'names') namesChanged(msg.contextKey);
});

chrome.runtime.onInstalled.addListener(({ reason }) => void reconcileSites(reason !== 'install'));
chrome.runtime.onStartup.addListener(() => void reconcileSites(true));
chrome.permissions.onRemoved.addListener(() => void reconcileSites());
chrome.permissions.onAdded.addListener(({ origins = [] }) => {
  void sitesForGrants(origins).then((sites) => sites.forEach((s) => void enableSite(s)));
  void chrome.tabs.query({}).then((tabs) => {
    for (const t of tabs) if (t.id !== undefined) chrome.tabs.sendMessage(t.id, { type: 'access' } satisfies TabCommand).catch(() => undefined);
  });
});
