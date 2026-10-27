import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { applyEdit } from '../src/cast.ts';
import type { Name } from '../src/contract.ts';

const user: Name = { ja: '在藤', en: 'Zaito', reading: '', source: 'user', kind: 'name' };
const furigana: Name = { ja: '和音', en: 'Kazune', reading: 'かずね', source: 'furigana', kind: 'name' };
const auto: Name = { ja: '在藤さん、こんにちは', en: 'Zaito-san', reading: '', source: 'auto', kind: 'name' };
const names = [user, furigana, auto];

describe('cast sheet edits', () => {
  it('never sends auto names back, since the server rebuilds them', () => {
    assert.deepEqual(applyEdit(names, { type: 'delete', entry: user }), [furigana]);
  });

  it('makes a respelt or flipped entry yours and keeps its Japanese and reading', () => {
    assert.deepEqual(applyEdit(names, { type: 'spell', entry: furigana, en: 'Kazuné' }), [user, { ...furigana, en: 'Kazuné', source: 'user' }]);
    assert.deepEqual(applyEdit(names, { type: 'flip', entry: user }), [{ ...user, kind: 'term' }, furigana]);
  });

  it('turns an edit of an auto name into a name of yours without Japanese', () => {
    assert.deepEqual(applyEdit(names, { type: 'spell', entry: auto, en: 'Arifuji-san' }), [user, furigana, { ja: '', en: 'Arifuji-san', reading: '', source: 'user', kind: 'name' }]);
    assert.deepEqual(applyEdit(names, { type: 'flip', entry: auto }).at(-1), { ja: '', en: 'Zaito-san', reading: '', source: 'user', kind: 'term' });
  });

  it('adds a name in place of one with the same Japanese', () => {
    assert.deepEqual(applyEdit(names, { type: 'add', ja: '在藤', en: 'Arifuji', reading: 'ありふじ', kind: 'name' }), [furigana, { ja: '在藤', en: 'Arifuji', reading: 'ありふじ', source: 'user', kind: 'name' }]);
    const bare: Name = { ja: '', en: 'Lene', reading: '', source: 'user', kind: 'name' };
    assert.deepEqual(applyEdit([bare, furigana], { type: 'add', ja: '', en: 'Lene', reading: '', kind: 'term' }), [furigana, { ...bare, kind: 'term' }]);
  });
});
