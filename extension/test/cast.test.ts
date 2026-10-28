import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { applyEdit } from '../src/cast.ts';
import type { Name } from '../src/contract.ts';

const user: Name = { ja: '在藤', en: 'Zaito', reading: '', source: 'user', kind: 'name', was: [], guess: '' };
const furigana: Name = { ja: '和音', en: 'Kazune', reading: 'かずね', source: 'furigana', kind: 'name', was: [], guess: '' };
const auto: Name = { ja: '凛恋ちゃん、おはよう', en: 'Riren', reading: '', source: 'auto', kind: 'name', was: [], guess: '凛恋' };
const names = [user, furigana, auto];

describe('cast sheet edits', () => {
  it('never sends auto names back, since the server rebuilds them', () => {
    assert.deepEqual(applyEdit(names, { type: 'delete', entry: user }), [furigana]);
  });

  it('respells an entry in place, keeps its Japanese and reading and remembers the old spelling', () => {
    assert.deepEqual(applyEdit(names, { type: 'spell', entry: furigana, en: 'Kazuné' }), [user, { ...furigana, en: 'Kazuné', source: 'user', was: ['Kazune'] }]);
    const twice = applyEdit([{ ...user, en: 'Arifugi', was: ['Zaito'] }], { type: 'spell', entry: { ...user, en: 'Arifugi', was: ['Zaito'] }, en: 'Arifuji' });
    assert.deepEqual(twice, [{ ...user, en: 'Arifuji', was: ['Zaito', 'Arifugi'] }]);
    assert.deepEqual(applyEdit(names, { type: 'flip', entry: user }), [{ ...user, kind: 'term' }, furigana]);
  });

  it('saves an edited auto name under the Japanese the server found in its line', () => {
    const rirenx: Name = { ja: '凛恋', en: 'Rirenx', reading: '', source: 'user', kind: 'name', was: ['Riren'], guess: '' };
    assert.deepEqual(applyEdit(names, { type: 'spell', entry: auto, en: 'Rirenx' }), [user, furigana, rirenx]);
    assert.deepEqual(applyEdit(names, { type: 'flip', entry: auto }).at(-1), { ...rirenx, en: 'Riren', kind: 'term', was: [] });
    const unknown = { ...auto, guess: '' };
    assert.deepEqual(applyEdit([unknown], { type: 'spell', entry: unknown, en: 'Rirenx' }), [{ ...rirenx, ja: '' }]);
  });

  it('renames the saved entry an auto name belongs to instead of adding a second one', () => {
    const saved: Name = { ja: '凛恋', en: 'Riren', reading: 'りれん', source: 'furigana', kind: 'name', was: [], guess: '' };
    assert.deepEqual(applyEdit([saved, auto], { type: 'spell', entry: auto, en: 'Lily' }), [{ ...saved, en: 'Lily', source: 'user', was: ['Riren'] }]);
    assert.deepEqual(applyEdit([saved, auto], { type: 'flip', entry: auto }), [{ ...saved, source: 'user', kind: 'term' }]);
  });

  it('adds a name in place of one with the same Japanese', () => {
    assert.deepEqual(applyEdit(names, { type: 'add', ja: '在藤', en: 'Arifuji', reading: 'ありふじ', kind: 'name' }), [
      furigana,
      { ja: '在藤', en: 'Arifuji', reading: 'ありふじ', source: 'user', kind: 'name', was: [], guess: '' },
    ]);
    const bare: Name = { ja: '', en: 'Lene', reading: '', source: 'user', kind: 'name', was: [], guess: '' };
    assert.deepEqual(applyEdit([bare, furigana], { type: 'add', ja: '', en: 'Lene', reading: '', kind: 'term' }), [furigana, { ...bare, kind: 'term' }]);
  });
});
