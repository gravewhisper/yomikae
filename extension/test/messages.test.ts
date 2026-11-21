import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { fromBase64, toBase64 } from '../src/messages.ts';

describe('base64 between the service worker and the page', () => {
  it('gives back every byte of a render larger than one chunk', () => {
    const bytes = Uint8Array.from({ length: 0x8000 * 3 + 7 }, (_, i) => (i * 7919) % 256);
    assert.deepEqual(fromBase64(toBase64(bytes)), bytes);
  });

  it('gives back an empty render', () => {
    assert.deepEqual(fromBase64(toBase64(new Uint8Array())), new Uint8Array());
  });
});
