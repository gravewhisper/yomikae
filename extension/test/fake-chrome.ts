import { build } from 'esbuild';

let loads = 0;

export async function bundle<T>(entry: string): Promise<T> {
  const out = await build({ entryPoints: [new URL(`../${entry}`, import.meta.url).pathname], bundle: true, write: false, format: 'esm', platform: 'browser', logLevel: 'silent' });
  return import(`data:text/javascript,${encodeURIComponent(`${out.outputFiles[0]?.text}\n//${loads++}`)}`);
}

type Handler = (...args: never[]) => unknown;
type Script = { id: string; matches?: string[] };

export function fakeChrome(data: Record<string, unknown> = {}, granted: string[] = []) {
  const grants = new Set(granted);
  const scripts = new Map<string, Script>();
  const revoked: string[] = [];
  const listeners: Record<string, Handler> = {};
  const sent: unknown[] = [];
  const event = (name: string) => ({ addListener: (f: Handler) => (listeners[name] = f), removeListener: () => undefined });
  const keys = (k: string | string[]) => [k].flat();
  const chrome = {
    storage: {
      local: {
        get: async (k: string | string[] | null) => (k === null ? { ...data } : Object.fromEntries(keys(k).filter((x) => x in data).map((x) => [x, data[x]]))),
        set: async (items: Record<string, unknown>) => void Object.assign(data, items),
        remove: async (k: string | string[]) => keys(k).forEach((x) => delete data[x]),
      },
      onChanged: event('storage'),
    },
    permissions: {
      contains: async ({ origins = [] }: { origins?: string[] }) => origins.every((o) => grants.has(o)),
      remove: async ({ origins = [] }: { origins?: string[] }) => {
        revoked.push(...origins);
        origins.forEach((o) => grants.delete(o));
        return true;
      },
      getAll: async () => ({ origins: [...grants] }),
      onAdded: event('permissionsAdded'),
      onRemoved: event('permissionsRemoved'),
    },
    scripting: {
      registerContentScripts: async (list: Script[]) => list.forEach((s) => scripts.set(s.id, s)),
      getRegisteredContentScripts: async (filter?: { ids?: string[] }) => [...scripts.values()].filter((s) => !filter?.ids || filter.ids.includes(s.id)),
      unregisterContentScripts: async ({ ids = [] }: { ids?: string[] }) => ids.forEach((id) => scripts.delete(id)),
      executeScript: async () => [],
    },
    tabs: { TAB_ID_NONE: -1, query: async () => [], sendMessage: async () => undefined },
    declarativeNetRequest: { RuleActionType: {}, HeaderOperation: {}, ResourceType: {}, getSessionRules: async () => [], updateSessionRules: async () => undefined },
    runtime: { onConnect: event('connect'), onMessage: event('message'), onInstalled: event('installed'), onStartup: event('startup'), sendMessage: async (msg: unknown) => void sent.push(msg) },
  };
  return { chrome, data, grants, scripts, revoked, listeners, sent };
}
