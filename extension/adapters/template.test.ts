import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import template from './template.ts';

const loc = (href: string) => new URL(href) as unknown as Location;
const page3 = 'https://img2.reader-cdn.example/chapters/77/3.webp';

describe('template adapter', () => {
  const realDocument = globalThis.document;
  afterEach(() => {
    globalThis.document = realDocument;
  });

  it('matches the site and its subdomains only', () => {
    assert.ok(template.matches('reader.example'));
    assert.ok(template.matches('www.reader.example'));
    assert.ok(!template.matches('notreader.example'));
  });

  it('recognizes chapter pages but not other images', () => {
    assert.ok(template.isPage?.(page3));
    assert.ok(!template.isPage?.('https://img2.reader-cdn.example/covers/77.webp'));
  });

  it('keys a page alike on every image host', () => {
    assert.equal(template.pageKey(page3), 'template:chapters/77/3.webp');
    assert.equal(template.pageKey(page3.replace('img2', 'img1')), 'template:chapters/77/3.webp');
  });

  it('uses the chapter as context, whatever the page', () => {
    assert.equal(template.contextKey(loc('https://reader.example/chapter/77/1')), 'reader.example/chapter/77');
    assert.equal(template.contextKey(loc('https://reader.example/chapter/77/9')), 'reader.example/chapter/77');
  });

  it('names the chapter as the work and reads its language from the page', async () => {
    assert.equal(template.workId?.(loc('https://reader.example/chapter/77/9')), 'reader.example/chapter/77');
    assert.equal(template.workId?.(loc('https://reader.example/')), undefined);
    globalThis.document = { querySelector: () => ({ getAttribute: () => 'English' }) } as unknown as Document;
    assert.equal(await template.workLanguage?.(loc('https://reader.example/chapter/77/9')), 'english');
  });

  it('fetches the shown URL first, then the other image host', () => {
    assert.deepEqual(template.fetchCandidates(page3), [page3, 'https://img1.reader-cdn.example/chapters/77/3.webp']);
  });

  it('prefetches the next pages up to the end of the chapter', async () => {
    globalThis.document = { querySelector: () => ({ getAttribute: () => '5' }) } as unknown as Document;
    const next = await template.prefetch!(page3, loc('https://reader.example/chapter/77/3'));
    assert.deepEqual(
      next.map((u) => [u.pageIndex, u.url]),
      [
        [4, 'https://img2.reader-cdn.example/chapters/77/4.webp'],
        [5, 'https://img2.reader-cdn.example/chapters/77/5.webp'],
      ],
    );
  });
});
