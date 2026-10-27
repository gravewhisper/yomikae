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

const fromAuto = (en: string, kind: NameKind): Name => ({ ja: '', en, reading: '', source: 'user', kind });

function replace(stored: Name[], entry: Name, next: Name): Name[] {
  if (entry.source === 'auto') return [...stored, next];
  return stored.map((n) => (same(n, entry) ? next : n));
}

export function applyEdit(names: Name[], edit: CastEdit): Name[] {
  const stored = names.filter((n) => n.source !== 'auto');
  switch (edit.type) {
    case 'spell': {
      const { entry, en } = edit;
      return replace(stored, entry, entry.source === 'auto' ? fromAuto(en, entry.kind) : { ...entry, en, source: 'user' });
    }
    case 'flip': {
      const { entry } = edit;
      const kind = flipped(entry.kind);
      return replace(stored, entry, entry.source === 'auto' ? fromAuto(entry.en, kind) : { ...entry, kind, source: 'user' });
    }
    case 'delete':
      return stored.filter((n) => !same(n, edit.entry));
    case 'add': {
      const { ja, en, reading, kind } = edit;
      const kept = stored.filter((n) => (ja ? n.ja !== ja : n.ja || n.en !== en));
      return [...kept, { ja, en, reading, source: 'user', kind }];
    }
  }
}
