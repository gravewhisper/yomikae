import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { parseServerError } from '../src/contract.ts';

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
