import type { NameKind, TranslatedPage } from './contract';
import type { SourceLang } from './languages';

export const PORT_NAME = 'yomikae';

export const noAccessText = (host: string) => `No access to ${host}. Open the Yomikae popup and click Allow access.`;

export type Want = {
  key: string;
  url: string;
  candidates: string[];
  contextKey: string;
  sourceLang: SourceLang;
  pageIndex?: number;
  position: number;
  priority: number;
};

export type ReportArea = { box: [number, number, number, number]; bubbleIndex: number | null };

export type ReportRequest = {
  want: Want;
  pageUrl: string;
  imageUrl: string;
  note: string;
  jobState: string;
  jobError: string | null;
  areas: ReportArea[];
};

export const HINT_MAX = 300;
export const NAME_FIXES_MAX = 20;

export type NameFix = { ja: string; en: string; kind: NameKind };

export type RetranslateRequest = {
  want: Want;
  bubbles: number[];
  hint: string;
  names: NameFix[];
};

export type ToBackground =
  | { type: 'want'; jobs: Want[] }
  | { type: 'lookup'; keys: string[] }
  | { type: 'drop'; keys: string[] }
  | { type: 'report'; report: ReportRequest }
  | { type: 'retranslate'; request: RetranslateRequest }
  | { type: 'ping' };

export type ToContent =
  | { type: 'progress'; key: string; state: 'queued' | 'fetching' | 'translating' }
  | { type: 'progress'; key: string; state: 'waiting'; detail: string }
  | { type: 'ready'; key: string; page: TranslatedPage; mime: string; base64: string; contextKey: string }
  | { type: 'failed'; key: string; error: string; retryable: boolean }
  | { type: 'blocked'; key: string; host: string }
  | { type: 'reported'; key: string; outcome: { ok: true; reportId: string; path: string } | { ok: false; error: string } }
  | { type: 'retranslated'; key: string; outcome: { ok: true } | { ok: false; error: string } }
  | { type: 'stale'; contextKey: string };

export type TabCommand = { type: 'disable' } | { type: 'access' } | { type: 'work' } | { type: 'context' };

export type SiteQuery = { type: 'site' };

export type Broadcast = { type: 'names'; contextKey: string } | { type: 'context'; contextKey: string | null };

export function toBase64(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

export function fromBase64(base64: string): Uint8Array<ArrayBuffer> {
  const s = atob(base64);
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
  return bytes;
}
