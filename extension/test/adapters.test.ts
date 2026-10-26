import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import { adapterFor, parsePxPages, parseSiteMode } from '../src/adapters.ts';

const loc = (href: string) => new URL(href) as unknown as Location;

type FakeNode = { href?: string; time?: boolean; src?: string; parent?: FakeNode; article?: FakeNode; links?: FakeNode[]; imgs?: HTMLImageElement[] };

function fakeElement(node: FakeNode): HTMLImageElement {
  const el = {
    src: node.src ?? '',
    getAttribute: (name: string) => (name === 'href' ? (node.href ?? null) : null),
    querySelector: (sel: string) => (sel === 'time' && node.time ? {} : null),
    querySelectorAll: (sel: string) => (sel === 'img' ? (node.imgs ?? []) : (node.links ?? []).map(fakeElement)),
    closest: (sel: string): unknown => {
      if (sel === 'article') return node.article ? fakeElement(node.article) : null;
      const part = sel.match(/href\*="([^"]+)"/)?.[1];
      for (let at = node.parent; at; at = at.parent) if (part && at.href?.includes(part)) return fakeElement(at);
      return null;
    },
  };
  return el as unknown as HTMLImageElement;
}

describe('site modes', () => {
  it('defaults X, pixiv and unknown sites to on-demand', () => {
    assert.equal(adapterFor('x.com').defaultMode, 'on-demand');
    assert.equal(adapterFor('twitter.com').defaultMode, 'on-demand');
    assert.equal(adapterFor('www.pixiv.net').defaultMode, 'on-demand');
    assert.equal(adapterFor('pixiv.net').defaultMode, 'on-demand');
    assert.equal(adapterFor('example.com').defaultMode, 'on-demand');
  });

  it('parses only known modes', () => {
    assert.equal(parseSiteMode('auto'), 'auto');
    assert.equal(parseSiteMode('on-demand'), 'on-demand');
    assert.equal(parseSiteMode('always'), undefined);
    assert.equal(parseSiteMode(undefined), undefined);
  });
});

describe('x.com adapter', () => {
  const x = adapterFor('x.com');
  const small = 'https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=jpg&name=small';

  it('recognizes tweet media but not avatars, emoji or video thumbnails', () => {
    assert.ok(x.isPage?.(small));
    assert.ok(x.isPage?.('https://pbs.twimg.com/media/HVU2SLKW0AA7q0a.jpg:large'));
    assert.ok(!x.isPage?.('https://pbs.twimg.com/profile_images/1321163587679784960/0ZxKlEKB_normal.jpg'));
    assert.ok(!x.isPage?.('https://abs-0.twimg.com/emoji/v2/svg/1f3d6.svg'));
    assert.ok(!x.isPage?.('https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/a.jpg'));
  });

  it('keys every size of one media by its id', () => {
    const key = x.pageKey(small);
    assert.equal(key, 'x:HVU2SLKW0AA7q0a');
    for (const name of ['medium', '900x900', 'large', 'orig']) assert.equal(x.pageKey(small.replace('small', name)), key);
    assert.equal(x.pageKey('https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=webp&name=large'), key);
    assert.equal(x.pageKey('https://pbs.twimg.com/media/HVU2SLKW0AA7q0a.jpg:large'), key);
  });

  it('asks for the original first, then the largest sizes, then the shown URL', () => {
    assert.deepEqual(x.fetchCandidates('https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=webp&name=small'), [
      'https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=jpg&name=orig',
      'https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=png&name=orig',
      'https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=jpg&name=4096x4096',
      'https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=jpg&name=large',
      'https://pbs.twimg.com/media/HVU2SLKW0AA7q0a?format=webp&name=small',
    ]);
  });

  it('tries the format named in a legacy URL first', () => {
    const c = x.fetchCandidates('https://pbs.twimg.com/media/Abc.png:small') as string[];
    assert.equal(c[0], 'https://pbs.twimg.com/media/Abc?format=png&name=orig');
    assert.equal(c[1], 'https://pbs.twimg.com/media/Abc?format=jpg&name=orig');
    assert.ok(c.includes('https://pbs.twimg.com/media/Abc?format=png&name=4096x4096'));
  });

  it('uses the thread of a status page as context, ignoring the photo suffix and the user name', () => {
    assert.equal(x.contextKey(loc('https://x.com/NASA/status/2113655288687431819')), 'x.com/i/status/2113655288687431819');
    assert.equal(x.contextKey(loc('https://x.com/nasa/status/2113655288687431819/photo/1')), 'x.com/i/status/2113655288687431819');
    assert.equal(x.contextKey(loc('https://twitter.com/NASA/status/2113655288687431819')), 'x.com/i/status/2113655288687431819');
  });

  it('uses the tweet around the image as context on a timeline', () => {
    const article: FakeNode = { links: [{ href: '/someone/status/42', time: true }] };
    const photo: FakeNode = { href: '/someone/status/42/photo/2' };
    const img = fakeElement({ src: small, article, parent: { parent: photo } });
    assert.equal(x.contextKey(loc('https://x.com/home'), img), 'x.com/i/status/42');
    assert.equal(x.pageIndex(small, img), 2);
    const loose = fakeElement({ src: small, article });
    assert.equal(x.contextKey(loc('https://x.com/home'), loose), 'x.com/i/status/42');
  });

  it('prefetches the next untranslated photos of the same tweet', async () => {
    const article: FakeNode = { links: [] };
    const photo = (n: number) => ({ href: `/someone/status/42/photo/${n}` });
    const srcs = ['https://pbs.twimg.com/profile_images/1/a_normal.jpg', ...[1, 2, 3, 4, 5, 6].map((n) => `https://pbs.twimg.com/media/M${n}?format=jpg&name=small`)];
    srcs[3] = 'blob:https://x.com/translated';
    article.imgs = srcs.map((src, i) => fakeElement({ src, article, parent: i ? { parent: photo(i) } : undefined }));
    const clicked = article.imgs[2]!;
    const next = await x.prefetch!('https://pbs.twimg.com/media/M2?format=jpg&name=small', loc('https://x.com/home'), clicked);
    assert.deepEqual(
      next.map((u) => [u.url.match(/M\d/)?.[0], u.pageIndex]),
      [
        ['M4', 4],
        ['M5', 5],
        ['M6', 6],
      ],
    );
  });

  it('asks for the image CDN host with the site', () => {
    assert.deepEqual(x.extraOrigins, ['*://pbs.twimg.com/*']);
  });
});

