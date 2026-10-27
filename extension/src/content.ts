import { adapterFor, type SiteMode } from './adapters';
import type { SiteAdapter } from './adapter';
import type { PageJob, Redo, TranslatedPage } from './contract';
import { HINT_MAX, noAccessText, PORT_NAME, type Broadcast, type NameFix, type ReportArea, type TabCommand, type ToBackground, type ToContent, type Want } from './messages';
import { getSiteMode, siteForUrl, watchSiteMode, type Site } from './sites';
import { loadWork, tabMode, watchWorkChoice, type TabMode, type Work } from './works';

type Layer = { el: HTMLElement; original: string; painted?: string; paintedUrl?: string };
type ReadyJob = Extract<PageJob, { state: 'ready' }>;
type PickPurpose = 'report' | 'retranslate';
type Pick = { img: HTMLImageElement; areas: ReportArea[]; purpose: PickPurpose };
type Place = { position: number; pageIndex?: number };

type Tracked = {
  key: string;
  originalSrc: string;
  originalSrcset: string | null;
  sources: Map<HTMLSourceElement, string>;
  layer?: Layer;
};

const MIN_NATURAL = 500;
const MIN_RENDERED_WIDTH = 300;
const MIN_OFFER_PX = 200;
const MIN_OFFER_PAGE_PX = 100;
const PING_MS = 20_000;
const FADE_MS = 220;
const SNAP_REACH = 0.05;
const TAKEOVER = 'yomikae-takeover';

const w = window as Window & { __yomikae?: true };
if (!w.__yomikae) {
  w.__yomikae = true;
  const site = siteForUrl(location.href);
  const adapter = adapterFor(location.hostname);
  if (site) void Promise.all([getSiteMode(site), loadWork(adapter, location)]).then(([mode, work]) => start(site, adapter, mode, work));
}

