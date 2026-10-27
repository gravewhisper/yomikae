export type Bubble = {
  box: [number, number, number, number];
  source: string;
  translation: string;
  vertical: boolean;
};

export type TranslatedPage = {
  page_id: string;
  width: number;
  height: number;
  rendered_url: string;
  bubbles: Bubble[];
  timings_ms: Record<string, number>;
  cached: boolean;
  updated: boolean;
};

export const NAME_SOURCES = ['user', 'furigana', 'auto'] as const;
export type NameSource = (typeof NAME_SOURCES)[number];
export const NAME_KINDS = ['name', 'term'] as const;
export type NameKind = (typeof NAME_KINDS)[number];

export type Name = { ja: string; en: string; reading: string; source: NameSource; kind: NameKind };
export type Cast = { contextKey: string; names: Name[] };

export type Health =
  | { ok: true; version: string; translator: string; gpu: string }
  | { ok: false; detail: string };

export type Redo = 'stale' | 'refreshing' | 'retranslating';

export type PageJob =
  | { state: 'queued'; url: string; priority: number }
  | { state: 'fetching'; url: string }
  | { state: 'translating'; url: string }
  | { state: 'waiting'; url: string; detail: string }
  | { state: 'ready'; url: string; page: TranslatedPage; renderedBlobUrl: string; contextKey: string; redo?: Redo }
  | { state: 'failed'; url: string; error: string; retryable: boolean }
  | { state: 'blocked'; url: string; host: string };

export const SERVER_ERROR_CODES = ['bad_image', 'bad_request', 'not_found', 'translator_unavailable', 'translator_error', 'internal'] as const;
export type ServerErrorCode = (typeof SERVER_ERROR_CODES)[number];

class ParseError extends Error {}

type Obj = Record<string, unknown>;

function obj(v: unknown, what: string): Obj {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new ParseError(`${what}: expected object`);
  return v as Obj;
}
function str(o: Obj, k: string): string {
  const v = o[k];
  if (typeof v !== 'string') throw new ParseError(`${k}: expected string`);
  return v;
}
function num(o: Obj, k: string): number {
  const v = o[k];
  if (typeof v !== 'number' || !Number.isFinite(v)) throw new ParseError(`${k}: expected number`);
  return v;
}
function bool(o: Obj, k: string): boolean {
  const v = o[k];
  if (typeof v !== 'boolean') throw new ParseError(`${k}: expected boolean`);
  return v;
}

function parseBubble(v: unknown): Bubble {
  const o = obj(v, 'bubble');
  const box = o.box;
  if (!Array.isArray(box) || box.length !== 4 || !box.every((n) => typeof n === 'number')) {
    throw new ParseError('bubble.box: expected [x0, y0, x1, y1]');
  }
  return {
    box: box as Bubble['box'],
    source: str(o, 'source'),
    translation: str(o, 'translation'),
    vertical: bool(o, 'vertical'),
  };
}

export function parseTranslatedPage(v: unknown): TranslatedPage {
  const o = obj(v, 'TranslatedPage');
  if (!Array.isArray(o.bubbles)) throw new ParseError('bubbles: expected array');
  const timings = obj(o.timings_ms ?? {}, 'timings_ms');
  return {
    page_id: str(o, 'page_id'),
    width: num(o, 'width'),
    height: num(o, 'height'),
    rendered_url: str(o, 'rendered_url'),
    bubbles: o.bubbles.map(parseBubble),
    timings_ms: Object.fromEntries(Object.entries(timings).filter((e): e is [string, number] => typeof e[1] === 'number')),
    cached: bool(o, 'cached'),
    updated: o.updated === true,
  };
}

function parseName(v: unknown): Name {
  const o = obj(v, 'name');
  const source = NAME_SOURCES.find((x) => x === o.source);
  if (!source) throw new ParseError('source: expected user, furigana or auto');
  return {
    ja: str(o, 'ja'),
    en: str(o, 'en'),
    reading: typeof o.reading === 'string' ? o.reading : '',
    source,
    kind: NAME_KINDS.find((x) => x === o.kind) ?? 'name',
  };
}

export function parseCast(v: unknown): Cast {
  const o = obj(v, 'names');
  if (!Array.isArray(o.names)) throw new ParseError('names: expected array');
  return { contextKey: str(o, 'context_key'), names: o.names.map(parseName) };
}

export type ReportReceipt = { reportId: string; path: string };

export function parseReportReceipt(v: unknown): ReportReceipt {
  const o = obj(v, 'report');
  return { reportId: str(o, 'report_id'), path: str(o, 'path') };
}

export function parseHealth(v: unknown): Health {
  const o = obj(v, 'health');
  if (o.ok === true) {
    return {
      ok: true,
      version: typeof o.version === 'string' ? o.version : '?',
      translator: typeof o.translator === 'string' ? o.translator : '?',
      gpu: typeof o.gpu === 'string' ? o.gpu : '?',
    };
  }
  return { ok: false, detail: typeof o.detail === 'string' ? o.detail : 'Server reports not ok.' };
}

export function parseServerError(v: unknown): { code: ServerErrorCode | 'unknown'; detail: string } {
  try {
    const o = obj(v, 'error');
    const code = o.error;
    const known = SERVER_ERROR_CODES.find((c) => c === code);
    return { code: known ?? 'unknown', detail: typeof o.detail === 'string' ? o.detail : String(code) };
  } catch {
    return { code: 'unknown', detail: 'Unreadable error response.' };
  }
}

export const DEFAULT_SERVER_URL = 'http://127.0.0.1:7878';

export async function getServerUrl(): Promise<string> {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  return typeof serverUrl === 'string' && serverUrl ? serverUrl.replace(/\/+$/, '') : DEFAULT_SERVER_URL;
}
