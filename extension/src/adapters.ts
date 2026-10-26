import { defaultAdapter, type ImageReferer, type SiteAdapter } from './adapter.ts';
import { localAdapters } from './local-adapters.ts';

export { parseSiteMode, type SiteMode } from './adapter.ts';

const X_MEDIA = /^https:\/\/pbs\.twimg\.com\/media\/([\w-]+)(?:\.(jpe?g|png|webp))?(?::\w+)?(?:\?(.*))?$/;
const X_STATUS = /^(?:https:\/\/(?:[\w-]+\.)?(?:x|twitter)\.com)?\/\w+\/status\/(\d+)/;
const X_PHOTO = /\/photo\/(\d+)$/;
const X_AHEAD = 3;

const xStatus = (href: string | null | undefined) => {
  const id = href?.match(X_STATUS)?.[1];
  return id ? `x.com/i/status/${id}` : undefined;
};

function xTweetOf(img: Element): string | undefined {
  const article = img.closest('article');
  if (!article) return undefined;
  const links = [...article.querySelectorAll('a[href*="/status/"]')];
  const own = img.closest('a[href*="/status/"]') ?? links.find((a) => a.querySelector('time')) ?? links[0];
  return xStatus(own?.getAttribute('href'));
}

const xPhotoIndex = (img: Element) => {
  const href = img.closest('a[href*="/photo/"]')?.getAttribute('href') ?? (img.closest('article') ? undefined : location.pathname);
  const n = href?.match(X_PHOTO)?.[1];
  return n ? Number(n) : undefined;
};

const twitter: SiteAdapter = {
  matches: (host) => ['x.com', 'twitter.com'].some((d) => host === d || host.endsWith(`.${d}`)),
  defaultMode: 'on-demand',
  contextKey: (loc, img) => xStatus(loc.pathname) ?? (img && xTweetOf(img)) ?? defaultAdapter.contextKey(loc),
  isPage: (url) => X_MEDIA.test(url),
  pageKey: (url) => {
    const id = url.match(X_MEDIA)?.[1];
    return id ? `x:${id}` : url;
  },
  fetchCandidates: (url) => {
    const m = url.match(X_MEDIA);
    if (!m?.[1]) return [url];
    const base = `https://pbs.twimg.com/media/${m[1]}`;
    const ext = m[2]?.replace('jpeg', 'jpg');
    const shown = new URLSearchParams(m[3] ?? '').get('format') ?? ext;
    const sized = shown === 'png' ? 'png' : 'jpg';
    const originals = ext ? [ext, ...['jpg', 'png'].filter((f) => f !== ext)] : ['jpg', 'png'];
    return [
      ...new Set([
        ...originals.map((f) => `${base}?format=${f}&name=orig`),
        `${base}?format=${sized}&name=4096x4096`,
        `${base}?format=${sized}&name=large`,
        url,
      ]),
    ];
  },
  pageIndex: (_url, img) => (img ? xPhotoIndex(img) : undefined),
  prefetch: async (_url, _loc, img) => {
    const article = img?.closest('article');
    if (!img || !article) return [];
    const all = [...article.querySelectorAll<HTMLImageElement>('img')];
    return all
      .slice(all.indexOf(img) + 1)
      .filter((i) => X_MEDIA.test(i.src))
      .slice(0, X_AHEAD)
      .map((i) => ({ url: i.src, pageIndex: xPhotoIndex(i) ?? all.indexOf(i) + 1 }));
  },
  extraOrigins: ['*://pbs.twimg.com/*'],
};

const PX_IMAGE = /^https:\/\/i\.pximg\.net\/(?:c\/[^/]+\/)?img-(?:master|original)\/img\/((?:\d+\/){6})(\d+)_p(\d+)(?:_master1200)?\.\w+$/;
const PX_ARTWORK = /^\/(?:[a-z]{2}\/)?artworks\/(\d+)/;
const PX_AHEAD = 3;
const PX_ORIGIN = 'https://i.pximg.net/';

type PxImage = { date: string; id: string; page: number };
type PxPage = { original: string; regular: string };
const pxWorks = new Map<string, Promise<PxPage[] | undefined>>();

function pxImage(url: string): PxImage | undefined {
  const m = url.match(PX_IMAGE);
  return m?.[1] && m[2] && m[3] ? { date: m[1], id: m[2], page: Number(m[3]) } : undefined;
}

const pxWorkId = (loc: Location) => {
  const id = loc.pathname.match(PX_ARTWORK)?.[1];
  return id ? `pixiv.net/artworks/${id}` : undefined;
};

const pxMaster = ({ date, id }: PxImage, page: number) => `${PX_ORIGIN}img-master/img/${date}${id}_p${page}_master1200.jpg`;

function pxPages(id: string): Promise<PxPage[] | undefined> {
  let pending = pxWorks.get(id);
  if (!pending) {
    pending = fetch(`/ajax/illust/${id}/pages`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : undefined))
      .then(parsePxPages)
      .catch(() => undefined);
    pxWorks.set(id, pending);
  }
  return pending;
}

export function parsePxPages(v: unknown): PxPage[] | undefined {
  const body = (v as { body?: unknown } | undefined)?.body;
  if (!Array.isArray(body)) return undefined;
  const pages = body.map((p: { urls?: { original?: unknown; regular?: unknown } }) => {
    const { original, regular } = p?.urls ?? {};
    return typeof original === 'string' && typeof regular === 'string' && PX_IMAGE.test(original) && PX_IMAGE.test(regular) ? { original, regular } : undefined;
  });
  return pages.every((p) => p !== undefined) ? pages : undefined;
}

const pixiv: SiteAdapter = {
  matches: (host) => host === 'pixiv.net' || host.endsWith('.pixiv.net'),
  defaultMode: 'on-demand',
  contextKey: (loc, _img, url) => {
    const id = url && pxImage(url)?.id;
    return id ? `pixiv.net/artworks/${id}` : (pxWorkId(loc) ?? defaultAdapter.contextKey(loc));
  },
  workId: pxWorkId,
  isPage: (url) => PX_IMAGE.test(url),
  pageKey: (url) => {
    const m = pxImage(url);
    return m ? `pixiv:${m.id}_p${m.page}` : url;
  },
  fetchCandidates: async (url) => {
    const m = pxImage(url);
    if (!m) return [url];
    const original = (await pxPages(m.id))?.[m.page]?.original;
    return [...new Set([...(original ? [original] : []), pxMaster(m, m.page), url])];
  },
  pageIndex: (url) => pxImage(url)?.page,
  prefetch: async (url) => {
    const m = pxImage(url);
    if (!m) return [];
    const pages = await pxPages(m.id);
    if (!pages) return [];
    return pages.slice(m.page + 1, m.page + 1 + PX_AHEAD).map((p, n) => ({ url: p.regular, pageIndex: m.page + 1 + n }));
  },
  extraOrigins: ['*://i.pximg.net/*'],
  referer: { domain: 'i.pximg.net', referer: 'https://www.pixiv.net/' },
};

const registry: SiteAdapter[] = [twitter, pixiv, ...localAdapters];

export const isExtraOrigin = (pattern: string) => registry.some((a) => a.extraOrigins?.includes(pattern));

export const imageReferers = (): ImageReferer[] => registry.flatMap((a) => (a.referer ? [a.referer] : []));

export function adapterFor(host: string): SiteAdapter {
  return registry.find((a) => a.matches(host)) ?? defaultAdapter;
}