function start(site: Site, adapter: SiteAdapter, initialMode: SiteMode, initialWork: Work | undefined): void {
  let siteMode = initialMode;
  let work = initialWork;
  let workId = work?.id;
  let mode: TabMode = tabMode(siteMode, work);
  const resolvedMode = (): TabMode => (workId !== work?.id ? 'off' : tabMode(siteMode, work));
  const jobs = new Map<string, PageJob>();
  const wants = new Map<string, Want>();
  const tracked = new WeakMap<HTMLImageElement, Tracked>();
  const elementsByKey = new Map<string, Set<HTMLImageElement>>();
  const fellBack = new Set<string>();
  let port: chrome.runtime.Port | undefined;
  let peeking = false;
  let current: HTMLImageElement | undefined;
  let currentPos = 0;
  let alive = true;
  let pointer: { x: number; y: number } | undefined;
  let pick: Pick | undefined;
  let announced: string | undefined;

  const chips = createChips({ retry, translate, openPick, pickPoint, pickRect, submit: submitPick });
  const say = debugLog();
  say('start', { site: site.id, mode, work, url: location.href });

  function post(msg: ToBackground): void {
    if (!alive) return;
    try {
      port ??= connect();
      port.postMessage(msg);
    } catch {
      if (!chrome.runtime?.id) teardown();
    }
  }

  function connect(): chrome.runtime.Port {
    const p = chrome.runtime.connect({ name: PORT_NAME });
    p.onMessage.addListener((msg: ToContent) => void onMessage(msg));
    p.onDisconnect.addListener(() => {
      port = undefined;
      if (!alive) return;
      if (!chrome.runtime?.id) return teardown();
      setTimeout(resendPending, 250);
    });
    return p;
  }

  function pendingWants(): Want[] {
    return [...wants.values()].filter((x) => {
      const job = jobs.get(x.key);
      const s = job?.state;
      return s === 'queued' || s === 'fetching' || s === 'translating' || s === 'waiting' || (job?.state === 'ready' && job.redo === 'refreshing');
    });
  }

  function resendPending(): void {
    const pending = pendingWants();
    if (pending.length) post({ type: 'want', jobs: pending });
  }

  async function onMessage(msg: ToContent): Promise<void> {
    if (msg.type === 'reported') {
      const o = msg.outcome;
      chips.toast(o.ok ? `Reported · ${o.reportId}` : `Report failed: ${o.error}`, !o.ok);
      return;
    }
    if (msg.type === 'retranslated') {
      const o = msg.outcome;
      if (!o.ok) settle(msg.key, 'retranslating');
      chips.toast(o.ok ? 'Retranslated' : `Retranslate failed: ${o.error}`, !o.ok);
      return;
    }
    if (msg.type === 'stale') return markStale(msg.contextKey);
    const job = jobs.get(msg.key);
    if (job?.state === 'ready' && msg.type !== 'ready') {
      if (msg.type === 'failed' || msg.type === 'blocked') settle(msg.key, 'refreshing');
      return;
    }
    if (msg.type === 'ready') {
      if (job?.state === 'ready' && !job.redo) return;
      const first = firstElement(msg.key);
      const url = job?.url ?? (first && tracked.get(first)?.originalSrc);
      if (!url) return;
      if (job?.state === 'ready' && msg.page.rendered_url === job.page.rendered_url && !msg.page.updated) return settle(msg.key, job.redo);
      const bytes = Uint8Array.from(atob(msg.base64), (c) => c.charCodeAt(0));
      const renderedBlobUrl = URL.createObjectURL(new Blob([bytes], { type: msg.mime }));
      const pre = new Image();
      pre.src = renderedBlobUrl;
      await pre.decode().catch(() => undefined);
      const now = jobs.get(msg.key);
      if (!alive || (now?.state === 'ready' && !now.redo)) return URL.revokeObjectURL(renderedBlobUrl);
      const replaced = now?.state === 'ready' ? now.renderedBlobUrl : undefined;
      jobs.set(msg.key, { state: 'ready', url, page: msg.page, renderedBlobUrl, contextKey: msg.contextKey });
      for (const img of elementsByKey.get(msg.key) ?? []) showTranslated(img, true, replaced);
      if (replaced) setTimeout(() => URL.revokeObjectURL(replaced), FADE_MS + 200);
    } else if (!job) {
      return;
    } else if (msg.type === 'progress') {
      if (msg.state === 'queued') jobs.set(msg.key, { state: 'queued', url: job.url, priority: wants.get(msg.key)?.priority ?? 0 });
      else if (msg.state === 'waiting') jobs.set(msg.key, { state: 'waiting', url: job.url, detail: msg.detail });
      else jobs.set(msg.key, { state: msg.state, url: job.url });
    } else if (msg.type === 'blocked') {
      jobs.set(msg.key, { state: 'blocked', url: job.url, host: msg.host });
    } else if (msg.type === 'failed') {
      jobs.set(msg.key, { state: 'failed', url: job.url, error: msg.error, retryable: msg.retryable });
      if (msg.retryable && adapter.fallback && !fellBack.has(msg.key)) {
        fellBack.add(msg.key);
        const alt = await adapter.fallback(job.url);
        const want = wants.get(msg.key);
        if (alt && alive && want && jobs.get(msg.key)?.state === 'failed') request(msg.key, alt, want, firstElement(msg.key));
      }
    }
    chips.render();
  }

  async function makeWant(key: string, url: string, { position, pageIndex }: Place, img?: HTMLImageElement): Promise<Want> {
    const priority = Math.abs(position - currentPos);
    const contextKey = adapter.contextKey(location, img, url);
    return { key, url, candidates: await adapter.fetchCandidates(url), contextKey, pageIndex, position, priority };
  }

  function request(key: string, url: string, place: Place, img?: HTMLImageElement): void {
    if (mode === 'off') return;
    jobs.set(key, { state: 'queued', url, priority: Math.abs(place.position - currentPos) });
    void makeWant(key, url, place, img).then((want) => {
      if (!alive || jobs.get(key)?.url !== url) return;
      wants.set(key, want);
      post({ type: 'want', jobs: [want] });
    });
  }

  function setRedo(key: string, job: ReadyJob, redo: Redo | undefined): void {
    const { state, url, page, renderedBlobUrl, contextKey } = job;
    jobs.set(key, redo ? { state, url, page, renderedBlobUrl, contextKey, redo } : { state, url, page, renderedBlobUrl, contextKey });
    chips.render();
  }

  function settle(key: string, from: Redo | undefined): void {
    const job = jobs.get(key);
    if (job?.state === 'ready' && job.redo === from) setRedo(key, job, undefined);
  }

  function markStale(contextKey: string): void {
    for (const [key, job] of jobs) if (job.state === 'ready' && job.contextKey === contextKey && !job.redo) setRedo(key, job, 'stale');
    refreshInView();
  }

  function refreshInView(): void {
    for (const [key, set] of elementsByKey) {
      const job = jobs.get(key);
      if (job?.state !== 'ready' || job.redo !== 'stale') continue;
      const img = [...set].find(inView);
      if (!img) continue;
      setRedo(key, job, 'refreshing');
      void makeWant(key, job.url, placeOf(img), img).then((want) => {
        if (!alive) return;
        wants.set(key, want);
        post({ type: 'want', jobs: [want] });
      });
    }
  }

  function currentContext(): string {
    return adapter.contextKey(location, current, current && tracked.get(current)?.originalSrc);
  }

  function announceContext(): void {
    const contextKey = currentContext();
    if (contextKey === announced) return;
    announced = contextKey;
    try {
      chrome.runtime.sendMessage({ type: 'context', contextKey } satisfies Broadcast).catch(() => undefined);
    } catch {}
  }

  function firstElement(key: string): HTMLImageElement | undefined {
    return elementsByKey.get(key)?.values().next().value;
  }

  function translate(img: HTMLImageElement): void {
    const t = tracked.get(img);
    if (!t || jobs.has(t.key)) return;
    request(t.key, t.originalSrc, placeOf(img), img);
    void prefetchFrom(img);
    chips.render();
  }

  function offerable(img: HTMLImageElement): boolean {
    const r = shownRect(img);
    const min = adapter.isPage ? MIN_OFFER_PAGE_PX : MIN_OFFER_PX;
    return r.right - r.left >= min && r.bottom - r.top >= min;
  }

  function imageAtPointer(): HTMLImageElement | undefined {
    if (!pointer) return undefined;
    const { x, y } = pointer;
    const onChip = chips.imageAt(x, y);
    if (onChip?.isConnected) return onChip;
    let hits: Element[] | undefined;
    let best: HTMLImageElement | undefined;
    let bestRank = Infinity;
    const debug = debugging();
    const seen: object[] = [];
    for (const [key, set] of elementsByKey) {
      for (const img of set) {
        const rect = img.getBoundingClientRect();
        if (!img.isConnected || !contains(rect, x, y)) continue;
        const shown = shownRect(img);
        if (debug) seen.push({ key, rect: box(rect), shown: box(shown) });
        if (!contains(shown, x, y)) continue;
        hits ??= document.elementsFromPoint(x, y);
        const at = hits.indexOf(img);
        const rank = at < 0 ? Infinity : at;
        if (debug) seen.push({ key, rank: at });
        if (!best || rank < bestRank) [best, bestRank] = [img, rank];
      }
    }
    if (debug) {
      const under = document.elementsFromPoint(x, y).find((e): e is HTMLImageElement => e instanceof HTMLImageElement);
      const t = under && tracked.get(under);
      say('pointer', { seen, chosen: best && tracked.get(best)?.key, under: under && (t ? t.key : `untracked ${under.currentSrc || under.src}`) }, { x, y });
    }
    return best;
  }

  function openPick(img: HTMLImageElement, purpose: PickPurpose): void {
    if (pick?.img !== img || pick.purpose !== purpose) pick = { img, areas: [], purpose };
    chips.render();
  }

  function closePick(): void {
    pick = undefined;
    chips.render();
  }

  function readyPage(img: HTMLImageElement): TranslatedPage | undefined {
    const job = jobs.get(tracked.get(img)?.key ?? '');
    return job?.state === 'ready' ? job.page : undefined;
  }

  function pickPoint(img: HTMLImageElement, x: number, y: number, add: boolean): void {
    const page = readyPage(img);
    if (pick?.img !== img || !page) return;
    const index = snapBubble(page, x, y);
    if (index === null) return;
    const picked = pick.areas.filter((a) => a.bubbleIndex !== index);
    if (picked.length < pick.areas.length) pick.areas = picked;
    else pick.areas = [...(add ? pick.areas : []), bubbleArea(page, index)];
    chips.render();
  }

  function pickRect(img: HTMLImageElement, box: ReportArea['box']): void {
    if (pick?.img !== img || pick.purpose !== 'report') return;
    pick.areas = [...pick.areas, { box, bubbleIndex: null }];
    chips.render();
  }

  function submitPick(img: HTMLImageElement): void {
    if (pick?.img === img && pick.purpose === 'retranslate') void submitRetranslate(img, pick);
    else void submitReport(img);
  }

  async function submitRetranslate(img: HTMLImageElement, picked: Pick): Promise<void> {
    const t = tracked.get(img);
    const job = t && jobs.get(t.key);
    if (!t || job?.state !== 'ready') return;
    const bubbles = picked.areas.flatMap((a) => (a.bubbleIndex === null ? [] : [a.bubbleIndex]));
    if (!bubbles.length) return chips.toast('Click the bubbles to retranslate first', true);
    const { hint, ja, en } = chips.fixText(img);
    if (ja && !en) return chips.toast('Type the English spelling of the name', true);
    const names: NameFix[] = en ? [{ ja, en, kind: 'name' }] : [];
    closePick();
    setRedo(t.key, job, 'retranslating');
    const want = wants.get(t.key) ?? (await makeWant(t.key, t.originalSrc, placeOf(img), img));
    post({ type: 'retranslate', request: { want, bubbles, hint, names } });
    chips.toast('Retranslating…', false);
  }

  async function submitReport(img: HTMLImageElement): Promise<void> {
    const areas = pick?.img === img ? pick.areas : [];
    const note = chips.noteText(img);
    closePick();
    const t = tracked.get(img);
    if (!t) return;
    const job = jobs.get(t.key);
    const jobError = job?.state === 'failed' ? job.error : job?.state === 'blocked' ? noAccessText(job.host) : null;
    const want = wants.get(t.key) ?? (await makeWant(t.key, t.originalSrc, placeOf(img), img));
    post({ type: 'report', report: { want, pageUrl: location.href, imageUrl: t.originalSrc, note, jobState: job?.state ?? 'untracked', jobError, areas } });
    chips.toast('Sending report…', false);
  }

  function retryBlocked(): void {
    const blocked = [...wants.values()].filter((x) => jobs.get(x.key)?.state === 'blocked');
    for (const x of blocked) jobs.set(x.key, { state: 'queued', url: x.url, priority: x.priority });
    if (blocked.length) post({ type: 'want', jobs: blocked });
    chips.render();
  }

  function retry(img: HTMLImageElement): void {
    const t = tracked.get(img);
    if (!t) return;
    jobs.delete(t.key);
    request(t.key, t.originalSrc, placeOf(img), img);
    chips.render();
  }

  function placeOf(img: HTMLImageElement): Place {
    const t = tracked.get(img);
    const pageIndex = t && adapter.pageIndex(t.originalSrc, img);
    return { pageIndex, position: pageIndex ?? [...document.images].filter((i) => tracked.has(i)).indexOf(img) + 1 };
  }

  function looksLikePage(img: HTMLImageElement): boolean | undefined {
    if (img.getBoundingClientRect().width < MIN_RENDERED_WIDTH) return img.complete ? false : undefined;
    const nw = img.naturalWidth || Number(img.getAttribute('width'));
    const nh = img.naturalHeight || Number(img.getAttribute('height'));
    if (nw && nh) return nw >= MIN_NATURAL && nh >= MIN_NATURAL;
    return img.complete ? false : undefined;
  }

  function looksOfferable(img: HTMLImageElement): boolean | undefined {
    const nw = img.naturalWidth || Number(img.getAttribute('width'));
    const nh = img.naturalHeight || Number(img.getAttribute('height'));
    if (nw && nh) return nw >= MIN_OFFER_PX && nh >= MIN_OFFER_PX;
    return img.complete ? false : undefined;
  }

  function isCandidate(img: HTMLImageElement, src: string): boolean | undefined {
    if (adapter.isPage) return adapter.isPage(src);
    return mode === 'auto' ? looksLikePage(img) : looksOfferable(img);
  }

  function link(img: HTMLImageElement, key: string): void {
    let set = elementsByKey.get(key);
    if (!set) elementsByKey.set(key, (set = new Set()));
    set.add(img);
    io.unobserve(img);
    io.observe(img);
  }

  function track(img: HTMLImageElement, key: string, src: string): void {
    tracked.set(img, { key, originalSrc: src, originalSrcset: null, sources: new Map() });
    link(img, key);
    if (!jobs.has(key)) post({ type: 'lookup', keys: [key] });
  }

  function untrack(img: HTMLImageElement): void {
    const t = tracked.get(img);
    if (!t) return;
    elementsByKey.get(t.key)?.delete(img);
    tracked.delete(img);
    io.unobserve(img);
  }

  function scan(img: HTMLImageElement): void {
    syncWork();
    if (mode === 'off') return;
    const known = tracked.get(img);
    if (known && img.isConnected && !elementsByKey.get(known.key)?.has(img)) link(img, known.key);
    const src = img.getAttribute('src') ? img.src : img.currentSrc;
    if (!src || src.startsWith('blob:') || src.startsWith('data:')) return;
    const key = adapter.pageKey(src);
    const t = tracked.get(img);
    if (t?.key === key) {
      showTranslated(img, false);
      return;
    }
    if (t) untrack(img);
    if (jobs.get(key)?.state === 'ready') {
      track(img, key, src);
      showTranslated(img, false);
      return;
    }
    const verdict = isCandidate(img, src);
    say(`scan ${key}`, { verdict, mode, src, rect: box(img.getBoundingClientRect()), natural: [img.naturalWidth, img.naturalHeight] });
    if (verdict === undefined) img.addEventListener('load', () => scan(img), { once: true });
    if (!verdict) return;
    track(img, key, src);
    chips.render();
  }

  function layerOf(img: HTMLImageElement, t: Tracked): Layer | undefined {
    if (t.layer?.el.parentElement === img.parentElement) return t.layer;
    const el = [...(img.parentElement?.children ?? [])].find(
      (c): c is HTMLElement => c !== img && c instanceof HTMLElement && cssUrls(getComputedStyle(c).backgroundImage).some((u) => adapter.pageKey(u) === t.key),
    );
    t.layer = el && { el, original: el.style.backgroundImage };
    return t.layer;
  }

  function showTranslated(img: HTMLImageElement, fade: boolean, replaced?: string): void {
    const t = tracked.get(img);
    const job = t && jobs.get(t.key);
    if (!t || job?.state !== 'ready' || peeking) return;
    const layer = layerOf(img, t);
    const layerShown = !layer || (layer.el.style.backgroundImage === layer.painted && layer.paintedUrl === job.renderedBlobUrl);
    const imgShown = img.getAttribute('src') === job.renderedBlobUrl && !img.hasAttribute('srcset');
    if (layerShown && imgShown) return;
    if (fade && layer && !layerShown && isUncovered(layer.el)) crossfadeLayer(layer.el);
    else if (fade && !layer && img.complete && img.naturalWidth > 0 && isUncovered(img)) crossfade(img, replaced ?? t.originalSrc);
    if (layer && !layerShown) {
      if (layer.el.style.backgroundImage !== layer.painted) layer.original = layer.el.style.backgroundImage;
      layer.el.style.backgroundImage = `url("${job.renderedBlobUrl}")`;
      layer.painted = layer.el.style.backgroundImage;
      layer.paintedUrl = job.renderedBlobUrl;
    }
    if (imgShown) return;
    if (img.hasAttribute('srcset')) {
      t.originalSrcset = img.getAttribute('srcset');
      img.removeAttribute('srcset');
    }
    if (img.parentElement instanceof HTMLPictureElement) {
      for (const s of img.parentElement.querySelectorAll('source')) {
        const set = s.getAttribute('srcset');
        if (set !== null) {
          t.sources.set(s, set);
          s.removeAttribute('srcset');
        }
      }
    }
    img.src = job.renderedBlobUrl;
  }

  function showOriginal(img: HTMLImageElement): void {
    const t = tracked.get(img);
    if (!t) return;
    for (const [s, set] of t.sources) s.setAttribute('srcset', set);
    t.sources.clear();
    if (t.originalSrcset !== null) img.setAttribute('srcset', t.originalSrcset);
    if (img.getAttribute('src') !== t.originalSrc) img.src = t.originalSrc;
    const layer = t.layer;
    if (layer && layer.el.style.backgroundImage === layer.painted) layer.el.style.backgroundImage = layer.original;
  }

  function onSrcMutation(img: HTMLImageElement): void {
    const t = tracked.get(img);
    const job = t && jobs.get(t.key);
    const src = img.getAttribute('src');
    if (t && job?.state === 'ready' && src === job.renderedBlobUrl) {
      if (img.hasAttribute('srcset') && !peeking) showTranslated(img, false);
      return;
    }
    if (t && peeking && img.src === t.originalSrc) return;
    scan(img);
  }

  function setPeek(on: boolean): void {
    if (peeking === on) return;
    peeking = on;
    for (const set of elementsByKey.values()) {
      for (const img of set) (on ? showOriginal(img) : showTranslated(img, false));
    }
    chips.render();
  }

  function updateCurrent(): void {
    syncWork();
    refreshInView();
    let best: HTMLImageElement | undefined;
    let bestVisible = 0;
    for (const [key, set] of elementsByKey) {
      for (const img of set) {
        if (!img.isConnected) {
          set.delete(img);
          io.unobserve(img);
          continue;
        }
        const r = img.getBoundingClientRect();
        const visible = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
        if (visible > bestVisible) [best, bestVisible] = [img, visible];
      }
      if (!set.size) elementsByKey.delete(key);
    }
    if (!best || best === current) return;
    current = best;
    const place = placeOf(best);
    currentPos = place.position;
    const t = tracked.get(best);
    if (mode === 'auto' && t && !jobs.has(t.key)) request(t.key, t.originalSrc, place, best);
    reprioritize();
    if (mode === 'auto' || (t && jobs.has(t.key))) void prefetchFrom(best);
  }

  function setMode(next: TabMode): void {
    if (next === mode || !alive) return;
    mode = next;
    if (next === 'off') stopWork();
    for (const set of elementsByKey.values()) {
      for (const img of set) if (!jobs.has(tracked.get(img)?.key ?? '')) untrack(img);
    }
    document.querySelectorAll('img').forEach(scan);
    current = undefined;
    updateCurrent();
    chips.render();
  }

  function stopWork(): void {
    const dropped = [...jobs].flatMap(([key, job]) => (job.state === 'ready' ? [] : [key]));
    for (const key of dropped) {
      jobs.delete(key);
      wants.delete(key);
    }
    if (dropped.length) post({ type: 'drop', keys: dropped });
    for (const img of [...elementsByKey.values()].flatMap((set) => [...set])) {
      showOriginal(img);
      untrack(img);
    }
    for (const [key, job] of jobs) if (job.state === 'ready' && job.redo === 'refreshing') setRedo(key, job, 'stale');
    pick = undefined;
  }

  function syncWork(): void {
    const id = adapter.workId?.(location);
    if (id === workId || !alive) return;
    workId = id;
    work = undefined;
    unwatchWork();
    unwatchWork = () => undefined;
    setMode(resolvedMode());
    if (id) void loadWork(adapter, location).then(applyWork);
  }

  function applyWork(loaded: Work | undefined): void {
    if (!alive || !loaded || loaded.id !== workId) return;
    work = loaded;
    unwatchWork = watchWorkChoice(loaded.id, (choice) => {
      if (work?.id !== loaded.id) return;
      work = { ...work, choice };
      setMode(resolvedMode());
    });
    setMode(resolvedMode());
  }

  function reprioritize(): void {
    const pending = pendingWants().map((x) => ({ ...x, priority: Math.abs(x.position - currentPos) }));
    for (const x of pending) wants.set(x.key, x);
    if (pending.length) post({ type: 'want', jobs: pending });
  }

  async function prefetchFrom(img: HTMLImageElement): Promise<void> {
    const t = tracked.get(img);
    if (!t || !adapter.prefetch) return;
    const upcoming = await adapter.prefetch(t.originalSrc, location, img);
    for (const u of upcoming) {
      const key = adapter.pageKey(u.url);
      if (alive && !jobs.has(key)) request(key, u.url, { position: u.pageIndex, pageIndex: u.pageIndex }, img);
    }
  }

  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const img = e.target as HTMLImageElement;
        const t = tracked.get(img);
        if (mode === 'auto' && e.isIntersecting && t && !jobs.has(t.key)) request(t.key, t.originalSrc, placeOf(img), img);
      }
      updateCurrent();
      announceContext();
      chips.render();
    },
    { rootMargin: '100% 0px 300% 0px' },
  );

  const mo = new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'attributes') {
        if (r.target instanceof HTMLImageElement) onSrcMutation(r.target);
        continue;
      }
      for (const n of r.addedNodes) {
        if (n instanceof HTMLImageElement) scan(n);
        else if (n instanceof Element) n.querySelectorAll('img').forEach(scan);
      }
    }
  });
  mo.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['src', 'srcset'] });

  let frame = 0;
  const onScroll = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      updateCurrent();
      announceContext();
      chips.render();
    });
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Alt' && !e.repeat) setPeek(true);
    if (pick && (e.key === 'Enter' || e.key === 'Escape') && !e.isComposing && !isEditable(e.target)) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Enter') submitPick(pick.img);
      else closePick();
      return;
    }
    if (e.altKey && e.code === 'KeyR' && !e.repeat && !isEditable(e.target)) {
      const img = imageAtPointer() ?? current;
      if (!img || !jobs.has(tracked.get(img)?.key ?? '')) return;
      e.preventDefault();
      e.stopPropagation();
      openPick(img, 'report');
    }
    if (e.altKey && e.code === 'KeyT' && !e.repeat && !isEditable(e.target)) {
      const img = imageAtPointer() ?? current;
      if (!img || !readyPage(img)?.bubbles.length) return;
      e.preventDefault();
      e.stopPropagation();
      openPick(img, 'retranslate');
    }
  };
  const onPointer = (e: MouseEvent) => {
    if (!chrome.runtime?.id) return teardown();
    pointer = { x: e.clientX, y: e.clientY };
    say('move', { visibility: document.visibilityState, focus: document.hasFocus(), peeking });
    chips.render();
  };
  const onPointerLeave = () => {
    pointer = undefined;
    chips.render();
  };
  const onKeyUp = (e: KeyboardEvent) => {
    if (e.key === 'Alt') setPeek(false);
  };
  const endPeek = () => setPeek(false);
  addEventListener('scroll', onScroll, { passive: true, capture: true });
  addEventListener('resize', onScroll, { passive: true });
  addEventListener('keydown', onKeyDown, true);
  addEventListener('keyup', onKeyUp, true);
  addEventListener('mousemove', onPointer, { passive: true, capture: true });
  document.documentElement.addEventListener('mouseleave', onPointerLeave);
  addEventListener('blur', endPeek);
  document.addEventListener('visibilitychange', endPeek);
  const ping = setInterval(() => pendingWants().length && post({ type: 'ping' }), PING_MS);
  const onCommand = (msg: TabCommand, _sender: chrome.runtime.MessageSender, reply: (answer: Work | string | null) => void) => {
    if (msg.type === 'work') reply(work ?? null);
    else if (msg.type === 'context') reply(currentContext());
    else if (msg.type === 'disable') teardown();
    else retryBlocked();
  };
  chrome.runtime.onMessage.addListener(onCommand);
  const unwatchMode = watchSiteMode(site, (next) => {
    siteMode = next;
    setMode(resolvedMode());
  });
  let unwatchWork: () => void = () => undefined;
  if (work) applyWork(work);
  document.dispatchEvent(new Event(TAKEOVER));
  document.addEventListener(TAKEOVER, teardown);

  function teardown(): void {
    if (!alive) return;
    alive = false;
    setPeek(true);
    mo.disconnect();
    io.disconnect();
    clearInterval(ping);
    removeEventListener('scroll', onScroll, { capture: true });
    removeEventListener('resize', onScroll);
    removeEventListener('keydown', onKeyDown, true);
    removeEventListener('keyup', onKeyUp, true);
    removeEventListener('mousemove', onPointer, { capture: true });
    document.documentElement.removeEventListener('mouseleave', onPointerLeave);
    removeEventListener('blur', endPeek);
    document.removeEventListener('visibilitychange', endPeek);
    document.removeEventListener(TAKEOVER, teardown);
    try {
      chrome.runtime.onMessage.removeListener(onCommand);
      unwatchMode();
      unwatchWork();
      port?.disconnect();
    } catch {}
    chips.destroy();
    for (const job of jobs.values()) if (job.state === 'ready') URL.revokeObjectURL(job.renderedBlobUrl);
    delete w.__yomikae;
  }

  chips.bind(() => {
    const out: ChipItem[] = [];
    const hovered = peeking ? undefined : imageAtPointer();
    say('hover', { peeking, pointer: !!pointer, hovered: hovered ? (tracked.get(hovered)?.key ?? 'untracked') : null });
    for (const [key, set] of elementsByKey) {
      const job = jobs.get(key);
      if (!job) {
        for (const img of set) {
          if (img !== hovered) continue;
          const offer = mode === 'on-demand' && offerable(img);
          say('offer', { key, mode, offer, shown: box(shownRect(img)) });
          if (offer) out.push({ img, job, hover: true });
        }
        continue;
      }
      if (hovered && set.has(hovered)) say('offer', { key, job: job.state });
      for (const img of set) {
        const picking = pick?.img === img ? { purpose: pick.purpose, areas: pick.areas, bubbles: job.state === 'ready' ? bubbleAreas(job.page) : [] } : undefined;
        if (picking || (!peeking && (img === hovered || job.state !== 'ready' || isBusy(job)))) out.push({ img, job, hover: img === hovered, pick: picking });
      }
    }
    return out;
  });

  document.querySelectorAll('img').forEach(scan);
  updateCurrent();
  announceContext();
  say('tracked', { keys: [...elementsByKey.keys()], images: document.images.length });
}

