# Site adapters

An adapter tells the extension which images on a site are manga pages, which URLs to fetch for each page, how pages group into one work, and which pages come next. Sites without an adapter use the default adapter. The built-in adapters are in `extension/src/adapters.ts`, and the build can add adapters of your own from a local folder, as [adapters.md](adapters.md) explains. The behavior they share is in [contract.md](contract.md).

## Permissions

The manifest asks for `scripting`, `storage`, `activeTab`, and `declarativeNetRequestWithHostAccess`. Its host permissions cover only `http://127.0.0.1/*` and `http://localhost/*`, where the server runs. Access to any other site is an optional permission, requested one site at a time.

Switching a site on in the popup requests access to `*://*.<domain>/*`, where `<domain>` is the page's registrable domain by the Public Suffix List, plus the adapter's image hosts. So `www.bbc.co.uk` requests `*://*.bbc.co.uk/*` and `someone.github.io` requests `*://*.someone.github.io/*`. On an IP address, a host without a dot, or a host that is itself a public suffix, it requests `<protocol>//<host>/*` instead. The extension then registers its content script for that pattern and injects it into open tabs of the site. Switching the site off unregisters the script and gives the access back, except access another enabled site still uses, such as `pbs.twimg.com` for x.com and twitter.com.

`declarativeNetRequestWithHostAccess` lets the background worker set a `Referer` header on its own image requests to hosts that refuse requests without one. The session rule applies only to requests that come from no tab (tab id -1), so the site's own requests are untouched.

## Summary

| Site | Default mode | Access requested | Prefetch | Referer rule |
| --- | --- | --- | --- | --- |
| x.com | `on-demand` | `*://*.x.com/*`, `*://pbs.twimg.com/*` | Next 3 photos of the post | None |
| twitter.com | `on-demand` | `*://*.twitter.com/*`, `*://pbs.twimg.com/*` | Next 3 photos of the post | None |
| pixiv.net | `on-demand` | `*://*.pixiv.net/*`, `*://i.pximg.net/*` | Next 3 pages of the artwork | `https://www.pixiv.net/` on `i.pximg.net` |
| Any other site | `on-demand` | `*://*.<domain>/*` | None | None |

In `auto` mode, every page image near the viewport is translated, plus the pages the adapter prefetches. In `on-demand` mode, nothing is translated until the reader clicks **Translate** in an image's chip. After that click, the adapter's prefetch runs for that work. The popup names the modes **Every page, automatically** and **Only images I click Translate on**. x.com and twitter.com are separate sites in the popup, each with its own switch and mode.

## Skipping a work

An adapter that names the work on a page lets the reader turn that work off in the popup with **Translate this gallery**. The choice is remembered for that work only, so other works on the site still follow the site's mode. An adapter that also reports the work's language skips a work that is already in English, and the popup offers **Translate anyway**. [contract.md](contract.md#skipping-a-work) has the details. Of the built-in adapters, only pixiv.net names its works, and none reports a language.

## Fetching a page

Each adapter turns an image URL into a list of candidate URLs, best first. The background worker fetches them as follows:

- A candidate that answers 404 or 410 moves on to the next one. A page fails without **Retry** only when every candidate is missing.
- A candidate with no response after 2 s starts the next candidate in parallel, and its host counts as slow for 10 minutes.
- A fetch with no first byte for 8 s, or that stalls for 8 s mid-download, is cancelled.
- Fetches send the browser's cookies for the image host.
- A page on the web cannot have the extension fetch an image from this computer, such as `127.0.0.1` or `localhost`. Such a page fails without **Retry**. A page served from this computer can.
- At most 2 pages are fetched or translated at once. The page nearest to the one on screen goes first.

Every adapter also gives each page a key. Two image URLs with the same key are one page and one job, so a page shown at several sizes or from several mirrors is translated once.

## x.com and twitter.com

- Pages are photos at `pbs.twimg.com/media/<id>`. Video thumbnails and GIFs are not pages.
- The key is the media id, so every size of one photo shares one job.
- The work is the post, `x.com/i/status/<id>`. On a status page it is the post in the address, and on a timeline it is the post that contains the photo.
- The page number comes from `/photo/<n>` in the photo's link, or in the address when the photo view is open.
- Candidates are the original size (`name=orig`) as JPEG and as PNG, then `4096x4096`, then `large`, then the shown URL. When the URL's path names a format, that format is tried first.
- Prefetch queues the next 3 photos of the same post after the one translated.
- X draws each photo as the background image of a DIV next to a transparent IMG. The swap and the **Alt** peek change that DIV too.

## pixiv.net

- Pages are images at `i.pximg.net` under `img-master` or `img-original`, named `<artwork id>_p<n>`.
- The key is `<artwork id>_p<n>`.
- The work is `pixiv.net/artworks/<id>`. On an artwork page, the popup offers **Translate this gallery** for that artwork. pixiv does not report a language, so no artwork is skipped as English.
- The page number is `<n>`.
- Candidates are the original named by `/ajax/illust/<id>/pages`, fetched with the reader's cookies, then the `master1200` JPEG, then the shown URL.
- Prefetch queues the next 3 pages of the artwork at their regular size, from the same page list.
- `i.pximg.net` refuses requests without a pixiv `Referer`, so the background worker sets `Referer: https://www.pixiv.net/` on its own requests to it.

## Other sites

The default adapter applies to every site without an adapter of its own.

- In `on-demand` mode, the **Translate** chip appears on images at least 200 px on each side. Avatars, emoji, and icons never offer it.
- In `auto` mode, an image counts as a page when it is drawn at least 300 px wide and its file is at least 500 px on each side.
- The key and the only candidate are the image URL.
- The work is the page's host and path, without a trailing `/<number>`. For example, `/read/abc/3` and `/read/abc/4` are one work.
- The page number is the image's position among the pages the extension tracks on the page.
- There is no prefetch.

Known limits:

- The popup requests access to the page's own domain only. If the site serves its images from a different domain, the extension has no access to that domain, and fetching the image can fail.
- Two image URLs for the same page, such as two sizes, are two jobs.
- Page numbers count only the images on the current page. On a site that shows one page per address, every page is number 1, so the translator gets no earlier pages as context. Reused translations and names from the same work still apply.
