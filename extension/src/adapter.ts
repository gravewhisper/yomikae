export type Upcoming = { url: string; pageIndex: number };

export const SITE_MODES = ['auto', 'on-demand'] as const;
export type SiteMode = (typeof SITE_MODES)[number];
export const parseSiteMode = (v: unknown): SiteMode | undefined => SITE_MODES.find((m) => m === v);

export type ImageReferer = { domain: string; referer: string };

export type SiteAdapter = {
  matches(host: string): boolean;
  defaultMode: SiteMode;
  contextKey(loc: Location, img?: HTMLImageElement, url?: string): string;
  workId?(loc: Location): string | undefined;
  workLanguage?(loc: Location): Promise<string | undefined>;
  pageKey(url: string): string;
  fetchCandidates(url: string): string[] | Promise<string[]>;
  isPage?(url: string): boolean;
  pageIndex(url: string, img?: HTMLImageElement): number | undefined;
  prefetch?(url: string, loc: Location, img?: HTMLImageElement): Promise<Upcoming[]>;
  fallback?(url: string): Promise<string | undefined>;
  extraOrigins?: string[];
  referer?: ImageReferer;
};

export const defaultAdapter: SiteAdapter = {
  matches: () => true,
  defaultMode: 'on-demand',
  contextKey: (loc) => loc.host + loc.pathname.replace(/\/\d+\/?$/, '').replace(/\/+$/, ''),
  pageKey: (url) => url,
  fetchCandidates: (url) => [url],
  pageIndex: () => undefined,
};

export const PREFETCH_AHEAD = 5;