const isBusy = (job: PageJob) => job.state === 'ready' && (job.redo === 'refreshing' || job.redo === 'retranslating');

function inView(img: HTMLImageElement): boolean {
  if (!img.isConnected) return false;
  const r = img.getBoundingClientRect();
  return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
}

function bubbleArea(page: TranslatedPage, index: number): ReportArea {
  const [x0, y0, x1, y1] = page.bubbles[index]!.box;
  return { box: [x0 / page.width, y0 / page.height, x1 / page.width, y1 / page.height], bubbleIndex: index };
}

function bubbleAreas(page: TranslatedPage): ReportArea[] {
  return page.bubbles.map((_, i) => bubbleArea(page, i));
}

function snapBubble(page: TranslatedPage, fx: number, fy: number): number | null {
  const x = fx * page.width;
  const y = fy * page.height;
  let best: number | null = null;
  let bestDist = Infinity;
  let bestArea = Infinity;
  for (const [index, { box: [x0, y0, x1, y1] }] of page.bubbles.entries()) {
    const dist = Math.hypot(Math.max(x0 - x, 0, x - x1), Math.max(y0 - y, 0, y - y1));
    const area = (x1 - x0) * (y1 - y0);
    if (dist < bestDist || (dist === bestDist && area < bestArea)) [best, bestDist, bestArea] = [index, dist, area];
  }
  return bestDist <= SNAP_REACH * Math.max(page.width, page.height) ? best : null;
}

