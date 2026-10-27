import type { NameKind, TranslatedPage } from './contract';

export const PORT_NAME = 'yomikae';

export const noAccessText = (host: string) => `No access to ${host}. Open the Yomikae popup and click Allow access.`;

export type Want = {
  key: string;
  url: string;
  candidates: string[];
  contextKey: string;
  pageIndex: number;
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

export type Broadcast = { type: 'names'; contextKey: string } | { type: 'context'; contextKey: string | null };
