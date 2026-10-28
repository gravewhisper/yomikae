import type { Name, NameKind, NameSource } from './contract';

export type CastEdit =
  | { type: 'spell'; entry: Name; en: string }
  | { type: 'flip'; entry: Name }
  | { type: 'delete'; entry: Name }
  | { type: 'add'; ja: string; en: string; reading: string; kind: NameKind };

export const SOURCE_LABELS: Record<NameSource, string> = { user: 'yours', furigana: 'furigana', auto: 'auto' };
export const KIND_LABELS: Record<NameKind, string> = { name: 'Characters', term: 'Terms' };

export const flipped = (kind: NameKind): NameKind => (kind === 'name' ? 'term' : 'name');

const same = (a: Name, b: Name) => a.source === b.source && a.ja === b.ja && a.en === b.en;

const owner = (stored: Name[], entry: Name) => (entry.source === 'auto' ? stored.find((n) => entry.guess && n.ja === entry.guess) : stored.find((n) => same(n, entry)));

function update(stored: Name[], entry: Name, change: (n: Name) => Name): Name[] {
  const target = owner(stored, entry);
  if (target) return stored.map((n) => (n === target ? { ...change(n), source: 'user' } : n));
  if (entry.source !== 'auto') return stored;
  return [...stored, change({ ja: entry.guess, en: entry.en, reading: '', source: 'user', kind: entry.kind, was: [], guess: '' })];
}

export function applyEdit(names: Name[], edit: CastEdit): Name[] {
  const stored = names.filter((n) => n.source !== 'auto');
  switch (edit.type) {
    case 'spell': {
      const { en } = edit;
      return update(stored, edit.entry, (n) => ({ ...n, en, was: [...n.was, n.en] }));
    }
    case 'flip':
      return update(stored, edit.entry, (n) => ({ ...n, kind: flipped(n.kind) }));
    case 'delete':
      return stored.filter((n) => !same(n, edit.entry));
    case 'add': {
      const { ja, en, reading, kind } = edit;
      const kept = stored.filter((n) => (ja ? n.ja !== ja : n.ja || n.en !== en));
      return [...kept, { ja, en, reading, source: 'user', kind, was: [], guess: '' }];
    }
  }
}