function contentRect(img: HTMLImageElement): { left: number; top: number; width: number; height: number } {
  const r = img.getBoundingClientRect();
  const cs = getComputedStyle(img);
  const px = (v: string) => parseFloat(v) || 0;
  const left = r.left + px(cs.borderLeftWidth) + px(cs.paddingLeft);
  const top = r.top + px(cs.borderTopWidth) + px(cs.paddingTop);
  const width = r.width - px(cs.borderLeftWidth) - px(cs.paddingLeft) - px(cs.borderRightWidth) - px(cs.paddingRight);
  const height = r.height - px(cs.borderTopWidth) - px(cs.paddingTop) - px(cs.borderBottomWidth) - px(cs.paddingBottom);
  return { left, top, width, height };
}

type Box = { left: number; top: number; right: number; bottom: number };

const box = (r: Box) => [r.left, r.top, r.right, r.bottom].map(Math.round);

function debugging(): boolean {
  try {
    return localStorage.getItem('yomikaeDebug') === '1';
  } catch {
    return false;
  }
}

function debugLog() {
  const last = new Map<string, string>();
  return (topic: string, data: object, extra?: object) => {
    const line = JSON.stringify(data);
    if (last.get(topic) === line || !debugging()) return;
    last.set(topic, line);
    console.log(`[yomikae] ${topic} ${line}${extra ? ` ${JSON.stringify(extra)}` : ''}`);
  };
}

