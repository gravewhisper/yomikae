import { applyEdit, flipped, SOURCE_LABELS, type CastEdit } from './cast';
import { getServerUrl, NAME_KINDS, parseCast, parseServerError, type Cast, type Name } from './contract';
import type { Broadcast, TabCommand } from './messages';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const forced = Number(new URLSearchParams(location.search).get('tab')) || undefined;

let tabId: number | undefined;
let windowId: number | undefined;
let contextKey: string | null = null;
let loads = 0;

async function activeTab(): Promise<chrome.tabs.Tab | undefined> {
  if (forced) return chrome.tabs.get(forced).catch(() => undefined);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function follow(): Promise<void> {
  const tab = await activeTab();
  tabId = tab?.id;
  windowId = tab?.windowId;
  const answer: unknown = tabId === undefined ? undefined : await chrome.tabs.sendMessage(tabId, { type: 'context' } satisfies TabCommand).catch(() => undefined);
  show(typeof answer === 'string' && answer ? answer : null);
}

function show(next: string | null): void {
  contextKey = next;
  void load();
}

const namesPath = (key: string) => `/v1/names?context_key=${encodeURIComponent(key)}`;

async function api(path: string, init?: RequestInit): Promise<Cast> {
  const res = await fetch(`${await getServerUrl()}${path}`, init).catch(() => {
    throw new Error('Local server not reachable.');
  });
  if (!res.ok) throw new Error(parseServerError(await res.json().catch(() => null)).detail);
  return parseCast(await res.json());
}

async function load(): Promise<void> {
  const key = contextKey;
  const seq = ++loads;
  $('work').hidden = !key;
  $('work').textContent = key ?? '';
  $('empty').hidden = !!key;
  if (!key) {
    $('names').hidden = true;
    return say('');
  }
  try {
    const cast = await api(namesPath(key));
    if (seq === loads) render(cast);
  } catch (err) {
    if (seq === loads) say((err as Error).message, true);
  }
}

async function edit(change: CastEdit): Promise<boolean> {
  const key = contextKey;
  if (!key) return false;
  say('Saving…');
  try {
    const fresh = await api(namesPath(key));
    const body = JSON.stringify({ names: applyEdit(fresh.names, change) });
    const cast = await api(namesPath(key), { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
    chrome.runtime.sendMessage({ type: 'names', contextKey: key } satisfies Broadcast).catch(() => undefined);
    if (key !== contextKey) return true;
    render(cast);
    say('Saved. Pages that use the old spelling are redone when you view them.');
    return true;
  } catch (err) {
    say((err as Error).message, true);
    return false;
  }
}

function say(text: string, error = false): void {
  const status = $('status');
  status.hidden = !text;
  status.textContent = text;
  status.toggleAttribute('data-error', error);
}

function render(cast: Cast): void {
  $('names').hidden = false;
  say('');
  for (const kind of NAME_KINDS) {
    const list = document.querySelector(`.group[data-kind="${kind}"] .list`)!;
    list.replaceChildren(...cast.names.filter((n) => n.kind === kind).map(entryRow));
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = ''): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function entryRow(name: Name): HTMLElement {
  const row = el('div', 'entry');
  const jp = el('div', 'jp');
  if (name.source === 'auto') {
    const line = el('span', 'line', name.ja ? `from “${name.ja}”` : '');
    line.title = name.ja;
    jp.append(line);
  } else {
    jp.append(el('span', 'ja', name.ja || '—'));
    if (name.reading) jp.append(el('span', 'reading', name.reading));
  }
  const en = el('input', 'en');
  en.value = name.en;
  en.maxLength = 100;
  en.title = 'English spelling';
  en.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') en.blur();
    if (e.key === 'Escape') {
      en.value = name.en;
      en.blur();
    }
  });
  en.addEventListener('change', () => {
    const value = en.value.trim();
    if (value && value !== name.en) void edit({ type: 'spell', entry: name, en: value });
    else en.value = name.en;
  });
  const source = el('span', 'src', SOURCE_LABELS[name.source]);
  source.dataset.source = name.source;
  const flip = el('button', 'flip', flipped(name.kind) === 'term' ? '→ term' : '→ character');
  flip.title = flipped(name.kind) === 'term' ? 'Make this a term' : 'Make this a character';
  flip.addEventListener('click', () => void edit({ type: 'flip', entry: name }));
  const del = el('button', 'del', '×');
  del.title = 'Delete';
  del.hidden = name.source === 'auto';
  del.addEventListener('click', () => void edit({ type: 'delete', entry: name }));
  row.append(jp, en, source, flip, del);
  return row;
}

$('add').addEventListener('submit', (e) => {
  e.preventDefault();
  const value = (id: string) => $<HTMLInputElement>(id).value.trim();
  const en = value('add-en');
  if (!en) return;
  const kind = NAME_KINDS.find((k) => k === $<HTMLSelectElement>('add-kind').value) ?? 'name';
  void edit({ type: 'add', ja: value('add-ja'), en, reading: value('add-reading'), kind }).then((saved) => {
    if (saved) for (const id of ['add-ja', 'add-en', 'add-reading']) $<HTMLInputElement>(id).value = '';
  });
});

chrome.tabs.onActivated.addListener((info) => {
  if (!forced && info.windowId === windowId) void follow();
});
chrome.tabs.onUpdated.addListener((id, info) => {
  if (id === tabId && (info.url || info.status === 'complete')) void follow();
});
chrome.runtime.onMessage.addListener((msg: Broadcast, sender) => {
  if (msg.type === 'context' && sender.tab?.id === tabId && msg.contextKey !== contextKey) show(msg.contextKey);
  else if (msg.type === 'names' && msg.contextKey === contextKey) void load();
});

void follow();