describe('pixiv adapter', () => {
  const px = adapterFor('www.pixiv.net');
  const date = '2026/10/24/20/00/54/';
  const master = (p: number) => `https://i.pximg.net/img-master/img/${date}151225632_p${p}_master1200.jpg`;
  const original = (p: number) => `https://i.pximg.net/img-original/img/${date}151225632_p${p}.png`;
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('recognizes artwork pages, with or without a resize prefix, but not crops or avatars', () => {
    assert.ok(px.isPage?.(master(0)));
    assert.ok(px.isPage?.(original(3)));
    assert.ok(px.isPage?.(`https://i.pximg.net/c/600x1200_90_webp/img-master/img/${date}151225632_p0_master1200.jpg`));
    assert.ok(!px.isPage?.(`https://i.pximg.net/c/250x250_80_a2/custom-thumb/img/${date}151225632_p0_custom1200.jpg`));
    assert.ok(!px.isPage?.(`https://i.pximg.net/c/128x128/img-master/img/${date}151225632_p0_square1200.jpg`));
    assert.ok(!px.isPage?.('https://i.pximg.net/user-profile/img/2019/05/13/23/50/47/15767304_2f12e2499bb3d88efbf3aefcf1186ffb_50.png'));
  });

  it('keys master, resized and original URLs of one page alike', () => {
    assert.equal(px.pageKey(master(2)), 'pixiv:151225632_p2');
    assert.equal(px.pageKey(original(2)), 'pixiv:151225632_p2');
    assert.equal(px.pageKey(`https://i.pximg.net/c/480x960/img-master/img/${date}151225632_p2_master1200.jpg`), 'pixiv:151225632_p2');
    assert.equal(px.pageIndex(master(2)), 2);
  });

  it('uses the artwork id of the requested image as context, else the one in the address', () => {
    assert.equal(px.contextKey(loc('https://www.pixiv.net/en/artworks/151225632')), 'pixiv.net/artworks/151225632');
    assert.equal(px.contextKey(loc('https://www.pixiv.net/artworks/151225632')), 'pixiv.net/artworks/151225632');
    const translated = fakeElement({ src: 'blob:https://www.pixiv.net/x' });
    assert.equal(px.contextKey(loc('https://www.pixiv.net/bookmark_new_illust.php'), translated, master(0).replace('151225632', '999')), 'pixiv.net/artworks/999');
  });

  it('names the artwork in the address as the work, and nothing elsewhere', () => {
    assert.equal(px.workId?.(loc('https://www.pixiv.net/en/artworks/151225632')), 'pixiv.net/artworks/151225632');
    assert.equal(px.workId?.(loc('https://www.pixiv.net/users/1')), undefined);
    assert.equal(adapterFor('x.com').workId, undefined);
  });

  it('fetches the original named by the pages API first, then master1200', async () => {
    globalThis.fetch = (async (url: string) => {
      assert.equal(url, '/ajax/illust/151225632/pages');
      const body = [0, 1].map((p) => ({ urls: { original: original(p), regular: master(p) } }));
      return new Response(JSON.stringify({ error: false, body }));
    }) as typeof fetch;
    assert.deepEqual(await px.fetchCandidates(`https://i.pximg.net/c/480x960/img-master/img/${date}151225632_p1_master1200.jpg`), [
      original(1),
      master(1),
      `https://i.pximg.net/c/480x960/img-master/img/${date}151225632_p1_master1200.jpg`,
    ]);
  });

  it('falls back to master1200 when the pages API fails', async () => {
    globalThis.fetch = (async () => new Response('nope', { status: 500 })) as unknown as typeof fetch;
    const url = master(0).replace('151225632', '777');
    assert.deepEqual(await px.fetchCandidates(url), [url]);
  });

  it('prefetches the next three pages of the work', async () => {
    globalThis.fetch = (async () => {
      const body = [0, 1, 2, 3, 4, 5].map((p) => ({ urls: { original: original(p).replace('151225632', '555'), regular: master(p).replace('151225632', '555') } }));
      return new Response(JSON.stringify({ body }));
    }) as unknown as typeof fetch;
    const next = await px.prefetch!(master(1).replace('151225632', '555'), loc('https://www.pixiv.net/en/artworks/555'));
    assert.deepEqual(
      next.map((u) => [u.pageIndex, px.pageKey(u.url)]),
      [
        [2, 'pixiv:555_p2'],
        [3, 'pixiv:555_p3'],
        [4, 'pixiv:555_p4'],
      ],
    );
  });

  it('rejects a pages response with a foreign URL', () => {
    assert.equal(parsePxPages({ body: [{ urls: { original: 'https://evil.example/x.png', regular: master(0) } }] }), undefined);
    assert.equal(parsePxPages({ error: true }), undefined);
  });

  it('sets the pixiv Referer for its image host and asks for that host', () => {
    assert.deepEqual(px.referer, { domain: 'i.pximg.net', referer: 'https://www.pixiv.net/' });
    assert.deepEqual(px.extraOrigins, ['*://i.pximg.net/*']);
  });
});