const contains = (r: Box, x: number, y: number) => x >= r.left && x < r.right && y >= r.top && y < r.bottom;

function shownRect(el: Element): Box {
  let { left, top, right, bottom } = el.getBoundingClientRect();
  let position = getComputedStyle(el).position;
  for (let a = el.parentElement; a && position !== 'fixed'; a = a.parentElement) {
    const cs = getComputedStyle(a);
    if (position === 'absolute' && cs.position === 'static') continue;
    position = cs.position;
    if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
    const r = a.getBoundingClientRect();
    if (cs.overflowX !== 'visible') [left, right] = [Math.max(left, r.left), Math.min(right, r.right)];
    if (cs.overflowY !== 'visible') [top, bottom] = [Math.max(top, r.top), Math.min(bottom, r.bottom)];
  }
  return { left, top, right: Math.max(left, right), bottom: Math.max(top, bottom) };
}

function isEditable(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement);
}

function cssUrls(value: string): string[] {
  return [...value.matchAll(/url\("?(.*?)"?\)/g)].flatMap((m) => (m[1] ? [m[1]] : []));
}

function isUncovered(el: Element): boolean {
  const r = shownRect(el);
  const x = (Math.max(r.left, 0) + Math.min(r.right, innerWidth)) / 2;
  const y = (Math.max(r.top, 0) + Math.min(r.bottom, innerHeight)) / 2;
  const top = document.elementFromPoint(x, y);
  return !!top && (el.contains(top) || top.contains(el) || top.parentElement === el.parentElement);
}

