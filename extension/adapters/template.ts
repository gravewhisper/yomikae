import { PREFETCH_AHEAD, defaultAdapter, type SiteAdapter, type Upcoming } from '#adapter';

const SITE = 'reader.example';
const IMAGE_HOSTS = ['https://img1.reader-cdn.example/', 'https://img2.reader-cdn.example/'];
const PAGE_IMAGE = /^https:\/\/img\d\.reader-cdn\.example\/(chapters\/(\d+)\/(\d+)\.webp)$/;
const READER_PATH = /^\/chapter\/(\d+)\/(\d+)$/;

const chapterOf = (loc: Location) => {
  const chapter = loc.pathname.match(READER_PATH)?.[1];
  return chapter ? `${SITE}/chapter/${chapter}` : undefined;
};

const chapterPageCount = () => Number(document.querySelector('[data-page-count]')?.getAttribute('data-page-count')) || Infinity;

const template: SiteAdapter = {
  matches: (host) => host === SITE || host.endsWith(`.${SITE}`),
  defaultMode: 'on-demand',
  contextKey: (loc) => chapterOf(loc) ?? defaultAdapter.contextKey(loc),
  workId: chapterOf,
  workLanguage: async () => document.querySelector('[data-language]')?.getAttribute('data-language')?.toLowerCase(),
  isPage: (url) => PAGE_IMAGE.test(url),
  pageKey: (url) => {
    const path = url.match(PAGE_IMAGE)?.[1];
    return path ? `template:${path}` : url;
  },
  fetchCandidates: (url) => {
    const path = url.match(PAGE_IMAGE)?.[1];
    if (!path) return [url];
    return [url, ...IMAGE_HOSTS.map((host) => host + path).filter((u) => u !== url)];
  },
  pageIndex: (url) => {
    const page = url.match(PAGE_IMAGE)?.[3];
    return page ? Number(page) : undefined;
  },
  prefetch: async (url) => {
    const m = url.match(PAGE_IMAGE);
    if (!m?.[2] || !m[3]) return [];
    const origin = new URL(url).origin;
    const current = Number(m[3]);
    const last = Math.min(current + PREFETCH_AHEAD, chapterPageCount());
    const upcoming: Upcoming[] = [];
    for (let page = current + 1; page <= last; page++) upcoming.push({ url: `${origin}/chapters/${m[2]}/${page}.webp`, pageIndex: page });
    return upcoming;
  },
  fallback: async (url) => (PAGE_IMAGE.test(url) ? url.replace(/\.webp$/, '.jpg') : undefined),
  extraOrigins: ['*://*.reader-cdn.example/*'],
  referer: { domain: 'reader-cdn.example', referer: 'https://reader.example/' },
};

export default template;
