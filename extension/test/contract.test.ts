import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { parseCast, parseServerError, parseTranslatedPage } from '../src/contract.ts';

describe('server errors', () => {
  it('knows every code the server sends', () => {
    for (const code of ['bad_image', 'bad_request', 'not_found', 'translator_unavailable', 'translator_error', 'internal']) {
      assert.deepEqual(parseServerError({ error: code, detail: 'why' }), { code, detail: 'why' });
    }
  });

  it('reports a code it does not know as unknown', () => {
    assert.deepEqual(parseServerError({ error: 'teapot', detail: 'short and stout' }), { code: 'unknown', detail: 'short and stout' });
    assert.deepEqual(parseServerError(null), { code: 'unknown', detail: 'Unreadable error response.' });
  });
});

describe('pages and names', () => {
  const page = { page_id: 'a', width: 1, height: 1, rendered_url: '/r', bubbles: [], timings_ms: {}, cached: true };

  it('reads updated, and treats a missing one as false', () => {
    assert.equal(parseTranslatedPage({ ...page, updated: true }).updated, true);
    assert.equal(parseTranslatedPage(page).updated, false);
  });

  it('reads a work’s names, with a missing kind as a name', () => {
    const cast = parseCast({ context_key: 'example.com/g/1', names: [{ ja: '在藤', en: 'Arifuji', reading: 'ありふじ', source: 'user' }, { ja: '月光流', en: 'Gekko Style', reading: '', source: 'furigana', kind: 'term' }] });
    assert.deepEqual(cast, {
      contextKey: 'example.com/g/1',
      names: [
        { ja: '在藤', en: 'Arifuji', reading: 'ありふじ', source: 'user', kind: 'name' },
        { ja: '月光流', en: 'Gekko Style', reading: '', source: 'furigana', kind: 'term' },
      ],
    });
    assert.throws(() => parseCast({ context_key: 'k', names: [{ ja: '', en: 'X', source: 'guess' }] }));
  });
});