function crossfade(img: HTMLImageElement, originalSrc: string): void {
  const cs = getComputedStyle(img);
  const ghost = new Image();
  ghost.src = originalSrc;
  fadeOut(ghost, contentRect(img), `object-fit:${cs.objectFit};object-position:${cs.objectPosition}`);
}

function crossfadeLayer(el: HTMLElement): void {
  const cs = getComputedStyle(el);
  const r = shownRect(el);
  const paint = `background-image:${cs.backgroundImage};background-size:${cs.backgroundSize};background-repeat:${cs.backgroundRepeat};background-position:${cs.backgroundPosition}`;
  const full = el.getBoundingClientRect();
  const ghost = document.createElement('div');
  const inner = document.createElement('div');
  inner.style.cssText = `all:initial;position:absolute;left:${full.left - r.left}px;top:${full.top - r.top}px;width:${full.width}px;height:${full.height}px;${paint};pointer-events:none`;
  ghost.append(inner);
  fadeOut(ghost, { left: r.left, top: r.top, width: r.right - r.left, height: r.bottom - r.top }, 'overflow:hidden');
}

function fadeOut(ghost: HTMLElement, { left, top, width, height }: { left: number; top: number; width: number; height: number }, style: string): void {
  ghost.setAttribute('aria-hidden', 'true');
  ghost.style.cssText = `all:initial;position:fixed;left:${left}px;top:${top}px;width:${width}px;height:${height}px;${style};pointer-events:none;z-index:2147483646;opacity:1;transition:opacity ${FADE_MS}ms ease-out`;
  document.documentElement.append(ghost);
  requestAnimationFrame(() => requestAnimationFrame(() => (ghost.style.opacity = '0')));
  setTimeout(() => ghost.remove(), FADE_MS + 80);
}

type ChipItem = { img: HTMLImageElement; job: PageJob | undefined; hover: boolean; pick?: { purpose: PickPurpose; areas: ReportArea[]; bubbles: ReportArea[] } };
type ChipSource = () => ChipItem[];
type ChipActions = {
  retry(img: HTMLImageElement): void;
  translate(img: HTMLImageElement): void;
  openPick(img: HTMLImageElement, purpose: PickPurpose): void;
  pickPoint(img: HTMLImageElement, x: number, y: number, add: boolean): void;
  pickRect(img: HTMLImageElement, box: ReportArea['box']): void;
  submit(img: HTMLImageElement): void;
};
type Chip = {
  el: HTMLElement;
  status: HTMLElement;
  translate: HTMLButtonElement;
  report: HTMLButtonElement;
  retranslate: HTMLButtonElement;
  note: HTMLInputElement;
  fix: HTMLElement;
  hint: HTMLInputElement;
  ja: HTMLInputElement;
  en: HTMLInputElement;
  send: HTMLButtonElement;
  overlay: HTMLElement;
  marks: HTMLElement;
  statusKey: string;
  marksKey: string;
  pickKey: string;
};

const TOAST_MS = 2600;
const CLICK_SLOP_PX = 6;
const POINTER_EVENTS = ['click', 'mousedown', 'mouseup', 'pointerdown', 'pointerup', 'dblclick', 'contextmenu', 'auxclick'];

function boxStyle([x0, y0, x1, y1]: ReportArea['box']): string {
  return `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%`;
}

