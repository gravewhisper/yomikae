import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import { bundle, fakeChrome } from './fake-chrome.ts';

type Sites = typeof import('../src/sites.ts');

const realChrome = (globalThis as { chrome?: unknown }).chrome;
afterEach(() => {
  (globalThis as { chrome?: unknown }).chrome = realChrome;
});

async function setup(data: Record<string, unknown> = {}, granted: string[] = []) {
  const fake = fakeChrome(data, granted);
  (globalThis as { chrome?: unknown }).chrome = fake.chrome;
  return { ...fake, sites: await bundle<Sites>('src/sites.ts') };
}

const enabled = (pattern: string, label: string) => ({ [`site:${label}`]: pattern });

describe('site for a page', () => {
  it('covers the registrable domain of the page, not the public suffix', async () => {
    const { sites } = await setup();
    const pattern = (url: string) => sites.siteForUrl(url)?.pattern;
    assert.equal(pattern('https://www.bbc.co.uk/news'), '*://*.bbc.co.uk/*');
    assert.equal(pattern('https://someone.github.io/manga/1'), '*://*.someone.github.io/*');
    assert.equal(pattern('https://example.com.au/'), '*://*.example.com.au/*');
    assert.equal(sites.siteForUrl('https://someone.github.io/')?.label, 'someone.github.io');
  });

  it('keeps the patterns of known sites, addresses and single-label hosts', async () => {
    const { sites } = await setup();
    assert.deepEqual(sites.siteForUrl('https://x.com/a/status/1'), { id: 'site:x.com', label: 'x.com', pattern: '*://*.x.com/*', origins: ['*://*.x.com/*', '*://pbs.twimg.com/*'] });
    assert.equal(sites.siteForUrl('https://mobile.twitter.com/')?.pattern, '*://*.twitter.com/*');
    assert.deepEqual(sites.siteForUrl('https://www.pixiv.net/artworks/1')?.origins, ['*://*.pixiv.net/*', '*://i.pximg.net/*']);
    assert.equal(sites.siteForUrl('http://127.0.0.1:8000/read')?.pattern, 'http://127.0.0.1/*');
    assert.equal(sites.siteForUrl('http://localhost:3000/')?.pattern, 'http://localhost/*');
    assert.equal(sites.siteForUrl('http://[::1]:3000/')?.pattern, 'http://[::1]/*');
    assert.equal(sites.siteForUrl('chrome://extensions'), undefined);
  });

  it('forgets a stored site whose pattern covers a whole public suffix and gives its access back', async () => {
    const stored = { ...enabled('*://*.co.uk/*', 'co.uk'), ...enabled('*://*.github.io/*', 'github.io'), ...enabled('*://*.x.com/*', 'x.com'), ...enabled('http://localhost/*', 'localhost') };
    const granted = ['*://*.co.uk/*', '*://*.github.io/*', '*://*.x.com/*', '*://pbs.twimg.com/*', 'http://localhost/*'];
    const { sites, data, revoked, scripts } = await setup({ ...stored, sitesSeeded: true }, granted);
    await sites.reconcileSites();
    assert.deepEqual(Object.keys(data).filter((k) => k.startsWith('site:')).sort(), ['site:localhost', 'site:x.com']);
    assert.deepEqual([...revoked].sort(), ['*://*.co.uk/*', '*://*.github.io/*']);
    assert.deepEqual([...scripts.keys()].sort(), ['site:localhost', 'site:x.com']);
  });
});