function createChips(actions: ChipActions) {
  const host = document.createElement('yomikae-chips');
  host.style.cssText = 'all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `<style>
    [hidden]{display:none!important}
    .chip{position:fixed;display:flex;align-items:center;gap:6px;height:22px;min-width:22px;box-sizing:border-box;padding:0 4px;border-radius:11px;
      background:rgba(20,20,24,.62);color:#fff;font:600 11px/1 system-ui,sans-serif;backdrop-filter:blur(4px);opacity:.85;transition:opacity .2s}
    .chip:hover,.chip.picking{opacity:1}
    .status{display:flex;align-items:center}
    .spin{width:12px;height:12px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:s .8s linear infinite}
    .queued .spin{animation-duration:1.6s;opacity:.6}
    .failed{background:rgba(160,30,30,.85);padding:0 9px}
    .waiting{padding:0 9px 0 5px}
    .waiting .status{gap:6px}
    .waiting .spin{animation-duration:2.4s;opacity:.7}
    .retry{pointer-events:auto;cursor:pointer}
    .report,.retranslate,.send,.translate{all:unset;pointer-events:auto;cursor:pointer;padding:0 5px;font:inherit;color:inherit}
    .status:not([hidden])+.report{border-left:1px solid rgba(255,255,255,.35);padding-left:7px}
    .report:not([hidden])+.retranslate{border-left:1px solid rgba(255,255,255,.35);padding-left:7px}
    .report:hover,.retranslate:hover,.send:hover,.translate:hover{text-decoration:underline}
    .note,.fix input{pointer-events:auto;box-sizing:border-box;width:300px;height:18px;margin:0 -1px;border:0;border-radius:9px;padding:0 8px;
      font:500 12px/18px system-ui,sans-serif;background:#fff;color:#111;outline:none}
    .fix{display:flex;align-items:center;gap:5px}
    .fix .hint{width:230px}
    .fix .ja{width:80px}
    .fix .en{width:96px}
    .fix span{opacity:.8;font-weight:500}
    .pick{position:fixed;pointer-events:auto;cursor:crosshair;box-sizing:border-box;outline:2px solid rgba(255,0,200,.7);outline-offset:-2px;
      background:rgba(255,0,200,.04);touch-action:none;user-select:none}
    .pick div{position:absolute;box-sizing:border-box;pointer-events:none}
    .bub{border:1.5px dashed rgba(255,0,200,.75)}
    .area{border:3px solid #ff00c8;background:rgba(255,0,200,.14)}
    .area span{position:absolute;left:-3px;bottom:100%;padding:2px 5px;background:#ff00c8;color:#fff;font:700 11px/1 system-ui,sans-serif;white-space:nowrap}
    .drag{border:2px dashed #ff00c8;background:rgba(255,0,200,.1)}
    .bubbles-only .drag{display:none!important}
    .toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:80vw;padding:9px 16px;border-radius:18px;
      background:rgba(20,20,24,.88);color:#fff;font:600 13px/1.3 system-ui,sans-serif;backdrop-filter:blur(4px);opacity:0;transition:opacity .2s}
    .toast.show{opacity:1}
    .toast.error{background:rgba(160,30,30,.92)}
    @keyframes s{to{transform:rotate(360deg)}}
  </style>`;
  const toastEl = document.createElement('div');
  toastEl.className = 'toast';
  root.append(toastEl);
  document.documentElement.append(host);
  const chips = new Map<HTMLImageElement, Chip>();
  const say = debugLog();
  let source: ChipSource = () => [];
  let frame = 0;
  let toastTimer = 0;

  function swallow(el: HTMLElement): void {
    for (const type of POINTER_EVENTS) {
      el.addEventListener(type, (e) => {
        e.stopPropagation();
        if (type !== 'pointerdown' && type !== 'pointerup' && e.target === el) e.preventDefault();
      });
    }
  }

  function makeOverlay(img: HTMLImageElement, focus: () => void): { overlay: HTMLElement; marks: HTMLElement } {
    const overlay = document.createElement('div');
    overlay.className = 'pick';
    overlay.hidden = true;
    const marks = document.createElement('div');
    marks.style.inset = '0';
    const drag = document.createElement('div');
    drag.className = 'drag';
    drag.hidden = true;
    overlay.append(marks, drag);
    swallow(overlay);
    let start: { x: number; y: number; cx: number; cy: number } | undefined;
    const at = (e: PointerEvent) => {
      const r = overlay.getBoundingClientRect();
      const clamp = (v: number) => Math.min(Math.max(v, 0), 1);
      return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height), cx: e.clientX, cy: e.clientY };
    };
    const rect = (a: { x: number; y: number }, b: { x: number; y: number }): ReportArea['box'] => [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)];
    const moved = (e: PointerEvent) => !!start && Math.max(Math.abs(e.clientX - start.cx), Math.abs(e.clientY - start.cy)) >= CLICK_SLOP_PX;
    overlay.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      overlay.setPointerCapture(e.pointerId);
      start = at(e);
    });
    overlay.addEventListener('pointermove', (e) => {
      if (!start || !moved(e)) return;
      drag.hidden = false;
      drag.style.cssText = boxStyle(rect(start, at(e)));
    });
    overlay.addEventListener('pointerup', (e) => {
      if (!start) return;
      const end = at(e);
      if (moved(e)) actions.pickRect(img, rect(start, end));
      else actions.pickPoint(img, end.x, end.y, e.shiftKey);
      start = undefined;
      drag.hidden = true;
      focus();
    });
    overlay.addEventListener('pointercancel', () => {
      start = undefined;
      drag.hidden = true;
    });
    return { overlay, marks };
  }

  function makeChip(img: HTMLImageElement): Chip {
    const el = document.createElement('div');
    swallow(el);
    const status = document.createElement('span');
    status.className = 'status';
    status.addEventListener('click', () => status.classList.contains('retry') && actions.retry(img));
    const translate = document.createElement('button');
    translate.className = 'translate';
    translate.textContent = 'Translate';
    translate.title = 'Translate this image';
    translate.hidden = true;
    translate.addEventListener('click', () => actions.translate(img));
    const report = document.createElement('button');
    report.className = 'report';
    report.textContent = 'Report';
    report.title = 'Report a problem with this page (Alt+R)';
    report.addEventListener('click', () => actions.openPick(img, 'report'));
    const retranslate = document.createElement('button');
    retranslate.className = 'retranslate';
    retranslate.textContent = 'Retranslate';
    retranslate.title = 'Translate some bubbles again, with a hint or a name fix (Alt+T)';
    retranslate.hidden = true;
    retranslate.addEventListener('click', () => actions.openPick(img, 'retranslate'));
    const field = (className: string, placeholder: string, maxLength?: number) => {
      const input = document.createElement('input');
      input.className = className;
      input.placeholder = placeholder;
      if (maxLength) input.maxLength = maxLength;
      for (const type of ['keydown', 'keyup', 'keypress']) input.addEventListener(type, (e) => e.stopPropagation());
      return input;
    };
    const note = field('note', 'Click bubbles or drag boxes, add a note, Enter sends');
    note.hidden = true;
    const hint = field('hint', 'Click bubbles, add a hint (optional)', HINT_MAX);
    const ja = field('ja', 'Name in JP', 200);
    const en = field('en', 'In English', 100);
    const arrow = document.createElement('span');
    arrow.textContent = '→';
    const fix = document.createElement('div');
    fix.className = 'fix';
    fix.hidden = true;
    fix.append(hint, ja, arrow, en);
    const send = document.createElement('button');
    send.className = 'send';
    send.textContent = 'Send';
    send.hidden = true;
    send.addEventListener('click', () => actions.submit(img));
    el.append(status, translate, report, retranslate, note, fix, send);
    const { overlay, marks } = makeOverlay(img, () => (fix.hidden ? note : hint).focus());
    root.append(overlay, el);
    return { el, status, translate, report, retranslate, note, fix, hint, ja, en, send, overlay, marks, statusKey: '', marksKey: '', pickKey: '' };
  }

  function drawPick(chip: Chip, img: HTMLImageElement, pick: NonNullable<ChipItem['pick']>): void {
    const c = contentRect(img);
    chip.overlay.style.left = `${c.left}px`;
    chip.overlay.style.top = `${c.top}px`;
    chip.overlay.style.width = `${c.width}px`;
    chip.overlay.style.height = `${c.height}px`;
    chip.overlay.classList.toggle('bubbles-only', pick.purpose === 'retranslate');
    const key = JSON.stringify(pick);
    if (chip.marksKey === key) return;
    chip.marksKey = key;
    const picked = new Set(pick.areas.map((a) => a.bubbleIndex));
    const bubbles = pick.bubbles.filter((b) => !picked.has(b.bubbleIndex)).map((b) => `<div class="bub" style="${boxStyle(b.box)}"></div>`);
    const areas = pick.areas.map((a, n) => `<div class="area" style="${boxStyle(a.box)}"><span>${a.bubbleIndex !== null ? `bubble ${a.bubbleIndex}` : `area ${n}`}</span></div>`);
    chip.marks.innerHTML = [...bubbles, ...areas].join('');
  }

  function place(chip: Chip, r: Box, top: number): void {
    chip.el.style.left = `${Math.min(r.right, innerWidth) - 8 - chip.el.offsetWidth}px`;
    chip.el.style.top = `${top}px`;
  }

  function draw(): void {
    frame = 0;
    const seen = new Set<HTMLImageElement>();
    for (const { img, job, hover, pick } of source()) {
      const r = shownRect(img);
      const top = Math.max(r.top, 0) + 8;
      if (!img.isConnected || r.bottom - top < 30 || top > innerHeight || r.right < 0 || r.left > innerWidth) {
        say('draw', { skipped: box(r), connected: img.isConnected });
        continue;
      }
      if (hover) say('draw', { chip: box(r), job: job?.state ?? 'offer' });
      seen.add(img);
      let chip = chips.get(img);
      if (!chip) chips.set(img, (chip = makeChip(img)));
      chip.translate.hidden = !!job;
      if (!job) {
        chip.statusKey = 'offer';
        chip.el.className = 'chip';
        chip.status.hidden = true;
        chip.report.hidden = true;
        chip.retranslate.hidden = true;
        chip.send.hidden = true;
        chip.overlay.hidden = true;
        chip.note.hidden = true;
        chip.fix.hidden = true;
        chip.pickKey = '';
        place(chip, r, top);
        continue;
      }
      const failed = job.state === 'failed';
      const waiting = job.state === 'waiting';
      const blocked = job.state === 'blocked';
      const busy = isBusy(job);
      const shown = job.state === 'ready' && !busy;
      const statusKey = `${failed ? `failed:${job.retryable}` : blocked ? `blocked:${job.host}` : shown || job.state === 'queued' || waiting ? job.state : 'busy'}:${!!pick}`;
      if (chip.statusKey !== statusKey) {
        chip.statusKey = statusKey;
        chip.el.className = `chip ${failed || blocked ? 'failed' : job.state === 'queued' || waiting ? job.state : ''} ${pick ? 'picking' : ''}`;
        chip.status.hidden = shown;
        chip.status.classList.toggle('retry', failed && job.retryable);
        if (blocked) chip.status.textContent = noAccessText(job.host);
        else chip.status.innerHTML = failed
          ? job.retryable
            ? 'Translation failed · Retry'
            : 'Can’t translate'
          : shown
            ? ''
            : waiting
              ? '<div class="spin"></div>Waiting for server…'
              : '<div class="spin"></div>';
      }
      chip.status.title = failed
        ? job.error
        : blocked
          ? noAccessText(job.host)
          : waiting
            ? `${job.detail} Retrying automatically.`
            : job.state === 'queued'
              ? 'Waiting to translate'
              : job.state === 'ready' && job.redo === 'retranslating'
                ? 'Retranslating'
                : job.state === 'ready' && job.redo === 'refreshing'
                  ? 'Updating names'
                  : 'Translating';
      chip.report.hidden = !hover || !!pick;
      chip.retranslate.hidden = !hover || !!pick || !shown || !job.page.bubbles.length;
      chip.send.hidden = !pick;
      chip.overlay.hidden = !pick;
      if (pick) drawPick(chip, img, pick);
      else chip.marksKey = '';
      const pickKey = pick?.purpose ?? '';
      if (chip.pickKey !== pickKey) {
        chip.pickKey = pickKey;
        chip.note.hidden = pickKey !== 'report';
        chip.fix.hidden = pickKey !== 'retranslate';
        for (const input of [chip.note, chip.hint, chip.ja, chip.en]) input.value = '';
        if (pickKey) (pickKey === 'report' ? chip.note : chip.hint).focus();
      }
      place(chip, r, top);
    }
    for (const [img, chip] of chips) {
      if (!seen.has(img)) {
        chip.el.remove();
        chip.overlay.remove();
        chips.delete(img);
      }
    }
  }

  return {
    bind(s: ChipSource) {
      source = s;
    },
    render() {
      if (!frame) frame = requestAnimationFrame(draw);
    },
    imageAt(x: number, y: number): HTMLImageElement | undefined {
      for (const [img, chip] of chips) if (contains(chip.el.getBoundingClientRect(), x, y)) return img;
      return undefined;
    },
    noteText(img: HTMLImageElement): string {
      return chips.get(img)?.note.value.trim() ?? '';
    },
    fixText(img: HTMLImageElement): { hint: string; ja: string; en: string } {
      const chip = chips.get(img);
      return { hint: chip?.hint.value.trim() ?? '', ja: chip?.ja.value.trim() ?? '', en: chip?.en.value.trim() ?? '' };
    },
    toast(text: string, error: boolean) {
      toastEl.textContent = text;
      toastEl.className = `toast show${error ? ' error' : ''}`;
      clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => toastEl.classList.remove('show'), TOAST_MS);
    },
    destroy() {
      cancelAnimationFrame(frame);
      clearTimeout(toastTimer);
      host.remove();
    },
  };
}
